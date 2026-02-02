"""Deployment module for ECS applications."""

from .images import (
    build_and_push_images,
    ecr_login,
    format_missing_ecr_error,
    get_build_args,
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
from .validation import (
    validate_ecs_cluster,
)

__all__ = [
    # Images
    "build_and_push_images",
    "ecr_login",
    "format_missing_ecr_error",
    "get_build_args",
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
    # Validation
    "validate_ecs_cluster",
]
