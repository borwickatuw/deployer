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
    python deploy.py ../app/deploy.toml myapp-production --timing-output timing.json
"""

import argparse
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Fallback for older Python

import boto3
import requests

from deployer.config.toml import validate_deploy_toml
from deployer.core import run_audit
from deployer.core.config import (
    derive_environment_from_env_name,
    load_environment_config,
    validate_environment_config,
)
from deployer.core.ssm_secrets import (
    check_secrets_exist,
    format_missing_secrets_error,
)
from deployer.deploy import (
    build_and_push_images,
    deploy_services,
    ecr_login,
    format_missing_ecr_error,
    get_service_sizing,
    start_migrations,
    validate_ecr_repositories,
    validate_ecs_cluster,
    wait_for_migrations,
    wait_for_stable,
)
from deployer.timing import DeploymentTimer, set_timer
from deployer.utils import (
    Colors,
    configure_aws_profile_for_environment,
    get_environment_path,
    get_environments_dir,
    log,
    log_debug,
    log_error,
    log_success,
    log_warning,
    set_verbose,
)
from deployer.core import get_staging_url_from_config


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
                print(f"    uv run python bin/environment.py {self.app_name}-{self.environment} start")
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


def cmd_audit(args: list[str]) -> int:
    """Run standalone audit of deploy.toml against docker-compose.yml."""
    parser = argparse.ArgumentParser(
        prog="deploy.py audit",
        description="Audit deploy.toml against docker-compose.yml to find discrepancies.",
        epilog="""
Examples:
  python deploy.py audit ~/code/myapp
  python deploy.py audit . --docker-compose docker-compose.prod.yml
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_dir",
        help="Path to project directory containing deploy.toml and docker-compose.yml",
    )
    parser.add_argument(
        "--docker-compose",
        default="docker-compose.yml",
        help="Name of docker-compose file (default: docker-compose.yml)",
    )
    parser.add_argument(
        "--deploy-toml",
        default="deploy.toml",
        help="Name of deploy.toml file (default: deploy.toml)",
    )

    parsed = parser.parse_args(args)

    issue_count, issues = run_audit(
        parsed.project_dir,
        compose_filename=parsed.docker_compose,
        deploy_filename=parsed.deploy_toml,
        verbose=True,
    )

    if issue_count < 0:
        # Error case (file not found)
        print(f"{Colors.RED}Error: {issues[0]}{Colors.NC}")
        return 1
    elif issue_count > 0:
        return 1
    else:
        return 0


# =============================================================================
# Speed Test Subcommand
# =============================================================================


