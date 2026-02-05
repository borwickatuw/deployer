"""Emergency operations module for production incident handling.

This module provides tools for safely handling production emergencies:
- Checkpointing: Save state before changes, enable rollback
- ECS operations: Rollback services, scale quickly
- RDS operations: Create snapshots, restore databases
- ALB operations: Check target health
- Logs: Scan for errors
- Maintenance: Check pending maintenance
- ECR: Check vulnerability findings
- Logging: Audit trail of all emergency actions
"""

from .alb import (
    get_target_health,
    get_unhealthy_targets,
)
from .checkpoint import (
    Checkpoint,
    cleanup_old_checkpoints,
    create_checkpoint,
    get_checkpoint_dir,
    list_checkpoints,
    load_checkpoint,
)
from .ecr import (
    get_image_scan_findings,
    get_repository_scan_summary,
    list_repositories_for_environment,
)
from .ecs import (
    compare_task_definitions,
    force_new_deployment,
    get_all_services_state,
    get_service_state,
    get_task_definition_details,
    list_task_definition_revisions,
    rollback_service,
    scale_service,
    update_service_task_definition,
    wait_for_deployment,
)
from .logging import EmergencyLogger
from .logs import (
    get_log_groups_for_environment,
    scan_logs_for_errors,
)
from .maintenance import (
    get_all_pending_maintenance,
    get_elasticache_pending_maintenance,
    get_rds_pending_maintenance,
)
from .rds import (
    create_emergency_snapshot,
    get_rds_snapshots,
    restore_from_point_in_time,
    restore_from_snapshot,
)

__all__ = [
    # ALB
    "get_target_health",
    "get_unhealthy_targets",
    # Checkpoint
    "Checkpoint",
    "cleanup_old_checkpoints",
    "create_checkpoint",
    "get_checkpoint_dir",
    "list_checkpoints",
    "load_checkpoint",
    # ECR
    "get_image_scan_findings",
    "get_repository_scan_summary",
    "list_repositories_for_environment",
    # ECS
    "compare_task_definitions",
    "force_new_deployment",
    "get_all_services_state",
    "get_service_state",
    "get_task_definition_details",
    "list_task_definition_revisions",
    "rollback_service",
    "scale_service",
    "update_service_task_definition",
    "wait_for_deployment",
    # Logs
    "get_log_groups_for_environment",
    "scan_logs_for_errors",
    # Maintenance
    "get_all_pending_maintenance",
    "get_elasticache_pending_maintenance",
    "get_rds_pending_maintenance",
    # RDS
    "create_emergency_snapshot",
    "get_rds_snapshots",
    "restore_from_point_in_time",
    "restore_from_snapshot",
    # Logging
    "EmergencyLogger",
]
