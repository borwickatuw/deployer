"""Shared CLI utilities for bin/ scripts."""

import sys
from pathlib import Path

from .environment import get_environment_path, validate_environment_deployed
from .logging import log_error


class EnvironmentConfigError(Exception):
    """Raised when environment path or config cannot be loaded."""


def confirm_action(skip: bool = False) -> bool:
    """Prompt for confirmation before a destructive action.

    Args:
        skip: If True, skip the prompt and return True (for --yes flag).

    Returns:
        True if confirmed, False if cancelled.
    """
    if skip:
        return True

    try:
        confirm = input("Continue? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            log_error("Cancelled")
            return False
    except (EOFError, KeyboardInterrupt):
        print()
        log_error("Cancelled")
        return False

    return True


def require_environment(env_name: str) -> tuple[Path, dict]:
    """Load and return the environment path and resolved config.

    Validates the environment exists and loads its config.toml.

    Args:
        env_name: Environment name (e.g., "myapp-staging").

    Returns:
        Tuple of (env_path, resolved_config).

    Raises:
        EnvironmentConfigError: If environment is invalid or config can't be loaded.
    """
    from ..core.config import load_environment_config

    env_path = get_environment_path(env_name)
    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        raise EnvironmentConfigError(str(e)) from e

    return env_path, config


def require_validated_environment(env_name: str) -> tuple[Path, dict]:
    """Load environment with full deployment validation.

    Like require_environment(), but also checks that infrastructure
    has been deployed (terraform state exists).

    Args:
        env_name: Environment name (e.g., "myapp-staging").

    Returns:
        Tuple of (env_path, resolved_config).

    Raises:
        EnvironmentConfigError: If environment is invalid, not deployed,
            or config can't be loaded.
    """
    from ..core.config import load_environment_config

    env_path, error = validate_environment_deployed(env_name)
    if error:
        raise EnvironmentConfigError(error)

    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        raise EnvironmentConfigError(str(e)) from e

    return env_path, config
