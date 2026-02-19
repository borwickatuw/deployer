"""Base class and types for resource modules.

Resource modules provide a clean abstraction between what an application
declares it needs (in deploy.toml) and how an environment provides it
(in config.toml).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EnvironmentVariable:
    """An environment variable to inject into a container."""

    name: str
    value: str


@dataclass
class SecretReference:
    """A reference to a secret (SSM or Secrets Manager)."""

    name: str
    value_from: str  # SSM path or Secrets Manager ARN


@dataclass
class ModuleOutput:
    """Output from a module's collect method."""

    environment: list[EnvironmentVariable] = field(default_factory=list)
    secrets: list[SecretReference] = field(default_factory=list)

    def merge(self, other: "ModuleOutput") -> "ModuleOutput":
        """Merge another ModuleOutput into this one."""
        return ModuleOutput(
            environment=self.environment + other.environment,
            secrets=self.secrets + other.secrets,
        )


class ResourceModule(ABC):
    """Base class for resource modules.

    A resource module bridges the gap between application declarations
    (what the app needs) and environment configuration (how to provide it).

    Subclasses must implement:
    - name: The module name (e.g., "database", "cache")
    - validate(): Check that required config is present
    - collect(): Return environment variables and secrets to inject
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Module name, used for error messages and config section names."""
        pass

    @abstractmethod
    def validate(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate that required configuration is present.

        Args:
            app_config: The application's deploy.toml [module_name] section.
            env_config: The environment's config.toml [module_name] section.

        Returns:
            List of error messages. Empty list means validation passed.
        """
        pass

    @abstractmethod
    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: "ModuleContext",
    ) -> ModuleOutput:
        """Collect environment variables and secrets to inject.

        Args:
            app_config: The application's deploy.toml [module_name] section.
            env_config: The environment's config.toml [module_name] section.
            context: Deployment context (region, account_id, etc.).

        Returns:
            ModuleOutput with environment variables and secrets.
        """
        pass


@dataclass
class ModuleContext:
    """Context available to all modules during collection."""

    region: str
    account_id: str
    environment: str  # "staging" or "production"
    app_name: str
    domain_name: str | None = None
    # Service information for ${services.X.url} resolution
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
