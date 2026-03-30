"""
Lambda handler for staging environment scheduling.

Starts or stops ECS services and RDS based on the action parameter.

Environment variables:
  - ECS_CLUSTER_NAME: Name of the ECS cluster
  - ECS_SERVICES: JSON object mapping service names to replica counts
  - RDS_INSTANCE_ID: RDS instance identifier

Event:
  - action: "start" or "stop"
"""

import json
import logging
import os
import time

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ecs_client = boto3.client("ecs")
rds_client = boto3.client("rds")


def get_env_vars():
    """Get and validate environment variables."""
    cluster_name = os.environ.get("ECS_CLUSTER_NAME")
    services_json = os.environ["ECS_SERVICES"]
    rds_instance_id = os.environ.get("RDS_INSTANCE_ID")

    if not cluster_name:
        raise ValueError("ECS_CLUSTER_NAME environment variable is required")
    if not rds_instance_id:
        raise ValueError("RDS_INSTANCE_ID environment variable is required")

    try:
        services = json.loads(services_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid ECS_SERVICES JSON: {e}")

    return cluster_name, services, rds_instance_id


def _get_rds_status(instance_id: str) -> str:
    """Get current RDS instance status."""
    try:
        response = rds_client.describe_db_instances(
            DBInstanceIdentifier=instance_id
        )
        if response["DBInstances"]:
            return response["DBInstances"][0]["DBInstanceStatus"]
    except Exception as e:
        logger.error(f"Error getting RDS status: {e}")
    return "unknown"


def stop_environment(cluster_name: str, services: dict, rds_instance_id: str):
    """Stop the environment by scaling ECS to 0 and stopping RDS."""
    results = {"ecs": {}, "rds": None}

    # Scale ECS services to 0
    logger.info(f"Scaling ECS services to 0 in cluster {cluster_name}")
    for service_name in services.keys():
        try:
            ecs_client.update_service(
                cluster=cluster_name,
                service=service_name,
                desiredCount=0,
            )
            results["ecs"][service_name] = "scaled to 0"
            logger.info(f"Scaled {service_name} to 0")
        except Exception as e:
            results["ecs"][service_name] = f"error: {e}"
            logger.error(f"Error scaling {service_name}: {e}")

    # Stop RDS instance
    rds_status = _get_rds_status(rds_instance_id)
    logger.info(f"RDS instance {rds_instance_id} status: {rds_status}")

    if rds_status == "stopped":
        results["rds"] = "already stopped"
        logger.info("RDS instance already stopped")
    elif rds_status == "available":
        try:
            rds_client.stop_db_instance(DBInstanceIdentifier=rds_instance_id)
            results["rds"] = "stop initiated"
            logger.info("RDS stop initiated")
        except Exception as e:
            results["rds"] = f"error: {e}"
            logger.error(f"Error stopping RDS: {e}")
    else:
        results["rds"] = f"skipped (status: {rds_status})"
        logger.warning(f"RDS in unexpected state: {rds_status}")

    return results


def start_environment(cluster_name: str, services: dict, rds_instance_id: str):
    """Start the environment by starting RDS and scaling ECS services."""
    results = {"ecs": {}, "rds": None}

    # Start RDS instance first
    rds_status = _get_rds_status(rds_instance_id)
    logger.info(f"RDS instance {rds_instance_id} status: {rds_status}")

    if rds_status == "available":
        results["rds"] = "already running"
        logger.info("RDS instance already running")
    elif rds_status == "stopped":
        try:
            rds_client.start_db_instance(DBInstanceIdentifier=rds_instance_id)
            results["rds"] = "start initiated"
            logger.info("RDS start initiated")

            # Wait briefly for RDS to begin starting
            time.sleep(5)
        except Exception as e:
            results["rds"] = f"error: {e}"
            logger.error(f"Error starting RDS: {e}")
    else:
        results["rds"] = f"in state: {rds_status}"
        logger.info(f"RDS in state: {rds_status}")

    # Scale ECS services to configured replica counts
    logger.info(f"Scaling ECS services in cluster {cluster_name}")
    for service_name, config in services.items():
        replicas = config.get("replicas", 1)
        try:
            ecs_client.update_service(
                cluster=cluster_name,
                service=service_name,
                desiredCount=replicas,
            )
            results["ecs"][service_name] = f"scaled to {replicas}"
            logger.info(f"Scaled {service_name} to {replicas}")
        except Exception as e:
            results["ecs"][service_name] = f"error: {e}"
            logger.error(f"Error scaling {service_name}: {e}")

    return results


def handler(event, context):
    """Lambda handler for start/stop actions."""
    logger.info(f"Received event: {json.dumps(event)}")

    action = event.get("action", "").lower()
    if action not in ("start", "stop"):
        return {
            "statusCode": 400,
            "body": json.dumps({"error": f"Invalid action: {action}. Must be 'start' or 'stop'"})
        }

    try:
        cluster_name, services, rds_instance_id = get_env_vars()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }

    logger.info(f"Environment: cluster={cluster_name}, rds={rds_instance_id}")
    logger.info(f"Services: {list(services.keys())}")

    if action == "stop":
        results = stop_environment(cluster_name, services, rds_instance_id)
    else:
        results = start_environment(cluster_name, services, rds_instance_id)

    logger.info(f"Results: {json.dumps(results)}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "action": action,
            "results": results,
        })
    }
