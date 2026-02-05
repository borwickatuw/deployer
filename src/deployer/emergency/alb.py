"""ALB operations for emergency response.

Provides functions for:
- Checking target health
- Getting ALB metrics
"""

from typing import Any

import boto3
from botocore.exceptions import ClientError


def get_elbv2_client() -> Any:
    """Get boto3 ELBv2 client."""
    return boto3.client("elbv2")


def get_target_health(target_group_arn: str) -> list[dict]:
    """Get health status of all targets in a target group.

    Args:
        target_group_arn: ARN of the target group

    Returns:
        List of target health dicts:
        [
            {
                "target_id": "10.0.1.50",
                "port": 8000,
                "health_state": "healthy",
                "reason": None,
                "description": None,
            },
            ...
        ]
    """
    client = get_elbv2_client()
    result = []

    try:
        response = client.describe_target_health(TargetGroupArn=target_group_arn)
        for target in response.get("TargetHealthDescriptions", []):
            target_info = target.get("Target", {})
            health = target.get("TargetHealth", {})
            result.append(
                {
                    "target_id": target_info.get("Id", ""),
                    "port": target_info.get("Port", 0),
                    "health_state": health.get("State", "unknown"),
                    "reason": health.get("Reason"),
                    "description": health.get("Description"),
                }
            )
    except ClientError:
        pass

    return result


def get_unhealthy_targets(target_group_arn: str) -> list[dict]:
    """Get only unhealthy targets from a target group.

    Args:
        target_group_arn: ARN of the target group

    Returns:
        List of unhealthy target dicts
    """
    all_targets = get_target_health(target_group_arn)
    return [t for t in all_targets if t["health_state"] != "healthy"]
