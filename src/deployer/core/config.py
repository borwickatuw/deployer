"""Environment configuration loading and resolution.

This module handles loading config.toml files from environment directories
and resolving ${tofu:...} placeholders by calling tofu output commands.
"""

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from ..utils import run_command
from ..utils.aws_profile import PROFILE_DEFAULTS, get_environment_aws_profile

# Required fields in environment config.toml
# These are the minimum fields needed for a successful deployment
REQUIRED_CONFIG_FIELDS = {
    "infrastructure": [
        "cluster_name",
        "ecr_prefix",
        "execution_role_arn",
        "task_role_arn",
        "security_group_id",
        "private_subnet_ids",
    ],
}


def validate_environment_config(config: dict) -> list[str]:
    """Validate that required fields are present in environment config.

    Checks that all required infrastructure fields are present and non-empty.
    This is a fail-fast check to catch configuration errors early.

    Args:
        config: The resolved environment configuration dict.

    Returns:
        List of error messages. Empty list if all required fields are present.
    """
    errors = []

    for section, fields in REQUIRED_CONFIG_FIELDS.items():
        section_data = config.get(section, {})
        for field in fields:
            value = section_data.get(field)
            if not value:
                errors.append(f"Missing required field: [{section}].{field}")
            elif isinstance(value, list) and len(value) == 0:
                errors.append(f"Empty list for required field: [{section}].{field}")

    return errors


# Regex to match ${tofu:output_name} placeholders
TOFU_PLACEHOLDER_PATTERN = re.compile(r"\$\{tofu:([^}]+)\}")


def get_tofu_dir(config: dict, env_path: Path) -> Path:
    """Get the directory to run tofu commands in.

    If config.toml contains [tofu].dir, use that (resolved relative to env_path,
    with ~ expansion). Otherwise, use env_path itself.

    Args:
        config: Parsed (but unresolved) config.toml dict.
        env_path: Path to environment directory.

    Returns:
        Path to the directory where tofu commands should be run.

    Raises:
        FileNotFoundError: If the specified tofu_dir does not exist.
    """
    tofu_dir = config.get("tofu", {}).get("dir")
    if not tofu_dir:
        return env_path

    resolved = Path(os.path.expanduser(tofu_dir))
    if not resolved.is_absolute():
        resolved = (env_path / resolved).resolve()

    if not resolved.is_dir():
        raise FileNotFoundError(f"tofu.dir '{tofu_dir}' does not exist: {resolved}")

    return resolved


def get_all_tofu_outputs(env_path: Path, tofu_dir: Path | None = None) -> dict[str, Any]:
    """Fetch all terraform/tofu outputs at once.

    This is much faster than calling get_tofu_output() for each placeholder,
    as it only runs a single tofu command instead of one per placeholder.

    Note: This function temporarily switches to the infra AWS profile to access
    the S3 backend where state is stored, then restores the original profile.

    Args:
        env_path: Path to environment directory (for AWS profile lookup).
        tofu_dir: Directory to run tofu in. Defaults to env_path.

    Returns:
        Dict mapping output names to their values.

    Raises:
        RuntimeError: If tofu command fails.
    """
    if tofu_dir is None:
        tofu_dir = env_path

    # tofu needs the infra profile to access S3 backend
    # Save current profile and temporarily switch to infra profile
    original_profile = os.environ.get("AWS_PROFILE")
    infra_profile = get_environment_aws_profile(env_path, "infra") or PROFILE_DEFAULTS["infra"]
    os.environ["AWS_PROFILE"] = infra_profile

    try:
        success, output = run_command(
            ["tofu", "output", "-json"],
            cwd=str(tofu_dir),
        )

        if not success:
            raise RuntimeError(
                f"Failed to fetch tofu outputs from {tofu_dir}\n"
                f"Hint: Run 'tofu init' and 'tofu apply' in {tofu_dir}"
            )

        if not output.strip():
            return {}

        try:
            data = json.loads(output.strip())
            # Each output is {"value": ..., "type": ..., "sensitive": ...}
            # Extract just the values
            return {k: v["value"] for k, v in data.items()}
        except (json.JSONDecodeError, KeyError) as e:
            raise RuntimeError(f"Failed to parse tofu outputs: {e}") from e
    finally:
        # Restore original profile
        if original_profile:
            os.environ["AWS_PROFILE"] = original_profile
        elif "AWS_PROFILE" in os.environ:
            del os.environ["AWS_PROFILE"]


