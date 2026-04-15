#!/usr/bin/env python3
"""
Manage environment start/stop for cost savings.

Stops ECS services and RDS instances during off-hours while preserving all data.
ElastiCache and ALB continue running (cannot be stopped without deletion).

Usage:
    # Show current state of all environments
    python bin/environment.py status

    # Show status of a specific environment
    python bin/environment.py status myapp-staging

    # Stop an environment (scale ECS to 0, stop RDS)
    python bin/environment.py stop myapp-staging

    # Start an environment (waits for RDS, then scales ECS)
    python bin/environment.py start myapp-staging
"""

import sys

import click

from deployer.aws import ecs, rds
from deployer.core.config import (
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


def _load_environment_context(environment: str) -> tuple[dict, str, str]:
    """Load config and extract cluster_name and rds_id.

    Validates that the environment is deployed and both ECS cluster
    and RDS instance are configured. Exits on failure.

    Returns:
        Tuple of (config, cluster_name, rds_id).
    """
    env_path, error = validate_environment_deployed(environment)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)

    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        raise SystemExit(1) from None

    cluster_name = config.get("infrastructure", {}).get("cluster_name")
    rds_id = config.get("infrastructure", {}).get("rds_instance_id")

    if not cluster_name:
        print("Error: Unable to determine ECS cluster name", file=sys.stderr)
        raise SystemExit(1)

    if not rds_id:
        print("Error: Unable to determine RDS instance ID", file=sys.stderr)
        raise SystemExit(1)

    return config, cluster_name, rds_id


# =============================================================================
# Commands
# =============================================================================


def cmd_status(environment: str | None) -> int:
    """Show status of environments."""
    environments = [environment] if environment else get_all_environments(get_environments_dir())

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
        cluster_name = config.get("infrastructure", {}).get("cluster_name")
        if cluster_name:
            print(f"\n  ECS Cluster: {cluster_name}")
            services = ecs.get_services(cluster_name)
            if services:
                print(f"  {'Service':<30} {'Desired':<10} {'Running':<10} {'Status':<15}")
                print(f"  {'-' * 30} {'-' * 10} {'-' * 10} {'-' * 15}")
                for svc in services:
                    print(
                        f"  {svc.name:<30} {svc.desired_count:<10} {svc.running_count:<10} {svc.status:<15}"
                    )
            else:
                print("  No ECS services found")
        else:
            print("  ECS: Unable to determine cluster name")

        # RDS Status
        rds_id = config.get("infrastructure", {}).get("rds_instance_id")
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


def cmd_stop(environment: str) -> int:
    """Stop an environment."""
    _config, cluster_name, rds_id = _load_environment_context(environment)
    print(f"Stopping environment: {environment}")

    # Step 1: Scale ECS services to 0
    print("\n1. Scaling ECS services to 0...")
    services = ecs.get_services(cluster_name)
    for svc in services:
        print(f"   Scaling {svc.name} to 0...")
        if not ecs.scale_service(cluster_name, svc.name, 0):
            print(f"   Warning: Failed to scale {svc.name}", file=sys.stderr)
        else:
            print(f"   Scaled {svc.name} to 0")

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

    print(f"\nEnvironment {environment} stop initiated.")
    print("Note: ElastiCache and ALB continue running (cannot be stopped).")
    return 0


def _ensure_rds_available(rds_id: str) -> None:
    """Start RDS instance and wait for it to become available."""

    def status_callback(status: str) -> None:
        print(f"  RDS status: {status}...")

    rds_status = rds.get_status(rds_id)
    if not rds_status:
        print("   Warning: Unable to get RDS status", file=sys.stderr)
        return

    current = rds_status["status"]
    if current == "available":
        print("   RDS instance already running")
        return

    if current == "stopping":
        print("   RDS is currently stopping, waiting for it to stop first...")
        if not rds.wait_for_status(rds_id, "stopped", status_callback=status_callback):
            print("   Warning: Timeout waiting for RDS to stop", file=sys.stderr)
            return
        print("   RDS stopped, now starting...")
        current = "stopped"

    if current == "stopped":
        if not rds.start(rds_id):
            print("   Warning: Failed to start RDS instance", file=sys.stderr)
            return
        print("   RDS start initiated...")
    else:
        print(f"   RDS in state: {current}, waiting for available...")

    print("   Waiting for RDS to become available...")
    if rds.wait_for_status(rds_id, "available", status_callback=status_callback):
        print("   RDS is now available")
    else:
        print("   Warning: Timeout waiting for RDS", file=sys.stderr)


def cmd_start(environment: str) -> int:
    """Start an environment."""
    config, cluster_name, rds_id = _load_environment_context(environment)
    print(f"Starting environment: {environment}")

    # Get configured replica counts from config
    configured_replicas = get_service_replicas_from_config(config)

    # Step 1: Start RDS instance and wait for it to be available
    print("\n1. Starting RDS instance...")
    _ensure_rds_available(rds_id)

    # Step 2: Scale ECS services back up
    print("\n2. Scaling ECS services...")
    services = ecs.get_services(cluster_name)

    for svc in services:
        # Use configured replicas if available, otherwise default to 1
        target_replicas = configured_replicas.get(svc.name, 1)
        print(f"   Scaling {svc.name} to {target_replicas}...")
        if not ecs.scale_service(cluster_name, svc.name, target_replicas):
            print(f"   Warning: Failed to scale {svc.name}", file=sys.stderr)
        else:
            print(f"   Scaled {svc.name} to {target_replicas}")

    print(f"\nEnvironment {environment} started.")
    return 0


# =============================================================================
# CLI
# =============================================================================


@click.group()
def cli():
    """Manage environment start/stop for cost savings.

    \b
    Notes:
      - Stopping scales ECS to 0 and stops RDS (data preserved)
      - Starting waits for RDS to be available before scaling ECS
      - ElastiCache and ALB cannot be stopped (only deleted)
      - RDS auto-restarts after 7 days if stopped (AWS limitation)
    """
    # Load .env and configure AWS profile
    configure_aws_profile("infra")


@cli.command()
@click.argument("environment", required=False)
def status(environment):
    """Show environment status."""
    sys.exit(cmd_status(environment))


@cli.command()
@click.argument("environment")
def stop(environment):
    """Stop an environment (scale ECS to 0, stop RDS)."""
    sys.exit(cmd_stop(environment))


@cli.command()
@click.argument("environment")
def start(environment):
    """Start an environment (waits for RDS, then scales ECS)."""
    sys.exit(cmd_start(environment))


if __name__ == "__main__":
    cli()
