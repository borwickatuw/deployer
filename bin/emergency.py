#!/usr/bin/env python3
"""
Emergency operations tool for production incident handling.

These commands MODIFY production state. For read-only monitoring,
use bin/ops.py instead.

Provides safe abstractions for common emergency procedures with automatic
checkpointing and audit logging.

Usage:
    # Roll back to previous task definition
    uv run python bin/emergency.py myapp-production rollback --service web

    # Scale services quickly
    uv run python bin/emergency.py myapp-production scale --service web --count 10

    # Create emergency snapshot
    uv run python bin/emergency.py myapp-production snapshot

    # Restore database (creates new instance, doesn't modify original)
    uv run python bin/emergency.py myapp-production restore-db --snapshot <id>

    # List and restore from checkpoints
    uv run python bin/emergency.py myapp-production revert --list

    # Force new deployment
    uv run python bin/emergency.py myapp-production force-deploy --service web
"""

import argparse
import sys
from datetime import datetime

from deployer.aws import rds
from deployer.core import (
    get_cluster_name_from_config,
    get_rds_instance_id_from_config,
    get_service_replicas_from_config,
    load_environment_config,
)
from deployer.emergency import (
    EmergencyLogger,
    cleanup_old_checkpoints,
    compare_task_definitions,
    create_checkpoint,
    create_emergency_snapshot,
    force_new_deployment,
    get_all_services_state,
    get_rds_snapshots,
    get_service_state,
    list_checkpoints,
    list_task_definition_revisions,
    load_checkpoint,
    restore_from_point_in_time,
    restore_from_snapshot,
    scale_service,
    update_service_task_definition,
    wait_for_deployment,
)
from deployer.emergency.checkpoint import RdsState, ServiceState
from deployer.emergency.rds import get_rds_instance_details
from deployer.utils import (
    Colors,
    configure_aws_profile_for_environment,
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
# Rollback Command
# =============================================================================


def cmd_rollback(args) -> int:
    """Roll back ECS service(s) to previous task definition."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)
    cluster_name = get_cluster_name_from_config(config)
    rds_id = get_rds_instance_id_from_config(config)

    if not cluster_name:
        log_error("Unable to determine ECS cluster name")
        return 1

    logger = EmergencyLogger(args.environment)

    # Get all services
    services = get_all_services_state(cluster_name)
    if not services:
        log_error("No services found in cluster")
        return 1

    # Determine which service to roll back
    if args.service:
        if args.service not in services:
            log_error(
                f"Service '{args.service}' not found. Available: {', '.join(services.keys())}"
            )
            return 1
        service_name = args.service
    else:
        # Interactive mode: list services and prompt
        print()
        print(f"{Colors.BLUE}Available services:{Colors.NC}")
        service_list = sorted(services.keys())
        for i, name in enumerate(service_list, 1):
            state = services[name]
            revision = state.task_definition.split(":")[-1]
            print(f"  {i}. {name} (current revision: {revision})")
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
    if args.revision:
        # Find the specified revision
        target_rev = None
        for rev in revisions:
            if rev["revision"] == args.revision:
                target_rev = rev
                break
        if not target_rev:
            log_error(f"Revision {args.revision} not found")
            return 1
    else:
        # Interactive mode: show revisions and prompt
        print()
        print(f"{Colors.BLUE}Recent revisions for {service_name}:{Colors.NC}")
        for i, rev in enumerate(revisions):
            registered = rev.get("registered_at", "unknown")
            if registered and "T" in registered:
                try:
                    dt = datetime.fromisoformat(registered.replace("Z", "+00:00"))
                    registered = dt.strftime("%Y-%m-%d %H:%M UTC")
                except ValueError:
                    pass
            current = " (current)" if i == 0 else ""
            print(f"  {i}. revision {rev['revision']:>3} - {registered}{current}")
        print()

        try:
            choice = input(
                "Select revision to roll back to (number, default=1 for previous): "
            ).strip()
            if not choice:
                idx = 1
            else:
                idx = int(choice)
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
        for container, changes in diff.items():
            if changes.get("added"):
                for var, val in changes["added"].items():
                    print(f"  + {var}={val}")
            if changes.get("removed"):
                for var, val in changes["removed"].items():
                    print(f"  - {var}={val}")
            if changes.get("changed"):
                for var, vals in changes["changed"].items():
                    print(f"  ~ {var}: {vals['old']} -> {vals['new']}")

    # Show confirmation
    current_rev = current_state.task_definition.split(":")[-1]
    target_revision_num = target_rev["revision"]

    print()
    print(f"{Colors.YELLOW}About to roll back {service_name}:{Colors.NC}")
    print(f"  From: {family}:{current_rev}")
    print(f"  To:   {family}:{target_revision_num}")
    print()
    print(f"Checkpoint will be saved to: local/checkpoints/")
    print()

    if not args.yes:
        try:
            confirm = input("Continue? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                log_error("Cancelled")
                return 1
        except (EOFError, KeyboardInterrupt):
            print()
            log_error("Cancelled")
            return 1

    # Create checkpoint
    logger.action("rollback")
    log("Creating checkpoint...")

    rds_state = None
    if rds_id:
        rds_status = rds.get_status(rds_id)
        if rds_status:
            rds_state = RdsState(
                instance_id=rds_id,
                status=rds_status["status"],
            )

    checkpoint = create_checkpoint(
        environment=args.environment,
        action="rollback",
        reason=f"Rolling back {service_name} from revision {current_rev} to {target_revision_num}",
        services={
            name: ServiceState(
                task_definition=state.task_definition,
                desired_count=state.desired_count,
                running_count=state.running_count,
            )
            for name, state in services.items()
        },
        rds=rds_state,
    )
    logger.checkpoint(f"Created {checkpoint.filename}")
    log_success(f"Checkpoint saved: local/checkpoints/{checkpoint.filename}")

    # Perform rollback
    log(f"Rolling back {service_name} to revision {target_revision_num}...")
    logger.ecs(
        f"Rolling back {service_name} from revision {current_rev} to {target_revision_num}"
    )

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

    # Cleanup old checkpoints
    cleanup_old_checkpoints(keep_count=10, keep_days=7, environment=args.environment)

    return 0


# =============================================================================
# Scale Command
# =============================================================================


def cmd_scale(args) -> int:
    """Scale ECS services."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)
    cluster_name = get_cluster_name_from_config(config)
    rds_id = get_rds_instance_id_from_config(config)

    if not cluster_name:
        log_error("Unable to determine ECS cluster name")
        return 1

    logger = EmergencyLogger(args.environment)

    # Get all services
    services = get_all_services_state(cluster_name)
    if not services:
        log_error("No services found in cluster")
        return 1

    # Determine what to scale
    if args.reset:
        # Reset to configured replicas
        configured_replicas = get_service_replicas_from_config(config)
        to_scale = {name: configured_replicas.get(name, 1) for name in services.keys()}
    elif args.service:
        if args.service not in services:
            log_error(f"Service '{args.service}' not found")
            return 1
        if args.count is None:
            log_error("--count is required when using --service")
            return 1
        to_scale = {args.service: args.count}
    elif args.all:
        if args.multiplier:
            to_scale = {
                name: max(1, int(state.desired_count * args.multiplier))
                for name, state in services.items()
            }
        elif args.count is not None:
            to_scale = {name: args.count for name in services.keys()}
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

    if not args.yes:
        try:
            confirm = input("Continue? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                log_error("Cancelled")
                return 1
        except (EOFError, KeyboardInterrupt):
            print()
            log_error("Cancelled")
            return 1

    # Create checkpoint
    logger.action("scale")
    log("Creating checkpoint...")

    rds_state = None
    if rds_id:
        rds_status = rds.get_status(rds_id)
        if rds_status:
            rds_state = RdsState(
                instance_id=rds_id,
                status=rds_status["status"],
            )

    checkpoint = create_checkpoint(
        environment=args.environment,
        action="scale",
        reason=f"Scaling services: {', '.join(to_scale.keys())}",
        services={
            name: ServiceState(
                task_definition=state.task_definition,
                desired_count=state.desired_count,
                running_count=state.running_count,
            )
            for name, state in services.items()
        },
        rds=rds_state,
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

    # Cleanup old checkpoints
    cleanup_old_checkpoints(keep_count=10, keep_days=7, environment=args.environment)

    return 0


# =============================================================================
# Snapshot Command
# =============================================================================


def cmd_snapshot(args) -> int:
    """Create emergency RDS snapshot."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)
    rds_id = get_rds_instance_id_from_config(config)

    if not rds_id:
        log_error("RDS instance not configured for this environment")
        return 1

    logger = EmergencyLogger(args.environment)
    logger.action("snapshot")

    log(f"Creating emergency snapshot of {rds_id}...")
    logger.rds(f"Creating snapshot of {rds_id}")

    snapshot_id = create_emergency_snapshot(rds_id, wait=not args.no_wait)
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


def cmd_restore_db(args) -> int:
    """Restore database from snapshot or point-in-time."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)
    rds_id = get_rds_instance_id_from_config(config)

    if not rds_id:
        log_error("RDS instance not configured for this environment")
        return 1

    logger = EmergencyLogger(args.environment)
    logger.action("restore-db")

    if args.snapshot:
        # Restore from snapshot
        log(f"Restoring from snapshot: {args.snapshot}")
        logger.rds(f"Restoring from snapshot {args.snapshot}")

        result = restore_from_snapshot(rds_id, args.snapshot)
        if result:
            if result["status"] == "error":
                logger.error(result["message"])
                log_error(result["message"])
                return 1
            else:
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
                print(
                    "  - To use the restored database, update your application's DATABASE_URL"
                )
                print(f"  - To delete the restored instance if not needed:")
                print(
                    f"    aws rds delete-db-instance --db-instance-identifier {result['instance_id']} --skip-final-snapshot"
                )
                return 0
        else:
            logger.error("Failed to initiate restore")
            log_error("Failed to initiate restore")
            return 1

    elif args.time:
        # Parse time
        try:
            restore_time = datetime.fromisoformat(args.time.replace("Z", "+00:00"))
        except ValueError:
            log_error(f"Invalid time format: {args.time}")
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
                print(
                    "  - To use the restored database, update your application's DATABASE_URL"
                )
                print(f"  - To delete the restored instance if not needed:")
                print(
                    f"    aws rds delete-db-instance --db-instance-identifier {result['instance_id']} --skip-final-snapshot"
                )
                return 0
        else:
            logger.error("Failed to initiate restore")
            log_error("Failed to initiate restore")
            return 1

    else:
        # Interactive mode: show snapshots and prompt
        print()
        print(f"{Colors.BLUE}Available snapshots:{Colors.NC}")
        snapshots = get_rds_snapshots(rds_id, max_results=10, include_automated=True)
        if not snapshots:
            log_error("No snapshots found")
            return 1

        for i, snap in enumerate(snapshots):
            created = snap.get("created_at", "unknown")
            if created and "T" in created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    created = dt.strftime("%Y-%m-%d %H:%M UTC")
                except ValueError:
                    pass
            snap_type = snap.get("type", "")
            print(f"  {i}. {snap['id']:<50} {snap_type:<10} {created}")
        print()

        # Also show point-in-time option
        rds_details = get_rds_instance_details(rds_id)
        if rds_details and rds_details.get("latest_restorable_time"):
            latest = rds_details["latest_restorable_time"]
            if hasattr(latest, "isoformat"):
                latest = latest.isoformat()
            print(f"Point-in-time recovery is available up to: {latest}")
            print()

        print("Options:")
        print("  Enter a number to restore from that snapshot")
        print(
            "  Or enter a time in ISO format (e.g., 2026-02-04T12:00:00Z) for point-in-time"
        )
        print()

        try:
            choice = input("Selection: ").strip()
            if not choice:
                log_error("Cancelled")
                return 1

            # Try to parse as number first
            try:
                idx = int(choice)
                if idx < 0 or idx >= len(snapshots):
                    log_error("Invalid selection")
                    return 1
                args.snapshot = snapshots[idx]["id"]
                return cmd_restore_db(args)
            except ValueError:
                # Try to parse as time
                args.time = choice
                return cmd_restore_db(args)

        except (EOFError, KeyboardInterrupt):
            print()
            log_error("Cancelled")
            return 1


# =============================================================================
# Revert Command
# =============================================================================


def cmd_revert(args) -> int:
    """Revert to a previous checkpoint."""
    if args.list:
        # List checkpoints
        checkpoints = list_checkpoints(args.environment)
        if not checkpoints:
            log_info("No checkpoints found")
            return 0

        print()
        print(f"{Colors.BLUE}Available checkpoints for {args.environment}:{Colors.NC}")
        for cp in checkpoints:
            timestamp = cp.timestamp
            if "T" in timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    timestamp = dt.strftime("%Y-%m-%d %H:%M UTC")
                except ValueError:
                    pass
            print(f"  {cp.filename:<45} {cp.action:<12} {timestamp}")
            print(f"    Reason: {cp.reason}")
        print()
        return 0

    if not args.checkpoint:
        log_error("Specify --checkpoint <filename> or --list")
        return 1

    # Load checkpoint
    try:
        checkpoint = load_checkpoint(args.checkpoint)
    except FileNotFoundError:
        log_error(f"Checkpoint not found: {args.checkpoint}")
        return 1

    if checkpoint.environment != args.environment:
        log_error(
            f"Checkpoint is for environment '{checkpoint.environment}', "
            f"not '{args.environment}'"
        )
        return 1

    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)
    cluster_name = get_cluster_name_from_config(config)

    if not cluster_name:
        log_error("Unable to determine ECS cluster name")
        return 1

    logger = EmergencyLogger(args.environment)
    logger.action("revert")

    # Show what will be restored
    print()
    print(f"{Colors.YELLOW}Reverting to checkpoint: {args.checkpoint}{Colors.NC}")
    print(f"  Created: {checkpoint.timestamp}")
    print(f"  Action: {checkpoint.action}")
    print(f"  Reason: {checkpoint.reason}")
    print()
    print("Services to restore:")
    for name, state in checkpoint.services.items():
        revision = state.task_definition.split(":")[-1]
        print(f"  {name}: revision {revision}, count {state.desired_count}")
    print()

    if not args.yes:
        try:
            confirm = input("Continue? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                log_error("Cancelled")
                return 1
        except (EOFError, KeyboardInterrupt):
            print()
            log_error("Cancelled")
            return 1

    # Restore each service
    for name, state in checkpoint.services.items():
        log(f"Restoring {name}...")
        logger.ecs(
            f"Restoring {name} to revision {state.task_definition.split(':')[-1]}"
        )

        # Update task definition
        if not update_service_task_definition(
            cluster_name, name, state.task_definition
        ):
            logger.error(f"Failed to update task definition for {name}")
            log_error(f"Failed to update task definition for {name}")
            continue

        # Update desired count
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


def cmd_force_deploy(args) -> int:
    """Force a new deployment of ECS service(s)."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)
    cluster_name = get_cluster_name_from_config(config)

    if not cluster_name:
        log_error("Unable to determine ECS cluster name")
        return 1

    logger = EmergencyLogger(args.environment)

    # Get all services
    services = get_all_services_state(cluster_name)
    if not services:
        log_error("No services found in cluster")
        return 1

    # Determine which service(s) to force deploy
    if args.service:
        if args.service not in services:
            log_error(
                f"Service '{args.service}' not found. Available: {', '.join(services.keys())}"
            )
            return 1
        target_services = [args.service]
    elif args.all:
        target_services = list(services.keys())
    else:
        log_error("Specify --service <name> or --all")
        return 1

    print()
    print(f"{Colors.YELLOW}Force deploying:{Colors.NC}")
    for name in target_services:
        print(f"  - {name}")
    print()

    if not args.yes:
        try:
            confirm = input("Continue? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                log_error("Cancelled")
                return 1
        except (EOFError, KeyboardInterrupt):
            print()
            log_error("Cancelled")
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
# Main
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Emergency operations that modify production state",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  rollback      Roll back service(s) to previous task definition
  scale         Scale services up/down
  snapshot      Create emergency RDS snapshot
  restore-db    Restore database from snapshot or point-in-time
  revert        Revert to a previous checkpoint
  force-deploy  Force new deployment (replace all tasks)

For read-only monitoring commands (status, health, logs, maintenance, ecr, audit),
use bin/ops.py instead.

Examples:
  %(prog)s myapp-production rollback --service web
  %(prog)s myapp-production scale --service web --count 10
  %(prog)s myapp-production snapshot
  %(prog)s myapp-production restore-db --snapshot <snapshot-id>
  %(prog)s myapp-production force-deploy --service web
        """,
    )

    parser.add_argument(
        "environment",
        help="Environment name (e.g., myapp-production)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # rollback command
    rollback_parser = subparsers.add_parser(
        "rollback", help="Roll back to previous task definition"
    )
    rollback_parser.add_argument(
        "--service", "-s", help="Service name (interactive if not specified)"
    )
    rollback_parser.add_argument(
        "--revision", "-r", type=int, help="Specific revision number"
    )
    rollback_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    # scale command
    scale_parser = subparsers.add_parser("scale", help="Scale services")
    scale_parser.add_argument("--service", "-s", help="Service name")
    scale_parser.add_argument("--count", "-c", type=int, help="Target count")
    scale_parser.add_argument(
        "--all", "-a", action="store_true", help="Scale all services"
    )
    scale_parser.add_argument(
        "--multiplier", "-m", type=float, help="Scale by multiplier (with --all)"
    )
    scale_parser.add_argument(
        "--reset", action="store_true", help="Reset to configured replicas"
    )
    scale_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    # snapshot command
    snapshot_parser = subparsers.add_parser("snapshot", help="Create RDS snapshot")
    snapshot_parser.add_argument(
        "--no-wait", action="store_true", help="Don't wait for snapshot to complete"
    )

    # restore-db command
    restore_parser = subparsers.add_parser(
        "restore-db", help="Restore database (creates new instance)"
    )
    restore_parser.add_argument("--snapshot", help="Snapshot ID to restore from")
    restore_parser.add_argument(
        "--time", help="Point-in-time to restore to (ISO format)"
    )

    # revert command
    revert_parser = subparsers.add_parser("revert", help="Revert to checkpoint")
    revert_parser.add_argument(
        "--list", "-l", action="store_true", help="List available checkpoints"
    )
    revert_parser.add_argument("--checkpoint", help="Checkpoint filename to revert to")
    revert_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    # force-deploy command
    force_deploy_parser = subparsers.add_parser(
        "force-deploy", help="Force new deployment"
    )
    force_deploy_parser.add_argument("--service", "-s", help="Service name")
    force_deploy_parser.add_argument(
        "--all", "-a", action="store_true", help="Force deploy all services"
    )
    force_deploy_parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip confirmation"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Validate environment
    env_path, error = validate_environment_deployed(args.environment)
    if error:
        log_error(error)
        sys.exit(1)

    # Show warning banner for all commands (all commands in this tool modify production)
    print()
    print(f"{Colors.YELLOW}╔══════════════════════════════════════════════════════════════╗")
    print(f"║  ⚠  EMERGENCY TOOL - This command can modify {args.environment}")
    print(f"╚══════════════════════════════════════════════════════════════╝{Colors.NC}")

    # Configure AWS profile - use infra profile for broader access
    configure_aws_profile_for_environment("infra", args.environment)
    print()

    # Dispatch to command handler
    commands = {
        "rollback": cmd_rollback,
        "scale": cmd_scale,
        "snapshot": cmd_snapshot,
        "restore-db": cmd_restore_db,
        "revert": cmd_revert,
        "force-deploy": cmd_force_deploy,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
