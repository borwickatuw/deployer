"""Deployment module for ECS applications."""

from .deployer import Deployer
from .extensions import create_database_extensions
from .images import (
    build_and_push_images,
    ecr_login,
    format_missing_ecr_error,
    validate_ecr_repositories,
)
from .service import (
    DeploymentError,
    MigrationTask,
    ServiceWaitResult,
    create_service,
    deploy_services,
    register_task_definition,
    run_migrations,
    service_exists,
    start_migrations,
    wait_for_migrations,
    wait_for_stable,
)
from .task_definition import (
    build_task_definition,
    get_environment_variables,
    get_secrets,
    get_service_sizing,
)
from .preflight import (
    PreflightError,
    PreflightOptions,
    run_preflight_checks,
)
from .validation import (
    validate_ecs_cluster,
)

__all__ = [
    # Deployer
    "Deployer",
    # Extensions
    "create_database_extensions",
    # Images
    "build_and_push_images",
    "ecr_login",
    "format_missing_ecr_error",
    "validate_ecr_repositories",
    # Task definition
    "build_task_definition",
    "get_environment_variables",
    "get_secrets",
    "get_service_sizing",
    # Service
    "DeploymentError",
    "MigrationTask",
    "ServiceWaitResult",
    "create_service",
    "deploy_services",
    "register_task_definition",
    "run_migrations",
    "service_exists",
    "start_migrations",
    "wait_for_migrations",
    "wait_for_stable",
    # Preflight
    "PreflightError",
    "PreflightOptions",
    "run_preflight_checks",
    # Validation
    "validate_ecs_cluster",
]
