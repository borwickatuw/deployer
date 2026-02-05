#!/usr/bin/env python3
"""
Emergency operations tool for production incident handling.

Provides safe abstractions for common emergency procedures with automatic
checkpointing and audit logging.

Usage:
    # View current state
    uv run python bin/emergency.py myapp-production status

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
"""

import argparse
import sys
from datetime import datetime, timezone

from deployer.aws import rds
from deployer.core import (
    get_cluster_name_from_config,
    get_rds_instance_id_from_config,
    get_service_replicas_from_config,
    get_target_group_arn_from_config,
    load_environment_config,
)
from deployer.emergency import (
    Checkpoint,
    EmergencyLogger,
    cleanup_old_checkpoints,
    compare_task_definitions,
    create_checkpoint,
    create_emergency_snapshot,
    force_new_deployment,
    get_all_pending_maintenance,
    get_all_services_state,
    get_image_scan_findings,
    get_log_groups_for_environment,
    get_rds_snapshots,
    get_repository_scan_summary,
    get_service_state,
    get_target_health,
    get_task_definition_details,
    list_checkpoints,
    list_repositories_for_environment,
    list_task_definition_revisions,
    load_checkpoint,
    restore_from_point_in_time,
    restore_from_snapshot,
    scale_service,
    scan_logs_for_errors,
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
# Status Command
# =============================================================================


def cmd_status(args) -> int:
    """Show current state of environment."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)

    cluster_name = get_cluster_name_from_config(config)
    rds_id = get_rds_instance_id_from_config(config)

    print()
    print(f"{Colors.BLUE}Environment: {args.environment}{Colors.NC}")
    print()

    # ECS Services
    if cluster_name:
        print(f"{Colors.BLUE}ECS Services:{Colors.NC}")
        services = get_all_services_state(cluster_name)
        if services:
            print(
                f"  {'Service':<25} {'Running':<10} {'Desired':<10} {'Task Definition'}"
            )
            print(f"  {'-' * 25} {'-' * 10} {'-' * 10} {'-' * 40}")
            for name, state in sorted(services.items()):
                # Extract revision from task definition ARN
                revision = state.task_definition.split(":")[-1]
                family = state.task_definition.split("/")[-1].rsplit(":", 1)[0]
                print(
                    f"  {name:<25} {state.running_count:<10} {state.desired_count:<10} {family}:{revision}"
                )
        else:
            print("  No services found")
        print()

        # Recent task definition revisions for each service
        print(f"{Colors.BLUE}Recent Task Definitions:{Colors.NC}")
        for name in sorted(services.keys()):
            family = f"{args.environment}-{name}"
            revisions = list_task_definition_revisions(family, max_results=5)
            if revisions:
                print(f"  {name}:")
                for rev in revisions:
                    registered = rev.get("registered_at", "unknown")
                    if registered and "T" in registered:
                        # Parse and format timestamp
                        try:
                            dt = datetime.fromisoformat(
                                registered.replace("Z", "+00:00")
                            )
                            registered = dt.strftime("%Y-%m-%d %H:%M UTC")
                        except ValueError:
                            pass
                    print(f"    revision {rev['revision']:>3} - {registered}")
        print()
    else:
        log_warning("Unable to determine ECS cluster name")
        print()

    # RDS Status
    if rds_id:
        print(f"{Colors.BLUE}RDS Instance: {rds_id}{Colors.NC}")
        rds_status = rds.get_status(rds_id)
        if rds_status:
            print(f"  Status: {rds_status['status']}")
            print(f"  Class: {rds_status['instance_class']}")
            print(f"  Engine: {rds_status['engine']}")
        else:
            print("  Unable to retrieve status")
        print()

        # Recent snapshots
        snapshots = get_rds_snapshots(rds_id, max_results=5, include_automated=True)
        if snapshots:
            print(f"{Colors.BLUE}Recent Snapshots:{Colors.NC}")
            for snap in snapshots:
                created = snap.get("created_at", "unknown")
                if created and "T" in created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        created = dt.strftime("%Y-%m-%d %H:%M UTC")
                    except ValueError:
                        pass
                snap_type = snap.get("type", "")
                print(f"  {snap['id']:<50} {snap_type:<10} {created}")
        print()
    else:
        log_warning("RDS instance not configured")
        print()

    # Auto-scaling info (if available)
    scaling_config = config.get("services", {}).get("scaling", {})
    if scaling_config:
        print(f"{Colors.BLUE}Auto-Scaling Configuration:{Colors.NC}")
        for name, cfg in scaling_config.items():
            min_r = cfg.get("min_replicas", "?")
            max_r = cfg.get("max_replicas", "?")
            target = cfg.get("cpu_target", "?")
            print(f"  {name}: min={min_r}, max={max_r}, cpu_target={target}%")
        print()

    return 0


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
# Health Command
# =============================================================================


def cmd_health(args) -> int:
    """Check ALB target health."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)
    target_group_arn = get_target_group_arn_from_config(config)

    if not target_group_arn:
        log_error("Target group ARN not configured")
        return 1

    print()
    print(f"{Colors.BLUE}ALB Target Health:{Colors.NC}")

    targets = get_target_health(target_group_arn)
    if not targets:
        print("  No targets registered")
        return 0

    healthy_count = 0
    unhealthy_count = 0

    for target in targets:
        state = target["health_state"]
        target_id = target["target_id"]
        port = target["port"]

        if state == "healthy":
            healthy_count += 1
            status_color = Colors.GREEN
        else:
            unhealthy_count += 1
            status_color = Colors.RED

        print(f"  {target_id}:{port} - {status_color}{state}{Colors.NC}")
        if target["reason"]:
            print(f"    Reason: {target['reason']}")
        if target["description"]:
            print(f"    Details: {target['description']}")

    print()
    print(f"Summary: {Colors.GREEN}{healthy_count} healthy{Colors.NC}, ", end="")
    if unhealthy_count > 0:
        print(f"{Colors.RED}{unhealthy_count} unhealthy{Colors.NC}")
    else:
        print(f"{unhealthy_count} unhealthy")

    return 0 if unhealthy_count == 0 else 1


# =============================================================================
# Logs Command
# =============================================================================


def cmd_logs(args) -> int:
    """Scan logs for errors."""
    log_groups = get_log_groups_for_environment(args.environment)

    if not log_groups:
        log_warning(f"No log groups found with prefix /ecs/{args.environment}")
        log_info("Log groups are created when ECS tasks first run")
        log_info("Check if any tasks have been deployed to this environment")
        return 0

    print()
    print(f"{Colors.BLUE}Scanning logs for errors (last {args.minutes} minutes):{Colors.NC}")
    print(f"Log groups: {', '.join(log_groups)}")
    print()

    total_errors = 0

    for log_group in log_groups:
        events = scan_logs_for_errors(
            log_group,
            lookback_minutes=args.minutes,
            max_results=args.limit,
        )

        if events:
            # Extract service name from log group
            service_name = log_group.replace(f"/ecs/{args.environment}-", "")
            print(f"{Colors.YELLOW}{service_name}:{Colors.NC} ({len(events)} errors)")

            for event in events[:10]:  # Show first 10
                timestamp = event["timestamp"]
                if "T" in timestamp:
                    try:
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        timestamp = dt.strftime("%H:%M:%S")
                    except ValueError:
                        pass

                # Truncate long messages
                message = event["message"][:200]
                if len(event["message"]) > 200:
                    message += "..."
                print(f"  [{timestamp}] {message}")

            if len(events) > 10:
                print(f"  ... and {len(events) - 10} more")
            print()

            total_errors += len(events)

    if total_errors == 0:
        log_success("No errors found")
    else:
        log_warning(f"Found {total_errors} error(s)")

    return 0


# =============================================================================
# Maintenance Command
# =============================================================================


def cmd_maintenance(args) -> int:
    """Show pending maintenance for RDS and ElastiCache."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)
    rds_id = get_rds_instance_id_from_config(config)

    # ElastiCache cluster ID follows module convention: {environment}-cache
    # Could also try to derive from [cache].url if available
    elasticache_id = f"{args.environment}-cache"
    cache_config = config.get("cache", {})
    if not cache_config.get("url"):
        # No cache configured for this environment
        elasticache_id = None

    print()
    print(f"{Colors.BLUE}Pending Maintenance:{Colors.NC}")
    print()

    maintenance = get_all_pending_maintenance(
        rds_instance_id=rds_id,
        elasticache_cluster_id=elasticache_id,
    )

    has_pending = False

    # RDS maintenance
    if rds_id:
        if maintenance["rds"]:
            has_pending = True
            print(f"{Colors.YELLOW}RDS ({rds_id}):{Colors.NC}")
            for item in maintenance["rds"]:
                print(f"  - {item['action']}: {item['description']}")
                if item.get("auto_apply_after"):
                    print(f"    Auto-apply after: {item['auto_apply_after']}")
                if item.get("current_apply_date"):
                    print(f"    Scheduled: {item['current_apply_date']}")
            print()
        else:
            print(f"RDS ({rds_id}): No pending maintenance")
    else:
        print("RDS: Not configured")

    # ElastiCache maintenance
    if elasticache_id:
        if maintenance["elasticache"]:
            has_pending = True
            print(f"{Colors.YELLOW}ElastiCache ({elasticache_id}):{Colors.NC}")
            for item in maintenance["elasticache"]:
                severity = item.get("severity", "")
                if severity:
                    print(f"  - [{severity}] {item['action']}: {item['description']}")
                else:
                    print(f"  - {item['action']}: {item['description']}")
            print()
        else:
            print(f"ElastiCache ({elasticache_id}): No pending maintenance")
    else:
        print("ElastiCache: Not configured")

    print()
    if has_pending:
        log_warning("Pending maintenance found - schedule updates during maintenance window")
    else:
        log_success("No pending maintenance")

    return 0


# =============================================================================
# ECR Command
# =============================================================================


def cmd_ecr(args) -> int:
    """Show ECR vulnerability findings."""
    env_path = get_environment_path(args.environment)
    config = load_environment_config(env_path)

    print()
    print(f"{Colors.BLUE}ECR Vulnerability Scan Results:{Colors.NC}")
    print()

    # Get service names from config to construct repository names
    # This avoids needing permission to list all repositories
    service_config = config.get("services", {}).get("config", {})
    service_names = list(service_config.keys()) if service_config else None

    # Find repositories for this environment
    # Repositories are named {environment}-{service} (e.g., myapp-staging-web)
    repos = list_repositories_for_environment(args.environment, service_names)
    if not repos:
        log_warning(f"No ECR repositories found for {args.environment}")
        if service_names:
            log_info(f"Checked: {', '.join(f'{args.environment}-{s}' for s in service_names)}")
        return 0

    total_critical = 0
    total_high = 0

    for repo in repos:
        # Get scan summary for recent images
        summaries = get_repository_scan_summary(repo, max_images=1)
        if not summaries:
            continue

        latest = summaries[0]
        critical = latest.get("critical_count", 0)
        high = latest.get("high_count", 0)
        scan_status = latest.get("scan_status", "UNKNOWN")

        total_critical += critical
        total_high += high

        # Color based on severity
        if critical > 0:
            status_color = Colors.RED
        elif high > 0:
            status_color = Colors.YELLOW
        else:
            status_color = Colors.GREEN

        repo_short = repo.replace(f"{args.environment}-", "")
        print(f"  {repo_short}:")
        print(f"    Tag: {latest['image_tag']}")
        print(f"    Scan: {scan_status}")
        print(
            f"    Vulnerabilities: {status_color}CRITICAL={critical}, HIGH={high}{Colors.NC}"
        )

        # If there are findings and verbose mode, show details
        if args.verbose and (critical > 0 or high > 0):
            findings = get_image_scan_findings(repo, latest["image_tag"])
            for finding in findings.get("findings", [])[:5]:
                print(f"      - [{finding['severity']}] {finding['name']}")
            if len(findings.get("findings", [])) > 5:
                print(f"      ... and {len(findings['findings']) - 5} more")
        print()

    # Summary
    print(f"{Colors.BLUE}Summary:{Colors.NC}")
    if total_critical > 0:
        print(f"  {Colors.RED}CRITICAL: {total_critical}{Colors.NC}")
    else:
        print(f"  CRITICAL: 0")
    if total_high > 0:
        print(f"  {Colors.YELLOW}HIGH: {total_high}{Colors.NC}")
    else:
        print(f"  HIGH: 0")

    if total_critical > 0:
        log_error("Critical vulnerabilities found - update base images immediately")
        return 1
    elif total_high > 0:
        log_warning("High severity vulnerabilities found - plan updates soon")
        return 0
    else:
        log_success("No critical or high vulnerabilities")
        return 0


# =============================================================================
# Audit Command (Super-command)
# =============================================================================


def cmd_audit(args) -> int:
    """Run all read-only health and security checks."""
    print()
    print(f"{Colors.BLUE}{'=' * 60}{Colors.NC}")
    print(f"{Colors.BLUE}Emergency Audit: {args.environment}{Colors.NC}")
    print(f"{Colors.BLUE}{'=' * 60}{Colors.NC}")

    exit_code = 0

    # 1. Status
    print()
    print(f"{Colors.BLUE}[1/5] Environment Status{Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    if cmd_status(args) != 0:
        exit_code = 1

    # 2. Health
    print()
    print(f"{Colors.BLUE}[2/5] ALB Target Health{Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    if cmd_health(args) != 0:
        exit_code = 1

    # 3. Logs
    print()
    print(f"{Colors.BLUE}[3/5] Recent Errors (last 60 minutes){Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    # Set defaults for logs command
    args.minutes = getattr(args, "minutes", 60)
    args.limit = getattr(args, "limit", 50)
    cmd_logs(args)  # Don't fail audit on log errors

    # 4. Maintenance
    print()
    print(f"{Colors.BLUE}[4/5] Pending Maintenance{Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    cmd_maintenance(args)  # Don't fail audit on pending maintenance

    # 5. ECR Vulnerabilities
    print()
    print(f"{Colors.BLUE}[5/5] ECR Vulnerability Findings{Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    args.verbose = getattr(args, "verbose", False)
    if cmd_ecr(args) != 0:
        exit_code = 1

    # Final summary
    print()
    print(f"{Colors.BLUE}{'=' * 60}{Colors.NC}")
    if exit_code == 0:
        log_success("Audit completed - no critical issues found")
    else:
        log_warning("Audit completed - issues found that require attention")

    return exit_code


# =============================================================================
# Main
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Emergency operations for production incidents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  status        Show current state (services, task definitions, RDS, snapshots)
  rollback      Roll back service(s) to previous task definition
  scale         Scale services up/down
  snapshot      Create emergency RDS snapshot
  restore-db    Restore database from snapshot or point-in-time
  revert        Revert to a previous checkpoint
  force-deploy  Force new deployment (replace all tasks)
  health        Check ALB target health
  logs          Scan recent logs for errors
  maintenance   Show pending RDS/ElastiCache maintenance
  ecr           Show ECR vulnerability findings
  audit         Run all read-only checks (status, health, logs, maintenance, ecr)

Examples:
  %(prog)s myapp-production status
  %(prog)s myapp-production rollback --service web
  %(prog)s myapp-production scale --service web --count 10
  %(prog)s myapp-production snapshot
  %(prog)s myapp-production audit
        """,
    )

    parser.add_argument(
        "environment",
        help="Environment name (e.g., myapp-production)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # status command
    subparsers.add_parser("status", help="Show current environment state")

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

    # health command
    subparsers.add_parser("health", help="Check ALB target health")

    # logs command
    logs_parser = subparsers.add_parser("logs", help="Scan logs for errors")
    logs_parser.add_argument(
        "--minutes", "-m", type=int, default=60, help="Lookback period (default: 60)"
    )
    logs_parser.add_argument(
        "--limit", "-l", type=int, default=100, help="Max events per log group"
    )

    # maintenance command
    subparsers.add_parser("maintenance", help="Show pending maintenance")

    # ecr command
    ecr_parser = subparsers.add_parser("ecr", help="Show ECR vulnerability findings")
    ecr_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show vulnerability details"
    )

    # audit command (super-command)
    audit_parser = subparsers.add_parser(
        "audit", help="Run all read-only checks"
    )
    audit_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed output"
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

    # Configure AWS profile - use infra profile for broader read access
    # (deploy profile lacks logs:DescribeLogGroups, ecr:DescribeRepositories, etc.)
    configure_aws_profile_for_environment("infra", args.environment)
    print()

    # Dispatch to command handler
    commands = {
        "status": cmd_status,
        "rollback": cmd_rollback,
        "scale": cmd_scale,
        "snapshot": cmd_snapshot,
        "restore-db": cmd_restore_db,
        "revert": cmd_revert,
        "force-deploy": cmd_force_deploy,
        "health": cmd_health,
        "logs": cmd_logs,
        "maintenance": cmd_maintenance,
        "ecr": cmd_ecr,
        "audit": cmd_audit,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
