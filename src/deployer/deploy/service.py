"""ECS service deployment operations."""

import hashlib
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, NoReturn

import boto3
from botocore.exceptions import ClientError

from ..aws import ssm
from ..aws.cloudwatch import get_task_logs
from ..timing import get_timer
from ..utils import Colors, log, log_debug, log_error, log_status, log_success, log_warning
from .context import DeploymentContext, InfraConfig, StabilityConfig
from .migrations import should_skip_migrations, store_migrations_hash
from .task_definition import build_task_definition, get_service_sizing


@dataclass
class ServiceWaitResult:
    """Result of waiting for a single service to stabilize."""

    service_name: str
    success: bool
    health_check_failed: bool = False
    error: Exception | None = None


@dataclass
class MigrationTask:
    """Represents a running migration task."""

    task_arn: str
    cluster_name: str
    current_hash: str | None
    app_name: str
    environment: str


@dataclass
class DeploymentConfig:
    """Deployment configuration extracted from infra_config."""

    min_healthy: int = 100
    max_percent: int = 200
    circuit_breaker: bool = False
    circuit_rollback: bool = True


_DEPLOYMENT_CONFIG_KEYS = {
    "minimum_healthy_percent": "min_healthy",
    "maximum_percent": "max_percent",
    "circuit_breaker_enabled": "circuit_breaker",
    "circuit_breaker_rollback": "circuit_rollback",
}


def _get_deployment_config(
    infra_config: InfraConfig, service_toml: dict | None = None
) -> DeploymentConfig:
    """Extract deployment configuration from infra_config.

    A service's deploy.toml stanza may override ``minimum_healthy_percent``
    and ``maximum_percent`` per key: a service setting only one of them
    inherits the other from the environment's [deployment] section. The
    circuit-breaker keys have no per-service form.

    Args:
        infra_config: Environment infrastructure config carrying [deployment].
        service_toml: Raw deploy.toml stanza for the service, or None.

    Returns:
        The merged DeploymentConfig.
    """
    deployment_cfg = infra_config.deployment_config
    kwargs = {
        field: deployment_cfg[key]
        for key, field in _DEPLOYMENT_CONFIG_KEYS.items()
        if key in deployment_cfg
    }
    if service_toml:
        for key, field in (
            ("minimum_healthy_percent", "min_healthy"),
            ("maximum_percent", "max_percent"),
        ):
            if service_toml.get(key) is not None:
                kwargs[field] = service_toml[key]
    return DeploymentConfig(**kwargs)


class DeploymentError(Exception):
    """Raised when a deployment fails with a known error."""

    def __init__(self, message: str, service_name: str, error_type: str | None = None):
        self.service_name = service_name
        self.error_type = error_type
        super().__init__(message)


# Known fatal error patterns in ECS service events
FATAL_ERROR_PATTERNS = [
    (
        r"invalid ssm parameters?: (.+)",
        "missing_ssm_parameters",
        "Missing SSM parameters: {match}. Create them with: "
        "aws ssm put-parameter --name <name> --type SecureString --value <value>",
    ),
    (
        r"CannotPullContainerError.*repository.*does not exist",
        "ecr_repo_not_found",
        "ECR repository not found. Ensure the repository exists and the image has been pushed.",
    ),
    (
        r"CannotPullContainerError.*manifest.*not found",
        "image_not_found",
        "Image tag not found in ECR. Ensure the image has been pushed with the correct tag.",
    ),
    (
        r"unable to pull secrets or registry auth.*AccessDeniedException",
        "iam_secrets_access",
        "IAM role lacks permission to access secrets. Check the ECS execution role policy.",
    ),
    (
        r"ResourceInitializationError.*unable to pull secrets",
        "secrets_pull_failed",
        "Failed to pull secrets. Check that all SSM parameters exist "
        "and IAM permissions are correct.",
    ),
    (
        r"No Container Instances were found",
        "no_capacity",
        "No container instances available. For Fargate, check subnet/security group configuration.",
    ),
    (
        r"ECS was unable to assume the role",
        "iam_role_assume",
        "ECS cannot assume the task execution role. Check the role's trust policy.",
    ),
]


