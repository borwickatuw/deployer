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
    python deploy.py env myapp-staging --format json
    python deploy.py audit ~/code/myapp
"""

import contextlib
import secrets
import sys
from dataclasses import replace
from pathlib import Path

import click

from deployer.core.audit import run_audit
from deployer.core.config import (
    get_environment_type,
    load_environment_config,
)
from deployer.deploy.context import EnvironmentTarget
from deployer.deploy.deployer import Deployer, common_deploy_options
from deployer.deploy.env_dump import encode_dotenv, encode_json
from deployer.deploy.pipeline import run_deploy_pipeline
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


def _load_env_config_or_exit(environment: str) -> tuple[dict, str]:
    """Validate the environment directory, load its config.toml, report its type.

    Args:
        environment: Environment name (e.g. "myapp-staging").

    Returns:
        Tuple of (resolved config, environment type).

    Raises:
        SystemExit: With code 1 if the directory is missing, the config cannot
            be loaded, or the environment type is invalid.
    """
    with exit_on(RuntimeError):
        env_path = get_environments_dir() / environment
    if not env_path.exists():
        log_error(f"Environment directory not found: {env_path}")
        sys.exit(1)

    log(f"Loading deployment config from {env_path}...")
    try:
        env_config = load_environment_config(env_path)
    except FileNotFoundError:
        log_error(f"Config file not found: {env_path / 'config.toml'}")
        sys.exit(1)
    except RuntimeError as e:
        # Narrowed from `except Exception` (Phase 53i-3c): the documented pair
        # is FileNotFoundError and RuntimeError. Anything else is a bug in the
        # resolver, and reporting it as "Failed to load deployment config" sent
        # the operator to check a config.toml that was never the problem.
        log_error(f"Failed to load deployment config: {e}")
        sys.exit(1)
    log_success("Loaded config from config.toml")

    with exit_on(ValueError):
        environment_type = get_environment_type(env_config)
    log(f"Environment type: {environment_type}")
    print()

    return env_config, environment_type


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
def deploy(
    environment,
    deploy_toml,
    ignore_audit,
    options,
    preflight,
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

    env_config, environment_type = _load_env_config_or_exit(environment)

    # Set up timing
    timer = None
    if timing_output:
        rid = run_id or f"deploy-{secrets.token_hex(4)}"
        timer = DeploymentTimer(rid)

    sys.exit(
        run_deploy_pipeline(
            config_path,
            EnvironmentTarget(environment, environment_type, env_config),
            preflight=replace(preflight, skip_audit=ignore_audit),
            options=options,
            timer=timer,
            timing_output=Path(timing_output) if timing_output else None,
            ecr_hint=True,
        )
    )


ENV_ENCODERS = {"dotenv": encode_dotenv, "json": encode_json}


@cli.command()
@click.argument("environment")
@click.option(
    "--deploy-toml", metavar="PATH", help="Path to deploy.toml (optional if environment is linked)"
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(sorted(ENV_ENCODERS)),
    default="dotenv",
    show_default=True,
    help="dotenv: shell-sourceable KEY='value' lines. json: a flat object.",
)
def env(environment, deploy_toml, output_format):
    """Print the environment variables a deploy sets, in a parseable form.

    This is the same merged map the deploy log's "Global environment
    variables" block narrates -- resource modules, [environment] and
    [environment.<type>] -- read by the same code, but quoted so it can be
    read back exactly. Per-service [services.<name>.environment] overrides are
    not included, as they are not in the log block.

    Secrets are never printed, and there is no option to print them: [secrets]
    names reach the container from SSM through the task definition's secrets
    block, a separate route from the environment (DECISIONS.md 2026-01-21).

    Only the document goes to stdout; progress and errors go to stderr, so
    the output can be piped or redirected.

    \b
    Examples:
      deploy.py env myapp-staging > myapp-staging.env
      deploy.py env myapp-staging --format json | jq keys
    """
    # Everything before the document -- the link tip, profile validation,
    # config loading, deploy.toml warnings -- prints to stdout by the
    # helpers' own contract; send all of it to stderr instead.
    with contextlib.redirect_stdout(sys.stderr):
        config_path = resolve_deploy_toml_or_exit(
            environment,
            deploy_toml,
            specify_hint=f"deploy.py env {environment} --deploy-toml /path/to/deploy.toml",
            link_benefit=f"dump with just: deploy.py env {environment}",
        )
        configure_profile_or_exit("deploy", environment)
        env_config, environment_type = _load_env_config_or_exit(environment)
        with exit_on(ValueError):
            deployer = Deployer(str(config_path), environment_type, env_config)
        env_vars = deployer.environment_variables()
        with exit_on(ValueError):
            document = ENV_ENCODERS[output_format](env_vars)

    sys.stdout.write(document)


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
