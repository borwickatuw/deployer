#!/usr/bin/env python3
"""
Production monitoring tool for read-only diagnostics.

Provides safe, read-only commands to check environment health and status.
All commands are safe to run at any time - they do not modify production state.

Usage:
    # Run full audit (all checks)
    uv run python bin/ops.py audit myapp-production

    # View current state
    uv run python bin/ops.py status myapp-production

    # Check ALB target health
    uv run python bin/ops.py health myapp-production

    # Scan recent logs for errors
    uv run python bin/ops.py logs myapp-production --minutes 60

    # Check pending maintenance
    uv run python bin/ops.py maintenance myapp-production

    # Check ECR vulnerability findings
    uv run python bin/ops.py ecr myapp-production
"""

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import click
from botocore.exceptions import ClientError

from deployer.aws import rds
from deployer.core.config import load_environment_config
from deployer.emergency.ecs import (
    get_all_services_state,
    list_task_definition_revisions,
)
from deployer.emergency.rds import get_rds_snapshots
from deployer.utils import (
    Colors,
    configure_aws_profile_for_environment,
    format_iso,
    get_environment_path,
    log_error,
    log_info,
    log_success,
    log_warning,
    validate_environment_deployed,
)

# =============================================================================
# Inlined from emergency/alb.py, emergency/logs.py, emergency/maintenance.py,
# emergency/ecr.py — these modules are only used by this script.
# =============================================================================


def get_target_health(target_group_arn: str) -> list[dict]:
    """Get health status of all targets in a target group."""
    client = boto3.client("elbv2")
    result = []

    try:
        response = client.describe_target_health(TargetGroupArn=target_group_arn)
        for target in response.get("TargetHealthDescriptions", []):
            target_info = target.get("Target", {})
            health = target.get("TargetHealth", {})
            result.append(
                {
                    "target_id": target_info.get("Id", ""),
                    "port": target_info.get("Port", 0),
                    "health_state": health.get("State", "unknown"),
                    "reason": health.get("Reason"),
                    "description": health.get("Description"),
                }
            )
    except ClientError:
        pass

    return result


def scan_logs_for_errors(
    log_group_name: str,
    lookback_minutes: int = 60,
    patterns: list[str] | None = None,
    max_results: int = 100,
) -> list[dict]:
    """Scan CloudWatch logs for error patterns."""
    if patterns is None:
        patterns = ["ERROR", "Exception", "Traceback", "CRITICAL"]

    client = boto3.client("logs")
    result = []
    start_time = int((time.time() - (lookback_minutes * 60)) * 1000)
    filter_pattern = " ".join(f'?"{p}"' for p in patterns)

    try:
        response = client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time,
            filterPattern=filter_pattern,
            limit=max_results,
        )

        for event in response.get("events", []):
            timestamp = event.get("timestamp", 0)
            if timestamp:
                dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
                timestamp = dt.isoformat()

            result.append(
                {
                    "timestamp": timestamp,
                    "message": event.get("message", ""),
                    "log_stream": event.get("logStreamName", ""),
                }
            )

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code != "ResourceNotFoundException":
            raise

    return result


def get_log_groups_for_environment(environment: str) -> list[str]:
    """Get CloudWatch log group names for an environment."""
    client = boto3.client("logs")
    result = []
    prefix = f"/ecs/{environment}"

    try:
        paginator = client.get_paginator("describe_log_groups")
        for page in paginator.paginate(logGroupNamePrefix=prefix):
            for group in page.get("logGroups", []):
                result.append(group.get("logGroupName", ""))
    except ClientError as e:
        print(f"  Warning: Error listing log groups: {e}", file=sys.stderr)

    return result