def _ensure_az_rebalancing_disabled(ecs_client, cluster_name: str, service_name: str) -> bool:
    """Disable AZ rebalancing if it's enabled and we need max_percent <= 100.

    AWS's Availability Zone Rebalancing feature doesn't support maximumPercent <= 100.
    This function checks if AZ rebalancing is enabled and disables it if needed.

    Args:
        ecs_client: boto3 ECS client.
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.

    Returns:
        True if AZ rebalancing was disabled, False if already disabled
        or service doesn't exist.
    """
    try:
        response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
        if not response.get("services"):
            return False

        service = response["services"][0]
        if service.get("status") == "INACTIVE":
            return False

        az_rebalancing = service.get("availabilityZoneRebalancing", "DISABLED")
        if az_rebalancing == "ENABLED":
            # Use subprocess to call AWS CLI since botocore doesn't support this parameter yet
            cmd = [
                "aws",
                "ecs",
                "update-service",
                "--cluster",
                cluster_name,
                "--service",
                service_name,
                "--availability-zone-rebalancing",
                "DISABLED",
                "--no-force-new-deployment",
                "--query",
                "service.serviceName",
                "--output",
                "text",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                log_status(service_name, "AZ rebalancing disabled")
                return True
            else:
                log_warning(f"Could not disable AZ rebalancing for {service_name}: {result.stderr}")
        return False
    except ClientError:
        return False


def _get_live_service(ecs_client, cluster_name: str, service_name: str) -> dict | None:
    """Describe an ECS service, returning its description or None if absent.

    Args:
        ecs_client: boto3 ECS client.
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.

    Returns:
        The live service description, or None if the service is absent or
        INACTIVE.

    Raises:
        RuntimeError: If ``describe_services`` fails. A genuinely absent service
            comes back as an *empty* ``services`` list, not an error, so every
            ``ClientError`` here is a failure and none is an absence. Answering
            one with None sent a mistyped cluster name down the CREATE branch.
            Deliberately not caught by ``_deploy_one_service``: the failure is
            cluster-level, so the per-service collector would report the one bad
            cluster once per service.
    """
    try:
        response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
    except ClientError as e:
        raise RuntimeError(
            f"Could not check whether service '{service_name}' exists in cluster "
            f"'{cluster_name}': {e}"
        ) from e

    # Service exists if it's in the response and not INACTIVE
    for svc in response.get("services", []):
        if svc["serviceName"] == service_name and svc["status"] != "INACTIVE":
            return svc
    return None


def _register_task_definition(
    ctx,
    service_name: str,
    image_uri: str,
    credential_mode: str = "app",
) -> str:
    """Register a new task definition revision.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        image_uri: ECR image URI to use.
        credential_mode: For database credentials - "app" for runtime services
            (DML only), "migrate" for migrations (DDL + DML). Default is "app".

    Returns:
        The task definition ARN.
    """
    task_def = build_task_definition(
        ctx,
        service_name,
        image_uri,
        credential_mode=credential_mode,
    )

    if ctx.dry_run:
        family = task_def["family"]
        print(
            f"  {Colors.YELLOW}[dry-run]{Colors.NC} "
            f"aws ecs register-task-definition --family {family}"
        )
        return f"arn:aws:ecs:{ctx.region}:{ctx.account_id}:task-definition/{family}:dry-run"

    response = ctx.ecs_client.register_task_definition(**task_def)
    task_def_arn = response["taskDefinition"]["taskDefinitionArn"]
    log_success(f"{service_name} task definition registered")
    return task_def_arn


def _require_network_config(ctx) -> tuple[list[str], str]:
    """Return (subnet_ids, security_group_id) from infra_config, or raise.

    Args:
        ctx: DeploymentContext with shared deployment parameters.

    Returns:
        Tuple of (subnet_ids, security_group_id).

    Raises:
        RuntimeError: If either value is missing from infra_config.
    """
    subnet_ids = ctx.infra_config.subnet_ids
    security_group_id = ctx.infra_config.security_group_id

    if not subnet_ids or not security_group_id:
        log_error("Missing network configuration in infra_config (subnet_ids, security_group_id).")
        raise RuntimeError("Missing network configuration")

    return subnet_ids, security_group_id


def _deployment_configuration(dep_cfg: DeploymentConfig) -> dict:
    """Build the ECS deploymentConfiguration block.

    Args:
        dep_cfg: Deployment configuration extracted from infra_config.

    Returns:
        Dict for the ``deploymentConfiguration`` service parameter.
    """
    deployment_configuration: dict[str, Any] = {
        "minimumHealthyPercent": dep_cfg.min_healthy,
        "maximumPercent": dep_cfg.max_percent,
    }
    if dep_cfg.circuit_breaker:
        deployment_configuration["deploymentCircuitBreaker"] = {
            "enable": True,
            "rollback": dep_cfg.circuit_rollback,
        }
    return deployment_configuration


def _load_balancer_params(ctx, service_name: str, service_cfg: dict, service_toml: dict) -> dict:
    """Return the load balancer keys for a service, or an empty dict.

    A load-balanced service with no resolvable target group is silently
    skipped -- it is created without load balancer registration.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        service_cfg: Merged sizing config for the service.
        service_toml: Raw deploy.toml stanza for the service.

    Returns:
        Dict of ECS service params to merge, empty when not applicable.
    """
    if not (service_cfg.get("load_balanced") and "port" in service_toml):
        return {}

    # Use per-service target group if available, otherwise default
    service_target_groups = ctx.infra_config.service_target_groups
    target_group_arn = service_target_groups.get(service_name) or ctx.infra_config.target_group_arn
    if not target_group_arn:
        return {}

    # Health check grace period gives the container time to start before
    # health checks begin.
    health_check_cfg = ctx.infra_config.health_check_config
    return {
        "loadBalancers": [
            {
                "targetGroupArn": target_group_arn,
                "containerName": service_name,
                "containerPort": service_toml["port"],
            }
        ],
        "healthCheckGracePeriodSeconds": health_check_cfg.get("grace_period", 60),
    }


def _service_registries(ctx, service_name: str) -> list[dict] | None:
    """Return the service discovery registries for a service, or None.

    For A record DNS routing only registryArn is needed (no containerPort).

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.

    Returns:
        List of serviceRegistries entries, or None when not configured.
    """
    service_discovery_registries = ctx.infra_config.service_discovery_registries
    registry_arn = service_discovery_registries.get(service_name)
    if not registry_arn:
        return None
    return [{"registryArn": registry_arn}]


def _create_service(
    ctx,
    service_name: str,
    task_def_arn: str,
) -> None:
    """Create a new ECS service.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        task_def_arn: Task definition ARN to use.
    """
    service_cfg = get_service_sizing(service_name, ctx.config, ctx.service_config)
    service_toml = ctx.config.get("services", {}).get(service_name, {})

    subnet_ids, security_group_id = _require_network_config(ctx)

    dep_cfg = _get_deployment_config(ctx.infra_config, service_toml)

    create_params = {
        "cluster": ctx.cluster_name,
        "serviceName": service_name,
        "taskDefinition": task_def_arn,
        "desiredCount": service_cfg["replicas"],
        "networkConfiguration": {
            "awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [security_group_id],
                "assignPublicIp": "DISABLED",
            }
        },
        "deploymentConfiguration": _deployment_configuration(dep_cfg),
    }

    # Use capacity provider strategy for interruptible services (Fargate Spot),
    # otherwise use standard FARGATE launch type
    if service_toml.get("interruptible"):
        create_params["capacityProviderStrategy"] = [
            {"capacityProvider": "FARGATE", "base": 1, "weight": 0},
            {"capacityProvider": "FARGATE_SPOT", "weight": 1},
        ]
    else:
        create_params["launchType"] = "FARGATE"

    create_params.update(_load_balancer_params(ctx, service_name, service_cfg, service_toml))

    registries = _service_registries(ctx, service_name)
    if registries:
        create_params["serviceRegistries"] = registries

    if ctx.dry_run:
        print(
            f"  {Colors.YELLOW}[dry-run]{Colors.NC} "
            f"aws ecs create-service --service-name {service_name}"
        )
        return

    ctx.ecs_client.create_service(**create_params)
    log_success(f"{service_name} service created")

    # Disable AZ rebalancing if using max_percent <= 100 (AWS doesn't support it)
    # Must be done after creation since botocore doesn't support the parameter yet
    if dep_cfg.max_percent <= 100:
        _ensure_az_rebalancing_disabled(ctx.ecs_client, ctx.cluster_name, service_name)


