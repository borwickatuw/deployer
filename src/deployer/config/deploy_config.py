"""Deploy.toml configuration dataclasses.

Provides typed configuration classes that replace manual KNOWN_KEYS validation.
Adding a new field only requires updating the relevant dataclass.
"""

from dataclasses import dataclass, field
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
    "autoscale",
}


@dataclass
class ImageConfig:
    """Configuration for a Docker image build."""

    name: str
    context: str
    dockerfile: str = "Dockerfile"
    push: bool = True
    depends_on: list[str] = field(default_factory=list)
    _target: str | dict[str, str] | None = field(default=None, repr=False)
    _build_args: dict[str, Any] = field(default_factory=dict, repr=False)

    def get_target(self, environment: str) -> str | None:
        """Get the Docker build target for this image.

        Supports environment-specific override via target.{environment}.

        Args:
            environment: The target environment (staging, production).

        Returns:
            Target name or None if not specified.
        """
        if isinstance(self._target, dict):
            return self._target.get(environment)
        return self._target

    def get_build_args(self, environment: str) -> dict[str, str]:
        """Get merged build arguments for this image.

        Merge order (later values override earlier):
        1. build_args = { KEY = "value" } - base args
        2. build_args.{environment} = { KEY = "value" } - environment-specific

        Args:
            environment: The target environment (staging, production).

        Returns:
            Merged build arguments dictionary.
        """
        # Start with base build_args - filter out sub-tables (staging, production, etc.)
        merged = {k: v for k, v in self._build_args.items() if not isinstance(v, dict)}

        # Merge environment-specific build_args if exists
        env_override = self._build_args.get(environment, {})
        if isinstance(env_override, dict):
            merged.update(env_override)

        return merged

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "ImageConfig":
        """Create ImageConfig from a dictionary.

        Args:
            name: The image name (key from [images.name]).
            data: The image configuration dictionary.

        Returns:
            ImageConfig instance.
        """
        return cls(
            name=name,
            context=data.get("context", "."),
            dockerfile=data.get("dockerfile", "Dockerfile"),
            push=data.get("push", True),
            depends_on=data.get("depends_on", []),
            _target=data.get("target"),
            _build_args=data.get("build_args", {}),
        )


# Known keys for image configuration (for validation)
_IMAGE_KNOWN_KEYS = {
    "context",
    "dockerfile",
    "target",
    "push",
    "depends_on",
    "build_args",
}


@dataclass
class ServiceConfig:
    """Configuration for an ECS service."""

    name: str
    image: str | None = None
    port: int | None = None
    command: list[str] | None = None
    health_check_path: str | None = None
    path_pattern: str | None = None
    min_cpu: int | None = None
    min_memory: int | None = None
    _environment: dict[str, Any] = field(default_factory=dict, repr=False)

    def get_environment(self, environment: str) -> dict[str, str]:
        """Get merged environment variables for this service.

        Merge order:
        1. Base service env (filter out sub-tables)
        2. Service + environment override

        Args:
            environment: The target environment (staging, production).

        Returns:
            Merged environment variables dictionary.
        """
        # Base service env (filter out sub-tables)
        merged = {k: v for k, v in self._environment.items() if not isinstance(v, dict)}

        # Service + environment override
        env_override = self._environment.get(environment, {})
        if isinstance(env_override, dict):
            merged.update(env_override)

        return merged

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "ServiceConfig":
        """Create ServiceConfig from a dictionary.

        Args:
            name: The service name (key from [services.name]).
            data: The service configuration dictionary.

        Returns:
            ServiceConfig instance.
        """
        return cls(
            name=name,
            image=data.get("image"),
            port=data.get("port"),
            command=data.get("command"),
            health_check_path=data.get("health_check_path"),
            path_pattern=data.get("path_pattern"),
            min_cpu=data.get("min_cpu"),
            min_memory=data.get("min_memory"),
            _environment=data.get("environment", {}),
        )


# Known keys for service configuration (for validation)
_SERVICE_KNOWN_KEYS = {
    "image",
    "port",
    "command",
    "health_check_path",
    "path_pattern",
    "environment",
    "min_cpu",
    "min_memory",
}


