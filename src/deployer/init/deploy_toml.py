"""Generate deploy.toml from docker-compose.yml or interactively."""

from pathlib import Path
from typing import Any

from deployer.config.compose import get_compose_services, parse_docker_compose

from .framework import detect_framework, get_migration_command

# Infrastructure services that should not be deployed to ECS
INFRASTRUCTURE_SERVICES = {
    "postgres",
    "postgresql",
    "db",
    "database",
    "mysql",
    "mariadb",
    "redis",
    "elasticache",
    "memcached",
    "localstack",
    "minio",
    "mailhog",
    "mailpit",
    "nginx",
    "traefik",
    "caddy",
    "rabbitmq",
    "kafka",
    "zookeeper",
    "elasticsearch",
    "opensearch",
    "mongo",
    "mongodb",
}

# Environment variable patterns that indicate secrets
SECRET_PATTERNS = [
    "SECRET",
    "PASSWORD",
    "KEY",
    "TOKEN",
    "CREDENTIAL",
    "API_KEY",
    "APIKEY",
    "AUTH",
    "PRIVATE",
]

# Environment variables that look like secrets but are actually placeholders/infrastructure
NON_SECRET_ENV_VARS = {
    "DATABASE_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CACHE_URL",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_STORAGE_BUCKET_NAME",
    "ALLOWED_HOSTS",
    "DEBUG",
    "LOG_LEVEL",
}


# ------------------------------------------------------------------------------
# Detection helpers
# ------------------------------------------------------------------------------


def _is_infrastructure_service(name: str) -> bool:
    """Check if a service name indicates infrastructure (not application code)."""
    name_lower = name.lower()
    return any(infra in name_lower for infra in INFRASTRUCTURE_SERVICES)


def is_likely_secret(var_name: str) -> bool:
    """Check if an environment variable name suggests it's a secret."""
    if var_name in NON_SECRET_ENV_VARS:
        return False
    var_upper = var_name.upper()
    return any(pattern in var_upper for pattern in SECRET_PATTERNS)


def extract_port_from_ports(ports: list) -> int | None:
    """Extract container port from docker-compose ports configuration."""
    for port in ports:
        if isinstance(port, str):
            parts = port.split(":")
            if len(parts) == 2:
                return int(parts[1])
            elif len(parts) == 1:
                return int(parts[0])
        elif isinstance(port, int):
            return port
    return None


# ------------------------------------------------------------------------------
# Name normalization helpers
# ------------------------------------------------------------------------------


def _normalize_image_name(service_name: str, app_name: str) -> str:
    """Normalize a service name to an image name."""
    if service_name == app_name:
        return "web"
    return service_name.replace("-", "_").replace(" ", "_")


def _normalize_service_name(service_name: str, app_name: str) -> str:
    """Normalize a service name for deploy.toml."""
    name_lower = service_name.lower()

    if name_lower == app_name.lower():
        return "web"

    if "celery" in name_lower and "worker" in name_lower:
        return "celery"
    if "worker" in name_lower:
        return "worker"
    if "celery" in name_lower:
        return "celery"

    return service_name.replace("-", "_").replace(" ", "_")


def _var_to_ssm_name(var_name: str) -> str:
    """Convert environment variable name to SSM parameter name."""
    return var_name.lower().replace("_", "-")


# ------------------------------------------------------------------------------
# Config generation helpers
# ------------------------------------------------------------------------------


def _read_dockerfile_content(compose_path: Path, services: dict) -> str | None:
    """Try to read Dockerfile content for framework detection."""
    for svc in services.values():
        if svc.get("has_build"):
            context = svc.get("build_context", ".")
            dockerfile = svc.get("dockerfile", "Dockerfile")
            dockerfile_path = compose_path.parent / context / dockerfile
            if dockerfile_path.exists():
                try:
                    return dockerfile_path.read_text()
                except Exception:  # noqa: BLE001, S110 — best-effort Dockerfile read
                    pass
    return None


def _filter_app_services(services: dict) -> dict:
    """Filter to application services (with builds, not infrastructure, not optional)."""
    return {
        name: svc
        for name, svc in services.items()
        if svc.get("has_build") and not _is_infrastructure_service(name) and not svc.get("profiles")
    }


def _build_images_config(app_services: dict, app_name: str) -> dict:
    """Build the images section of deploy.toml config."""
    images = {}
    for name, svc in app_services.items():
        image_name = _normalize_image_name(name, app_name)
        images[image_name] = {
            "context": svc.get("build_context", "."),
            "dockerfile": svc.get("dockerfile", "Dockerfile"),
        }
    return images


