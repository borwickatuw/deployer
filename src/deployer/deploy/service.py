"""ECS service deployment operations."""

import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

from ..aws.cloudwatch import get_task_logs
from ..utils import Colors, log, log_debug, log_error, log_status, log_success, log_warning
from .context import DeploymentContext, StabilityConfig
from .migrations import should_skip_migrations, store_migrations_hash
from .task_definition import build_task_definition, get_service_sizing


@dataclass
class ServiceWaitResult:
    """Result of waiting for a single service to stabilize."""

    service_name: str
    success: bool
    health_check_failed: bool = False
    error: Exception | None = None


@dataclass
class MigrationTask:
    """Represents a running migration task."""

    task_arn: str
    cluster_name: str
    current_hash: str | None
    app_name: str
    environment: str


@dataclass
class DeploymentConfig:
    """Deployment configuration extracted from infra_config."""

    min_healthy: int = 100
    max_percent: int = 200
    circuit_breaker: bool = False
    circuit_rollback: bool = True


_DEPLOYMENT_CONFIG_KEYS = {
    "minimum_healthy_percent": "min_healthy",
    "maximum_percent": "max_percent",
    "circuit_breaker_enabled": "circuit_breaker",
    "circuit_breaker_rollback": "circuit_rollback",
}


def _get_deployment_config(infra_config: dict) -> DeploymentConfig:
    """Extract deployment configuration from infra_config."""
    deployment_cfg = infra_config.get("deployment_config", {})
    kwargs = {
        field: deployment_cfg[key]
        for key, field in _DEPLOYMENT_CONFIG_KEYS.items()
        if key in deployment_cfg
    }
    return DeploymentConfig(**kwargs)


class DeploymentError(Exception):
    """Raised when a deployment fails with a known error."""

    def __init__(self, message: str, service_name: str, error_type: str | None = None):
        self.service_name = service_name
        self.error_type = error_type
        super().__init__(message)


# Known fatal error patterns in ECS service events
FATAL_ERROR_PATTERNS = [
    (
        r"invalid ssm parameters?: (.+)",
        "missing_ssm_parameters",
        "Missing SSM parameters: {match}. Create them with: aws ssm put-parameter --name <name> --type SecureString --value <value>",
    ),
    (
        r"CannotPullContainerError.*repository.*does not exist",
        "ecr_repo_not_found",
        "ECR repository not found. Ensure the repository exists and the image has been pushed.",
    ),
    (
        r"CannotPullContainerError.*manifest.*not found",
        "image_not_found",
        "Image tag not found in ECR. Ensure the image has been pushed with the correct tag.",
    ),
    (
        r"unable to pull secrets or registry auth.*AccessDeniedException",
        "iam_secrets_access",
        "IAM role lacks permission to access secrets. Check the ECS execution role policy.",
    ),
    (
        r"ResourceInitializationError.*unable to pull secrets",
        "secrets_pull_failed",
        "Failed to pull secrets. Check that all SSM parameters exist and IAM permissions are correct.",
    ),
    (
        r"No Container Instances were found",
        "no_capacity",
        "No container instances available. For Fargate, check subnet/security group configuration.",
    ),
    (
        r"ECS was unable to assume the role",
        "iam_role_assume",
        "ECS cannot assume the task execution role. Check the role's trust policy.",
    ),
]


