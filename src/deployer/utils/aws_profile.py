"""
AWS profile configuration utilities.

This module provides functions for automatically selecting the correct AWS profile
based on the operation being performed. Profiles are configured per-environment
in config.toml:

    [aws]
    deploy_profile = "deployer-app"      # for deploy.py
    infra_profile = "deployer-infra"     # for tofu.sh
    cognito_profile = "deployer-cognito" # for manage-cognito-access.py

Priority order:
1. AWS_PROFILE environment variable (explicit override)
2. Environment's config.toml [aws].<operation>_profile
3. Default values (deployer-app, deployer-infra, deployer-cognito)

Usage:
    from deployer.utils.aws_profile import configure_aws_profile_for_environment

    # At the start of deploy.py
    configure_aws_profile_for_environment("deploy", environment)

    # At the start of manage-cognito-access.py
    configure_aws_profile_for_environment("cognito", environment)
"""

import os
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # Fallback for older Python

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
    environment: str | None = None,
    verbose: bool = True,
) -> None:
    """Configure AWS_PROFILE for an operation, using environment config.

    Priority order:
    1. AWS_PROFILE environment variable (explicit override)
    2. Environment's config.toml [aws].<operation>_profile
    3. Default values

    Args:
        operation: One of "deploy", "infra", "cognito", or "secrets"
        environment: Environment name (e.g., "myapp-staging"). Required for
                     looking up the profile in config.toml.
        verbose: If True, log which profile is being used
    """
    # If AWS_PROFILE is explicitly set, use it
    if os.environ.get("AWS_PROFILE"):
        if verbose:
            log(f"Using AWS profile: {os.environ['AWS_PROFILE']} (from AWS_PROFILE)")
        return

    # Try to get profile from environment's config.toml
    if environment:
        from .environment import get_environment_path

        env_path = get_environment_path(environment)
        env_profile = get_environment_aws_profile(env_path, operation)
        if env_profile:
            os.environ["AWS_PROFILE"] = env_profile
            if verbose:
                config_key = PROFILE_CONFIG_KEYS.get(operation, operation)
                log(f"Using AWS profile: {env_profile} (from {environment}/config.toml [aws].{config_key})")
            return

    # Fall back to default
    default_profile = PROFILE_DEFAULTS.get(operation)
    if default_profile:
        os.environ["AWS_PROFILE"] = default_profile
        if verbose:
            log(f"Using AWS profile: {default_profile} (default)")
    elif verbose:
        log("Using AWS profile: default (no profile configured)")


# Legacy function for backward compatibility
def configure_aws_profile(operation: str, verbose: bool = True) -> None:
    """Configure AWS_PROFILE using defaults only.

    Deprecated: Use configure_aws_profile_for_environment() instead.

    Args:
        operation: One of "deploy", "infra", "cognito", or "secrets"
        verbose: If True, log which profile is being used
    """
    configure_aws_profile_for_environment(operation, environment=None, verbose=verbose)
