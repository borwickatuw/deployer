"""ECS task definition building."""

from deployer.modules import (
    ModuleContext,
    ModuleRegistry,
    resolve_service_urls,
)
from deployer.utils import log_debug

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
            f"Service '{service_name}' CPU ({merged['cpu']}) is below minimum "
            f"required ({min_cpu}) from deploy.toml.\n"
            f"  Update terraform.tfvars to set cpu >= {min_cpu} for the {service_name} service."
        )

    if min_memory is not None and merged["memory"] < min_memory:
        raise ValueError(
            f"Service '{service_name}' memory ({merged['memory']}) is below minimum "
            f"required ({min_memory}) from deploy.toml.\n"
            f"  Update terraform.tfvars to set memory >= {min_memory} for the {service_name} service."
        )

    # Validate Fargate CPU/memory combination
    validate_fargate_sizing(merged["cpu"], merged["memory"], service_name)

    return merged


def get_environment_variables(
    config: dict,
    environment: str,
    region: str,
    service_name: str | None = None,
    infra_config: dict | None = None,
    env_config: dict | None = None,
    credential_mode: str = "app",
) -> dict[str, str]:
    """Get merged environment variables for a service.

    This function merges environment variables from multiple sources:
    1. Resource modules (database, cache, storage, cdn) - auto-generated
    2. [environment] section from deploy.toml - app-specific
    3. [environment.{env}] overrides
    4. Service-specific environment variables

    Args:
        config: The deployment TOML configuration (deploy.toml).
        environment: Target environment (staging, production).
        region: AWS region.
        service_name: Optional service name for service-specific overrides.
        infra_config: Infrastructure configuration (legacy, for backward compat).
        env_config: Environment configuration (config.toml, for modules).
        credential_mode: For database credentials - "app" for runtime services
            (DML only), "migrate" for migrations (DDL + DML). Default is "app".

    Returns:
        Merged and resolved environment variables dictionary.
    """
    merged = {}

    # Check if using new module system (deploy.toml has resource declarations)
    uses_modules = any(
        config.get(section)
        for section in ["database", "cache", "storage", "cdn", "secrets", "autoscale"]
    )

    if uses_modules and env_config:
        # Get domain name for service URL resolution
        domain_name = env_config.get("environment", {}).get("domain_name")
        account_id = infra_config.get("account_id", "") if infra_config else ""

        # Create module context
        context = ModuleContext(
            region=region,
            account_id=account_id,
            environment=environment,
            app_name=config.get("application", {}).get("name", ""),
            domain_name=domain_name,
            services=config.get("services", {}),
        )

        # Collect from modules
        module_output = ModuleRegistry.collect_all(
            config, env_config, context, credential_mode=credential_mode
        )

        # Add module environment variables
        for env_var in module_output.environment:
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

    # Resolve legacy placeholders for backward compatibility
    if infra_config:
        merged = _resolve_legacy_placeholders(merged, region, environment, infra_config)

    return merged


def _resolve_legacy_placeholders(
    env_vars: dict[str, str],
    region: str,
    environment: str,
    infra_config: dict,
) -> dict[str, str]:
    """Resolve legacy ${placeholder} variables in environment configuration.

    This supports the old-style placeholder syntax for backward compatibility
    during migration to the new module system.

    Args:
        env_vars: Environment variables with potential placeholders.
        region: AWS region.
        environment: Target environment name.
        infra_config: Infrastructure configuration.

    Returns:
        Environment variables with placeholders resolved.
    """
    # Build placeholders from all infra_config values plus built-in ones
    placeholders = {
        "aws_region": region,
        "environment": environment,
    }
    # Add all infra_config values as available placeholders
    for key, value in infra_config.items():
        if isinstance(value, str):
            placeholders[key] = value
        elif isinstance(value, (int, float)):
            placeholders[key] = str(value)

    resolved = {}
    for key, value in env_vars.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            placeholder_name = value[2:-1]
            # Skip service URL references (handled separately)
            if not placeholder_name.startswith("services."):
                resolved[key] = placeholders.get(placeholder_name, value)
            else:
                resolved[key] = value
        else:
            resolved[key] = value

    return resolved


