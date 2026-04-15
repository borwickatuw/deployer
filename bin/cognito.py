#!/usr/bin/env python3
"""
Manage Cognito user access for environments.

Each Cognito-enabled environment has its own user pool, so access is managed separately.
The email address is used as both the Cognito username and email attribute.

Usage:
    # List users across all Cognito-enabled environments
    python bin/cognito.py list

    # List users for a specific environment
    python bin/cognito.py list myapp-staging

    # Create a user
    python bin/cognito.py create myapp-staging --email alice@example.com

    # Disable a user (prevents login but keeps account)
    python bin/cognito.py disable myapp-staging --email alice@example.com

    # Enable a previously disabled user
    python bin/cognito.py enable myapp-staging --email alice@example.com

    # Delete a user
    python bin/cognito.py delete myapp-staging --email alice@example.com

    # Reset a user's password
    python bin/cognito.py reset-password myapp-staging --email alice@example.com -p "NewPass123"
"""

import sys
from pathlib import Path

import click

from deployer.aws import cognito
from deployer.core.cognito import (
    copy_to_clipboard,
    format_user,
    format_welcome_message,
    generate_temp_password,
)
from deployer.core.config import (
    get_cognito_user_pool_id_from_config,
    get_staging_url_from_config,
    is_cognito_enabled,
    load_environment_config,
)
from deployer.utils import (
    EnvironmentConfigError,
    configure_aws_profile,
    configure_aws_profile_for_environment,
    get_all_environments,
    get_environment_path,
    get_environments_dir,
    require_validated_environment,
)


def _configure_aws(environment: str | None) -> None:
    """Configure AWS profile for the given environment."""
    if environment:
        configure_aws_profile_for_environment("cognito", environment)
    else:
        configure_aws_profile("cognito")


def get_cognito_environments() -> list[str]:
    """Find all environments with Cognito enabled.

    Returns:
        Sorted list of environment names that have Cognito enabled.
    """
    cognito_envs = []
    for env_name in get_all_environments(get_environments_dir()):
        env_path = get_environment_path(env_name)
        state_file = env_path / "terraform.tfstate"
        if not state_file.exists():
            continue
        try:
            config = load_environment_config(env_path)
            if is_cognito_enabled(config):
                cognito_envs.append(env_name)
        except (FileNotFoundError, RuntimeError):
            continue
    return cognito_envs


def print_users_table(users: list[dict], indent: str = "") -> None:
    """Print users in a formatted table."""
    if not users:
        print(f"{indent}No users found.")
        return

    # Column widths
    email_width = max(len(u["email"] or u["username"]) for u in users)
    email_width = max(email_width, len("Email"))

    # Header
    print(f"{indent}{'Email':<{email_width}}  {'Status':<20}  {'Enabled':<8}  {'Created':<16}")
    print(f"{indent}{'-' * email_width}  {'-' * 20}  {'-' * 8}  {'-' * 16}")

    # Rows
    for user in users:
        enabled_str = "Yes" if user["enabled"] else "NO"
        email_display = user["email"] or user["username"]
        print(
            f"{indent}{email_display:<{email_width}}  {user['status']:<20}  {enabled_str:<8}  {user['created'] or 'N/A':<16}"
        )


def resolve_environment(env_name: str) -> tuple[Path, str, dict]:
    """Resolve environment name to path, user pool ID, and config.

    Returns:
        Tuple of (env_path, user_pool_id, resolved_config).

    Raises:
        SystemExit: If the environment is invalid or Cognito is not enabled.
    """
    try:
        env_path, config = require_validated_environment(env_name)
    except EnvironmentConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1) from None

    user_pool_id = get_cognito_user_pool_id_from_config(config)
    if not user_pool_id:
        print(f"Error: Cognito auth not enabled for '{env_name}'", file=sys.stderr)
        raise SystemExit(1)

    return env_path, user_pool_id, config


# =============================================================================
# Commands
# =============================================================================


