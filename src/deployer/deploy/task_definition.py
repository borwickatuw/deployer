"""ECS task definition building."""

from __future__ import annotations

import re
from collections.abc import Mapping

from deployer.modules import (
    ModuleContext,
    ModuleOutput,
    ModuleRegistry,
    resolve_service_urls,
)
from deployer.utils import advice_block, log_debug

from .autoscaling import autoscale_namespace
from .context import DeploymentContext


def _collect_modules(ctx: DeploymentContext, credential_mode: str) -> ModuleOutput:
    """Collect every declared module's output, or nothing without a config.toml.

    There is one route into the module system and this is it. Until 53h-2a
    both readers below chose between three routes by inspecting the shape of
    deploy.toml, and the choice was wrong: a deploy.toml carrying explicit
    ``[secrets]`` *and* any module section took the module route, which never
    read ``[secrets]`` at all, and the container started without its secrets.
    Collecting unconditionally makes that unreachable by construction rather
    than by a guard -- ``ModuleRegistry.collect_all`` already skips any module
    the application did not declare.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        credential_mode: "app" for runtime services, "migrate" for migrations.

    Returns:
        The merged output, empty when ``ctx.env_config`` is falsy -- no
        environment config means no environment has answered, and every module
        resolves its values from the environment's side.
    """
    if not ctx.env_config:
        return ModuleOutput()

    return ModuleRegistry.collect_all(
        ctx.config, ctx.env_config, _build_module_context(ctx, credential_mode)
    )


def _build_module_context(ctx: DeploymentContext, credential_mode: str) -> ModuleContext:
    """Build a ModuleContext from a DeploymentContext.

    Raises:
        ValueError: If ``credential_mode`` is neither "app" nor "migrate".
    """
    return ModuleContext(
        region=ctx.region, account_id=ctx.account_id, credential_mode=credential_mode
    )


# A ``${name}`` reference in an environment value. ``${tofu:...}`` never gets
# this far: load_environment_config resolves it in config.toml, and deploy.toml
# is not a place it is read.
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _secrets_to_ecs_format(secrets) -> list[dict[str, str]]:
    """Convert module secret outputs to ECS secrets format."""
    return [{"name": s.name, "valueFrom": s.value_from} for s in secrets]


# Valid Fargate CPU/memory combinations
# https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html#task_size
FARGATE_VALID_MEMORY = {
    256: [512, 1024, 2048],
    512: [1024, 2048, 3072, 4096],
    1024: [2048, 3072, 4096, 5120, 6144, 7168, 8192],
    2048: [4096, 5120, 6144, 7168, 8192, 9216, 10240, 11264, 12288, 13312, 14336, 15360, 16384],
    4096: list(range(8192, 30721, 1024)),
    8192: list(range(16384, 61441, 4096)),
    16384: list(range(32768, 122881, 8192)),
}


def validate_fargate_sizing(cpu: int, memory: int, service_name: str) -> None:
    """Validate CPU/memory combination is valid for Fargate.

    Args:
        cpu: CPU units (256, 512, 1024, 2048, 4096, 8192, or 16384)
        memory: Memory in MB
        service_name: Service name for error messages

    Raises:
        ValueError: If the combination is not valid for Fargate.
    """
    if cpu not in FARGATE_VALID_MEMORY:
        valid_cpus = sorted(FARGATE_VALID_MEMORY.keys())
        raise ValueError(
            f"Service '{service_name}' has invalid CPU value: {cpu}.\n"
            f"  Valid Fargate CPU values: {valid_cpus}"
        )

    valid_memory = FARGATE_VALID_MEMORY[cpu]
    if memory not in valid_memory:
        raise ValueError(
            f"Service '{service_name}' has invalid CPU/memory combination: "
            f"{cpu} CPU, {memory} memory.\n"
            f"  Valid memory values for {cpu} CPU: {valid_memory}"
        )