def _build_services_config(app_services: dict, app_name: str, framework: str) -> dict:
    """Build the services section of deploy.toml config."""
    deploy_services = {}
    for name, svc in app_services.items():
        image_name = _normalize_image_name(name, app_name)
        port = extract_port_from_ports(svc.get("ports", []))

        service_config: dict[str, Any] = {"image": image_name}

        if port:
            service_config["port"] = port
            if framework == "django":
                service_config["health_check_path"] = "/health/"
            elif framework == "rails":
                service_config["health_check_path"] = "/health"
            else:
                service_config["health_check_path"] = "/health"

        deploy_services[_normalize_service_name(name, app_name)] = service_config

    return deploy_services


def _build_environment_config(all_env_vars: set, app_name: str) -> tuple[dict, dict]:
    """Build environment and secrets sections of deploy.toml config."""
    environment = {}
    secrets = {}

    for var_name in sorted(all_env_vars):
        if is_likely_secret(var_name):
            secrets[var_name] = f"ssm:/{app_name}/${{environment}}/{_var_to_ssm_name(var_name)}"
        elif var_name == "DATABASE_URL":
            environment["DATABASE_URL"] = "${database_url}"
        elif var_name in {"REDIS_URL", "CELERY_BROKER_URL"}:
            environment["REDIS_URL"] = "${redis_url}"
            if var_name == "CELERY_BROKER_URL":
                environment["CELERY_BROKER_URL"] = "${redis_url}"
        elif var_name == "ALLOWED_HOSTS":
            environment["ALLOWED_HOSTS"] = "*"
        elif var_name not in {"DEBUG", "LOG_LEVEL"}:
            environment[var_name] = ""

    if "ALLOWED_HOSTS" not in environment:
        environment["ALLOWED_HOSTS"] = "*"

    return environment, secrets


def _build_migrations_config(framework: str, deploy_services: dict, app_name: str) -> dict | None:
    """Build the migrations section if framework detected."""
    migration_cmd = get_migration_command(framework)
    if not migration_cmd:
        return None

    # Find the main web service
    web_service = None
    for name in deploy_services:
        if "web" in name.lower() or name == app_name:
            web_service = name
            break
    if not web_service:
        web_service = list(deploy_services.keys())[0]

    return {
        "enabled": True,
        "service": web_service,
        "command": migration_cmd,
    }


# ------------------------------------------------------------------------------
# Main generation function
# ------------------------------------------------------------------------------


def generate_deploy_toml(
    compose_path: Path | None,
    app_name: str | None,
    compose_data: dict | None = None,
) -> dict[str, Any]:
    """Generate deploy.toml configuration from docker-compose.yml."""
    if compose_data is None:
        if compose_path is None:
            raise ValueError("Either compose_path or compose_data must be provided")
        compose_data = parse_docker_compose(compose_path)

    # Determine app name
    if not app_name:
        app_name = compose_path.parent.name if compose_path else "myapp"

    services = get_compose_services(compose_data)

    # Collect all environment variables for framework detection
    all_env_vars = set()
    for svc in services.values():
        all_env_vars.update(svc.get("environment", []))

    # Try to read Dockerfile for framework detection
    dockerfile_content = None
    if compose_path:
        dockerfile_content = _read_dockerfile_content(compose_path, services)

    framework = detect_framework(env_vars=list(all_env_vars), dockerfile_content=dockerfile_content)

    # Filter to application services
    app_services = _filter_app_services(services)
    if not app_services:
        raise ValueError(
            "No application services found in docker-compose.yml. "
            "Services must have a 'build' section and not be infrastructure (postgres, redis, etc.)"
        )

    # Build config sections
    images = _build_images_config(app_services, app_name)
    deploy_services = _build_services_config(app_services, app_name, framework)
    environment, secrets = _build_environment_config(all_env_vars, app_name)
    migrations = _build_migrations_config(framework, deploy_services, app_name)

    # Infrastructure services to ignore in audit
    infra_services = [name for name in services if _is_infrastructure_service(name)]

    # Build final config
    config: dict[str, Any] = {
        "application": {
            "name": app_name,
            "description": f"{app_name.title()} application",
            "source": ".",
        },
        "images": images,
        "services": deploy_services,
        "environment": environment,
    }

    if secrets:
        config["secrets"] = secrets

    config["environment.staging"] = {"DEBUG": "true", "LOG_LEVEL": "DEBUG"}
    config["environment.production"] = {"DEBUG": "false", "LOG_LEVEL": "INFO"}

    if migrations:
        config["migrations"] = migrations

    if infra_services:
        config["audit"] = {"ignore": infra_services}

    return config


# ------------------------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------------------------


