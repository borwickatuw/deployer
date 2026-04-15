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

from deployer.config import parse_deploy_config
from deployer.core.audit import run_audit
from deployer.core.config import (
    get_environment_type,
    load_environment_config,
)
from deployer.deploy.deployer import Deployer, common_deploy_options, handle_push_error
from deployer.deploy.preflight import PreflightError, PreflightOptions, run_preflight_checks
from deployer.timing import DeploymentTimer
from deployer.utils import (
    Colors,
    configure_aws_profile_for_environment,
    get_environments_dir,
    get_linked_deploy_toml,
    log,
    log_error,
    log_success,
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

    # Resolve deploy.toml path
    config_path = None
    used_explicit_flag = False

    if deploy_toml:
        config_path = Path(deploy_toml).expanduser().resolve()
        used_explicit_flag = True
    else:
        linked_path = get_linked_deploy_toml(environment)
        if linked_path:
            config_path = linked_path
            log(f"Using linked deploy.toml: {config_path}")
        else:
            log_error(f"No deploy.toml linked for '{environment}'")
            log_error(
                f"\nTo link: python bin/link-environments.py {environment} /path/to/deploy.toml"
            )
            log_error(
                f"Or specify: deploy.py deploy {environment} --deploy-toml /path/to/deploy.toml"
            )
            sys.exit(1)

    if used_explicit_flag:
        print(f"Tip: Run 'python bin/link-environments.py {environment} {config_path}'")
        print(f"     to deploy with just: deploy.py deploy {environment}\n")

    # Configure AWS profile
    try:
        configure_aws_profile_for_environment("deploy", environment, validate=True)
    except RuntimeError as e:
        log_error(str(e))
        sys.exit(1)
    print()

    # Validate config file
    if config_path.is_dir():
        log_error(f"Config path is a directory, expected a .toml file: {config_path}")
        sys.exit(1)
    if not config_path.exists():
        log_error(f"Config file not found: {config_path}")
        sys.exit(1)
    if config_path.suffix != ".toml":
        log_error(f"Config file must be a .toml file, got: {config_path}")
        sys.exit(1)

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

    try:
        environment_type = get_environment_type(env_config)
        log(f"Environment type: {environment_type}")
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)
    print()

    # Run pre-flight checks
    preflight_options = PreflightOptions(
        skip_ecr_check=skip_ecr_check,
        skip_secrets_check=skip_secrets_check,
        skip_cluster_check=skip_cluster_check,
        skip_audit=ignore_audit,
    )
    try:
        run_preflight_checks(
            deploy_config=parse_deploy_config(config_path),
            env_config=env_config,
            environment=environment,
            environment_type=environment_type,
            project_dir=config_path.parent,
            options=preflight_options,
        )
    except PreflightError as e:
        log_error(str(e))
        sys.exit(1)

    # Set up timing
    timer = None
    if timing_output:
        rid = run_id or f"deploy-{secrets.token_hex(4)}"
        timer = DeploymentTimer(rid)

    try:
        deployer = Deployer(
            config_path,
            environment_type,
            env_config,
            dry_run=dry_run,
            force=force,
            force_build=force_build,
            timer=timer,
        )
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)

    try:
        _, health_failures = deployer.deploy()
    except RuntimeError as e:
        if handle_push_error(e, include_ecr_hint=True):
            sys.exit(1)
        raise

    if timer:
        print()
        log("Timing report:")
        print(timer.report.to_json())

        if timing_output:
            output_path = Path(timing_output)
            timer.report.save_json(output_path)
            log_success(f"Timing saved to {output_path}")

    if health_failures:
        sys.exit(2)


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