def _update_service(ctx, service_name: str, task_def_arn: str, dep_cfg: DeploymentConfig) -> bool:
    """Force a new deployment of an existing ECS service.

    A ClientError is logged and reported as False so the caller keeps deploying
    the remaining services. The caller collects the failures and raises once
    every service has been attempted: a partial deploy must not report itself
    as a complete one.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        task_def_arn: Task definition ARN to deploy.
        dep_cfg: Deployment configuration extracted from infra_config.

    Returns:
        True if the deployment was started, False if AWS rejected it.
    """
    try:
        update_params = {
            "cluster": ctx.cluster_name,
            "service": service_name,
            "taskDefinition": task_def_arn,
            "forceNewDeployment": True,
            "deploymentConfiguration": _deployment_configuration(dep_cfg),
        }

        # Ensure existing services get updated with service discovery too
        registries = _service_registries(ctx, service_name)
        if registries:
            update_params["serviceRegistries"] = registries

        ctx.ecs_client.update_service(**update_params)
        log_status(service_name, "deployment started")
        return True
    except ClientError as e:
        log_error(f"Failed to update service {service_name}: {e}")
        return False


@dataclass
class ServiceDeployOutcome:
    """Outcome of deploying (or skipping) one service.

    ``task_def_arn`` is set when the service was created or its deployment
    started; None when it was skipped as unchanged or failed. ``state_hash``
    is the intended-state hash (None on dry runs, which never hash).

    The three outcomes are read off the two fields, in this order:
    ``skipped`` is a deliberate no-op, then no ``task_def_arn`` is a failure,
    and anything else deployed.
    """

    task_def_arn: str | None = None
    state_hash: str | None = None
    skipped: bool = False


@dataclass
class DeployedServices:
    """What ``deploy_services`` did, service by service.

    ``updated`` maps each created-or-updated service to the task definition
    ARN this deploy registered for it — ``wait_for_stable`` waits on exactly
    these and verifies PRIMARY runs that ARN. ``state_hashes`` carries the
    intended-state hash per updated service, stored to SSM only after
    stability. ``skipped`` lists services left untouched as unchanged.
    """

    updated: dict[str, str] = field(default_factory=dict)
    state_hashes: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def _compute_service_state_hash(ctx, service_name: str, image_uri: str, dep_cfg) -> str:
    """Hash everything this deploy would send ECS for a service.

    Covers the full task definition plus the update-time service parameters
    (deployment configuration, service registries) — everything
    ``_update_service`` would send except the ARN and forceNewDeployment.
    ``containerDefinitions[].environment`` and ``.secrets`` are sorted by name
    first: they are built from dicts, and dict-order lists would make an
    unchanged service hash differently between runs (a false redeploy).

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        image_uri: ECR image URI the service would run.
        dep_cfg: Merged DeploymentConfig for the service.

    Returns:
        Hex SHA-256 of the sorted-key JSON of the intended state.
    """
    task_def = build_task_definition(ctx, service_name, image_uri)
    for container in task_def.get("containerDefinitions", []):
        container["environment"] = sorted(
            container.get("environment", []), key=lambda entry: entry["name"]
        )
        container["secrets"] = sorted(container.get("secrets", []), key=lambda entry: entry["name"])

    intended = {
        "task_def": task_def,
        "deployment_configuration": _deployment_configuration(dep_cfg),
        "service_registries": _service_registries(ctx, service_name),
    }
    return hashlib.sha256(json.dumps(intended, sort_keys=True).encode()).hexdigest()


def _service_state_param_name(app_name: str, environment: str, service_name: str) -> str:
    """SSM parameter holding a service's last deployed state hash + ARN."""
    return f"/{app_name}/{environment}/service-state-hash-{service_name}"


def get_stored_service_state(
    app_name: str, environment: str, service_name: str
) -> tuple[str, str] | None:
    """Read a service's stored (state hash, task definition ARN) from SSM.

    Returns:
        The (hash, arn) pair, or None if absent or unparseable — both of
        which simply disable skipping for the service.
    """
    value, error = ssm.get_parameter(_service_state_param_name(app_name, environment, service_name))
    if error or value is None:
        return None
    try:
        data = json.loads(value)
        return data["hash"], data["task_def_arn"]
    except (ValueError, KeyError, TypeError):
        return None


def store_service_state(
    app_name: str, environment: str, service_name: str, state_hash: str, task_def_arn: str
) -> bool:
    """Store a service's deployed state hash + ARN in SSM.

    Returns:
        True on success. A denied write is logged and answered False — the
        stale stored state then fails the next deploy's live-ARN gate, so
        skipping stays disabled rather than going wrong.
    """
    success, error = ssm.put_parameter(
        name=_service_state_param_name(app_name, environment, service_name),
        value=json.dumps({"hash": state_hash, "task_def_arn": task_def_arn}),
        description="Intended-state hash of the last stable deployment",
        overwrite=True,
    )
    if not success:
        log_warning(f"Failed to store service state hash for {service_name}: {error}")
    return success


def store_service_state_hashes(
    app_name: str, environment: str, deployed: "DeployedServices", health_check_failures: list[str]
) -> None:
    """Persist state hashes for the services that deployed AND stabilized.

    Runs only after ``wait_for_stable`` — a hash stored earlier would let a
    failed deploy skip its own retry. Services in ``health_check_failures``
    (the exit-2 warning list) are excluded for the same reason: storing their
    hash would make "redeploy after exit 2" silently no-op.

    Args:
        app_name: Application name.
        environment: Environment name.
        deployed: The ``deploy_services`` result.
        health_check_failures: Services whose health checks failed.
    """
    for service_name, state_hash in deployed.state_hashes.items():
        if service_name in health_check_failures:
            continue
        store_service_state(
            app_name, environment, service_name, state_hash, deployed.updated[service_name]
        )


