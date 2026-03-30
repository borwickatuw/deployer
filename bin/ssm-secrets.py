#!/usr/bin/env python3
"""
Manage SSM Parameter Store secrets for environments.

Secrets are stored in SSM Parameter Store and referenced in deploy.toml.
ECS tasks fetch secrets at runtime using their task execution role.

Naming convention: /{project}/{environment}/{secret-name}
Example: /myapp/staging/SECRET_KEY

Usage:
    # Check secrets: missing from SSM, or extra (in SSM but not in deploy.toml)
    python bin/ssm-secrets.py check myapp-staging

    # Set a secret (prompts for value or offers to generate random)
    python bin/ssm-secrets.py put myapp-staging SECRET_KEY

    # Set a secret with a random value (default 32 chars)
    python bin/ssm-secrets.py put myapp-staging SECRET_KEY --random

    # List all secrets for an environment
    python bin/ssm-secrets.py list myapp-staging

    # Get a secret value
    python bin/ssm-secrets.py get myapp-staging SECRET_KEY

    # Delete a secret
    python bin/ssm-secrets.py delete myapp-staging SECRET_KEY
"""

import getpass
import secrets as secrets_module
import sys
from datetime import datetime
from pathlib import Path

import click

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


def _generate_random_secret(length: int = 32) -> str:
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


def cmd_check(environment: str, deploy_toml: str | None) -> int:
    """Check which secrets from deploy.toml are missing in SSM, and which SSM secrets are unused."""
    # Resolve deploy.toml path: explicit --deploy-toml, or linked, or error
    deploy_toml_path = None
    used_explicit_flag = False

    if deploy_toml:
        deploy_toml_path = Path(deploy_toml).expanduser().resolve()
        used_explicit_flag = True
    else:
        linked_path = get_linked_deploy_toml(environment)
        if linked_path:
            deploy_toml_path = linked_path
            print(f"Using linked deploy.toml: {deploy_toml_path}")
        else:
            print(f"Error: No deploy.toml linked for '{environment}'", file=sys.stderr)
            print(
                f"\nTo link: python bin/link-environments.py {environment} /path/to/deploy.toml",
                file=sys.stderr,
            )
            print(
                f"Or specify: ssm-secrets.py check {environment} --deploy-toml /path/to/deploy.toml",
                file=sys.stderr,
            )
            return 1

    if not deploy_toml_path.exists():
        print(f"Error: File not found: {deploy_toml_path}", file=sys.stderr)
        return 1

    # Print tip if --deploy-toml was explicitly provided
    if used_explicit_flag:
        print(f"Tip: Run 'python bin/link-environments.py {environment} {deploy_toml_path}'")
        print(f"     to check with just: ssm-secrets.py check {environment}\n")

    # Parse environment name
    project, env = parse_environment(environment)

    print(f"Checking secrets for {environment}...")
    print(f"Reading: {deploy_toml_path}\n")

    # Get required secrets from deploy.toml
    try:
        required_secrets = get_secrets_from_deploy_toml(deploy_toml_path, env)
    except Exception as e:
        print(f"Error parsing deploy.toml: {e}", file=sys.stderr)
        return 1

    # Get all existing secrets from SSM for this environment
    path_prefix = get_path_prefix(environment)
    existing_params, error = ssm.list_parameters(path_prefix)
    if error:
        print(f"Error listing SSM parameters: {error}", file=sys.stderr)
        return 1

    # Build set of existing SSM paths
    existing_paths = {p["name"] for p in existing_params}

    # Check each required secret
    sorted_secrets = sorted(required_secrets.items())
    required_paths = {path for _, path in sorted_secrets}
    present = [(env_var, path) for env_var, path in sorted_secrets if path in existing_paths]
    missing = [(env_var, path) for env_var, path in sorted_secrets if path not in existing_paths]

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
            print(f"  uv run python bin/ssm-secrets.py put {environment} {secret_name}")

    if extra:
        print("\nExtra secrets not referenced in deploy.toml:")
        for secret_name, ssm_path in extra:
            print(f"  uv run python bin/ssm-secrets.py delete {environment} {secret_name}")

    if missing or extra:
        return 1

    return 0


