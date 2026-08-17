"""Base class and types for resource modules.

Resource modules provide a clean abstraction between what an application
declares it needs (in deploy.toml) and how an environment provides it
(in config.toml).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

#: The two database credential modes. "app" is DML-only and used by runtime
#: services; "migrate" is DDL+DML and used only by migrations.
CREDENTIAL_MODES = ("app", "migrate")


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

    def _validate_common(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        supported_types: list[str],
    ) -> tuple[list[str], bool]:
        """Validate common module config: app declared, type valid, env present.

        Args:
            app_config: The application's deploy.toml [module_name] section.
            env_config: The environment's config.toml [module_name] section.
            supported_types: List of valid type values (e.g., ["redis"]).

        Returns:
            Tuple of (errors, should_continue). If should_continue is False,
            the caller should return errors immediately.
        """
        if not app_config:
            return [], False  # Module not declared - not an error

        module_type = app_config.get("type")
        if not module_type:
            return [f"[{self.name}] section missing 'type' in deploy.toml"], False

        if module_type not in supported_types:
            types_str = "', '".join(supported_types)
            return [f"[{self.name}] type '{module_type}' not supported (only '{types_str}')"], False

        if not env_config:
            return [f"[{self.name}] section missing from config.toml"], False

        return [], True

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

    @abstractmethod
    def injected_names(self, app_config: dict[str, Any]) -> set[str]:
        """The names ``collect()`` will inject, answerable from deploy.toml alone.

        The audit compares docker-compose.yml against deploy.toml, before any
        environment has been chosen, so it cannot call ``collect()`` -- it has
        no ``config.toml`` and no ``ModuleContext``. It needs the *names*
        anyway, to tell a variable the application forgot to declare from one a
        module will supply.

        Abstract rather than defaulting to the empty set: a module that grows a
        new injected variable and does not say so here makes the audit report
        that variable as unprovided, and a module that stops injecting one
        makes the audit call it satisfied by nothing. 53h-2b found the second
        of those already shipped -- ``DeployConfig`` claimed
        ``S3_{NAME}_BUCKET_REGION``, which ``StorageModule`` has never injected.

        Secrets count. From the container's point of view a secret *is* an
        environment variable, and what the audit is asking is what the
        container will see.

        Args:
            app_config: The application's deploy.toml [module_name] section.

        Returns:
            Environment variable names, empty if the section declares nothing
            this module acts on.
        """
        pass


@dataclass
class ModuleContext:
    """What a deployment tells its modules: where it is, and which credentials.

    ``region`` and ``account_id`` are read for one purpose -- naming an SSM
    parameter -- so ``ssm_parameter_arn`` is here rather than repeated in every
    module that needs one. Four other fields (``environment``, ``app_name``,
    ``domain_name`` and ``services``) were carried here and never read by any
    module; Phase 53h-1 deleted them. ``domain_name`` and ``services`` are read
    by ``resolve_service_urls``, but ``task_definition`` calls that with the
    config values directly and never routed them through this object.

    ``credential_mode`` used to widen ``DatabaseModule.collect``'s signature
    past the ABC's, which forced ``collect_all`` to dispatch on
    ``module.name == "database"``. It belongs to the deployment, not to one
    module's parameter list, so it lives here and the registry calls one
    signature for everything.
    """

    region: str
    account_id: str
    #: "app" for runtime services (DML only), "migrate" for migrations (DDL+DML).
    credential_mode: str = "app"

    def __post_init__(self) -> None:
        if self.credential_mode not in CREDENTIAL_MODES:
            raise ValueError(
                f"credential_mode must be 'app' or 'migrate', got '{self.credential_mode}'"
            )

    def ssm_parameter_arn(self, path: str) -> str:
        """Return the ARN of an SSM parameter path in this deployment's account.

        The path is concatenated verbatim -- a path without a leading ``/``
        produces a malformed ARN, the same as it did when each caller built
        this string itself.
        """
        return f"arn:aws:ssm:{self.region}:{self.account_id}:parameter{path}"
