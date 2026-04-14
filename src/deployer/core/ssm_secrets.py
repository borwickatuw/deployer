"""Shared utilities for SSM secrets management."""

from pathlib import Path

from deployer.aws import ssm
from deployer.config import parse_deploy_config


def parse_environment(env_name: str) -> tuple[str, str]:
    """Parse environment name into project and environment.

    Args:
        env_name: Environment name like "myapp-staging"

    Returns:
        Tuple of (project, environment)

    Raises:
        ValueError: If env_name doesn't match expected format
    """
    parts = env_name.rsplit("-", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid environment name '{env_name}'. "
            "Expected format: <project>-<environment> (e.g., myapp-staging)"
        )
    return parts[0], parts[1]


# pysmelly: ignore pass-through-params — abstracts parse_environment() from callers
def get_path_prefix(env_name: str) -> str:
    """Get the SSM path prefix for an environment.

    Args:
        env_name: Environment name like "myapp-staging"

    Returns:
        Path prefix like "/myapp/staging"
    """
    project, environment = parse_environment(env_name)
    return f"/{project}/{environment}"


# pysmelly: ignore pass-through-params — abstracts parse_environment() from callers
def get_parameter_path(env_name: str, secret_name: str) -> str:
    """Get the full SSM parameter path.

    Args:
        env_name: Environment name like "myapp-staging"
        secret_name: Secret name like "SECRET_KEY"

    Returns:
        Full parameter path like "/myapp/staging/SECRET_KEY"
    """
    project, environment = parse_environment(env_name)
    return f"/{project}/{environment}/{secret_name}"


# pysmelly: ignore pass-through-params — convenience wrapper: parse + extract in one call
def get_secrets_from_deploy_toml(
    deploy_toml_path: Path,
    environment: str,
    env_config: dict | None = None,
) -> dict[str, str]:
    """Extract SSM secrets from deploy.toml.

    Args:
        deploy_toml_path: Path to deploy.toml file
        environment: Environment name (e.g., "staging")
        env_config: Environment config.toml for module-style secrets

    Returns:
        Dictionary mapping env var names to SSM parameter paths.
    """
    config = parse_deploy_config(deploy_toml_path)
    return get_secrets_from_config(config.get_raw_dict(), environment, env_config)


# pysmelly: ignore param-clumps — config, env_config, and environment are distinct objects
def get_secrets_from_config(
    config: dict,
    environment: str,
    env_config: dict,
) -> dict[str, str]:
    """Extract SSM secrets from a parsed deploy.toml config.

    Supports two formats:
    1. Legacy: `SECRET_KEY = "ssm:/app/${environment}/secret-key"`
    2. Module: `names = ["SECRET_KEY", ...]` with path_prefix from env_config

    Args:
        config: Parsed deploy.toml configuration dictionary
        environment: Environment name (e.g., "staging")
        env_config: Environment config.toml for module-style secrets

    Returns:
        Dictionary mapping env var names to SSM parameter paths.
    """
    secrets_config = config.get("secrets", {})
    result = {}

    # Check for new module-style secrets (names = [...])
    if "names" in secrets_config:
        names = secrets_config.get("names", [])
        secrets_env_config = env_config.get("secrets", {})
        path_prefix = secrets_env_config.get("path_prefix", "")

        if path_prefix and names:
            # Normalize path prefix
            if not path_prefix.startswith("/"):
                path_prefix = "/" + path_prefix
            path_prefix = path_prefix.rstrip("/")

            for name in names:
                # Convert SECRET_KEY -> secret-key
                param_name = name.replace("_", "-").lower()
                result[name] = f"{path_prefix}/{param_name}"

    # Also check legacy format (ssm:/path)
    for key, value in secrets_config.items():
        if key == "names":
            continue  # Skip the names list
        if isinstance(value, str):
            # Resolve ${environment} placeholder
            resolved = value.replace("${environment}", environment)
            if resolved.startswith("ssm:"):
                # Extract path (remove "ssm:" prefix)
                result[key] = resolved[4:]

    return result


