"""RDS operations for emergency response.

Provides functions for:
- Creating emergency snapshots
- Listing snapshots
- Restoring from snapshots or point-in-time
"""

from datetime import UTC, datetime
from typing import Any, NamedTuple

import boto3
from botocore.exceptions import ClientError

from ..aws.rds import POLL_INTERVAL_SECONDS, WAIT_TIMEOUT_SECONDS
from ..utils import format_iso


class RdsInstanceDetails(NamedTuple):
    """One RDS instance as described by ``describe_db_instances``.

    A ``NamedTuple`` rather than a dataclass: most of these fields exist to
    describe the instance to an operator, and only ``instance_class``,
    ``db_subnet_group``, ``vpc_security_groups`` and ``latest_restorable_time``
    are read in production. A dataclass would report the rest as
    write-only attributes; a NamedTuple carries them honestly.
    """

    id: str
    status: str
    instance_class: str
    engine: str
    engine_version: str
    endpoint: str | None
    port: int | None
    vpc_security_groups: list[str]
    db_subnet_group: str | None
    latest_restorable_time: datetime | None


class RestoreResult(NamedTuple):
    """The outcome of a restore request, success or expected failure.

    Both restore entry points answer with this shape, and so does
    ``_handle_restore_error``, which already served both. ``status`` is
    ``"creating"`` for an initiated restore and ``"error"`` for one that was
    rejected before it started; the two optional fields record which kind of
    restore was asked for. A ``None`` return -- distinct from an error result
    -- means the source instance does not exist; one that could not be *read*
    now raises (Phase 53i-3b).
    """

    instance_id: str
    status: str
    message: str
    source_snapshot: str | None = None
    restore_time: str | None = None


def _get_rds_client() -> Any:
    """Get boto3 RDS client."""
    return boto3.client("rds")


def generate_emergency_snapshot_id(instance_id: str) -> str:
    """Generate a unique snapshot ID for emergency operations.

    Args:
        instance_id: RDS instance identifier

    Returns:
        Snapshot ID like 'myapp-production-db-emergency-2026-02-04-120000'
    """
    now = datetime.now(UTC)
    return f"{instance_id}-emergency-{now.strftime('%Y-%m-%d-%H%M%S')}"


def create_emergency_snapshot(
    instance_id: str,
    wait: bool = True,
    timeout: int = WAIT_TIMEOUT_SECONDS,
) -> str | None:
    """Create an emergency snapshot of an RDS instance.

    Args:
        instance_id: RDS instance identifier
        wait: If True, wait for snapshot to complete
        timeout: Maximum seconds to wait (default:
            ``aws.rds.WAIT_TIMEOUT_SECONDS``, shared with the instance-status
            wait so the two RDS wait budgets cannot drift apart)

    Returns:
        The snapshot identifier if the snapshot was created, or None if AWS
        rejected the request. This is a *failure* sentinel, not an absence
        one, and the caller branches on it (PYTHON.md #19, contract 3).

    Raises:
        BotoCoreError: Connectivity and configuration failures are not caught;
            only ClientError is.
    """
    client = _get_rds_client()
    snapshot_id = generate_emergency_snapshot_id(instance_id)

    try:
        client.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=instance_id,
        )

        if wait:
            waiter = client.get_waiter("db_snapshot_available")
            waiter.wait(
                DBSnapshotIdentifier=snapshot_id,
                WaiterConfig={
                    "Delay": POLL_INTERVAL_SECONDS,
                    "MaxAttempts": timeout // POLL_INTERVAL_SECONDS,
                },
            )

        return snapshot_id
    except ClientError:
        return None


