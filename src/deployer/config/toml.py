"""Deploy.toml configuration parsing."""

import tomllib
from pathlib import Path
from typing import Any


def parse_deploy_toml(path: Path) -> dict[str, Any]:
    """Parse deploy.toml configuration file.

    Note: For typed access with validation, use parse_deploy_config() instead.
    This function returns the raw dictionary for backward compatibility.

    Args:
        path: Path to deploy.toml file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If file doesn't exist.
        tomllib.TOMLDecodeError: If file is invalid TOML.
    """
    with open(path, "rb") as f:
        return tomllib.load(f)
