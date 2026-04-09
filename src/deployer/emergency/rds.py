"""RDS operations for emergency response.

Provides functions for:
- Creating emergency snapshots
- Listing snapshots
- Restoring from snapshots or point-in-time
"""

from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError


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
    now = datetime.now(timezone.utc)
    return f"{instance_id}-emergency-{now.strftime('%Y-%m-%d-%H%M%S')}"


def create_emergency_snapshot(
    instance_id: str,
    wait: bool = True,
    timeout: int = 600,
) -> str | None:
    """Create an emergency snapshot of an RDS instance.

    Args:
        instance_id: RDS instance identifier
        wait: If True, wait for snapshot to complete
        timeout: Maximum seconds to wait

    Returns:
        Snapshot identifier if successful, None on error
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
                    "Delay": 15,
                    "MaxAttempts": timeout // 15,
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
            created_at = snapshot.get("SnapshotCreateTime")
            if hasattr(created_at, "isoformat"):
                created_at = created_at.isoformat()

            result.append(
                {
                    "id": snapshot.get("DBSnapshotIdentifier", ""),
                    "created_at": created_at,
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
                created_at = snapshot.get("SnapshotCreateTime")
                if hasattr(created_at, "isoformat"):
                    created_at = created_at.isoformat()

                result.append(
                    {
                        "id": snapshot.get("DBSnapshotIdentifier", ""),
                        "created_at": created_at,
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

    except ClientError:
        return []


def get_rds_instance_details(instance_id: str) -> dict | None:
    """Get details of an RDS instance.

    Args:
        instance_id: RDS instance identifier

    Returns:
        Dict with instance details, or None if not found
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
        return {
            "id": inst.get("DBInstanceIdentifier", ""),
            "status": inst.get("DBInstanceStatus", ""),
            "instance_class": inst.get("DBInstanceClass", ""),
            "engine": inst.get("Engine", ""),
            "engine_version": inst.get("EngineVersion", ""),
            "endpoint": inst.get("Endpoint", {}).get("Address"),
            "port": inst.get("Endpoint", {}).get("Port"),
            "vpc_security_groups": [
                sg.get("VpcSecurityGroupId") for sg in inst.get("VpcSecurityGroups", [])
            ],
            "db_subnet_group": inst.get("DBSubnetGroup", {}).get("DBSubnetGroupName"),
            "latest_restorable_time": inst.get("LatestRestorableTime"),
        }
    except ClientError:
        return None


def _prepare_restore(source_instance_id: str, target_suffix: str) -> tuple[str, dict] | None:
    """Common setup for restore operations: get source details and build target ID.

    Returns:
        Tuple of (target_id, source_details), or None if source instance not found.
    """
    target_id = f"{source_instance_id}{target_suffix}"
    source_details = get_rds_instance_details(source_instance_id)
    if not source_details:
        return None
    return target_id, source_details


def _handle_restore_error(e: ClientError, target_id: str) -> dict | None:
    """Handle ClientError from a restore operation.

    Returns an error dict for DBInstanceAlreadyExists, None otherwise.
    """
    error_code = e.response.get("Error", {}).get("Code", "")
    if error_code == "DBInstanceAlreadyExists":
        return {
            "instance_id": target_id,
            "status": "error",
            "message": (
                f"Instance '{target_id}' already exists. Delete it first with:\n"
                f"  aws rds delete-db-instance "
                f"--db-instance-identifier {target_id} --skip-final-snapshot"
            ),
        }
    return None


def restore_from_snapshot(
    source_instance_id: str,
    snapshot_id: str,
    target_suffix: str = "-restore",
) -> dict | None:
    """Restore a database from a snapshot to a new instance.

    Creates a new RDS instance with a suffix appended to the original name.
    The original instance is NOT modified.

    Args:
        source_instance_id: Original RDS instance identifier
        snapshot_id: Snapshot identifier to restore from
        target_suffix: Suffix for the new instance name

    Returns:
        Dict with new instance info, or None on error:
        {
            "instance_id": "myapp-production-db-restore",
            "status": "creating",
            "message": "Restore initiated. Instance will be available in 10-30 minutes.",
        }
    """
    result = _prepare_restore(source_instance_id, target_suffix)
    if not result:
        return None
    target_id, source_details = result

    try:
        _get_rds_client().restore_db_instance_from_db_snapshot(
            DBInstanceIdentifier=target_id,
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceClass=source_details["instance_class"],
            DBSubnetGroupName=source_details["db_subnet_group"],
            VpcSecurityGroupIds=source_details["vpc_security_groups"],
            PubliclyAccessible=False,
        )

        return {
            "instance_id": target_id,
            "status": "creating",
            "source_snapshot": snapshot_id,
            "message": (
                f"Restore initiated. Instance '{target_id}' will be available "
                "in 10-30 minutes. Check status with:\n"
                f"  aws rds describe-db-instances --db-instance-identifier {target_id}"
            ),
        }
    except ClientError as e:
        return _handle_restore_error(e, target_id)


def restore_from_point_in_time(
    source_instance_id: str,
    restore_time: datetime,
    target_suffix: str = "-restore",
) -> dict | None:
    """Restore a database to a point in time.

    Creates a new RDS instance with a suffix appended to the original name.
    The original instance is NOT modified.

    Args:
        source_instance_id: Original RDS instance identifier
        restore_time: Point in time to restore to (UTC)
        target_suffix: Suffix for the new instance name

    Returns:
        Dict with new instance info, or None on error
    """
    result = _prepare_restore(source_instance_id, target_suffix)
    if not result:
        return None
    target_id, source_details = result

    # Check if restore time is valid
    latest_restorable = source_details.get("latest_restorable_time")
    if latest_restorable and restore_time > latest_restorable:
        return {
            "instance_id": target_id,
            "status": "error",
            "message": (
                f"Restore time {restore_time.isoformat()} is after the latest "
                f"restorable time {latest_restorable.isoformat()}."
            ),
        }

    try:
        _get_rds_client().restore_db_instance_to_point_in_time(
            SourceDBInstanceIdentifier=source_instance_id,
            TargetDBInstanceIdentifier=target_id,
            RestoreTime=restore_time,
            DBInstanceClass=source_details["instance_class"],
            DBSubnetGroupName=source_details["db_subnet_group"],
            VpcSecurityGroupIds=source_details["vpc_security_groups"],
            PubliclyAccessible=False,
        )

        return {
            "instance_id": target_id,
            "status": "creating",
            "restore_time": restore_time.isoformat(),
            "message": (
                f"Point-in-time restore initiated. Instance '{target_id}' will be "
                "available in 10-30 minutes. Check status with:\n"
                f"  aws rds describe-db-instances --db-instance-identifier {target_id}"
            ),
        }
    except ClientError as e:
        return _handle_restore_error(e, target_id)