def _service_is_unchanged(ctx, service_name: str, current_hash: str, live_service: dict) -> bool:
    """Decide whether a service can skip its register + update entirely.

    All three gates must hold:

    1. The stored hash (last stable deploy) matches ``current_hash``.
    2. The live service still runs the stored task definition ARN. This gate
       makes out-of-band changes — emergency-module pins, console edits, a
       rollback after the hash was stored — self-heal into a redeploy
       instead of a silent skip.
    3. The live service is settled and healthy: exactly one deployment
       (PRIMARY), running == desired > 0.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        current_hash: Hash of the state this deploy intends.
        live_service: The live ECS service description.

    Returns:
        True if the service is unchanged and healthy.
    """
    stored = get_stored_service_state(ctx.app_name, ctx.environment, service_name)
    if stored is None:
        return False
    stored_hash, stored_arn = stored
    if stored_hash != current_hash:
        return False

    if live_service.get("taskDefinition") != stored_arn:
        return False

    deployments = live_service.get("deployments", [])
    if len(deployments) != 1 or deployments[0].get("status") != "PRIMARY":
        return False
    primary = deployments[0]
    running = primary.get("runningCount", 0)
    desired = primary.get("desiredCount", 0)
    return running == desired and running > 0


def _deploy_one_service(
    ctx, service_name: str, svc_config: dict, image_uris: dict, force_deploy: bool = False
) -> ServiceDeployOutcome:
    """Register one service's task definition and create or update the service.

    An unchanged, healthy service is skipped entirely (no new task-definition
    revision, no forced deployment) unless ``force_deploy`` is set -- see
    ``_service_is_unchanged`` for the gates. Dry runs bypass the skip check:
    they narrate what a real deploy would send and never touch SSM.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        svc_config: The service's entry in deploy.toml's [services].
        image_uris: Dictionary mapping image names to ECR URIs.
        force_deploy: Deploy even when the service state is unchanged.

    Returns:
        ServiceDeployOutcome. ``failed`` is True when the service had no
        built image or AWS rejected the update -- both are failures the
        caller must report; a service that was never deployed is not a
        deployed service.
    """
    # Get merged config (deploy.toml + environment sizing)
    service_cfg = get_service_sizing(service_name, ctx.config, ctx.service_config)

    # Get image URI for this service
    image_name = svc_config.get("image", service_name)
    image_uri = image_uris.get(image_name)
    if not image_uri:
        log_error(f"No image URI for service {service_name} (image: {image_name})")
        return ServiceDeployOutcome()

    dep_cfg = _get_deployment_config(ctx.infra_config, svc_config)

    if ctx.dry_run:
        task_def_arn = _register_task_definition(ctx, service_name, image_uri)
        print(
            f"  {Colors.YELLOW}[dry-run]{Colors.NC} "
            f"aws ecs update-service --service {service_name} "
            f"--task-definition {task_def_arn}"
        )
        cpu = service_cfg.get("cpu")
        mem = service_cfg.get("memory")
        reps = service_cfg.get("replicas")
        print(f"    cpu={cpu}, memory={mem}, replicas={reps}")
        print(
            f"    deployment: minHealthy={dep_cfg.min_healthy}%, "
            f"maxPercent={dep_cfg.max_percent}%, "
            f"circuitBreaker={dep_cfg.circuit_breaker}"
        )
        return ServiceDeployOutcome(task_def_arn=task_def_arn)

    live_service = _get_live_service(ctx.ecs_client, ctx.cluster_name, service_name)
    state_hash = _compute_service_state_hash(ctx, service_name, image_uri, dep_cfg)

    if (
        live_service is not None
        and not force_deploy
        and _service_is_unchanged(ctx, service_name, state_hash, live_service)
    ):
        log_status(service_name, "unchanged, skipping")
        return ServiceDeployOutcome(state_hash=state_hash, skipped=True)

    task_def_arn = _register_task_definition(ctx, service_name, image_uri)

    if live_service is None:
        _create_service(ctx, service_name, task_def_arn)
        log_status(service_name, "service created")
        return ServiceDeployOutcome(task_def_arn=task_def_arn, state_hash=state_hash)

    # Disable AZ rebalancing if using max_percent <= 100 (AWS doesn't support it)
    if dep_cfg.max_percent <= 100:
        _ensure_az_rebalancing_disabled(ctx.ecs_client, ctx.cluster_name, service_name)

    if not _update_service(ctx, service_name, task_def_arn, dep_cfg):
        return ServiceDeployOutcome()
    return ServiceDeployOutcome(task_def_arn=task_def_arn, state_hash=state_hash)


def deploy_services(
    ctx,
    image_uris: dict[str, str],
    force_deploy: bool = False,
) -> DeployedServices:
    """Register task definitions and deploy all services (create or update).

    Every service is attempted even after one fails -- stopping halfway leaves
    a worse state than finishing -- but the failures are collected and raised
    at the end. Logging them and returning normally made a partial deploy
    indistinguishable from a complete one to everything downstream.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        image_uris: Dictionary mapping image names to ECR URIs.
        force_deploy: Deploy every service even when unchanged.

    Returns:
        DeployedServices: what was updated (name -> task definition ARN, for
        ``wait_for_stable``'s identity check), the per-service state hashes
        (stored after stability), and which services were skipped unchanged.

    Raises:
        RuntimeError: If any service could not be deployed, naming all of them.
    """
    log("Deploying ECS services...")

    services = ctx.config.get("services", {})
    log_debug(f"Services to deploy: {list(services.keys())}")

    deployed = DeployedServices()
    failed: list[str] = []
    for name, svc_config in services.items():
        outcome = _deploy_one_service(ctx, name, svc_config, image_uris, force_deploy)
        if outcome.skipped:
            deployed.skipped.append(name)
        elif outcome.task_def_arn is None:
            failed.append(name)
        else:
            deployed.updated[name] = outcome.task_def_arn
            if outcome.state_hash is not None:
                deployed.state_hashes[name] = outcome.state_hash

    if failed:
        raise RuntimeError(f"Failed to deploy service(s): {', '.join(failed)}")

    return deployed


