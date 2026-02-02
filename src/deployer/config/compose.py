"""Docker-compose.yml configuration parsing."""

from pathlib import Path
from typing import Any

import yaml


def parse_docker_compose(path: Path) -> dict[str, Any]:
    """Parse docker-compose.yml file.

    Args:
        path: Path to docker-compose.yml file.

    Returns:
        Parsed compose configuration dictionary.

    Raises:
        FileNotFoundError: If file doesn't exist.
        yaml.YAMLError: If file is invalid YAML.
    """
    with open(path) as f:
        return yaml.safe_load(f)


def get_compose_services(compose: dict[str, Any]) -> dict[str, dict]:
    """Extract services from docker-compose.yml with their properties.

    Args:
        compose: Parsed docker-compose.yml dictionary.

    Returns:
        Dictionary mapping service names to their extracted properties.
    """
    services = {}
    for name, config in compose.get("services", {}).items():
        services[name] = {
            "has_build": "build" in config,
            "build_context": None,
            "dockerfile": None,
            "ports": config.get("ports", []),
            "environment": [],
            "profiles": config.get("profiles", []),
        }

        # Extract build info
        if "build" in config:
            build = config["build"]
            if isinstance(build, str):
                services[name]["build_context"] = build
            elif isinstance(build, dict):
                services[name]["build_context"] = build.get("context", ".")
                services[name]["dockerfile"] = build.get("dockerfile")

        # Extract environment variables
        env = config.get("environment", [])
        if isinstance(env, list):
            for item in env:
                if isinstance(item, str):
                    # Format: VAR=value or VAR=${VAR}
                    var_name = item.split("=")[0]
                    services[name]["environment"].append(var_name)
        elif isinstance(env, dict):
            services[name]["environment"] = list(env.keys())

    return services
