"""Deploy.toml configuration parsing."""

from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore


# Known top-level sections in deploy.toml
KNOWN_SECTIONS = {
    "application",
    "images",
    "services",
    "environment",
    "secrets",
    "migrations",
    "audit",
    "commands",
    # Resource module declarations
    "database",
    "cache",
    "storage",
    "cdn",
}

# Known keys for each section
KNOWN_KEYS = {
    "application": {"name", "description", "source", "ecr_prefix"},
    "images": {
        # Per-image keys
        "context",
        "dockerfile",
        "target",
        "push",
        "depends_on",
        "build_args",
    },
    "services": {
        # Per-service keys
        "image",
        "port",
        "command",
        "health_check_path",
        "path_pattern",
        "environment",
        "min_cpu",
        "min_memory",
    },
    "migrations": {"enabled", "service", "command"},
    "audit": {"ignore_services", "service_mapping", "ignore_env_vars", "ignore_images"},
    # Resource module declarations
    "database": {"type"},
    "cache": {"type"},
    "storage": {"type", "buckets"},
    "cdn": {"type"},
    # environment, secrets, and commands allow arbitrary keys
    # secrets can also have "names" for the new declarative style
}


def validate_deploy_toml(config: dict[str, Any]) -> list[str]:
    """Validate deploy.toml configuration and return warnings for unknown options.

    Args:
        config: Parsed deploy.toml dictionary.

    Returns:
        List of warning messages for unrecognized configuration options.
    """
    warnings = []

    # Check for unknown top-level sections
    for section in config:
        if section not in KNOWN_SECTIONS:
            warnings.append(f"Unknown top-level section: [{section}]")

    # Validate [application] section
    if "application" in config:
        app = config["application"]
        if isinstance(app, dict):
            for key in app:
                if key not in KNOWN_KEYS["application"]:
                    warnings.append(f"Unknown key in [application]: {key}")

    # Validate [images] section - each subsection is an image definition
    if "images" in config:
        images = config["images"]
        if isinstance(images, dict):
            for image_name, image_config in images.items():
                if isinstance(image_config, dict):
                    for key in image_config:
                        if key not in KNOWN_KEYS["images"]:
                            warnings.append(
                                f"Unknown key in [images.{image_name}]: {key}"
                            )
                        # Validate build_args subsections
                        if key == "build_args" and isinstance(image_config[key], dict):
                            for arg_key, arg_value in image_config[key].items():
                                # build_args can have environment-specific subsections
                                # or direct key-value pairs, both are valid
                                pass

    # Validate [services] section - each subsection is a service definition
    if "services" in config:
        services = config["services"]
        if isinstance(services, dict):
            for service_name, service_config in services.items():
                if isinstance(service_config, dict):
                    for key in service_config:
                        if key not in KNOWN_KEYS["services"]:
                            warnings.append(
                                f"Unknown key in [services.{service_name}]: {key}"
                            )

    # Validate [migrations] section
    if "migrations" in config:
        migrations = config["migrations"]
        if isinstance(migrations, dict):
            for key in migrations:
                if key not in KNOWN_KEYS["migrations"]:
                    warnings.append(f"Unknown key in [migrations]: {key}")

    # Validate [audit] section
    if "audit" in config:
        audit = config["audit"]
        if isinstance(audit, dict):
            for key in audit:
                if key not in KNOWN_KEYS["audit"]:
                    warnings.append(f"Unknown key in [audit]: {key}")

    # [environment], [secrets], and [commands] allow arbitrary keys,
    # so no validation needed for them

    return warnings


