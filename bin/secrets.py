#!/usr/bin/env python3
"""
Manage SSM Parameter Store secrets for environments.

Secrets are stored in SSM Parameter Store and referenced in deploy.toml.
ECS tasks fetch secrets at runtime using their task execution role.

Naming convention: /{project}/{environment}/{secret-name}
Example: /myapp/staging/SECRET_KEY

Usage:
    # Check secrets: missing from SSM, or extra (in SSM but not in deploy.toml)
    python bin/secrets.py check ../app/deploy.toml myapp-staging

    # Set a secret (prompts for value or offers to generate random)
    python bin/secrets.py put myapp-staging SECRET_KEY

    # Set a secret with a random value (default 32 chars)
    python bin/secrets.py put myapp-staging SECRET_KEY --random

    # Set a secret with a random value of specific length
    python bin/secrets.py put myapp-staging SECRET_KEY --random 64

    # Set a secret with value from command line (less secure - visible in history)
    python bin/secrets.py put myapp-staging SECRET_KEY --value "my-secret-value"

    # Set a secret with value from file
    python bin/secrets.py put myapp-staging SSL_CERT --from-file /path/to/cert.pem

    # List all secrets for an environment
    python bin/secrets.py list myapp-staging

    # Get a secret value
    python bin/secrets.py get myapp-staging SECRET_KEY

    # Delete a secret
    python bin/secrets.py delete myapp-staging SECRET_KEY
"""

import argparse
import getpass
import secrets as secrets_module
import sys
from datetime import datetime
from pathlib import Path

from deployer.aws import ssm
from deployer.core.ssm_secrets import (
    get_parameter_path,
    get_path_prefix,
    get_secrets_from_deploy_toml,
)
from deployer.core.ssm_secrets import parse_environment as _parse_environment
from deployer.utils import (
    configure_aws_profile,
    configure_aws_profile_for_environment,
    get_linked_deploy_toml,
)


