#!/usr/bin/env python3
"""
Emergency operations tool for production incident handling.

These commands MODIFY production state. For read-only monitoring,
use bin/ops.py instead.

Provides safe abstractions for common emergency procedures with automatic
checkpointing and audit logging.

Usage:
    # Roll back to previous task definition
    uv run python bin/emergency.py rollback myapp-production --service web

    # Scale services quickly
    uv run python bin/emergency.py scale myapp-production --service web --count 10

    # Create emergency snapshot
    uv run python bin/emergency.py snapshot myapp-production

    # Restore database (creates new instance, doesn't modify original)
    uv run python bin/emergency.py restore-db myapp-production --snapshot <id>

    # List and restore from checkpoints
    uv run python bin/emergency.py revert myapp-production --list

    # Force new deployment
    uv run python bin/emergency.py force-deploy myapp-production --service web
"""

import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import click

from deployer.aws import rds
from deployer.core.config import (
    get_service_replicas_from_config,
    load_environment_config,
)
from deployer.emergency.checkpoint import (
    RdsState,
    ServiceState,
    cleanup_old_checkpoints,
    create_checkpoint,
    list_checkpoints,
    load_checkpoint,
)
from deployer.emergency.ecs import (
    compare_task_definitions,
    force_new_deployment,
    get_all_services_state,
    get_service_state,
    list_task_definition_revisions,
    scale_service,
    update_service_task_definition,
    wait_for_deployment,
)
from deployer.emergency.rds import (
    create_emergency_snapshot,
    get_rds_instance_details,
    get_rds_snapshots,
    restore_from_point_in_time,
    restore_from_snapshot,
)
from deployer.utils import (
    Colors,
    configure_aws_profile_for_environment,
    confirm_action,
    format_iso,
    get_deployer_root,
    get_environment_path,
    log,
    log_error,
    log_info,
    log_ok,
    log_success,
    log_warning,
    validate_environment_deployed,
)

# =============================================================================
# Inlined from emergency/logging.py — only used by this script.
# =============================================================================


class EmergencyLogger:
    """Append-only logger for emergency operations."""

    def __init__(self, environment: str):
        self.environment = environment
        self.log_path = get_deployer_root() / "local" / "emergency.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, category: str, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{timestamp} [{self.environment}] {category}: {message}\n"
        with open(self.log_path, "a") as f:
            f.write(line)

    def action(self, action_name: str) -> None:
        self._write("ACTION", action_name)

    def checkpoint(self, message: str) -> None:
        self._write("CHECKPOINT", message)

    def ecs(self, message: str) -> None:
        self._write("ECS", message)

    def rds(self, message: str) -> None:
        self._write("RDS", message)

    def success(self, message: str) -> None:
        self._write("SUCCESS", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)


# =============================================================================
# Helpers
# =============================================================================


def _format_utc_timestamp(iso_string: str) -> str:
    """Format an ISO timestamp string to 'YYYY-MM-DD HH:MM UTC'."""
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso_string


def _capture_rds_state(rds_id: str | None) -> RdsState | None:
    """Capture current RDS state for checkpointing."""
    if not rds_id:
        return None
    rds_status = rds.get_status(rds_id)
    if not rds_status:
        return None
    return RdsState(instance_id=rds_id, status=rds_status["status"])


def _snapshot_services(services: dict) -> dict[str, ServiceState]:
    """Snapshot current service state for checkpointing."""
    return {
        name: ServiceState(
            task_definition=state.task_definition,
            desired_count=state.desired_count,
            running_count=state.running_count,
        )
        for name, state in services.items()
    }


def _print_restore_success(logger: EmergencyLogger, result: dict) -> None:
    """Print success message after a database restore."""
    logger.rds(f"Restore initiated: {result['instance_id']}")
    logger.success("Restore initiated")
    print()
    log_success("Restore initiated")
    print()
    print(f"New instance: {result['instance_id']}")
    print()
    print(result["message"])
    print()
    print(f"{Colors.YELLOW}Important:{Colors.NC}")
    print("  - The original database is NOT modified")
    print("  - To use the restored database, update your application's DATABASE_URL")
    print("  - To delete the restored instance if not needed:")
    print(
        f"    aws rds delete-db-instance --db-instance-identifier {result['instance_id']} --skip-final-snapshot"
    )


