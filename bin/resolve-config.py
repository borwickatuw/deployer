#!/usr/bin/env python3
"""
Resolve an environment's config.toml into a standalone JSON file.

This script resolves all ${tofu:...} placeholders in config.toml by fetching
tofu outputs, then writes the fully-resolved configuration as JSON. The output
includes a _meta block with hashes and timestamps for staleness detection.

The resolved config JSON can be used by ci-deploy for CI/CD deployments
without needing tofu or the deployer-environments directory.

Usage:
    python resolve-config.py <environment> [--output FILE] [--push-s3]

Examples:
    python resolve-config.py myapp-staging
    python resolve-config.py myapp-staging --output resolved.json
    python resolve-config.py myapp-staging --push-s3
    python resolve-config.py myapp-staging --output resolved.json --push-s3
    python resolve-config.py myapp-staging --verify
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import click
from botocore.exceptions import ClientError

from deployer.core.config import (
    get_all_tofu_outputs,
    get_environment_type,
    load_environment_config,
)
from deployer.utils import (
    configure_aws_profile_for_environment,
    get_environment_path,
    log,
    log_error,
    log_success,
)


def _compute_hash(data: str) -> str:
    """Compute SHA-256 hash of a string, returning a prefixed hex digest."""
    return f"sha256:{hashlib.sha256(data.encode()).hexdigest()}"


def build_meta(
    environment: str,
    environment_type: str,
    config_toml_content: str,
    tofu_outputs_json: str,
) -> dict:
    """Build the _meta block for the resolved config.

    Args:
        environment: Environment name (e.g., "myapp-staging").
        environment_type: "staging" or "production".
        config_toml_content: Raw content of config.toml (for hashing).
        tofu_outputs_json: JSON string of tofu outputs (for hashing).

    Returns:
        Dict containing metadata for staleness detection.
    """
    return {
        "environment": environment,
        "environment_type": environment_type,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "config_toml_hash": _compute_hash(config_toml_content),
        "tofu_outputs_hash": _compute_hash(tofu_outputs_json),
    }


def resolve_config(environment: str) -> dict:
    """Resolve an environment's config.toml into a dict with _meta.

    Args:
        environment: Environment name (e.g., "myapp-staging").

    Returns:
        Fully resolved config dict with _meta block.

    Raises:
        FileNotFoundError: If config.toml doesn't exist.
        RuntimeError: If tofu outputs cannot be fetched.
        ValueError: If environment type cannot be determined.
    """
    env_path = get_environment_path(environment)
    if not env_path.exists():
        raise FileNotFoundError(f"Environment directory not found: {env_path}")

    # Read raw config.toml for hashing
    config_toml_path = env_path / "config.toml"
    if not config_toml_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_toml_path}")
    config_toml_content = config_toml_path.read_text()

    # Get tofu outputs for hashing (before resolution)
    tofu_outputs = get_all_tofu_outputs(env_path)
    tofu_outputs_json = json.dumps(tofu_outputs, sort_keys=True)

    # Resolve the config
    resolved = load_environment_config(env_path)

    # Strip the [aws] section — CI/CD doesn't use named profiles
    resolved.pop("aws", None)

    # Add _meta block
    resolved["_meta"] = build_meta(
        environment=environment,
        environment_type=get_environment_type(resolved),
        config_toml_content=config_toml_content,
        tofu_outputs_json=tofu_outputs_json,
    )

    return resolved


def verify_config(environment: str, resolved_config: dict) -> bool:
    """Verify a resolved config is still fresh.

    Compares the stored tofu_outputs_hash against the current outputs.

    Args:
        environment: Environment name.
        resolved_config: Previously resolved config dict (with _meta).

    Returns:
        True if config is still fresh, False if stale.
    """
    env_path = get_environment_path(environment)
    meta = resolved_config.get("_meta", {})

    # Check tofu outputs hash
    stored_tofu_hash = meta.get("tofu_outputs_hash")
    if stored_tofu_hash:
        tofu_outputs = get_all_tofu_outputs(env_path)
        current_tofu_hash = _compute_hash(json.dumps(tofu_outputs, sort_keys=True))
        if current_tofu_hash != stored_tofu_hash:
            return False

    # Check config.toml hash
    stored_config_hash = meta.get("config_toml_hash")
    if stored_config_hash:
        config_content = (env_path / "config.toml").read_text()
        current_config_hash = _compute_hash(config_content)
        if current_config_hash != stored_config_hash:
            return False

    return True


def push_to_s3(environment: str, config_json: str) -> str:
    """Push resolved config JSON to S3.

    Discovers the resolved-configs bucket by convention
    (deployer-resolved-configs-{account_id}).

    Args:
        environment: Environment name (used as S3 key prefix).
        config_json: JSON string to upload.

    Returns:
        S3 URI of the uploaded file.

    Raises:
        RuntimeError: If S3 push fails.
    """
    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    bucket = f"deployer-resolved-configs-{account_id}"
    key = f"{environment}/config.json"

    try:
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=config_json.encode("utf-8"),
            ContentType="application/json",
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        raise RuntimeError(
            f"Failed to push to s3://{bucket}/{key}: {error_code} - {error_msg}"
        ) from e

    return f"s3://{bucket}/{key}"


@click.command()
@click.argument("environment")
@click.option(
    "--output",
    "-o",
    "output_file",
    metavar="FILE",
    help="Write resolved config to file (default: stdout)",
)
@click.option(
    "--push-s3",
    is_flag=True,
    help="Push resolved config to the S3 bucket (deployer-resolved-configs-*)",
)
@click.option("--verify", is_flag=True, help="Verify an existing resolved config is still fresh")
@click.option(
    "--verify-file",
    metavar="FILE",
    help="Path to resolved config JSON to verify (used with --verify)",
)
def cli(environment, output_file, push_s3, verify, verify_file):
    """Resolve an environment's config.toml into standalone JSON.

    \b
    Examples:
      python resolve-config.py myapp-staging
      python resolve-config.py myapp-staging --output resolved.json
      python resolve-config.py myapp-staging --verify
    """
    # Configure AWS profile (needs infra profile for tofu outputs)
    try:
        configure_aws_profile_for_environment("infra", environment, validate=True)
    except RuntimeError as e:
        log_error(str(e))
        sys.exit(1)

    if verify:
        # Verify mode: check if resolved config is still fresh
        verify_path = verify_file or output_file
        if not verify_path:
            log_error("--verify requires --verify-file or --output to specify the file to check")
            sys.exit(1)

        verify_path = Path(verify_path)
        if not verify_path.exists():
            log_error(f"Resolved config not found: {verify_path}")
            sys.exit(1)

        with open(verify_path) as f:
            resolved = json.load(f)

        log(f"Verifying resolved config for {environment}...")
        if verify_config(environment, resolved):
            meta = resolved.get("_meta", {})
            log_success(f"Config is fresh (resolved at {meta.get('resolved_at', 'unknown')})")
            sys.exit(0)
        else:
            log_error(
                "Config is STALE — infrastructure or config.toml has changed since resolution"
            )
            log_error("Re-run: python resolve-config.py {environment} --output {file}")
            sys.exit(1)

    # Resolve mode: generate resolved config JSON
    try:
        log(f"Resolving config for {environment}...")
        resolved = resolve_config(environment)
    except FileNotFoundError as e:
        log_error(str(e))
        sys.exit(1)
    except RuntimeError as e:
        log_error(f"Failed to resolve config: {e}")
        sys.exit(1)
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)

    output_json = json.dumps(resolved, indent=2)

    if output_file:
        output_path = Path(output_file)
        output_path.write_text(output_json + "\n")
        log_success(f"Resolved config written to {output_path}")
        meta = resolved["_meta"]
        log(f"  Environment: {meta['environment']}")
        log(f"  Type:        {meta['environment_type']}")
        log(f"  Resolved at: {meta['resolved_at']}")
    elif not push_s3:
        # Only print to stdout if not pushing to S3 (and no --output)
        print(output_json)

    if push_s3:
        try:
            s3_uri = push_to_s3(environment, output_json)
            log_success(f"Resolved config pushed to {s3_uri}")
        except RuntimeError as e:
            log_error(str(e))
            sys.exit(1)


if __name__ == "__main__":
    cli()
