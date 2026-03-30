"""ECS operations for emergency response.

Provides functions for:
- Listing task definition revisions
- Rolling back services to previous revisions
- Scaling services
- Monitoring deployments
"""

import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .checkpoint import ServiceState


def _get_ecs_client() -> Any:
    """Get boto3 ECS client."""
    return boto3.client("ecs")


def get_service_state(cluster_name: str, service_name: str) -> ServiceState | None:
    """Get current state of an ECS service.

    Args:
        cluster_name: Name of the ECS cluster
        service_name: Name of the service

    Returns:
        ServiceState or None if service not found
    """
    client = _get_ecs_client()
    try:
        response = client.describe_services(
            cluster=cluster_name,
            services=[service_name],
        )
        services = response.get("services", [])
        if not services:
            return None

        svc = services[0]
        return ServiceState(
            task_definition=svc.get("taskDefinition", ""),
            desired_count=svc.get("desiredCount", 0),
            running_count=svc.get("runningCount", 0),
        )
    except ClientError:
        return None


def get_all_services_state(cluster_name: str) -> dict[str, ServiceState]:
    """Get current state of all ECS services in a cluster.

    Args:
        cluster_name: Name of the ECS cluster

    Returns:
        Dict mapping service name to ServiceState
    """
    client = _get_ecs_client()
    result = {}

    try:
        paginator = client.get_paginator("list_services")
        for page in paginator.paginate(cluster=cluster_name):
            service_arns = page.get("serviceArns", [])
            if not service_arns:
                continue

            response = client.describe_services(
                cluster=cluster_name,
                services=service_arns,
            )

            for svc in response.get("services", []):
                name = svc.get("serviceName", "")
                result[name] = ServiceState(
                    task_definition=svc.get("taskDefinition", ""),
                    desired_count=svc.get("desiredCount", 0),
                    running_count=svc.get("runningCount", 0),
                )
    except ClientError:
        pass

    return result


def list_task_definition_revisions(
    family: str,
    max_results: int = 10,
) -> list[dict]:
    """List recent task definition revisions for a family.

    Args:
        family: Task definition family name
        max_results: Maximum number of revisions to return

    Returns:
        List of dicts with revision info, newest first:
        [
            {
                "arn": "arn:aws:ecs:...",
                "revision": 45,
                "registered_at": "2026-02-04T10:30:00Z",
            },
            ...
        ]
    """
    client = _get_ecs_client()
    result = []

    try:
        response = client.list_task_definitions(
            familyPrefix=family,
            sort="DESC",
            maxResults=max_results,
        )

        arns = response.get("taskDefinitionArns", [])
        for arn in arns:
            # Extract revision number from ARN
            revision = int(arn.split(":")[-1])
            result.append(
                {
                    "arn": arn,
                    "revision": revision,
                    "family": family,
                }
            )

        # Get registered timestamps by describing each task definition
        for item in result:
            try:
                desc_response = client.describe_task_definition(taskDefinition=item["arn"])
                task_def = desc_response.get("taskDefinition", {})
                registered_at = task_def.get("registeredAt")
                if registered_at:
                    if hasattr(registered_at, "isoformat"):
                        item["registered_at"] = registered_at.isoformat()
                    else:
                        item["registered_at"] = str(registered_at)
            except ClientError:
                item["registered_at"] = None

    except ClientError:
        pass

    return result


def get_task_definition_details(task_def_arn: str) -> dict | None:
    """Get details of a task definition.

    Args:
        task_def_arn: Task definition ARN

    Returns:
        Dict with task definition details, or None if not found
    """
    client = _get_ecs_client()
    try:
        response = client.describe_task_definition(taskDefinition=task_def_arn)
        task_def = response.get("taskDefinition", {})

        # Extract environment variables from containers
        env_vars = {}
        for container in task_def.get("containerDefinitions", []):
            container_name = container.get("name", "")
            container_env = {}
            for env in container.get("environment", []):
                container_env[env.get("name", "")] = env.get("value", "")
            env_vars[container_name] = container_env

        registered_at = task_def.get("registeredAt")
        if hasattr(registered_at, "isoformat"):
            registered_at = registered_at.isoformat()

        return {
            "arn": task_def.get("taskDefinitionArn", ""),
            "family": task_def.get("family", ""),
            "revision": task_def.get("revision", 0),
            "registered_at": registered_at,
            "cpu": task_def.get("cpu", ""),
            "memory": task_def.get("memory", ""),
            "environment_variables": env_vars,
        }
    except ClientError:
        return None


