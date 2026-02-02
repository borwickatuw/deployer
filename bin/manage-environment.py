#!/usr/bin/env python3
"""
Manage environment start/stop for cost savings.

Stops ECS services and RDS instances during off-hours while preserving all data.
ElastiCache and ALB continue running (cannot be stopped without deletion).

Usage:
    # Show current state of all environments
    python bin/manage-environment.py status

    # Show status of a specific environment
    python bin/manage-environment.py myapp-staging status

    # Stop an environment (scale ECS to 0, stop RDS)
    python bin/manage-environment.py myapp-staging stop

    # Start an environment (start RDS, restore ECS replicas)
    python bin/manage-environment.py myapp-staging start
"""

import argparse
import sys
from pathlib import Path

from deployer.aws import ecs, rds
from deployer.core import (
    get_cluster_name_from_config,
    get_rds_instance_id_from_config,
    get_service_replicas_from_config,
    load_environment_config,
)
from deployer.utils import (
    configure_aws_profile,
    get_all_environments,
    get_environment_path,
    get_environments_dir,
    validate_environment_deployed,
)


# =============================================================================
# Commands
# =============================================================================

def cmd_status(args) -> int:
    """Show status of environments."""
    if args.environment:
        environments = [args.environment]
    else:
        environments = get_all_environments(get_environments_dir())

    if not environments:
        print("No environments found.", file=sys.stderr)
        return 1

    for env_name in environments:
        env_path = get_environment_path(env_name)

        print(f"\n{'=' * 60}")
        print(f"Environment: {env_name}")
        print(f"{'=' * 60}")

        if not env_path.exists():
            print("  Directory not found")
            continue

        state_file = env_path / "terraform.tfstate"
        if not state_file.exists():
            print("  Status: Not deployed")
            continue

        # Load config from config.toml
        try:
            config = load_environment_config(env_path)
        except (FileNotFoundError, RuntimeError) as e:
            print(f"  Error loading config: {e}")
            continue

        # ECS Status
        cluster_name = get_cluster_name_from_config(config)
        if cluster_name:
            print(f"\n  ECS Cluster: {cluster_name}")
            services = ecs.get_services(cluster_name)
            if services:
                print(f"  {'Service':<30} {'Desired':<10} {'Running':<10} {'Status':<15}")
                print(f"  {'-' * 30} {'-' * 10} {'-' * 10} {'-' * 15}")
                for svc in services:
                    print(f"  {svc['name']:<30} {svc['desired_count']:<10} {svc['running_count']:<10} {svc['status']:<15}")
            else:
                print("  No ECS services found")
        else:
            print("  ECS: Unable to determine cluster name")

        # RDS Status
        rds_id = get_rds_instance_id_from_config(config)
        if rds_id:
            print(f"\n  RDS Instance: {rds_id}")
            rds_status = rds.get_status(rds_id)
            if rds_status:
                print(f"    Status: {rds_status['status']}")
                print(f"    Class: {rds_status['instance_class']}")
                print(f"    Engine: {rds_status['engine']}")
            else:
                print("    Status: Unable to retrieve")
        else:
            print("\n  RDS: Not configured or unable to determine instance ID")

    return 0


def cmd_stop(args) -> int:
    """Stop an environment."""
    env_path, error = validate_environment_deployed(args.environment)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Stopping environment: {args.environment}")

    # Load config from config.toml
    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Get resource identifiers from config
    cluster_name = get_cluster_name_from_config(config)
    rds_id = get_rds_instance_id_from_config(config)

    if not cluster_name:
        print("Error: Unable to determine ECS cluster name", file=sys.stderr)
        return 1

    if not rds_id:
        print("Error: Unable to determine RDS instance ID", file=sys.stderr)
        return 1

    # Step 1: Scale ECS services to 0
    print("\n1. Scaling ECS services to 0...")
    services = ecs.get_services(cluster_name)
    for svc in services:
        print(f"   Scaling {svc['name']} to 0...")
        if not ecs.scale_service(cluster_name, svc["name"], 0):
            print(f"   Warning: Failed to scale {svc['name']}", file=sys.stderr)
        else:
            print(f"   Scaled {svc['name']} to 0")

    # Step 2: Stop RDS instance
    print("\n2. Stopping RDS instance...")
    rds_status = rds.get_status(rds_id)
    if rds_status:
        if rds_status["status"] == "stopped":
            print("   RDS instance already stopped")
        elif rds_status["status"] == "available":
            if rds.stop(rds_id):
                print("   RDS stop initiated (takes 5-10 minutes)")
            else:
                print("   Warning: Failed to stop RDS instance", file=sys.stderr)
        else:
            print(f"   RDS in unexpected state: {rds_status['status']}")
    else:
        print("   Warning: Unable to get RDS status", file=sys.stderr)

    print(f"\nEnvironment {args.environment} stop initiated.")
    print("Note: ElastiCache and ALB continue running (cannot be stopped).")
    return 0


