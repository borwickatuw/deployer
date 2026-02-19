#!/usr/bin/env python3
"""
Manage Cognito user access for environments.

Each Cognito-enabled environment has its own user pool, so access is managed separately.

Usage:
    # List users across all Cognito-enabled environments
    python bin/cognito.py list

    # List users for a specific environment
    python bin/cognito.py list myapp-staging

    # Create a user
    python bin/cognito.py create myapp-staging --username alice@example.com

    # Create a user and copy welcome message to clipboard
    python bin/cognito.py create myapp-staging --username alice@example.com --clipboard

    # Create a user with a specific password
    python bin/cognito.py create myapp-staging --username alice@example.com -p "SecurePass123"

    # Disable a user (prevents login but keeps account)
    python bin/cognito.py disable myapp-staging --username alice@example.com

    # Enable a previously disabled user
    python bin/cognito.py enable myapp-staging --username alice@example.com

    # Delete a user
    python bin/cognito.py delete myapp-staging --username alice@example.com

    # Reset a user's password
    python bin/cognito.py reset-password myapp-staging --username alice@example.com -p "NewPass123"
"""

import argparse
import sys
from pathlib import Path

from deployer.aws import cognito
from deployer.core import (
    copy_to_clipboard,
    format_user,
    format_welcome_message,
    generate_temp_password,
    get_cognito_user_pool_id_from_config,
    get_staging_url_from_config,
    is_cognito_enabled,
    load_environment_config,
)
from deployer.utils import (
    configure_aws_profile,
    configure_aws_profile_for_environment,
    get_all_environments,
    get_environment_path,
    get_environments_dir,
    validate_environment_deployed,
)


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
    username_width = max(len(u["username"]) for u in users)
    username_width = max(username_width, len("Username"))
    email_width = max(len(u["email"]) for u in users)
    email_width = max(email_width, len("Email"))

    # Header
    print(
        f"{indent}{'Username':<{username_width}}  {'Email':<{email_width}}  {'Status':<20}  {'Enabled':<8}  {'Created':<16}"
    )
    print(f"{indent}{'-' * username_width}  {'-' * email_width}  {'-' * 20}  {'-' * 8}  {'-' * 16}")

    # Rows
    for user in users:
        enabled_str = "Yes" if user["enabled"] else "NO"
        print(
            f"{indent}{user['username']:<{username_width}}  {user['email']:<{email_width}}  {user['status']:<20}  {enabled_str:<8}  {user['created'] or 'N/A':<16}"
        )


def resolve_environment(env_name: str) -> tuple[Path, str, dict] | None:
    """Resolve environment name to path, user pool ID, and config.

    Returns:
        Tuple of (env_path, user_pool_id, resolved_config), or None on error.
    """
    env_path, error = validate_environment_deployed(env_name)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return None

    try:
        config = load_environment_config(env_path)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return None

    user_pool_id = get_cognito_user_pool_id_from_config(config)
    if not user_pool_id:
        print(f"Error: Cognito auth not enabled for '{env_name}'", file=sys.stderr)
        return None

    return env_path, user_pool_id, config


# =============================================================================
# Commands
# =============================================================================