def compare_task_definitions(arn1: str, arn2: str) -> dict:
    """Compare environment variables between two task definitions.

    Args:
        arn1: First task definition ARN
        arn2: Second task definition ARN

    Returns:
        Dict with changes per container:
        {
            "container_name": {
                "added": {"VAR": "value"},
                "removed": {"VAR": "value"},
                "changed": {"VAR": {"old": "value1", "new": "value2"}},
            }
        }
    """
    details1 = get_task_definition_details(arn1)
    details2 = get_task_definition_details(arn2)

    if not details1 or not details2:
        return {}

    env1 = details1.get("environment_variables", {})
    env2 = details2.get("environment_variables", {})

    result = {}

    # Get all container names
    all_containers = set(env1.keys()) | set(env2.keys())

    for container in all_containers:
        vars1 = env1.get(container, {})
        vars2 = env2.get(container, {})

        added = {k: v for k, v in vars2.items() if k not in vars1}
        removed = {k: v for k, v in vars1.items() if k not in vars2}
        changed = {
            k: {"old": vars1[k], "new": vars2[k]}
            for k in vars1
            if k in vars2 and vars1[k] != vars2[k]
        }

        if added or removed or changed:
            result[container] = {
                "added": added,
                "removed": removed,
                "changed": changed,
            }

    return result


def update_service_task_definition(
    cluster_name: str,
    service_name: str,
    task_definition_arn: str,
) -> bool:
    """Update a service to use a specific task definition.

    Args:
        cluster_name: Name of the ECS cluster
        service_name: Name of the service
        task_definition_arn: Task definition ARN to roll back to

    Returns:
        True if update was initiated, False on error
    """
    client = _get_ecs_client()
    try:
        client.update_service(
            cluster=cluster_name,
            service=service_name,
            taskDefinition=task_definition_arn,
        )
        return True
    except ClientError:
        return False



def wait_for_deployment(
    cluster_name: str,
    service_name: str,
    timeout: int = 300,
    poll_interval: int = 10,
    *,
    callback: Any,
) -> bool:
    """Wait for a service deployment to stabilize.

    Args:
        cluster_name: Name of the ECS cluster
        service_name: Name of the service
        timeout: Maximum seconds to wait
        poll_interval: Seconds between polls
        callback: Callback function called as callback(running, desired) on each poll

    Returns:
        True if deployment stabilized, False on timeout
    """
    client = _get_ecs_client()
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            response = client.describe_services(
                cluster=cluster_name,
                services=[service_name],
            )
            services = response.get("services", [])
            if not services:
                time.sleep(poll_interval)
                continue

            svc = services[0]
            running = svc.get("runningCount", 0)
            desired = svc.get("desiredCount", 0)

            callback(running, desired)

            # Check if stable
            deployments = svc.get("deployments", [])
            if len(deployments) == 1 and running == desired:
                return True

        except ClientError:
            pass

        time.sleep(poll_interval)

    return False


def scale_service(
    cluster_name: str,
    service_name: str,
    desired_count: int,
) -> bool:
    """Scale a service to a specific count.

    Args:
        cluster_name: Name of the ECS cluster
        service_name: Name of the service
        desired_count: Target number of tasks

    Returns:
        True if update was initiated, False on error
    """
    client = _get_ecs_client()
    try:
        client.update_service(
            cluster=cluster_name,
            service=service_name,
            desiredCount=desired_count,
        )
        return True
    except ClientError:
        return False


def force_new_deployment(
    cluster_name: str,
    service_name: str,
) -> bool:
    """Force a new deployment of a service.

    This triggers ECS to replace all tasks with new ones using the current
    task definition. Useful when containers are unhealthy but not being replaced.

    Args:
        cluster_name: Name of the ECS cluster
        service_name: Name of the service

    Returns:
        True if deployment was initiated, False on error
    """
    client = _get_ecs_client()
    try:
        client.update_service(
            cluster=cluster_name,
            service=service_name,
            forceNewDeployment=True,
        )
        return True
    except ClientError:
        return False