def _ensure_az_rebalancing_disabled(ecs_client, cluster_name: str, service_name: str) -> bool:
    """Disable AZ rebalancing if it's enabled and we need max_percent <= 100.

    AWS's Availability Zone Rebalancing feature doesn't support maximumPercent <= 100.
    This function checks if AZ rebalancing is enabled and disables it if needed.

    Args:
        ecs_client: boto3 ECS client.
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.

    Returns:
        True if AZ rebalancing was disabled, False if it was already disabled or service doesn't exist.
    """
    try:
        response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
        if not response.get("services"):
            return False

        service = response["services"][0]
        if service.get("status") == "INACTIVE":
            return False

        az_rebalancing = service.get("availabilityZoneRebalancing", "DISABLED")
        if az_rebalancing == "ENABLED":
            # Use subprocess to call AWS CLI since botocore doesn't support this parameter yet
            cmd = [
                "aws",
                "ecs",
                "update-service",
                "--cluster",
                cluster_name,
                "--service",
                service_name,
                "--availability-zone-rebalancing",
                "DISABLED",
                "--no-force-new-deployment",
                "--query",
                "service.serviceName",
                "--output",
                "text",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                log_status(service_name, "AZ rebalancing disabled")
                return True
            else:
                log_warning(f"Could not disable AZ rebalancing for {service_name}: {result.stderr}")
        return False
    except ClientError:
        return False


def service_exists(ecs_client, cluster_name: str, service_name: str) -> bool:
    """Check if an ECS service exists.

    Args:
        ecs_client: boto3 ECS client.
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.

    Returns:
        True if service exists and is not INACTIVE.
    """
    try:
        response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
        # Service exists if it's in the response and not INACTIVE
        for svc in response.get("services", []):
            if svc["serviceName"] == service_name and svc["status"] != "INACTIVE":
                return True
        return False
    except ClientError:
        return False


def register_task_definition(
    ctx,
    service_name: str,
    image_uri: str,
    credential_mode: str = "app",
) -> str:
    """Register a new task definition revision.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        image_uri: ECR image URI to use.
        credential_mode: For database credentials - "app" for runtime services
            (DML only), "migrate" for migrations (DDL + DML). Default is "app".

    Returns:
        The task definition ARN.
    """
    task_def = build_task_definition(
        ctx,
        service_name,
        image_uri,
        credential_mode=credential_mode,
    )

    if ctx.dry_run:
        print(
            f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws ecs register-task-definition --family {task_def['family']}"
        )
        return f"arn:aws:ecs:{ctx.region}:{ctx.account_id}:task-definition/{task_def['family']}:dry-run"

    response = ctx.ecs_client.register_task_definition(**task_def)
    task_def_arn = response["taskDefinition"]["taskDefinitionArn"]
    log_success(f"{service_name} task definition registered")
    return task_def_arn


def create_service(
    ctx,
    service_name: str,
    task_def_arn: str,
) -> None:
    """Create a new ECS service.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        task_def_arn: Task definition ARN to use.
    """
    service_cfg = get_service_sizing(service_name, ctx.config, ctx.service_config)
    service_toml = ctx.config.get("services", {}).get(service_name, {})

    # Get network configuration from infra_config
    subnet_ids = ctx.infra_config.get("subnet_ids", [])
    security_group_id = ctx.infra_config.get("security_group_id", "")

    if not subnet_ids or not security_group_id:
        log_error("Missing network configuration in infra_config (subnet_ids, security_group_id).")
        raise RuntimeError("Missing network configuration")

    dep_cfg = _get_deployment_config(ctx.infra_config)

    create_params = {
        "cluster": ctx.cluster_name,
        "serviceName": service_name,
        "taskDefinition": task_def_arn,
        "desiredCount": service_cfg["replicas"],
        "networkConfiguration": {
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [security_group_id],
                "assignPublicIp": "DISABLED",
            }
        },
        "deploymentConfiguration": {
            "minimumHealthyPercent": dep_cfg.min_healthy,
            "maximumPercent": dep_cfg.max_percent,
        },
    }

    # Use capacity provider strategy for interruptible services (Fargate Spot),
    # otherwise use standard FARGATE launch type
    if service_toml.get("interruptible"):
        create_params["capacityProviderStrategy"] = [
            {"capacityProvider": "FARGATE", "base": 1, "weight": 0},
            {"capacityProvider": "FARGATE_SPOT", "weight": 1},
        ]
    else:
        create_params["launchType"] = "FARGATE"

    # Add circuit breaker if enabled
    if dep_cfg.circuit_breaker:
        create_params["deploymentConfiguration"]["deploymentCircuitBreaker"] = {
            "enable": True,
            "rollback": dep_cfg.circuit_rollback,
        }

    # Add load balancer configuration if service is load balanced
    if service_cfg.get("load_balanced") and "port" in service_toml:
        # Use per-service target group if available, otherwise default
        service_target_groups = ctx.infra_config.get("service_target_groups", {})
        target_group_arn = service_target_groups.get(service_name) or ctx.infra_config.get(
            "target_group_arn", ""
        )
        if target_group_arn:
            create_params["loadBalancers"] = [
                {
                    "targetGroupArn": target_group_arn,
                    "containerName": service_name,
                    "containerPort": service_toml["port"],
                }
            ]
            # Add health check grace period for load-balanced services
            # This gives the container time to start before health checks begin
            health_check_cfg = ctx.infra_config.get("health_check_config", {})
            grace_period = health_check_cfg.get("grace_period", 60)
            create_params["healthCheckGracePeriodSeconds"] = grace_period

    # Add service discovery registration if configured
    # This allows internal service-to-service communication via DNS
    # Note: For A record DNS routing, only registryArn is needed (no containerPort)
    service_discovery_registries = ctx.infra_config.get("service_discovery_registries", {})
    registry_arn = service_discovery_registries.get(service_name)
    if registry_arn:
        create_params["serviceRegistries"] = [
            {
                "registryArn": registry_arn,
            }
        ]

    if ctx.dry_run:
        print(
            f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws ecs create-service --service-name {service_name}"
        )
        return

    ctx.ecs_client.create_service(**create_params)
    log_success(f"{service_name} service created")

    # Disable AZ rebalancing if using max_percent <= 100 (AWS doesn't support it)
    # Must be done after creation since botocore doesn't support the parameter yet
    if dep_cfg.max_percent <= 100:
        _ensure_az_rebalancing_disabled(ctx.ecs_client, ctx.cluster_name, service_name)