def parse_deploy_toml(path: Path) -> dict[str, Any]:
    """Parse deploy.toml configuration file.

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


def get_deploy_services(deploy: dict[str, Any]) -> dict[str, dict]:
    """Extract services from deploy.toml.

    Args:
        deploy: Parsed deploy.toml dictionary.

    Returns:
        Dictionary mapping service names to their configuration.
    """
    services = {}
    for name, config in deploy.get("services", {}).items():
        services[name] = {
            "image": config.get("image"),
            "port": config.get("port"),
            "command": config.get("command"),
        }
    return services


def get_deploy_images(deploy: dict[str, Any]) -> dict[str, dict]:
    """Extract images from deploy.toml.

    Args:
        deploy: Parsed deploy.toml dictionary.

    Returns:
        Dictionary mapping image names to their build configuration.
    """
    images = {}
    for name, config in deploy.get("images", {}).items():
        images[name] = {
            "context": config.get("context"),
            "dockerfile": config.get("dockerfile", "Dockerfile"),
            "depends_on": config.get("depends_on", []),
            "push": config.get("push", True),
        }
    return images


def get_deploy_env_vars(deploy: dict[str, Any]) -> set[str]:
    """Extract all environment variable names from deploy.toml.

    Includes:
    - Explicit environment variables from [environment] section
    - Environment-specific overrides (e.g., [environment.staging])
    - Service-specific environment variables (e.g., [services.X.environment])
    - Secret names
    - Variables that modules will inject based on declared resources

    Args:
        deploy: Parsed deploy.toml dictionary.

    Returns:
        Set of environment variable names.
    """
    env_vars = set()

    # Base environment
    env_vars.update(deploy.get("environment", {}).keys())

    # Environment overrides (staging, production, etc.)
    for key, value in deploy.get("environment", {}).items():
        if isinstance(value, dict):
            env_vars.update(value.keys())

    # Service-specific environment variables ([services.X.environment])
    for service_name, service_config in deploy.get("services", {}).items():
        if isinstance(service_config, dict):
            service_env = service_config.get("environment", {})
            # Add base service env vars
            for key, value in service_env.items():
                if not isinstance(value, dict):
                    env_vars.add(key)
                else:
                    # Environment-specific overrides within service
                    env_vars.update(value.keys())

    # Legacy secrets format (SECRET_KEY = "ssm:/path")
    secrets_config = deploy.get("secrets", {})
    for key in secrets_config:
        if key != "names":  # Skip the names list
            env_vars.add(key)

    # Module-injected variables based on declared resources
    env_vars.update(_get_module_injected_vars(deploy))

    return env_vars


def _get_module_injected_vars(deploy: dict[str, Any]) -> set[str]:
    """Get environment variable names that modules will inject.

    Based on what resource modules are declared in deploy.toml,
    determine what env vars will be injected at deploy time.

    Args:
        deploy: Parsed deploy.toml dictionary.

    Returns:
        Set of environment variable names that modules will inject.
    """
    injected = set()

    # Database module: DB_HOST, DB_PORT, DB_NAME, DB_USERNAME, DB_PASSWORD
    if "database" in deploy:
        injected.update({"DB_HOST", "DB_PORT", "DB_NAME", "DB_USERNAME", "DB_PASSWORD"})

    # Cache module: REDIS_URL
    if "cache" in deploy:
        injected.add("REDIS_URL")

    # Storage module: S3_{NAME}_BUCKET for each declared bucket
    storage = deploy.get("storage", {})
    buckets = storage.get("buckets", [])
    for bucket in buckets:
        bucket_upper = bucket.upper()
        injected.add(f"S3_{bucket_upper}_BUCKET")
        # Also add optional region var
        injected.add(f"S3_{bucket_upper}_BUCKET_REGION")

    # CDN module: CLOUDFRONT_DOMAIN, CLOUDFRONT_KEY_ID, CLOUDFRONT_PRIVATE_KEY
    if "cdn" in deploy:
        injected.update({"CLOUDFRONT_DOMAIN", "CLOUDFRONT_KEY_ID", "CLOUDFRONT_PRIVATE_KEY"})

    # Secrets module: each secret name in the names list
    secrets = deploy.get("secrets", {})
    names = secrets.get("names", [])
    injected.update(names)

    return injected


def get_audit_config(deploy: dict[str, Any]) -> dict[str, Any]:
    """Extract audit configuration from deploy.toml.

    Args:
        deploy: Parsed deploy.toml dictionary.

    Returns:
        Dictionary with audit configuration including ignore lists and mappings.
    """
    audit = deploy.get("audit", {})
    return {
        "ignore_services": set(audit.get("ignore_services", [])),
        "service_mapping": audit.get("service_mapping", {}),
        "ignore_env_vars": set(audit.get("ignore_env_vars", [])),
        "ignore_images": set(audit.get("ignore_images", [])),
    }
