#!/usr/bin/env python3
"""
Deploy an application to AWS ECS using a TOML configuration file.

This script reads an application's deployment configuration and:
1. Builds and pushes Docker images to ECR
2. Runs database migrations (if configured)
3. Updates ECS services to use the new images

Service sizing (cpu, memory, replicas) comes from the environment's config.toml
which references OpenTofu outputs. This allows different sizing per environment
while keeping app structure in deploy.toml.

Usage:
    python deploy.py <deploy.toml> <environment> [--dry-run]

Examples:
    python deploy.py ~/code/myapp/deploy.toml myapp-staging
    python deploy.py ~/code/myapp/deploy.toml myapp-staging --dry-run
    python deploy.py ../app/deploy.toml myapp-production --timing
"""

import argparse
import os
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Fallback for older Python

import boto3

from deployer.config.toml import validate_deploy_toml
from deployer.core import run_audit
from deployer.core.config import (
    derive_environment_from_env_name,
    load_environment_config,
)
from deployer.core.secrets import (
    check_secrets_exist,
    format_missing_secrets_error,
)
from deployer.deploy import (
    build_and_push_images,
    deploy_services,
    ecr_login,
    get_service_sizing,
    start_migrations,
    wait_for_migrations,
    wait_for_stable,
)
from deployer.timing import DeploymentTimer, set_timer
from deployer.utils import (
    Colors,
    configure_aws_profile_for_environment,
    get_environments_dir,
    log,
    log_error,
    log_success,
    log_warning,
)


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

        # Load configuration
        with open(self.config_path, "rb") as f:
            self.config = tomllib.load(f)

        # Validate configuration and warn about unknown options
        config_warnings = validate_deploy_toml(self.config)
        for warning in config_warnings:
            log_warning(f"deploy.toml: {warning}")
        if config_warnings:
            print()  # Add blank line after warnings

        self.app_name = self.config["application"]["name"]
        # Resolve source_dir relative to the config file's location
        source_path = self.config["application"]["source"]
        self.source_dir = (self.config_path.parent / source_path).resolve()

        # Extract sections from resolved environment config
        infra = env_config.get("infrastructure", {})
        services = env_config.get("services", {})
        database = env_config.get("database", {})
        redis = env_config.get("redis", {})
        storage = env_config.get("storage", {})
        deployment = env_config.get("deployment", {})
        scheduler = env_config.get("scheduler", {})

        # ECR prefix from environment config (required)
        self.ecr_prefix = infra.get("ecr_prefix")
        if not self.ecr_prefix:
            raise ValueError(
                "ecr_prefix not found in environment config. "
                "Add 'ecr_prefix = \"${tofu:ecr_prefix}\"' to the [infrastructure] section of config.toml"
            )

        # Service config from environment config.toml
        self.service_config = services.get("config", {})
        self.scaling_config = services.get("scaling", {})
        self.health_check_config = services.get("health_check", {})

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
            "redis_url": redis.get("url"),
            "s3_media_bucket": storage.get("media_bucket"),
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
        self.region = boto3.session.Session().region_name or "us-west-2"

        # Cluster name from config (supports shared environments) or fallback
        self.cluster_name = infra.get("cluster_name")
        if not self.cluster_name:
            # Fallback for standalone environments (backward compatibility)
            self.cluster_name = f"{self.app_name}-{self.environment}-cluster"

        # Set global timer for sub-modules to use
        if self.timer:
            set_timer(self.timer)

    def print_service_config(self) -> None:
        """Print the merged service configuration for visibility."""
        log("Service configuration:")
        for service_name in self.config.get("services", {}):
            cfg = get_service_sizing(service_name, self.config, self.service_config)
            sizing = f"cpu={cfg.get('cpu')}, memory={cfg.get('memory')}, replicas={cfg.get('replicas')}"
            lb = "load_balanced" if cfg.get("load_balanced") else "no ALB"
            print(f"  {service_name}: {sizing} ({lb})")
        print()

    def print_environment_config(self) -> None:
        """Print the merged environment configuration for visibility."""
        from deployer.deploy import get_environment_variables

        log("Global environment variables:")
        env_vars = get_environment_variables(
            self.config, self.environment, self.region,
            infra_config=self.infra_config, env_config=self.env_config
        )
        for key, value in sorted(env_vars.items()):
            # Mask sensitive values
            if any(s in key.lower() for s in ["secret", "password", "key", "token", "url", "database", "connection"]):
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
                response = self.rds.describe_db_instances(
                    DBInstanceIdentifier=rds_instance_id
                )
                if response["DBInstances"]:
                    status = response["DBInstances"][0]["DBInstanceStatus"]
                    # Available statuses: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/accessing-monitoring.html
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
            except Exception as e:
                # Don't fail deployment if we can't check - just skip the warning
                pass

        return warnings, is_critical

    def deploy(self) -> dict[str, str]:
        """Run the full deployment pipeline.

        Returns:
            Dictionary mapping image names to their ECR URIs.
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
                print(f"    uv run python bin/manage-environment.py {self.app_name}-{self.environment} start")
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

        # Step 3: Start migrations (non-blocking)
        # This runs in parallel with ECS pulling images and starting containers
        if self.timer:
            with self.timer.step("start_migrations"):
                migration_task = start_migrations(
                    self.ecs, self.cluster_name, self.config,
                    self.app_name, self.environment,
                    image_uris=image_uris,
                    service_config=self.service_config,
                    infra_config=self.infra_config,
                    region=self.region,
                    account_id=self.account_id,
                    dry_run=self.dry_run,
                    env_config=self.env_config,
                    source_dir=self.source_dir,
                )
        else:
            migration_task = start_migrations(
                self.ecs, self.cluster_name, self.config,
                self.app_name, self.environment,
                image_uris=image_uris,
                service_config=self.service_config,
                infra_config=self.infra_config,
                region=self.region,
                account_id=self.account_id,
                dry_run=self.dry_run,
                env_config=self.env_config,
                source_dir=self.source_dir,
            )
        print()

        # Step 4: Deploy services (triggers ECS to pull images)
        if self.timer:
            with self.timer.step("deploy_services"):
                deploy_services(
                    ecs_client=self.ecs,
                    cluster_name=self.cluster_name,
                    image_uris=image_uris,
                    config=self.config,
                    service_config=self.service_config,
                    infra_config=self.infra_config,
                    app_name=self.app_name,
                    environment=self.environment,
                    region=self.region,
                    account_id=self.account_id,
                    dry_run=self.dry_run,
                    env_config=self.env_config,
                )
        else:
            deploy_services(
                ecs_client=self.ecs,
                cluster_name=self.cluster_name,
                image_uris=image_uris,
                config=self.config,
                service_config=self.service_config,
                infra_config=self.infra_config,
                app_name=self.app_name,
                environment=self.environment,
                region=self.region,
                account_id=self.account_id,
                dry_run=self.dry_run,
                env_config=self.env_config,
            )
        print()

        # Step 5: Wait for migrations to complete
        # Must complete before services become healthy (they may need DB schema changes)
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

        # Step 6: Wait for services to stabilize (parallel)
        if self.timer:
            with self.timer.step("wait_for_stable"):
                health_failures = wait_for_stable(self.ecs, self.cluster_name, self.config, self.dry_run)
        else:
            health_failures = wait_for_stable(self.ecs, self.cluster_name, self.config, self.dry_run)
        print()

        if self.timer:
            self.timer.finish()

        if health_failures:
            print(f"{Colors.YELLOW}Deployment completed with warnings:{Colors.NC}")
            print(f"  The following services did not pass health checks: {', '.join(health_failures)}")
            print(f"  Services may still become healthy - check the AWS console.")
            return image_uris, health_failures
        else:
            print(f"{Colors.GREEN}Deployment complete!{Colors.NC}")
            return image_uris, []


def main():
    # Parse arguments first to get the environment name for profile configuration
    parser = argparse.ArgumentParser(
        description="Deploy an application to AWS ECS using a TOML configuration file.",
        epilog="""