@dataclass
class MigrationConfig:
    """Configuration for database migrations."""

    enabled: bool = False
    service: str = "web"
    command: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MigrationConfig":
        """Create MigrationConfig from a dictionary.

        Args:
            data: The migrations configuration dictionary.

        Returns:
            MigrationConfig instance.
        """
        return cls(
            enabled=data.get("enabled", False),
            service=data.get("service", "web"),
            command=data.get("command", []),
        )


# Known keys for migration configuration (for validation)
_MIGRATION_KNOWN_KEYS = {"enabled", "service", "command"}


@dataclass
class AuditConfig:
    """Configuration for deploy.toml audit."""

    ignore_services: set[str] = field(default_factory=set)
    service_mapping: dict[str, str] = field(default_factory=dict)
    ignore_env_vars: set[str] = field(default_factory=set)
    ignore_images: set[str] = field(default_factory=set)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditConfig":
        """Create AuditConfig from a dictionary.

        Args:
            data: The audit configuration dictionary.

        Returns:
            AuditConfig instance.
        """
        return cls(
            ignore_services=set(data.get("ignore_services", [])),
            service_mapping=data.get("service_mapping", {}),
            ignore_env_vars=set(data.get("ignore_env_vars", [])),
            ignore_images=set(data.get("ignore_images", [])),
        )


# Known keys for audit configuration (for validation)
_AUDIT_KNOWN_KEYS = {"ignore_services", "service_mapping", "ignore_env_vars", "ignore_images"}


@dataclass
class ApplicationConfig:
    """Configuration for the application metadata."""

    name: str
    source: str = "."
    description: str | None = None
    ecr_prefix: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApplicationConfig":
        """Create ApplicationConfig from a dictionary.

        Args:
            data: The application configuration dictionary.

        Returns:
            ApplicationConfig instance.

        Raises:
            ValueError: If required 'name' field is missing.
        """
        name = data.get("name")
        if not name:
            raise ValueError("[application] section requires 'name' field")
        return cls(
            name=name,
            source=data.get("source", "."),
            description=data.get("description"),
            ecr_prefix=data.get("ecr_prefix"),
        )


# Known keys for application configuration (for validation)
_APPLICATION_KNOWN_KEYS = {"name", "description", "source", "ecr_prefix"}


