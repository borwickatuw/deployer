"""Generate AWS CLI profiles for deployer roles."""

import sys
from pathlib import Path

import click

from .bootstrap import detect_aws_account_id

# Profile name -> IAM role name
ROLE_PROFILES = {
    "deployer-app": "deployer-app-deploy",
    "deployer-infra": "deployer-infra-admin",
    "deployer-cognito": "deployer-cognito-admin",
}


def generate_profile_config(
    account_id: str,
    region: str,
    source_profile: str,
) -> str:
    """Generate AWS CLI config file entries for deployer profiles.

    Returns the text to append to ~/.aws/config.
    """
    lines = [
        f"[profile {source_profile}]",
        f"region = {region}",
        "output = json",
        "",
    ]

    for profile_name, role_name in ROLE_PROFILES.items():
        lines.extend([
            f"[profile {profile_name}]",
            f"role_arn = arn:aws:iam::{account_id}:role/{role_name}",
            f"source_profile = {source_profile}",
            f"region = {region}",
            "",
        ])

    return "\n".join(lines)


def _find_existing_profiles(config_path: Path) -> list[str]:
    """Check which deployer profiles already exist in ~/.aws/config."""
    if not config_path.exists():
        return []

    content = config_path.read_text()
    found = []
    for profile_name in ROLE_PROFILES:
        if f"[profile {profile_name}]" in content:
            found.append(profile_name)
    return found


def cmd_setup_profiles(dry_run: bool) -> int:
    """Generate and optionally write AWS CLI profile configuration."""
    # Collect inputs
    detected_id = detect_aws_account_id()
    account_id = click.prompt(
        "AWS Account ID", default=detected_id or "", type=str
    ).strip()
    if not account_id or not account_id.isdigit() or len(account_id) != 12:
        print("Error: AWS Account ID must be exactly 12 digits.", file=sys.stderr)
        return 1

    region = click.prompt("AWS Region", default="us-west-2", type=str).strip()
    source_profile = click.prompt(
        "Source profile name (base credentials)", default="deployer", type=str
    ).strip()

    # Generate config text
    config_text = generate_profile_config(account_id, region, source_profile)

    config_path = Path.home() / ".aws" / "config"

    # Check for existing profiles
    existing = _find_existing_profiles(config_path)
    if existing:
        print(
            f"Warning: These profiles already exist in {config_path}:",
            file=sys.stderr,
        )
        for name in existing:
            print(f"  {name}", file=sys.stderr)
        print("\nGenerated config (not written):\n")
        print(config_text)
        return 0

    if dry_run:
        print(f"Would append to {config_path}:\n")
        print(config_text)
        return 0

    # Offer to append
    print(f"Will append to {config_path}:\n")
    print(config_text)

    if not click.confirm("Append to config file?", default=True):
        print("Not written. Copy the text above into your config manually.")
        return 0

    # Ensure ~/.aws directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "a") as f:
        # Add a newline separator if the file isn't empty
        if config_path.stat().st_size > 0:
            f.write("\n")
        f.write(config_text)

    print(f"Profiles written to {config_path}")
    print()
    print("Next: add credentials to ~/.aws/credentials:")
    print(f"  [{source_profile}]")
    print("  aws_access_key_id = YOUR_ACCESS_KEY")
    print("  aws_secret_access_key = YOUR_SECRET_KEY")
    return 0