def get_service_sizing(
    service_name: str,
    config: dict,
    service_config: dict,
) -> dict:
    """Get merged service configuration (deploy.toml + environment sizing).

    Args:
        service_name: Name of the service.
        config: The deployment TOML configuration.
        service_config: Service configuration from environment variables.

    Returns:
        Merged service configuration with defaults applied.

    Raises:
        ValueError: If configured CPU or memory is below the minimum required.
    """
    # Start with deploy.toml service config
    base_config = config.get("services", {}).get(service_name, {})

    # Get environment-specific sizing from SERVICE_CONFIG
    env_config = service_config.get(service_name, {})

    # Merge: environment config overrides base config
    merged = {**base_config, **env_config}

    # Apply defaults for required fields if not present anywhere
    defaults = {
        "cpu": 256,
        "memory": 512,
        "replicas": 1,
        "load_balanced": False,
    }
    for key, default in defaults.items():
        if key not in merged:
            merged[key] = default

    # Validate minimums from deploy.toml
    min_cpu = base_config.get("min_cpu")
    min_memory = base_config.get("min_memory")

    if min_cpu is not None and merged["cpu"] < min_cpu:
        raise ValueError(
            f"Service '{service_name}' CPU ({merged['cpu']}) is below minimum "  # noqa: S608 — not SQL
            f"required ({min_cpu}) from deploy.toml.\n"
            f"  Update terraform.tfvars to set cpu >= {min_cpu} "
            f"for the {service_name} service."
        )

    if min_memory is not None and merged["memory"] < min_memory:
        raise ValueError(
            f"Service '{service_name}' memory ({merged['memory']}) is below minimum "  # noqa: S608 — not SQL
            f"required ({min_memory}) from deploy.toml.\n"
            f"  Update terraform.tfvars to set memory >= {min_memory} "
            f"for the {service_name} service."
        )

    # min_replicas (default 1) is the app-declared replica floor. A service
    # that is safe at zero (pull-based queue worker) declares min_replicas = 0;
    # everything else may not be configured down to nothing. The same floor
    # bounds a scaling block's min (autoscaling.validate_scaling_config).
    min_replicas = base_config.get("min_replicas", 1)
    if merged["replicas"] < min_replicas:
        raise ValueError(
            f"Service '{service_name}' replicas ({merged['replicas']}) is below minimum "  # noqa: S608 — not SQL
            f"required ({min_replicas}) from deploy.toml.\n"
            f"  Update terraform.tfvars to set replicas >= {min_replicas} "
            f"for the {service_name} service."
        )

    _validate_port_source(service_name, base_config, env_config, merged["load_balanced"])

    # Validate Fargate CPU/memory combination
    validate_fargate_sizing(merged["cpu"], merged["memory"], service_name)

    return merged


def _validate_port_source(
    service_name: str, base_config: dict, env_config: dict, load_balanced: bool
) -> None:
    """Fail fast unless deploy.toml's ``port`` is the one port for the service.

    The port is the application's: it is what the image listens on, and the
    port mapping and the load-balancer registration both read it from
    deploy.toml. The environment's ``services`` map carries a ``port`` too (tofu
    uses it for the target group), so it may repeat deploy.toml's value but not
    replace or contradict it. Both used to be ignored silently (Phase 69).

    Raises:
        ValueError: If the environment sets a port deploy.toml does not, the two
            disagree, or a load-balanced service has no deploy.toml port.
    """
    toml_port = base_config.get("port")
    env_port = env_config.get("port")  # tofu renders an unset optional as null

    if env_port is not None and toml_port is None:
        raise ValueError(
            f"Service '{service_name}': the environment's services map sets port "
            f"{env_port}, but deploy.toml declares none. The container port "
            f"belongs in deploy.toml: add port = {env_port} to "
            f"[services.{service_name}]."
        )
    if env_port is not None and env_port != toml_port:
        raise ValueError(
            f"Service '{service_name}': the environment's services map sets port "
            f"{env_port}, but deploy.toml's [services.{service_name}] port is "
            f"{toml_port}. Make them agree."
        )
    if load_balanced and toml_port is None:
        raise ValueError(
            f"Service '{service_name}' is load_balanced but deploy.toml "
            f"[services.{service_name}] declares no port, so there is nothing to "
            f"register with the target group. Add the port the container listens "
            f"on, or set load_balanced = false."
        )


