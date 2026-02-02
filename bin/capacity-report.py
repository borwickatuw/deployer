#!/usr/bin/env python3
"""
Generate ECS capacity utilization report for right-sizing analysis.

This script queries CloudWatch Container Insights metrics and compares actual
utilization against allocated resources to identify:
- Over-provisioned services (wasting money)
- Under-provisioned services (performance risk)
- Bursty workloads (high p95 but low average)

When a tfvars file is provided, it also compares the current tfvars configuration
against running infrastructure and recommendations, showing what changes are needed.

Usage:
    # Show capacity report for all environments
    uv run bin/capacity-report.py

    # Show capacity report for a specific environment
    uv run bin/capacity-report.py myapp-staging

    # Analyze 14 days of data
    uv run bin/capacity-report.py myapp-staging --days 14

    # Output as JSON
    uv run bin/capacity-report.py myapp-staging --format json

    # Compare against tfvars file
    uv run bin/capacity-report.py myapp-staging --tfvars environments/myapp-staging/terraform.tfvars

Requires:
    - AWS credentials configured
    - Container Insights enabled on ECS cluster (already configured in ecs-cluster module)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3

from deployer.aws import cloudwatch, ecs
from deployer.aws.ecs import get_oom_events
from deployer.aws.cloudwatch import search_logs_for_oom
from deployer.config import TfvarsService, parse_tfvars
from deployer.core import (
    ServiceMetrics,
    calculate_percentile,
    classify_service,
    estimate_savings,
    generate_suggested_tfvars,
    generate_tfvars_diff,
    get_cluster_name_from_config,
    get_recommended_cpu,
    get_recommended_memory,
    load_environment_config,
)
from deployer.utils import Colors, configure_aws_profile, get_all_environments, get_environment_path, get_environments_dir


# Fargate-compatible memory values by CPU allocation
FARGATE_MEMORY_BY_CPU = {
    256: [512, 1024, 2048],
    512: [1024, 2048, 3072, 4096],
    1024: [2048, 3072, 4096, 5120, 6144, 7168, 8192],
    2048: list(range(4096, 16385, 1024)),
    4096: list(range(8192, 30721, 1024)),
}


def calculate_oom_memory_recommendation(cpu_allocated: int, current_memory: int) -> int:
    """Calculate recommended memory after OOM kills.

    For OOM kills, recommends at least 1.5x current memory using valid Fargate values.

    Args:
        cpu_allocated: Current CPU allocation in vCPU units.
        current_memory: Current memory allocation in MB.

    Returns:
        Recommended memory value in MB (Fargate-compatible).
    """
    valid_values = FARGATE_MEMORY_BY_CPU.get(cpu_allocated, [512, 1024, 2048, 4096])
    target = int(current_memory * 1.5)

    for v in valid_values:
        if v >= target:
            return v

    # Use highest available if target exceeds all options
    return valid_values[-1] if valid_values else current_memory * 2


def format_table_row(
    name: str,
    cpu_alloc: int,
    cpu_avg: float,
    cpu_p95: float,
    mem_alloc: int,
    mem_avg: float,
    mem_p95: float,
    status: str,
) -> str:
    """Format a single table row."""
    status_icons = {
        "OK": f"{Colors.GREEN}OK{Colors.NC}",
        "OVER_PROVISIONED": f"{Colors.YELLOW}OVER-PROVISIONED{Colors.NC}",
        "UNDER_PROVISIONED": f"{Colors.RED}UNDER-PROVISIONED{Colors.NC}",
        "BURSTY": f"{Colors.CYAN}OK (bursty){Colors.NC}",
        "OOM_KILLS": f"{Colors.RED}OOM KILLS DETECTED{Colors.NC}",
    }

    return (
        f"{name:<14} {cpu_alloc:<10} {cpu_avg:>6.0f}%    {cpu_p95:>6.0f}%    "
        f"{mem_alloc:<14} {mem_avg:>6.0f}%    {mem_p95:>6.0f}%    {status_icons.get(status, status)}"
    )


def _build_recommendations(services: list[ServiceMetrics]) -> list[str]:
    """Build list of recommendation strings for services."""
    recommendations = []
    for svc in services:
        if svc.status == "OOM_KILLS":
            recommendations.append(
                f"  {Colors.RED}{svc.service_name}: {svc.oom_kill_count} OOM kill(s) detected! "
                f"Increase memory from {svc.memory_allocated}MB "
                f"(recommend at least {svc.memory_recommendation or svc.memory_allocated * 2}MB){Colors.NC}"
            )
        elif svc.status == "OVER_PROVISIONED":
            parts = []
            if svc.cpu_recommendation:
                parts.append(f"cpu={svc.cpu_recommendation}")
            if svc.memory_recommendation:
                parts.append(f"memory={svc.memory_recommendation}")
            if parts:
                recommendations.append(f"  {svc.service_name}: Consider reducing to {', '.join(parts)}")
        elif svc.status == "UNDER_PROVISIONED":
            parts = []
            if svc.cpu_recommendation:
                parts.append(f"cpu={svc.cpu_recommendation}")
            if svc.memory_recommendation:
                parts.append(f"memory={svc.memory_recommendation}")
            if parts:
                recommendations.append(f"  {svc.service_name}: Consider increasing to {', '.join(parts)}")
    return recommendations


def _print_zero_utilization_warning(services: list[ServiceMetrics]) -> None:
    """Print warning if all services show zero utilization."""
    all_zero = all(svc.cpu_avg == 0 and svc.memory_avg == 0 for svc in services)
    if all_zero:
        print(f"{Colors.YELLOW}Note: All services show 0% utilization. Services may have been stopped during this period.")
        print(f"      'OVER-PROVISIONED' status may be inaccurate. Check CloudWatch or Celery Results for OOM errors.{Colors.NC}")
        print()


def _print_oom_summary(services: list[ServiceMetrics]) -> None:
    """Print OOM detection summary."""
    total_oom = sum(svc.oom_kill_count for svc in services)
    if total_oom > 0:
        print(f"{Colors.RED}OOM Detection: {total_oom} OOM kill(s) found in ECS stopped tasks{Colors.NC}")
    else:
        print(f"OOM Detection: No recent OOM kills found in ECS (note: ECS only retains stopped tasks for ~1 hour)")
    print()


def _print_tfvars_comparison(
    services: list[ServiceMetrics],
    tfvars_config: dict[str, TfvarsService],
) -> None:
    """Print tfvars comparison section."""
    print(f"{Colors.BOLD}tfvars Comparison:{Colors.NC}")
    diffs = generate_tfvars_diff(services, tfvars_config)
    if diffs:
        for diff in diffs:
            print(diff)
        print()
        print(f"{Colors.BOLD}Suggested tfvars:{Colors.NC}")
        print(generate_suggested_tfvars(services, tfvars_config))
        print()
    else:
        print("  No differences between tfvars and recommendations.")
        print()


def print_text_report(
    services: list[ServiceMetrics],
    cluster_name: str,
    start_time: datetime,
    end_time: datetime,
    tfvars_config: dict[str, TfvarsService] | None = None,
) -> None:
    """Print human-readable report to stdout."""
    # Header and rows
    header = (
        f"{'Service':<14} {'CPU Alloc':<10} {'CPU Avg':<10} {'CPU p95':<10} "
        f"{'Memory Alloc':<14} {'Mem Avg':<10} {'Mem p95':<10} Status"
    )
    print(header)
    print("-" * 110)

    for svc in sorted(services, key=lambda s: s.service_name):
        print(format_table_row(
            svc.service_name, svc.cpu_allocated, svc.cpu_avg, svc.cpu_p95,
            svc.memory_allocated, svc.memory_avg, svc.memory_p95, svc.status,
        ))
    print()

    _print_zero_utilization_warning(services)
    _print_oom_summary(services)

    recommendations = _build_recommendations(services)
    if recommendations:
        print(f"{Colors.BOLD}Recommendations:{Colors.NC}")
        for rec in recommendations:
            print(rec)
        print()

    savings = estimate_savings(services)
    if savings > 0:
        print(f"Estimated monthly savings from right-sizing: ~${savings:.0f}")
        print()

    if tfvars_config:
        _print_tfvars_comparison(services, tfvars_config)


def print_json_report(
    services: list[ServiceMetrics],
    cluster_name: str,
    start_time: datetime,
    end_time: datetime,
    tfvars_config: dict[str, TfvarsService] | None = None,
) -> None:
    """Print JSON report for automation."""
    report: dict[str, Any] = {
        "cluster": cluster_name,
        "period": {
            "start": start_time.isoformat(),
            "end": end_time.isoformat(),
            "days": (end_time - start_time).days,
        },
        "services": [],
        "estimated_monthly_savings_usd": round(estimate_savings(services), 2),
    }

    for svc in sorted(services, key=lambda s: s.service_name):
        tfvars = tfvars_config.get(svc.service_name) if tfvars_config else None

        service_data: dict[str, Any] = {
            "name": svc.service_name,
            "cpu": {
                "allocated": svc.cpu_allocated,
                "avg_percent": round(svc.cpu_avg, 1),
                "p95_percent": round(svc.cpu_p95, 1),
                "max_percent": round(svc.cpu_max, 1),
                "recommendation": svc.cpu_recommendation,
            },
            "memory": {
                "allocated_mb": svc.memory_allocated,
                "avg_percent": round(svc.memory_avg, 1),
                "p95_percent": round(svc.memory_p95, 1),
                "max_percent": round(svc.memory_max, 1),
                "recommendation_mb": svc.memory_recommendation,
            },
            "status": svc.status,
            "oom_kills": {
                "count": svc.oom_kill_count,
                "events": svc.oom_events,
            } if svc.oom_kill_count > 0 else None,
        }

        if tfvars:
            service_data["tfvars"] = {
                "cpu": tfvars.cpu,
                "memory": tfvars.memory,
                "replicas": tfvars.replicas,
                "cpu_differs": tfvars.cpu != svc.cpu_allocated,
                "memory_differs": tfvars.memory != svc.memory_allocated,
                "cpu_recommendation_differs": (
                    svc.cpu_recommendation is not None
                    and svc.cpu_recommendation != tfvars.cpu
                ),
                "memory_recommendation_differs": (
                    svc.memory_recommendation is not None
                    and svc.memory_recommendation != tfvars.memory
                ),
            }

        report["services"].append(service_data)

    if tfvars_config:
        # Include suggested tfvars as a string
        report["suggested_tfvars"] = generate_suggested_tfvars(services, tfvars_config)

    print(json.dumps(report, indent=2))


def _collect_service_metrics(
    service: dict,
    cluster_name: str,
    start_time: datetime,
    end_time: datetime,
    days: int,
    ecs_client: Any,
    cloudwatch_client: Any,
    tfvars_config: dict[str, TfvarsService] | None,
) -> ServiceMetrics:
    """Collect metrics for a single service."""
    service_name = service["name"]
    task_def_arn = service["task_definition"]

    # Get allocated resources
    cpu_allocated, memory_allocated = ecs.get_task_definition_resources(task_def_arn, ecs_client)

    # Get CPU and memory metrics
    cpu_values = cloudwatch.get_container_insights_metrics(
        cloudwatch_client, cluster_name, service_name, "CpuUtilized", start_time, end_time,
    )
    memory_values = cloudwatch.get_container_insights_metrics(
        cloudwatch_client, cluster_name, service_name, "MemoryUtilized", start_time, end_time,
    )

    # Calculate statistics
    cpu_avg = sum(cpu_values) / len(cpu_values) if cpu_values else 0
    cpu_p95 = calculate_percentile(cpu_values, 95)
    cpu_max = max(cpu_values) if cpu_values else 0
    memory_avg = sum(memory_values) / len(memory_values) if memory_values else 0
    memory_p95 = calculate_percentile(memory_values, 95)
    memory_max = max(memory_values) if memory_values else 0

    # Get tfvars values if available
    tfvars_cpu, tfvars_memory, tfvars_replicas = None, None, None
    if tfvars_config and service_name in tfvars_config:
        tfvars_cpu = tfvars_config[service_name].cpu
        tfvars_memory = tfvars_config[service_name].memory
        tfvars_replicas = tfvars_config[service_name].replicas

    # Check for OOM kills
    oom_events = get_oom_events(cluster_name, service_name, since_hours=days * 24, ecs_client=ecs_client)
    if oom_events:
        print(f"  Found {len(oom_events)} OOM event(s) for {service_name} (from ECS stopped tasks)")

    metrics = ServiceMetrics(
        service_name=service_name,
        cpu_allocated=cpu_allocated,
        memory_allocated=memory_allocated,
        cpu_avg=cpu_avg,
        cpu_p95=cpu_p95,
        cpu_max=cpu_max,
        memory_avg=memory_avg,
        memory_p95=memory_p95,
        memory_max=memory_max,
        status="OK",
        tfvars_cpu=tfvars_cpu,
        tfvars_memory=tfvars_memory,
        tfvars_replicas=tfvars_replicas,
        oom_kill_count=len(oom_events),
        oom_events=oom_events if oom_events else None,
    )

    # Classify and add recommendations
    metrics.status = classify_service(metrics)
    if metrics.status in ("OVER_PROVISIONED", "UNDER_PROVISIONED"):
        metrics.cpu_recommendation = get_recommended_cpu(cpu_allocated, cpu_avg, cpu_p95)
        metrics.memory_recommendation = get_recommended_memory(memory_allocated, memory_avg, memory_p95, cpu_allocated)
    elif metrics.status == "OOM_KILLS":
        metrics.memory_recommendation = calculate_oom_memory_recommendation(cpu_allocated, memory_allocated)

    return metrics


def _search_cloudwatch_logs_for_oom(
    service_metrics: list[ServiceMetrics],
    cluster_name: str,
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Search CloudWatch Logs for OOM events and update service metrics in place."""
    log_group = f"/ecs/{cluster_name.replace('-cluster', '')}"
    start_time_ms = int(start_time.timestamp() * 1000)
    end_time_ms = int(end_time.timestamp() * 1000)

    print(f"\nSearching CloudWatch Logs for OOM events...")
    print(f"  Log group: {log_group}")

    logs_client = boto3.client("logs")

    for svc in service_metrics:
        if svc.oom_kill_count > 0:
            continue  # Already found OOM events from ECS

        log_stream_prefix = f"{svc.service_name}/"
        service_oom_events = search_logs_for_oom(
            log_group, start_time_ms, end_time_ms,
            cloudwatch_client=logs_client,
            log_stream_prefix=log_stream_prefix,
        )

        if service_oom_events:
            print(f"  Found {len(service_oom_events)} OOM event(s) for {svc.service_name} (from CloudWatch Logs)")
            svc.oom_kill_count = len(service_oom_events)
            svc.oom_events = service_oom_events
            svc.status = classify_service(svc)
            if svc.status == "OOM_KILLS":
                svc.memory_recommendation = calculate_oom_memory_recommendation(svc.cpu_allocated, svc.memory_allocated)

    total_oom = sum(svc.oom_kill_count for svc in service_metrics)
    if total_oom > 0:
        print(f"  Total: {total_oom} OOM-related events found")
    else:
        print(f"  No OOM events found in CloudWatch Logs")
    print()


