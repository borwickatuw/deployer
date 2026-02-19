"""Terraform.tfvars configuration parsing."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TfvarsService:
    """Service configuration from tfvars file."""

    cpu: int
    memory: int
    replicas: int
    load_balanced: bool = False
    port: int | None = None
    health_check_path: str = "/"
    path_pattern: str | None = None


def parse_tfvars(tfvars_path: Path) -> dict[str, TfvarsService]:
    """Parse a terraform.tfvars file to extract service configurations.

    This is a simple HCL parser that handles the specific format we use:
    services = {
      service_name = {
        cpu           = 256
        memory        = 512
        replicas      = 1
        load_balanced = false
        ...
      }
    }

    Args:
        tfvars_path: Path to terraform.tfvars file.

    Returns:
        Dictionary mapping service names to TfvarsService objects.
    """
    if not tfvars_path.exists():
        return {}

    content = tfvars_path.read_text()
    services: dict[str, TfvarsService] = {}

    # Find the services block
    services_match = re.search(r"services\s*=\s*\{(.*?)\n\}", content, re.DOTALL)
    if not services_match:
        return {}

    services_block = services_match.group(1)

    # Find each service definition
    # Pattern matches: service_name = { ... }
    service_pattern = re.compile(r"(\w+)\s*=\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", re.DOTALL)

    for match in service_pattern.finditer(services_block):
        service_name = match.group(1)
        service_content = match.group(2)

        # Extract values from the service block
        def extract_int(key: str, default: int = 0) -> int:
            m = re.search(rf"{key}\s*=\s*(\d+)", service_content)
            return int(m.group(1)) if m else default

        def extract_bool(key: str, default: bool = False) -> bool:
            m = re.search(rf"{key}\s*=\s*(true|false)", service_content)
            return m.group(1) == "true" if m else default

        def extract_str(key: str, default: str | None = None) -> str | None:
            m = re.search(rf'{key}\s*=\s*"([^"]*)"', service_content)
            return m.group(1) if m else default

        services[service_name] = TfvarsService(
            cpu=extract_int("cpu", 256),
            memory=extract_int("memory", 512),
            replicas=extract_int("replicas", 1),
            load_balanced=extract_bool("load_balanced", False),
            port=extract_int("port") or None,
            health_check_path=extract_str("health_check_path", "/") or "/",
            path_pattern=extract_str("path_pattern"),
        )

    return services