def get_rds_snapshots(
    instance_id: str,
    max_results: int = 10,
    include_automated: bool = True,
) -> list[dict]:
    """List RDS snapshots for an instance.

    Args:
        instance_id: RDS instance identifier
        max_results: Maximum number of snapshots to return
        include_automated: Include automated backups

    Returns:
        List of snapshot dicts, newest first:
        [
            {
                "id": "snapshot-id",
                "created_at": "2026-02-04T10:30:00Z",
                "status": "available",
                "type": "manual",
                "engine": "postgres",
            },
            ...
        ]
        Empty only when the instance genuinely has no snapshots.

    Raises:
        RuntimeError: If the snapshots could not be listed. An empty list
            meaning both "no backups exist" and "I could not check" is the
            worst possible answer during an incident.
    """
    client = _get_rds_client()
    result = []

    try:
        # Get manual snapshots
        response = client.describe_db_snapshots(
            DBInstanceIdentifier=instance_id,
            SnapshotType="manual",
        )

        for snapshot in response.get("DBSnapshots", []):
            result.append(
                {
                    "id": snapshot.get("DBSnapshotIdentifier", ""),
                    "created_at": format_iso(snapshot.get("SnapshotCreateTime")),
                    "status": snapshot.get("Status", ""),
                    "type": "manual",
                    "engine": snapshot.get("Engine", ""),
                    "storage_gb": snapshot.get("AllocatedStorage", 0),
                }
            )

        # Optionally get automated backups
        if include_automated:
            response = client.describe_db_snapshots(
                DBInstanceIdentifier=instance_id,
                SnapshotType="automated",
            )

            for snapshot in response.get("DBSnapshots", []):
                result.append(
                    {
                        "id": snapshot.get("DBSnapshotIdentifier", ""),
                        "created_at": format_iso(snapshot.get("SnapshotCreateTime")),
                        "status": snapshot.get("Status", ""),
                        "type": "automated",
                        "engine": snapshot.get("Engine", ""),
                        "storage_gb": snapshot.get("AllocatedStorage", 0),
                    }
                )

        # Sort by created_at, newest first
        result.sort(
            key=lambda s: s.get("created_at", "") or "",
            reverse=True,
        )

        return result[:max_results]

    except ClientError as e:
        raise RuntimeError(f"Could not list snapshots for instance '{instance_id}': {e}") from e


def get_rds_instance_details(instance_id: str) -> RdsInstanceDetails | None:
    """Get details of an RDS instance.

    Args:
        instance_id: RDS instance identifier

    Returns:
        RdsInstanceDetails, or None if the instance genuinely does not exist.
        AWS reports that two ways -- an empty DBInstances list, and a
        DBInstanceNotFoundFault -- and both mean absence.

    Raises:
        RuntimeError: If the instance exists but could not be read: a
            permissions gap, a throttle, a network failure. Callers act on the
            None (restore has no source) and must not act on it for a failure
            they were never told about.
    """
    client = _get_rds_client()
    try:
        response = client.describe_db_instances(
            DBInstanceIdentifier=instance_id,
        )
        instances = response.get("DBInstances", [])
        if not instances:
            return None

        inst = instances[0]
        return RdsInstanceDetails(
            id=inst.get("DBInstanceIdentifier", ""),
            status=inst.get("DBInstanceStatus", ""),
            instance_class=inst.get("DBInstanceClass", ""),
            engine=inst.get("Engine", ""),
            engine_version=inst.get("EngineVersion", ""),
            endpoint=inst.get("Endpoint", {}).get("Address"),
            port=inst.get("Endpoint", {}).get("Port"),
            vpc_security_groups=[
                sg.get("VpcSecurityGroupId") for sg in inst.get("VpcSecurityGroups", [])
            ],
            db_subnet_group=inst.get("DBSubnetGroup", {}).get("DBSubnetGroupName"),
            latest_restorable_time=inst.get("LatestRestorableTime"),
        )
    except ClientError as e:
        # The error *code* is "DBInstanceNotFound"; "DBInstanceNotFoundFault"
        # is the botocore exception class. Matched by code to stay consistent
        # with _handle_restore_error() below, which keys on the same field.
        if e.response.get("Error", {}).get("Code", "") == "DBInstanceNotFound":
            return None
        raise RuntimeError(f"Could not read RDS instance '{instance_id}': {e}") from e


def _prepare_restore(
    source_instance_id: str, target_suffix: str
) -> tuple[str, RdsInstanceDetails] | None:
    """Common setup for restore operations: get source details and build target ID.

    Returns:
        Tuple of (target_id, source_details), or None if the source instance
        genuinely does not exist.

    Raises:
        RuntimeError: If the source instance could not be read.
    """
    target_id = f"{source_instance_id}{target_suffix}"
    source_details = get_rds_instance_details(source_instance_id)
    if not source_details:
        return None
    return target_id, source_details


