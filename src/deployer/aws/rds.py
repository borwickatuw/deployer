"""AWS RDS instance operations."""

import json
import time
from collections.abc import Callable
from typing import NamedTuple

from .cli import run_aws


class RdsStatus(NamedTuple):
    """One RDS instance's current state, flattened out of describe-db-instances.

    A ``NamedTuple`` rather than a dataclass, for the reason
    ``emergency.rds.RdsInstanceDetails`` gives: ``identifier`` names the
    instance AWS actually described and has no reader outside the tests, so a
    dataclass would report it as a write-only attribute.
    """

    identifier: str
    status: str
    instance_class: str
    engine: str


# absence sentinel: None means the instance does not exist, and nothing else —
# every other failure raises, per DECISIONS.md 2026-08-18 "Error Contracts".
def get_status(instance_id: str) -> RdsStatus | None:
    """Get RDS instance status.

    Args:
        instance_id: The DB instance identifier.

    Returns:
        RdsStatus for the instance, or None if no such instance exists.

    Raises:
        RuntimeError: If the instance could not be described for any reason
            other than it not existing, or if the output was not valid JSON.
    """
    success, output = run_aws(
        "rds", "describe-db-instances", "--db-instance-identifier", instance_id
    )
    if not success:
        if "DBInstanceNotFound" in output:
            return None
        raise RuntimeError(f"Could not describe RDS instance '{instance_id}': {output}")

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not parse the describe-db-instances response for '{instance_id}'"
        ) from exc

    instances = data.get("DBInstances", [])
    if not instances:
        return None

    inst = instances[0]
    return RdsStatus(
        identifier=inst["DBInstanceIdentifier"],
        status=inst["DBInstanceStatus"],
        instance_class=inst["DBInstanceClass"],
        engine=f"{inst['Engine']} {inst.get('EngineVersion', '')}",
    )


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
        try:
            rds_status = get_status(instance_id)
        except RuntimeError:
            # Deliberate: a describe that fails mid-wait (throttling, a brief
            # credential expiry) reports as "unknown" and the loop tries again.
            # Only the timeout ends this wait, not one bad poll.
            rds_status = None

        current_status = rds_status.status if rds_status else "unknown"

        if rds_status and rds_status.status == target_status:
            return True

        if status_callback:
            status_callback(current_status)

        time.sleep(poll_interval)

    return False
