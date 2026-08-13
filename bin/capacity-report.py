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
from datetime import UTC, datetime, timedelta

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
    get_environments_dir,
    iter_deployed_environments,
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


def _deployment_cutoff(last_deployment_at) -> datetime | None:
    """Parse a service's last-deployment timestamp.

    Args:
        last_deployment_at: An ISO-8601 string, a datetime, or None.

    Returns:
        The parsed datetime, or None if absent or unparseable. Callers choose
        their own fallback — they differ.
    """
    if not last_deployment_at:
        return None

    if isinstance(last_deployment_at, datetime):
        return last_deployment_at

    try:
        return datetime.fromisoformat(str(last_deployment_at).replace("Z", "+00:00"))
    except ValueError:
        return None


def _count_ecs_oom(services, cluster_name: str, ecs_client, days: int) -> int:
    """Report ECS stopped-task OOM kills per service, and return the total.

    Args:
        services: Services in the cluster.
        cluster_name: ECS cluster name.
        ecs_client: boto3 ECS client.
        days: Look-back window, used when a service has no deployment time.

    Returns:
        Total OOM kills found across all services.
    """
    total_oom = 0

    for service in services:
        cutoff = _deployment_cutoff(service.last_deployment_at)

        try:
            response = ecs_client.describe_task_definition(taskDefinition=service.task_definition)
            task_def = response["taskDefinition"]
            cpu_allocated = int(task_def.get("cpu", 256))
            memory_allocated = int(task_def.get("memory", 512))
        except Exception:  # noqa: BLE001 — a missing task definition only costs the recommendation
            cpu_allocated, memory_allocated = 256, 512

        oom_events = get_oom_events(
            cluster_name,
            service.name,
            since_hours=days * 24,
            since_datetime=cutoff,
            ecs_client=ecs_client,
        )

        deploy_str = cutoff.strftime("%Y-%m-%d %H:%M UTC") if cutoff else "unknown"

        if not oom_events:
            print(f"  {service.name}: no OOM kills since deploy ({deploy_str})")
            continue

        total_oom += len(oom_events)
        rec = _recommend_memory(cpu_allocated, memory_allocated)
        rec_str = f" (recommend >= {rec}MB)" if rec else ""
        print(
            f"  {Colors.RED}{service.name}: {len(oom_events)} OOM kill(s) "
            f"since deploy ({deploy_str}). "
            f"Current memory: {memory_allocated}MB{rec_str}{Colors.NC}"
        )

    return total_oom


def _count_log_oom(services, log_group: str, logs_client, start_time, end_time_ms: int) -> int:
    """Report CloudWatch Logs OOM events per service, and return the total.

    These are events ECS did not capture as stopped tasks.

    Args:
        services: Services in the cluster.
        log_group: CloudWatch log group to search.
        logs_client: boto3 CloudWatch Logs client.
        start_time: Window start, used when a service has no deployment time.
        end_time_ms: Window end, in epoch milliseconds.

    Returns:
        Total OOM log events found across all services.
    """
    total_oom = 0

    for service in services:
        cutoff = _deployment_cutoff(service.last_deployment_at)
        svc_start = cutoff or start_time

        log_oom_events = search_logs_for_oom(
            log_group,
            int(svc_start.timestamp() * 1000),
            end_time_ms,
            cloudwatch_client=logs_client,
            log_stream_prefix=f"{service.name}/",
        )

        if log_oom_events:
            total_oom += len(log_oom_events)
            print(
                f"  {Colors.RED}{service.name}: {len(log_oom_events)} OOM event(s) "
                f"in CloudWatch Logs{Colors.NC}"
            )

    return total_oom


def check_environment(_env_name: str, env_path, days: int) -> int:
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

    services = ecs.get_services(cluster_name, ecs_client)
    if not services:
        print(f"  No services found in cluster {cluster_name}", file=sys.stderr)
        return 1

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(days=days)

    total_oom = _count_ecs_oom(services, cluster_name, ecs_client, days)
    total_oom += _count_log_oom(
        services,
        f"/ecs/{cluster_name.replace('-cluster', '')}",
        boto3.client("logs"),
        start_time,
        int(end_time.timestamp() * 1000),
    )

    print()
    if total_oom > 0:
        print(f"{Colors.RED}Total: {total_oom} OOM event(s) found{Colors.NC}")
    else:
        print("No OOM events found")
        print("  (Note: ECS only retains stopped tasks for ~1 hour)")

    return 1 if total_oom > 0 else 0


# pysmelly: ignore shotgun-surgery — Click's @click.command() pattern inherently spans files  (re-evaluate-by: 2026-11 review)
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

    for env_name, env_path in iter_deployed_environments(
        environments, header_suffix=f" (last {days} days)"
    ):
        print()
        if check_environment(env_name, env_path, days) != 0:
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    cli()
