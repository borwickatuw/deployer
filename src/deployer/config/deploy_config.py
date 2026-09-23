"""Deploy.toml configuration dataclasses.

Provides typed configuration classes that replace manual KNOWN_KEYS validation.
Adding a new field only requires updating the relevant dataclass.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dacite

from deployer.modules import ModuleRegistry

_DACITE_CONFIG = dacite.Config(cast=[set])

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
}


def merge_build_args(
    image_name: str, build_args: dict[str, Any], environment: str
) -> dict[str, str]:
    """Merge one image's global and per-environment build args.

    Merge order (later values override earlier):
    1. ``build_args = { KEY = "value" }`` -- base args
    2. ``build_args.{environment} = { KEY = "value" }`` -- environment-specific

    Values that are themselves tables are read as per-environment blocks rather
    than as build args, so only the block for ``environment`` is merged in.

    Both config shapes route here -- ``ImageConfig.get_build_args`` and the raw
    dict arm in ``deploy/images.py`` -- so the two cannot drift. They used to:
    the dict arm called ``dict.update(<scalar>)`` and died with a message naming
    neither the image nor the key, while this arm silently passed the scalar
    through as a literal ``--build-arg staging=5``.

    Args:
        image_name: Name of the image, for the error message.
        build_args: The image's raw ``build_args`` table.
        environment: The target environment (staging, production).

    Returns:
        Merged build arguments dictionary.

    Raises:
        RuntimeError: If ``build_args.<environment>`` is present but is not a
            table. A build arg named after an environment cannot be expressed;
            that is accepted.
    """
    merged = {k: v for k, v in build_args.items() if not isinstance(v, dict)}

    env_override = build_args.get(environment)
    if env_override is None:
        return merged
    if not isinstance(env_override, dict):
        raise RuntimeError(
            f"Image '{image_name}': build_args.{environment} must be a table of "
            f"build args, got {type(env_override).__name__}. "
            f"Did you mean [images.{image_name}.build_args.{environment}]?"
        )

    merged.update(env_override)
    return merged


@dataclass
class ImageConfig:
    """Configuration for a Docker image build."""

    name: str
    context: str
    dockerfile: str = "Dockerfile"
    push: bool = True
    depends_on: list[str] = field(default_factory=list)
    target: str | dict[str, str] | None = field(default=None, repr=False)
    build_args: dict[str, Any] = field(default_factory=dict, repr=False)
    additional_contexts: dict[str, str] = field(default_factory=dict, repr=False)

    _KNOWN_KEYS = {
        "context",
        "dockerfile",
        "target",
        "push",
        "depends_on",
        "build_args",
        "additional_contexts",
    }

    def get_target(self, environment: str) -> str | None:
        """Get the Docker build target for this image.

        Supports environment-specific override via target.{environment}.

        Args:
            environment: The target environment (staging, production).

        Returns:
            Target name or None if not specified.
        """
        if isinstance(self.target, dict):
            return self.target.get(environment)
        return self.target

    def get_build_args(self, environment: str) -> dict[str, str]:
        """Get merged build arguments for this image.

        Args:
            environment: The target environment (staging, production).

        Returns:
            Merged build arguments dictionary.

        Raises:
            RuntimeError: If ``build_args.<environment>`` is not a table.
        """
        return merge_build_args(self.name, self.build_args, environment)


# ECS's valid ranges for a container healthCheck (API_HealthCheck), inclusive.
# retries is a count; the rest are seconds.
_HEALTH_CHECK_RANGES = {
    "interval": (5, 300),
    "timeout": (2, 60),
    "retries": (1, 10),
    "start_period": (0, 300),
}
# deploy.toml key -> ECS healthCheck key.
_HEALTH_CHECK_ECS_KEYS = {
    "command": "command",
    "interval": "interval",
    "timeout": "timeout",
    "retries": "retries",
    "start_period": "startPeriod",
}


def container_health_check(service_name: str, block: dict[str, Any]) -> dict[str, Any]:
    """Validate a ``[services.X.container_health_check]`` block; return ECS's ``healthCheck``.

    ``command`` is required and takes ECS's own list form: ``["CMD", arg, ...]``
    runs the arguments directly, ``["CMD-SHELL", "one shell string"]`` runs one
    string in the container's default shell. Every other key is optional and,
    when omitted, is left to ECS's default (interval 30, timeout 5, retries 3,
    no start period). Keys are deploy.toml's snake_case; ``start_period``
    becomes ECS's ``startPeriod``.

    Called at parse time, so a bad block stops the deploy before anything
    runs, and again when the task definition is built, to render it.

    Args:
        service_name: The service, for error messages.
        block: The block as TOML produced it.

    Returns:
        The ECS ``healthCheck`` dict.

    Raises:
        ValueError: On an unknown key, a missing or malformed ``command``, or a
            value that is not a whole number within ECS's range.
    """
    where = f"[services.{service_name}.container_health_check]"

    unknown = sorted(set(block) - set(_HEALTH_CHECK_ECS_KEYS))
    if unknown:
        raise ValueError(
            f"{where} has unknown key(s): {', '.join(unknown)}. "
            f"Allowed: {', '.join(_HEALTH_CHECK_ECS_KEYS)}."
        )

    if "command" not in block:
        raise ValueError(f"{where} requires 'command'")
    command = block["command"]
    if not isinstance(command, list):
        raise ValueError(
            f"{where} command must be a list, e.g. "
            '["CMD-SHELL", "test -f /tmp/alive"] or ["CMD", "/app/healthcheck"]'
        )
    if not command or command[0] not in ("CMD", "CMD-SHELL"):
        raise ValueError(f"{where} command must start with CMD or CMD-SHELL")
    args = command[1:]
    if not all(isinstance(arg, str) and arg for arg in args):
        raise ValueError(f"{where} command arguments must be non-empty strings")
    if command[0] == "CMD" and not args:
        raise ValueError(f"{where} command CMD needs at least one argument")
    if command[0] == "CMD-SHELL" and len(args) != 1:
        raise ValueError(f"{where} command CMD-SHELL takes exactly one shell string")

    for key, (low, high) in _HEALTH_CHECK_RANGES.items():
        if key not in block:
            continue
        value = block[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where} {key} must be a whole number, got {value!r}")
        if not low <= value <= high:
            raise ValueError(f"{where} {key} must be between {low} and {high}, got {value}")

    return {_HEALTH_CHECK_ECS_KEYS[key]: value for key, value in block.items()}


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
    # Replica floor (None = 1). Declare 0 only for pull-based queue workers
    # that are safe at zero; required before an environment may autoscale
    # the service to nothing.
    min_replicas: int | None = None
    interruptible: bool = False
    # Per-service override of the environment's [deployment] rollout strategy.
    # Same key names as config.toml's [deployment] section; None inherits the
    # environment value per key.
    minimum_healthy_percent: int | None = None
    maximum_percent: int | None = None
    # ECS container healthCheck; see container_health_check() for the shape.
    container_health_check: dict[str, Any] | None = None
    environment: dict[str, Any] = field(default_factory=dict, repr=False)

    _KNOWN_KEYS = {
        "image",
        "port",
        "command",
        "health_check_path",
        "path_pattern",
        "environment",
        "min_cpu",
        "min_memory",
        "min_replicas",
        "interruptible",
        "minimum_healthy_percent",
        "maximum_percent",
        "container_health_check",
    }

    def validate_deployment_override(self) -> None:
        """Fail fast on a rollout override ECS would only reject mid-deploy.

        ECS refuses ``minimumHealthyPercent == maximumPercent == 100`` (no
        room to make progress), but only when ``update_service`` is called --
        by which point ``_update_service`` swallows the ClientError into a
        vague per-service failure. Rejecting at parse time names the service
        and the rule instead.

        Raises:
            ValueError: If a set value is out of range, or the pair is 100/100.
        """
        if self.minimum_healthy_percent is not None and not (
            0 <= self.minimum_healthy_percent <= 100
        ):
            raise ValueError(
                f"[services.{self.name}] minimum_healthy_percent must be "
                f"between 0 and 100, got {self.minimum_healthy_percent}"
            )
        if self.maximum_percent is not None and self.maximum_percent < 100:
            raise ValueError(
                f"[services.{self.name}] maximum_percent must be at least "
                f"100, got {self.maximum_percent}"
            )
        if self.minimum_healthy_percent == 100 and self.maximum_percent == 100:
            raise ValueError(
                f"[services.{self.name}] minimum_healthy_percent and "
                f"maximum_percent cannot both be 100: ECS would have no "
                f"capacity headroom to replace tasks"
            )


@dataclass
class MigrationConfig:
    """Configuration for database migrations."""

    enabled: bool = False
    service: str = "web"
    command: list[str] = field(default_factory=list)

    _KNOWN_KEYS = {"enabled", "service", "command"}


@dataclass
class AuditConfig:
    """Configuration for deploy.toml audit."""

    ignore_services: set[str] = field(default_factory=set)
    service_mapping: dict[str, str] = field(default_factory=dict)
    ignore_env_vars: set[str] = field(default_factory=set)
    ignore_images: set[str] = field(default_factory=set)

    _KNOWN_KEYS = {"ignore_services", "service_mapping", "ignore_env_vars", "ignore_images"}


@dataclass
class ApplicationConfig:
    """Configuration for the application metadata."""

    name: str
    source: str = "."
    description: str | None = None

    _KNOWN_KEYS = {"name", "source", "description"}


# Known keys for application configuration (for validation)


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
    _warnings: list[str] = field(default_factory=list, repr=False)
    _path: Path | None = field(default=None, repr=False)

    def declared_env_var_names(self) -> set[str]:
        """The environment variable names deploy.toml writes out by hand.

        Split out of ``get_all_env_var_names`` for preflight's
        ``check_environment_secrets_overlap``, which asks whether a
        ``[secrets] names`` entry is *also* declared as an environment
        variable. It cannot use ``get_all_env_var_names``: that one unions the
        module-injected names on top, which is where ``[secrets] names``
        themselves arrive from, so every declared secret would collide with
        itself. One traversal, two callers -- the alternative is a second copy
        of this walk, and the comment below records how the last duplicated
        piece of this knowledge went.

        Includes:
        - Explicit environment variables from [environment] section
        - Environment-specific overrides (e.g., [environment.staging])
        - Service-specific environment variables (e.g., [services.X.environment])

        Returns:
            Set of environment variable names.
        """
        env_vars: set[str] = set()

        # Base environment
        env_vars.update(self._environment.keys())

        # Environment overrides (staging, production, etc.)
        for _key, value in self._environment.items():
            if isinstance(value, dict):
                env_vars.update(value.keys())

        # Service-specific environment variables ([services.X.environment])
        for service in self.services.values():
            # Add base service env vars
            for key, value in service.environment.items():
                if not isinstance(value, dict):
                    env_vars.add(key)
                else:
                    # Environment-specific overrides within service
                    env_vars.update(value.keys())

        return env_vars

    def get_all_env_var_names(self) -> set[str]:
        """Every environment variable name the container will see.

        Includes everything ``declared_env_var_names`` returns, plus the
        variables modules inject based on declared resources -- which is where
        [secrets] names arrive from.

        Returns:
            Set of environment variable names.
        """
        # Module-injected variables -- each module answers for itself. This
        # used to be a fourth hand-maintained copy of module knowledge, and it
        # had already gone wrong: it claimed S3_{NAME}_BUCKET_REGION, which
        # StorageModule has never injected, so the audit reported that variable
        # as satisfied by nothing.
        return self.declared_env_var_names() | ModuleRegistry.injected_names(self.get_raw_dict())

    def get_warnings(self) -> list[str]:
        """Get configuration warnings (unknown keys, etc.).

        Returns:
            List of warning messages.
        """
        return list(self._warnings)

    def get_raw_dict(  # noqa: C901 — dict serialization with multiple sections
        self,
    ) -> dict[str, Any]:
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

        for name, img in self.images.items():
            img_dict: dict[str, Any] = {
                "context": img.context,
                "dockerfile": img.dockerfile,
                "push": img.push,
                "depends_on": img.depends_on,
            }
            if img.target is not None:
                img_dict["target"] = img.target
            if img.build_args:
                img_dict["build_args"] = img.build_args
            if img.additional_contexts:
                img_dict["additional_contexts"] = img.additional_contexts
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
            if svc.min_replicas is not None:
                svc_dict["min_replicas"] = svc.min_replicas
            if svc.interruptible:
                svc_dict["interruptible"] = svc.interruptible
            # service.py reads these off the raw dict, not ServiceConfig; a
            # field dropped here silently never reaches ECS.
            if svc.minimum_healthy_percent is not None:
                svc_dict["minimum_healthy_percent"] = svc.minimum_healthy_percent
            if svc.maximum_percent is not None:
                svc_dict["maximum_percent"] = svc.maximum_percent
            if svc.container_health_check is not None:
                svc_dict["container_health_check"] = svc.container_health_check
            if svc.environment:
                svc_dict["environment"] = svc.environment
            result["services"][name] = svc_dict

        if self.database:
            result["database"] = self.database
        if self.cache:
            result["cache"] = self.cache
        if self.storage:
            result["storage"] = self.storage

        return result

    @classmethod
    def from_dict(  # noqa: C901 — TOML parsing with validation
        cls, data: dict[str, Any], path: Path | None = None
    ) -> "DeployConfig":
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
            if key not in ApplicationConfig._KNOWN_KEYS:
                warnings.append(f"Unknown key in [application]: {key}")

        if not app_data.get("name"):
            raise ValueError("[application] section requires 'name' field")
        application = dacite.from_dict(ApplicationConfig, app_data, config=_DACITE_CONFIG)

        # Parse [images] section
        images: dict[str, ImageConfig] = {}
        images_data = data.get("images", {})
        for image_name, image_config in images_data.items():
            if isinstance(image_config, dict):
                # Validate image keys
                for key in image_config:
                    if key not in ImageConfig._KNOWN_KEYS:
                        warnings.append(f"Unknown key in [images.{image_name}]: {key}")
                images[image_name] = dacite.from_dict(
                    ImageConfig, {**image_config, "name": image_name}, config=_DACITE_CONFIG
                )

        # Parse [services] section
        services: dict[str, ServiceConfig] = {}
        services_data = data.get("services", {})
        for service_name, service_config in services_data.items():
            if isinstance(service_config, dict):
                # Validate service keys
                for key in service_config:
                    if key not in ServiceConfig._KNOWN_KEYS:
                        warnings.append(f"Unknown key in [services.{service_name}]: {key}")
                services[service_name] = dacite.from_dict(
                    ServiceConfig, {**service_config, "name": service_name}, config=_DACITE_CONFIG
                )
                services[service_name].validate_deployment_override()
                health_check = services[service_name].container_health_check
                if health_check is not None:
                    container_health_check(service_name, health_check)

        # Parse [migrations] section
        migrations_data = data.get("migrations", {})
        for key in migrations_data:
            if key not in MigrationConfig._KNOWN_KEYS:
                warnings.append(f"Unknown key in [migrations]: {key}")
        migrations = dacite.from_dict(MigrationConfig, migrations_data, config=_DACITE_CONFIG)

        # Parse [audit] section
        audit_data = data.get("audit", {})
        for key in audit_data:
            if key not in AuditConfig._KNOWN_KEYS:
                warnings.append(f"Unknown key in [audit]: {key}")
        audit = dacite.from_dict(AuditConfig, audit_data, config=_DACITE_CONFIG)

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