def deploy_services(
    ctx,
    image_uris: dict[str, str],
) -> None:
    """Register task definitions and deploy all services (create or update).

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        image_uris: Dictionary mapping image names to ECR URIs.
    """
    log("Deploying ECS services...")

    services = ctx.config.get("services", {})
    log_debug(f"Services to deploy: {list(services.keys())}")

    for service_name, svc_config in services.items():
        # Get merged config (deploy.toml + environment sizing)
        service_cfg = get_service_sizing(service_name, ctx.config, ctx.service_config)

        # Get image URI for this service
        image_name = svc_config.get("image", service_name)
        image_uri = image_uris.get(image_name)
        if not image_uri:
            log_error(f"No image URI for service {service_name} (image: {image_name})")
            continue

        # Register task definition
        task_def_arn = register_task_definition(
            ctx,
            service_name,
            image_uri,
        )

        # Check if service exists
        exists = (
            service_exists(ctx.ecs_client, ctx.cluster_name, service_name)
            if not ctx.dry_run
            else True
        )

        if exists:
            # Update existing service
            dep_cfg = _get_deployment_config(ctx.infra_config)

            # Disable AZ rebalancing if using max_percent <= 100 (AWS doesn't support it)
            if dep_cfg.max_percent <= 100 and not ctx.dry_run:
                _ensure_az_rebalancing_disabled(ctx.ecs_client, ctx.cluster_name, service_name)

            if ctx.dry_run:
                print(
                    f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws ecs update-service --service {service_name} --task-definition {task_def_arn}"
                )
                print(
                    f"    cpu={service_cfg.get('cpu')}, memory={service_cfg.get('memory')}, replicas={service_cfg.get('replicas')}"
                )
                print(
                    f"    deployment: minHealthy={dep_cfg.min_healthy}%, maxPercent={dep_cfg.max_percent}%, circuitBreaker={dep_cfg.circuit_breaker}"
                )
            else:
                try:
                    update_params = {
                        "cluster": ctx.cluster_name,
                        "service": service_name,
                        "taskDefinition": task_def_arn,
                        "forceNewDeployment": True,
                        "deploymentConfiguration": {
                            "minimumHealthyPercent": dep_cfg.min_healthy,
                            "maximumPercent": dep_cfg.max_percent,
                        },
                    }

                    # Add circuit breaker if enabled
                    if dep_cfg.circuit_breaker:
                        update_params["deploymentConfiguration"]["deploymentCircuitBreaker"] = {
                            "enable": True,
                            "rollback": dep_cfg.circuit_rollback,
                        }

                    # Add service discovery registration if configured
                    # This ensures existing services get updated with service discovery
                    # Note: For A record DNS routing, only registryArn is needed (no containerPort)
                    service_discovery_registries = ctx.infra_config.get(
                        "service_discovery_registries", {}
                    )
                    registry_arn = service_discovery_registries.get(service_name)
                    if registry_arn:
                        update_params["serviceRegistries"] = [
                            {
                                "registryArn": registry_arn,
                            }
                        ]

                    ctx.ecs_client.update_service(**update_params)
                    log_status(service_name, "deployment started")
                except ClientError as e:
                    log_error(f"Failed to update service {service_name}: {e}")
                    continue
        else:
            # Create new service
            create_service(
                ctx,
                service_name,
                task_def_arn,
            )
            log_status(service_name, "service created")


