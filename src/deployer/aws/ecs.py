"""AWS ECS service operations."""

import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError, WaiterError

from ..utils import AWS_REGION, format_iso


def _get_ecs_client() -> Any:
    """Get a boto3 ECS client using the current AWS profile."""
    return boto3.client("ecs", region_name=AWS_REGION)


# pysmelly: ignore dict-as-dataclass — strong candidate but 15+ call sites; defer to focused session
def _format_service(svc: dict) -> dict:
    """Format raw ECS service data into a consistent dict structure.

    Args:
        svc: Raw service dict from AWS API.

    Returns:
        Formatted service dict with standardized keys.
    """
    # Get the most recent deployment time (PRIMARY deployment)
    last_deployment_at = None
    deployments = svc.get("deployments", [])
    for deployment in deployments:
        if deployment.get("status") == "PRIMARY":
            last_deployment_at = deployment.get("updatedAt")
            break
    # Fallback to first deployment if no PRIMARY found
    if not last_deployment_at and deployments:
        last_deployment_at = deployments[0].get("updatedAt")

    return {
        "name": svc["serviceName"],
        "arn": svc["serviceArn"],
        "desired_count": svc["desiredCount"],
        "running_count": svc["runningCount"],
        "status": svc["status"],
        "task_definition": svc.get("taskDefinition"),
        "last_deployment_at": last_deployment_at,
    }


def get_services(cluster_name: str, ecs_client: Any | None = None) -> list[dict]:
    """List all ECS services in a cluster with their current state.

    Args:
        cluster_name: Name of the ECS cluster.
        ecs_client: Optional boto3 ECS client. If None, creates one.

    Returns:
        List of service dicts with name, arn, desired_count, running_count, status.
    """
    if ecs_client is None:
        ecs_client = _get_ecs_client()

    services = []
    paginator = ecs_client.get_paginator("list_services")

    try:
        for page in paginator.paginate(cluster=cluster_name):
            if page["serviceArns"]:
                details = ecs_client.describe_services(
                    cluster=cluster_name, services=page["serviceArns"]
                )
                services.extend(_format_service(svc) for svc in details["services"])
    except ClientError as e:
        if "ClusterNotFoundException" in str(e):
            return []
        raise

    return services


# pysmelly: ignore param-clumps — boto3 client + cluster + service are distinct AWS identifiers
def scale_service(
    cluster_name: str,
    service_name: str,
    desired_count: int,
    ecs_client: Any | None = None,
) -> bool:
    """Scale an ECS service to the desired count.

    Args:
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service to scale.
        desired_count: Target number of tasks.
        ecs_client: Optional boto3 ECS client. If None, creates one.

    Returns:
        True if successful, False otherwise.
    """
    if ecs_client is None:
        ecs_client = _get_ecs_client()

    try:
        ecs_client.update_service(
            cluster=cluster_name,
            service=service_name,
            desiredCount=desired_count,
        )
        return True
    except ClientError:
        return False


def _format_container_definitions(containers: list[dict]) -> list[dict]:
    """Format raw container definitions into a consistent structure.

    Args:
        containers: List of container definitions from AWS API.

    Returns:
        List of formatted container dicts.
    """
    return [
        {
            "name": c["name"],
            "image": c.get("image", ""),
            "essential": c.get("essential", True),
            "logConfiguration": c.get("logConfiguration"),
        }
        for c in containers
    ]


def get_task_containers(task_definition: str, ecs_client: Any | None = None) -> list[dict]:
    """Get container names and info from a task definition.

    Args:
        task_definition: Task definition ARN or family:revision.
        ecs_client: Optional boto3 ECS client. If None, creates one.

    Returns:
        List of dicts with name, image, essential, and logConfiguration.
    """
    if ecs_client is None:
        ecs_client = _get_ecs_client()

    try:
        response = ecs_client.describe_task_definition(taskDefinition=task_definition)
        containers = response.get("taskDefinition", {}).get("containerDefinitions", [])
        return _format_container_definitions(containers)
    except ClientError:
        return []


