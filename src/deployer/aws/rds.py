"""AWS RDS instance operations."""

import time
from collections.abc import Callable

from .cli import run_aws, run_aws_json

# query function, None means "not found"  (re-evaluate-by: 2026-11 review)


# pysmelly: ignore return-none-instead-of-raise
def get_status(instance_id: str) -> dict | None:
    """Get RDS instance status.

    Args:
        instance_id: The DB instance identifier.

    Returns:
        Dict with identifier, status, instance_class, engine, or None if not found.
    """
    data = run_aws_json("rds", "describe-db-instances", "--db-instance-identifier", instance_id)
    if not data:
        return None

    instances = data.get("DBInstances", [])
    if not instances:
        return None

    inst = instances[0]
    return {
        "identifier": inst["DBInstanceIdentifier"],
        "status": inst["DBInstanceStatus"],
        "instance_class": inst["DBInstanceClass"],
        "engine": f"{inst['Engine']} {inst.get('EngineVersion', '')}",
    }


def _instance_action(operation: str, instance_id: str) -> bool:
    """Run an RDS operation whose only argument is an instance identifier.

    Args:
        operation: The rds subcommand, e.g. "stop-db-instance".
        instance_id: The DB instance identifier.

    Returns:
        True if the command succeeded, False otherwise.
    """
    success, _ = run_aws("rds", operation, "--db-instance-identifier", instance_id)
    return success


def stop(instance_id: str) -> bool:
    """Stop an RDS instance.

    Args:
        instance_id: The DB instance identifier.

    Returns:
        True if the stop command succeeded, False otherwise.
    """
    return _instance_action("stop-db-instance", instance_id)


def start(instance_id: str) -> bool:
    """Start a stopped RDS instance.

    Args:
        instance_id: The DB instance identifier.

    Returns:
        True if the start command succeeded, False otherwise.
    """
    return _instance_action("start-db-instance", instance_id)


def wait_for_status(
    instance_id: str,
    target_status: str,
    status_callback: Callable[[str], None] | None,
    timeout: int = 600,
    poll_interval: int = 15,
) -> bool:
    """Wait for RDS instance to reach a target status.

    Args:
        instance_id: The DB instance identifier.
        target_status: The status to wait for (e.g., "available", "stopped").
        timeout: Maximum seconds to wait (default: 600).
        poll_interval: Seconds between status checks (default: 15).
        status_callback: Optional callback(status_str) called on each poll.

    Returns:
        True if target status reached, False if timeout.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        rds_status = get_status(instance_id)
        current_status = rds_status["status"] if rds_status else "unknown"

        if rds_status and rds_status["status"] == target_status:
            return True

        if status_callback:
            status_callback(current_status)

        time.sleep(poll_interval)

    return False