Examples:
  python deploy.py ~/code/myapp/deploy.toml myapp-staging
  python deploy.py ~/code/myapp/deploy.toml myapp-staging --dry-run
  python deploy.py ../app/deploy.toml myapp-production --timing
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("config", help="Path to the application's deploy.toml file")
    parser.add_argument(
        "environment",
        help="Environment name (e.g., myapp-staging, myapp-production)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--ignore-audit",
        action="store_true",
        help="Skip the deploy.toml vs docker-compose.yml audit check"
    )
    parser.add_argument(
        "--skip-secrets-check",
        action="store_true",
        help="Skip the SSM secrets existence check"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Deploy even if infrastructure is unavailable (database down, etc.)"
    )
    parser.add_argument(
        "--force-build",
        action="store_true",
        help="Force rebuilding images even if unchanged (skip cache check)"
    )
    parser.add_argument(
        "--timing",
        action="store_true",
        help="Enable timing instrumentation and print timing report"
    )
    parser.add_argument(
        "--timing-output",
        metavar="FILE",
        help="Save timing report to JSON file (implies --timing)"
    )
    parser.add_argument(
        "--timing-csv",
        metavar="FILE",
        help="Append timing data to CSV file (implies --timing)"
    )
    parser.add_argument(
        "--run-id",
        metavar="ID",
        help="Run ID for timing report (auto-generated if not specified)"
    )

    args = parser.parse_args()

    # Configure AWS profile before any boto3 clients are created
    # Uses environment-specific profile from config.toml if available
    configure_aws_profile_for_environment("deploy", args.environment)
    print()

    # Validate config file
    config_path = Path(args.config)
    if config_path.is_dir():
        log_error(f"Config path is a directory, expected a .toml file: {config_path}")
        sys.exit(1)
    if not config_path.exists():
        log_error(f"Config file not found: {config_path}")
        sys.exit(1)
    if not config_path.suffix == ".toml":
        log_error(f"Config file must be a .toml file, got: {config_path}")
        sys.exit(1)

    # Validate environment directory exists
    env_path = get_environments_dir() / args.environment
    if not env_path.exists():
        log_error(f"Environment directory not found: {env_path}")
        sys.exit(1)

    # Load config from environment's config.toml
    log(f"Loading deployment config from {env_path}...")
    try:
        env_config = load_environment_config(env_path)
        log_success(f"Loaded config from config.toml")
    except FileNotFoundError:
        log_error(f"Config file not found: {env_path / 'config.toml'}")
        sys.exit(1)
    except Exception as e:
        log_error(f"Failed to load deployment config: {e}")
        sys.exit(1)

    # Derive environment type (staging/production) from env name
    try:
        environment_type = derive_environment_from_env_name(args.environment)
        log(f"Environment type: {environment_type}")
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)
    print()

    if not os.path.exists(args.config):
        log_error(f"Config file not found: {args.config}")
        sys.exit(1)

    # Run audit check unless --ignore-audit is set
    config_path = Path(args.config).resolve()
    project_dir = config_path.parent

    if not args.ignore_audit:
        log("Running deploy.toml audit...")
        issue_count, issues = run_audit(project_dir, verbose=False)

        if issue_count < 0:
            # File not found - skip audit silently (docker-compose.yml may not exist)
            log_warning(f"Audit skipped: {issues[0]}")
            print()
        elif issue_count > 0:
            log_error(f"Audit found {issue_count} issue(s):")
            for issue in issues:
                print(f"  - {issue}")
            print()
            print("To fix: add an [audit] section to deploy.toml to acknowledge differences,")
            print(f"        or run: python bin/audit-deploy-config.py {project_dir}")
            print("        or use --ignore-audit to skip this check")
            sys.exit(1)
        else:
            log_success("Audit passed")
            print()

    # Check SSM secrets exist unless --skip-secrets-check is set
    if not args.skip_secrets_check:
        log("Checking SSM secrets...")
        try:
            # Load deploy.toml to check secrets
            with open(config_path, "rb") as f:
                deploy_config = tomllib.load(f)

            missing, present = check_secrets_exist(
                deploy_config, environment_type, args.environment, env_config
            )

            if missing:
                log_error(format_missing_secrets_error(missing, args.environment))
                sys.exit(1)
            elif present:
                log_success(f"All {len(present)} secret(s) present")
            else:
                log("No secrets defined in deploy.toml")
            print()
        except RuntimeError as e:
            log_error(f"Failed to check secrets: {e}")
            sys.exit(1)

    # Set up timing if requested
    timer = None
    enable_timing = args.timing or args.timing_output or args.timing_csv
    if enable_timing:
        import secrets
        run_id = args.run_id or f"deploy-{secrets.token_hex(4)}"
        timer = DeploymentTimer(run_id)

    deployer = Deployer(
        args.config,
        environment_type,
        env_config,
        dry_run=args.dry_run,
        force=args.force,
        force_build=args.force_build,
        timer=timer,
    )

    try:
        _, health_failures = deployer.deploy()
    except RuntimeError as e:
        error_msg = str(e)
        if "Push failed" in error_msg:
            print()
            log_error(error_msg)
            print()
            print("  This is often caused by a temporary network issue.")
            print("  Please try running the deploy command again.")
            print()
            print("  If the problem persists, check your network connection")
            print("  and verify ECR repository access.")
            sys.exit(1)
        # Re-raise other RuntimeErrors
        raise

    # Output timing results
    if timer:
        print()
        log("Timing report:")
        print(timer.report.to_json())

        if args.timing_output:
            output_path = Path(args.timing_output)
            timer.report.save_json(output_path)
            log_success(f"Timing saved to {output_path}")

        if args.timing_csv:
            csv_path = Path(args.timing_csv)
            timer.report.append_csv(csv_path)
            log_success(f"Timing appended to {csv_path}")

    # Exit with warning code if health checks failed
    if health_failures:
        sys.exit(2)  # Exit code 2 = deployment completed but with warnings


if __name__ == "__main__":
    main()
