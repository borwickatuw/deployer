"""AWS RDS instance operations."""

import json
import time
from collections.abc import Callable

from ..utils import AWS_REGION, run_command


# pysmelly: ignore return-none-instead-of-raise — query function, None means "not found"
def get_status(instance_id: str) -> dict | None:
    """Get RDS instance status.

    Args:
        instance_id: The DB instance identifier.

    Returns:
        Dict with identifier, status, instance_class, engine, or None if not found.
    """
    cmd = [
        "aws",
        "rds",
        "describe-db-instances",
        "--db-instance-identifier",
        instance_id,
        "--region",
        AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return None

    data = json.loads(output)
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


def stop(instance_id: str) -> bool:
    """Stop an RDS instance.

    Args:
        instance_id: The DB instance identifier.

    Returns:
        True if the stop command succeeded, False otherwise.
    """
    cmd = [
        "aws",
        "rds",
        "stop-db-instance",
        "--db-instance-identifier",
        instance_id,
        "--region",
        AWS_REGION,
    ]
    success, _ = run_command(cmd)
    return success


def start(instance_id: str) -> bool:
    """Start a stopped RDS instance.

    Args:
        instance_id: The DB instance identifier.

    Returns:
        True if the start command succeeded, False otherwise.
    """
    cmd = [
        "aws",
        "rds",
        "start-db-instance",
        "--db-instance-identifier",
        instance_id,
        "--region",
        AWS_REGION,
    ]
    success, _ = run_command(cmd)
    return success


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
