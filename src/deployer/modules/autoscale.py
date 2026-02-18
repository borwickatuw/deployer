"""Autoscale module for ECS queue-depth autoscaling.

Application declares:
    [autoscale]
    services = ["transcoder"]

Environment provides:
    [autoscale]
    enabled = true
    namespace = "havoc-production"

Injects: AUTOSCALE_NAMESPACE, AUTOSCALE_SERVICES
"""

from typing import Any

from .base import (
    EnvironmentVariable,
    ModuleContext,
    ModuleOutput,
    ResourceModule,
)


class AutoscaleModule(ResourceModule):
    """ECS queue-depth autoscaling module."""

    @property
    def name(self) -> str:
        return "autoscale"

    def validate(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate autoscale configuration."""
        errors = []

        if not app_config:
            return []  # Autoscale not declared - not an error

        # App must declare services as a list
        services = app_config.get("services")
        if services is None:
            errors.append("[autoscale] section missing 'services' in deploy.toml")
            return errors

        if not isinstance(services, list):
            errors.append("[autoscale] 'services' must be a list in deploy.toml")
            return errors

        # Env config must exist when app declares autoscale
        if not env_config:
            errors.append("[autoscale] declared in deploy.toml but missing from config.toml")
            return errors

        # enabled must be explicitly true or false
        enabled = env_config.get("enabled")
        if enabled is None:
            errors.append("[autoscale] 'enabled' must be explicitly true or false in config.toml")
            return errors

        if not isinstance(enabled, bool):
            errors.append("[autoscale] 'enabled' must be explicitly true or false in config.toml")
            return errors

        # If enabled, namespace is required
        if enabled and not env_config.get("namespace"):
            errors.append("[autoscale] 'namespace' required in config.toml when enabled = true")

        return errors

    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
    ) -> ModuleOutput:
        """Collect autoscale environment variables."""
        if not app_config:
            return ModuleOutput()

        if not env_config or not env_config.get("enabled"):
            return ModuleOutput()

        services = app_config.get("services", [])

        return ModuleOutput(
            environment=[
                EnvironmentVariable("AUTOSCALE_NAMESPACE", env_config["namespace"]),
                EnvironmentVariable("AUTOSCALE_SERVICES", ",".join(services)),
            ]
        )