@dataclass
class EmergencyContext:
    """Shared context loaded at the start of each emergency command."""

    config: dict
    cluster_name: str | None
    rds_id: str | None
    logger: EmergencyLogger


# pysmelly: ignore duplicate-blocks — config loading pattern shared with ops.py
def _load_emergency_context(
    environment: str,
    require_cluster: bool = False,
    require_rds: bool = False,
) -> EmergencyContext:
    """Load environment config and create emergency logger.

    Raises SystemExit(1) if required infrastructure is missing.
    """
    # pysmelly: ignore duplicate-blocks — config loading pattern shared with ops.py
    env_path = get_environment_path(environment)
    config = load_environment_config(env_path)
    cluster_name = config.get("infrastructure", {}).get("cluster_name")
    rds_id = config.get("infrastructure", {}).get("rds_instance_id")

    if require_cluster and not cluster_name:
        log_error("Unable to determine ECS cluster name")
        raise SystemExit(1)

    if require_rds and not rds_id:
        log_error("RDS instance not configured for this environment")
        raise SystemExit(1)

    return EmergencyContext(
        config=config, cluster_name=cluster_name, rds_id=rds_id, logger=EmergencyLogger(environment)
    )


def _load_cluster_services(environment: str) -> tuple[EmergencyContext, dict] | None:
    """Load emergency context and fetch all services in the cluster.

    Returns:
        Tuple of (ctx, services) if successful, None if no services found.
    """
    ctx = _load_emergency_context(environment, require_cluster=True)
    services = get_all_services_state(ctx.cluster_name)
    if not services:
        log_error("No services found in cluster")
        return None
    return ctx, services


def _validate_and_configure(environment: str) -> None:
    """Validate environment and configure AWS. Exits on error."""
    _, error = validate_environment_deployed(environment)
    if error:
        log_error(error)
        sys.exit(1)

    # Show warning banner
    print()
    print(f"{Colors.YELLOW}{'=' * 62}")
    print(f"  EMERGENCY TOOL - This command can modify {environment}")
    print(f"{'=' * 62}{Colors.NC}")

    # Configure AWS profile
    configure_aws_profile_for_environment("infra", environment)
    print()


# =============================================================================
# Rollback Command
# =============================================================================