def get_environment_variables(
    ctx: DeploymentContext,
    service_name: str | None = None,
    credential_mode: str = "app",
) -> dict[str, str]:
    """Get merged environment variables for a service.

    This function merges environment variables from multiple sources:
    1. Resource modules (database, cache, storage) - auto-generated
    2. [environment] section from deploy.toml - app-specific
    3. [environment.{env}] overrides
    4. Service-specific environment variables

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Optional service name for service-specific overrides.
        credential_mode: For database credentials - "app" for runtime services
            (DML only), "migrate" for migrations (DDL + DML). Default is "app".

    Returns:
        Merged and resolved environment variables dictionary.
    """
    config = ctx.config
    environment = ctx.environment
    region = ctx.region
    env_config = ctx.env_config

    merged = {}

    # Resource modules first, so deploy.toml's own [environment] can override.
    for env_var in _collect_modules(ctx, credential_mode).environment:
        merged[env_var.name] = env_var.value

    # Get [environment] section from deploy.toml
    env_section = config.get("environment", {})

    # Add base [environment] - filter out sub-tables (staging, production, etc.)
    base_env = {k: v for k, v in env_section.items() if not isinstance(v, dict)}
    merged.update(base_env)

    # Merge [environment.{env}] if exists
    env_override = env_section.get(environment, {})
    merged.update(env_override)

    # If service specified, merge service-specific environment variables
    if service_name:
        service_config = config.get("services", {}).get(service_name, {})
        service_env = service_config.get("environment", {})

        # Base service env (filter out sub-tables)
        service_base = {k: v for k, v in service_env.items() if not isinstance(v, dict)}
        merged.update(service_base)

        # Service + environment override
        service_env_override = service_env.get(environment, {})
        merged.update(service_env_override)

    # A scaling block in the environment's tfvars is the single autoscaling
    # switch: it injects the metric namespace and the scaled service list,
    # overriding the "" defaults deploy.toml documents. There is no second
    # config location to keep in agreement (see deploy/autoscaling.py).
    if ctx.scaling_config:
        merged["AUTOSCALE_NAMESPACE"] = autoscale_namespace(ctx.app_name, ctx.environment)
        merged["AUTOSCALE_SERVICES"] = ",".join(sorted(ctx.scaling_config))

    # Resolve service URL references like ${services.api.url}
    # and internal URLs like ${services.web.internal_url}
    if env_config:
        domain_name = env_config.get("environment", {}).get("domain_name")
        service_discovery_namespace = env_config.get("infrastructure", {}).get(
            "service_discovery_namespace"
        )
        merged = resolve_service_urls(
            merged,
            config.get("services", {}),
            domain_name,
            service_discovery_namespace,
        )

    # Resolve legacy placeholders for backward compatibility. account_id is
    # only offered here -- get_secrets() does not add it.
    merged = _resolve_legacy_placeholders(
        merged,
        region,
        environment,
        ctx.infra_config.legacy_placeholders() | {"account_id": ctx.account_id},
    )

    return merged


def stringify_environment(env_vars: Mapping[str, object]) -> dict[str, str]:
    """Stringify an environment map the way the container receives it.

    ``get_environment_variables`` passes deploy.toml values through untouched,
    so TOML ints and bools arrive as ``int`` and ``bool``. ECS takes strings
    only, and this is the one place that decides how they become strings:
    ``str()``, so ``4`` is ``"4"`` and ``false`` is ``"False"`` (Python's
    spelling, not TOML's). The task definition, the deploy log's environment
    block and ``deploy.py env`` all go through here, which is what lets the
    last two claim to show what deploys.

    Args:
        env_vars: Merged environment variables, values as TOML produced them.

    Returns:
        The same map with every value converted by ``str()``.
    """
    return {key: str(value) for key, value in env_vars.items()}


def _resolve_legacy_placeholders(
    env_vars: dict[str, str],
    region: str,
    environment: str,
    infra_placeholders: dict[str, str],
) -> dict[str, str]:
    """Resolve legacy ${placeholder} variables in environment configuration.

    This is the last pass over the environment, so every ``${...}`` still in
    a value must resolve here or it is an error. A placeholder is substituted
    wherever it appears in a value -- the rule ``${tofu:...}`` follows in
    config.toml. Until Phase 69 only a value that was *entirely* one
    placeholder was substituted, and an unknown, embedded or unresolvable one
    reached the container as a literal ``${...}`` string with no warning.

    ``${services.*}`` references are resolved earlier by
    ``resolve_service_urls``; one still here could not be, and is reported
    rather than looked up in this table.

    Args:
        env_vars: Environment variables with potential placeholders.
        region: AWS region.
        environment: Target environment name.
        infra_placeholders: Substitution table from
            ``InfraConfig.legacy_placeholders()``; overrides the built-ins.

    Returns:
        Environment variables with placeholders resolved.

    Raises:
        ValueError: Naming every variable whose value holds a placeholder that
            does not resolve.
    """
    placeholders = {
        "aws_region": region,
        "environment": environment,
        **infra_placeholders,
    }

    unresolved: list[str] = []

    def substitute(key: str, match: re.Match) -> str:
        name = match.group(1)
        if name.startswith("services.") or name not in placeholders:
            unresolved.append(f"'{key}' -> {match.group(0)}")
            return match.group(0)
        return placeholders[name]

    resolved = {}
    for key, value in env_vars.items():
        if isinstance(value, str):
            resolved[key] = _PLACEHOLDER_PATTERN.sub(lambda m, k=key: substitute(k, m), value)
        else:
            resolved[key] = value

    if unresolved:
        raise ValueError(
            advice_block(
                "Environment variables hold placeholders that do not resolve:",
                unresolved,
                (
                    "Available placeholders: " + ", ".join(sorted(placeholders)) + ".",
                    "A list-, map- or unset config.toml value makes no placeholder.",
                    "${services.X.url} needs a path_pattern on X and a domain_name;",
                    "${services.X.internal_url} needs a port on X and service discovery.",
                ),
                bullet="  - ",
            )
        )

    return resolved