def get_rds_pending_maintenance(instance_id: str) -> list[dict]:
    """Get pending maintenance actions for an RDS instance."""
    client = boto3.client("rds")
    result = []

    try:
        response = client.describe_db_instances(DBInstanceIdentifier=instance_id)
        instances = response.get("DBInstances", [])
        if not instances:
            return []

        instance_arn = instances[0].get("DBInstanceArn", "")
        response = client.describe_pending_maintenance_actions(ResourceIdentifier=instance_arn)

        for resource in response.get("PendingMaintenanceActions", []):
            for action in resource.get("PendingMaintenanceActionDetails", []):
                auto_apply = format_iso(action.get("AutoAppliedAfterDate"))
                current_apply = format_iso(action.get("CurrentApplyDate"))

                result.append(
                    {
                        "action": action.get("Action", ""),
                        "description": action.get("Description", ""),
                        "auto_apply_after": auto_apply,
                        "current_apply_date": current_apply,
                        "opt_in_status": action.get("OptInStatus", ""),
                    }
                )

    except ClientError:
        pass

    return result


def get_elasticache_pending_maintenance(cluster_id: str) -> list[dict]:
    """Get pending maintenance for an ElastiCache cluster."""
    client = boto3.client("elasticache")
    result = []

    try:
        response = client.describe_cache_clusters(
            CacheClusterId=cluster_id,
            ShowCacheNodeInfo=True,
        )

        clusters = response.get("CacheClusters", [])
        if not clusters:
            return []

        cluster = clusters[0]
        pending = cluster.get("PendingModifiedValues", {})
        if pending:
            for key, value in pending.items():
                if value:
                    result.append(
                        {
                            "action": "modify",
                            "description": f"Pending {key} change to {value}",
                            "severity": None,
                        }
                    )

        try:
            updates_response = client.describe_service_updates(
                ServiceUpdateStatus=["available", "scheduled"],
            )

            for update in updates_response.get("ServiceUpdates", []):
                result.append(
                    {
                        "action": update.get("ServiceUpdateName", ""),
                        "description": update.get("ServiceUpdateDescription", ""),
                        "severity": update.get("ServiceUpdateSeverity", ""),
                    }
                )
        except ClientError:
            pass

    except ClientError:
        pass

    return result


def get_all_pending_maintenance(
    rds_instance_id: str | None,
    elasticache_cluster_id: str | None,
) -> dict:
    """Get all pending maintenance for an environment."""
    result: dict[str, list] = {"rds": [], "elasticache": []}

    if rds_instance_id:
        result["rds"] = get_rds_pending_maintenance(rds_instance_id)

    if elasticache_cluster_id:
        result["elasticache"] = get_elasticache_pending_maintenance(elasticache_cluster_id)

    return result


def get_image_scan_findings(
    repository_name: str,
    image_tag: str = "latest",
    severity_filter: list[str] | None = None,
) -> dict:
    """Get vulnerability scan findings for an ECR image."""
    if severity_filter is None:
        severity_filter = ["CRITICAL", "HIGH"]

    client = boto3.client("ecr")
    result: dict = {
        "image_digest": None,
        "scan_status": None,
        "vulnerability_counts": {},
        "findings": [],
    }

    try:
        response = client.describe_image_scan_findings(
            repositoryName=repository_name,
            imageId={"imageTag": image_tag},
        )

        image_scan = response.get("imageScanFindings", {})
        result["image_digest"] = response.get("imageId", {}).get("imageDigest")
        result["scan_status"] = response.get("imageScanStatus", {}).get("status")
        result["vulnerability_counts"] = image_scan.get("findingSeverityCounts", {})

        for finding in image_scan.get("findings", []):
            severity = finding.get("severity", "")
            if severity in severity_filter:
                result["findings"].append(
                    {
                        "name": finding.get("name", ""),
                        "severity": severity,
                        "description": finding.get("description", ""),
                        "uri": finding.get("uri", ""),
                    }
                )

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ScanNotFoundException":
            result["scan_status"] = "NOT_SCANNED"
        elif error_code == "ImageNotFoundException":
            result["scan_status"] = "IMAGE_NOT_FOUND"
        else:
            result["scan_status"] = f"ERROR: {error_code}"

    return result


