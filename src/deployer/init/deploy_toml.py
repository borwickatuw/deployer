"""Generate deploy.toml from docker-compose.yml or interactively."""

from pathlib import Path
from typing import Any

from deployer.config.compose import get_compose_services, parse_docker_compose
from deployer.modules.secrets import normalize_secret_name

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

# Variables whose deploy.toml value is fixed, whatever the compose file said.
# deploy.toml is checked in, so a local value must never be carried across.
FIXED_ENVIRONMENT_VALUES = {
    "DATABASE_URL": "${database_url}",
    "ALLOWED_HOSTS": "*",
}

# Variables that all name the same Redis endpoint. Whichever one the compose
# file declares, REDIS_URL is emitted -- deployer resolves ${redis_url} from
# the environment's infra config.
REDIS_ALIAS_VARS = {"REDIS_URL", "CELERY_BROKER_URL"}

# Variables the generator leaves out of the shared [environment] table because
# it writes them per-environment in [environment.staging]/[environment.production].
PER_ENVIRONMENT_ONLY_VARS = {"DEBUG", "LOG_LEVEL"}

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
    """Decide whether the generator declares a variable as a secret: an exact
    NON_SECRET_ENV_VARS entry wins, otherwise any SECRET_PATTERNS substring of
    the upper-cased name does. A heuristic, hence "likely" (MONKEY_COUNT hits KEY).
    """
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


# ------------------------------------------------------------------------------
# Config generation helpers
# ------------------------------------------------------------------------------


def _read_dockerfile_content(compose_path: Path, services: dict) -> str | None:
    """Try to read Dockerfile content for framework detection."""
    for svc in services.values():
        if svc.get("has_build"):
            # get_compose_services always sets both keys, leaving them None
            # when the compose file did not spell them out -- so the `or`
            # defaults are load-bearing and a `.get(key, default)` is not.
            context = svc.get("build_context") or "."
            dockerfile = svc.get("dockerfile") or "Dockerfile"
            dockerfile_path = compose_path.parent / context / dockerfile
            if dockerfile_path.exists():
                try:
                    return dockerfile_path.read_text(encoding="utf-8")
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


def _build_services_config(app_services: dict, app_name: str, framework: str | None) -> dict:
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


def _build_environment_config(all_env_vars: set) -> tuple[dict, dict]:
    """Build environment and secrets sections of deploy.toml config.

    Secrets are declared by name only. Where a named secret actually lives is
    the environment's answer -- config.toml's ``[secrets] path_prefix`` -- so
    it has no business in the application's checked-in file.
    """
    # ALLOWED_HOSTS is wide open whether or not the application declared it,
    # so it is seeded rather than patched in afterwards; a declared one takes
    # the same value from the same table below.
    environment: dict[str, str] = {"ALLOWED_HOSTS": FIXED_ENVIRONMENT_VALUES["ALLOWED_HOSTS"]}
    secret_names: list[str] = []

    for var_name in sorted(all_env_vars):
        if is_likely_secret(var_name):
            secret_names.append(var_name)
            continue
        if var_name in REDIS_ALIAS_VARS:
            # An alias also emits REDIS_URL, which the application may not
            # itself have mentioned.
            environment["REDIS_URL"] = "${redis_url}"
            environment[var_name] = "${redis_url}"
            continue
        if var_name not in PER_ENVIRONMENT_ONLY_VARS:
            # Anything without a fixed value is declared empty for the
            # operator to fill in; the compose value is never carried across.
            environment[var_name] = FIXED_ENVIRONMENT_VALUES.get(var_name, "")

    return environment, {"names": secret_names} if secret_names else {}


def _build_migrations_config(
    framework: str | None, deploy_services: dict, app_name: str
) -> dict | None:
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
    environment, secrets = _build_environment_config(all_env_vars)
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
        config["audit"] = {"ignore_services": infra_services}

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
    """Format the [secrets] section.

    The names are all that goes in deploy.toml. The SSM paths appear only as
    comments -- the environment's config.toml ``path_prefix`` is what actually
    resolves them, and the commented commands are there so the operator can
    create the parameters before the first deployment.
    """
    names = config.get("secrets", {}).get("names")
    if not names:
        return []

    app_name = config["application"]["name"]
    lines = [
        "# Secrets the application needs. Their SSM paths come from the",
        "# environment's config.toml [secrets] path_prefix -- see",
        "# docs/CONFIG-REFERENCE.md. Create the parameters before first deploy:",
    ]
    for name in names:
        lines.append(
            f'#   aws ssm put-parameter --name "/{app_name}/staging/{normalize_secret_name(name)}" '
            f'--value "..." --type SecureString'
        )
    lines.append("[secrets]")
    lines.append("names = [" + ", ".join(f'"{name}"' for name in names) + "]")
    lines.append("")
    return lines


def _format_migrations_section(migrations: dict | None) -> list[str]:
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


def _format_audit_section(audit: dict | None) -> list[str]:
    """Format the [audit] section."""
    if not audit:
        return []

    lines = [
        "# Audit configuration - infrastructure services to ignore",
        "[audit]",
    ]
    ignore_str = ", ".join(f'"{s}"' for s in audit["ignore_services"])
    lines.append(f"ignore_services = [{ignore_str}]")
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