def cmd_list(environment: str | None) -> int:
    """List users in Cognito-enabled environments.

    Deduplicates by user pool ID so shared pools are only listed once,
    with all environments that use them shown together.
    """
    environments = [environment] if environment else get_cognito_environments()

    if not environments:
        print("No Cognito-enabled environments found.", file=sys.stderr)
        return 1

    # Group environments by pool ID to deduplicate shared pools
    pools: dict[str, list[str]] = {}  # pool_id -> [env_names]
    errors: list[str] = []

    for env_name in environments:
        env_path = get_environment_path(env_name)

        if not env_path.exists():
            continue

        state_file = env_path / "terraform.tfstate"
        if not state_file.exists():
            errors.append(f"  {env_name}: Not deployed")
            continue

        try:
            config = load_environment_config(env_path)
        except (FileNotFoundError, RuntimeError) as e:
            errors.append(f"  {env_name}: Error loading config: {e}")
            continue

        user_pool_id = get_cognito_user_pool_id_from_config(config)
        if not user_pool_id:
            continue

        if user_pool_id not in pools:
            pools[user_pool_id] = []
        pools[user_pool_id].append(env_name)

    # Print any errors from environments we couldn't resolve
    for error in errors:
        print(error)

    if not pools:
        print("No Cognito-enabled environments found.", file=sys.stderr)
        return 1

    total_users = 0

    for user_pool_id, env_names in pools.items():
        # Try to get the pool's display name from AWS
        pool_name = cognito.get_user_pool_name(user_pool_id)

        print(f"\n{'=' * 60}")
        if pool_name:
            print(f"User Pool: {pool_name} ({user_pool_id})")
        else:
            print(f"User Pool: {user_pool_id}")
        print(f"Environments: {', '.join(env_names)}")
        print(f"{'=' * 60}")

        raw_users = cognito.list_users(user_pool_id)
        users = [format_user(u) for u in raw_users]

        print(f"\n  Users: {len(users)}\n")
        print_users_table(users, indent="  ")

        total_users += len(users)

    if len(pools) > 1:
        print(f"\nTotal: {total_users} user(s) across {len(pools)} pool(s)")

    return 0


def cmd_create(environment: str, email: str, password: str | None, clipboard: bool) -> int:
    """Create a new user."""
    env_path, user_pool_id, config = resolve_environment(environment)

    # Validate email format
    if "@" not in email:
        print(f"Error: Invalid email address: {email}", file=sys.stderr)
        return 1

    print(f"Creating user in {environment}...")
    print(f"  Email: {email}")

    is_temporary = not password
    pwd = password or generate_temp_password()

    # Create the user (email is used as the Cognito username)
    success, error = cognito.create_user(
        user_pool_id=user_pool_id,
        username=email,
        email=email,
        password=pwd,
        suppress_email=True,
    )

    if not success:
        print(f"Error creating user: {error}", file=sys.stderr)
        return 1

    # If password was provided, set it as permanent
    if password:
        success, error = cognito.set_user_password(
            user_pool_id=user_pool_id,
            username=email,
            password=password,
            permanent=True,
        )
        if not success:
            print(f"Warning: User created but failed to set password: {error}", file=sys.stderr)
            return 1

    print("\nUser created successfully.")

    # Build welcome message
    message = format_welcome_message(
        environment=environment,
        email=email,
        password=pwd,
        url=get_staging_url_from_config(config),
        is_temporary=is_temporary,
    )

    print("\n--- Welcome message ---")
    print(message)
    print("--- End message ---\n")

    if is_temporary:
        print("The user will be prompted to change their password on first login.\n")

    # Handle clipboard
    should_copy = clipboard
    if not should_copy:
        try:
            response = input("Copy to clipboard? [Y/n] ").strip().lower()
            should_copy = response in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            print()
            should_copy = False

    if should_copy:
        if copy_to_clipboard(message):
            print("Copied to clipboard!")
        else:
            print("(Could not copy to clipboard - please copy manually)", file=sys.stderr)

    return 0


