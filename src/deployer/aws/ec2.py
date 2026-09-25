"""AWS EC2 instance operations: the GPU container instance's lifecycle.

The environment's GPU container instance (modules/ecs-gpu-capacity) is
stopped and started with the rest of the environment by
``bin/environment.py``; these are the three calls that needs.
"""

from typing import Any

import boto3
from botocore.exceptions import ClientError

from ..utils import AWS_REGION


def _get_ec2_client() -> Any:
    """Get a boto3 EC2 client using the current AWS profile."""
    return boto3.client("ec2", region_name=AWS_REGION)


# absence sentinel: None means the instance does not exist, and nothing else —
# every other failure raises, per DECISIONS.md 2026-08-18 "Error Contracts".
def get_instance_state(instance_id: str, ec2_client: Any | None = None) -> str | None:
    """Get an EC2 instance's state name.

    Args:
        instance_id: The instance id (i-...).
        ec2_client: Optional boto3 EC2 client. If None, creates one.

    Returns:
        The state name ("running", "stopped", "stopping", "pending", ...), or
        None if no such instance exists.

    Raises:
        RuntimeError: If the instance could not be described for any reason
            other than it not existing.
    """
    client: Any = _get_ec2_client() if ec2_client is None else ec2_client

    try:
        response = client.describe_instances(InstanceIds=[instance_id])
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
            return None
        raise RuntimeError(f"Could not describe EC2 instance '{instance_id}': {e}") from e

    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    if not instances:
        return None
    return instances[0]["State"]["Name"]


def _instance_action(operation: str, instance_id: str, ec2_client: Any | None) -> bool:
    client: Any = _get_ec2_client() if ec2_client is None else ec2_client
    try:
        getattr(client, operation)(InstanceIds=[instance_id])
        return True
    except ClientError:
        return False


def start_instance(instance_id: str, ec2_client: Any | None = None) -> bool:
    """Start a stopped EC2 instance.

    Args:
        instance_id: The instance id.
        ec2_client: Optional boto3 EC2 client. If None, creates one.

    Returns:
        True if the start was initiated, False if AWS rejected it -- a
        failure sentinel the caller branches on, not an absence one.
    """
    return _instance_action("start_instances", instance_id, ec2_client)


def stop_instance(instance_id: str, ec2_client: Any | None = None) -> bool:
    """Stop a running EC2 instance.

    Args:
        instance_id: The instance id.
        ec2_client: Optional boto3 EC2 client. If None, creates one.

    Returns:
        True if the stop was initiated, False if AWS rejected it.
    """
    return _instance_action("stop_instances", instance_id, ec2_client)
