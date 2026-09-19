"""Deployment orchestrator.

The Deployer class orchestrates the full deployment pipeline: ECR login,
image build/push, database extensions, migrations, service deployment,
and stability checks.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Concatenate

import boto3
import click
from botocore.exceptions import BotoCoreError, ClientError

from deployer.config import parse_deploy_config
from deployer.deploy.autoscaling import apply_autoscaling, validate_scaling_config
from deployer.deploy.context import DeploymentContext, DeployOptions, InfraConfig, StabilityConfig
from deployer.deploy.extensions import create_database_extensions
from deployer.deploy.images import build_and_push_images, ecr_login
from deployer.deploy.preflight import PreflightOptions
from deployer.deploy.service import (
    DeployedServices,
    MigrationTask,
    deploy_services,
    start_migrations,
    store_service_state_hashes,
    wait_for_migrations,
    wait_for_stable,
)
from deployer.deploy.task_definition import get_environment_variables, get_service_sizing
from deployer.timing import DeploymentTimer, NullTimer, set_timer
from deployer.utils import Colors, log, log_warning, print_with_advice


@dataclass
class InfraStatus:
    """Result of infrastructure availability check."""

    warnings: list[str] = field(default_factory=list)
    is_critical: bool = False


def _build_infra_config(env_config: dict) -> InfraConfig:
    """Assemble the ECS infrastructure config from a resolved environment config.

    Args:
        env_config: Resolved environment configuration (config.toml).

    Returns:
        The infrastructure/database/scheduler settings consumed by the
        task-definition and service layers.
    """
    infra = env_config.get("infrastructure", {})
    database = env_config.get("database", {})
    deployment = env_config.get("deployment", {})
    scheduler = env_config.get("scheduler", {})

    return InfraConfig(
        execution_role_arn=infra.get("execution_role_arn"),
        task_role_arn=infra.get("task_role_arn"),
        security_group_id=infra.get("security_group_id"),
        subnet_ids=infra.get("private_subnet_ids", []),
        target_group_arn=infra.get("target_group_arn"),
        service_target_groups=infra.get("service_target_groups", {}),
        service_discovery_registries=infra.get("service_discovery_registries", {}),
        database_url=database.get("url"),
        db_host=database.get("host"),
        db_port=database.get("port"),
        db_name=database.get("name"),
        db_password_secret_arn=database.get("password_secret_arn"),
        db_username_secret_arn=database.get("username_secret_arn"),
        redis_url=env_config.get("cache", {}).get("url"),
        s3_media_bucket=env_config.get("storage", {}).get("media_bucket"),
        rds_instance_id=infra.get("rds_instance_id"),
        scheduler={
            "enabled": scheduler.get("enabled", False),
            "description": scheduler.get("description"),
        },
        deployment_config={
            "minimum_healthy_percent": deployment.get("minimum_healthy_percent", 100),
            "maximum_percent": deployment.get("maximum_percent", 200),
            "circuit_breaker_enabled": deployment.get("circuit_breaker_enabled", False),
            "circuit_breaker_rollback": deployment.get("circuit_breaker_rollback", True),
            "poll_interval": deployment.get("poll_interval", 15),
        },
        # Tofu's `health_check_config` output, wired into config.toml as
        # [services].health_check -- see docs/CONFIG-REFERENCE.md.
        health_check_config=env_config.get("services", {}).get("health_check", {}),
    )


# Total time wait_for_stable gives a service before declaring a timeout.
# Fixed regardless of poll cadence: a faster poll_interval buys detection
# granularity, not a shorter deadline.
STABILITY_TIMEOUT_SECONDS = 600


def _build_stability_config(infra_config: InfraConfig) -> StabilityConfig:
    """Build the wait_for_stable polling config from [deployment] settings.

    Raises:
        ValueError: If poll_interval is not an integer in 1-60. Values below
            ~5s mostly re-read the same ECS snapshot, but are allowed; the
            settle window's strength is independent of the poll cadence
            (StabilityConfig.settle_seconds).
    """
    poll = infra_config.deployment_config.get("poll_interval", 15)
    if isinstance(poll, bool) or not isinstance(poll, int) or not 1 <= poll <= 60:
        raise ValueError(
            f"[deployment] poll_interval must be an integer between 1 and 60 seconds, got {poll!r}"
        )
    return StabilityConfig(
        poll_interval=poll, max_attempts=max(1, round(STABILITY_TIMEOUT_SECONDS / poll))
    )


def _timed_step[**P, R](
    method: Callable[Concatenate[Deployer, P], R],
) -> Callable[Concatenate[Deployer, P], R]:
    """Time a Deployer step under the method's own name.

    The timing key is the method name with its leading underscore stripped,
    so ``_create_extensions`` is recorded as ``create_extensions``. Deriving
    it is the point: the step names are read by the deploy timing report, and
    written by hand they were two places to keep in sync -- a ``timer.step()``
    string literal and the call it wrapped -- free to drift apart. They had
    already drifted once (``create_extensions`` against
    ``create_database_extensions``), which is why the derivation strips only
    the underscore and renames nothing else.

    Args:
        method: A Deployer method that performs one pipeline step.

    Returns:
        The method, wrapped so its call is recorded against the run's timer.
    """
    step_name = method.__name__.removeprefix("_")

    @functools.wraps(method)
    def wrapper(self: Deployer, *args: P.args, **kwargs: P.kwargs) -> R:
        with (self.timer or NullTimer()).step(step_name):
            return method(self, *args, **kwargs)

    return wrapper


class Deployer:
    """Orchestrates the deployment pipeline."""

    def __init__(
        self,
        config_path: str,
        environment: str,
        env_config: dict,
        options: DeployOptions | None = None,
        timer: DeploymentTimer | None = None,
    ):
        self.config_path = Path(config_path).resolve()
        self.environment = environment
        self.env_config = env_config  # Store for module system
        self.options = options or DeployOptions()
        self.timer = timer

        # Load configuration using typed dataclass
        self.deploy_config = parse_deploy_config(self.config_path)

        # Warn about unknown options
        config_warnings = self.deploy_config.get_warnings()
        for warning in config_warnings:
            log_warning(f"deploy.toml: {warning}")
        if config_warnings:
            print()  # Add blank line after warnings

        # Get raw dict for backward compatibility with existing code
        self.config = self.deploy_config.get_raw_dict()

        application = self.deploy_config.application
        self.app_name = application.name
        # Resolve source_dir relative to the config file's location
        self.source_dir = (self.config_path.parent / application.source).resolve()

        # Extract sections from resolved environment config
        infra = env_config.get("infrastructure", {})
        services = env_config.get("services", {})

        # ECR prefix from environment config (required)
        self.ecr_prefix = infra.get("ecr_prefix")
        if not self.ecr_prefix:
            raise ValueError(
                "ecr_prefix not found in environment config. "
                "Add 'ecr_prefix = \"${tofu:ecr_prefix}\"' to the "
                "[infrastructure] section of config.toml"
            )

        # Service config from environment config.toml
        self.service_config = services.get("config", {})
        self.scaling_config = services.get("scaling", {})
        validate_scaling_config(self.config, self.scaling_config)

        # Infrastructure config for ECS deployment
        self.infra_config = _build_infra_config(env_config)
        self.stability_config = _build_stability_config(self.infra_config)

        # AWS clients
        self.ecs = boto3.client("ecs")
        self.ecr = boto3.client("ecr")
        self.rds = boto3.client("rds")
        self.sts = boto3.client("sts")
        self.autoscaling = boto3.client("application-autoscaling")
        self.cloudwatch = boto3.client("cloudwatch")

        # Get AWS account info
        self.account_id = self.sts.get_caller_identity()["Account"]
        region = boto3.Session().region_name
        if not region:
            raise ValueError(
                "No AWS region configured. Set AWS_DEFAULT_REGION environment variable "
                "or configure a default region in your AWS profile."
            )
        self.region: str = region

        # Cluster name from config (supports shared environments) or fallback
        self.cluster_name = infra.get("cluster_name")
        if not self.cluster_name:
            # Fallback for standalone environments (backward compatibility)
            self.cluster_name = f"{self.app_name}-{self.environment}-cluster"

        # Set global timer for sub-modules to use
        if self.timer:
            set_timer(self.timer)

        # Bundle shared deployment parameters
        self.ctx = DeploymentContext(
            ecs_client=self.ecs,
            cluster_name=self.cluster_name,
            config=self.config,
            service_config=self.service_config,
            infra_config=self.infra_config,
            app_name=self.app_name,
            environment=self.environment,
            region=self.region,
            account_id=self.account_id,
            env_config=self.env_config,
            dry_run=self.options.dry_run,
            scaling_config=self.scaling_config,
        )

    def print_service_config(self) -> None:
        """Print the merged service configuration for visibility."""
        log("Service configuration:")
        for service_name in self.config.get("services", {}):
            cfg = get_service_sizing(service_name, self.config, self.service_config)
            sizing = (
                f"cpu={cfg.get('cpu')}, memory={cfg.get('memory')}, replicas={cfg.get('replicas')}"
            )
            lb = "load_balanced" if cfg.get("load_balanced") else "no ALB"
            print(f"  {service_name}: {sizing} ({lb})")
        print()

    def print_environment_config(self) -> None:
        """Print the merged environment configuration in full, masking nothing.

        Nothing printed here can be a secret. ``get_environment_variables`` and
        ``get_secrets`` are disjoint routes off ``_collect_modules`` -- the
        ``.environment`` half and the ``.secrets`` half (task_definition.py).
        A ``[secrets] names`` entry reaches the container through the task
        definition's ``secrets`` block via SSM and never enters the environment
        map, which is the repo's own ADR: "Secret values never appear in
        deploy.toml, deploy script logs, or CI/CD output" (DECISIONS.md
        2026-01-21). ``check_environment_secrets_overlap`` enforces that
        disjointness at preflight rather than leaving it assumed.

        The masking this replaced keyed on substrings of the *name*, and
        measured against the only live app it was wrong nine times out of nine:
        it hid a public base URL, a health-check path, an IdP metadata URL, an
        expiry in seconds, and three empty strings, while printing
        CSRF_TRUSTED_ORIGINS in full holding the value it masked BASE_URL for.

        ``str(raw_value)`` is what makes ``or`` safe: after it, only ``""`` is
        falsy, so ``0`` prints ``0`` and ``false`` prints ``False`` rather than
        ``(unset)``. TOML yields ints and bools as well as strings, and the
        task definition stringifies the same value (build_task_definition), so
        this prints what deploys.

        ``(unset)`` is a marker for a human reading deploy narration, not a
        parseable encoding -- a value literally equal to ``(unset)`` is
        indistinguishable from an empty one, and nothing in the fleet parses
        this block. A machine-readable dump would need its own subcommand with
        real quoting.
        """
        log("Global environment variables:")
        env_vars = get_environment_variables(self.ctx)
        for key, raw_value in sorted(env_vars.items()):
            value = str(raw_value)
            print(f"  {key}={value or '(unset)'}")
        print()

    def check_infrastructure_status(self) -> InfraStatus:
        """Check if critical infrastructure is available."""
        rds_instance_id = self.infra_config.rds_instance_id
        if not rds_instance_id:
            return InfraStatus()

        try:
            response = self.rds.describe_db_instances(DBInstanceIdentifier=rds_instance_id)
        except self.rds.exceptions.DBInstanceNotFoundFault:
            return InfraStatus(
                warnings=[f"RDS instance '{rds_instance_id}' not found"], is_critical=True
            )
        except (ClientError, BotoCoreError) as e:
            # An unreadable instance is not a healthy one. Returning a clean
            # InfraStatus() here made a credentials failure indistinguishable
            # from "RDS is available", and the deploy went ahead on that.
            # Still not critical -- a pre-flight check that cannot run must not
            # block a deploy on its own -- but the operator is told.
            return InfraStatus(warnings=[f"Could not check RDS instance '{rds_instance_id}': {e}"])

        if not response["DBInstances"]:
            return InfraStatus()

        status = response["DBInstances"][0]["DBInstanceStatus"]
        if status == "available":
            return InfraStatus()

        msg = f"RDS instance '{rds_instance_id}' is {status} (not available)."
        scheduler = self.infra_config.scheduler
        if scheduler.get("enabled") and scheduler.get("description"):
            msg += f"\n         Service hours: {scheduler['description']}"

        return InfraStatus(warnings=[msg], is_critical=True)

    def _check_infrastructure_or_abort(self) -> InfraStatus:
        """Check infrastructure, report any warnings, and abort if disqualifying.

        The enforcement half of check_infrastructure_status(): critical
        infrastructure stops the deploy unless --force was passed.

        Returns:
            The status, so a later failure can re-display its warnings.

        Raises:
            RuntimeError: If infrastructure is critical and --force was not passed.
        """
        infra = self.check_infrastructure_status()
        if not infra.warnings:
            return infra

        for warning in infra.warnings:
            log_warning(warning)
        if infra.is_critical and not self.options.force:
            print_with_advice(
                "Cannot deploy: critical infrastructure is unavailable.",
                "  The database must be running for migrations to succeed.",
                "  Start the environment first:",
                f"    uv run python bin/environment.py {self.app_name}-{self.environment} start",
                "",
                "  Or use --force to deploy anyway (migrations will fail).",
            )
            raise RuntimeError("Infrastructure unavailable")
        elif infra.is_critical:
            log_warning("Continuing anyway due to --force flag. Migrations will likely fail.")
        print()
        return infra

    @_timed_step
    def _ecr_login(self) -> None:
        """Authenticate the local Docker client against ECR."""
        ecr_login(self.ecr, self.options.dry_run)

    @_timed_step
    def _build_and_push_images(self) -> dict[str, str]:
        """Build every declared image and push it to ECR.

        Returns:
            Map of image name to the ECR URI that was pushed.
        """
        return build_and_push_images(
            config=self.config,
            source_dir=self.source_dir,
            ecr_prefix=self.ecr_prefix,
            account_id=self.account_id,
            region=self.region,
            environment=self.environment,
            dry_run=self.options.dry_run,
            ecr_client=self.ecr,
            force_build=self.options.force_build,
        )

    @_timed_step
    def _create_extensions(self) -> None:
        """Create the declared database extensions, if any.

        Skipped when the declared extensions and the target database both
        match the last deploy's.
        """
        create_database_extensions(
            config=self.config,
            env_config=self.env_config,
            region=self.region,
            dry_run=self.options.dry_run,
            app_name=self.app_name,
            environment=self.environment,
        )

    @_timed_step
    def _start_migrations(self, image_uris: dict[str, str]) -> MigrationTask | None:
        """Start the migration task and return without waiting for it.

        Args:
            image_uris: Map of image name to ECR URI from _build_and_push_images.

        Returns:
            The running migration task, or None if there is nothing to migrate.
        """
        return start_migrations(self.ctx, image_uris, source_dir=self.source_dir)

    @_timed_step
    def _deploy_services(self, image_uris: dict[str, str]) -> DeployedServices:
        """Register task definitions and trigger ECS to pull the new images.

        Unchanged, healthy services are skipped unless --force-deploy.

        Args:
            image_uris: Map of image name to ECR URI from _build_and_push_images.

        Returns:
            Which services were updated, which were skipped, and their hashes.
        """
        return deploy_services(self.ctx, image_uris, force_deploy=self.options.force_deploy)

    @_timed_step
    def _wait_for_migrations(self, migration_task: MigrationTask | None) -> None:
        """Wait for the migration task to complete.

        Args:
            migration_task: The task returned by _start_migrations.

        Raises:
            RuntimeError: If the migration task did not succeed.
        """
        wait_for_migrations(self.ecs, migration_task)

    @_timed_step
    def _wait_for_stable(self, deployed: DeployedServices) -> list[str]:
        """Wait in parallel for the services this deploy updated to stabilize.

        Only the services this deploy actually updated are waited on, and each
        must end up with PRIMARY running the revision _deploy_services
        registered for it -- a circuit-breaker rollback otherwise reads as
        success.

        Args:
            deployed: The result of _deploy_services.

        Returns:
            Names of the services that did not pass their health checks.
        """
        return wait_for_stable(self.ctx, deployed.updated, self.stability_config)

    @_timed_step
    def _apply_autoscaling(self) -> None:
        """Bring Application Auto Scaling in line with the scaling config."""
        apply_autoscaling(self.ctx, self.scaling_config, self.autoscaling, self.cloudwatch)

    def _print_deploy_banner(self) -> None:
        """Print what is about to be deployed, and where, before any work starts."""
        print()
        print(f"{Colors.BLUE}Deploying {self.app_name} to {self.environment}{Colors.NC}")
        print(f"  Account: {self.account_id}")
        print(f"  Region:  {self.region}")
        print(f"  Cluster: {self.cluster_name}")
        if self.options.dry_run:
            print(f"  Mode:    {Colors.YELLOW}DRY RUN{Colors.NC}")
        print()

    def _replay_infrastructure_warnings(self, infra: InfraStatus) -> None:
        """Re-display earlier infrastructure warnings to help diagnose a failure.

        Args:
            infra: The status returned by _check_infrastructure_or_abort.
        """
        if not infra.warnings:
            return
        print()
        log_warning("Reminder: infrastructure issues were detected earlier:")
        for warning in infra.warnings:
            log_warning(f"  {warning}")

    def _report_outcome(
        self, image_uris: dict[str, str], health_failures: list[str]
    ) -> tuple[dict[str, str], list[str]]:
        """Print the closing summary and produce deploy()'s return value.

        Args:
            image_uris: Map of image name to ECR URI from _build_and_push_images.
            health_failures: Services that did not pass their health checks.

        Returns:
            Tuple of (image_uris dict, health_failures list).
        """
        if not health_failures:
            print(f"{Colors.GREEN}Deployment complete!{Colors.NC}")
            return image_uris, []

        print(f"{Colors.YELLOW}Deployment completed with warnings:{Colors.NC}")
        print(f"  The following services did not pass health checks: {', '.join(health_failures)}")
        print("  Services may still become healthy - check the AWS console.")
        return image_uris, health_failures

    def deploy(self) -> tuple[dict[str, str], list[str]]:
        """Run the full deployment pipeline.

        Every step below is a ``_``-prefixed method whose name is also its
        timing key (see :func:`_timed_step`). The order is load-bearing:

        * migrations start before services deploy, so the schema moves while
          ECS is still pulling images;
        * the per-service state hashes are written only once stability is
          proven -- a hash stored earlier would let a failed deploy skip its
          own retry;
        * autoscaling runs last because a first deploy must create the service
          before a scalable target can reference it, and because a failing
          policy apply (an IAM gap, say) must not force full service rolls on
          every retry: the retry re-runs autoscaling either way.

        ``store_service_state_hashes`` is the one step that is not timed -- it
        is milliseconds, and a no-op on dry runs, which compute no hashes.

        Returns:
            Tuple of (image_uris dict, health_failures list).
        """
        timer = self.timer or NullTimer()
        timer.start()

        self._print_deploy_banner()
        infra = self._check_infrastructure_or_abort()
        self.print_service_config()
        self.print_environment_config()

        self._ecr_login()
        print()
        image_uris = self._build_and_push_images()
        print()
        self._create_extensions()
        migration_task = self._start_migrations(image_uris)
        print()
        deployed = self._deploy_services(image_uris)
        print()

        try:
            self._wait_for_migrations(migration_task)
        except RuntimeError:
            self._replay_infrastructure_warnings(infra)
            raise
        print()

        health_failures = self._wait_for_stable(deployed)
        print()
        store_service_state_hashes(self.app_name, self.environment, deployed, health_failures)
        self._apply_autoscaling()
        print()

        timer.finish()
        return self._report_outcome(image_uris, health_failures)


def common_deploy_options(func):
    """Click decorator adding the deploy flags, collapsed into their two objects.

    The seven flags shared by deploy.py and ci-deploy arrive as click kwargs
    and reach the command as the two objects the pipeline actually takes:
    ``options`` (:class:`DeployOptions`) and ``preflight``
    (:class:`PreflightOptions`). Doing the mapping here is what makes
    "they arrive together and stay together" true -- both commands used to
    rebuild the same two constructions by hand from the same flag names.

    ``skip_audit`` is deliberately not among them: it is each command's own
    policy (deploy.py takes ``--ignore-audit``, ci-deploy always skips, having
    no docker-compose.yml to audit against), so each sets it with
    ``dataclasses.replace``.
    """

    @click.option("--dry-run", is_flag=True, help="Show what would be done without making changes")
    @click.option(
        "--force",
        is_flag=True,
        help="Deploy even if infrastructure is unavailable (database down, etc.)",
    )
    @click.option(
        "--force-build",
        is_flag=True,
        help="Force rebuilding images even if unchanged (skip cache check)",
    )
    @click.option(
        "--force-deploy",
        is_flag=True,
        help="Roll every service even if unchanged (secret rotation, restart-via-deploy)",
    )
    @click.option("--skip-ecr-check", is_flag=True, help="Skip the ECR repository existence check")
    @click.option("--skip-secrets-check", is_flag=True, help="Skip the SSM secrets existence check")
    @click.option("--skip-cluster-check", is_flag=True, help="Skip the ECS cluster existence check")
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        options = DeployOptions(
            dry_run=kwargs.pop("dry_run"),
            force=kwargs.pop("force"),
            force_build=kwargs.pop("force_build"),
            force_deploy=kwargs.pop("force_deploy"),
        )
        preflight = PreflightOptions(
            skip_ecr_check=kwargs.pop("skip_ecr_check"),
            skip_secrets_check=kwargs.pop("skip_secrets_check"),
            skip_cluster_check=kwargs.pop("skip_cluster_check"),
        )
        return func(*args, options=options, preflight=preflight, **kwargs)

    return wrapper


def handle_push_error(error: RuntimeError, include_ecr_hint: bool = False) -> bool:
    """Handle 'Push failed' errors with user-friendly messaging.

    Args:
        error: The RuntimeError from deployer.deploy().
        include_ecr_hint: If True, include hint about verifying ECR access.

    Returns:
        True if the error was handled (caller should sys.exit(1)),
        False if not a push error (caller should re-raise).
    """
    error_msg = str(error)
    if "Push failed" not in error_msg:
        return False

    print_with_advice(
        error_msg,
        "  This is often caused by a temporary network issue.",
        "  Please try running the deploy command again.",
    )
    if include_ecr_hint:
        print()
        print("  If the problem persists, check your network connection")
        print("  and verify ECR repository access.")
    return True