def get_repository_scan_summary(repository_name: str, max_images: int = 5) -> list[dict]:
    """Get scan summary for recent images in a repository."""
    client = boto3.client("ecr")
    result = []

    try:
        response = client.describe_images(
            repositoryName=repository_name,
            maxResults=max_images,
        )

        images = sorted(
            response.get("imageDetails", []),
            key=lambda x: x.get("imagePushedAt", ""),
            reverse=True,
        )[:max_images]

        for image in images:
            tags = image.get("imageTags", [])
            tag = tags[0] if tags else "(untagged)"
            pushed_at = format_iso(image.get("imagePushedAt"))

            scan_status = image.get("imageScanStatus", {}).get("status", "NOT_SCANNED")
            scan_findings = image.get("imageScanFindingsSummary", {})
            counts = scan_findings.get("findingSeverityCounts", {})

            result.append(
                {
                    "image_tag": tag,
                    "image_digest": image.get("imageDigest", ""),
                    "pushed_at": pushed_at,
                    "scan_status": scan_status,
                    "critical_count": counts.get("CRITICAL", 0),
                    "high_count": counts.get("HIGH", 0),
                }
            )

    except ClientError:
        pass

    return result


def list_repositories_for_environment(environment: str, service_names: list[str]) -> list[str]:
    """List ECR repositories for an environment."""
    client = boto3.client("ecr")
    result = []

    repo_names = [f"{environment}-{svc}" for svc in service_names]
    try:
        response = client.describe_repositories(repositoryNames=repo_names)
        for repo in response.get("repositories", []):
            result.append(repo.get("repositoryName", ""))
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "RepositoryNotFoundException":
            for repo_name in repo_names:
                try:
                    response = client.describe_repositories(repositoryNames=[repo_name])
                    for repo in response.get("repositories", []):
                        result.append(repo.get("repositoryName", ""))
                except ClientError:
                    pass

    return result