def parse_environment(env_name: str) -> tuple[str, str]:
    """Parse environment name into project and environment.

    CLI wrapper that exits on error.

    Args:
        env_name: Environment name like "myapp-staging"

    Returns:
        Tuple of (project, environment)
    """
    try:
        return _parse_environment(env_name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def generate_random_secret(length: int = 32) -> str:
    """Generate a cryptographically secure random string.

    Args:
        length: Desired length of the secret (default 32)

    Returns:
        URL-safe base64 string of the specified length
    """
    # token_urlsafe generates ~1.3 chars per byte, so request enough bytes
    return secrets_module.token_urlsafe(length)[:length]


# =============================================================================
# Commands
# =============================================================================


def cmd_check(args) -> int:
    """Check which secrets from deploy.toml are missing in SSM, and which SSM secrets are unused."""
    # Resolve deploy.toml path: explicit --deploy-toml, or linked, or error
    deploy_toml_path = None
    used_explicit_flag = False

    if args.deploy_toml:
        deploy_toml_path = Path(args.deploy_toml).expanduser().resolve()
        used_explicit_flag = True
    else:
        linked_path = get_linked_deploy_toml(args.environment)
        if linked_path:
            deploy_toml_path = linked_path
            print(f"Using linked deploy.toml: {deploy_toml_path}")
        else:
            print(f"Error: No deploy.toml linked for '{args.environment}'", file=sys.stderr)
            print(f"\nTo link: python bin/link-environments.py {args.environment} /path/to/deploy.toml", file=sys.stderr)
            print(f"Or specify: secrets.py check {args.environment} --deploy-toml /path/to/deploy.toml", file=sys.stderr)
            return 1

    if not deploy_toml_path.exists():
        print(f"Error: File not found: {deploy_toml_path}", file=sys.stderr)
        return 1

    # Print tip if --deploy-toml was explicitly provided
    if used_explicit_flag:
        print(f"Tip: Run 'python bin/link-environments.py {args.environment} {deploy_toml_path}'")
        print(f"     to check with just: secrets.py check {args.environment}\n")

    # Parse environment name
    project, environment = parse_environment(args.environment)

    print(f"Checking secrets for {args.environment}...")
    print(f"Reading: {deploy_toml_path}\n")

    # Get required secrets from deploy.toml
    try:
        required_secrets = get_secrets_from_deploy_toml(deploy_toml_path, environment)
    except Exception as e:
        print(f"Error parsing deploy.toml: {e}", file=sys.stderr)
        return 1

    # Get all existing secrets from SSM for this environment
    path_prefix = get_path_prefix(args.environment)
    existing_params, error = ssm.list_parameters(path_prefix)
    if error:
        print(f"Error listing SSM parameters: {error}", file=sys.stderr)
        return 1

    # Build set of existing SSM paths
    existing_paths = {p["name"] for p in existing_params}

    # Check each required secret
    missing = []
    present = []
    required_paths = set()

    for env_var, ssm_path in sorted(required_secrets.items()):
        required_paths.add(ssm_path)
        if ssm_path in existing_paths:
            present.append((env_var, ssm_path))
        else:
            missing.append((env_var, ssm_path))

    # Find extra secrets (in SSM but not in deploy.toml)
    extra_paths = existing_paths - required_paths
    extra = [(path.split("/")[-1], path) for path in sorted(extra_paths)]

    # Report results
    if required_secrets or extra:
        all_names = [s[0] for s in required_secrets.items()] + [e[0] for e in extra]
        name_width = max(len(n) for n in all_names) if all_names else 20
        name_width = max(name_width, len("Environment Variable"))

        print(f"{'Environment Variable':<{name_width}}  {'SSM Path':<50}  {'Status'}")
        print(f"{'-' * name_width}  {'-' * 50}  {'-' * 10}")

        for env_var, ssm_path in present:
            print(f"{env_var:<{name_width}}  {ssm_path:<50}  OK")

        for env_var, ssm_path in missing:
            print(f"{env_var:<{name_width}}  {ssm_path:<50}  MISSING")

        for secret_name, ssm_path in extra:
            print(f"{secret_name:<{name_width}}  {ssm_path:<50}  EXTRA")

        print()
        print(f"Required: {len(required_secrets)} secret(s)")
        print(f"Present: {len(present)}, Missing: {len(missing)}, Extra: {len(extra)}")
    else:
        print("No SSM secrets defined in deploy.toml and none found in SSM.")
        return 0

    if missing:
        print("\nTo set missing secrets, run:")
        for env_var, ssm_path in missing:
            secret_name = ssm_path.split("/")[-1]
            print(f"  uv run python bin/secrets.py put {args.environment} {secret_name}")

    if extra:
        print("\nExtra secrets not referenced in deploy.toml:")
        for secret_name, ssm_path in extra:
            print(f"  uv run python bin/secrets.py delete {args.environment} {secret_name}")

    if missing or extra:
        return 1

    return 0


def cmd_put(args) -> int:
    """Create or update a secret."""
    param_path = get_parameter_path(args.environment, args.secret_name)

    # Get the value
    if args.from_file:
        try:
            with open(args.from_file) as f:
                value = f.read()
        except FileNotFoundError:
            print(f"Error: File not found: {args.from_file}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            return 1
    elif args.value:
        value = args.value
    elif args.random is not None:
        # Generate random value (args.random is the length, default 32)
        length = args.random
        value = generate_random_secret(length)
        print(f"Generated random value ({length} chars): {value}")
    else:
        # Interactive mode - offer choice
        try:
            print(f"Set value for {args.secret_name}:")
            print("  [1] Enter value manually")
            print("  [2] Generate random value (32 chars)")
            choice = input("Choice [1/2]: ").strip()

            if choice == "2":
                value = generate_random_secret(32)
                print(f"Generated: {value}")
            else:
                # Default to manual entry
                value = getpass.getpass(f"Enter value for {args.secret_name}: ")
                if not value:
                    print("Error: Value cannot be empty", file=sys.stderr)
                    return 1
                # Confirm the value
                confirm = getpass.getpass("Confirm value: ")
                if value != confirm:
                    print("Error: Values do not match", file=sys.stderr)
                    return 1
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 1

    # Check if parameter exists
    exists = ssm.parameter_exists(param_path)
    action = "Updating" if exists else "Creating"

    print(f"{action} secret: {param_path}")

    success, error = ssm.put_parameter(
        name=param_path,
        value=value,
        description=f"Secret for {args.environment}",
        overwrite=True,
    )

    if not success:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Secret {'updated' if exists else 'created'} successfully.")
    return 0


def cmd_get(args) -> int:
    """Get a secret value."""
    param_path = get_parameter_path(args.environment, args.secret_name)

    value, error = ssm.get_parameter(param_path)

    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.quiet:
        print(value)
    else:
        print(f"Secret: {param_path}")
        print(f"Value: {value}")

    return 0


def cmd_list(args) -> int:
    """List all secrets for an environment."""
    path_prefix = get_path_prefix(args.environment)

    print(f"Secrets in {args.environment} ({path_prefix}):\n")

    parameters, error = ssm.list_parameters(path_prefix)

    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if not parameters:
        print("No secrets found.")
        return 0

    # Calculate column widths
    name_width = max(len(p["name"].split("/")[-1]) for p in parameters)
    name_width = max(name_width, len("Name"))

    # Print header
    print(f"{'Name':<{name_width}}  {'Last Modified':<20}  {'Description'}")
    print(f"{'-' * name_width}  {'-' * 20}  {'-' * 40}")

    # Print rows
    for param in sorted(parameters, key=lambda p: p["name"]):
        name = param["name"].split("/")[-1]
        last_modified = param.get("last_modified")
        if last_modified:
            if isinstance(last_modified, datetime):
                last_modified = last_modified.strftime("%Y-%m-%d %H:%M:%S")
            else:
                last_modified = str(last_modified)[:20]
        else:
            last_modified = "N/A"
        description = param.get("description", "")[:40]
        print(f"{name:<{name_width}}  {last_modified:<20}  {description}")

    print(f"\nTotal: {len(parameters)} secret(s)")
    return 0


def cmd_delete(args) -> int:
    """Delete a secret."""
    param_path = get_parameter_path(args.environment, args.secret_name)

    # Confirm deletion
    if not args.force:
        print(f"This will permanently delete: {param_path}")
        response = input("Are you sure? [y/N]: ").strip().lower()
        if response != "y":
            print("Cancelled.")
            return 0

    success, error = ssm.delete_parameter(param_path)

    if not success:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Secret deleted: {param_path}")
    return 0


# =============================================================================
# Main
# =============================================================================


def main():
    # Parse arguments first to get the environment name for profile configuration
    parser = argparse.ArgumentParser(
        description="Manage SSM Parameter Store secrets for environments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s check myapp-staging                     Check which secrets are missing (uses linked deploy.toml)
  %(prog)s check myapp-staging --deploy-toml ../app/deploy.toml
  %(prog)s put myapp-staging SECRET_KEY            Set a secret (prompts or random)
  %(prog)s put myapp-staging SECRET_KEY --random   Generate random 32-char value
  %(prog)s put myapp-staging SECRET_KEY -r 64      Generate random 64-char value
  %(prog)s put myapp-staging SECRET_KEY -v "val"   Set a secret with value
  %(prog)s put myapp-staging CERT --from-file x    Set a secret from file
  %(prog)s list myapp-staging                      List all secrets
  %(prog)s get myapp-staging SECRET_KEY            Get a secret value
  %(prog)s delete myapp-staging SECRET_KEY         Delete a secret

SSM Parameter naming convention:
  /{project}/{environment}/{secret-name}
  Example: /myapp/staging/SECRET_KEY

In deploy.toml, reference secrets using:
  [secrets]
  SECRET_KEY = "ssm:/myapp/${environment}/SECRET_KEY"
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # check
    check_parser = subparsers.add_parser("check", help="Check for missing or extra secrets")
    check_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    check_parser.add_argument(
        "--deploy-toml",
        metavar="PATH",
        help="Path to deploy.toml (optional if environment is linked)"
    )

    # put
    put_parser = subparsers.add_parser("put", help="Create or update a secret")
    put_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    put_parser.add_argument("secret_name", help="Secret name (e.g., SECRET_KEY)")
    put_parser.add_argument("-v", "--value", help="Secret value (prompts if not provided)")
    put_parser.add_argument("--from-file", help="Read secret value from file")
    put_parser.add_argument(
        "-r", "--random",
        nargs="?",
        const=32,
        type=int,
        metavar="LENGTH",
        help="Generate random value (default 32 chars)",
    )

    # get
    get_parser = subparsers.add_parser("get", help="Get a secret value")
    get_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    get_parser.add_argument("secret_name", help="Secret name (e.g., SECRET_KEY)")
    get_parser.add_argument("-q", "--quiet", action="store_true", help="Output only the value")

    # list
    list_parser = subparsers.add_parser("list", help="List secrets in an environment")
    list_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")

    # delete
    delete_parser = subparsers.add_parser("delete", help="Delete a secret")
    delete_parser.add_argument("environment", help="Environment name (e.g., myapp-staging)")
    delete_parser.add_argument("secret_name", help="Secret name (e.g., SECRET_KEY)")
    delete_parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    # Configure AWS profile before any boto3 clients are created
    # Uses environment-specific profile from config.toml if available
    env_name = getattr(args, 'environment', None)
    if env_name:
        configure_aws_profile_for_environment("secrets", env_name)
    else:
        configure_aws_profile("secrets")

    # Dispatch to command handler
    commands = {
        "check": cmd_check,
        "put": cmd_put,
        "get": cmd_get,
        "list": cmd_list,
        "delete": cmd_delete,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