@dataclass
class DeployConfig:
    """Complete deploy.toml configuration."""

    application: ApplicationConfig
    images: dict[str, ImageConfig]
    services: dict[str, ServiceConfig]
    migrations: MigrationConfig
    audit: AuditConfig
    _environment: dict[str, Any] = field(default_factory=dict, repr=False)
    _secrets: dict[str, Any] = field(default_factory=dict, repr=False)
    commands: dict[str, list[str]] = field(default_factory=dict)
    database: dict[str, Any] | None = None
    cache: dict[str, Any] | None = None
    storage: dict[str, Any] | None = None
    cdn: dict[str, Any] | None = None
    autoscale: dict[str, Any] | None = None
    _warnings: list[str] = field(default_factory=list, repr=False)
    _path: Path | None = field(default=None, repr=False)

    def get_environment_vars(self, environment: str) -> dict[str, str]:
        """Get merged global environment variables.

        Merge order:
        1. Base [environment] - filter out sub-tables
        2. [environment.{env}] overrides

        Args:
            environment: The target environment (staging, production).

        Returns:
            Merged environment variables dictionary.
        """
        # Base env (filter out sub-tables)
        merged = {k: v for k, v in self._environment.items() if not isinstance(v, dict)}

        # Environment-specific override
        env_override = self._environment.get(environment, {})
        if isinstance(env_override, dict):
            merged.update(env_override)

        return merged

    def get_all_env_var_names(self) -> set[str]:
        """Extract all environment variable names from configuration.

        Includes:
        - Explicit environment variables from [environment] section
        - Environment-specific overrides (e.g., [environment.staging])
        - Service-specific environment variables (e.g., [services.X.environment])
        - Secret names
        - Variables that modules will inject based on declared resources

        Returns:
            Set of environment variable names.
        """
        env_vars: set[str] = set()

        # Base environment
        env_vars.update(self._environment.keys())

        # Environment overrides (staging, production, etc.)
        for key, value in self._environment.items():
            if isinstance(value, dict):
                env_vars.update(value.keys())

        # Service-specific environment variables ([services.X.environment])
        for service in self.services.values():
            # Add base service env vars
            for key, value in service._environment.items():
                if not isinstance(value, dict):
                    env_vars.add(key)
                else:
                    # Environment-specific overrides within service
                    env_vars.update(value.keys())

        # Legacy secrets format (SECRET_KEY = "ssm:/path")
        for key in self._secrets:
            if key != "names":  # Skip the names list
                env_vars.add(key)

        # Module-injected variables based on declared resources
        env_vars.update(self._get_module_injected_vars())

        return env_vars

    def _get_module_injected_vars(self) -> set[str]:
        """Get environment variable names that modules will inject.

        Based on what resource modules are declared in deploy.toml,
        determine what env vars will be injected at deploy time.

        Returns:
            Set of environment variable names that modules will inject.
        """
        injected: set[str] = set()

        # Database module: DB_HOST, DB_PORT, DB_NAME, DB_USERNAME, DB_PASSWORD
        if self.database:
            injected.update({"DB_HOST", "DB_PORT", "DB_NAME", "DB_USERNAME", "DB_PASSWORD"})

        # Cache module: REDIS_URL
        if self.cache:
            injected.add("REDIS_URL")

        # Storage module: S3_{NAME}_BUCKET for each declared bucket
        if self.storage:
            buckets = self.storage.get("buckets", [])
            for bucket in buckets:
                bucket_upper = bucket.upper()
                injected.add(f"S3_{bucket_upper}_BUCKET")
                # Also add optional region var
                injected.add(f"S3_{bucket_upper}_BUCKET_REGION")

        # CDN module: CLOUDFRONT_DOMAIN, CLOUDFRONT_KEY_ID, CLOUDFRONT_PRIVATE_KEY
        if self.cdn:
            injected.update({"CLOUDFRONT_DOMAIN", "CLOUDFRONT_KEY_ID", "CLOUDFRONT_PRIVATE_KEY"})

        # Secrets module: each secret name in the names list
        names = self._secrets.get("names", [])
        injected.update(names)

        # Autoscale module: AUTOSCALE_NAMESPACE, AUTOSCALE_SERVICES
        if self.autoscale:
            injected.update({"AUTOSCALE_NAMESPACE", "AUTOSCALE_SERVICES"})

        return injected

    def get_warnings(self) -> list[str]:
        """Get configuration warnings (unknown keys, etc.).

        Returns:
            List of warning messages.
        """
        return list(self._warnings)

    def get_raw_dict(self) -> dict[str, Any]:
        """Get a dict representation compatible with existing code.

        This provides backward compatibility with code that expects
        the raw dict format.

        Returns:
            Dictionary in the original deploy.toml format.
        """
        result: dict[str, Any] = {
            "application": {
                "name": self.application.name,
                "source": self.application.source,
            },
            "images": {},
            "services": {},
            "environment": self._environment,
            "secrets": self._secrets,
            "migrations": {
                "enabled": self.migrations.enabled,
                "service": self.migrations.service,
                "command": self.migrations.command,
            },
            "audit": {
                "ignore_services": list(self.audit.ignore_services),
                "service_mapping": self.audit.service_mapping,
                "ignore_env_vars": list(self.audit.ignore_env_vars),
                "ignore_images": list(self.audit.ignore_images),
            },
            "commands": self.commands,
        }

        if self.application.description:
            result["application"]["description"] = self.application.description
        if self.application.ecr_prefix:
            result["application"]["ecr_prefix"] = self.application.ecr_prefix

        for name, img in self.images.items():
            img_dict: dict[str, Any] = {
                "context": img.context,
                "dockerfile": img.dockerfile,
                "push": img.push,
                "depends_on": img.depends_on,
            }
            if img._target is not None:
                img_dict["target"] = img._target
            if img._build_args:
                img_dict["build_args"] = img._build_args
            result["images"][name] = img_dict

        for name, svc in self.services.items():
            svc_dict: dict[str, Any] = {}
            if svc.image is not None:
                svc_dict["image"] = svc.image
            if svc.port is not None:
                svc_dict["port"] = svc.port
            if svc.command is not None:
                svc_dict["command"] = svc.command
            if svc.health_check_path is not None:
                svc_dict["health_check_path"] = svc.health_check_path
            if svc.path_pattern is not None:
                svc_dict["path_pattern"] = svc.path_pattern
            if svc.min_cpu is not None:
                svc_dict["min_cpu"] = svc.min_cpu
            if svc.min_memory is not None:
                svc_dict["min_memory"] = svc.min_memory
            if svc._environment:
                svc_dict["environment"] = svc._environment
            result["services"][name] = svc_dict

        if self.database:
            result["database"] = self.database
        if self.cache:
            result["cache"] = self.cache
        if self.storage:
            result["storage"] = self.storage
        if self.cdn:
            result["cdn"] = self.cdn
        if self.autoscale:
            result["autoscale"] = self.autoscale

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path | None = None) -> "DeployConfig":
        """Create DeployConfig from a dictionary.

        Args:
            data: Parsed deploy.toml dictionary.
            path: Optional path to the config file (for error messages).

        Returns:
            DeployConfig instance.

        Raises:
            ValueError: If required fields are missing.
        """
        warnings: list[str] = []

        # Check for unknown top-level sections
        for section in data:
            if section not in KNOWN_SECTIONS:
                warnings.append(f"Unknown top-level section: [{section}]")

        # Parse [application] section (required)
        app_data = data.get("application", {})
        if not app_data:
            raise ValueError("deploy.toml requires [application] section")

        # Validate [application] keys
        for key in app_data:
            if key not in _APPLICATION_KNOWN_KEYS:
                warnings.append(f"Unknown key in [application]: {key}")

        application = ApplicationConfig.from_dict(app_data)

        # Parse [images] section
        images: dict[str, ImageConfig] = {}
        images_data = data.get("images", {})
        for image_name, image_config in images_data.items():
            if isinstance(image_config, dict):
                # Validate image keys
                for key in image_config:
                    if key not in _IMAGE_KNOWN_KEYS:
                        warnings.append(f"Unknown key in [images.{image_name}]: {key}")
                images[image_name] = ImageConfig.from_dict(image_name, image_config)

        # Parse [services] section
        services: dict[str, ServiceConfig] = {}
        services_data = data.get("services", {})
        for service_name, service_config in services_data.items():
            if isinstance(service_config, dict):
                # Validate service keys
                for key in service_config:
                    if key not in _SERVICE_KNOWN_KEYS:
                        warnings.append(f"Unknown key in [services.{service_name}]: {key}")
                services[service_name] = ServiceConfig.from_dict(service_name, service_config)

        # Parse [migrations] section
        migrations_data = data.get("migrations", {})
        for key in migrations_data:
            if key not in _MIGRATION_KNOWN_KEYS:
                warnings.append(f"Unknown key in [migrations]: {key}")
        migrations = MigrationConfig.from_dict(migrations_data)

        # Parse [audit] section
        audit_data = data.get("audit", {})
        for key in audit_data:
            if key not in _AUDIT_KNOWN_KEYS:
                warnings.append(f"Unknown key in [audit]: {key}")
        audit = AuditConfig.from_dict(audit_data)

        return cls(
            application=application,
            images=images,
            services=services,
            migrations=migrations,
            audit=audit,
            _environment=data.get("environment", {}),
            _secrets=data.get("secrets", {}),
            commands=data.get("commands", {}),
            database=data.get("database"),
            cache=data.get("cache"),
            storage=data.get("storage"),
            cdn=data.get("cdn"),
            autoscale=data.get("autoscale"),
            _warnings=warnings,
            _path=path,
        )


def parse_deploy_config(path: Path) -> DeployConfig:
    """Parse deploy.toml and return a typed DeployConfig.

    Args:
        path: Path to deploy.toml file.

    Returns:
        DeployConfig instance.

    Raises:
        FileNotFoundError: If file doesn't exist.
        tomllib.TOMLDecodeError: If file is invalid TOML.
        ValueError: If required fields are missing.
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return DeployConfig.from_dict(data, path)
