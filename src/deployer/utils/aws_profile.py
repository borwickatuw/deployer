"""
AWS profile configuration utilities.

This module provides functions for automatically selecting the correct AWS profile
based on the operation being performed. Profiles are configured per-environment
in config.toml:

    [aws]
    deploy_profile = "deployer-app"      # for deploy.py
    infra_profile = "deployer-infra"     # for tofu.sh
    cognito_profile = "deployer-cognito" # for cognito.py

Priority order:
1. AWS_PROFILE environment variable (explicit override)
2. Environment's config.toml [aws].<operation>_profile
3. Default values (deployer-app, deployer-infra, deployer-cognito)

Usage:
    from deployer.utils.aws_profile import configure_aws_profile_for_environment

    # At the start of deploy.py
    configure_aws_profile_for_environment("deploy", environment)

    # At the start of cognito.py
    configure_aws_profile_for_environment("cognito", environment)
"""

import os
import tomllib
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

from .environment import get_environment_path
from .logging import log

# Default profile names (created by bootstrap terraform)
PROFILE_DEFAULTS = {
    "deploy": "deployer-app",
    "infra": "deployer-infra",
    "cognito": "deployer-cognito",
    "secrets": "deployer-infra",  # SSM secrets use infra profile (has SSM permissions)
}

# Config.toml key names for each operation type
PROFILE_CONFIG_KEYS = {
    "deploy": "deploy_profile",
    "infra": "infra_profile",
    "cognito": "cognito_profile",
    "secrets": "infra_profile",  # SSM secrets use infra profile
}


def get_environment_aws_profile(env_path: Path, operation: str) -> str | None:
    """Get the AWS profile for an operation from an environment's config.toml.

    Reads the raw TOML file without resolving ${tofu:...} placeholders,
    since we only need the [aws] section which should contain literal strings.

    Args:
        env_path: Path to the environment directory
        operation: One of "deploy", "infra", "cognito", or "secrets"

    Returns:
        The profile name from config.toml, or None if not set.
    """
    config_path = env_path / "config.toml"
    if not config_path.exists():
        return None

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)

        aws_config = config.get("aws", {})
        config_key = PROFILE_CONFIG_KEYS.get(operation)
        if config_key:
            return aws_config.get(config_key)
        return None
    except Exception:
        # If we can't read/parse the config, just return None
        return None


def configure_aws_profile_for_environment(
    operation: str,
    environment: str,
    verbose: bool = True,
    validate: bool = False,
) -> None:
    """Configure AWS_PROFILE for an operation, using environment config.

    Priority order:
    1. AWS_PROFILE environment variable (explicit override)
    2. Environment's config.toml [aws].<operation>_profile
    3. Default values

    Args:
        operation: One of "deploy", "infra", "cognito", or "secrets"
        environment: Environment name (e.g., "myapp-staging").
        verbose: If True, log which profile is being used
        validate: If True, validate the profile credentials work before returning.
                  Raises RuntimeError if validation fails.

    Raises:
        RuntimeError: If validate=True and the profile cannot be validated.
    """
    profile_name = None

    # If AWS_PROFILE is explicitly set, use it
    if os.environ.get("AWS_PROFILE"):
        profile_name = os.environ["AWS_PROFILE"]
        if verbose:
            log(f"Using AWS profile: {profile_name} (from AWS_PROFILE)")
    else:
        # Try to get profile from environment's config.toml
        env_path = get_environment_path(environment)
        env_profile = get_environment_aws_profile(env_path, operation)
        if env_profile:
            os.environ["AWS_PROFILE"] = env_profile
            profile_name = env_profile
            config_key = PROFILE_CONFIG_KEYS[operation]
            if verbose:
                log(f"Using AWS profile: {profile_name} (from {environment}/config.toml [aws].{config_key})")

        # Fall back to default if no profile set yet
        if not profile_name:
            default_profile = PROFILE_DEFAULTS[operation]
            os.environ["AWS_PROFILE"] = default_profile
            profile_name = default_profile
            if verbose:
                log(f"Using AWS profile: {profile_name} (default)")

    # Validate credentials if requested
    if validate:
        success, error = validate_aws_profile(profile_name)
        if not success:
            raise RuntimeError(error)


def validate_aws_profile(profile_name: str) -> tuple[bool, str | None]:
    """Validate that an AWS profile exists and has working credentials.

    Checks:
    1. Profile exists in ~/.aws/config or ~/.aws/credentials
    2. Credentials work by calling STS get_caller_identity()

    Args:
        profile_name: Name of the AWS profile to validate.

    Returns:
        Tuple of (success, error_message). If success is True, error_message is None.
    """
    try:
        session = boto3.Session(profile_name=profile_name)
        sts = session.client("sts")
        sts.get_caller_identity()
        return True, None
    except ProfileNotFound:
        return False, (
            f"AWS profile '{profile_name}' not found.\n"
            f"Check your ~/.aws/config and ~/.aws/credentials files.\n"
            f"See docs/GETTING-STARTED.md for profile setup instructions."
        )
    except NoCredentialsError:
        return False, (
            f"No credentials found for AWS profile '{profile_name}'.\n"
            f"Ensure the profile has valid access keys or role configuration."
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "ExpiredToken":
            return False, (
                f"AWS credentials for profile '{profile_name}' have expired.\n"
                f"Refresh your credentials (e.g., re-run `aws sso login` if using SSO)."
            )
        elif error_code == "AccessDenied":
            return False, (
                f"AWS credentials for profile '{profile_name}' are invalid or access is denied.\n"
                f"Check that the credentials have the required permissions."
            )
        return False, f"AWS credential error for profile '{profile_name}': {e}"


# Legacy function for backward compatibility
def configure_aws_profile(operation: str, verbose: bool = True) -> None:
    """Configure AWS_PROFILE using defaults only.

    Deprecated: Use configure_aws_profile_for_environment() instead.

    Args:
        operation: One of "deploy", "infra", "cognito", or "secrets"
        verbose: If True, log which profile is being used
    """
    if os.environ.get("AWS_PROFILE"):
        if verbose:
            log(f"Using AWS profile: {os.environ['AWS_PROFILE']} (from AWS_PROFILE)")
        return

    default_profile = PROFILE_DEFAULTS[operation]
    os.environ["AWS_PROFILE"] = default_profile
    if verbose:
        log(f"Using AWS profile: {default_profile} (default)")
