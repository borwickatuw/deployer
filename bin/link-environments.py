#!/usr/bin/env python3
"""Link environments to their deploy.toml files.

Links are stored locally (gitignored) so you don't have to specify
--deploy-toml on every command.

Usage:
    # Link an environment to its deploy.toml
    python bin/link-environments.py myapp-staging ~/code/myapp/deploy.toml

    # List all links
    python bin/link-environments.py --list

    # Show link file location
    python bin/link-environments.py --show-file
"""

import sys
from pathlib import Path

import click

from deployer.utils import (
    get_all_links,
    get_linked_deploy_toml,
    get_links_file,
    set_linked_deploy_toml,
    unlink_deploy_toml,
    validate_environment_deployed,
)


@click.command()
@click.argument("environment", required=False)
@click.argument("deploy_toml", required=False)
@click.option("--list", "-l", "list_links", is_flag=True, help="List all environment links")
@click.option("--show-file", is_flag=True, help="Show the links file location")
@click.option("--unlink", "-u", is_flag=True, help="Remove link for an environment")
def cli(environment, deploy_toml, list_links, show_file, unlink):
    """Link environments to deploy.toml files.

    Links are stored in local/environments.toml (gitignored).
    Once linked, you can omit --deploy-toml from ecs-run.py, deploy.py, etc.

    \b
    Examples:
      link-environments.py myapp-staging ~/code/myapp/deploy.toml
      link-environments.py otherapp-staging ../otherapp/deploy.toml
      link-environments.py --unlink myapp-staging
      link-environments.py --list
      link-environments.py --show-file
    """
    if list_links:
        sys.exit(cmd_list())

    if show_file:
        sys.exit(cmd_show_file())

    if unlink:
        if not environment:
            click.echo("Error: environment is required with --unlink", err=True)
            sys.exit(1)
        sys.exit(cmd_unlink(environment))

    # Handle link command
    if not environment or not deploy_toml:
        ctx = click.get_current_context()
        click.echo(ctx.get_help())
        sys.exit(1)

    sys.exit(cmd_link(environment, deploy_toml))


def cmd_link(environment: str, deploy_toml: str) -> int:
    """Link an environment to a deploy.toml file."""
    deploy_toml_path = Path(deploy_toml).expanduser().resolve()

    # Validate environment exists
    _, error = validate_environment_deployed(environment)
    if error:
        click.echo(f"Error: {error}", err=True)
        return 1

    # Validate deploy.toml exists
    if not deploy_toml_path.exists():
        click.echo(f"Error: File not found: {deploy_toml_path}", err=True)
        return 1

    if not deploy_toml_path.name.endswith(".toml"):
        click.echo(f"Warning: File does not end with .toml: {deploy_toml_path}", err=True)

    # Save the link
    set_linked_deploy_toml(environment, deploy_toml_path)

    # Display with ~ for readability
    display_path = str(deploy_toml_path)
    home = str(Path.home())
    if display_path.startswith(home):
        display_path = "~" + display_path[len(home):]

    click.echo(f"Linked: {environment} -> {display_path}")
    return 0


def cmd_list() -> int:
    """List all environment links."""
    links = get_all_links()

    if not links:
        click.echo("No environments linked.")
        click.echo("\nTo link an environment:")
        click.echo("  python bin/link-environments.py <environment> <path/to/deploy.toml>")
        return 0

    click.echo("Environment links:")
    click.echo("-" * 60)
    for env, path in sorted(links.items()):
        click.echo(f"  {env} -> {path}")

    click.echo(f"\nStored in: {get_links_file()}")
    return 0


def cmd_show_file() -> int:
    """Show the links file location."""
    links_file = get_links_file()
    click.echo(f"Links file: {links_file}")
    click.echo(f"Exists: {links_file.exists()}")
    return 0


def cmd_unlink(environment: str) -> int:
    """Remove link for an environment."""
    # Show current link before removing
    current = get_linked_deploy_toml(environment)
    if not current:
        click.echo(f"No link found for '{environment}'", err=True)
        return 1

    if unlink_deploy_toml(environment):
        click.echo(f"Unlinked: {environment} (was -> {current})")
        return 0
    else:
        click.echo(f"Failed to unlink '{environment}'", err=True)
        return 1


if __name__ == "__main__":
    cli()