# pysmelly: ignore inconsistent-error-handling — callers handle ClientError at their boundaries
def run_task(
    cluster_name: str,
    task_definition: str,
    network_config: dict,
    container_name: str,
    command: list[str],
    environment: list[dict] | None = None,
    ecs_client: Any | None = None,
) -> str | None:
    """Run a one-off task with command override.

    Args:
        cluster_name: Name of the ECS cluster.
        task_definition: Task definition ARN or family:revision.
        network_config: Network configuration dict (from get_service_network_config).
        container_name: Name of the container to override.
        command: Command to run as list of strings.
        environment: Optional list of {"name": str, "value": str} env var overrides.
        ecs_client: Optional boto3 ECS client. If None, creates one.

    Returns:
        Task ARN if successful, None otherwise.
    """
    if ecs_client is None:
        ecs_client = _get_ecs_client()

    override = {
        "containerOverrides": [
            {
                "name": container_name,
                "command": command,
            }
        ]
    }

    if environment:
        override["containerOverrides"][0]["environment"] = environment

    try:
        response = ecs_client.run_task(
            cluster=cluster_name,
            taskDefinition=task_definition,
            launchType="FARGATE",
            networkConfiguration=network_config,
            overrides=override,
        )

        tasks = response.get("tasks", [])
        if not tasks:
            failures = response.get("failures", [])
            if failures:
                for f in failures:
                    print(f"  Task failure: {f.get('reason', 'Unknown')}", file=sys.stderr)
            return None

        return tasks[0].get("taskArn")

    except ClientError as e:
        print(f"  Failed to run task: {e}", file=sys.stderr)
        return None


