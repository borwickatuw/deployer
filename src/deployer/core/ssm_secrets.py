"""Shared utilities for SSM secrets management."""

from pathlib import Path

from deployer.aws import ssm
from deployer.config import parse_deploy_config
from deployer.modules.secrets import (
    explicit_path_error,
    explicit_path_keys,
    normalize_secret_name,
)
from deployer.utils import EnvironmentConfigError, advice_block


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


def get_path_prefix(env_name: str) -> str:
    """Get the SSM path prefix for an environment.

    Args:
        env_name: Environment name like "myapp-staging"

    Returns:
        Path prefix like "/myapp/staging"
    """
    project, environment = parse_environment(env_name)
    return f"/{project}/{environment}"


def get_parameter_path(env_name: str, secret_name: str) -> str:
    """Get the full SSM parameter path.

    Args:
        env_name: Environment name like "myapp-staging"
        secret_name: Secret name like "SECRET_KEY"

    Returns:
        Full parameter path like "/myapp/staging/SECRET_KEY"
    """
    return f"{get_path_prefix(env_name)}/{secret_name}"


def get_secrets_from_deploy_toml(
    deploy_toml_path: Path,
    env_config: dict | None = None,
) -> dict[str, str]:
    """Extract SSM secrets from deploy.toml.

    Args:
        deploy_toml_path: Path to deploy.toml file
        env_config: Environment config.toml for module-style secrets, or None
            when the caller has no environment config -- see
            ``_get_secrets_from_config`` for what None means.

    Returns:
        Dictionary mapping env var names to SSM parameter paths.

    Raises:
        EnvironmentConfigError: If deploy.toml declares module-style secrets
            and ``env_config`` is None.
    """
    config = parse_deploy_config(deploy_toml_path)
    return _get_secrets_from_config(config.get_raw_dict(), env_config)


def _get_secrets_from_config(
    config: dict,
    env_config: dict | None,
) -> dict[str, str]:
    """Extract SSM secrets from a parsed deploy.toml config.

    One format: `[secrets] names = ["SECRET_KEY", ...]`, whose SSM paths come
    from the environment's `[secrets] path_prefix`. The explicit-path form
    `SECRET_KEY = "ssm:/app/${environment}/secret-key"` was removed in 53h-2a
    and is rejected here rather than ignored -- a deploy.toml still using it
    would otherwise report *no* required secrets, which is how
    `bin/ssm-secrets.py check` comes to advise deleting live parameters.

    Args:
        config: Parsed deploy.toml configuration dictionary
        env_config: Environment config.toml for module-style secrets, or None
            when no environment config could be loaded -- which is how
            `get_secrets_from_deploy_toml`'s own optional parameter arrives
            here. Module-style secrets take their SSM paths from the
            environment's `[secrets] path_prefix`, so None means the required
            set is *unknown*, not empty, and this raises rather than answering.
            An env_config that was loaded but carries no `[secrets] path_prefix`
            is a different case: the environment has answered, so module-style
            names still resolve to nothing.

    Returns:
        Dictionary mapping env var names to SSM parameter paths.

    Raises:
        EnvironmentConfigError: If `config` declares module-style secret names
            and `env_config` is None. Returning {} there would let a caller
            conclude that nothing is required -- and so that every live SSM
            parameter under the prefix is unreferenced.
        ValueError: If `config` still uses the removed explicit-path form.
            Same reasoning: an unreadable declaration must not be reported as
            an empty one.
    """
    secrets_config = config.get("secrets", {})

    explicit = explicit_path_keys(secrets_config)
    if explicit:
        raise ValueError(explicit_path_error(explicit))

    names = secrets_config.get("names", [])
    if names and env_config is None:
        raise EnvironmentConfigError(
            "deploy.toml declares module-style secrets ([secrets] names = [...]), "
            "whose SSM paths come from the environment config.toml's "
            "[secrets] path_prefix -- and no environment config.toml was loaded. "
            "The set of required secrets is unknown, not empty."
        )

    path_prefix = (env_config or {}).get("secrets", {}).get("path_prefix", "")
    if not (path_prefix and names):
        return {}

    # Normalize path prefix
    if not path_prefix.startswith("/"):
        path_prefix = "/" + path_prefix
    path_prefix = path_prefix.rstrip("/")

    # Convert SECRET_KEY -> secret-key
    return {name: f"{path_prefix}/{normalize_secret_name(name)}" for name in names}


def check_secrets_exist(
    config: dict,
    env_name: str,
    env_config: dict,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Check which required SSM secrets exist.

    Args:
        config: Parsed deploy.toml configuration dictionary
        env_name: Full environment name (e.g., "myapp-staging")
        env_config: Environment config.toml for module-style secrets

    Returns:
        Tuple of (missing_secrets, present_secrets).
        Each is a list of (env_var_name, ssm_path) tuples.
    """
    # Get required secrets from config
    required_secrets = _get_secrets_from_config(config, env_config)

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
    env_config: dict,
) -> list[str]:
    """Find SSM secrets that exist but aren't referenced in deploy.toml.

    Needs `[secrets] names` and a `path_prefix`; without a prefix there is no
    tree to scan, so the answer is "nothing to report" rather than "everything
    is unreferenced".

    Args:
        config: Parsed deploy.toml configuration dictionary.
        env_config: Environment config.toml for module-style secrets.

    Returns:
        List of unreferenced SSM parameter paths (empty if none or not applicable).

    Raises:
        RuntimeError: If the parameters under the prefix could not be listed.
            The check is advisory, so the caller decides whether that is fatal;
            it must not be reported as "no drift".
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
    declared = _get_secrets_from_config(config, env_config)
    declared_paths = set(declared.values())

    # Get existing secrets from SSM
    existing_params, error = ssm.list_parameters(path_prefix)
    if error:
        # Not [] -- that is "no drift", and a listing that failed checked nothing.
        raise RuntimeError(f"Could not list SSM parameters under {path_prefix}: {error}")

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


def ssm_put_commands(env_name: str, missing: list[tuple[str, str]]) -> list[str]:
    """Build the "ssm-secrets.py put" invocation that creates each missing secret.

    Args:
        env_name: Full environment name (e.g., "myapp-staging")
        missing: List of (env_var_name, ssm_path) tuples for missing secrets

    Returns:
        One command line per missing secret, unindented.
    """
    return [
        f"uv run python bin/ssm-secrets.py put {env_name} {ssm_path.split('/')[-1]}"
        for _env_var, ssm_path in missing
    ]


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
    return advice_block(
        f"Missing {len(missing)} required SSM secret(s):",
        (f"{env_var}: {ssm_path}" for env_var, ssm_path in missing),
        [
            "To create missing secrets, run:",
            *(f"  {c}" for c in ssm_put_commands(env_name, missing)),
        ],
        bullet="  - ",
    )
