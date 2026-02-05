#!/usr/bin/env python3
"""
Production monitoring tool for read-only diagnostics.

Provides safe, read-only commands to check environment health and status.
All commands are safe to run at any time - they do not modify production state.

Usage:
    # Run full audit (all checks)
    uv run python bin/ops.py myapp-production audit

    # View current state
    uv run python bin/ops.py myapp-production status

    # Check ALB target health
    uv run python bin/ops.py myapp-production health

    # Scan recent logs for errors
    uv run python bin/ops.py myapp-production logs --minutes 60

    # Check pending maintenance
    uv run python bin/ops.py myapp-production maintenance

    # Check ECR vulnerability findings
    uv run python bin/ops.py myapp-production ecr
"""

import argparse
import sys
from datetime import datetime

from deployer.aws import rds
from deployer.core import (
    get_cluster_name_from_config,
    get_rds_instance_id_from_config,
    get_target_group_arn_from_config,
    load_environment_config,
)
from deployer.emergency import (
    get_all_pending_maintenance,
    get_all_services_state,
    get_image_scan_findings,
    get_log_groups_for_environment,
    get_rds_snapshots,
    get_repository_scan_summary,
    get_target_health,
    list_repositories_for_environment,
    list_task_definition_revisions,
    scan_logs_for_errors,
)
from deployer.utils import (
    Colors,
    configure_aws_profile_for_environment,
    get_environment_path,
    log_error,
    log_info,
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
    print(f"{Colors.BLUE}Production Audit: {args.environment}{Colors.NC}")
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
        description="Production monitoring tool (read-only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  status        Show current state (services, task definitions, RDS, snapshots)
  health        Check ALB target health
  logs          Scan recent logs for errors
  maintenance   Show pending RDS/ElastiCache maintenance
  ecr           Show ECR vulnerability findings
  audit         Run all checks (status, health, logs, maintenance, ecr)

Examples:
  %(prog)s myapp-production status
  %(prog)s myapp-production health
  %(prog)s myapp-production logs --minutes 30
  %(prog)s myapp-production audit

All commands are read-only and safe to run at any time.
For commands that modify production, see bin/emergency.py.
        """,
    )

    parser.add_argument(
        "environment",
        help="Environment name (e.g., myapp-production)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # status command
    subparsers.add_parser("status", help="Show current environment state")

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

    # Show banner
    print()
    print(f"{Colors.CYAN}ops.py: Production monitoring tool (read-only){Colors.NC}")

    # Configure AWS profile - use infra profile for broader read access
    configure_aws_profile_for_environment("infra", args.environment)
    print()

    # Dispatch to command handler
    commands = {
        "status": cmd_status,
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