def generate_report_for_environment(
    env_name: str,
    env_path: Path,
    days: int,
    output_format: str,
    tfvars_path: Path | None,
    dry_run: bool,
) -> int:
    """Generate capacity report for a single environment."""
    # Load config
    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"  Error loading config: {e}", file=sys.stderr)
        return 1

    cluster_name = get_cluster_name_from_config(config)
    if not cluster_name:
        print(f"  Unable to determine ECS cluster name", file=sys.stderr)
        return 1

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    # Load tfvars if provided
    tfvars_config: dict[str, TfvarsService] | None = None
    if tfvars_path:
        if not tfvars_path.exists():
            print(f"  tfvars file not found: {tfvars_path}", file=sys.stderr)
            return 1
        tfvars_config = parse_tfvars(tfvars_path)
        if not tfvars_config:
            print(f"  No services found in tfvars file: {tfvars_path}", file=sys.stderr)
            return 1

    if dry_run:
        print(f"  Cluster: {cluster_name}")
        print(f"  Period: {start_time.isoformat()} to {end_time.isoformat()}")
        print(f"  Metrics: CpuUtilized, MemoryUtilized from ECS/ContainerInsights namespace")
        if tfvars_config:
            print(f"  tfvars file: {tfvars_path}")
            print(f"  Services in tfvars: {', '.join(tfvars_config.keys())}")
        return 0

    # Initialize AWS clients
    ecs_client = boto3.client("ecs")
    cloudwatch_client = boto3.client("cloudwatch")

    # Get services in cluster
    services = ecs.get_services(cluster_name, ecs_client)
    if not services:
        print(f"  No services found in cluster {cluster_name}", file=sys.stderr)
        return 1

    # Collect metrics for each service
    service_metrics = [
        _collect_service_metrics(
            service, cluster_name, start_time, end_time, days,
            ecs_client, cloudwatch_client, tfvars_config,
        )
        for service in services
    ]

    # Search CloudWatch Logs for additional OOM events
    _search_cloudwatch_logs_for_oom(service_metrics, cluster_name, start_time, end_time)

    # Output report
    if output_format == "json":
        print_json_report(service_metrics, cluster_name, start_time, end_time, tfvars_config)
    else:
        print_text_report(service_metrics, cluster_name, start_time, end_time, tfvars_config)

    return 0