def start_migrations(
    ctx,
    image_uris: dict[str, str],
    source_dir: str | None,
) -> MigrationTask | None:
    """Start database migrations (non-blocking).

    This function starts the migration task and returns immediately.
    Use wait_for_migrations() to wait for completion.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        image_uris: Dictionary mapping image names to ECR URIs.
        source_dir: Path to the application source directory (for migration hashing).

    Returns:
        MigrationTask with task ARN and metadata, or None if migrations
        are disabled, skipped, or this is a dry run.
    """
    migrations = ctx.config.get("migrations", {})
    if not migrations.get("enabled", False):
        return None

    # Determine which service to use for migrations
    migration_service = migrations.get("service", "web")
    services_config = ctx.config.get("services", {})
    svc_config = services_config.get(migration_service, {})
    image_name = svc_config.get("image", migration_service)
    image_uri = image_uris.get(image_name)

    if not image_uri:
        log_error(f"No image URI for migration service {migration_service} (image: {image_name})")
        return None

    # Always register the migrate task definition so it's available for ecs-run.py
    # This ensures the task definition exists even if we skip running migrations
    log("Registering migrate task definition...")
    task_def_arn = register_task_definition(
        ctx,
        "migrate",  # Use "migrate" as task family name
        image_uri,
        credential_mode="migrate",  # Use migrate credentials (DDL + DML)
    )

    if ctx.dry_run:
        print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws ecs run-task (migrate command)")
        return None

    # Check if migrations can be skipped (no changes since last deploy)
    current_hash = None
    if source_dir:
        should_skip, current_hash = should_skip_migrations(
            source_dir, ctx.app_name, ctx.environment
        )
        if should_skip:
            # Task definition is registered but we skip running the migration
            return None

    log("Starting migrations...")

    command = migrations.get("command", ["python", "manage.py", "migrate"])

    # Get network configuration from an existing service
    try:
        services = ctx.ecs_client.describe_services(
            cluster=ctx.cluster_name, services=[migration_service]
        )
        if not services["services"]:
            log_error(f"No {migration_service} service found to get network configuration")
            return None

        network_config = services["services"][0]["networkConfiguration"]
    except ClientError as e:
        log_error(f"Could not get network configuration: {e}")
        return None

    # Run the migration task using the newly registered task definition
    # The container name is "migrate" (same as the task family/service name)
    response = ctx.ecs_client.run_task(
        cluster=ctx.cluster_name,
        taskDefinition=task_def_arn,
        launchType="FARGATE",
        networkConfiguration=network_config,
        overrides={
            "containerOverrides": [
                {"name": "migrate", "command": command}  # Container name matches task family
            ]
        },
    )

    task_arn = response["tasks"][0]["taskArn"]
    print(f"  Migration task started: {task_arn}")

    return MigrationTask(
        task_arn=task_arn,
        cluster_name=ctx.cluster_name,
        current_hash=current_hash,
        app_name=ctx.app_name,
        environment=ctx.environment,
    )