def _format_timestamp(value: str, fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    """Parse an ISO timestamp and reformat it, returning the original on failure."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime(fmt)
    except (ValueError, AttributeError):
        return value


def _validate_and_configure(environment: str) -> None:
    """Validate environment and configure AWS profile. Exits on error."""
    env_path, error = validate_environment_deployed(environment)
    if error:
        log_error(error)
        sys.exit(1)

    # Show banner
    print()
    print(f"{Colors.CYAN}ops.py: Production monitoring tool (read-only){Colors.NC}")

    # Configure AWS profile - use infra profile for broader read access
    configure_aws_profile_for_environment("infra", environment)
    print()


# =============================================================================
# Status Command
# =============================================================================


def cmd_status(environment: str) -> int:
    """Show current state of environment."""
    env_path = get_environment_path(environment)
    config = load_environment_config(env_path)

    cluster_name = config.get("infrastructure", {}).get("cluster_name")
    rds_id = config.get("infrastructure", {}).get("rds_instance_id")

    print()
    print(f"{Colors.BLUE}Environment: {environment}{Colors.NC}")
    print()

    # ECS Services
    if cluster_name:
        print(f"{Colors.BLUE}ECS Services:{Colors.NC}")
        services = get_all_services_state(cluster_name)
        if services:
            print(f"  {'Service':<25} {'Running':<10} {'Desired':<10} {'Task Definition'}")
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
            family = f"{environment}-{name}"
            revisions = list_task_definition_revisions(family, max_results=5)
            if revisions:
                print(f"  {name}:")
                for rev in revisions:
                    registered = rev.get("registered_at", "unknown")
                    if registered and "T" in registered:
                        registered = _format_timestamp(registered)
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
        snapshots = get_rds_snapshots(rds_id, max_results=5)
        if snapshots:
            print(f"{Colors.BLUE}Recent Snapshots:{Colors.NC}")
            for snap in snapshots:
                created = snap.get("created_at", "unknown")
                if created and "T" in created:
                    created = _format_timestamp(created)
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


def cmd_health(environment: str) -> int:
    """Check ALB target health."""
    env_path = get_environment_path(environment)
    config = load_environment_config(env_path)
    target_group_arn = config.get("infrastructure", {}).get("target_group_arn")

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


def cmd_logs(environment: str, minutes: int, limit: int) -> int:
    """Scan logs for errors."""
    log_groups = get_log_groups_for_environment(environment)

    if not log_groups:
        log_warning(f"No log groups found with prefix /ecs/{environment}")
        log_info("Log groups are created when ECS tasks first run")
        log_info("Check if any tasks have been deployed to this environment")
        return 0

    print()
    print(f"{Colors.BLUE}Scanning logs for errors (last {minutes} minutes):{Colors.NC}")
    print(f"Log groups: {', '.join(log_groups)}")
    print()

    total_errors = 0

    for log_group in log_groups:
        events = scan_logs_for_errors(
            log_group,
            lookback_minutes=minutes,
            max_results=limit,
        )

        if events:
            # Extract service name from log group
            service_name = log_group.replace(f"/ecs/{environment}-", "")
            print(f"{Colors.YELLOW}{service_name}:{Colors.NC} ({len(events)} errors)")

            for event in events[:10]:  # Show first 10
                timestamp = event["timestamp"]
                if "T" in timestamp:
                    timestamp = _format_timestamp(timestamp, "%H:%M:%S")

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


def cmd_maintenance(environment: str) -> int:
    """Show pending maintenance for RDS and ElastiCache."""
    env_path = get_environment_path(environment)
    config = load_environment_config(env_path)
    rds_id = config.get("infrastructure", {}).get("rds_instance_id")

    # ElastiCache cluster ID follows module convention: {environment}-cache
    elasticache_id = f"{environment}-cache"
    cache_config = config.get("cache", {})
    if not cache_config.get("url"):
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


def cmd_ecr(environment: str, verbose: bool) -> int:
    """Show ECR vulnerability findings."""
    env_path = get_environment_path(environment)
    config = load_environment_config(env_path)

    print()
    print(f"{Colors.BLUE}ECR Vulnerability Scan Results:{Colors.NC}")
    print()

    service_config = config.get("services", {}).get("config", {})
    service_names = list(service_config.keys()) if service_config else None

    repos = list_repositories_for_environment(environment, service_names)
    if not repos:
        log_warning(f"No ECR repositories found for {environment}")
        if service_names:
            log_info(f"Checked: {', '.join(f'{environment}-{s}' for s in service_names)}")
        return 0

    total_critical = 0
    total_high = 0

    for repo in repos:
        summaries = get_repository_scan_summary(repo, max_images=1)
        if not summaries:
            continue

        latest = summaries[0]
        critical = latest.get("critical_count", 0)
        high = latest.get("high_count", 0)
        scan_status = latest.get("scan_status", "UNKNOWN")

        total_critical += critical
        total_high += high

        if critical > 0:
            status_color = Colors.RED
        elif high > 0:
            status_color = Colors.YELLOW
        else:
            status_color = Colors.GREEN

        repo_short = repo.replace(f"{environment}-", "")
        print(f"  {repo_short}:")
        print(f"    Tag: {latest['image_tag']}")
        print(f"    Scan: {scan_status}")
        print(f"    Vulnerabilities: {status_color}CRITICAL={critical}, HIGH={high}{Colors.NC}")

        if verbose and (critical > 0 or high > 0):
            findings = get_image_scan_findings(repo, latest["image_tag"])
            for finding in findings.get("findings", [])[:5]:
                print(f"      - [{finding['severity']}] {finding['name']}")
            if len(findings.get("findings", [])) > 5:
                print(f"      ... and {len(findings['findings']) - 5} more")
        print()

    print(f"{Colors.BLUE}Summary:{Colors.NC}")
    if total_critical > 0:
        print(f"  {Colors.RED}CRITICAL: {total_critical}{Colors.NC}")
    else:
        print("  CRITICAL: 0")
    if total_high > 0:
        print(f"  {Colors.YELLOW}HIGH: {total_high}{Colors.NC}")
    else:
        print("  HIGH: 0")

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


def cmd_audit(environment: str) -> int:
    """Run all read-only health and security checks."""
    print()
    print(f"{Colors.BLUE}{'=' * 60}{Colors.NC}")
    print(f"{Colors.BLUE}Production Audit: {environment}{Colors.NC}")
    print(f"{Colors.BLUE}{'=' * 60}{Colors.NC}")

    exit_code = 0

    # 1. Status
    print()
    print(f"{Colors.BLUE}[1/5] Environment Status{Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    if cmd_status(environment) != 0:
        exit_code = 1

    # 2. Health
    print()
    print(f"{Colors.BLUE}[2/5] ALB Target Health{Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    if cmd_health(environment) != 0:
        exit_code = 1

    # 3. Logs
    print()
    print(f"{Colors.BLUE}[3/5] Recent Errors (last 60 minutes){Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    cmd_logs(environment, minutes=60, limit=50)

    # 4. Maintenance
    print()
    print(f"{Colors.BLUE}[4/5] Pending Maintenance{Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    cmd_maintenance(environment)

    # 5. ECR Vulnerabilities
    print()
    print(f"{Colors.BLUE}[5/5] ECR Vulnerability Findings{Colors.NC}")
    print(f"{Colors.BLUE}{'-' * 40}{Colors.NC}")
    if cmd_ecr(environment, verbose=False) != 0:
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
# Incident Tracking
# =============================================================================

INCIDENTS_DIR = Path(__file__).resolve().parent.parent / "local" / "incidents"


def _require_open_incident() -> Path:
    """Find the most recent open incident file, or exit with error."""
    incident = _get_open_incident()
    if not incident:
        log_error(
            'No open incident found. Start one with: ops.py incident start <env> "description"'
        )
        raise SystemExit(1)
    return incident


def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:50]


def _get_open_incident() -> Path | None:
    """Find the most recent open incident file."""
    if not INCIDENTS_DIR.exists():
        return None
    for path in sorted(INCIDENTS_DIR.glob("*.md"), reverse=True):
        content = path.read_text()
        if "Status: OPEN" in content:
            return path
    return None


def cmd_incident_start(environment: str, description: str) -> int:
    """Start a new incident."""
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    slug = _slugify(description)
    filename = f"{now.strftime('%Y-%m-%d-%H%M')}-{slug}.md"
    filepath = INCIDENTS_DIR / filename

    # Capture initial state
    initial_state = ""
    try:
        env_path = get_environment_path(environment)
        config = load_environment_config(env_path)
        cluster_name = config.get("infrastructure", {}).get("cluster_name")
        if cluster_name:
            services = get_all_services_state(cluster_name)
            if services:
                initial_state = "\n".join(
                    f"- {name}: {state.running_count}/{state.desired_count} running"
                    for name, state in sorted(services.items())
                )
    except Exception as e:
        initial_state = f"(Could not capture state: {e})"

    content = f"""# Incident: {description}
Started: {now.isoformat(timespec='seconds')}
Environment: {environment}
Status: OPEN

## Initial State
{initial_state or '(no services found)'}

## Timeline
- {now.strftime('%H:%M')} Incident started

## Resolution
"""

    filepath.write_text(content)
    log_success(f"Incident started: {filepath.name}")
    return 0


def cmd_incident_note(text: str) -> int:
    """Add a note to the most recent open incident."""
    incident = _require_open_incident()
    now = datetime.now()
    content = incident.read_text()

    # Insert note before ## Resolution
    note_line = f"- {now.strftime('%H:%M')} {text}\n"
    content = content.replace("## Resolution\n", f"{note_line}\n## Resolution\n")

    incident.write_text(content)
    log_success(f"Note added to {incident.name}")
    return 0


def cmd_incident_resolve() -> int:
    """Resolve the most recent open incident."""
    incident = _require_open_incident()
    now = datetime.now()
    content = incident.read_text()

    # Mark as resolved
    content = content.replace("Status: OPEN", "Status: RESOLVED")

    # Add resolution timestamp
    resolution = f"Resolved: {now.isoformat(timespec='seconds')}\n"
    content = content.replace("## Resolution\n", f"## Resolution\n{resolution}")

    # Add resolve note to timeline
    note_line = f"- {now.strftime('%H:%M')} Incident resolved\n"
    content = content.replace("\n## Resolution\n", f"{note_line}\n## Resolution\n")

    incident.write_text(content)
    log_success(f"Incident resolved: {incident.name}")
    return 0


def cmd_incident_list() -> int:
    """List open and recent incidents."""
    if not INCIDENTS_DIR.exists():
        print("No incidents recorded")
        return 0

    files = sorted(INCIDENTS_DIR.glob("*.md"), reverse=True)
    if not files:
        print("No incidents recorded")
        return 0

    open_count = 0
    for path in files[:20]:  # Show last 20
        content = path.read_text()
        first_line = content.split("\n")[0]
        title = first_line.replace("# Incident: ", "")

        if "Status: OPEN" in content:
            print(f"  {Colors.RED}[OPEN]{Colors.NC}     {path.name}  {title}")
            open_count += 1
        else:
            print(f"  {Colors.GREEN}[RESOLVED]{Colors.NC} {path.name}  {title}")

    if open_count:
        print(f"\n{open_count} open incident(s)")
    return 0


# =============================================================================
# CLI
# =============================================================================


@click.group()
def cli():
    """Production monitoring tool (read-only).

    \b
    All commands are read-only and safe to run at any time.
    For commands that modify production, see bin/emergency.py.
    """


@cli.command()
@click.argument("environment")
def status(environment):
    """Show current environment state."""
    _validate_and_configure(environment)
    sys.exit(cmd_status(environment))


@cli.command()
@click.argument("environment")
def health(environment):
    """Check ALB target health."""
    _validate_and_configure(environment)
    sys.exit(cmd_health(environment))


@cli.command()
@click.argument("environment")
@click.option("--minutes", "-m", type=int, default=60, help="Lookback period (default: 60)")
@click.option("--limit", "-l", type=int, default=100, help="Max events per log group")
def logs(environment, minutes, limit):
    """Scan logs for errors."""
    _validate_and_configure(environment)
    sys.exit(cmd_logs(environment, minutes, limit))


@cli.command()
@click.argument("environment")
def maintenance(environment):
    """Show pending maintenance."""
    _validate_and_configure(environment)
    sys.exit(cmd_maintenance(environment))


@cli.command()
@click.argument("environment")
@click.option("--verbose", "-v", is_flag=True, help="Show vulnerability details")
def ecr(environment, verbose):
    """Show ECR vulnerability findings."""
    _validate_and_configure(environment)
    sys.exit(cmd_ecr(environment, verbose))


@cli.command()
@click.argument("environment")
def audit(environment):
    """Run all read-only checks."""
    _validate_and_configure(environment)
    sys.exit(cmd_audit(environment))


@cli.group()
def incident():
    """Lightweight incident tracking."""


@incident.command("start")
@click.argument("environment")
@click.argument("description")
def incident_start(environment, description):
    """Start a new incident."""
    _validate_and_configure(environment)
    sys.exit(cmd_incident_start(environment, description))


@incident.command("note")
@click.argument("text")
def incident_note(text):
    """Add a note to the most recent open incident."""
    sys.exit(cmd_incident_note(text))


@incident.command("resolve")
def incident_resolve():
    """Resolve the most recent open incident."""
    sys.exit(cmd_incident_resolve())


@incident.command("list")
def incident_list():
    """List open and recent incidents."""
    sys.exit(cmd_incident_list())


if __name__ == "__main__":
    cli()