def cmd_list(args) -> int:
    """List users in Cognito-enabled environments."""
    if args.environment:
        environments = [args.environment]
    else:
        environments = get_cognito_environments()

    if not environments:
        print("No Cognito-enabled environments found.", file=sys.stderr)
        return 1

    results = []

    for env_name in environments:
        env_path = get_environment_path(env_name)

        if not env_path.exists():
            continue

        print(f"\n{'=' * 60}")
        print(f"Environment: {env_name}")
        print(f"{'=' * 60}")

        state_file = env_path / "terraform.tfstate"
        if not state_file.exists():
            print("  Status: Not deployed")
            results.append({"env": env_name, "count": 0, "status": "not_deployed"})
            continue

        # Load config from config.toml
        try:
            config = load_environment_config(env_path)
        except (FileNotFoundError, RuntimeError) as e:
            print(f"  Error loading config: {e}")
            results.append({"env": env_name, "count": 0, "status": "config_error"})
            continue

        user_pool_id = get_cognito_user_pool_id_from_config(config)
        if not user_pool_id:
            print("  Status: Cognito auth not enabled")
            results.append({"env": env_name, "count": 0, "status": "no_cognito"})
            continue

        print(f"  User Pool ID: {user_pool_id}\n")

        raw_users = cognito.list_users(user_pool_id)
        users = [format_user(u) for u in raw_users]

        print(f"  Users: {len(users)}\n")
        print_users_table(users, indent="  ")

        results.append({"env": env_name, "count": len(users), "status": "active"})

    # Summary
    if len(environments) > 1:
        print(f"\n{'=' * 60}")
        print("Summary")
        print(f"{'=' * 60}")
        total = 0
        for r in results:
            if r["status"] == "active":
                print(f"  {r['env']}: {r['count']} user(s)")
                total += r["count"]
            else:
                print(f"  {r['env']}: {r['status'].replace('_', ' ').title()}")
        print(f"\n  Total: {total} user(s)")

    return 0


def cmd_create(args) -> int:
    """Create a new user."""
    result = resolve_environment(args.environment)
    if not result:
        return 1

    env_path, user_pool_id, config = result
    username = args.username
    email = args.email or username  # Default email to username if not provided

    # Validate email format
    if "@" not in email:
        if not args.email:
            print(
                f"Error: Invalid email address: {email} (using username as email since --email not provided)",
                file=sys.stderr,
            )
        else:
            print(f"Error: Invalid email address: {email}", file=sys.stderr)
        return 1

    print(f"Creating user in {args.environment}...")
    print(f"  Username: {username}")
    print(f"  Email: {email}")

    is_temporary = not args.password

    if args.password:
        password = args.password
    else:
        password = generate_temp_password()

    # Create the user
    success, error = cognito.create_user(
        user_pool_id=user_pool_id,
        username=username,
        email=email,
        password=password,
        suppress_email=True,
    )

    if not success:
        print(f"Error creating user: {error}", file=sys.stderr)
        return 1

    # If password was provided, set it as permanent
    if args.password:
        success, error = cognito.set_user_password(
            user_pool_id=user_pool_id,
            username=username,
            password=args.password,
            permanent=True,
        )
        if not success:
            print(f"Warning: User created but failed to set password: {error}", file=sys.stderr)
            return 1

    print("\nUser created successfully.")

    # Build welcome message
    url = get_staging_url_from_config(config)
    message = format_welcome_message(
        environment=args.environment,
        username=username,
        password=password,
        url=url,
        is_temporary=is_temporary,
    )

    print("\n--- Welcome message ---")
    print(message)
    print("--- End message ---\n")

    if is_temporary:
        print("The user will be prompted to change their password on first login.\n")

    # Handle clipboard
    should_copy = args.clipboard
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


def cmd_delete(args) -> int:
    """Delete a user."""
    result = resolve_environment(args.environment)
    if not result:
        return 1

    _env_path, user_pool_id, _config = result
    username = args.username

    # Confirm deletion
    if not args.force:
        print(f"This will permanently delete user '{username}' from {args.environment}.")
        response = input("Are you sure? [y/N]: ").strip().lower()
        if response != "y":
            print("Cancelled.")
            return 0

    success, error = cognito.delete_user(user_pool_id, username)

    if not success:
        print(f"Error deleting user: {error}", file=sys.stderr)
        return 1

    print(f"User '{username}' deleted from {args.environment}.")
    return 0


def cmd_disable(args) -> int:
    """Disable a user (prevent login)."""
    result = resolve_environment(args.environment)
    if not result:
        return 1

    _env_path, user_pool_id, _config = result
    username = args.username

    success, error = cognito.disable_user(user_pool_id, username)

    if not success:
        print(f"Error disabling user: {error}", file=sys.stderr)
        return 1

    print(f"User '{username}' disabled in {args.environment}.")
    print("The user will no longer be able to log in.")
    return 0


