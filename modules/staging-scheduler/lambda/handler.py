"""
Lambda handler for staging environment scheduling.

Starts or stops ECS services and RDS based on the action parameter.

Environment variables:
  - ECS_CLUSTER_NAME: Name of the ECS cluster
  - ECS_SERVICES: JSON object mapping each service name to an object with a
    "replicas" key, e.g. {"web": {"replicas": 2}} (main.tf builds it from the
    module's ecs_services variable)
  - RDS_INSTANCE_ID: RDS instance identifier
  - LOG_LEVEL: Python log level (optional; defaults to INFO)

Event:
  - action: "start" or "stop"

Error contract:
  Every AWS step is attempted even after an earlier one fails -- a scheduler
  that abandons the remaining services on the first error leaves the
  environment half-scaled -- but a step that failed is reported as a failure,
  not absorbed. Two rules follow from that:

  - Only the AWS failure modes (ClientError, BotoCoreError) are caught and
    recorded. Anything else is a bug in this handler; it escapes so the
    invocation fails and the Lambda Errors metric fires, rather than being
    written into the results dict as "error: ..." under a 200.
  - handler() answers 500 when any step reported an error. A scheduler that
    half-ran and answered 200 is invisible to its invoker: EventBridge records
    a success and the only evidence is a log line nobody reads.
"""

import json
import logging
import os
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# LOG_LEVEL is the fleet-wide level control (claude-meta best-practices/LOGGING.md).
# An invalid value raises at import, so a typo fails the invocation instead of
# silently logging at the wrong level.
logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

ecs_client = boto3.client("ecs")
rds_client = boto3.client("rds")

#: Prefix marking a results-dict entry as a failed step. _failures() reads it.
ERROR_PREFIX = "error: "