# pysmelly: ignore param-clumps — distinct CLI params from Click decorators
def cmd_rollback(  # noqa: C901 — rollback with interactive revision selection
    environment: str, service: str | None, revision: int | None, yes: bool
) -> int:
    """Roll back ECS service(s) to previous task definition."""
    # pysmelly: ignore duplicate-blocks — _load_cluster_services unpacking shared across commands
    result = _load_cluster_services(environment)
    if result is None:
        return 1
    ctx, services = result
    cluster_name = ctx.cluster_name
    logger = ctx.logger

    # Determine which service to roll back
    if service:
        if service not in services:
            log_error(f"Service '{service}' not found. Available: {', '.join(services.keys())}")
            return 1
        service_name = service
    else:
        # Interactive mode: list services and prompt
        print()
        print(f"{Colors.BLUE}Available services:{Colors.NC}")
        service_list = sorted(services.keys())
        for i, name in enumerate(service_list, 1):
            state = services[name]
            rev = state.task_definition.split(":")[-1]
            print(f"  {i}. {name} (current revision: {rev})")
        print()

        try:
            choice = input("Select service to roll back (number): ").strip()
            idx = int(choice) - 1
            if idx < 0 or idx >= len(service_list):
                log_error("Invalid selection")
                return 1
            service_name = service_list[idx]
        except (ValueError, EOFError, KeyboardInterrupt):
            print()
            log_error("Cancelled")
            return 1

    # Get current state
    current_state = services[service_name]
    family = current_state.task_definition.split("/")[-1].rsplit(":", 1)[0]

    # Get recent revisions
    revisions = list_task_definition_revisions(family, max_results=10)
    if len(revisions) < 2:
        log_error(f"Not enough revisions to roll back for {service_name}")
        return 1

    # Determine target revision
    if revision:
        target_rev = None
        for rev in revisions:
            if rev["revision"] == revision:
                target_rev = rev
                break
        if not target_rev:
            log_error(f"Revision {revision} not found")
            return 1
    else:
        # Interactive mode
        print()
        print(f"{Colors.BLUE}Recent revisions for {service_name}:{Colors.NC}")
        for i, rev in enumerate(revisions):
            registered = rev.get("registered_at", "unknown")
            if registered and "T" in registered:
                registered = _format_utc_timestamp(registered)
            current = " (current)" if i == 0 else ""
            print(f"  {i}. revision {rev['revision']:>3} - {registered}{current}")
        print()

        try:
            choice = input(
                "Select revision to roll back to (number, default=1 for previous): "
            ).strip()
            idx = 1 if not choice else int(choice)
            if idx < 1 or idx >= len(revisions):
                log_error("Invalid selection (cannot select current revision)")
                return 1
            target_rev = revisions[idx]
        except (ValueError, EOFError, KeyboardInterrupt):
            print()
            log_error("Cancelled")
            return 1

    # Show diff of environment variables
    current_arn = current_state.task_definition
    target_arn = target_rev["arn"]
    diff = compare_task_definitions(current_arn, target_arn)

    if diff:
        print()
        print(f"{Colors.YELLOW}Environment variable changes:{Colors.NC}")
        for _container, changes in diff.items():
            if changes.get("added"):
                for var, val in changes["added"].items():
                    print(f"  + {var}={val}")
            if changes.get("removed"):
                for var, val in changes["removed"].items():
                    print(f"  - {var}={val}")
            if changes.get("changed"):
                for var, vals in changes["changed"].items():
                    print(f"  ~ {var}: {vals['old']} -> {vals['new']}")

    current_rev = current_state.task_definition.split(":")[-1]
    target_revision_num = target_rev["revision"]

    print()
    print(f"{Colors.YELLOW}About to roll back {service_name}:{Colors.NC}")
    print(f"  From: {family}:{current_rev}")
    print(f"  To:   {family}:{target_revision_num}")
    print()
    print("Checkpoint will be saved to: local/checkpoints/")
    print()

    if not confirm_action(skip=yes):
        return 1

    # Create checkpoint
    logger.action("rollback")
    log("Creating checkpoint...")

    checkpoint = create_checkpoint(
        environment=environment,
        action="rollback",
        reason=f"Rolling back {service_name} from revision {current_rev} to {target_revision_num}",
        services=_snapshot_services(services),
        rds=_capture_rds_state(ctx.rds_id),
    )
    logger.checkpoint(f"Created {checkpoint.filename}")
    log_success(f"Checkpoint saved: local/checkpoints/{checkpoint.filename}")

    # Perform rollback
    log(f"Rolling back {service_name} to revision {target_revision_num}...")
    logger.ecs(f"Rolling back {service_name} from revision {current_rev} to {target_revision_num}")

    if not update_service_task_definition(cluster_name, service_name, target_arn):
        logger.error(f"Failed to update service {service_name}")
        log_error("Failed to update service")
        return 1

    logger.ecs("update-service returned: deployment in progress")

    # Wait for deployment
    def progress_callback(running: int, desired: int) -> None:
        print(f"  Waiting for deployment ({running}/{desired} tasks running)...")

    log("Waiting for deployment to complete...")
    if wait_for_deployment(cluster_name, service_name, callback=progress_callback):
        state = get_service_state(cluster_name, service_name)
        running = state.running_count if state else 0
        logger.ecs(f"Rollback complete, running_count={running}")
        logger.success("Rollback completed")
        log_success(f"Rollback complete: {running} tasks running")
    else:
        logger.ecs("Rollback timed out waiting for deployment")
        log_warning("Deployment still in progress (timed out waiting)")

    cleanup_old_checkpoints(environment=environment)

    return 0


# =============================================================================
# Scale Command
# =============================================================================


