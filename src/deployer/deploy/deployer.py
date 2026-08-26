"""Deployment orchestrator.

The Deployer class orchestrates the full deployment pipeline: ECR login,
image build/push, database extensions, migrations, service deployment,
and stability checks.
"""

import functools
from dataclasses import dataclass, field
from pathlib import Path

import boto3
import click
from botocore.exceptions import BotoCoreError, ClientError

from deployer.config import parse_deploy_config
from deployer.deploy.context import DeploymentContext, DeployOptions, InfraConfig
from deployer.deploy.extensions import create_database_extensions
from deployer.deploy.images import build_and_push_images, ecr_login
from deployer.deploy.service import (
    deploy_services,
    start_migrations,
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
        },
        # Tofu's `health_check_config` output, wired into config.toml as
        # [services].health_check -- see docs/CONFIG-REFERENCE.md.
        health_check_config=env_config.get("services", {}).get("health_check", {}),
    )


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

        # Infrastructure config for ECS deployment
        self.infra_config = _build_infra_config(env_config)

        # AWS clients
        self.ecs = boto3.client("ecs")
        self.ecr = boto3.client("ecr")
        self.rds = boto3.client("rds")
        self.sts = boto3.client("sts")

        # Get AWS account info
        self.account_id = self.sts.get_caller_identity()["Account"]
        self.region = boto3.session.Session().region_name
        if not self.region:
            raise ValueError(
                "No AWS region configured. Set AWS_DEFAULT_REGION environment variable "
                "or configure a default region in your AWS profile."
            )

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

    def deploy(self) -> tuple[dict[str, str], list[str]]:
        """Run the full deployment pipeline.

        Returns:
            Tuple of (image_uris dict, health_failures list).
        """
        timer = self.timer or NullTimer()
        timer.start()

        print()
        print(f"{Colors.BLUE}Deploying {self.app_name} to {self.environment}{Colors.NC}")
        print(f"  Account: {self.account_id}")
        print(f"  Region:  {self.region}")
        print(f"  Cluster: {self.cluster_name}")
        if self.options.dry_run:
            print(f"  Mode:    {Colors.YELLOW}DRY RUN{Colors.NC}")
        print()

        infra = self._check_infrastructure_or_abort()

        # Show service configuration
        self.print_service_config()

        # Show environment configuration
        self.print_environment_config()

        # Step 1: ECR login
        with timer.step("ecr_login"):
            ecr_login(self.ecr, self.options.dry_run)
        print()

        # Step 2: Build and push images
        with timer.step("build_and_push_images"):
            image_uris = build_and_push_images(
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
        print()

        # Step 3: Create database extensions (if declared)
        with timer.step("create_extensions"):
            create_database_extensions(
                config=self.config,
                env_config=self.env_config,
                region=self.region,
                dry_run=self.options.dry_run,
            )

        # Step 4: Start migrations (non-blocking)
        with timer.step("start_migrations"):
            migration_task = start_migrations(self.ctx, image_uris, source_dir=self.source_dir)
        print()

        # Step 5: Deploy services (triggers ECS to pull images)
        with timer.step("deploy_services"):
            updated_services = deploy_services(self.ctx, image_uris)
        print()

        # Step 6: Wait for migrations to complete
        try:
            with timer.step("wait_for_migrations"):
                wait_for_migrations(self.ecs, migration_task)
        except RuntimeError:
            # Re-display infrastructure warnings to help diagnose the failure
            if infra.warnings:
                print()
                log_warning("Reminder: infrastructure issues were detected earlier:")
                for warning in infra.warnings:
                    log_warning(f"  {warning}")
            raise
        print()

        # Step 7: Wait for services to stabilize (parallel). Each service must
        # end up with PRIMARY running the revision deploy_services registered
        # for it — a circuit-breaker rollback otherwise reads as success.
        with timer.step("wait_for_stable"):
            health_failures = wait_for_stable(self.ctx, updated_services)
        print()

        timer.finish()

        if health_failures:
            print(f"{Colors.YELLOW}Deployment completed with warnings:{Colors.NC}")
            print(
                f"  The following services did not pass health checks: {', '.join(health_failures)}"
            )
            print("  Services may still become healthy - check the AWS console.")
            return image_uris, health_failures
        else:
            print(f"{Colors.GREEN}Deployment complete!{Colors.NC}")
            return image_uris, []


def common_deploy_options(func):
    """Click decorator that adds common deployment options shared by deploy.py and ci-deploy."""

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
    @click.option("--skip-ecr-check", is_flag=True, help="Skip the ECR repository existence check")
    @click.option("--skip-secrets-check", is_flag=True, help="Skip the SSM secrets existence check")
    @click.option("--skip-cluster-check", is_flag=True, help="Skip the ECS cluster existence check")
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

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
