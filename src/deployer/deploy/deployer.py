"""Deployment orchestrator.

The Deployer class orchestrates the full deployment pipeline: ECR login,
image build/push, database extensions, migrations, service deployment,
and stability checks.
"""

import functools
from pathlib import Path

import boto3
import click

from deployer.config import parse_deploy_config
from deployer.deploy.context import DeploymentContext
from deployer.deploy.extensions import create_database_extensions
from deployer.deploy.images import build_and_push_images, ecr_login
from deployer.deploy.service import (
    deploy_services,
    start_migrations,
    wait_for_migrations,
    wait_for_stable,
)
from deployer.deploy.task_definition import get_environment_variables, get_service_sizing
from deployer.timing import DeploymentTimer, set_timer
from deployer.utils import Colors, log, log_error, log_warning


class Deployer:
    """Orchestrates the deployment pipeline."""

    def __init__(
        self,
        config_path: str,
        environment: str,
        env_config: dict,
        dry_run: bool = False,
        force: bool = False,
        force_build: bool = False,
        timer: DeploymentTimer | None = None,
    ):
        self.config_path = Path(config_path).resolve()
        self.environment = environment
        self.env_config = env_config  # Store for module system
        self.dry_run = dry_run
        self.force = force
        self.force_build = force_build
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

        self.app_name = self.deploy_config.application.name
        # Resolve source_dir relative to the config file's location
        source_path = self.deploy_config.application.source
        self.source_dir = (self.config_path.parent / source_path).resolve()

        # Extract sections from resolved environment config
        infra = env_config.get("infrastructure", {})
        services = env_config.get("services", {})
        database = env_config.get("database", {})
        deployment = env_config.get("deployment", {})
        scheduler = env_config.get("scheduler", {})

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
        self.infra_config = {
            "execution_role_arn": infra.get("execution_role_arn"),
            "task_role_arn": infra.get("task_role_arn"),
            "security_group_id": infra.get("security_group_id"),
            "subnet_ids": infra.get("private_subnet_ids", []),
            "target_group_arn": infra.get("target_group_arn"),
            "service_target_groups": infra.get("service_target_groups", {}),
            "service_discovery_registries": infra.get("service_discovery_registries", {}),
            # Database config - supports both URL (legacy) and component-based (Secrets Manager)
            "database_url": database.get("url"),
            "db_host": database.get("host"),
            "db_port": database.get("port"),
            "db_name": database.get("name"),
            "db_password_secret_arn": database.get("password_secret_arn"),
            "db_username_secret_arn": database.get("username_secret_arn"),
            "redis_url": env_config.get("cache", {}).get("url"),
            "s3_media_bucket": env_config.get("storage", {}).get("media_bucket"),
            "rds_instance_id": infra.get("rds_instance_id"),
            "scheduler": {
                "enabled": scheduler.get("enabled", False),
                "description": scheduler.get("description"),
            },
            "deployment_config": {
                "minimum_healthy_percent": deployment.get("minimum_healthy_percent", 100),
                "maximum_percent": deployment.get("maximum_percent", 200),
                "circuit_breaker_enabled": deployment.get("circuit_breaker_enabled", False),
                "circuit_breaker_rollback": deployment.get("circuit_breaker_rollback", True),
            },
        }

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
            dry_run=self.dry_run,
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
        """Print the merged environment configuration for visibility."""
        log("Global environment variables:")
        env_vars = get_environment_variables(self.ctx)
        for key, value in sorted(env_vars.items()):
            # Mask sensitive values
            if any(
                s in key.lower()
                for s in ["secret", "password", "key", "token", "url", "database", "connection"]
            ):
                display_value = "***"
            elif value.startswith("ssm:") or value.startswith("secretsmanager:"):
                display_value = value  # Show reference, not actual value
            else:
                display_value = value
            print(f"  {key}={display_value}")
        print()

    def check_infrastructure_status(self) -> tuple[list[str], bool]:
        """Check if critical infrastructure is available.

        Returns:
            Tuple of (warning messages, is_critical). is_critical=True means
            deployment should not proceed without --force.
        """
        warnings = []
        is_critical = False

        # Check RDS status
        rds_instance_id = self.infra_config.get("rds_instance_id")
        if rds_instance_id:
            try:
                response = self.rds.describe_db_instances(DBInstanceIdentifier=rds_instance_id)
                if response["DBInstances"]:
                    status = response["DBInstances"][0]["DBInstanceStatus"]
                    if status != "available":
                        msg = f"RDS instance '{rds_instance_id}' is {status} (not available)."
                        is_critical = True

                        # Add scheduler info if available
                        scheduler = self.infra_config.get("scheduler", {})
                        if scheduler.get("enabled") and scheduler.get("description"):
                            msg += f"\n         Service hours: {scheduler['description']}"

                        warnings.append(msg)
            except self.rds.exceptions.DBInstanceNotFoundFault:
                warnings.append(f"RDS instance '{rds_instance_id}' not found")
                is_critical = True
            except Exception:  # noqa: BLE001, S110 — don't fail deploy for infra-check errors
                pass

        return warnings, is_critical

    def deploy(self) -> tuple[dict[str, str], list[str]]:  # noqa: C901 — full deployment pipeline
        """Run the full deployment pipeline.

        Returns:
            Tuple of (image_uris dict, health_failures list).
        """
        if self.timer:
            self.timer.start()

        print()
        print(f"{Colors.BLUE}Deploying {self.app_name} to {self.environment}{Colors.NC}")
        print(f"  Account: {self.account_id}")
        print(f"  Region:  {self.region}")
        print(f"  Cluster: {self.cluster_name}")
        if self.dry_run:
            print(f"  Mode:    {Colors.YELLOW}DRY RUN{Colors.NC}")
        print()

        # Check infrastructure status and fail if critical services are down
        infra_warnings, infra_critical = self.check_infrastructure_status()
        if infra_warnings:
            for warning in infra_warnings:
                log_warning(warning)
            if infra_critical and not self.force:
                print()
                log_error("Cannot deploy: critical infrastructure is unavailable.")
                print()
                print("  The database must be running for migrations to succeed.")
                print("  Start the environment first:")
                print(
                    f"    uv run python bin/environment.py {self.app_name}-{self.environment} start"
                )
                print()
                print("  Or use --force to deploy anyway (migrations will fail).")
                raise RuntimeError("Infrastructure unavailable")
            elif infra_critical:
                log_warning("Continuing anyway due to --force flag. Migrations will likely fail.")
            print()

        # Show service configuration
        self.print_service_config()

        # Show environment configuration
        self.print_environment_config()

        # Step 1: ECR login
        if self.timer:
            with self.timer.step("ecr_login"):
                ecr_login(self.ecr, self.dry_run)
        else:
            ecr_login(self.ecr, self.dry_run)
        print()

        # Step 2: Build and push images
        if self.timer:
            with self.timer.step("build_and_push_images"):
                image_uris = build_and_push_images(
                    config=self.config,
                    source_dir=self.source_dir,
                    ecr_prefix=self.ecr_prefix,
                    account_id=self.account_id,
                    region=self.region,
                    environment=self.environment,
                    dry_run=self.dry_run,
                    ecr_client=self.ecr,
                    force_build=self.force_build,
                )
        else:
            image_uris = build_and_push_images(
                config=self.config,
                source_dir=self.source_dir,
                ecr_prefix=self.ecr_prefix,
                account_id=self.account_id,
                region=self.region,
                environment=self.environment,
                dry_run=self.dry_run,
                ecr_client=self.ecr,
                force_build=self.force_build,
            )
        print()

        # Step 3: Create database extensions (if declared)
        if self.timer:
            with self.timer.step("create_extensions"):
                create_database_extensions(
                    config=self.config,
                    env_config=self.env_config,
                    region=self.region,
                    dry_run=self.dry_run,
                )
        else:
            create_database_extensions(
                config=self.config,
                env_config=self.env_config,
                region=self.region,
                dry_run=self.dry_run,
            )

        # Step 4: Start migrations (non-blocking)
        if self.timer:
            with self.timer.step("start_migrations"):
                migration_task = start_migrations(self.ctx, image_uris, source_dir=self.source_dir)
        else:
            migration_task = start_migrations(self.ctx, image_uris, source_dir=self.source_dir)
        print()

        # Step 5: Deploy services (triggers ECS to pull images)
        if self.timer:
            with self.timer.step("deploy_services"):
                deploy_services(self.ctx, image_uris)
        else:
            deploy_services(self.ctx, image_uris)
        print()

        # Step 6: Wait for migrations to complete
        try:
            if self.timer:
                with self.timer.step("wait_for_migrations"):
                    wait_for_migrations(self.ecs, migration_task)
            else:
                wait_for_migrations(self.ecs, migration_task)
        except RuntimeError:
            # Re-display infrastructure warnings to help diagnose the failure
            if infra_warnings:
                print()
                log_warning("Reminder: infrastructure issues were detected earlier:")
                for warning in infra_warnings:
                    log_warning(f"  {warning}")
            raise
        print()

        # Step 7: Wait for services to stabilize (parallel)
        if self.timer:
            with self.timer.step("wait_for_stable"):
                health_failures = wait_for_stable(self.ctx)
        else:
            health_failures = wait_for_stable(self.ctx)
        print()

        if self.timer:
            self.timer.finish()

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

    print()
    log_error(error_msg)
    print()
    print("  This is often caused by a temporary network issue.")
    print("  Please try running the deploy command again.")
    if include_ecr_hint:
        print()
        print("  If the problem persists, check your network connection")
        print("  and verify ECR repository access.")
    return True
