"""Environment configuration loading and resolution.

This module handles loading config.toml files from environment directories
and resolving ${tofu:...} placeholders by calling tofu output commands.
"""

import json
import re
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Fallback for older Python

from ..utils import run_command

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


def get_all_tofu_outputs(env_path: Path) -> dict[str, Any]:
    """Fetch all terraform/tofu outputs at once.

    This is much faster than calling get_tofu_output() for each placeholder,
    as it only runs a single tofu command instead of one per placeholder.

    Note: This function temporarily switches to the infra AWS profile to access
    the S3 backend where state is stored, then restores the original profile.

    Args:
        env_path: Path to environment directory.

    Returns:
        Dict mapping output names to their values.

    Raises:
        RuntimeError: If tofu command fails.
    """
    import os

    from ..utils.aws_profile import PROFILE_DEFAULTS, get_environment_aws_profile

    # tofu needs the infra profile to access S3 backend
    # Save current profile and temporarily switch to infra profile
    original_profile = os.environ.get("AWS_PROFILE")
    infra_profile = get_environment_aws_profile(env_path, "infra") or PROFILE_DEFAULTS["infra"]
    os.environ["AWS_PROFILE"] = infra_profile

    try:
        success, output = run_command(
            ["tofu", "output", "-json"],
            cwd=str(env_path),
        )

        if not success:
            raise RuntimeError(
                f"Failed to fetch tofu outputs from {env_path}\n"
                f"Hint: Run 'tofu init' and 'tofu apply' in {env_path}"
            )

        if not output.strip():
            return {}

        try:
            data = json.loads(output.strip())
            # Each output is {"value": ..., "type": ..., "sensitive": ...}
            # Extract just the values
            return {k: v["value"] for k, v in data.items()}
        except (json.JSONDecodeError, KeyError) as e:
            raise RuntimeError(f"Failed to parse tofu outputs: {e}")
    finally:
        # Restore original profile
        if original_profile:
            os.environ["AWS_PROFILE"] = original_profile
        elif "AWS_PROFILE" in os.environ:
            del os.environ["AWS_PROFILE"]


def get_tofu_output(env_path: Path, output_name: str) -> Any:
    """Get a terraform/tofu output value.

    Note: For batch operations, prefer get_all_tofu_outputs() which is much faster.

    Tries -json first (for complex types), falls back to -raw for simple strings.

    Args:
        env_path: Path to environment directory.
        output_name: Name of the tofu output.

    Returns:
        Output value (dict, list, or string), or None if not found.

    Raises:
        RuntimeError: If tofu command fails.
    """
    # Try JSON first for complex types
    success, output = run_command(
        ["tofu", "output", "-json", output_name],
        cwd=str(env_path),
    )

    if success and output.strip():
        try:
            return json.loads(output.strip())
        except json.JSONDecodeError:
            pass

    # Fall back to raw for simple strings
    success, output = run_command(
        ["tofu", "output", "-raw", output_name],
        cwd=str(env_path),
    )

    if success and output.strip():
        return output.strip()

    return None


def resolve_tofu_placeholders(
    value: Any, env_path: Path, tofu_outputs: dict[str, Any] | None = None
) -> Any:
    """Recursively resolve ${tofu:...} placeholders in a value.

    Args:
        value: Value to resolve (can be string, dict, list, or other).
        env_path: Path to environment directory (used for error messages).
        tofu_outputs: Pre-fetched tofu outputs dict. If None, will fetch
            outputs individually (slower, kept for backward compatibility).

    Returns:
        Value with all placeholders resolved.

    Raises:
        RuntimeError: If a required tofu output cannot be fetched.
    """

    def get_output(output_name: str) -> Any:
        """Look up an output from the cache or fetch individually."""
        if tofu_outputs is not None:
            return tofu_outputs.get(output_name)
        return get_tofu_output(env_path, output_name)

    if isinstance(value, str):
        # Check if the entire string is a placeholder
        match = TOFU_PLACEHOLDER_PATTERN.fullmatch(value)
        if match:
            # Entire value is a placeholder - return the resolved value directly
            # This preserves types (dict, list) instead of converting to string
            output_name = match.group(1)
            resolved = get_output(output_name)
            if resolved is None:
                raise RuntimeError(
                    f"Could not resolve tofu output: {output_name}\n"
                    f"Hint: If you recently added this output, run 'tofu apply' in {env_path}"
                )
            return resolved

        # Check for embedded placeholders
        def replace_placeholder(m: re.Match) -> str:
            output_name = m.group(1)
            resolved = get_output(output_name)
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
        return {k: resolve_tofu_placeholders(v, env_path, tofu_outputs) for k, v in value.items()}

    elif isinstance(value, list):
        return [resolve_tofu_placeholders(item, env_path, tofu_outputs) for item in value]

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

    # Fetch all tofu outputs at once (much faster than one-by-one)
    tofu_outputs = get_all_tofu_outputs(env_path)

    # Resolve all placeholders using the cached outputs
    return resolve_tofu_placeholders(config, env_path, tofu_outputs)


