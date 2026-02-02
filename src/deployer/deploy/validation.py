"""Pre-flight validation functions for deployment."""

from botocore.exceptions import ClientError


def validate_ecs_cluster(ecs_client, cluster_name: str) -> tuple[bool, str | None]:
    """Validate that the ECS cluster exists and is active.

    Args:
        ecs_client: boto3 ECS client.
        cluster_name: Name of the ECS cluster to validate.

    Returns:
        Tuple of (exists, error_message). If exists is True, error_message is None.
    """
    try:
        response = ecs_client.describe_clusters(clusters=[cluster_name])
        clusters = response.get("clusters", [])

        if not clusters:
            # Check for failures (e.g., cluster doesn't exist)
            failures = response.get("failures", [])
            if failures:
                reason = failures[0].get("reason", "MISSING")
                return False, (
                    f"ECS cluster '{cluster_name}' not found ({reason}).\n"
                    f"Run OpenTofu to create the infrastructure:\n"
                    f"  ./bin/tofu.sh <environment> apply"
                )
            return False, f"ECS cluster '{cluster_name}' not found."

        cluster = clusters[0]
        status = cluster.get("status")

        if status != "ACTIVE":
            return False, (
                f"ECS cluster '{cluster_name}' exists but status is '{status}' (expected ACTIVE).\n"
                f"The cluster may be provisioning or deprovisioning."
            )

        return True, None

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        return False, f"Error checking ECS cluster '{cluster_name}': {error_code} - {error_msg}"
