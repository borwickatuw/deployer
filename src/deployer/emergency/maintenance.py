"""Maintenance operations for emergency response.

Provides functions for:
- Checking pending RDS maintenance
- Checking pending ElastiCache maintenance
"""

from typing import Any

import boto3
from botocore.exceptions import ClientError


def get_rds_client() -> Any:
    """Get boto3 RDS client."""
    return boto3.client("rds")


def get_elasticache_client() -> Any:
    """Get boto3 ElastiCache client."""
    return boto3.client("elasticache")


def get_rds_pending_maintenance(instance_id: str) -> list[dict]:
    """Get pending maintenance actions for an RDS instance.

    Args:
        instance_id: RDS instance identifier

    Returns:
        List of pending maintenance actions:
        [
            {
                "action": "system-update",
                "description": "New PostgreSQL engine version",
                "auto_apply_after": "2026-02-10T00:00:00Z",
                "current_apply_date": "2026-02-15T00:00:00Z",
                "opt_in_status": "next-maintenance",
            },
            ...
        ]
    """
    client = get_rds_client()
    result = []

    try:
        # First get the instance ARN
        response = client.describe_db_instances(DBInstanceIdentifier=instance_id)
        instances = response.get("DBInstances", [])
        if not instances:
            return []

        instance_arn = instances[0].get("DBInstanceArn", "")

        # Then get pending maintenance
        response = client.describe_pending_maintenance_actions(
            ResourceIdentifier=instance_arn
        )

        for resource in response.get("PendingMaintenanceActions", []):
            for action in resource.get("PendingMaintenanceActionDetails", []):
                auto_apply = action.get("AutoAppliedAfterDate")
                if hasattr(auto_apply, "isoformat"):
                    auto_apply = auto_apply.isoformat()

                current_apply = action.get("CurrentApplyDate")
                if hasattr(current_apply, "isoformat"):
                    current_apply = current_apply.isoformat()

                result.append(
                    {
                        "action": action.get("Action", ""),
                        "description": action.get("Description", ""),
                        "auto_apply_after": auto_apply,
                        "current_apply_date": current_apply,
                        "opt_in_status": action.get("OptInStatus", ""),
                    }
                )

    except ClientError:
        pass

    return result


def get_elasticache_pending_maintenance(cluster_id: str) -> list[dict]:
    """Get pending maintenance for an ElastiCache cluster.

    Args:
        cluster_id: ElastiCache cluster identifier

    Returns:
        List of pending maintenance actions
    """
    client = get_elasticache_client()
    result = []

    try:
        response = client.describe_cache_clusters(
            CacheClusterId=cluster_id,
            ShowCacheNodeInfo=True,
        )

        clusters = response.get("CacheClusters", [])
        if not clusters:
            return []

        cluster = clusters[0]

        # Check for pending modified values
        pending = cluster.get("PendingModifiedValues", {})
        if pending:
            for key, value in pending.items():
                if value:
                    result.append(
                        {
                            "action": "modify",
                            "description": f"Pending {key} change to {value}",
                            "auto_apply_after": None,
                            "current_apply_date": None,
                            "opt_in_status": "pending",
                        }
                    )

        # Check service updates
        try:
            updates_response = client.describe_service_updates(
                ServiceUpdateStatus=["available", "scheduled"],
            )

            for update in updates_response.get("ServiceUpdates", []):
                # Check if this update applies to our cluster
                update_name = update.get("ServiceUpdateName", "")
                severity = update.get("ServiceUpdateSeverity", "")

                result.append(
                    {
                        "action": update_name,
                        "description": update.get("ServiceUpdateDescription", ""),
                        "severity": severity,
                        "recommended_apply_by": update.get(
                            "ServiceUpdateRecommendedApplyByDate"
                        ),
                        "opt_in_status": update.get("ServiceUpdateStatus", ""),
                    }
                )
        except ClientError:
            # Service updates API may not be available in all regions
            pass

    except ClientError:
        pass

    return result


def get_all_pending_maintenance(
    rds_instance_id: str | None = None,
    elasticache_cluster_id: str | None = None,
) -> dict:
    """Get all pending maintenance for an environment.

    Args:
        rds_instance_id: RDS instance identifier
        elasticache_cluster_id: ElastiCache cluster identifier

    Returns:
        Dict with maintenance by service:
        {
            "rds": [...],
            "elasticache": [...],
        }
    """
    result = {
        "rds": [],
        "elasticache": [],
    }

    if rds_instance_id:
        result["rds"] = get_rds_pending_maintenance(rds_instance_id)

    if elasticache_cluster_id:
        result["elasticache"] = get_elasticache_pending_maintenance(elasticache_cluster_id)

    return result
