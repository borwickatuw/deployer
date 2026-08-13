#!/usr/bin/env python3
"""
Deploy an application to AWS ECS using a TOML configuration file.

This script reads an application's deployment configuration and:
1. Builds and pushes Docker images to ECR
2. Runs database migrations (if configured)
3. Updates ECS services to use the new images

Service sizing (cpu, memory, replicas) comes from the environment's config.toml
which references OpenTofu outputs. This allows different sizing per environment
while keeping app structure in deploy.toml.

Usage:
    python deploy.py deploy myapp-staging
    python deploy.py deploy myapp-staging --dry-run
    python deploy.py audit ~/code/myapp
"""

import secrets
import sys
from pathlib import Path

import click

from deployer.core.audit import run_audit
from deployer.core.config import (
    get_environment_type,
    load_environment_config,
)
from deployer.deploy.context import DeployOptions, EnvironmentTarget
from deployer.deploy.deployer import common_deploy_options
from deployer.deploy.pipeline import run_deploy_pipeline
from deployer.deploy.preflight import PreflightOptions
from deployer.timing import DeploymentTimer
from deployer.utils import (
    Colors,
    configure_profile_or_exit,
    exit_on,
    get_environments_dir,
    log,
    log_error,
    log_success,
    resolve_deploy_toml_or_exit,
    set_verbose,
)


@click.group()
def cli():
    """Deploy applications to AWS ECS."""


@cli.command()
@click.argument("environment")
@click.option(
    "--deploy-toml", metavar="PATH", help="Path to deploy.toml (optional if environment is linked)"
)
@click.option(
    "--ignore-audit", is_flag=True, help="Skip the deploy.toml vs docker-compose.yml audit check"
)
@common_deploy_options
@click.option(
    "--timing-output",
    metavar="FILE",
    help="Save timing report to JSON file (also prints to stdout)",
)
@click.option(
    "--run-id", metavar="ID", help="Run ID for timing report (auto-generated if not specified)"
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed debug information")
def deploy(  # noqa: C901 — main deploy orchestration
    environment,
    deploy_toml,
    ignore_audit,
    dry_run,
    force,
    force_build,
    skip_ecr_check,
    skip_secrets_check,
    skip_cluster_check,
    timing_output,
    run_id,
    verbose,
):
    """Deploy an application to an environment.

    \b
    Examples:
      deploy.py deploy myapp-staging
      deploy.py deploy myapp-staging --dry-run
      deploy.py deploy myapp-staging --deploy-toml ~/code/myapp/deploy.toml
    """
    if verbose:
        set_verbose(True)

    config_path = resolve_deploy_toml_or_exit(
        environment,
        deploy_toml,
        specify_hint=f"deploy.py deploy {environment} --deploy-toml /path/to/deploy.toml",
        link_benefit=f"deploy with just: deploy.py deploy {environment}",
    )

    # Configure AWS profile
    configure_profile_or_exit("deploy", environment)
    print()

    # Validate environment directory
    env_path = get_environments_dir() / environment
    if not env_path.exists():
        log_error(f"Environment directory not found: {env_path}")
        sys.exit(1)

    # Load config
    log(f"Loading deployment config from {env_path}...")
    try:
        env_config = load_environment_config(env_path)
        log_success("Loaded config from config.toml")
    except FileNotFoundError:
        log_error(f"Config file not found: {env_path / 'config.toml'}")
        sys.exit(1)
    except Exception as e:
        log_error(f"Failed to load deployment config: {e}")
        sys.exit(1)

    with exit_on(ValueError):
        environment_type = get_environment_type(env_config)
    log(f"Environment type: {environment_type}")
    print()

    # Set up timing
    timer = None
    if timing_output:
        rid = run_id or f"deploy-{secrets.token_hex(4)}"
        timer = DeploymentTimer(rid)

    sys.exit(
        run_deploy_pipeline(
            config_path,
            EnvironmentTarget(environment, environment_type, env_config),
            preflight=PreflightOptions(
                skip_ecr_check=skip_ecr_check,
                skip_secrets_check=skip_secrets_check,
                skip_cluster_check=skip_cluster_check,
                skip_audit=ignore_audit,
            ),
            options=DeployOptions(dry_run=dry_run, force=force, force_build=force_build),
            timer=timer,
            timing_output=Path(timing_output) if timing_output else None,
            ecr_hint=True,
        )
    )


@cli.command()
@click.argument("project_dir")
@click.option(
    "--docker-compose",
    default="docker-compose.yml",
    help="Name of docker-compose file (default: docker-compose.yml)",
)
@click.option(
    "--deploy-toml", default="deploy.toml", help="Name of deploy.toml file (default: deploy.toml)"
)
def audit(project_dir, docker_compose, deploy_toml):
    """Audit deploy.toml against docker-compose.yml to find discrepancies.

    \b
    Examples:
      deploy.py audit ~/code/myapp
      deploy.py audit . --docker-compose docker-compose.prod.yml
    """
    issue_count, issues = run_audit(
        project_dir,
        compose_filename=docker_compose,
        deploy_filename=deploy_toml,
        verbose=True,
    )

    if issue_count < 0:
        print(f"{Colors.RED}Error: {issues[0]}{Colors.NC}")
        sys.exit(1)
    elif issue_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    cli()