class CognitoAuthenticator:
    """Handles Cognito authentication for accessing protected staging environments."""

    def __init__(self, environment: str, region: str = "us-west-2"):
        self.environment = environment
        self.region = region
        self.ssm = boto3.client("ssm", region_name=region)
        self.cognito = boto3.client("cognito-idp", region_name=region)
        self._tokens = None
        self._config = None

    def _load_config(self) -> dict:
        """Load environment config from config.toml."""
        if self._config is None:
            env_dir = get_environment_path(self.environment)
            if not env_dir.exists():
                raise RuntimeError(f"Environment directory not found: {env_dir}")
            self._config = load_environment_config(env_dir)
        return self._config

    def get_password(self) -> str:
        """Retrieve the deployer test password from SSM."""
        config = self._load_config()
        cognito_config = config.get("cognito", {})
        param_name = cognito_config.get("test_password_ssm")

        if not param_name:
            param_name = f"/deployer/{self.environment}/cognito-test-password"

        try:
            response = self.ssm.get_parameter(Name=param_name, WithDecryption=True)
            return response["Parameter"]["Value"]
        except self.ssm.exceptions.ParameterNotFound:
            raise RuntimeError(
                f"Cognito test password not found in SSM at {param_name}. "
                "See docs/STAGING-ENVIRONMENTS.md for setup instructions."
            )

    def get_user_pool_info(self) -> tuple[str, str, str]:
        """Get user pool ID, client ID, and client secret from config.toml."""
        import subprocess

        config = self._load_config()
        cognito_config = config.get("cognito", {})

        user_pool_id = cognito_config.get("user_pool_id")
        client_id = cognito_config.get("client_id")

        if not user_pool_id:
            raise RuntimeError(f"cognito.user_pool_id not found in config for {self.environment}")
        if not client_id:
            raise RuntimeError(f"cognito.client_id not found in config for {self.environment}")

        env_dir = get_environment_path(self.environment)
        result = subprocess.run(
            ["tofu", "output", "-raw", "cognito_user_pool_client_secret"],
            cwd=env_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to get cognito_user_pool_client_secret: {result.stderr}")
        client_secret = result.stdout.strip()

        return user_pool_id, client_id, client_secret

    def _compute_secret_hash(self, username: str, client_id: str, client_secret: str) -> str:
        """Compute the SECRET_HASH for Cognito authentication."""
        import base64
        import hashlib
        import hmac

        message = username + client_id
        dig = hmac.new(
            client_secret.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(dig).decode()

    def get_session(self, target_url: str) -> requests.Session:
        """Create an authenticated requests session by simulating browser OAuth flow."""
        import html as html_module
        import re
        from urllib.parse import urlparse

        config = self._load_config()
        cognito_config = config.get("cognito", {})
        username = cognito_config.get("test_username", "deployer@test.local")
        password = self.get_password()

        session = requests.Session()

        resp = session.get(target_url, allow_redirects=True, timeout=30)

        if "cognito" not in resp.url.lower():
            if resp.status_code == 200:
                return session
            raise RuntimeError(f"Unexpected response: {resp.status_code} at {resp.url}")

        page_html = resp.text

        form_match = re.search(r'<form[^>]+action="([^"]+)"', page_html)
        if not form_match:
            raise RuntimeError("Could not find login form in Cognito page")
        form_action = html_module.unescape(form_match.group(1))

        hidden_fields = {}
        for match in re.finditer(r'<input[^>]+type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page_html):
            hidden_fields[match.group(1)] = html_module.unescape(match.group(2))
        for match in re.finditer(r'<input[^>]+name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', page_html):
            hidden_fields[match.group(1)] = html_module.unescape(match.group(2))

        login_data = {
            **hidden_fields,
            "username": username,
            "password": password,
        }

        if form_action.startswith("/"):
            parsed = urlparse(resp.url)
            form_url = f"{parsed.scheme}://{parsed.netloc}{form_action}"
        else:
            form_url = form_action

        resp = session.post(
            form_url,
            data=login_data,
            allow_redirects=True,
            timeout=30,
        )

        if "AWSELBAuthSessionCookie" not in str(session.cookies):
            if "error" in resp.url.lower() or resp.status_code >= 400:
                raise RuntimeError(f"Login failed. Response URL: {resp.url}, Status: {resp.status_code}")
            log_warning(f"No AWSELBAuthSessionCookie found, but got status {resp.status_code}")

        return session


class MarkerPoller:
    """Polls a URL until a specific marker appears in the response."""

    def __init__(
        self,
        url: str,
        marker: str,
        session: requests.Session | None = None,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
    ):
        self.url = url
        self.marker = marker
        self.session = session or requests.Session()
        self.poll_interval = poll_interval
        self.timeout = timeout

        self._found_time: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    def start(self) -> None:
        """Start polling in a background thread."""
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def wait(self, timeout: float | None = None) -> float | None:
        """Wait for the marker to be found."""
        if self._thread:
            self._thread.join(timeout=timeout or self.timeout)
        return self._found_time

    @property
    def found_time(self) -> float | None:
        return self._found_time

    @property
    def error(self) -> str | None:
        return self._error

    def _poll_loop(self) -> None:
        """Polling loop running in background thread."""
        start_time = time.time()

        while not self._stop_event.is_set():
            elapsed = time.time() - start_time
            if elapsed > self.timeout:
                self._error = f"Timeout after {self.timeout}s waiting for marker"
                return

            try:
                response = self.session.get(self.url, timeout=10)

                if response.status_code == 302:
                    log_warning("Got 302 redirect - auth may not be configured correctly")
                elif response.status_code == 200:
                    try:
                        data = response.json()
                        if data.get("build_marker") == self.marker:
                            self._found_time = time.time()
                            return
                    except json.JSONDecodeError:
                        pass

            except requests.RequestException as e:
                log_warning(f"Poll request failed: {e}")

            time.sleep(self.poll_interval)


def _inject_marker(deploy_toml_path: Path, marker: str) -> str:
    """Inject a BUILD_MARKER into deploy.toml. Returns original content."""
    original_content = deploy_toml_path.read_text()

    lines = original_content.split("\n")
    new_lines = []
    marker_added = False

    for line in lines:
        if line.strip().startswith("BUILD_MARKER"):
            new_lines.append(f'BUILD_MARKER = "{marker}"')
            marker_added = True
            continue

        new_lines.append(line)

        if line.strip() == "[environment]" and not marker_added:
            new_lines.append(f'BUILD_MARKER = "{marker}"')
            marker_added = True

    deploy_toml_path.write_text("\n".join(new_lines))
    return original_content


def _get_health_check_path(deploy_config: dict) -> str:
    """Get health check path from deploy.toml."""
    services = deploy_config.get("services", {})
    web_service = services.get("web", {})
    return web_service.get("health_check_path", "/health/")


def cmd_speed_test(args: list[str]) -> int:
    """Run deployment speed test with visibility polling."""
    parser = argparse.ArgumentParser(
        prog="deploy.py speed-test",
        description="Deployment speed test - measures time until changes are visible.",
        epilog="""
Examples:
  python deploy.py speed-test ~/code/myapp/deploy.toml myapp-staging
  python deploy.py speed-test ~/code/myapp/deploy.toml myapp-staging --output results.json
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("config", type=Path, help="Path to deploy.toml")
    parser.add_argument("environment", help="Environment name")
    parser.add_argument("--output", "-o", metavar="FILE", help="Save timing report to JSON file")
    parser.add_argument("--marker", help="Use specific marker (auto-generated if not specified)")
    parser.add_argument("--dry-run", action="store_true", help="Skip actual deployment")
    parser.add_argument("--skip-auth", action="store_true", help="Skip Cognito authentication")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between polls (default: 2.0)")

    parsed = parser.parse_args(args)

    if not parsed.config.exists():
        log_error(f"Config file not found: {parsed.config}")
        return 1

    # Load configs
    with open(parsed.config, "rb") as f:
        deploy_config = tomllib.load(f)

    app_name = deploy_config.get("application", {}).get("name", "unknown")
    health_path = _get_health_check_path(deploy_config)

    env_dir = get_environment_path(parsed.environment)
    if not env_dir.exists():
        log_error(f"Environment directory not found: {env_dir}")
        return 1

    try:
        env_config = load_environment_config(env_dir)
        staging_url = get_staging_url_from_config(env_config)
        if not staging_url:
            log_error(f"Unable to determine staging URL from config for {parsed.environment}")
            return 1
    except (FileNotFoundError, RuntimeError) as e:
        log_error(f"Failed to load environment config: {e}")
        return 1

    # Configure AWS profile
    configure_aws_profile_for_environment(parsed.environment, "deploy")

    marker = parsed.marker or f"speedtest-{secrets.token_hex(4)}"
    run_id = f"speedtest-{marker.split('-')[-1]}"

    print()
    print(f"{Colors.BLUE}Deployment Speed Test{Colors.NC}")
    print(f"  App:         {app_name}")
    print(f"  Config:      {parsed.config}")
    print(f"  Environment: {parsed.environment}")
    print(f"  Marker:      {marker}")
    print(f"  URL:         {staging_url}{health_path}")
    print()

    # Initialize timer
    timer = DeploymentTimer(run_id)
    set_timer(timer)

    # Set up authenticated session
    health_url = f"{staging_url}{health_path}"
    session = None
    if not parsed.skip_auth:
        try:
            log("Authenticating with Cognito...")
            auth = CognitoAuthenticator(parsed.environment)
            session = auth.get_session(health_url)
            log_success("Authenticated")
        except Exception as e:
            log_error(f"Authentication failed: {e}")
            log_warning("Continuing without authentication (may get 302 redirects)")
            session = requests.Session()
    else:
        session = requests.Session()

    # Inject marker
    log(f"Injecting marker into {parsed.config}...")
    original_content = _inject_marker(parsed.config, marker)
    log_success(f"Marker injected: {marker}")

    try:
        deploy_start = time.time()

        # Start polling
        poller = MarkerPoller(
            url=health_url,
            marker=marker,
            session=session,
            poll_interval=parsed.poll_interval,
        )

        log("Starting deployment and visibility polling...")
        poller.start()

        # Run deployment
        try:
            deployer = Deployer(
                config_path=str(parsed.config),
                environment=parsed.environment,
                env_config=env_config,
                dry_run=parsed.dry_run,
                timer=timer,
            )
            deployer.run()
        except Exception as e:
            log_error(f"Deployment failed: {e}")
            poller.stop()
            raise

        time_to_stable = time.time() - deploy_start
        timer.set_time_to_stable(time_to_stable)

        if poller.found_time is None:
            log("Waiting for marker to appear...")
            poller.wait(timeout=120)

        poller.stop()

        if poller.found_time:
            time_to_visible = poller.found_time - deploy_start
            timer.set_time_to_visible(time_to_visible)

        # Print results
        print()
        print(f"{Colors.GREEN}Speed Test Results{Colors.NC}")
        print(f"  Run ID:           {run_id}")

        if poller.found_time:
            print(f"  Time to visible:  {time_to_visible:.2f}s")
            print(f"  Time to stable:   {time_to_stable:.2f}s")
            gap = time_to_stable - time_to_visible
            print(f"  Visibility gap:   {gap:.2f}s")
        else:
            print(f"  Time to visible:  NOT DETECTED")
            print(f"  Time to stable:   {time_to_stable:.2f}s")
            if poller.error:
                print(f"  Error:            {poller.error}")

        print()
        print("Step breakdown:")
        for step in timer.report.steps:
            print(f"  {step.name}: {step.duration_seconds:.2f}s")
            for sub in step.sub_steps:
                print(f"    {sub.name}: {sub.duration_seconds:.2f}s")

        if parsed.output:
            output_path = Path(parsed.output)
            timer.report.save_json(output_path)
            print()
            log_success(f"Report saved to {output_path}")

        return 0

    finally:
        log("Restoring deploy.toml...")
        parsed.config.write_text(original_content)
        log_success("deploy.toml restored")


def main():
    # Check for subcommands
    if len(sys.argv) > 1 and sys.argv[1] == "audit":
        sys.exit(cmd_audit(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "speed-test":
        sys.exit(cmd_speed_test(sys.argv[2:]))

    # Parse arguments first to get the environment name for profile configuration
    parser = argparse.ArgumentParser(
        description="Deploy an application to AWS ECS using a TOML configuration file.",
        epilog="""
Examples:
  python deploy.py ~/code/myapp/deploy.toml myapp-staging
  python deploy.py ~/code/myapp/deploy.toml myapp-staging --dry-run
  python deploy.py ../app/deploy.toml myapp-production --timing-output timing.json
  python deploy.py audit ~/code/myapp  (standalone audit)
  python deploy.py speed-test ~/code/myapp/deploy.toml myapp-staging  (visibility test)
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
        "--skip-ecr-check",
        action="store_true",
        help="Skip the ECR repository existence check"
    )
    parser.add_argument(
        "--skip-cluster-check",
        action="store_true",
        help="Skip the ECS cluster existence check"
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
        "--timing-output",
        metavar="FILE",
        help="Save timing report to JSON file (also prints to stdout)"
    )
    parser.add_argument(
        "--run-id",
        metavar="ID",
        help="Run ID for timing report (auto-generated if not specified)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed debug information"
    )

    args = parser.parse_args()

    # Enable verbose mode if requested
    if args.verbose:
        set_verbose(True)

    # Configure AWS profile before any boto3 clients are created
    # Uses environment-specific profile from config.toml if available
    try:
        configure_aws_profile_for_environment("deploy", args.environment, validate=True)
    except RuntimeError as e:
        log_error(str(e))
        sys.exit(1)
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

    # Validate required config fields are present
    config_errors = validate_environment_config(env_config)
    if config_errors:
        log_error("Environment config.toml is missing required fields:")
        for error in config_errors:
            print(f"    {error}")
        print()
        print("  These fields are typically set via ${tofu:...} placeholders.")
        print("  Run 'tofu apply' in the environment directory to create infrastructure,")
        print("  then update config.toml with the appropriate placeholders.")
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
            print(f"        or run: python bin/deploy.py audit {project_dir}")
            print("        or use --ignore-audit to skip this check")
            sys.exit(1)
        else:
            log_success("Audit passed")
            print()

    # Load deploy.toml for pre-flight checks
    with open(config_path, "rb") as f:
        deploy_config = tomllib.load(f)

    # Check ECR repositories exist unless --skip-ecr-check is set
    if not args.skip_ecr_check:
        log("Checking ECR repositories...")
        try:
            ecr_client = boto3.client("ecr")
            ecr_prefix = env_config.get("infrastructure", {}).get("ecr_prefix")
            log_debug(f"ECR prefix: {ecr_prefix}")
            if not ecr_prefix:
                log_warning("ecr_prefix not found in config.toml, skipping ECR check")
            else:
                images = deploy_config.get("images", {})
                log_debug(f"Images to check: {list(images.keys())}")
                missing_repos = validate_ecr_repositories(ecr_client, deploy_config, ecr_prefix)
                if missing_repos:
                    log_error(format_missing_ecr_error(missing_repos, args.environment))
                    sys.exit(1)
                else:
                    image_count = len([
                        img for img, cfg in images.items()
                        if cfg.get("push", True)
                    ])
                    log_success(f"All {image_count} ECR repository(ies) present")
            print()
        except Exception as e:
            log_error(f"Failed to check ECR repositories: {e}")
            sys.exit(1)

    # Check SSM secrets exist unless --skip-secrets-check is set
    if not args.skip_secrets_check:
        log("Checking SSM secrets...")
        try:
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

    # Check ECS cluster exists unless --skip-cluster-check is set
    if not args.skip_cluster_check:
        log("Checking ECS cluster...")
        try:
            ecs_client = boto3.client("ecs")
            cluster_name = env_config.get("infrastructure", {}).get("cluster_name")
            log_debug(f"Cluster name: {cluster_name}")
            if not cluster_name:
                log_warning("cluster_name not found in config.toml, skipping cluster check")
            else:
                log_debug(f"Calling describe_clusters for: {cluster_name}")
                exists, error = validate_ecs_cluster(ecs_client, cluster_name)
                if not exists:
                    log_error(error)
                    sys.exit(1)
                log_success(f"ECS cluster '{cluster_name}' is active")
            print()
        except Exception as e:
            log_error(f"Failed to check ECS cluster: {e}")
            sys.exit(1)

    # Set up timing if requested
    timer = None
    if args.timing_output:
        run_id = args.run_id or f"deploy-{secrets.token_hex(4)}"
        timer = DeploymentTimer(run_id)

    try:
        deployer = Deployer(
            args.config,
            environment_type,
            env_config,
            dry_run=args.dry_run,
            force=args.force,
            force_build=args.force_build,
            timer=timer,
        )
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)

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

    # Exit with warning code if health checks failed
    if health_failures:
        sys.exit(2)  # Exit code 2 = deployment completed but with warnings


if __name__ == "__main__":
    main()
