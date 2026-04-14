#!/usr/bin/env python3
"""
Check ECS services for OOM kills since last deployment.

Queries ECS stopped tasks and CloudWatch Logs for out-of-memory events,
then reports which services have experienced OOM kills and recommends
memory increases.

Usage:
    # Check all environments
    uv run bin/capacity-report.py

    # Check a specific environment
    uv run bin/capacity-report.py myapp-staging

Requires:
    - AWS credentials configured
"""

import sys
from datetime import datetime, timedelta, timezone

import boto3
import click

from deployer.aws import ecs
from deployer.aws.cloudwatch import search_logs_for_oom
from deployer.aws.ecs import get_oom_events
from deployer.core.config import load_environment_config
from deployer.deploy.task_definition import FARGATE_VALID_MEMORY
from deployer.utils import (
    Colors,
    configure_aws_profile,
    get_all_environments,
    get_environment_path,
    get_environments_dir,
)


def _recommend_memory(cpu_allocated: int, current_memory: int) -> int | None:
    """Recommend memory after OOM kills (1.5x current, Fargate-compatible)."""
    if cpu_allocated not in FARGATE_VALID_MEMORY:
        return None

    valid_values = FARGATE_VALID_MEMORY[cpu_allocated]
    target = int(current_memory * 1.5)

    for v in valid_values:
        if v >= target:
            return v

    return valid_values[-1]


def check_environment(  # noqa: C901 — checks OOM across all services
    _env_name: str, env_path, days: int
) -> int:
    """Check a single environment for OOM kills. Returns 1 if OOM found."""
    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"  Error loading config: {e}", file=sys.stderr)
        return 1

    cluster_name = config.get("infrastructure", {}).get("cluster_name")
    if not cluster_name:
        print("  Unable to determine ECS cluster name", file=sys.stderr)
        return 1

    ecs_client = boto3.client("ecs")

    # Get services in cluster
    services = ecs.get_services(cluster_name, ecs_client)
    if not services:
        print(f"  No services found in cluster {cluster_name}", file=sys.stderr)
        return 1

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    # Check each service for OOM kills
    total_oom = 0

    for service in services:
        service_name = service["name"]
        task_def_arn = service["task_definition"]
        last_deployment_at = service.get("last_deployment_at")

        # Parse deployment time for OOM filtering
        deployment_cutoff = None
        if last_deployment_at:
            if isinstance(last_deployment_at, str):
                deployment_cutoff = datetime.fromisoformat(
                    last_deployment_at.replace("Z", "+00:00")
                )
            else:
                deployment_cutoff = last_deployment_at

        # Get allocated resources for recommendations
        try:
            response = ecs_client.describe_task_definition(taskDefinition=task_def_arn)
            task_def = response["taskDefinition"]
            cpu_allocated = int(task_def.get("cpu", 256))
            memory_allocated = int(task_def.get("memory", 512))
        except Exception:
            cpu_allocated, memory_allocated = 256, 512

        # Check ECS stopped tasks for OOM
        oom_events = get_oom_events(
            cluster_name,
            service_name,
            since_hours=days * 24,
            since_datetime=deployment_cutoff,
            ecs_client=ecs_client,
        )

        # Format deployment time
        deploy_str = "unknown"
        if deployment_cutoff:
            deploy_str = deployment_cutoff.strftime("%Y-%m-%d %H:%M UTC")

        if oom_events:
            total_oom += len(oom_events)
            rec = _recommend_memory(cpu_allocated, memory_allocated)
            rec_str = f" (recommend >= {rec}MB)" if rec else ""
            print(
                f"  {Colors.RED}{service_name}: {len(oom_events)} OOM kill(s) "
                f"since deploy ({deploy_str}). "
                f"Current memory: {memory_allocated}MB{rec_str}{Colors.NC}"
            )
        else:
            print(f"  {service_name}: no OOM kills since deploy ({deploy_str})")

    # Also search CloudWatch Logs for OOM events not captured by ECS
    log_group = f"/ecs/{cluster_name.replace('-cluster', '')}"
    logs_client = boto3.client("logs")
    end_time_ms = int(end_time.timestamp() * 1000)

    for service in services:
        service_name = service["name"]
        last_deployment_at = service.get("last_deployment_at")

        if last_deployment_at:
            try:
                if isinstance(last_deployment_at, str):
                    dt = datetime.fromisoformat(last_deployment_at.replace("Z", "+00:00"))
                else:
                    dt = last_deployment_at
                svc_start_ms = int(dt.timestamp() * 1000)
            except (ValueError, AttributeError):
                svc_start_ms = int(start_time.timestamp() * 1000)
        else:
            svc_start_ms = int(start_time.timestamp() * 1000)

        log_oom_events = search_logs_for_oom(
            log_group,
            svc_start_ms,
            end_time_ms,
            cloudwatch_client=logs_client,
            log_stream_prefix=f"{service_name}/",
        )

        if log_oom_events:
            total_oom += len(log_oom_events)
            print(
                f"  {Colors.RED}{service_name}: {len(log_oom_events)} OOM event(s) "
                f"in CloudWatch Logs{Colors.NC}"
            )

    print()
    if total_oom > 0:
        print(f"{Colors.RED}Total: {total_oom} OOM event(s) found{Colors.NC}")
    else:
        print("No OOM events found")
        print("  (Note: ECS only retains stopped tasks for ~1 hour)")

    return 1 if total_oom > 0 else 0


# pysmelly: ignore shotgun-surgery — Click's @click.command() pattern inherently spans files
@click.command()
@click.argument("environment", required=False)
@click.option("--days", "-d", type=int, default=7, help="Number of days to check (default: 7)")
def cli(environment, days):
    """Check ECS services for OOM kills since last deployment.

    \b
    Examples:
      capacity-report.py                         Check all environments
      capacity-report.py myapp-staging            Check specific environment
      capacity-report.py myapp-staging --days 14  Check last 14 days
    """
    configure_aws_profile("infra")

    environments = [environment] if environment else get_all_environments(get_environments_dir())

    if not environments:
        print("No environments found.", file=sys.stderr)
        sys.exit(1)

    exit_code = 0

    for env_name in environments:
        env_path = get_environment_path(env_name)

        print()
        # pysmelly: ignore duplicate-blocks — environment header pattern shared with environment.py
        print(f"{'=' * 60}")
        print(f"Environment: {env_name} (last {days} days)")
        print(f"{'=' * 60}")

        if not env_path.exists():
            print("  Directory not found")
            continue

        state_file = env_path / "terraform.tfstate"
        if not state_file.exists():
            print("  Status: Not deployed")
            continue

        print()
        if check_environment(env_name, env_path, days) != 0:
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    cli()
