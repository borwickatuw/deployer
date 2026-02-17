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

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from deployer.config import parse_deploy_config
from deployer.deploy.preflight import PreflightError, PreflightOptions, run_preflight_checks
from deployer.utils import Colors, log, log_error, log_warning


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
            raise ValueError(f"Invalid JSON in resolved config: {e}")

    if not isinstance(data, dict):
        raise ValueError("Resolved config must be a JSON object")

    meta = data.pop("_meta", None)
    if meta is None:
        raise ValueError(
            "Resolved config is missing _meta block. "
            "Generate it with: python bin/resolve-config.py <environment> --output <file>"
        )

    required_meta = ["environment", "environment_type", "resolved_at"]
    missing = [f for f in required_meta if f not in meta]
    if missing:
        raise ValueError(f"_meta block is missing required fields: {', '.join(missing)}")

    return data, meta


def fetch_from_s3(s3_uri: str) -> str:
    """Fetch a file from S3 and return its content as a string.

    Args:
        s3_uri: S3 URI (s3://bucket/key).

    Returns:
        File content as string.

    Raises:
        RuntimeError: If S3 fetch fails.
    """
    import boto3
    from botocore.exceptions import ClientError

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
        raise RuntimeError(f"Failed to fetch {s3_uri}: {error_code} - {error_msg}")


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
        raise ValueError(f"Invalid JSON from {s3_uri}: {e}")

    if not isinstance(data, dict):
        raise ValueError("Resolved config must be a JSON object")

    meta = data.pop("_meta", None)
    if meta is None:
        raise ValueError(f"Resolved config from {s3_uri} is missing _meta block")

    required_meta = ["environment", "environment_type", "resolved_at"]
    missing = [f for f in required_meta if f not in meta]
    if missing:
        raise ValueError(f"_meta block is missing required fields: {', '.join(missing)}")

    return data, meta


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


def main():
    parser = argparse.ArgumentParser(
        prog="ci-deploy",
        description="Deploy an application using a pre-resolved config (for CI/CD pipelines).",
        epilog="""
Examples:
  ci-deploy deploy.toml resolved-config.json
  ci-deploy deploy.toml s3://bucket/myapp-staging/config.json
  ci-deploy deploy.toml resolved-config.json --dry-run
  ci-deploy deploy.toml resolved-config.json --max-config-age 48 --strict
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "deploy_toml",
        help="Path to application's deploy.toml",
    )
    parser.add_argument(
        "resolved_config",
        help="Path to resolved config JSON file (or s3:// URI)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Deploy even if infrastructure is unavailable",
    )
    parser.add_argument(
        "--force-build",
        action="store_true",
        help="Force rebuilding images even if unchanged",
    )
    parser.add_argument(
        "--skip-ecr-check",
        action="store_true",
        help="Skip the ECR repository existence check",
    )
    parser.add_argument(
        "--skip-secrets-check",
        action="store_true",
        help="Skip the SSM secrets existence check",
    )
    parser.add_argument(
        "--skip-cluster-check",
        action="store_true",
        help="Skip the ECS cluster existence check",
    )
    parser.add_argument(
        "--max-config-age",
        type=float,
        metavar="HOURS",
        help="Warn if resolved config is older than this (hours)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat staleness warnings as errors",
    )

    args = parser.parse_args()

    # Validate deploy.toml path
    deploy_toml_path = Path(args.deploy_toml)
    if not deploy_toml_path.exists():
        log_error(f"deploy.toml not found: {deploy_toml_path}")
        sys.exit(1)
    if not deploy_toml_path.suffix == ".toml":
        log_error(f"Expected a .toml file, got: {deploy_toml_path}")
        sys.exit(1)

    # Load resolved config (from file or S3)
    try:
        if args.resolved_config.startswith("s3://"):
            env_config, meta = load_resolved_config_from_s3(args.resolved_config)
        else:
            env_config, meta = load_resolved_config(args.resolved_config)
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
    print(f"  Resolved config:  {args.resolved_config}")
    print(f"  Resolved at:      {meta.get('resolved_at', 'unknown')}")
    print_config_age(meta)
    print()

    # Check config staleness
    if args.max_config_age:
        resolved_at_str = meta.get("resolved_at")
        if resolved_at_str:
            try:
                resolved_at = datetime.fromisoformat(resolved_at_str)
                age_hours = (datetime.now(timezone.utc) - resolved_at).total_seconds() / 3600
                if age_hours > args.max_config_age:
                    msg = (
                        f"Resolved config is {age_hours:.1f} hours old "
                        f"(limit: {args.max_config_age} hours)"
                    )
                    if args.strict:
                        log_error(msg)
                        sys.exit(1)
                    else:
                        log_warning(msg)
            except (ValueError, TypeError):
                log_warning("Could not parse resolved_at timestamp for staleness check")

    # Parse deploy.toml
    try:
        deploy_config = parse_deploy_config(deploy_toml_path)
    except Exception as e:
        log_error(f"Failed to parse deploy.toml: {e}")
        sys.exit(1)

    project_dir = deploy_toml_path.parent

    # Run preflight checks (skip audit by default in CI — no docker-compose)
    preflight_options = PreflightOptions(
        skip_ecr_check=args.skip_ecr_check,
        skip_secrets_check=args.skip_secrets_check,
        skip_cluster_check=args.skip_cluster_check,
        skip_audit=True,
    )
    try:
        run_preflight_checks(
            deploy_config=deploy_config,
            env_config=env_config,
            environment=environment,
            environment_type=environment_type,
            project_dir=project_dir,
            options=preflight_options,
        )
    except PreflightError as e:
        log_error(str(e))
        sys.exit(1)

    from deployer.deploy import Deployer

    try:
        deployer = Deployer(
            config_path=str(deploy_toml_path),
            environment=environment_type,
            env_config=env_config,
            dry_run=args.dry_run,
            force=args.force,
            force_build=args.force_build,
        )
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)

    try:
        _, health_failures = deployer.deploy()
    except RuntimeError as e:
        error_msg = str(e)
        if "Push failed" in error_msg:
            print()
            log_error(error_msg)
            print()
            print("  This is often caused by a temporary network issue.")
            print("  Please try running the deploy command again.")
            sys.exit(1)
        raise

    if health_failures:
        sys.exit(2)


if __name__ == "__main__":
    main()