def cmd_scale(
    environment: str,
    service: str | None,
    count: int | None,
    all_services: bool,
    multiplier: float | None,
    reset: bool,
    yes: bool,
) -> int:
    """Scale ECS services."""
    result = _load_cluster_services(environment)
    if result is None:
        return 1
    ctx, services = result
    cluster_name = ctx.cluster_name
    logger = ctx.logger

    # Determine what to scale
    if reset:
        configured_replicas = get_service_replicas_from_config(ctx.config)
        to_scale = {name: configured_replicas.get(name, 1) for name in services}
    elif service:
        if service not in services:
            log_error(f"Service '{service}' not found")
            return 1
        if count is None:
            log_error("--count is required when using --service")
            return 1
        to_scale = {service: count}
    elif all_services:
        if multiplier:
            to_scale = {
                name: max(1, int(state.desired_count * multiplier))
                for name, state in services.items()
            }
        elif count is not None:
            to_scale = {name: count for name in services}
        else:
            log_error("--multiplier or --count is required with --all")
            return 1
    else:
        log_error("Specify --service, --all, or --reset")
        return 1

    # Show what will change
    print()
    print(f"{Colors.YELLOW}Scaling changes:{Colors.NC}")
    for name, new_count in to_scale.items():
        old_count = services[name].desired_count
        print(f"  {name}: {old_count} -> {new_count}")
    print()

    if not confirm_action(skip=yes):
        return 1

    # Create checkpoint
    logger.action("scale")
    log("Creating checkpoint...")

    checkpoint = create_checkpoint(
        environment=environment,
        action="scale",
        reason=f"Scaling services: {', '.join(to_scale.keys())}",
        services=_snapshot_services(services),
        rds=_capture_rds_state(ctx.rds_id),
    )
    logger.checkpoint(f"Created {checkpoint.filename}")
    log_success(f"Checkpoint saved: local/checkpoints/{checkpoint.filename}")

    # Perform scaling
    for name, new_count in to_scale.items():
        old_count = services[name].desired_count
        log(f"Scaling {name} from {old_count} to {new_count}...")
        logger.ecs(f"Scaling {name} from {old_count} to {new_count}")

        if scale_service(cluster_name, name, new_count):
            log_ok(f"Scaled {name}")
        else:
            logger.error(f"Failed to scale {name}")
            log_error(f"Failed to scale {name}")

    logger.success("Scale completed")
    log_success("Scale operation completed")

    cleanup_old_checkpoints(environment=environment)

    return 0


# =============================================================================
# Snapshot Command
# =============================================================================


def _init_rds_command(environment: str, action: str) -> tuple[str, EmergencyLogger]:
    """Load context for an RDS emergency command."""
    ctx = _load_emergency_context(environment, require_rds=True)
    ctx.logger.action(action)
    return ctx.rds_id, ctx.logger


def cmd_snapshot(environment: str, no_wait: bool) -> int:
    """Create emergency RDS snapshot."""
    rds_id, logger = _init_rds_command(environment, "snapshot")

    log(f"Creating emergency snapshot of {rds_id}...")
    logger.rds(f"Creating snapshot of {rds_id}")

    snapshot_id = create_emergency_snapshot(rds_id, wait=not no_wait)
    if snapshot_id:
        logger.rds(f"Snapshot created: {snapshot_id}")
        logger.success("Snapshot created")
        log_success(f"Snapshot created: {snapshot_id}")
        return 0
    else:
        logger.error("Failed to create snapshot")
        log_error("Failed to create snapshot")
        return 1


# =============================================================================
# Restore-DB Command
# =============================================================================