def cmd_delete(environment: str, email: str, force: bool) -> int:
    """Delete a user."""
    _env_path, user_pool_id, _config = resolve_environment(environment)

    # Confirm deletion
    if not force:
        print(f"This will permanently delete user '{email}' from {environment}.")
        response = input("Are you sure? [y/N]: ").strip().lower()
        if response != "y":
            print("Cancelled.")
            return 0

    success, error = cognito.delete_user(user_pool_id, email)

    if not success:
        print(f"Error deleting user: {error}", file=sys.stderr)
        return 1

    print(f"User '{email}' deleted from {environment}.")
    return 0


def cmd_disable(environment: str, email: str) -> int:
    """Disable a user (prevent login)."""
    _env_path, user_pool_id, _config = resolve_environment(environment)

    success, error = cognito.disable_user(user_pool_id, email)

    if not success:
        print(f"Error disabling user: {error}", file=sys.stderr)
        return 1

    print(f"User '{email}' disabled in {environment}.")
    print("The user will no longer be able to log in.")
    return 0


def cmd_enable(environment: str, email: str) -> int:
    """Enable a previously disabled user."""
    _env_path, user_pool_id, _config = resolve_environment(environment)

    success, error = cognito.enable_user(user_pool_id, email)

    if not success:
        print(f"Error enabling user: {error}", file=sys.stderr)
        return 1

    print(f"User '{email}' enabled in {environment}.")
    return 0


def cmd_reset_password(environment: str, email: str, password: str | None, permanent: bool) -> int:
    """Reset a user's password."""
    _env_path, user_pool_id, _config = resolve_environment(environment)

    pwd = password or generate_temp_password()

    success, error = cognito.set_user_password(
        user_pool_id=user_pool_id,
        username=email,
        password=pwd,
        permanent=permanent,
    )

    if not success:
        print(f"Error resetting password: {error}", file=sys.stderr)
        return 1

    print(f"Password reset for '{email}' in {environment}.")

    if not password:
        print(f"New password: {pwd}")

    if not permanent:
        print("The user will be prompted to change their password on next login.")

    return 0


# =============================================================================
# CLI
# =============================================================================


@click.group()
def cli():
    """Manage Cognito user access for environments."""


# pysmelly: ignore shotgun-surgery — Click's @cli.command() pattern inherently spans files
@cli.command("list")
@click.argument("environment", required=False)
def list_cmd(environment):
    """List users in Cognito-enabled environments."""
    _configure_aws(environment)
    sys.exit(cmd_list(environment))


@cli.command()
@click.argument("environment")
@click.option("--email", "-e", required=True, help="Email address (used as username)")
@click.option("-p", "--password", help="Set permanent password (otherwise temporary is generated)")
@click.option(
    "-c", "--clipboard", is_flag=True, help="Copy welcome message with credentials to clipboard"
)
def create(environment, email, password, clipboard):
    """Create a new user."""
    _configure_aws(environment)
    sys.exit(cmd_create(environment, email, password, clipboard))


@cli.command("delete")
@click.argument("environment")
@click.option("--email", "-e", required=True, help="Email address of user to delete")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation prompt")
def delete_cmd(environment, email, force):
    """Delete a user."""
    _configure_aws(environment)
    sys.exit(cmd_delete(environment, email, force))


@cli.command()
@click.argument("environment")
@click.option("--email", "-e", required=True, help="Email address of user to disable")
def disable(environment, email):
    """Disable a user (prevent login)."""
    _configure_aws(environment)
    sys.exit(cmd_disable(environment, email))


@cli.command()
@click.argument("environment")
@click.option("--email", "-e", required=True, help="Email address of user to enable")
def enable(environment, email):
    """Enable a disabled user."""
    _configure_aws(environment)
    sys.exit(cmd_enable(environment, email))


@cli.command("reset-password")
@click.argument("environment")
@click.option("--email", "-e", required=True, help="Email address")
@click.option("-p", "--password", help="New password (otherwise generated)")
@click.option("--permanent", is_flag=True, help="Set as permanent (no change required)")
def reset_password(environment, email, password, permanent):
    """Reset a user's password."""
    _configure_aws(environment)
    sys.exit(cmd_reset_password(environment, email, password, permanent))


if __name__ == "__main__":
    cli()
