"""AWS ECS service operations."""

import json
import sys
from typing import Any

from botocore.exceptions import ClientError

from ..utils import AWS_REGION, run_command


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

    Can use either boto3 client or AWS CLI depending on what's passed.

    Args:
        cluster_name: Name of the ECS cluster.
        ecs_client: Optional boto3 ECS client. If None, uses AWS CLI.

    Returns:
        List of service dicts with name, arn, desired_count, running_count, status.
    """
    if ecs_client:
        return _get_services_boto3(cluster_name, ecs_client)
    return _get_services_cli(cluster_name)


def _get_services_cli(cluster_name: str) -> list[dict]:
    """List ECS services using AWS CLI."""
    cmd = [
        "aws", "ecs", "list-services",
        "--cluster", cluster_name,
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return []

    data = json.loads(output)
    service_arns = data.get("serviceArns", [])

    if not service_arns:
        return []

    # Get detailed service info
    cmd = [
        "aws", "ecs", "describe-services",
        "--cluster", cluster_name,
        "--services", *service_arns,
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return []

    data = json.loads(output)
    return [_format_service(svc) for svc in data.get("services", [])]


def _get_services_boto3(cluster_name: str, ecs_client: Any) -> list[dict]:
    """List ECS services using boto3 client."""
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


def scale_service(cluster_name: str, service_name: str, desired_count: int) -> bool:
    """Scale an ECS service to the desired count.

    Args:
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service to scale.
        desired_count: Target number of tasks.

    Returns:
        True if successful, False otherwise.
    """
    cmd = [
        "aws", "ecs", "update-service",
        "--cluster", cluster_name,
        "--service", service_name,
        "--desired-count", str(desired_count),
        "--region", AWS_REGION,
    ]
    success, _ = run_command(cmd)
    return success


def get_task_definition_resources(
    task_def_arn: str, ecs_client: Any | None = None
) -> tuple[int, int]:
    """Get CPU and memory allocation from a task definition.

    Args:
        task_def_arn: ARN or family:revision of the task definition.
        ecs_client: Optional boto3 ECS client. If None, uses AWS CLI.

    Returns:
        Tuple of (cpu_units, memory_mb).
    """
    if ecs_client:
        return _get_task_def_resources_boto3(task_def_arn, ecs_client)
    return _get_task_def_resources_cli(task_def_arn)


def _get_task_def_resources_cli(task_def_arn: str) -> tuple[int, int]:
    """Get task definition resources using AWS CLI."""
    cmd = [
        "aws", "ecs", "describe-task-definition",
        "--task-definition", task_def_arn,
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return 256, 512  # Default values

    data = json.loads(output)
    task_def = data.get("taskDefinition", {})
    cpu = int(task_def.get("cpu", 256))
    memory = int(task_def.get("memory", 512))
    return cpu, memory


def _get_task_def_resources_boto3(task_def_arn: str, ecs_client: Any) -> tuple[int, int]:
    """Get task definition resources using boto3 client."""
    try:
        response = ecs_client.describe_task_definition(taskDefinition=task_def_arn)
        task_def = response["taskDefinition"]
        cpu = int(task_def.get("cpu", 256))
        memory = int(task_def.get("memory", 512))
        return cpu, memory
    except ClientError:
        return 256, 512  # Default values


def get_service_network_config(cluster_name: str, service_name: str) -> dict | None:
    """Get network configuration from a running service.

    Args:
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.

    Returns:
        Network configuration dict suitable for run_task, or None if not found.
    """
    cmd = [
        "aws", "ecs", "describe-services",
        "--cluster", cluster_name,
        "--services", service_name,
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return None

    data = json.loads(output)
    services = data.get("services", [])
    if not services:
        return None

    service = services[0]
    net_config = service.get("networkConfiguration", {}).get("awsvpcConfiguration")
    if not net_config:
        return None

    return {
        "awsvpcConfiguration": {
            "subnets": net_config.get("subnets", []),
            "securityGroups": net_config.get("securityGroups", []),
            "assignPublicIp": net_config.get("assignPublicIp", "DISABLED"),
        }
    }


def get_service_task_definition(cluster_name: str, service_name: str) -> str | None:
    """Get the task definition ARN for a service.

    Args:
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.

    Returns:
        Task definition ARN, or None if not found.
    """
    cmd = [
        "aws", "ecs", "describe-services",
        "--cluster", cluster_name,
        "--services", service_name,
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return None

    data = json.loads(output)
    services = data.get("services", [])
    if not services:
        return None

    return services[0].get("taskDefinition")


def get_task_containers(task_definition: str) -> list[dict]:
    """Get container names and info from a task definition.

    Args:
        task_definition: Task definition ARN or family:revision.

    Returns:
        List of dicts with name, image, essential, and logConfiguration.
    """
    cmd = [
        "aws", "ecs", "describe-task-definition",
        "--task-definition", task_definition,
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return []

    data = json.loads(output)
    containers = data.get("taskDefinition", {}).get("containerDefinitions", [])

    return [
        {
            "name": c["name"],
            "image": c.get("image", ""),
            "essential": c.get("essential", True),
            "logConfiguration": c.get("logConfiguration"),
        }
        for c in containers
    ]


def run_task(
    cluster_name: str,
    task_definition: str,
    network_config: dict,
    container_name: str,
    command: list[str],
    environment: list[dict] | None = None,
) -> str | None:
    """Run a one-off task with command override.

    Args:
        cluster_name: Name of the ECS cluster.
        task_definition: Task definition ARN or family:revision.
        network_config: Network configuration dict (from get_service_network_config).
        container_name: Name of the container to override.
        command: Command to run as list of strings.
        environment: Optional list of {"name": str, "value": str} env var overrides.

    Returns:
        Task ARN if successful, None otherwise.
    """
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

    cmd = [
        "aws", "ecs", "run-task",
        "--cluster", cluster_name,
        "--task-definition", task_definition,
        "--launch-type", "FARGATE",
        "--network-configuration", json.dumps(network_config),
        "--overrides", json.dumps(override),
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return None

    data = json.loads(output)
    tasks = data.get("tasks", [])
    if not tasks:
        failures = data.get("failures", [])
        if failures:
            for f in failures:
                print(f"  Task failure: {f.get('reason', 'Unknown')}", file=sys.stderr)
        return None

    return tasks[0].get("taskArn")


def wait_for_task(cluster_name: str, task_arn: str, timeout: int = 300) -> int:
    """Wait for a task to complete and return its exit code.

    Args:
        cluster_name: Name of the ECS cluster.
        task_arn: ARN of the task to wait for.
        timeout: Maximum seconds to wait (default: 300).

    Returns:
        Exit code of the main container, or -1 on error/timeout.
    """
    import time

    start_time = time.time()
    poll_interval = 5

    while time.time() - start_time < timeout:
        cmd = [
            "aws", "ecs", "describe-tasks",
            "--cluster", cluster_name,
            "--tasks", task_arn,
            "--region", AWS_REGION,
        ]
        success, output = run_command(cmd)
        if not success:
            return -1

        data = json.loads(output)
        tasks = data.get("tasks", [])
        if not tasks:
            return -1

        task = tasks[0]
        status = task.get("lastStatus")

        if status == "STOPPED":
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

        time.sleep(poll_interval)

    print("  Task timed out", file=sys.stderr)
    return -1


def get_oom_events(
    cluster_name: str,
    service_name: str,
    since_hours: int = 168,
    since_datetime: Any | None = None,
    ecs_client: Any | None = None,
) -> list[dict]:
    """Get recent OOM (Out of Memory) kill events for a service.

    Detects tasks that were killed due to memory pressure by checking:
    - Exit code 137 (128 + SIGKILL)
    - stoppedReason containing "OutOfMemory"
    - Container exit reasons indicating memory issues

    Args:
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.
        since_hours: How far back to look (default: 168 = 7 days). Ignored if since_datetime is provided.
        since_datetime: Optional datetime cutoff. If provided, only returns events after this time.
        ecs_client: Optional boto3 ECS client. If None, uses AWS CLI.

    Returns:
        List of OOM event dicts with task_arn, stopped_at, reason, exit_code.
    """
    if ecs_client:
        return _get_oom_events_boto3(cluster_name, service_name, since_hours, since_datetime, ecs_client)
    return _get_oom_events_cli(cluster_name, service_name, since_hours, since_datetime)


def _get_oom_events_cli(
    cluster_name: str, service_name: str, since_hours: int, since_datetime: Any | None = None
) -> list[dict]:
    """Get OOM events using AWS CLI."""
    from datetime import datetime, timedelta, timezone

    # List stopped tasks for this service
    cmd = [
        "aws", "ecs", "list-tasks",
        "--cluster", cluster_name,
        "--service-name", service_name,
        "--desired-status", "STOPPED",
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return []

    data = json.loads(output)
    task_arns = data.get("taskArns", [])

    if not task_arns:
        return []

    # Describe tasks to get stop reasons
    cmd = [
        "aws", "ecs", "describe-tasks",
        "--cluster", cluster_name,
        "--tasks", *task_arns,
        "--region", AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return []

    data = json.loads(output)
    tasks = data.get("tasks", [])

    # Use since_datetime if provided, otherwise calculate from since_hours
    if since_datetime is not None:
        cutoff = since_datetime
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    return _filter_oom_tasks(tasks, cutoff)


def _get_oom_events_boto3(
    cluster_name: str, service_name: str, since_hours: int, since_datetime: Any | None, ecs_client: Any
) -> list[dict]:
    """Get OOM events using boto3 client."""
    from datetime import datetime, timedelta, timezone

    try:
        # List stopped tasks
        response = ecs_client.list_tasks(
            cluster=cluster_name,
            serviceName=service_name,
            desiredStatus="STOPPED",
        )
        task_arns = response.get("taskArns", [])

        if not task_arns:
            return []

        # Describe tasks
        response = ecs_client.describe_tasks(cluster=cluster_name, tasks=task_arns)
        tasks = response.get("tasks", [])

        # Use since_datetime if provided, otherwise calculate from since_hours
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
    from datetime import datetime, timezone

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
            oom_events.append({
                "task_arn": task.get("taskArn", ""),
                "stopped_at": stopped_at.isoformat() if hasattr(stopped_at, "isoformat") else str(stopped_at),
                "reason": oom_reason or stopped_reason,
                "stop_code": stop_code,
            })

    return oom_events


def get_task_logs_location(
    task_definition: str, container_name: str
) -> tuple[str, str] | None:
    """Get CloudWatch log group and stream prefix for a container.

    Args:
        task_definition: Task definition ARN or family:revision.
        container_name: Name of the container.

    Returns:
        Tuple of (log_group, stream_prefix), or None if not configured.
    """
    containers = get_task_containers(task_definition)

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