def _resolve_migration_image(ctx, migration_service: str, image_uris: dict[str, str]) -> str | None:
    """Resolve the ECR image URI to run migrations with.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        migration_service: Name of the service whose image runs migrations.
        image_uris: Dictionary mapping image names to ECR URIs.

    Returns:
        The ECR image URI, or None if no image was built for that service.
    """
    services_config = ctx.config.get("services", {})
    svc_config = services_config.get(migration_service, {})
    image_name = svc_config.get("image", migration_service)
    image_uri = image_uris.get(image_name)

    if not image_uri:
        log_error(f"No image URI for migration service {migration_service} (image: {image_name})")

    return image_uri


def _migration_network_config(ctx, migration_service: str) -> dict | None:
    """Get the network configuration to run the migration task in.

    Reuses the network configuration of an already-deployed service.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        migration_service: Name of the service to copy network config from.

    Returns:
        The service's networkConfiguration, or None if it could not be read.
    """
    try:
        services = ctx.ecs_client.describe_services(
            cluster=ctx.cluster_name, services=[migration_service]
        )
        if not services["services"]:
            log_error(f"No {migration_service} service found to get network configuration")
            return None

        return services["services"][0]["networkConfiguration"]
    except ClientError as e:
        log_error(f"Could not get network configuration: {e}")
        return None


def start_migrations(
    ctx,
    image_uris: dict[str, str],
    source_dir: Path | None,
) -> MigrationTask | None:
    """Start database migrations (non-blocking).

    This function starts the migration task and returns immediately.
    Use wait_for_migrations() to wait for completion.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        image_uris: Dictionary mapping image names to ECR URIs.
        source_dir: Resolved path to the application source directory (for
            migration hashing), or None to always run the migration.

    Returns:
        MigrationTask with task ARN and metadata, or None if migrations
        are disabled, skipped, or this is a dry run.
    """
    migrations = ctx.config.get("migrations", {})
    if not migrations.get("enabled", False):
        return None

    migration_service = migrations.get("service", "web")
    image_uri = _resolve_migration_image(ctx, migration_service, image_uris)
    if not image_uri:
        return None

    # Always register the migrate task definition so it's available for ecs-run.py
    # This ensures the task definition exists even if we skip running migrations
    log("Registering migrate task definition...")
    task_def_arn = _register_task_definition(
        ctx,
        "migrate",  # Use "migrate" as task family name
        image_uri,
        credential_mode="migrate",  # Use migrate credentials (DDL + DML)
    )

    if ctx.dry_run:
        print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws ecs run-task (migrate command)")
        return None

    # Check if migrations can be skipped (no changes since last deploy)
    current_hash = None
    if source_dir:
        should_skip, current_hash = should_skip_migrations(
            source_dir, ctx.app_name, ctx.environment
        )
        if should_skip:
            # Task definition is registered but we skip running the migration
            return None

    log("Starting migrations...")

    command = migrations.get("command", ["python", "manage.py", "migrate"])

    network_config = _migration_network_config(ctx, migration_service)
    if network_config is None:
        return None

    # Run the migration task using the newly registered task definition
    # The container name is "migrate" (same as the task family/service name)
    response = ctx.ecs_client.run_task(
        cluster=ctx.cluster_name,
        taskDefinition=task_def_arn,
        launchType="FARGATE",
        networkConfiguration=network_config,
        overrides={
            "containerOverrides": [
                {"name": "migrate", "command": command}  # Container name matches task family
            ]
        },
    )

    task_arn = response["tasks"][0]["taskArn"]
    print(f"  Migration task started: {task_arn}")

    return MigrationTask(
        task_arn=task_arn,
        cluster_name=ctx.cluster_name,
        current_hash=current_hash,
        app_name=ctx.app_name,
        environment=ctx.environment,
    )


def wait_for_migrations(
    ecs_client,
    migration_task: MigrationTask | None,
) -> None:
    """Wait for a migration task to complete.

    Args:
        ecs_client: boto3 ECS client.
        migration_task: MigrationTask returned by start_migrations(), or None.

    Raises:
        RuntimeError: If the migration task fails.
    """
    if migration_task is None:
        return

    print("  Waiting for migrations to complete...")

    # Wait for task to complete
    waiter = ecs_client.get_waiter("tasks_stopped")
    waiter.wait(
        cluster=migration_task.cluster_name,
        tasks=[migration_task.task_arn],
    )

    # Check exit code
    task_desc = ecs_client.describe_tasks(
        cluster=migration_task.cluster_name,
        tasks=[migration_task.task_arn],
    )
    exit_code = task_desc["tasks"][0]["containers"][0].get("exitCode", 1)

    if exit_code != 0:
        log_error(f"Migration failed with exit code {exit_code}")

        # Fetch and display CloudWatch logs to help diagnose the failure
        _display_migration_logs(migration_task)

        raise RuntimeError(f"Migration failed with exit code {exit_code}")

    log_success("Migrations complete")

    # Store the migrations hash for future skip detection
    if migration_task.current_hash:
        store_migrations_hash(
            migration_task.app_name,
            migration_task.environment,
            migration_task.current_hash,
        )


def _display_migration_logs(migration_task: MigrationTask, limit: int = 50) -> None:
    """Fetch and display CloudWatch logs for a failed migration task.

    Never fatal: the caller is already raising on the migration failure this
    tail is meant to explain. A CloudWatch read that fails is reported as a
    read failure, distinct from a task that genuinely logged nothing.

    Args:
        migration_task: The failed migration task.
        limit: Maximum number of log lines to display.
    """
    # Extract task ID from ARN (last segment)
    task_id = migration_task.task_arn.split("/")[-1]

    # Log group follows ECS convention: /ecs/{app_name}-{environment}
    log_group = f"/ecs/{migration_task.app_name}-{migration_task.environment}"

    # Stream prefix is "migrate" for migration tasks
    stream_prefix = "migrate"

    # Container name is "migrate" for migration tasks
    container_name = "migrate"

    print()
    log("Fetching migration logs...")

    try:
        events = get_task_logs(log_group, stream_prefix, container_name, task_id, limit=limit)

        if events:
            print()
            print(f"  {Colors.CYAN}--- Migration Logs (last {limit} lines) ---{Colors.NC}")
            for event in events:
                message = event.get("message", "").rstrip()
                print(f"  {message}")
            print(f"  {Colors.CYAN}--- End of Logs ---{Colors.NC}")
            print()
        else:
            log_warning(f"No logs found. Check CloudWatch log group: {log_group}")
            print(f"  Stream: {stream_prefix}/{container_name}/{task_id}")
    except RuntimeError as e:
        # get_log_events raises only when CloudWatch could not be read at all.
        # Saying so beats "No logs found", which claims the task was silent.
        log_warning(f"Could not read the migration logs: {e}")
        print(f"  Check CloudWatch manually: {log_group}")
    except Exception as e:  # noqa: BLE001 — graceful fallback for log fetching
        log_warning(f"Could not fetch logs: {e}")
        print(f"  Check CloudWatch manually: {log_group}")