def check_secrets_exist(
    config: dict,
    environment: str,
    env_name: str,
    env_config: dict,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Check which required SSM secrets exist.

    Args:
        config: Parsed deploy.toml configuration dictionary
        environment: Environment type (e.g., "staging")
        env_name: Full environment name (e.g., "myapp-staging")
        env_config: Environment config.toml for module-style secrets

    Returns:
        Tuple of (missing_secrets, present_secrets).
        Each is a list of (env_var_name, ssm_path) tuples.
    """
    # Get required secrets from config
    required_secrets = get_secrets_from_config(config, environment, env_config)

    if not required_secrets:
        return [], []

    # Get existing secrets from SSM
    path_prefix = get_path_prefix(env_name)
    existing_params, error = ssm.list_parameters(path_prefix)

    if error:
        raise RuntimeError(f"Failed to list SSM parameters: {error}")

    # Build set of existing SSM paths
    existing_paths = {p["name"] for p in existing_params}

    # Check each required secret
    missing = []
    present = []

    for env_var, ssm_path in sorted(required_secrets.items()):
        if ssm_path in existing_paths:
            present.append((env_var, ssm_path))
        else:
            missing.append((env_var, ssm_path))

    return missing, present


def check_secrets_drift(
    config: dict,
    environment: str,
    env_config: dict,
) -> list[str]:
    """Find SSM secrets that exist but aren't referenced in deploy.toml.

    Only works with module-style secrets (names = [...] with path_prefix).
    Legacy ssm:/path secrets don't have a predictable prefix to scan.

    Args:
        config: Parsed deploy.toml configuration dictionary.
        environment: Environment type (e.g., "staging").
        env_config: Environment config.toml for module-style secrets.

    Returns:
        List of unreferenced SSM parameter paths (empty if none or not applicable).
    """
    secrets_config = config.get("secrets", {})

    # Only works with module-style secrets
    if "names" not in secrets_config:
        return []

    secrets_env_config = env_config.get("secrets", {})
    path_prefix = secrets_env_config.get("path_prefix", "")
    if not path_prefix:
        return []

    # Normalize path prefix
    if not path_prefix.startswith("/"):
        path_prefix = "/" + path_prefix
    path_prefix = path_prefix.rstrip("/")

    # Get declared secrets from deploy.toml
    declared = get_secrets_from_config(config, environment, env_config)
    declared_paths = set(declared.values())

    # Get existing secrets from SSM
    existing_params, error = ssm.list_parameters(path_prefix)
    if error:
        return []  # Can't check drift if we can't list parameters

    # Deployer-managed parameters that aren't app secrets
    deployer_managed_suffixes = ("/last-migrations-hash",)

    # Find unreferenced secrets
    unreferenced = []
    for param in existing_params:
        if param["name"] not in declared_paths:
            if any(param["name"].endswith(s) for s in deployer_managed_suffixes):
                continue
            unreferenced.append(param["name"])

    return sorted(unreferenced)


def format_missing_secrets_error(
    missing: list[tuple[str, str]],
    env_name: str,
) -> str:
    """Format an error message for missing secrets.

    Args:
        missing: List of (env_var_name, ssm_path) tuples for missing secrets
        env_name: Full environment name (e.g., "myapp-staging")

    Returns:
        Formatted error message with remediation commands
    """
    lines = [f"Missing {len(missing)} required SSM secret(s):"]

    for env_var, ssm_path in missing:
        lines.append(f"  - {env_var}: {ssm_path}")

    lines.append("")
    lines.append("To create missing secrets, run:")

    for _env_var, ssm_path in missing:
        secret_name = ssm_path.split("/")[-1]
        lines.append(f"  uv run python bin/ssm-secrets.py put {env_name} {secret_name}")

    return "\n".join(lines)