def cmd_put(environment: str, secret_name: str, value: str | None, from_file: str | None, random: int | None) -> int:
    """Create or update a secret."""
    param_path = get_parameter_path(environment, secret_name)

    # Get the value
    if from_file:
        try:
            with open(from_file) as f:
                value = f.read()
        except FileNotFoundError:
            print(f"Error: File not found: {from_file}", file=sys.stderr)
            return 1
        except OSError as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            return 1
    elif value:
        pass  # value already set
    elif random is not None:
        # Generate random value (random is the length, default 32)
        length = random
        value = _generate_random_secret(length)
        print(f"Generated random value ({length} chars): {value}")
    else:
        # Interactive mode - offer choice
        try:
            print(f"Set value for {secret_name}:")
            print("  [1] Enter value manually")
            print("  [2] Generate random value (32 chars)")
            choice = input("Choice [1/2]: ").strip()

            if choice == "2":
                value = _generate_random_secret(32)
                print(f"Generated: {value}")
            else:
                # Default to manual entry
                value = getpass.getpass(f"Enter value for {secret_name}: ")
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
        description=f"Secret for {environment}",
        overwrite=True,
    )

    if not success:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Secret {'updated' if exists else 'created'} successfully.")
    return 0


def cmd_get(environment: str, secret_name: str, quiet: bool) -> int:
    """Get a secret value."""
    param_path = get_parameter_path(environment, secret_name)

    value, error = ssm.get_parameter(param_path)

    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if quiet:
        print(value)
    else:
        print(f"Secret: {param_path}")
        print(f"Value: {value}")

    return 0


def cmd_list(environment: str) -> int:
    """List all secrets for an environment."""
    path_prefix = get_path_prefix(environment)

    print(f"Secrets in {environment} ({path_prefix}):\n")

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


def cmd_delete(environment: str, secret_name: str, force: bool) -> int:
    """Delete a secret."""
    param_path = get_parameter_path(environment, secret_name)

    # Confirm deletion
    if not force:
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
# CLI
# =============================================================================


class SSMGroup(click.Group):
    """Custom group that configures AWS profile after parsing."""

    def invoke(self, ctx):
        # Configure AWS profile before any boto3 clients are created
        # Uses environment-specific profile from config.toml if available
        env_name = None
        # Try to get environment from the subcommand's params
        if ctx.invoked_subcommand and ctx.invoked_subcommand in self.commands:
            # We need to parse ahead; use a simpler approach
            pass

        # Fall back: configure after subcommand parsing via callback
        super().invoke(ctx)


def _configure_aws(environment: str | None) -> None:
    """Configure AWS profile for the given environment."""
    if environment:
        configure_aws_profile_for_environment("secrets", environment)
    else:
        configure_aws_profile("secrets")


@click.group()
def cli():
    """Manage SSM Parameter Store secrets for environments.

    \b
    SSM Parameter naming convention:
      /{project}/{environment}/{secret-name}
      Example: /myapp/staging/SECRET_KEY
    """


@cli.command()
@click.argument("environment")
@click.option("--deploy-toml", metavar="PATH", help="Path to deploy.toml (optional if environment is linked)")
def check(environment, deploy_toml):
    """Check for missing or extra secrets."""
    _configure_aws(environment)
    sys.exit(cmd_check(environment, deploy_toml))


@cli.command("put")
@click.argument("environment")
@click.argument("secret_name")
@click.option("-v", "--value", help="Secret value (prompts if not provided)")
@click.option("--from-file", help="Read secret value from file")
@click.option("-r", "--random", type=int, default=None, is_flag=False, flag_value=32, help="Generate random value (default 32 chars)")
def put_cmd(environment, secret_name, value, from_file, random):
    """Create or update a secret."""
    _configure_aws(environment)
    sys.exit(cmd_put(environment, secret_name, value, from_file, random))


@cli.command("get")
@click.argument("environment")
@click.argument("secret_name")
@click.option("-q", "--quiet", is_flag=True, help="Output only the value")
def get_cmd(environment, secret_name, quiet):
    """Get a secret value."""
    _configure_aws(environment)
    sys.exit(cmd_get(environment, secret_name, quiet))


@cli.command("list")
@click.argument("environment")
def list_cmd(environment):
    """List secrets in an environment."""
    _configure_aws(environment)
    sys.exit(cmd_list(environment))


@cli.command("delete")
@click.argument("environment")
@click.argument("secret_name")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation")
def delete_cmd(environment, secret_name, force):
    """Delete a secret."""
    _configure_aws(environment)
    sys.exit(cmd_delete(environment, secret_name, force))


if __name__ == "__main__":
    cli()