def wait_for_migrations(
    ecs_client,
    migration_task: MigrationTask | None,
) -> None:
    """Wait for a migration task to complete.

    Args:
        ecs_client: boto3 ECS client.
        migration_task: MigrationTask returned by start_migrations(), or None.

    Raises:
        RuntimeError: If the migration task fails.
    """
    if migration_task is None:
        return

    print("  Waiting for migrations to complete...")

    # Wait for task to complete
    waiter = ecs_client.get_waiter("tasks_stopped")
    waiter.wait(
        cluster=migration_task.cluster_name,
        tasks=[migration_task.task_arn],
    )

    # Check exit code
    task_desc = ecs_client.describe_tasks(
        cluster=migration_task.cluster_name,
        tasks=[migration_task.task_arn],
    )
    exit_code = task_desc["tasks"][0]["containers"][0].get("exitCode", 1)

    if exit_code != 0:
        log_error(f"Migration failed with exit code {exit_code}")

        # Fetch and display CloudWatch logs to help diagnose the failure
        _display_migration_logs(migration_task)

        raise RuntimeError(f"Migration failed with exit code {exit_code}")

    log_success("Migrations complete")

    # Store the migrations hash for future skip detection
    if migration_task.current_hash:
        store_migrations_hash(
            migration_task.app_name,
            migration_task.environment,
            migration_task.current_hash,
        )


def _display_migration_logs(migration_task: MigrationTask, limit: int = 50) -> None:
    """Fetch and display CloudWatch logs for a failed migration task.

    Args:
        migration_task: The failed migration task.
        limit: Maximum number of log lines to display.
    """
    # Extract task ID from ARN (last segment)
    task_id = migration_task.task_arn.split("/")[-1]

    # Log group follows ECS convention: /ecs/{app_name}-{environment}
    log_group = f"/ecs/{migration_task.app_name}-{migration_task.environment}"

    # Stream prefix is "migrate" for migration tasks
    stream_prefix = "migrate"

    # Container name is "migrate" for migration tasks
    container_name = "migrate"

    print()
    log("Fetching migration logs...")

    try:
        events = get_task_logs(log_group, stream_prefix, container_name, task_id, limit=limit)

        if events:
            print()
            print(f"  {Colors.CYAN}--- Migration Logs (last {limit} lines) ---{Colors.NC}")
            for event in events:
                message = event.get("message", "").rstrip()
                print(f"  {message}")
            print(f"  {Colors.CYAN}--- End of Logs ---{Colors.NC}")
            print()
        else:
            log_warning(f"No logs found. Check CloudWatch log group: {log_group}")
            print(f"  Stream: {stream_prefix}/{container_name}/{task_id}")
    except Exception as e:
        log_warning(f"Could not fetch logs: {e}")
        print(f"  Check CloudWatch manually: {log_group}")



def _check_for_fatal_errors(events: list[dict], service_name: str) -> None:
    """Check service events for known fatal error patterns.

    Args:
        events: List of ECS service events.
        service_name: Name of the service (for error messages).

    Raises:
        DeploymentError: If a fatal error pattern is detected.
    """
    for event in events:
        message = event.get("message", "")
        for pattern, error_type, help_text in FATAL_ERROR_PATTERNS:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                # Extract matched group if available for the help text
                match_text = match.group(1) if match.lastindex else ""
                detailed_help = help_text.format(match=match_text)
                raise DeploymentError(
                    f"{service_name}: {detailed_help}",
                    service_name=service_name,
                    error_type=error_type,
                )


def _get_deployment_status(service: dict) -> tuple[dict | None, int, int, int]:
    """Extract deployment status from service description.

    Args:
        service: ECS service description.

    Returns:
        Tuple of (primary_deployment, running_count, desired_count, failed_tasks).
    """
    deployments = service.get("deployments", [])
    primary = next((d for d in deployments if d["status"] == "PRIMARY"), None)

    if not primary:
        return None, 0, 0, 0

    return (
        primary,
        primary.get("runningCount", 0),
        primary.get("desiredCount", 0),
        primary.get("failedTasks", 0),
    )