def get_secrets(
    config: dict,
    environment: str,
    region: str,
    account_id: str,
    service_name: str | None = None,
    infra_config: dict | None = None,
    env_config: dict | None = None,
    credential_mode: str = "app",
) -> list[dict[str, str]]:
    """Get secrets configuration for a service.

    This function collects secrets from:
    1. Resource modules (database credentials, CDN private key, etc.)
    2. [secrets].names list (new declarative style)
    3. [secrets] section with explicit paths (legacy style)

    Args:
        config: The deployment TOML configuration (deploy.toml).
        environment: Target environment (staging, production).
        region: AWS region.
        account_id: AWS account ID.
        service_name: Optional service name (unused, for future extension).
        infra_config: Infrastructure configuration (legacy, for backward compat).
        env_config: Environment configuration (config.toml, for modules).
        credential_mode: For database credentials - "app" for runtime services
            (DML only), "migrate" for migrations (DDL + DML). Default is "app".

    Returns:
        List of secrets in ECS format: [{"name": "X", "valueFrom": "arn:..."}]
    """
    secrets = []

    # Check if using new module system
    uses_modules = any(
        config.get(section) for section in ["database", "cache", "storage", "cdn", "autoscale"]
    )

    # Check if using new secrets.names style
    secrets_section = config.get("secrets", {})
    uses_names_style = "names" in secrets_section

    if uses_modules and env_config:
        # Create module context
        context = ModuleContext(
            region=region,
            account_id=account_id,
            environment=environment,
            app_name=config.get("application", {}).get("name", ""),
            domain_name=env_config.get("environment", {}).get("domain_name"),
            services=config.get("services", {}),
        )

        # Collect from modules (includes secrets module if names are declared)
        module_output = ModuleRegistry.collect_all(
            config, env_config, context, credential_mode=credential_mode
        )

        # Add module secrets
        for secret in module_output.secrets:
            secrets.append(
                {
                    "name": secret.name,
                    "valueFrom": secret.value_from,
                }
            )

    elif uses_names_style and env_config:
        # Only using new secrets.names style (no other modules)
        from deployer.modules.secrets import SecretsModule

        module = SecretsModule()
        context = ModuleContext(
            region=region,
            account_id=account_id,
            environment=environment,
            app_name=config.get("application", {}).get("name", ""),
        )
        output = module.collect(secrets_section, env_config.get("secrets", {}), context)
        for secret in output.secrets:
            secrets.append(
                {
                    "name": secret.name,
                    "valueFrom": secret.value_from,
                }
            )

    else:
        # Legacy style: [secrets] with explicit ssm:/path or secretsmanager:arn
        secrets = _get_legacy_secrets(config, environment, region, account_id, infra_config)

    return secrets


def _get_legacy_secrets(
    config: dict,
    environment: str,
    region: str,
    account_id: str,
    infra_config: dict | None = None,
) -> list[dict[str, str]]:
    """Get secrets using legacy explicit path style.

    Supports:
        SECRET_KEY = "ssm:/app/${environment}/secret-key"
        DB_PASSWORD = "secretsmanager:${db_password_secret_arn}"
    """
    secrets_config = config.get("secrets", {})
    infra = infra_config or {}

    # Build placeholders
    placeholders = {"environment": environment}
    for key, val in infra.items():
        if isinstance(val, str):
            placeholders[key] = val
        elif isinstance(val, (int, float)):
            placeholders[key] = str(val)

    secrets = []
    for name, value in secrets_config.items():
        # Skip the new 'names' key
        if name == "names":
            continue

        if not isinstance(value, str):
            continue

        # Resolve placeholders
        result = value
        for ph_name, ph_value in placeholders.items():
            result = result.replace(f"${{{ph_name}}}", ph_value)

        if result.startswith("ssm:"):
            # SSM Parameter Store: ssm:/path/to/param
            param_path = result[4:]  # Remove "ssm:" prefix
            secrets.append(
                {
                    "name": name,
                    "valueFrom": f"arn:aws:ssm:{region}:{account_id}:parameter{param_path}",
                }
            )
        elif result.startswith("secretsmanager:"):
            # Secrets Manager: secretsmanager:arn
            secrets.append(
                {"name": name, "valueFrom": result[15:]}  # Remove "secretsmanager:" prefix
            )

    return secrets


def build_task_definition(
    service_name: str,
    image_uri: str,
    config: dict,
    service_config: dict,
    infra_config: dict,
    app_name: str,
    environment: str,
    region: str,
    account_id: str,
    env_config: dict | None = None,
    credential_mode: str = "app",
) -> dict:
    """Build an ECS task definition for a service.

    Args:
        service_name: Name of the service.
        image_uri: ECR image URI to use.
        config: The deployment TOML configuration (deploy.toml).
        service_config: Service configuration from environment variables.
        infra_config: Infrastructure configuration (roles, subnets, etc.).
        app_name: Application name.
        environment: Target environment (staging, production).
        region: AWS region.
        account_id: AWS account ID.
        env_config: Environment configuration (config.toml, for modules).
        credential_mode: For database credentials - "app" for runtime services
            (DML only), "migrate" for migrations (DDL + DML). Default is "app".

    Returns:
        Task definition dictionary ready for register_task_definition.
    """
    service_cfg = get_service_sizing(service_name, config, service_config)
    service_toml = config.get("services", {}).get(service_name, {})
    task_family = f"{app_name}-{environment}-{service_name}"

    log_debug(f"Building task definition: {task_family}")
    log_debug(f"  CPU: {service_cfg['cpu']}, Memory: {service_cfg['memory']}")
    log_debug(f"  Image: {image_uri}")

    # Build environment variables (merges modules + [environment] section)
    # Add account_id to infra_config for module context
    infra_with_account = {**infra_config, "account_id": account_id}
    env_vars = get_environment_variables(
        config,
        environment,
        region,
        service_name,
        infra_with_account,
        env_config,
        credential_mode=credential_mode,
    )
    task_env = [{"name": k, "value": str(v)} for k, v in env_vars.items()]
    log_debug(f"  Environment variables: {len(env_vars)}")

    # Build secrets (modules + legacy)
    secrets = get_secrets(
        config,
        environment,
        region,
        account_id,
        service_name,
        infra_config,
        env_config,
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
                "awslogs-group": f"/ecs/{app_name}-{environment}",
                "awslogs-region": region,
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
    execution_role_arn = infra_config.get("execution_role_arn")
    task_role_arn = infra_config.get("task_role_arn")

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