def wait_for_task(
    cluster_name: str,
    task_arn: str,
    timeout: int = 300,
    *,
    ecs_client: Any,
) -> int:
    """Wait for a task to complete and return its exit code.

    Uses AWS waiter for efficient polling instead of manual polling.

    Args:
        cluster_name: Name of the ECS cluster.
        task_arn: ARN of the task to wait for.
        timeout: Maximum seconds to wait (default: 300).
        ecs_client: boto3 ECS client.

    Returns:
        Exit code of the main container, or -1 on error/timeout.
    """
    try:
        # Use AWS waiter for efficient polling
        # Configure waiter with custom delay and max attempts based on timeout
        waiter = ecs_client.get_waiter("tasks_stopped")

        # Calculate max attempts: start with 2s delay, then use exponential backoff
        # Default waiter config is delay=6, max_attempts=100
        # We'll use delay=2 for faster initial response
        delay = 2
        max_attempts = max(1, timeout // delay)

        waiter.wait(
            cluster=cluster_name,
            tasks=[task_arn],
            WaiterConfig={
                "Delay": delay,
                "MaxAttempts": max_attempts,
            },
        )

        # Task stopped, now get the exit code
        response = ecs_client.describe_tasks(
            cluster=cluster_name,
            tasks=[task_arn],
        )

        tasks = response.get("tasks", [])
        if not tasks:
            return -1

        task = tasks[0]

        # Find the exit code from containers
        for container in task.get("containers", []):
            exit_code = container.get("exitCode")
            if exit_code is not None:
                return exit_code

        # If no exit code found, check stop reason
        stop_reason = task.get("stoppedReason", "")
        if stop_reason:
            print(f"  Task stopped: {stop_reason}", file=sys.stderr)
        return -1

    except WaiterError as e:
        print(f"  Task timed out or failed: {e}", file=sys.stderr)
        return -1
    except ClientError as e:
        print(f"  Error waiting for task: {e}", file=sys.stderr)
    return -1


def get_oom_events(
    cluster_name: str,
    service_name: str,
    since_datetime: Any | None,
    since_hours: int = 168,
    *,
    ecs_client: Any,
) -> list[dict]:
    """Get recent OOM (Out of Memory) kill events for a service.

    Detects tasks that were killed due to memory pressure by checking:
    - Exit code 137 (128 + SIGKILL)
    - stoppedReason containing "OutOfMemory"
    - Container exit reasons indicating memory issues

    Args:
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.
        since_hours: How far back to look (default: 168 = 7 days).
            Ignored if since_datetime is provided.
        since_datetime: Optional datetime cutoff. If provided, only returns events after this time.
        ecs_client: boto3 ECS client.

    Returns:
        List of OOM event dicts with task_arn, stopped_at, reason, exit_code.
    """
    try:
        response = ecs_client.list_tasks(
            cluster=cluster_name,
            serviceName=service_name,
            desiredStatus="STOPPED",
        )
        task_arns = response.get("taskArns", [])

        if not task_arns:
            return []

        response = ecs_client.describe_tasks(cluster=cluster_name, tasks=task_arns)
        tasks = response.get("tasks", [])

        if since_datetime is not None:
            cutoff = since_datetime
        else:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        return _filter_oom_tasks(tasks, cutoff)

    except ClientError:
        return []


def _filter_oom_tasks(tasks: list[dict], cutoff) -> list[dict]:
    """Filter tasks for OOM indicators.

    Args:
        tasks: List of task dicts from describe-tasks.
        cutoff: Datetime cutoff - ignore tasks stopped before this.

    Returns:
        List of OOM event dicts.
    """
    oom_events = []

    for task in tasks:
        stopped_at = task.get("stoppedAt")
        if not stopped_at:
            continue

        # Handle both datetime objects and strings
        if isinstance(stopped_at, str):
            # Parse ISO format string
            stopped_at = datetime.fromisoformat(stopped_at.replace("Z", "+00:00"))

        if stopped_at < cutoff:
            continue

        # Check for OOM indicators
        stopped_reason = task.get("stoppedReason", "")
        stop_code = task.get("stopCode", "")
        is_oom = False
        oom_reason = None

        # Check stoppedReason for memory keywords
        memory_keywords = ["OutOfMemory", "out of memory", "OOM", "memory"]
        for keyword in memory_keywords:
            if keyword.lower() in stopped_reason.lower():
                is_oom = True
                oom_reason = stopped_reason
                break

        # Check container exit codes
        for container in task.get("containers", []):
            exit_code = container.get("exitCode")
            container_reason = container.get("reason", "")

            # Exit code 137 = 128 + 9 (SIGKILL) - common for OOM
            if exit_code == 137:
                is_oom = True
                oom_reason = f"Container '{container['name']}' killed with SIGKILL (exit code 137)"
                break

            # Check container reason for memory keywords
            for keyword in memory_keywords:
                if keyword.lower() in container_reason.lower():
                    is_oom = True
                    oom_reason = container_reason
                    break

        if is_oom:
            oom_events.append(
                {
                    "task_arn": task.get("taskArn", ""),
                    "stopped_at": format_iso(stopped_at),
                    "reason": oom_reason or stopped_reason,
                    "stop_code": stop_code,
                }
            )

    return oom_events


# =============================================================================
# Combined operations to reduce API calls
# =============================================================================


def _extract_service_info(service: dict) -> tuple[dict | None, str | None]:
    """Extract network config and task definition from a service response.

    Args:
        service: Service dict from describe-services response.

    Returns:
        Tuple of (network_config, task_definition_arn).
    """
    # Extract network config
    network_config = None
    net_config = service.get("networkConfiguration", {}).get("awsvpcConfiguration")
    if net_config:
        network_config = {
            "awsvpcConfiguration": {
                "subnets": net_config.get("subnets", []),
                "securityGroups": net_config.get("securityGroups", []),
                "assignPublicIp": net_config.get("assignPublicIp", "DISABLED"),
            }
        }

    # Extract task definition
    task_definition = service.get("taskDefinition")

    return network_config, task_definition


def get_service_info(
    cluster_name: str, service_name: str, ecs_client: Any
) -> tuple[dict | None, str | None]:
    """Get network configuration and task definition from a service in one call.

    This combines get_service_network_config and get_service_task_definition
    to avoid redundant describe-services API calls.

    Args:
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.
        ecs_client: boto3 ECS client.

    Returns:
        Tuple of (network_config, task_definition_arn).
        Either or both may be None if not found.
    """
    try:
        response = ecs_client.describe_services(
            cluster=cluster_name,
            services=[service_name],
        )
        services = response.get("services", [])
        if not services:
            return None, None

        return _extract_service_info(services[0])

    except ClientError:
        return None, None


def get_logs_location_from_containers(
    containers: list[dict], container_name: str
) -> tuple[str, str] | None:
    """Get CloudWatch log location from already-fetched container definitions.

    Use this when you already have the container list from get_task_containers()
    to avoid redundant describe-task-definition API calls.

    Args:
        containers: List of container dicts from get_task_containers().
        container_name: Name of the container.

    Returns:
        Tuple of (log_group, stream_prefix), or None if not configured.
    """
    for container in containers:
        if container["name"] == container_name:
            log_config = container.get("logConfiguration")
            if log_config and log_config.get("logDriver") == "awslogs":
                options = log_config.get("options", {})
                log_group = options.get("awslogs-group")
                prefix = options.get("awslogs-stream-prefix", "")
                if log_group:
                    return (log_group, prefix)

    return None