def cmd_start(args) -> int:
    """Start an environment."""
    env_path, error = validate_environment_deployed(args.environment)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Starting environment: {args.environment}")

    # Load config from config.toml
    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Get resource identifiers from config
    cluster_name = get_cluster_name_from_config(config)
    rds_id = get_rds_instance_id_from_config(config)

    if not cluster_name:
        print("Error: Unable to determine ECS cluster name", file=sys.stderr)
        return 1

    if not rds_id:
        print("Error: Unable to determine RDS instance ID", file=sys.stderr)
        return 1

    # Get configured replica counts from config
    configured_replicas = get_service_replicas_from_config(config)

    # Callback for RDS wait status updates
    def rds_status_callback(status: str) -> None:
        print(f"  RDS status: {status}...")

    # Step 1: Start RDS instance
    print("\n1. Starting RDS instance...")
    rds_status = rds.get_status(rds_id)
    if rds_status:
        if rds_status["status"] == "available":
            print("   RDS instance already running")
        elif rds_status["status"] == "stopped":
            if rds.start(rds_id):
                print("   RDS start initiated...")
                if args.wait:
                    print("   Waiting for RDS to become available (this may take 5-10 minutes)...")
                    if rds.wait_for_status(rds_id, "available", status_callback=rds_status_callback):
                        print("   RDS is now available")
                    else:
                        print("   Warning: Timeout waiting for RDS", file=sys.stderr)
            else:
                print("   Warning: Failed to start RDS instance", file=sys.stderr)
        else:
            print(f"   RDS in state: {rds_status['status']} - waiting...")
            if args.wait:
                if rds.wait_for_status(rds_id, "available", status_callback=rds_status_callback):
                    print("   RDS is now available")
    else:
        print("   Warning: Unable to get RDS status", file=sys.stderr)

    # Step 2: Scale ECS services back up
    print("\n2. Scaling ECS services...")
    services = ecs.get_services(cluster_name)

    if not args.wait:
        rds_status = rds.get_status(rds_id)
        if rds_status and rds_status["status"] != "available":
            print(f"   Warning: RDS is not yet available ({rds_status['status']})")
            print("   ECS services may fail health checks until RDS is ready")

    for svc in services:
        # Use configured replicas if available, otherwise default to 1
        target_replicas = configured_replicas.get(svc["name"], 1)
        print(f"   Scaling {svc['name']} to {target_replicas}...")
        if not ecs.scale_service(cluster_name, svc["name"], target_replicas):
            print(f"   Warning: Failed to scale {svc['name']}", file=sys.stderr)
        else:
            print(f"   Scaled {svc['name']} to {target_replicas}")

    print(f"\nEnvironment {args.environment} start initiated.")
    if not args.wait:
        print("Note: Use --wait to wait for RDS before scaling ECS services.")
    return 0


# =============================================================================
# Main
# =============================================================================

def main():
    # Load .env and configure AWS profile
    configure_aws_profile("infra")

    parser = argparse.ArgumentParser(
        description="Manage environment start/stop for cost savings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s status                       Show status of all environments
  %(prog)s myapp-staging status         Show status of specific environment
  %(prog)s myapp-staging stop           Stop the environment
  %(prog)s myapp-staging start          Start the environment
  %(prog)s myapp-staging start --wait   Start and wait for RDS before scaling ECS

Notes:
  - Stopping scales ECS to 0 and stops RDS (data preserved)
  - ElastiCache and ALB cannot be stopped (only deleted)
  - RDS auto-restarts after 7 days if stopped (AWS limitation)
        """,
    )

    # First positional argument: either "status" (for all) or environment name
    parser.add_argument(
        "environment_or_command",
        metavar="environment|status",
        help="Environment name (e.g., myapp-staging) or 'status' for all environments",
    )

    # Second positional argument: command (optional if first arg is "status")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["start", "stop", "status"],
        help="Command to run: start, stop, or status",
    )

    # Options
    parser.add_argument(
        "--wait", "-w",
        action="store_true",
        help="Wait for RDS to become available before scaling ECS (with 'start' command)",
    )

    args = parser.parse_args()

    # Handle the two usage patterns:
    # 1. "status [--staging]" - show all/staging environments
    # 2. "<environment> <command>" - operate on specific environment
    if args.environment_or_command == "status":
        # Global status command
        args.environment = None
        args.command = "status"
    elif args.command is None:
        # No command specified after environment
        parser.error(f"Missing command. Usage: {parser.prog} {args.environment_or_command} start|stop|status")
    else:
        # Environment + command
        args.environment = args.environment_or_command

    # Dispatch to command handler
    commands = {
        "status": cmd_status,
        "stop": cmd_stop,
        "start": cmd_start,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
