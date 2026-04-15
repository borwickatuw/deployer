"""CI/CD deployment entry point.

This module provides the `ci-deploy` console_scripts command for deploying
applications from CI/CD pipelines. Unlike deploy.py, it requires no tofu,
no deployer-environments directory, and no AWS profile auto-selection.

It takes two explicit inputs:
  1. An application's deploy.toml (what to run)
  2. A pre-resolved config JSON file (where to run it)

The resolved config JSON is produced by bin/resolve-config.py and contains
all infrastructure values already resolved from tofu outputs.

Usage:
    ci-deploy deploy.toml resolved-config.json
    ci-deploy deploy.toml s3://bucket/myapp-staging/config.json
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import click
from botocore.exceptions import ClientError

from deployer.config import parse_deploy_config
from deployer.deploy.deployer import Deployer, common_deploy_options, handle_push_error
from deployer.deploy.preflight import PreflightError, PreflightOptions, run_preflight_checks
from deployer.utils import Colors, log, log_error, log_warning


def _validate_resolved_config(data: dict, source: str = "") -> tuple[dict, dict]:
    """Validate a resolved config dict and extract _meta.

    Args:
        data: Parsed JSON data.
        source: Description of where the data came from (for error messages).

    Returns:
        Tuple of (config, meta) with _meta removed from config.

    Raises:
        ValueError: If the data is invalid or missing required fields.
    """
    if not isinstance(data, dict):
        raise ValueError("Resolved config must be a JSON object")

    meta = data.pop("_meta", None)
    if meta is None:
        msg = "Resolved config is missing _meta block"
        if not source:
            msg += ". Generate it with: python bin/resolve-config.py <environment> --output <file>"
        else:
            msg += f" (from {source})"
        raise ValueError(msg)

    required_meta = ["environment", "environment_type", "resolved_at"]
    missing = [f for f in required_meta if f not in meta]
    if missing:
        raise ValueError(f"_meta block is missing required fields: {', '.join(missing)}")

    return data, meta


def load_resolved_config(config_path: str) -> tuple[dict, dict]:
    """Load a resolved config JSON file and extract _meta.

    Args:
        config_path: Path to the resolved config JSON file.

    Returns:
        Tuple of (env_config, meta). env_config has _meta stripped.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file is invalid or missing required fields.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Resolved config not found: {path}")

    with open(path) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in resolved config: {e}") from e

    return _validate_resolved_config(data)


def fetch_from_s3(s3_uri: str) -> str:
    """Fetch a file from S3 and return its content as a string.

    Args:
        s3_uri: S3 URI (s3://bucket/key).

    Returns:
        File content as string.

    Raises:
        RuntimeError: If S3 fetch fails.
    """
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {s3_uri}")

    # Parse s3://bucket/key
    parts = s3_uri[5:].split("/", 1)
    if len(parts) < 2:
        raise ValueError(f"Invalid S3 URI (no key): {s3_uri}")

    bucket, key = parts[0], parts[1]

    try:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read().decode("utf-8")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        raise RuntimeError(f"Failed to fetch {s3_uri}: {error_code} - {error_msg}") from e


def load_resolved_config_from_s3(s3_uri: str) -> tuple[dict, dict]:
    """Load a resolved config from S3 and extract _meta.

    Args:
        s3_uri: S3 URI to the resolved config JSON.

    Returns:
        Tuple of (env_config, meta).
    """
    content = fetch_from_s3(s3_uri)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON from {s3_uri}: {e}") from e

    return _validate_resolved_config(data, source=s3_uri)


def print_config_age(meta: dict) -> None:
    """Print the age of the resolved config and warn if stale."""
    resolved_at_str = meta.get("resolved_at")
    if not resolved_at_str:
        return

    try:
        resolved_at = datetime.fromisoformat(resolved_at_str)
        age = datetime.now(timezone.utc) - resolved_at
        hours = age.total_seconds() / 3600

        if hours < 1:
            minutes = age.total_seconds() / 60
            log(f"  Config age: {minutes:.0f} minutes")
        elif hours < 24:
            log(f"  Config age: {hours:.1f} hours")
        else:
            days = hours / 24
            log(f"  Config age: {days:.1f} days")

        if hours > 168:  # 7 days
            log_warning(f"Resolved config is {hours / 24:.0f} days old — consider re-resolving")
    except (ValueError, TypeError):
        pass