# pysmelly: ignore inconsistent-error-handling — CLI command, Click handles uncaught exceptions
def cmd_restore_db(  # noqa: C901 — RDS restore with snapshot/PITR paths
    environment: str, snapshot: str | None, time: str | None
) -> int:
    """Restore database from snapshot or point-in-time."""
    rds_id, logger = _init_rds_command(environment, "restore-db")

    if snapshot:
        log(f"Restoring from snapshot: {snapshot}")
        logger.rds(f"Restoring from snapshot {snapshot}")

        result = restore_from_snapshot(rds_id, snapshot)
        if result:
            if result["status"] == "error":
                logger.error(result["message"])
                log_error(result["message"])
                return 1
            else:
                _print_restore_success(logger, result)
                return 0
        else:
            logger.error("Failed to initiate restore")
            log_error("Failed to initiate restore")
            return 1

    elif time:
        try:
            restore_time = datetime.fromisoformat(time.replace("Z", "+00:00"))
        except ValueError:
            log_error(f"Invalid time format: {time}")
            print("  Expected ISO format: 2026-02-04T12:00:00Z")
            return 1

        log(f"Restoring to point in time: {restore_time.isoformat()}")
        logger.rds(f"Restoring to point in time {restore_time.isoformat()}")

        result = restore_from_point_in_time(rds_id, restore_time)
        if result:
            if result["status"] == "error":
                logger.error(result["message"])
                log_error(result["message"])
                return 1
            else:
                _print_restore_success(logger, result)
                return 0
        else:
            logger.error("Failed to initiate restore")
            log_error("Failed to initiate restore")
            return 1

    else:
        # Interactive mode
        print()
        print(f"{Colors.BLUE}Available snapshots:{Colors.NC}")
        snapshots = get_rds_snapshots(rds_id, max_results=10)
        if not snapshots:
            log_error("No snapshots found")
            return 1

        for i, snap in enumerate(snapshots):
            created = snap.get("created_at", "unknown")
            if created and "T" in created:
                created = _format_utc_timestamp(created)
            snap_type = snap.get("type", "")
            print(f"  {i}. {snap['id']:<50} {snap_type:<10} {created}")
        print()

        rds_details = get_rds_instance_details(rds_id)
        if rds_details and rds_details.get("latest_restorable_time"):
            latest = format_iso(rds_details["latest_restorable_time"])
            print(f"Point-in-time recovery is available up to: {latest}")
            print()

        print("Options:")
        print("  Enter a number to restore from that snapshot")
        print("  Or enter a time in ISO format (e.g., 2026-02-04T12:00:00Z) for point-in-time")
        print()

        try:
            choice = input("Selection: ").strip()
            if not choice:
                log_error("Cancelled")
                return 1

            try:
                idx = int(choice)
                if idx < 0 or idx >= len(snapshots):
                    log_error("Invalid selection")
                    return 1
                return cmd_restore_db(environment, snapshot=snapshots[idx]["id"], time=None)
            except ValueError:
                return cmd_restore_db(environment, snapshot=None, time=choice)

        # pysmelly: ignore duplicate-except-blocks — CLI user cancellation handler
        except (EOFError, KeyboardInterrupt):
            print()
            log_error("Cancelled")
            return 1


# =============================================================================
# Revert Command
# =============================================================================


def cmd_revert(
    environment: str, list_checkpoints_flag: bool, checkpoint: str | None, yes: bool
) -> int:
    """Revert to a previous checkpoint."""
    if list_checkpoints_flag:
        checkpoints = list_checkpoints(environment)
        if not checkpoints:
            log_info("No checkpoints found")
            return 0

        print()
        print(f"{Colors.BLUE}Available checkpoints for {environment}:{Colors.NC}")
        for cp in checkpoints:
            timestamp = cp.timestamp
            if "T" in timestamp:
                timestamp = _format_utc_timestamp(timestamp)
            print(f"  {cp.filename:<45} {cp.action:<12} {timestamp}")
            print(f"    Reason: {cp.reason}")
        print()
        return 0

    if not checkpoint:
        log_error("Specify --checkpoint <filename> or --list")
        return 1

    # Load checkpoint
    try:
        cp = load_checkpoint(checkpoint)
    except FileNotFoundError:
        log_error(f"Checkpoint not found: {checkpoint}")
        return 1

    if cp.environment != environment:
        log_error(f"Checkpoint is for environment '{cp.environment}', " f"not '{environment}'")
        return 1

    ctx = _load_emergency_context(environment, require_cluster=True)
    cluster_name = ctx.cluster_name
    logger = ctx.logger
    logger.action("revert")

    # Show what will be restored
    print()
    print(f"{Colors.YELLOW}Reverting to checkpoint: {checkpoint}{Colors.NC}")
    print(f"  Created: {cp.timestamp}")
    print(f"  Action: {cp.action}")
    print(f"  Reason: {cp.reason}")
    print()
    print("Services to restore:")
    for name, state in cp.services.items():
        rev = state.task_definition.split(":")[-1]
        print(f"  {name}: revision {rev}, count {state.desired_count}")
    print()

    if not confirm_action(skip=yes):
        return 1

    # Restore each service
    for name, state in cp.services.items():
        log(f"Restoring {name}...")
        logger.ecs(f"Restoring {name} to revision {state.task_definition.split(':')[-1]}")

        if not update_service_task_definition(cluster_name, name, state.task_definition):
            logger.error(f"Failed to update task definition for {name}")
            log_error(f"Failed to update task definition for {name}")
            continue

        if not scale_service(cluster_name, name, state.desired_count):
            logger.error(f"Failed to scale {name}")
            log_error(f"Failed to scale {name}")
            continue

        log_ok(f"Restored {name}")

    logger.success("Revert completed")
    log_success("Revert completed")
    print()
    log_info("Note: Services may take a few minutes to stabilize")

    return 0


