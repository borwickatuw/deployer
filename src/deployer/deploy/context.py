"""Shared context for ECS deployment operations."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EnvironmentTarget:
    """Where a deployment is going.

    The name, the type and the resolved config travelled together as three
    positional parameters through the preflight checks and the deploy pipeline.
    """

    name: str  # e.g. "myapp-staging"
    type: str  # e.g. "staging"
    config: dict  # resolved config.toml


@dataclass(frozen=True)
class DeployOptions:
    """The flags that control how a deploy behaves.

    They arrive together from ``common_deploy_options`` and stay together all
    the way into Deployer.

    ``force_deploy`` exists because secret rotation and deploy-to-restart both
    produce a state hash identical to the stored one, so the skip-unchanged
    check would turn those deploys into no-ops (``--force-build`` cannot help:
    image tags are content-addressed).
    """

    dry_run: bool = False
    force: bool = False
    force_build: bool = False
    force_deploy: bool = False


@dataclass(frozen=True)
class InfraConfig:
    """The ECS infrastructure settings a deploy runs against.

    Assembled by ``deployer._build_infra_config`` from the resolved
    environment ``config.toml`` and read by the task-definition and service
    layers.

    Ten fields are read by name. The other eight -- ``database_url``,
    ``db_host``, ``db_port``, ``db_name``, ``db_password_secret_arn``,
    ``db_username_secret_arn``, ``redis_url`` and ``s3_media_bucket`` -- are
    never read by name anywhere. They exist because their **names** are a
    user-facing contract: an application's own ``deploy.toml`` writes
    ``${database_url}`` and ``secretsmanager:${db_password_secret_arn}``, and
    ``legacy_placeholders()`` is what turns those names into a substitution
    table. Renaming one is a breaking change for every downstream app.
    """

    execution_role_arn: str | None = None
    task_role_arn: str | None = None
    security_group_id: str | None = None
    subnet_ids: list[str] = field(default_factory=list)
    target_group_arn: str | None = None
    service_target_groups: dict[str, str] = field(default_factory=dict)
    service_discovery_registries: dict[str, str] = field(default_factory=dict)
    # Database config - supports both URL (legacy) and component-based (Secrets Manager)
    database_url: str | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_name: str | None = None
    db_password_secret_arn: str | None = None
    db_username_secret_arn: str | None = None
    redis_url: str | None = None
    s3_media_bucket: str | None = None
    rds_instance_id: str | None = None
    scheduler: dict = field(default_factory=dict)
    deployment_config: dict = field(default_factory=dict)
    health_check_config: dict = field(default_factory=dict)

    def legacy_placeholders(self) -> dict[str, str]:
        """Return the ``${name}`` substitution table for legacy placeholders.

        Every scalar field is offered under its own name. The list-, dict- and
        ``None``-valued fields are not placeholder material and are dropped,
        so a ``${subnet_ids}`` or ``${scheduler}`` reference is reported as
        unresolved by ``_resolve_legacy_placeholders``.

        ``bool`` is excluded explicitly: it subclasses ``int`` and used to take
        the numeric arm, rendering Python-style as ``"True"`` (Phase 69). No
        field here is a bool, so one is a malformed config.toml value, and a
        reference to it is reported as unresolved instead.
        """
        return {
            k: str(v)
            for k, v in asdict(self).items()
            if isinstance(v, (str, int, float)) and not isinstance(v, bool)
        }


@dataclass(frozen=True)
class DeploymentContext:
    """Bundles shared parameters passed to ECS deployment functions.

    This replaces the 10+ individual parameters that were threaded through
    _register_task_definition, deploy_services, start_migrations, etc.
    """

    ecs_client: Any
    cluster_name: str
    config: dict
    service_config: dict
    infra_config: InfraConfig
    app_name: str
    environment: str
    region: str
    account_id: str
    env_config: dict
    dry_run: bool = False
    # The environment's queue-depth scaling map ({service: {min, max, steps}}).
    # Read by the autoscaling apply step and by get_environment_variables,
    # which injects AUTOSCALE_NAMESPACE/AUTOSCALE_SERVICES when it is set.
    scaling_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StabilityConfig:
    """Polling configuration for ECS service stability checks.

    ``settle_seconds`` is deliberately independent of ``poll_interval``: the
    settle window only detects a crash loop if the confirming observation is
    far enough from the first to catch a short-lived task flapping (or its
    failedTasks climbing). Faster polling must not shrink that distance.
    """

    poll_interval: int = 15
    max_attempts: int = 40
    failure_threshold: int = 3
    settle_seconds: int = 15

    @property
    def settle_polls(self) -> int:
        """Confirming polls required after the first qualifying poll."""
        return max(1, math.ceil(self.settle_seconds / self.poll_interval))