def _format_application_section(app: dict) -> list[str]:
    """Format the [application] section."""
    lines = ["[application]"]
    lines.append(f'name = "{app["name"]}"')
    if app.get("description"):
        lines.append(f'description = "{app["description"]}"')
    lines.append(f'source = "{app.get("source", ".")}"')
    lines.append("")
    return lines


def _format_images_section(images: dict) -> list[str]:
    """Format the [images.*] sections."""
    lines = ["# Images to build and push to ECR"]
    for name, img in images.items():
        lines.append(f"[images.{name}]")
        lines.append(f'context = "{img.get("context", ".")}"')
        if img.get("dockerfile"):
            lines.append(f'dockerfile = "{img["dockerfile"]}"')
        lines.append("")
    return lines


def _format_services_section(services: dict) -> list[str]:
    """Format the [services.*] sections."""
    lines = [
        "# Services to deploy to ECS",
        "# NOTE: Sizing (cpu, memory, replicas) is in terraform.tfvars",
    ]
    for name, svc in services.items():
        lines.append(f"[services.{name}]")
        lines.append(f'image = "{svc["image"]}"')
        if svc.get("port"):
            lines.append(f"port = {svc['port']}")
        if svc.get("command"):
            cmd_str = ", ".join(f'"{c}"' for c in svc["command"])
            lines.append(f"command = [{cmd_str}]")
        if svc.get("health_check_path"):
            lines.append(f'health_check_path = "{svc["health_check_path"]}"')
        lines.append("")
    return lines


def _format_environment_section(config: dict) -> list[str]:
    """Format [environment] and environment override sections."""
    lines = [
        "# Environment variables passed to all services",
        "[environment]",
    ]
    for key, value in config.get("environment", {}).items():
        lines.append(f'{key} = "{value}"')
    lines.append("")

    if "environment.staging" in config:
        lines.append("# Staging-specific environment variables")
        lines.append("[environment.staging]")
        for key, value in config["environment.staging"].items():
            lines.append(f'{key} = "{value}"')
        lines.append("")

    if "environment.production" in config:
        lines.append("# Production-specific environment variables")
        lines.append("[environment.production]")
        for key, value in config["environment.production"].items():
            lines.append(f'{key} = "{value}"')
        lines.append("")

    return lines


def _format_secrets_section(config: dict) -> list[str]:
    """Format the [secrets] section."""
    if not config.get("secrets"):
        return []

    app_name = config["application"]["name"]
    lines = [
        "# Secrets from AWS SSM Parameter Store",
        "# Create these parameters before first deployment:",
    ]
    for key in config["secrets"]:
        lines.append(
            f'#   aws ssm put-parameter --name "/{app_name}/staging/{_var_to_ssm_name(key)}" '
            f'--value "..." --type SecureString'
        )
    lines.append("[secrets]")
    for key, value in config["secrets"].items():
        lines.append(f'{key} = "{value}"')
    lines.append("")
    return lines


def _format_migrations_section(migrations: dict) -> list[str]:
    """Format the [migrations] section."""
    if not migrations:
        return []

    lines = ["# Database migrations", "[migrations]"]
    lines.append(f"enabled = {'true' if migrations.get('enabled') else 'false'}")
    lines.append(f'service = "{migrations["service"]}"')
    cmd_str = ", ".join(f'"{c}"' for c in migrations["command"])
    lines.append(f"command = [{cmd_str}]")
    lines.append("")
    return lines


def _format_audit_section(audit: dict) -> list[str]:
    """Format the [audit] section."""
    if not audit:
        return []

    lines = [
        "# Audit configuration - infrastructure services to ignore",
        "[audit]",
    ]
    ignore_str = ", ".join(f'"{s}"' for s in audit["ignore"])
    lines.append(f"ignore = [{ignore_str}]")
    lines.append("")
    return lines


# ------------------------------------------------------------------------------
# Main formatting function
# ------------------------------------------------------------------------------


def format_deploy_toml(config: dict[str, Any]) -> str:
    """Format deploy.toml configuration as a TOML string."""
    lines = [
        "# Application Deployment Configuration",
        "#",
        "# Generated by: bin/init.py deploy-toml",
        "# See docs/CONFIG-REFERENCE.md for complete documentation.",
        "#",
        "# This file defines WHAT to run. Service sizing (cpu, memory, replicas)",
        "# is configured in OpenTofu tfvars per environment.",
        "",
    ]

    lines.extend(_format_application_section(config["application"]))
    lines.extend(_format_images_section(config.get("images", {})))
    lines.extend(_format_services_section(config.get("services", {})))
    lines.extend(_format_environment_section(config))
    lines.extend(_format_secrets_section(config))
    lines.extend(_format_migrations_section(config.get("migrations")))
    lines.extend(_format_audit_section(config.get("audit")))

    return "\n".join(lines)