@click.command()
@click.argument("deploy_toml")
@click.argument("resolved_config")
@common_deploy_options
@click.option(
    "--max-config-age",
    type=float,
    metavar="HOURS",
    help="Warn if resolved config is older than this (hours)",
)
@click.option("--strict", is_flag=True, help="Treat staleness warnings as errors")
def main(  # noqa: C901 — CI deploy orchestration
    deploy_toml,
    resolved_config,
    dry_run,
    force,
    force_build,
    skip_ecr_check,
    skip_secrets_check,
    skip_cluster_check,
    max_config_age,
    strict,
):
    """Deploy an application using a pre-resolved config (for CI/CD pipelines).

    \b
    Examples:
      ci-deploy deploy.toml resolved-config.json
      ci-deploy deploy.toml s3://bucket/myapp-staging/config.json
      ci-deploy deploy.toml resolved-config.json --dry-run
      ci-deploy deploy.toml resolved-config.json --max-config-age 48 --strict
    """
    # Validate deploy.toml path
    deploy_toml_path = Path(deploy_toml)
    if not deploy_toml_path.exists():
        log_error(f"deploy.toml not found: {deploy_toml_path}")
        sys.exit(1)
    if deploy_toml_path.suffix != ".toml":
        log_error(f"Expected a .toml file, got: {deploy_toml_path}")
        sys.exit(1)

    # Load resolved config (from file or S3)
    try:
        if resolved_config.startswith("s3://"):
            env_config, meta = load_resolved_config_from_s3(resolved_config)
        else:
            env_config, meta = load_resolved_config(resolved_config)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        log_error(str(e))
        sys.exit(1)

    environment = meta["environment"]
    environment_type = meta["environment_type"]

    # Print deployment info
    print()
    print(f"{Colors.BLUE}CI/CD Deploy{Colors.NC}")
    print(f"  Environment:      {environment}")
    print(f"  Environment type: {environment_type}")
    print(f"  deploy.toml:      {deploy_toml_path}")
    print(f"  Resolved config:  {resolved_config}")
    print(f"  Resolved at:      {meta.get('resolved_at', 'unknown')}")
    print_config_age(meta)
    print()

    # Check config staleness
    if max_config_age:
        resolved_at_str = meta.get("resolved_at")
        if resolved_at_str:
            try:
                resolved_at = datetime.fromisoformat(resolved_at_str)
                age_hours = (datetime.now(timezone.utc) - resolved_at).total_seconds() / 3600
                if age_hours > max_config_age:
                    msg = (
                        f"Resolved config is {age_hours:.1f} hours old "
                        f"(limit: {max_config_age} hours)"
                    )
                    if strict:
                        log_error(msg)
                        sys.exit(1)
                    else:
                        log_warning(msg)
            except (ValueError, TypeError):
                log_warning("Could not parse resolved_at timestamp for staleness check")

    # Run preflight checks (skip audit by default in CI — no docker-compose)
    preflight_options = PreflightOptions(
        skip_ecr_check=skip_ecr_check,
        skip_secrets_check=skip_secrets_check,
        skip_cluster_check=skip_cluster_check,
        skip_audit=True,
    )
    try:
        deploy_config = parse_deploy_config(deploy_toml_path)
    except Exception as e:  # noqa: BLE001 — CLI error handler for deploy.toml parse
        log_error(f"Failed to parse deploy.toml: {e}")
        sys.exit(1)

    try:
        run_preflight_checks(
            deploy_config=deploy_config,
            env_config=env_config,
            environment=environment,
            environment_type=environment_type,
            project_dir=deploy_toml_path.parent,
            options=preflight_options,
        )
    except PreflightError as e:
        log_error(str(e))
        sys.exit(1)

    try:
        deployer = Deployer(
            config_path=str(deploy_toml_path),
            environment=environment_type,
            env_config=env_config,
            dry_run=dry_run,
            force=force,
            force_build=force_build,
        )
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)

    try:
        _, health_failures = deployer.deploy()
    except RuntimeError as e:
        if handle_push_error(e):
            sys.exit(1)
        raise

    if health_failures:
        sys.exit(2)


if __name__ == "__main__":
    main()
