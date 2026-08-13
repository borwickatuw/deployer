"""Shared context for ECS deployment operations."""

from __future__ import annotations

from dataclasses import dataclass
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
    """The three flags that control how a deploy behaves.

    They arrive together from ``common_deploy_options`` and stay together all
    the way into Deployer.
    """

    dry_run: bool = False
    force: bool = False
    force_build: bool = False


@dataclass(frozen=True)
class DeploymentContext:
    """Bundles shared parameters passed to ECS deployment functions.

    This replaces the 10+ individual parameters that were threaded through
    register_task_definition, deploy_services, start_migrations, etc.
    """

    ecs_client: Any
    cluster_name: str
    config: dict
    service_config: dict
    infra_config: dict
    app_name: str
    environment: str
    region: str
    account_id: str
    env_config: dict
    dry_run: bool = False


@dataclass(frozen=True)
class StabilityConfig:
    """Polling configuration for ECS service stability checks."""

    poll_interval: int = 15
    max_attempts: int = 40
    failure_threshold: int = 3