def _wait_for_service_and_targets(
    ctx: DeploymentContext,
    elbv2_client,
    service_name: str,
    stability: StabilityConfig = StabilityConfig(),
) -> ServiceWaitResult:
    """Wait for a single service to stabilize and its targets to be healthy.

    This function wraps _wait_for_service_stable() and _wait_for_target_group_healthy()
    for use in parallel execution. Instead of raising exceptions, it returns a
    ServiceWaitResult to allow thread-safe error collection.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        elbv2_client: boto3 ELBv2 client.
        service_name: Name of the service.
        stability: Polling configuration for stability checks.

    Returns:
        ServiceWaitResult with success status and any error details.
    """
    try:
        _wait_for_service_stable(ctx, service_name, stability)

        # Also wait for target group health if service is load balanced
        target_group_arn = _get_service_target_group(ctx.ecs_client, ctx.cluster_name, service_name)
        if target_group_arn:
            if not _wait_for_target_group_healthy(
                elbv2_client,
                target_group_arn,
                service_name,
            ):
                return ServiceWaitResult(
                    service_name=service_name,
                    success=True,  # Service stable, just health check timeout
                    health_check_failed=True,
                )

        return ServiceWaitResult(service_name=service_name, success=True)

    except (DeploymentError, RuntimeError) as e:
        return ServiceWaitResult(
            service_name=service_name,
            success=False,
            error=e,
        )


def wait_for_stable(
    ctx: DeploymentContext,
    stability: StabilityConfig = StabilityConfig(),
) -> list[str]:
    """Wait for all services to stabilize with active error detection.

    Uses active polling instead of AWS waiter to detect failures early
    and provide actionable error messages. Also waits for load balancer
    target groups to have healthy targets.

    Services are waited on in parallel using a thread pool for faster
    total deployment time.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        stability: Polling configuration for stability checks.

    Returns:
        List of service names that failed health checks (empty if all healthy).

    Raises:
        DeploymentError: If any service fails to stabilize with a known error.
        RuntimeError: If any service times out waiting to stabilize.
    """
    log("Waiting for services to stabilize...")

    if ctx.dry_run:
        print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws ecs wait services-stable")
        return []

    services = list(ctx.config.get("services", {}).keys())
    if not services:
        return []

    # Create elbv2 client once and share across threads (boto3 clients are thread-safe)
    elbv2_client = boto3.client("elbv2")
    health_check_failures = []

    # Wait for all services in parallel
    with ThreadPoolExecutor(max_workers=len(services)) as executor:
        futures = {
            executor.submit(
                _wait_for_service_and_targets,
                ctx,
                elbv2_client,
                service_name,
                stability,
            ): service_name
            for service_name in services
        }

        # Process results as they complete - first fatal error fails deployment
        for future in as_completed(futures):
            result = future.result()
            if not result.success:
                # Re-raise the original exception to fail the deployment
                if result.error:
                    raise result.error
            elif result.health_check_failed:
                health_check_failures.append(result.service_name)

    return health_check_failures