def _resolve_tofu_placeholders(value: Any, env_path: Path, tofu_outputs: dict[str, Any]) -> Any:
    """Recursively resolve ${tofu:...} placeholders in a value.

    Args:
        value: Value to resolve (can be string, dict, list, or other).
        env_path: Path to environment directory (used for error messages).
        tofu_outputs: Pre-fetched tofu outputs dict from get_all_tofu_outputs().

    Returns:
        Value with all placeholders resolved.

    Raises:
        RuntimeError: If a required tofu output cannot be resolved.
    """
    if isinstance(value, str):
        # Check if the entire string is a placeholder
        match = TOFU_PLACEHOLDER_PATTERN.fullmatch(value)
        if match:
            # Entire value is a placeholder - return the resolved value directly
            # This preserves types (dict, list) instead of converting to string
            output_name = match.group(1)
            resolved = tofu_outputs.get(output_name)
            if resolved is None:
                raise RuntimeError(
                    f"Could not resolve tofu output: {output_name}\n"
                    f"Hint: If you recently added this output, run 'tofu apply' in {env_path}"
                )
            return resolved

        # Check for embedded placeholders
        def replace_placeholder(m: re.Match) -> str:
            output_name = m.group(1)
            resolved = tofu_outputs.get(output_name)
            if resolved is None:
                raise RuntimeError(
                    f"Could not resolve tofu output: {output_name}\n"
                    f"Hint: If you recently added this output, run 'tofu apply' in {env_path}"
                )
            # Convert to string for embedded placeholders
            if isinstance(resolved, (dict, list)):
                return json.dumps(resolved)
            return str(resolved)

        return TOFU_PLACEHOLDER_PATTERN.sub(replace_placeholder, value)

    elif isinstance(value, dict):
        return {k: _resolve_tofu_placeholders(v, env_path, tofu_outputs) for k, v in value.items()}

    elif isinstance(value, list):
        return [_resolve_tofu_placeholders(item, env_path, tofu_outputs) for item in value]

    else:
        # Preserve other types (int, float, bool, None)
        return value


def load_environment_config(env_path: Path) -> dict:
    """Load and resolve an environment's config.toml.

    This function:
    1. Reads config.toml from the environment directory
    2. Fetches all tofu outputs in a single command (for performance)
    3. Resolves all ${tofu:...} placeholders using the cached outputs
    4. Returns the fully resolved configuration

    Args:
        env_path: Path to environment directory (e.g., environments/myapp-staging).

    Returns:
        Fully resolved configuration dict.

    Raises:
        FileNotFoundError: If config.toml doesn't exist.
        RuntimeError: If tofu outputs cannot be fetched.
    """
    config_file = env_path / "config.toml"

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "rb") as f:
        config = tomllib.load(f)

    # Determine where to run tofu (env_path or config's [tofu].dir)
    tofu_dir = get_tofu_dir(config, env_path)

    # Fetch all tofu outputs at once (much faster than one-by-one)
    tofu_outputs = get_all_tofu_outputs(env_path, tofu_dir)

    # Resolve all placeholders using the cached outputs
    return _resolve_tofu_placeholders(config, env_path, tofu_outputs)


def is_cognito_enabled(resolved_config: dict) -> bool:
    """Check if Cognito authentication is enabled for this environment.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        True if Cognito is enabled, False otherwise.
    """
    cognito = resolved_config.get("cognito", {})
    return cognito.get("enabled", False)


def get_staging_url_from_config(resolved_config: dict) -> str | None:
    """Get staging URL from resolved config (domain_name or alb_dns_name).

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        URL string (with https://), or None if not available.
    """
    # Try domain_name first
    domain_name = resolved_config.get("environment", {}).get("domain_name")
    if domain_name:
        return f"https://{domain_name}"

    # Fall back to ALB DNS name
    alb_dns = resolved_config.get("infrastructure", {}).get("alb_dns_name")
    if alb_dns:
        return f"https://{alb_dns}"

    return None


def get_service_replicas_from_config(resolved_config: dict) -> dict[str, int]:
    """Get service replica counts from resolved config.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        Dict mapping service name to replica count.
    """
    service_config = resolved_config.get("services", {}).get("config", {})
    return {name: cfg.get("replicas", 1) for name, cfg in service_config.items()}


def get_cognito_user_pool_id_from_config(resolved_config: dict) -> str | None:
    """Get Cognito user pool ID from resolved config.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        Cognito user pool ID string, or None if Cognito is not enabled.
    """
    cognito = resolved_config.get("cognito", {})
    if not cognito.get("enabled", False):
        return None
    return cognito.get("user_pool_id")


def get_environment_type(env_config: dict) -> str:
    """Get the environment type from a loaded config.toml.

    Reads [environment].type from the config. This is the canonical source
    for environment type — no naming conventions required.

    Args:
        env_config: Loaded and resolved environment config dict.

    Returns:
        Environment type string (e.g., 'staging', 'production').

    Raises:
        ValueError: If [environment].type is not set in config.toml.
    """
    env_type = env_config.get("environment", {}).get("type")
    if not env_type:
        raise ValueError(
            "Missing [environment].type in config.toml. "
            'Add \'type = "staging"\' (or "production") to the [environment] section.'
        )
    return env_type


