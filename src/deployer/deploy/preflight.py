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
from deployer.utils import advice_block, log, log_debug, log_success, log_warning


class PreflightError(Exception):
    """Raised when a preflight check fails."""


@dataclass
class PreflightOptions:
    """Options controlling which preflight checks to run."""

    skip_ecr_check: bool = False
    skip_secrets_check: bool = False
    skip_cluster_check: bool = False
    skip_audit: bool = False


def check_environment_config(env_config: dict) -> None:
    """Validate required fields are present in environment config.

    Raises:
        PreflightError: If required config fields are missing.
    """
    config_errors = validate_environment_config(env_config)
    if config_errors:
        raise PreflightError(
            advice_block(
                "Environment config is missing required fields:",
                config_errors,
                (
                    "  These fields are typically set via ${tofu:...} placeholders.",
                    "  Run 'tofu apply' in the environment directory to create infrastructure,",
                    "  then update config.toml with the appropriate placeholders.",
                ),
            )
        )


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
        raise PreflightError(
            advice_block(
                f"Audit found {issue_count} issue(s):",
                issues,
                (
                    "To fix: add an [audit] section to deploy.toml to acknowledge differences,",
                    f"        or run: python bin/deploy.py audit {project_dir}",
                    "        or use --ignore-audit to skip this check",
                ),
                bullet="  - ",
            )
        )
    else:
        log_success("Audit passed")
        print()


def _infrastructure_value(env_config: dict, key: str, check_name: str) -> str | None:
    """Read an [infrastructure] key, warning that the check is skipped if absent.

    Args:
        env_config: Resolved environment configuration dict.
        key: The [infrastructure] key to read, e.g. "ecr_prefix".
        check_name: How to name the skipped check in the warning, e.g. "ECR".

    Returns:
        The configured value, or None if the key is absent — in which case the
        caller has already been told the check is being skipped.
    """
    value = env_config.get("infrastructure", {}).get(key)
    log_debug(f"{key}: {value}")

    if not value:
        log_warning(f"{key} not found in config, skipping {check_name} check")
        print()
        return None

    return value


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
    ecr_prefix = _infrastructure_value(env_config, "ecr_prefix", "ECR")
    if not ecr_prefix:
        return

    images = deploy_config.images
    log_debug(f"Images to check: {list(images.keys())}")
    missing_repos = validate_ecr_repositories(boto3.client("ecr"), deploy_config, ecr_prefix)

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
        raise PreflightError(advice_block("Resource module validation failed:", errors))
    log_success("Resource modules validated")
    print()


def check_ecs_cluster(env_config: dict) -> None:
    """Verify the ECS cluster exists and is active.

    Raises:
        PreflightError: If the ECS cluster doesn't exist or isn't active.
    """
    log("Checking ECS cluster...")
    cluster_name = _infrastructure_value(env_config, "cluster_name", "cluster")
    if not cluster_name:
        return

    log_debug(f"Calling describe_clusters for: {cluster_name}")
    exists, error = validate_ecs_cluster(boto3.client("ecs"), cluster_name)
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
