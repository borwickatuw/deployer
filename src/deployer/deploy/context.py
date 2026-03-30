"""Shared context for ECS deployment operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