def cmd_enable(args) -> int:
    """Enable a previously disabled user."""
    result = resolve_environment(args.environment)
    if not result:
        return 1

    _env_path, user_pool_id, _config = result
    username = args.username

    success, error = cognito.enable_user(user_pool_id, username)

    if not success:
        print(f"Error enabling user: {error}", file=sys.stderr)
        return 1

    print(f"User '{username}' enabled in {args.environment}.")
    return 0


def cmd_reset_password(args) -> int:
    """Reset a user's password."""
    result = resolve_environment(args.environment)
    if not result:
        return 1

    _env_path, user_pool_id, _config = result
    username = args.username

    if args.password:
        password = args.password
    else:
        password = generate_temp_password()

    success, error = cognito.set_user_password(
        user_pool_id=user_pool_id,
        username=username,
        password=password,
        permanent=args.permanent,
    )

    if not success:
        print(f"Error resetting password: {error}", file=sys.stderr)
        return 1

    print(f"Password reset for '{username}' in {args.environment}.")

    if not args.password:
        print(f"New password: {password}")

    if not args.permanent:
        print("The user will be prompted to change their password on next login.")

    return 0


# =============================================================================
# Main
# =============================================================================


def main():
    # Parse arguments first to get the environment name for profile configuration
    parser = argparse.ArgumentParser(
        description="Manage Cognito user access for environments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list                                       List all Cognito users
  %(prog)s list myapp-staging                         List users in specific environment
  %(prog)s create myapp-staging --username user@example.com
  %(prog)s create myapp-staging --username user@example.com -c  (copy welcome to clipboard)
  %(prog)s create myapp-staging --username user@example.com -p "MyPassword123"
  %(prog)s disable myapp-staging --username user@example.com
  %(prog)s enable myapp-staging --username user@example.com
  %(prog)s delete myapp-staging --username user@example.com
  %(prog)s reset-password myapp-staging --username user@example.com
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    list_parser = subparsers.add_parser("list", help="List users in Cognito-enabled environments")
    list_parser.add_argument(
        "environment", nargs="?", help="Specific environment (default: all with Cognito)"
    )

    # create
    create_parser = subparsers.add_parser("create", help="Create a new user")
    create_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    create_parser.add_argument("--username", "-u", required=True, help="Username (typically email)")
    create_parser.add_argument("--email", help="Email address (defaults to username)")
    create_parser.add_argument(
        "-p", "--password", help="Set permanent password (otherwise temporary is generated)"
    )
    create_parser.add_argument(
        "-c",
        "--clipboard",
        action="store_true",
        help="Copy welcome message with credentials to clipboard",
    )

    # delete
    delete_parser = subparsers.add_parser("delete", help="Delete a user")
    delete_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    delete_parser.add_argument("--username", "-u", required=True, help="Username to delete")
    delete_parser.add_argument(
        "-f", "--force", action="store_true", help="Skip confirmation prompt"
    )

    # disable
    disable_parser = subparsers.add_parser("disable", help="Disable a user (prevent login)")
    disable_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    disable_parser.add_argument("--username", "-u", required=True, help="Username to disable")

    # enable
    enable_parser = subparsers.add_parser("enable", help="Enable a disabled user")
    enable_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    enable_parser.add_argument("--username", "-u", required=True, help="Username to enable")

    # reset-password
    reset_parser = subparsers.add_parser("reset-password", help="Reset a user's password")
    reset_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    reset_parser.add_argument("--username", "-u", required=True, help="Username")
    reset_parser.add_argument("-p", "--password", help="New password (otherwise generated)")
    reset_parser.add_argument(
        "--permanent", action="store_true", help="Set as permanent (no change required)"
    )

    args = parser.parse_args()

    # Configure AWS profile before any boto3 clients are created
    # Uses environment-specific profile from config.toml if available
    # Note: 'list' command may not have an environment, so fall back to default
    env_name = getattr(args, "environment", None)
    if env_name:
        configure_aws_profile_for_environment("cognito", env_name)
    else:
        configure_aws_profile("cognito")

    # Dispatch to command handler
    commands = {
        "list": cmd_list,
        "create": cmd_create,
        "delete": cmd_delete,
        "disable": cmd_disable,
        "enable": cmd_enable,
        "reset-password": cmd_reset_password,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