def _check_for_fatal_errors(events: list[dict], service_name: str) -> None:
    """Check service events for known fatal error patterns.

    Args:
        events: List of ECS service events.
        service_name: Name of the service (for error messages).

    Raises:
        DeploymentError: If a fatal error pattern is detected.
    """
    for event in events:
        message = event.get("message", "")
        for pattern, error_type, help_text in FATAL_ERROR_PATTERNS:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                # Extract matched group if available for the help text
                match_text = match.group(1) if match.lastindex else ""
                detailed_help = help_text.format(match=match_text)
                raise DeploymentError(
                    f"{service_name}: {detailed_help}",
                    service_name=service_name,
                    error_type=error_type,
                )


def _get_deployment_status(service: dict) -> tuple[dict | None, int, int, int]:
    """Extract deployment status from service description.

    Args:
        service: ECS service description.

    Returns:
        Tuple of (primary_deployment, running_count, desired_count, failed_tasks).
    """
    deployments = service.get("deployments", [])
    primary = next((d for d in deployments if d["status"] == "PRIMARY"), None)

    if not primary:
        return None, 0, 0, 0

    return (
        primary,
        primary.get("runningCount", 0),
        primary.get("desiredCount", 0),
        primary.get("failedTasks", 0),
    )


def _wait_for_service_and_targets(
    ctx: DeploymentContext,
    elbv2_client,
    service_name: str,
    stability: StabilityConfig = StabilityConfig(),  # noqa: B008
    expected_arn: str | None = None,
) -> ServiceWaitResult:
    """Wait for a single service to stabilize and its targets to be healthy.

    This function wraps _wait_for_service_stable() and _wait_for_target_group_healthy()
    for use in parallel execution. Instead of raising exceptions, it returns a
    ServiceWaitResult to allow thread-safe error collection.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        elbv2_client: boto3 ELBv2 client.
        service_name: Name of the service.
        stability: Polling configuration for stability checks.
        expected_arn: Task definition ARN this deploy registered for the
            service; stability additionally requires PRIMARY to run it.

    Returns:
        ServiceWaitResult with success status and any error details.
    """
    try:
        _wait_for_service_stable(ctx, service_name, stability, expected_arn)

        # Also wait for target group health if service is load balanced
        target_group_arn = _get_service_target_group(ctx.ecs_client, ctx.cluster_name, service_name)
        if target_group_arn:  # noqa: SIM102
            if not _wait_for_target_group_healthy(
                elbv2_client,
                target_group_arn,
                service_name,
            ):
                return ServiceWaitResult(
                    service_name=service_name,
                    success=True,  # Service stable, just health check timeout
                    health_check_failed=True,
                )

        return ServiceWaitResult(service_name=service_name, success=True)

    except (DeploymentError, RuntimeError) as e:
        return ServiceWaitResult(
            service_name=service_name,
            success=False,
            error=e,
        )


def _timed_wait_for_service_and_targets(
    ctx: DeploymentContext,
    elbv2_client,
    service_name: str,
    stability: StabilityConfig,
    expected_arn: str | None,
) -> ServiceWaitResult:
    """Run one service's wait under a per-service timing sub-step.

    Runs in a worker thread: ``sub_steps.append`` is atomic under the GIL and
    the parent step stays open until the executor joins.
    """
    timer = get_timer()
    if not (timer and timer.in_step):
        return _wait_for_service_and_targets(
            ctx, elbv2_client, service_name, stability, expected_arn
        )

    with timer.sub_step(service_name) as sub:
        result = _wait_for_service_and_targets(
            ctx, elbv2_client, service_name, stability, expected_arn
        )
    # The context manager stamps success=True on clean exit; a captured wait
    # failure is a clean exit, so restate it on the recorded timing.
    if not result.success and result.error is not None:
        sub.success = False
        sub.error = str(result.error)
    return result


