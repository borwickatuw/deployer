"""Resource module system for declarative infrastructure abstraction.

This module provides the infrastructure for separating what an application
declares it needs (in deploy.toml) from how an environment provides it
(in config.toml).

Example usage:
    from deployer.modules import ModuleRegistry, ModuleContext

    # Create context
    context = ModuleContext(
        region="us-west-2",
        account_id="123456789",
        environment="staging",
        app_name="myapp",
        domain_name="myapp-staging.example.com",
        services=app_config.get("services", {}),
    )

    # Collect from all modules
    output = ModuleRegistry.collect_all(app_config, env_config, context)

    # Use the output
    for env_var in output.environment:
        print(f"{env_var.name}={env_var.value}")
    for secret in output.secrets:
        print(f"{secret.name} from {secret.value_from}")
"""

import re
from typing import Any

from .base import (
    EnvironmentVariable,
    ModuleContext,
    ModuleOutput,
    ResourceModule,
    SecretReference,
)
from .cache import CacheModule
from .database import DatabaseModule
from .secrets import SecretsModule
from .storage import StorageModule


class ModuleRegistry:
    """Registry of all available resource modules."""

    # Module instances
    _modules: list[ResourceModule] = [
        DatabaseModule(),
        CacheModule(),
        StorageModule(),
        SecretsModule(),
    ]

    @classmethod
    def validate_all(
        cls,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate all modules that are declared in app_config.

        Args:
            app_config: The application's deploy.toml.
            env_config: The environment's config.toml.

        Returns:
            List of all validation errors across all modules.
        """
        errors = []
        for module in cls._modules:
            module_app_config = app_config.get(module.name, {})
            module_env_config = env_config.get(module.name, {})

            # Only validate if the app declares this module
            if module_app_config:
                module_errors = module.validate(module_app_config, module_env_config)
                errors.extend(module_errors)

        return errors

    @classmethod
    def collect_all(
        cls,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
        credential_mode: str = "app",
    ) -> ModuleOutput:
        """Collect environment variables and secrets from all declared modules.

        Args:
            app_config: The application's deploy.toml.
            env_config: The environment's config.toml.
            context: Deployment context.
            credential_mode: For database module - "app" for runtime services,
                "migrate" for migrations. Default is "app".

        Returns:
            Combined ModuleOutput from all modules.
        """
        output = ModuleOutput()

        for module in cls._modules:
            module_app_config = app_config.get(module.name, {})
            module_env_config = env_config.get(module.name, {})

            # Only collect if the app declares this module
            if module_app_config:
                # Pass credential_mode to database module
                if module.name == "database":
                    module_output = module.collect(
                        module_app_config,
                        module_env_config,
                        context,
                        credential_mode=credential_mode,
                    )
                else:
                    module_output = module.collect(module_app_config, module_env_config, context)
                output = output.merge(module_output)

        return output


# Service URL reference pattern: ${services.name.url}
SERVICE_URL_PATTERN = re.compile(r"\$\{services\.([^.]+)\.url\}")

# Internal service URL reference pattern: ${services.name.internal_url}
INTERNAL_SERVICE_URL_PATTERN = re.compile(r"\$\{services\.([^.]+)\.internal_url\}")


def resolve_service_url(
    service_name: str,
    services_config: dict[str, Any],
    domain_name: str | None,
) -> str | None:
    """Resolve a service URL from its path_pattern and domain.

    For a service with path_pattern = "/api/*", the URL would be:
    https://myapp-staging.example.com/api

    Args:
        service_name: Name of the service (e.g., "api").
        services_config: The [services] section from deploy.toml.
        domain_name: The domain name from config.toml.

    Returns:
        Full URL string, or None if service doesn't have a path_pattern.
    """
    if not domain_name:
        return None

    service_config = services_config.get(service_name, {})
    path_pattern = service_config.get("path_pattern")

    if not path_pattern:
        return None

    # Convert path pattern to URL path: "/api/*" -> "/api"
    # Remove trailing /* or *
    path = path_pattern.rstrip("*").rstrip("/")

    return f"https://{domain_name}{path}"


def resolve_internal_service_url(
    service_name: str,
    services_config: dict[str, Any],
    service_discovery_namespace: str | None,
) -> str | None:
    """Resolve an internal service URL using service discovery.

    For a service with port = 8000, the URL would be:
    http://web.myapp-staging.local:8000

    This is used for internal service-to-service communication that bypasses
    the ALB (and thus Cognito authentication).

    Args:
        service_name: Name of the service (e.g., "web").
        services_config: The [services] section from deploy.toml.
        service_discovery_namespace: The Cloud Map namespace (e.g., "myapp-staging.local").

    Returns:
        Full internal URL string, or None if service discovery not configured.
    """
    if not service_discovery_namespace:
        return None

    service_config = services_config.get(service_name, {})
    port = service_config.get("port")

    if not port:
        return None

    return f"http://{service_name}.{service_discovery_namespace}:{port}"


def resolve_service_urls(
    env_vars: dict[str, str],
    services_config: dict[str, Any],
    domain_name: str | None,
    service_discovery_namespace: str | None,
) -> dict[str, str]:
    """Resolve ${services.X.url} and ${services.X.internal_url} references.

    Args:
        env_vars: Dictionary of environment variable name -> value.
        services_config: The [services] section from deploy.toml.
        domain_name: The domain name from config.toml.
        service_discovery_namespace: Cloud Map namespace for internal URLs.

    Returns:
        env_vars with service URL references resolved.
    """
    resolved = {}

    for key, value in env_vars.items():
        if isinstance(value, str):
            # Check for internal service URL references first
            internal_match = INTERNAL_SERVICE_URL_PATTERN.search(value)
            if internal_match:
                service_name = internal_match.group(1)
                url = resolve_internal_service_url(
                    service_name, services_config, service_discovery_namespace
                )
                if url:
                    value = INTERNAL_SERVICE_URL_PATTERN.sub(url, value)  # noqa: PLW2901
                # Leave unresolved if service discovery not configured

            # Check for external service URL references
            match = SERVICE_URL_PATTERN.search(value)
            if match:
                service_name = match.group(1)
                url = resolve_service_url(service_name, services_config, domain_name)
                if url:
                    # Replace the reference with the URL
                    value = SERVICE_URL_PATTERN.sub(url, value)  # noqa: PLW2901
                else:
                    # Leave unresolved if service doesn't have path_pattern
                    pass
        resolved[key] = value

    return resolved


# Re-export commonly used types
__all__ = [
    "EnvironmentVariable",
    "ModuleContext",
    "ModuleOutput",
    "ModuleRegistry",
    "ResourceModule",
    "SecretReference",
    "resolve_service_urls",
    "resolve_service_url",
    "resolve_internal_service_url",
]