def _wait_for_service_stable(
    ctx: DeploymentContext,
    service_name: str,
    stability: StabilityConfig = StabilityConfig(),
) -> None:
    """Wait for a single service to stabilize.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        stability: Polling configuration for stability checks.

    Raises:
        DeploymentError: If a fatal error is detected.
        RuntimeError: If the service doesn't stabilize within max_attempts.
    """
    ecs_client = ctx.ecs_client
    cluster_name = ctx.cluster_name

    last_status = ""
    consecutive_failures = 0

    for attempt in range(1, stability.max_attempts + 1):
        try:
            response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
        except ClientError as e:
            log_error(f"Failed to describe service {service_name}: {e}")
            raise

        if not response.get("services"):
            raise DeploymentError(
                f"Service {service_name} not found in cluster {cluster_name}",
                service_name=service_name,
                error_type="service_not_found",
            )

        service = response["services"][0]
        events = service.get("events", [])[:5]  # Check last 5 events

        # Check for fatal errors in events
        _check_for_fatal_errors(events, service_name)

        # Get deployment status
        deployment, running, desired, failed = _get_deployment_status(service)

        if not deployment:
            log_warning(f"{service_name}: No primary deployment found")
            time.sleep(stability.poll_interval)
            continue

        # Build status message
        status = f"running={running}/{desired}"
        if failed > 0:
            status += f", failed={failed}"

        # Only print if status changed
        if status != last_status:
            print(f"  {service_name}: {status}")
            last_status = status

        # Check for success: running matches desired and no pending tasks
        pending = deployment.get("pendingCount", 0)
        rollout_state = deployment.get("rolloutState", "")

        if running == desired and pending == 0 and running > 0:
            # Additional check: ensure rollout is complete
            if rollout_state == "COMPLETED" or running == desired:
                log_success(f"{service_name} (stable)")
                return

        # Check for persistent failures
        if failed >= stability.failure_threshold:
            # Get the most recent error message
            error_msg = "Tasks are failing repeatedly"
            if events:
                error_msg = events[0].get("message", error_msg)

            # Try to extract actionable info
            try:
                _check_for_fatal_errors(events, service_name)
            except DeploymentError:
                raise

            # Generic failure if no pattern matched
            raise DeploymentError(
                f"{service_name}: {failed} tasks failed. Latest event: {error_msg}",
                service_name=service_name,
                error_type="task_failures",
            )

        # Track consecutive polls with no progress
        if running == 0 and failed > 0:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                # 3 polls (~45s) with failures and no running tasks
                if events:
                    _check_for_fatal_errors(events, service_name)
                    # If no pattern matched, raise generic error
                    raise DeploymentError(
                        f"{service_name}: No tasks running after multiple attempts. "
                        f"Check ECS console for details. Latest: {events[0].get('message', 'No events')}",
                        service_name=service_name,
                        error_type="no_progress",
                    )
        else:
            consecutive_failures = 0

        time.sleep(stability.poll_interval)

    # Timeout
    raise RuntimeError(
        f"Service {service_name} did not stabilize after "
        f"{stability.max_attempts * stability.poll_interval}s. "
        f"Last status: {last_status}"
    )


def _get_service_target_group(ecs_client, cluster_name: str, service_name: str) -> str | None:
    """Get the target group ARN for a service if it has a load balancer.

    Args:
        ecs_client: boto3 ECS client.
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.

    Returns:
        Target group ARN or None if not load balanced.
    """
    try:
        response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
    except ClientError:
        return None

    if not response.get("services"):
        return None

    service = response["services"][0]
    load_balancers = service.get("loadBalancers", [])

    if load_balancers:
        return load_balancers[0].get("targetGroupArn")

    return None


def _wait_for_target_group_healthy(
    elbv2_client,
    target_group_arn: str,
    service_name: str,
    poll_interval: int = 10,
    max_attempts: int = 30,
) -> bool:
    """Wait for target group to have healthy targets.

    Args:
        elbv2_client: boto3 ELBv2 client.
        target_group_arn: ARN of the target group.
        service_name: Service name for logging.
        poll_interval: Seconds between checks.
        max_attempts: Maximum polling attempts.

    Returns:
        True if targets are healthy, False if timed out.
    """
    last_status = ""

    for attempt in range(1, max_attempts + 1):
        try:
            response = elbv2_client.describe_target_health(TargetGroupArn=target_group_arn)
        except ClientError as e:
            log_warning(f"Could not check target health: {e}")
            return True  # Don't fail deployment if we can't check

        targets = response.get("TargetHealthDescriptions", [])
        healthy = sum(1 for t in targets if t.get("TargetHealth", {}).get("State") == "healthy")
        total = len(targets)

        status = f"healthy={healthy}/{total}"

        if status != last_status:
            print(f"  {service_name} target group: {status}")
            last_status = status

        if healthy > 0 and healthy == total:
            log_success(f"{service_name} targets healthy")
            return True

        time.sleep(poll_interval)

    log_warning(f"{service_name}: Target group health check timed out (last: {last_status})")
    return False