def get_secrets(
    ctx: DeploymentContext,
    _service_name: str | None,
    credential_mode: str = "app",
) -> list[dict[str, str]]:
    """Get secrets configuration for a service.

    Every secret comes from a resource module: database credentials from
    ``[database]``, named application secrets from ``[secrets] names``, and so
    on. There is no second route -- ``preflight.check_secrets_style`` rejects
    the removed explicit-path form before a deployment gets this far.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        _service_name: Optional service name (unused, for future extension).
        credential_mode: For database credentials - "app" for runtime services
            (DML only), "migrate" for migrations (DDL + DML). Default is "app".

    Returns:
        List of secrets in ECS format: [{"name": "X", "valueFrom": "arn:..."}]
    """
    return _secrets_to_ecs_format(_collect_modules(ctx, credential_mode).secrets)


def build_task_definition(
    ctx,
    service_name: str,
    image_uri: str,
    credential_mode: str = "app",
) -> dict:
    """Build an ECS task definition for a service.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        service_name: Name of the service.
        image_uri: ECR image URI to use.
        credential_mode: For database credentials - "app" for runtime services
            (DML only), "migrate" for migrations (DDL + DML). Default is "app".

    Returns:
        Task definition dictionary ready for _register_task_definition.
    """
    service_cfg = get_service_sizing(service_name, ctx.config, ctx.service_config)
    service_toml = ctx.config.get("services", {}).get(service_name, {})
    task_family = f"{ctx.app_name}-{ctx.environment}-{service_name}"

    log_debug(f"Building task definition: {task_family}")
    log_debug(f"  CPU: {service_cfg['cpu']}, Memory: {service_cfg['memory']}")
    log_debug(f"  Image: {image_uri}")

    # Build environment variables (merges modules + [environment] section)
    env_vars = get_environment_variables(
        ctx,
        service_name,
        credential_mode=credential_mode,
    )
    task_env = [{"name": k, "value": v} for k, v in stringify_environment(env_vars).items()]
    log_debug(f"  Environment variables: {len(env_vars)}")

    # Build secrets (modules + legacy)
    secrets = get_secrets(
        ctx,
        service_name,
        credential_mode=credential_mode,
    )
    log_debug(f"  Secrets: {len(secrets)}")

    # Build container definition
    container_def = {
        "name": service_name,
        "image": image_uri,
        "essential": True,
        "environment": task_env,
        "secrets": secrets,
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": f"/ecs/{ctx.app_name}-{ctx.environment}",
                "awslogs-region": ctx.region,
                "awslogs-stream-prefix": service_name,
            },
        },
    }

    # Add port mapping if service has a port
    if "port" in service_toml:
        container_def["portMappings"] = [{"containerPort": service_toml["port"], "protocol": "tcp"}]

    # Add command if specified
    if "command" in service_toml:
        container_def["command"] = service_toml["command"]

    # Get execution role and task role ARNs from infra_config
    execution_role_arn = ctx.infra_config.execution_role_arn
    task_role_arn = ctx.infra_config.task_role_arn

    task_def = {
        "family": task_family,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": str(service_cfg["cpu"]),
        "memory": str(service_cfg["memory"]),
        "containerDefinitions": [container_def],
    }

    if execution_role_arn:
        task_def["executionRoleArn"] = execution_role_arn
    if task_role_arn:
        task_def["taskRoleArn"] = task_role_arn

    return task_def