def get_env_vars() -> tuple[str, dict, str]:
    """Get and validate environment variables.

    Returns:
        Tuple of (cluster_name, services, rds_instance_id).

    Raises:
        ValueError: If a required variable is missing or ECS_SERVICES is not
            valid JSON. handler() turns this into a 500 with the reason.
    """
    cluster_name = os.environ.get("ECS_CLUSTER_NAME")
    services_json = os.environ.get("ECS_SERVICES")
    rds_instance_id = os.environ.get("RDS_INSTANCE_ID")

    if not cluster_name:
        raise ValueError("ECS_CLUSTER_NAME environment variable is required")
    if not services_json:
        raise ValueError("ECS_SERVICES environment variable is required")
    if not rds_instance_id:
        raise ValueError("RDS_INSTANCE_ID environment variable is required")

    try:
        services = json.loads(services_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid ECS_SERVICES JSON: {e}") from e

    return cluster_name, services, rds_instance_id


def _get_rds_status(instance_id: str) -> str:
    """Get current RDS instance status.

    Returns:
        The instance's DBInstanceStatus.

    Raises:
        ClientError, BotoCoreError: If the instance cannot be described. A
            failed lookup is never reported as a status: "unknown" used to
            mean both "AWS reported a state we don't handle" and "I could not
            ask", and the callers read the second as the first -- skipping a
            running instance, and answering 200.
        RuntimeError: If AWS describes no instance for an identifier it did
            not reject.
    """
    response = rds_client.describe_db_instances(DBInstanceIdentifier=instance_id)
    instances = response["DBInstances"]
    if not instances:
        raise RuntimeError(f"No RDS instance described for {instance_id!r}")
    return instances[0]["DBInstanceStatus"]


def _stop_rds(instance_id: str) -> str:
    """Stop the RDS instance if it is running.

    Returns:
        The outcome for the results dict. A value starting with ERROR_PREFIX
        means this step failed.
    """
    try:
        status = _get_rds_status(instance_id)
    except (BotoCoreError, ClientError) as e:
        logger.error(f"Error getting RDS status: {e}")
        return f"{ERROR_PREFIX}{e}"

    logger.info(f"RDS instance {instance_id} status: {status}")

    if status == "stopped":
        logger.info("RDS instance already stopped")
        return "already stopped"
    if status != "available":
        logger.warning(f"RDS in unexpected state: {status}")
        return f"skipped (status: {status})"

    try:
        rds_client.stop_db_instance(DBInstanceIdentifier=instance_id)
    except (BotoCoreError, ClientError) as e:
        logger.error(f"Error stopping RDS: {e}")
        return f"{ERROR_PREFIX}{e}"

    logger.info("RDS stop initiated")
    return "stop initiated"


def _start_rds(instance_id: str) -> str:
    """Start the RDS instance if it is stopped.

    Returns:
        The outcome for the results dict. A value starting with ERROR_PREFIX
        means this step failed.
    """
    try:
        status = _get_rds_status(instance_id)
    except (BotoCoreError, ClientError) as e:
        logger.error(f"Error getting RDS status: {e}")
        return f"{ERROR_PREFIX}{e}"

    logger.info(f"RDS instance {instance_id} status: {status}")

    if status == "available":
        logger.info("RDS instance already running")
        return "already running"
    if status != "stopped":
        logger.info(f"RDS in state: {status}")
        return f"in state: {status}"

    try:
        rds_client.start_db_instance(DBInstanceIdentifier=instance_id)
    except (BotoCoreError, ClientError) as e:
        logger.error(f"Error starting RDS: {e}")
        return f"{ERROR_PREFIX}{e}"

    logger.info("RDS start initiated")
    # Wait briefly for RDS to begin starting
    time.sleep(5)
    return "start initiated"


def _scale_services(cluster_name: str, desired: dict[str, int]) -> dict[str, str]:
    """Set each service's desired count, attempting every service.

    Args:
        cluster_name: Name of the ECS cluster.
        desired: Service name to the desired count for it.

    Returns:
        Service name to its outcome. A value starting with ERROR_PREFIX means
        that service failed; the loop keeps going so one bad service does not
        strand the rest.
    """
    results: dict[str, str] = {}
    for service_name, count in desired.items():
        try:
            ecs_client.update_service(
                cluster=cluster_name,
                service=service_name,
                desiredCount=count,
            )
        except (BotoCoreError, ClientError) as e:
            results[service_name] = f"{ERROR_PREFIX}{e}"
            logger.error(f"Error scaling {service_name}: {e}")
            continue
        results[service_name] = f"scaled to {count}"
        logger.info(f"Scaled {service_name} to {count}")
    return results


def stop_environment(cluster_name: str, services: dict, rds_instance_id: str) -> dict:
    """Stop the environment by scaling ECS to 0 and stopping RDS.

    Returns:
        Dict with an "ecs" map of per-service outcomes and an "rds" outcome.
    """
    logger.info(f"Scaling ECS services to 0 in cluster {cluster_name}")
    ecs_results = _scale_services(cluster_name, dict.fromkeys(services, 0))
    return {"ecs": ecs_results, "rds": _stop_rds(rds_instance_id)}


def start_environment(cluster_name: str, services: dict, rds_instance_id: str) -> dict:
    """Start the environment by starting RDS and scaling ECS services.

    RDS goes first: the services come up against a database that is at least
    already starting.

    Returns:
        Dict with an "ecs" map of per-service outcomes and an "rds" outcome.
    """
    rds_result = _start_rds(rds_instance_id)

    logger.info(f"Scaling ECS services in cluster {cluster_name}")
    desired = {name: config.get("replicas", 1) for name, config in services.items()}
    return {"ecs": _scale_services(cluster_name, desired), "rds": rds_result}


def _failures(results: dict) -> list[str]:
    """Name every step in a results dict that reported an error.

    Returns:
        Step names, e.g. ["ecs:web", "rds"]. Empty when everything succeeded.
    """
    failed = [
        f"ecs:{name}"
        for name, outcome in results["ecs"].items()
        if outcome.startswith(ERROR_PREFIX)
    ]
    if results["rds"].startswith(ERROR_PREFIX):
        failed.append("rds")
    return failed


def handler(event, _context):
    """Lambda handler for start/stop actions.

    Args:
        event: Invocation event; only its "action" key is read.
        _context: The Lambda context object. Unused -- AWS passes it
            positionally, so the parameter has to exist.

    Returns:
        A dict with statusCode 400 for a bad action, 500 for a configuration
        error or any failed step, and 200 only when every step succeeded.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    action = event.get("action", "").lower()
    if action not in ("start", "stop"):
        return {
            "statusCode": 400,
            "body": json.dumps({"error": f"Invalid action: {action}. Must be 'start' or 'stop'"}),
        }

    try:
        cluster_name, services, rds_instance_id = get_env_vars()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    logger.info(f"Environment: cluster={cluster_name}, rds={rds_instance_id}")
    logger.info(f"Services: {list(services.keys())}")

    if action == "stop":
        results = stop_environment(cluster_name, services, rds_instance_id)
    else:
        results = start_environment(cluster_name, services, rds_instance_id)

    logger.info(f"Results: {json.dumps(results)}")

    failures = _failures(results)
    if failures:
        logger.error(f"{action} failed for: {', '.join(failures)}")

    return {
        "statusCode": 500 if failures else 200,
        "body": json.dumps(
            {
                "action": action,
                "results": results,
                "failures": failures,
            }
        ),
    }