def _handle_restore_error(e: ClientError, target_id: str) -> RestoreResult:
    """Handle ClientError from a restore operation.

    Returns an error RestoreResult for DBInstanceAlreadyExists. Re-raises all
    other ClientErrors so they propagate to the caller instead of being
    swallowed.
    """
    error_code = e.response.get("Error", {}).get("Code", "")
    if error_code == "DBInstanceAlreadyExists":
        return RestoreResult(
            instance_id=target_id,
            status="error",
            message=(
                f"Instance '{target_id}' already exists. Delete it first with:\n"
                f"  aws rds delete-db-instance "
                f"--db-instance-identifier {target_id} --skip-final-snapshot"
            ),
        )
    raise e


def restore_from_snapshot(
    source_instance_id: str,
    snapshot_id: str,
    target_suffix: str = "-restore",
) -> RestoreResult | None:
    """Restore a database from a snapshot to a new instance.

    Creates a new RDS instance with a suffix appended to the original name.
    The original instance is NOT modified.

    Args:
        source_instance_id: Original RDS instance identifier
        snapshot_id: Snapshot identifier to restore from
        target_suffix: Suffix for the new instance name

    Returns:
        RestoreResult with status "creating" if the restore was initiated or
        "error" if the target already exists, or None if the source instance
        does not exist.

    Raises:
        RuntimeError: If the source instance could not be read.
        ClientError: Any restore failure other than DBInstanceAlreadyExists.
    """
    result = _prepare_restore(source_instance_id, target_suffix)
    if not result:
        return None
    target_id, source_details = result

    try:
        _get_rds_client().restore_db_instance_from_db_snapshot(
            DBInstanceIdentifier=target_id,
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceClass=source_details.instance_class,
            DBSubnetGroupName=source_details.db_subnet_group,
            VpcSecurityGroupIds=source_details.vpc_security_groups,
            PubliclyAccessible=False,
        )

        return RestoreResult(
            instance_id=target_id,
            status="creating",
            source_snapshot=snapshot_id,
            message=(
                f"Restore initiated. Instance '{target_id}' will be available "
                "in 10-30 minutes. Check status with:\n"
                f"  aws rds describe-db-instances --db-instance-identifier {target_id}"
            ),
        )
    except ClientError as e:
        return _handle_restore_error(e, target_id)


def restore_from_point_in_time(
    source_instance_id: str,
    restore_time: datetime,
    target_suffix: str = "-restore",
) -> RestoreResult | None:
    """Restore a database to a point in time.

    Creates a new RDS instance with a suffix appended to the original name.
    The original instance is NOT modified.

    Args:
        source_instance_id: Original RDS instance identifier
        restore_time: Point in time to restore to (UTC)
        target_suffix: Suffix for the new instance name

    Returns:
        RestoreResult with status "creating" if the restore was initiated or
        "error" if the requested time is unreachable or the target already
        exists, or None if the source instance does not exist.

    Raises:
        RuntimeError: If the source instance could not be read.
        ClientError: Any restore failure other than DBInstanceAlreadyExists.
    """
    result = _prepare_restore(source_instance_id, target_suffix)
    if not result:
        return None
    target_id, source_details = result

    # Check if restore time is valid
    latest_restorable = source_details.latest_restorable_time
    if latest_restorable and restore_time > latest_restorable:
        return RestoreResult(
            instance_id=target_id,
            status="error",
            message=(
                f"Restore time {restore_time.isoformat()} is after the latest "
                f"restorable time {latest_restorable.isoformat()}."
            ),
        )

    try:
        _get_rds_client().restore_db_instance_to_point_in_time(
            SourceDBInstanceIdentifier=source_instance_id,
            TargetDBInstanceIdentifier=target_id,
            RestoreTime=restore_time,
            DBInstanceClass=source_details.instance_class,
            DBSubnetGroupName=source_details.db_subnet_group,
            VpcSecurityGroupIds=source_details.vpc_security_groups,
            PubliclyAccessible=False,
        )

        return RestoreResult(
            instance_id=target_id,
            status="creating",
            restore_time=restore_time.isoformat(),
            message=(
                f"Point-in-time restore initiated. Instance '{target_id}' will be "
                "available in 10-30 minutes. Check status with:\n"
                f"  aws rds describe-db-instances --db-instance-identifier {target_id}"
            ),
        )
    except ClientError as e:
        return _handle_restore_error(e, target_id)