# =============================================================================
# Force-Deploy Command
# =============================================================================


def cmd_force_deploy(environment: str, service: str | None, all_services: bool, yes: bool) -> int:
    """Force a new deployment of ECS service(s)."""
    result = _load_cluster_services(environment)
    if result is None:
        return 1
    ctx, services = result
    cluster_name = ctx.cluster_name
    logger = ctx.logger

    if service:
        if service not in services:
            log_error(f"Service '{service}' not found. Available: {', '.join(services.keys())}")
            return 1
        target_services = [service]
    elif all_services:
        target_services = list(services.keys())
    else:
        log_error("Specify --service <name> or --all")
        return 1

    print()
    print(f"{Colors.YELLOW}Force deploying:{Colors.NC}")
    for name in target_services:
        print(f"  - {name}")
    print()

    if not confirm_action(skip=yes):
        return 1

    logger.action("force-deploy")

    for name in target_services:
        log(f"Forcing new deployment of {name}...")
        logger.ecs(f"Force new deployment of {name}")

        if force_new_deployment(cluster_name, name):
            log_ok(f"Force deployment initiated for {name}")
        else:
            logger.error(f"Failed to force deploy {name}")
            log_error(f"Failed to force deploy {name}")

    logger.success("Force deploy completed")
    log_success("Force deploy initiated")
    log_info("Note: Tasks will be replaced over the next few minutes")

    return 0


# =============================================================================
# CLI
# =============================================================================


@click.group()
def cli():
    """Emergency operations that modify production state.

    \b
    For read-only monitoring commands (status, health, logs, maintenance, ecr, audit),
    use bin/ops.py instead.
    """


@cli.command()
@click.argument("environment")
@click.option("--service", "-s", help="Service name (interactive if not specified)")
@click.option("--revision", "-r", type=int, help="Specific revision number")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def rollback(environment, service, revision, yes):
    """Roll back to previous task definition."""
    _validate_and_configure(environment)
    sys.exit(cmd_rollback(environment, service, revision, yes))


@cli.command()
@click.argument("environment")
@click.option("--service", "-s", help="Service name")
@click.option("--count", "-c", type=int, help="Target count")
@click.option("--all", "-a", "all_services", is_flag=True, help="Scale all services")
@click.option("--multiplier", "-m", type=float, help="Scale by multiplier (with --all)")
@click.option("--reset", is_flag=True, help="Reset to configured replicas")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def scale(environment, service, count, all_services, multiplier, reset, yes):
    """Scale services up/down."""
    _validate_and_configure(environment)
    sys.exit(cmd_scale(environment, service, count, all_services, multiplier, reset, yes))


@cli.command()
@click.argument("environment")
@click.option("--no-wait", is_flag=True, help="Don't wait for snapshot to complete")
def snapshot(environment, no_wait):
    """Create RDS snapshot."""
    _validate_and_configure(environment)
    sys.exit(cmd_snapshot(environment, no_wait))


@cli.command("restore-db")
@click.argument("environment")
@click.option("--snapshot", help="Snapshot ID to restore from")
@click.option("--time", help="Point-in-time to restore to (ISO format)")
def restore_db(environment, snapshot, time):
    """Restore database (creates new instance)."""
    _validate_and_configure(environment)
    sys.exit(cmd_restore_db(environment, snapshot, time))


@cli.command()
@click.argument("environment")
@click.option(
    "--list", "-l", "list_checkpoints_flag", is_flag=True, help="List available checkpoints"
)
@click.option("--checkpoint", help="Checkpoint filename to revert to")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def revert(environment, list_checkpoints_flag, checkpoint, yes):
    """Revert to checkpoint."""
    _validate_and_configure(environment)
    sys.exit(cmd_revert(environment, list_checkpoints_flag, checkpoint, yes))


@cli.command("force-deploy")
@click.argument("environment")
@click.option("--service", "-s", help="Service name")
@click.option("--all", "-a", "all_services", is_flag=True, help="Force deploy all services")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def force_deploy(environment, service, all_services, yes):
    """Force new deployment (replace all tasks)."""
    _validate_and_configure(environment)
    sys.exit(cmd_force_deploy(environment, service, all_services, yes))


if __name__ == "__main__":
    cli()
