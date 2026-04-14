"""Pre-deployment validation checks.

This module contains shared preflight checks used by both the local deploy.py
and the CI/CD ci-deploy entry point. Checks are designed to fail fast with
clear error messages before any deployment actions begin.
"""

from dataclasses import dataclass
from pathlib import Path

import boto3

from deployer.config import DeployConfig
from deployer.core.audit import run_audit
from deployer.core.config import validate_environment_config
from deployer.core.ssm_secrets import (
    check_secrets_drift,
    check_secrets_exist,
    format_missing_secrets_error,
)
from deployer.deploy.images import (
    format_missing_ecr_error,
    validate_ecr_repositories,
)
from deployer.deploy.validation import validate_ecs_cluster
from deployer.modules import ModuleRegistry
from deployer.utils import log, log_debug, log_success, log_warning


class PreflightError(Exception):
    """Raised when a preflight check fails."""


@dataclass
class PreflightOptions:
    """Options controlling which preflight checks to run."""

    skip_ecr_check: bool = False
    skip_secrets_check: bool = False
    skip_cluster_check: bool = False
    skip_audit: bool = False


# pysmelly: ignore pass-through-params — preflight wrapper adds PreflightError translation
def check_environment_config(env_config: dict) -> None:
    """Validate required fields are present in environment config.

    Raises:
        PreflightError: If required config fields are missing.
    """
    config_errors = validate_environment_config(env_config)
    if config_errors:
        lines = ["Environment config is missing required fields:"]
        for error in config_errors:
            lines.append(f"    {error}")
        lines.append("")
        lines.append("  These fields are typically set via ${tofu:...} placeholders.")
        lines.append("  Run 'tofu apply' in the environment directory to create infrastructure,")
        lines.append("  then update config.toml with the appropriate placeholders.")
        raise PreflightError("\n".join(lines))


def check_audit(
    _deploy_config: DeployConfig,
    project_dir: Path,
) -> None:
    """Run deploy.toml vs docker-compose.yml audit.

    Raises:
        PreflightError: If audit finds issues.
    """
    log("Running deploy.toml audit...")
    issue_count, issues = run_audit(project_dir, verbose=False)

    if issue_count < 0:
        # File not found - skip audit silently (docker-compose.yml may not exist)
        log_warning(f"Audit skipped: {issues[0]}")
        print()
    elif issue_count > 0:
        lines = [f"Audit found {issue_count} issue(s):"]
        for issue in issues:
            lines.append(f"  - {issue}")
        lines.append("")
        lines.append("To fix: add an [audit] section to deploy.toml to acknowledge differences,")
        lines.append(f"        or run: python bin/deploy.py audit {project_dir}")
        lines.append("        or use --ignore-audit to skip this check")
        raise PreflightError("\n".join(lines))
    else:
        log_success("Audit passed")
        print()


# pysmelly: ignore param-clumps — deploy_config, env_config, environment are distinct objects
def check_ecr_repositories(
    deploy_config: DeployConfig,
    env_config: dict,
    environment: str,
) -> None:
    """Verify all required ECR repositories exist.

    Raises:
        PreflightError: If ECR repositories are missing.
    """
    log("Checking ECR repositories...")
    ecr_client = boto3.client("ecr")
    ecr_prefix = env_config.get("infrastructure", {}).get("ecr_prefix")
    log_debug(f"ECR prefix: {ecr_prefix}")

    if not ecr_prefix:
        log_warning("ecr_prefix not found in config, skipping ECR check")
        print()
        return

    images = deploy_config.images
    log_debug(f"Images to check: {list(images.keys())}")
    missing_repos = validate_ecr_repositories(ecr_client, deploy_config, ecr_prefix)

    if missing_repos:
        raise PreflightError(format_missing_ecr_error(missing_repos, environment))

    image_count = len([img for img in images.values() if img.push])
    log_success(f"All {image_count} ECR repository(ies) present")
    print()


def check_ssm_secrets(
    deploy_config: DeployConfig,
    env_config: dict,
    environment: str,
    environment_type: str,
) -> None:
    """Verify all required SSM secrets exist.

    Raises:
        PreflightError: If SSM secrets are missing.
    """
    log("Checking SSM secrets...")
    missing, present = check_secrets_exist(
        deploy_config.get_raw_dict(), environment_type, environment, env_config
    )

    if missing:
        raise PreflightError(format_missing_secrets_error(missing, environment))
    elif present:
        log_success(f"All {len(present)} secret(s) present")
    else:
        log("No secrets defined in deploy.toml")

    # Check for unreferenced secrets in SSM (warn only)
    unreferenced = check_secrets_drift(deploy_config.get_raw_dict(), environment_type, env_config)
    if unreferenced:
        log_warning(f"{len(unreferenced)} SSM secret(s) not referenced in deploy.toml:")
        for path in unreferenced:
            log_warning(f"  {path}")
    print()


def check_modules(deploy_config: DeployConfig, env_config: dict) -> None:
    """Validate resource module declarations against environment config.

    Raises:
        PreflightError: If module validation fails.
    """
    log("Checking resource modules...")
    errors = ModuleRegistry.validate_all(deploy_config.get_raw_dict(), env_config)
    if errors:
        lines = ["Resource module validation failed:"]
        for error in errors:
            lines.append(f"    {error}")
        raise PreflightError("\n".join(lines))
    log_success("Resource modules validated")
    print()


def check_ecs_cluster(env_config: dict) -> None:
    """Verify the ECS cluster exists and is active.

    Raises:
        PreflightError: If the ECS cluster doesn't exist or isn't active.
    """
    log("Checking ECS cluster...")
    ecs_client = boto3.client("ecs")
    cluster_name = env_config.get("infrastructure", {}).get("cluster_name")
    log_debug(f"Cluster name: {cluster_name}")

    if not cluster_name:
        log_warning("cluster_name not found in config, skipping cluster check")
        print()
        return

    log_debug(f"Calling describe_clusters for: {cluster_name}")
    exists, error = validate_ecs_cluster(ecs_client, cluster_name)
    if not exists:
        raise PreflightError(error)

    log_success(f"ECS cluster '{cluster_name}' is active")
    print()


def run_preflight_checks(
    deploy_config: DeployConfig,
    env_config: dict,
    environment: str,
    environment_type: str,
    project_dir: Path,
    options: PreflightOptions,
) -> None:
    """Run all pre-deployment validation checks.

    This is the main entry point for preflight checks, used by both
    deploy.py (local) and ci-deploy (CI/CD).

    Args:
        deploy_config: Parsed deploy.toml configuration.
        env_config: Resolved environment configuration dict.
        environment: Environment name (e.g., "myapp-staging").
        environment_type: Environment type ("staging" or "production").
        project_dir: Path to project directory (for audit check).
        options: Options controlling which checks to run.

    Raises:
        PreflightError: If any check fails.
    """
    # Always validate environment config
    check_environment_config(env_config)

    # Resource modules (database, cache, storage, cdn, autoscale, etc.)
    check_modules(deploy_config, env_config)

    # Audit (deploy.toml vs docker-compose.yml)
    if not options.skip_audit:
        check_audit(deploy_config, project_dir)

    # ECR repositories
    if not options.skip_ecr_check:
        check_ecr_repositories(deploy_config, env_config, environment)

    # SSM secrets
    if not options.skip_secrets_check:
        check_ssm_secrets(deploy_config, env_config, environment, environment_type)

    # ECS cluster
    if not options.skip_cluster_check:
        check_ecs_cluster(env_config)