def load_deploy_toml(deploy_toml_path: Path) -> dict:
    """Load an application's deploy.toml file.

    Args:
        deploy_toml_path: Path to deploy.toml file.

    Returns:
        Parsed TOML configuration dict.

    Raises:
        FileNotFoundError: If deploy.toml doesn't exist.
    """
    if not deploy_toml_path.exists():
        raise FileNotFoundError(f"Deploy config not found: {deploy_toml_path}")

    with open(deploy_toml_path, "rb") as f:
        return tomllib.load(f)


# pysmelly: ignore isinstance-chain — TOML command entries can be list or dict format
def get_commands_from_deploy_toml(deploy_toml: dict) -> dict[str, list[str]]:
    """Extract the [commands] section from a deploy.toml config.

    The [commands] section defines framework-agnostic commands that can be run
    in ECS containers. Each command maps a name to either:
    - A list of command arguments (simple format)
    - A dict with 'command' (required) and 'ddl' (optional) keys

    Example deploy.toml:
        [commands]
        migrate = { command = ["python", "manage.py", "migrate"], ddl = true }
        shell = ["python", "manage.py", "shell"]

    Args:
        deploy_toml: Parsed deploy.toml config dict.

    Returns:
        Dict mapping command names to argument lists.
        Returns empty dict if no [commands] section exists.
    """
    commands = deploy_toml.get("commands", {})

    # Validate and normalize commands (support both list and dict formats)
    result = {}
    for name, value in commands.items():
        if isinstance(value, list):
            # Simple format: command = ["python", "manage.py", "migrate"]
            if not all(isinstance(arg, str) for arg in value):
                raise ValueError(f"Command '{name}' must be a list of strings")
            result[name] = value
        elif isinstance(value, dict):
            # Extended format: command = { command = [...], ddl = true }
            if "command" not in value:
                raise ValueError(f"Command '{name}' in dict format must have a 'command' key")
            args = value["command"]
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise ValueError(f"Command '{name}' must have a list of strings as 'command'")
            result[name] = args
        else:
            raise ValueError(
                f"Command '{name}' must be a list of strings or a dict with 'command' key, "
                f"got {type(value).__name__}"
            )

    return result


def command_requires_ddl(deploy_toml: dict | None, command_name: str) -> bool:
    """Check if a command requires DDL database privileges.

    Commands that modify database schema (migrations) need DDL privileges
    (CREATE, ALTER, DROP). This is indicated by ddl=true in the command config.

    Example deploy.toml:
        [commands]
        migrate = { command = ["python", "manage.py", "migrate"], ddl = true }
        shell = ["python", "manage.py", "shell"]  # No DDL needed

    Args:
        deploy_toml: Parsed deploy.toml config, or None.
        command_name: Name of the command to check.

    Returns:
        True if the command requires DDL privileges, False otherwise.

    Raises:
        ValueError: If deploy_toml is None (deploy.toml is required).
    """
    if not deploy_toml:
        raise ValueError(
            "deploy.toml is required to check DDL requirements. "
            "Link your environment with: python bin/link-environments.py <env> /path/to/deploy.toml"
        )

    commands = deploy_toml.get("commands", {})
    value = commands.get(command_name)

    if isinstance(value, dict):
        return value.get("ddl", False)

    # Simple list format or command not found — no DDL
    return False


def get_run_command(
    deploy_toml: dict | None,
    command_name: str,
    extra_args: list[str] | None,
) -> list[str]:
    """Get the full command array for a named command.

    Looks up the command in deploy.toml's [commands] section.

    Args:
        deploy_toml: Parsed deploy.toml config, or None.
        command_name: Name of the command (e.g., "migrate", "shell").
        extra_args: Additional arguments to append to the command.

    Returns:
        List of command arguments including any extra_args.

    Raises:
        ValueError: If deploy.toml is None or the command is not found.
    """
    if not deploy_toml:
        raise ValueError(
            "deploy.toml is required to run commands. "
            "Link your environment with: python bin/link-environments.py <env> /path/to/deploy.toml"
        )

    commands = get_commands_from_deploy_toml(deploy_toml)

    if command_name not in commands:
        available = sorted(commands.keys())
        if available:
            raise ValueError(
                f"Unknown command '{command_name}'. " f"Available commands: {', '.join(available)}"
            )
        else:
            raise ValueError(
                f"Unknown command '{command_name}'. "
                f"No commands defined in deploy.toml [commands] section."
            )

    cmd = commands[command_name].copy()

    if extra_args:
        cmd.extend(extra_args)

    return cmd