def get_ssm_parameter(name: str, with_decryption: bool = True) -> str | None:
    """Fetch a parameter value from AWS SSM Parameter Store.

    Args:
        name: The parameter name/path (e.g., /deployer/myapp-staging/cognito-test-password).
        with_decryption: Whether to decrypt SecureString parameters.

    Returns:
        The parameter value, or None if not found.
    """
    import boto3
    from botocore.exceptions import ClientError

    ssm = boto3.client("ssm")
    try:
        response = ssm.get_parameter(Name=name, WithDecryption=with_decryption)
        return response["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise


def is_cognito_enabled(resolved_config: dict) -> bool:
    """Check if Cognito authentication is enabled for this environment.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        True if Cognito is enabled, False otherwise.
    """
    cognito = resolved_config.get("cognito", {})
    return cognito.get("enabled", False)


def get_cognito_test_credentials(resolved_config: dict) -> tuple[str, str] | None:
    """Fetch Cognito test credentials for authenticated health checks.

    This retrieves the test account credentials used for automated access
    to Cognito-protected staging environments.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        Tuple of (username, password), or None if Cognito is not enabled
        or credentials are not configured.

    Raises:
        RuntimeError: If credentials are configured but cannot be fetched from SSM.
    """
    cognito = resolved_config.get("cognito", {})

    # Check if Cognito is enabled
    if not cognito.get("enabled", False):
        return None

    # Get test account config
    username = cognito.get("test_username")
    password_ssm = cognito.get("test_password_ssm")

    if not username or not password_ssm:
        return None

    # Fetch password from SSM
    password = get_ssm_parameter(password_ssm)
    if password is None:
        raise RuntimeError(
            f"Cognito test password not found in SSM: {password_ssm}\n"
            f"Create it with: aws ssm put-parameter --name '{password_ssm}' "
            f"--type SecureString --value '<password>'"
        )

    return (username, password)


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


def get_rds_instance_id_from_config(resolved_config: dict) -> str | None:
    """Get RDS instance ID from resolved config.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        RDS instance ID string, or None if not configured.
    """
    return resolved_config.get("infrastructure", {}).get("rds_instance_id")


def get_cluster_name_from_config(resolved_config: dict) -> str | None:
    """Get ECS cluster name from resolved config.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        ECS cluster name string, or None if not configured.
    """
    return resolved_config.get("infrastructure", {}).get("cluster_name")


def get_service_replicas_from_config(resolved_config: dict) -> dict[str, int]:
    """Get service replica counts from resolved config.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        Dict mapping service name to replica count.
    """
    service_config = resolved_config.get("services", {}).get("config", {})
    return {name: cfg.get("replicas", 1) for name, cfg in service_config.items()}


def get_target_group_arn_from_config(resolved_config: dict) -> str | None:
    """Get ALB target group ARN from resolved config.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        Target group ARN string, or None if not configured.
    """
    return resolved_config.get("infrastructure", {}).get("target_group_arn")


def get_ecr_prefix_from_config(resolved_config: dict) -> str | None:
    """Get ECR prefix from resolved config.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        ECR prefix string, or None if not configured.
    """
    return resolved_config.get("infrastructure", {}).get("ecr_prefix")


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


def get_cognito_auth_token(resolved_config: dict) -> str | None:
    """Authenticate with Cognito and return an access token.

    This authenticates using the test account credentials and returns
    an access token that can be used for authenticated HTTP requests
    to Cognito-protected endpoints.

    Args:
        resolved_config: Fully resolved config from load_environment_config().

    Returns:
        Access token string, or None if Cognito is not enabled.

    Raises:
        RuntimeError: If authentication fails.
    """
    import boto3
    from botocore.exceptions import ClientError

    cognito = resolved_config.get("cognito", {})

    # Check if Cognito is enabled
    if not cognito.get("enabled", False):
        return None

    # Get credentials
    credentials = get_cognito_test_credentials(resolved_config)
    if credentials is None:
        raise RuntimeError("Cognito is enabled but test credentials are not configured")

    username, password = credentials

    # Get client ID
    client_id = cognito.get("client_id")
    if not client_id:
        raise RuntimeError("Cognito client_id not found in config")

    # Authenticate
    cognito_client = boto3.client("cognito-idp")
    try:
        response = cognito_client.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
            },
        )

        # Check if we need to handle a challenge (e.g., NEW_PASSWORD_REQUIRED)
        if "ChallengeName" in response:
            raise RuntimeError(
                f"Cognito authentication requires challenge: {response['ChallengeName']}. "
                f"The test account may need password reset."
            )

        return response["AuthenticationResult"]["AccessToken"]

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        raise RuntimeError(f"Cognito authentication failed: {error_code} - {error_msg}")


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
    """
    if not deploy_toml:
        # Fall back to hardcoded list for backward compatibility
        return command_name in ("migrate", "makemigrations")

    commands = deploy_toml.get("commands", {})
    value = commands.get(command_name)

    if isinstance(value, dict):
        return value.get("ddl", False)

    # Simple list format or command not found - check fallback
    if command_name in commands:
        return False  # Explicit command without ddl flag

    # Command not in deploy.toml, use fallback for known DDL commands
    return command_name in ("migrate", "makemigrations")


# Default Django commands for backward compatibility
DJANGO_DEFAULT_COMMANDS = {
    "migrate": ["uv", "run", "python", "manage.py", "migrate"],
    "shell": ["uv", "run", "python", "manage.py", "shell"],
    "dbshell": ["uv", "run", "python", "manage.py", "dbshell"],
    "createsuperuser": ["uv", "run", "python", "manage.py", "createsuperuser"],
    "collectstatic": ["uv", "run", "python", "manage.py", "collectstatic", "--noinput"],
    "check": ["uv", "run", "python", "manage.py", "check"],
    "showmigrations": ["uv", "run", "python", "manage.py", "showmigrations"],
}


def get_run_command(
    deploy_toml: dict | None,
    command_name: str,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Get the full command array for a named command.

    Looks up the command in deploy.toml's [commands] section. If no deploy.toml
    is provided or the command isn't defined, falls back to Django defaults for
    known commands.

    Args:
        deploy_toml: Parsed deploy.toml config, or None.
        command_name: Name of the command (e.g., "migrate", "shell").
        extra_args: Additional arguments to append to the command.

    Returns:
        List of command arguments including any extra_args.

    Raises:
        ValueError: If the command is not found in deploy.toml or defaults.
    """
    commands = {}

    if deploy_toml:
        commands = get_commands_from_deploy_toml(deploy_toml)

    # Look up the command
    if command_name in commands:
        cmd = commands[command_name].copy()
    elif command_name in DJANGO_DEFAULT_COMMANDS:
        cmd = DJANGO_DEFAULT_COMMANDS[command_name].copy()
    else:
        available = sorted(set(commands.keys()) | set(DJANGO_DEFAULT_COMMANDS.keys()))
        raise ValueError(
            f"Unknown command '{command_name}'. Available commands: {', '.join(available)}"
        )

    if extra_args:
        cmd.extend(extra_args)

    return cmd


def get_manage_command(
    deploy_toml: dict | None,
    management_command: str,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Get a Django management command, for backward compatibility.

    This supports the old 'manage' subcommand pattern. It first checks if a
    'manage' command is defined in deploy.toml's [commands] section. If so,
    it appends the management_command and extra_args to that base command.

    If no 'manage' command is defined, it uses Django defaults:
    ["uv", "run", "python", "manage.py", <command>, <extra_args>...]

    Args:
        deploy_toml: Parsed deploy.toml config, or None.
        management_command: The Django management command (e.g., "migrate").
        extra_args: Additional arguments to pass to the management command.

    Returns:
        List of command arguments.
    """
    commands = {}
    if deploy_toml:
        commands = get_commands_from_deploy_toml(deploy_toml)

    # Check for 'manage' base command in deploy.toml
    if "manage" in commands:
        cmd = commands["manage"].copy()
    else:
        # Django default
        cmd = ["uv", "run", "python", "manage.py"]

    cmd.append(management_command)

    if extra_args:
        cmd.extend(extra_args)

    return cmd