def wait_for_stable(
    ctx: DeploymentContext,
    updated_services: dict[str, str],
    stability: StabilityConfig = StabilityConfig(),  # noqa: B008
) -> list[str]:
    """Wait for deployed services to stabilize with active error detection.

    Uses active polling instead of AWS waiter to detect failures early
    and provide actionable error messages. Also waits for load balancer
    target groups to have healthy targets.

    Services are waited on in parallel using a thread pool for faster
    total deployment time.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        updated_services: Mapping of service name to the task definition ARN
            this deploy registered for it, as returned by ``deploy_services``.
            Only these services are waited on, and each must end up with
            PRIMARY running its ARN — a circuit-breaker rollback swaps PRIMARY
            back to the healthy *old* revision, which otherwise looks exactly
            like success.
        stability: Polling configuration for stability checks.

    Returns:
        List of service names that failed health checks (empty if all healthy).

    Raises:
        DeploymentError: If any service fails to stabilize with a known error.
        RuntimeError: If any service times out waiting to stabilize.
    """
    log("Waiting for services to stabilize...")

    if ctx.dry_run:
        print(f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws ecs wait services-stable")
        return []

    if not updated_services:
        # Nothing deployed, nothing to wait for. Also protects
        # ThreadPoolExecutor(max_workers=0), which is a ValueError.
        return []

    # Create elbv2 client once and share across threads (boto3 clients are thread-safe)
    elbv2_client = boto3.client("elbv2")
    health_check_failures = []

    # Wait for all services in parallel
    with ThreadPoolExecutor(max_workers=len(updated_services)) as executor:
        futures = {
            executor.submit(
                _timed_wait_for_service_and_targets,
                ctx,
                elbv2_client,
                service_name,
                stability,
                expected_arn,
            ): service_name
            for service_name, expected_arn in updated_services.items()
        }

        # Process results as they complete - first fatal error fails deployment
        for future in as_completed(futures):
            result = future.result()
            if not result.success:
                # Re-raise the original exception to fail the deployment
                if result.error:
                    raise result.error
            elif result.health_check_failed:
                health_check_failures.append(result.service_name)

    return health_check_failures


def _raise_task_failure(events: list[dict], service_name: str, failed: int) -> NoReturn:
    """Raise a DeploymentError describing repeatedly failing tasks.

    Args:
        events: List of ECS service events (most recent first).
        service_name: Name of the service.
        failed: Number of failed tasks reported by the primary deployment.

    Raises:
        DeploymentError: Always.
    """
    error_msg = "Tasks are failing repeatedly"
    if events:
        error_msg = events[0].get("message", error_msg)

    raise DeploymentError(
        f"{service_name}: {failed} tasks failed. Latest event: {error_msg}",
        service_name=service_name,
        error_type="task_failures",
    )


def _track_no_progress(
    events: list[dict],
    service_name: str,
    running: int,
    failed: int,
    consecutive_failures: int,
) -> int:
    """Count a poll against the no-progress run, raising once it is hopeless.

    A poll makes no progress when it has failures and nothing running; any
    other poll ends the run. Owning that predicate here keeps the definition
    of "no progress" next to the count it drives.

    Args:
        events: List of ECS service events (most recent first).
        service_name: Name of the service.
        running: Running task count reported by this poll.
        failed: ``failedTasks`` reported by this poll.
        consecutive_failures: Count of prior consecutive no-progress polls.

    Returns:
        The incremented no-progress count, or 0 if this poll made progress.

    Raises:
        DeploymentError: After 3 polls (~45s) with failures and no running tasks.
    """
    if not (running == 0 and failed > 0):
        return 0

    consecutive_failures += 1
    if consecutive_failures >= 3 and events:
        # 3 polls (~45s) with failures and no running tasks
        _check_for_fatal_errors(events, service_name)
        # If no pattern matched, raise generic error
        raise DeploymentError(
            f"{service_name}: No tasks running after multiple attempts. "
            f"Check ECS console for details. "
            f"Latest: {events[0].get('message', 'No events')}",
            service_name=service_name,
            error_type="no_progress",
        )
    return consecutive_failures


def _describe_service_or_raise(ctx: DeploymentContext, service_name: str) -> dict:
    """Describe a single service, raising if it is missing or undescribable.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.

    Returns:
        The ECS service description.

    Raises:
        ClientError: If the describe_services call fails.
        DeploymentError: If the service is not present in the cluster.
    """
    try:
        response = ctx.ecs_client.describe_services(
            cluster=ctx.cluster_name, services=[service_name]
        )
    except ClientError as e:
        log_error(f"Failed to describe service {service_name}: {e}")
        raise

    if not response.get("services"):
        raise DeploymentError(
            f"Service {service_name} not found in cluster {ctx.cluster_name}",
            service_name=service_name,
            error_type="service_not_found",
        )

    return response["services"][0]


def _check_for_failed_rollout(service: dict, service_name: str) -> None:
    """Raise if any deployment in the service reports rolloutState FAILED.

    Only the circuit breaker sets FAILED, so this is a no-op on environments
    without one. The failed deployment is often no longer PRIMARY by the time
    a poll sees it -- circuit_breaker_rollback swaps PRIMARY back to the old
    revision -- which is why the whole list is scanned.

    Args:
        service: ECS service description.
        service_name: Name of the service (for error messages).

    Raises:
        DeploymentError: If a FAILED rollout is found.
    """
    for deployment in service.get("deployments", []):
        if deployment.get("rolloutState") == "FAILED":
            reason = deployment.get("rolloutStateReason", "no reason given")
            raise DeploymentError(
                f"{service_name}: deployment rollout FAILED: {reason}",
                service_name=service_name,
                error_type="rollout_failed",
            )


def _check_expected_task_definition(
    deployment: dict, service_name: str, expected_arn: str | None
) -> None:
    """Raise if a settled PRIMARY deployment runs the wrong task definition.

    This is the strong form of rollback detection: a 15s poll window can miss
    the transient FAILED state entirely, but after a circuit-breaker rollback
    the settled PRIMARY permanently runs an ARN this deploy never registered.

    Args:
        deployment: The PRIMARY deployment, already meeting the success
            criterion (running == desired, nothing pending).
        service_name: Name of the service (for error messages).
        expected_arn: The ARN this deploy registered, or None to skip.

    Raises:
        DeploymentError: If PRIMARY runs a different task definition.
    """
    actual_arn = deployment.get("taskDefinition")
    if expected_arn is not None and actual_arn != expected_arn:
        raise DeploymentError(
            f"{service_name}: service stabilized on task definition "
            f"{actual_arn}, but this deploy registered {expected_arn} "
            f"(circuit breaker rolled back?)",
            service_name=service_name,
            error_type="rollback_detected",
        )


class _SettleWindow(NamedTuple):
    """A run of consecutive qualifying polls of one ECS deployment.

    Success requires the criterion (running == desired > 0, nothing pending,
    PRIMARY running the expected ARN) to hold across the whole window: the
    first qualifying poll plus ``stability.settle_polls`` confirming polls of
    the same deployment with ``failedTasks`` not increasing, spanning
    ~``settle_seconds``. One poll is not enough -- a crash loop whose tasks
    live ~15s can show running == desired on every poll with a different task
    each time.
    """

    deployment_id: str | None
    failed_at_start: int
    confirming_polls: int


def _advance_settle_window(
    window: _SettleWindow | None, deployment: dict, failed: int
) -> _SettleWindow:
    """Fold one qualifying poll into the settle window.

    Args:
        window: Window built by the previous qualifying poll, or None when
            the previous poll did not qualify.
        deployment: The PRIMARY deployment this poll observed.
        failed: ``failedTasks`` this poll reported.

    Returns:
        A window restarted at zero confirming polls when this poll cannot
        confirm the previous one (there was no window, the deployment id
        changed, or failures grew); otherwise the same window with one more
        confirming poll.
    """
    deployment_id = deployment.get("id")
    if window is None or window.deployment_id != deployment_id or failed > window.failed_at_start:
        return _SettleWindow(deployment_id, failed, 0)
    return _SettleWindow(window.deployment_id, window.failed_at_start, window.confirming_polls + 1)


def _format_poll_status(deployment: dict, running: int, desired: int, failed: int) -> str:
    """Render the operator-facing one-line status for a poll.

    Args:
        deployment: The PRIMARY deployment this poll observed.
        running: Running task count.
        desired: Desired task count.
        failed: ``failedTasks`` this poll reported.

    Returns:
        A line such as ``running=1/2`` or ``running=0/0, rollout=COMPLETED``.
    """
    status = f"running={running}/{desired}"
    if failed > 0:
        status += f", failed={failed}"
    if desired == 0:
        # At 0/0 the counts never change; the rollout state is the only
        # progress signal (and the only clue in a timeout message).
        status += f", rollout={deployment.get('rolloutState', 'UNKNOWN')}"
    return status


def _wait_for_service_stable(
    ctx: DeploymentContext,
    service_name: str,
    stability: StabilityConfig = StabilityConfig(),  # noqa: B008
    expected_arn: str | None = None,
) -> None:
    """Wait for a single service to stabilize.

    Success requires a full ``_SettleWindow`` of qualifying polls, which
    documents what qualifies and why one poll is not enough. Costs
    ~settle_seconds per service, all services in parallel.

    A scale-to-zero service (desired == 0, e.g. held there by queue-depth
    autoscaling) instead succeeds as soon as PRIMARY reports rolloutState
    COMPLETED and runs ``expected_arn``: with no tasks there is nothing for
    the settle window to observe, so ECS's own rollout verdict is the signal.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        stability: Polling configuration for stability checks.
        expected_arn: Task definition ARN this deploy registered, or None to
            accept whatever PRIMARY runs.

    Raises:
        DeploymentError: If a fatal error, FAILED rollout, or rollback to a
            different task definition is detected.
        RuntimeError: If the service doesn't stabilize within max_attempts.
    """
    last_status = ""
    consecutive_failures = 0
    settling: _SettleWindow | None = None

    for _ in range(1, stability.max_attempts + 1):
        service = _describe_service_or_raise(ctx, service_name)
        events = service.get("events", [])[:5]  # Check last 5 events
        _check_for_fatal_errors(events, service_name)

        # A FAILED rollout anywhere in the deployment list is fatal
        _check_for_failed_rollout(service, service_name)

        deployment, running, desired, failed = _get_deployment_status(service)
        if not deployment:
            log_warning(f"{service_name}: No primary deployment found")
            settling = None
            time.sleep(stability.poll_interval)
            continue

        status = _format_poll_status(deployment, running, desired, failed)
        if status != last_status:
            print(f"  {service_name}: {status}")
            last_status = status

        # Check for success: running matches desired and no pending tasks
        pending = deployment.get("pendingCount", 0)

        if running == desired and pending == 0 and running > 0:
            _check_expected_task_definition(deployment, service_name, expected_arn)
            settling = _advance_settle_window(settling, deployment, failed)
            if settling.confirming_polls >= stability.settle_polls:
                log_success(f"{service_name} (stable)")
                return
        elif running == 0 and desired == 0 and pending == 0:
            # With no tasks the settle window has nothing to observe, so ECS's
            # own rollout verdict gates this route instead: PRIMARY COMPLETED
            # means the new task definition is what a scale-up runs.
            settling = None
            if deployment.get("rolloutState") == "COMPLETED":
                _check_expected_task_definition(deployment, service_name, expected_arn)
                log_success(f"{service_name} (stable, scaled to zero)")
                return
        else:
            settling = None
            if failed >= stability.failure_threshold:
                _raise_task_failure(events, service_name, failed)
            consecutive_failures = _track_no_progress(
                events, service_name, running, failed, consecutive_failures
            )

        time.sleep(stability.poll_interval)

    # Timeout
    raise RuntimeError(
        f"Service {service_name} did not stabilize after "
        f"{stability.max_attempts * stability.poll_interval}s. "
        f"Last status: {last_status}"
    )


def _get_service_target_group(ecs_client, cluster_name: str, service_name: str) -> str | None:
    """Get the target group ARN for a service if it has a load balancer.

    Args:
        ecs_client: boto3 ECS client.
        cluster_name: Name of the ECS cluster.
        service_name: Name of the service.

    Returns:
        Target group ARN or None if not load balanced.
    """
    try:
        response = ecs_client.describe_services(cluster=cluster_name, services=[service_name])
    except ClientError:
        return None

    if not response.get("services"):
        return None

    service = response["services"][0]
    load_balancers = service.get("loadBalancers", [])

    if load_balancers:
        return load_balancers[0].get("targetGroupArn")

    return None


def _wait_for_target_group_healthy(
    elbv2_client,
    target_group_arn: str,
    service_name: str,
    poll_interval: int = 10,
    max_attempts: int = 30,
) -> bool:
    """Wait for target group to have healthy targets.

    Args:
        elbv2_client: boto3 ELBv2 client.
        target_group_arn: ARN of the target group.
        service_name: Service name for logging.
        poll_interval: Seconds between checks.
        max_attempts: Maximum polling attempts.

    Returns:
        True if targets are healthy, False if timed out.
    """
    last_status = ""

    for _ in range(1, max_attempts + 1):
        try:
            response = elbv2_client.describe_target_health(TargetGroupArn=target_group_arn)
        except ClientError as e:
            log_warning(f"Could not check target health: {e}")
            return True  # Don't fail deployment if we can't check

        targets = response.get("TargetHealthDescriptions", [])
        healthy = sum(1 for t in targets if t.get("TargetHealth", {}).get("State") == "healthy")
        total = len(targets)

        status = f"healthy={healthy}/{total}"

        if status != last_status:
            print(f"  {service_name} target group: {status}")
            last_status = status

        if healthy > 0 and healthy == total:
            log_success(f"{service_name} targets healthy")
            return True

        time.sleep(poll_interval)

    log_warning(f"{service_name}: Target group health check timed out (last: {last_status})")
    return False
