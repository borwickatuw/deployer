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
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _env_file_var_names(config: dict[str, Any], base_dir: Path) -> list[str]:
    """Collect variable names from a service's env_file entries.

    env_file may be a string, or a list of strings / {path, required}
    mappings. Files marked required: false may be absent.
    """
    entries = config.get("env_file", [])
    if isinstance(entries, str):
        entries = [entries]

    names: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            path = base_dir / entry["path"]
            required = entry.get("required", True)
        else:
            path = base_dir / entry
            required = True

        if not path.exists():
            if required:
                raise FileNotFoundError(f"env_file not found: {path}")
            continue

        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name = line.split("=")[0].strip()
            if name.startswith("export "):
                name = name[len("export ") :].strip()
            names.append(name)

    return names


# YAML values can be str, list, or dict  (re-evaluate-by: 2026-11 review)


# pysmelly: ignore isinstance-chain
def get_compose_services(compose: dict[str, Any], base_dir: Path | None = None) -> dict[str, dict]:
    """Extract services from docker-compose.yml with their properties.

    Args:
        compose: Parsed docker-compose.yml dictionary.
        base_dir: Directory containing the compose file. When given,
            variables from each service's env_file entries are merged
            into that service's "environment" list (env_file paths are
            relative to the compose file). Without it, env_file entries
            are skipped.

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

        # Merge variables provided via env_file
        if base_dir is not None:
            for var_name in _env_file_var_names(config, base_dir):
                if var_name not in services[name]["environment"]:
                    services[name]["environment"].append(var_name)

    return services