def main() -> None:
    # Load .env and configure AWS profile
    configure_aws_profile("infra")

    parser = argparse.ArgumentParser(
        description="Generate ECS capacity utilization report for right-sizing analysis.",
        epilog="""
Examples:
  %(prog)s                              Show report for all environments
  %(prog)s myapp-staging                Show report for specific environment
  %(prog)s myapp-staging --days 14      Analyze 14 days of data
  %(prog)s myapp-staging --format json  Output as JSON
  %(prog)s myapp-staging --tfvars environments/myapp-staging/terraform.tfvars
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "environment",
        nargs="?",
        help="Specific environment (default: all deployed environments)",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=7,
        help="Number of days to analyze (default: 7)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--tfvars",
        "-t",
        type=Path,
        help="Path to terraform.tfvars file to compare against (e.g., environments/myapp-staging/terraform.tfvars)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be queried without making API calls",
    )

    args = parser.parse_args()

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=args.days)

    # Determine which environments to report on
    if args.environment:
        environments = [args.environment]
    else:
        environments = get_all_environments(get_environments_dir())

    if not environments:
        print("No environments found.", file=sys.stderr)
        sys.exit(1)

    exit_code = 0

    for env_name in environments:
        env_path = get_environment_path(env_name)

        print()
        print(f"{'=' * 60}")
        print(f"Environment: {env_name}")
        print(f"Period: {start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')} ({args.days} days)")
        print(f"{'=' * 60}")

        if not env_path.exists():
            print("  Directory not found")
            continue

        state_file = env_path / "terraform.tfstate"
        if not state_file.exists():
            print("  Status: Not deployed")
            continue

        # Determine tfvars path (auto-discover if not provided)
        tfvars_path = args.tfvars
        if not tfvars_path:
            default_tfvars = env_path / "terraform.tfvars"
            if default_tfvars.exists():
                tfvars_path = default_tfvars

        print()
        result = generate_report_for_environment(
            env_name,
            env_path,
            args.days,
            args.format,
            tfvars_path,
            args.dry_run,
        )
        if result != 0:
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
