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

import argparse
import sys
from pathlib import Path

from deployer.utils import (
    get_all_links,
    get_links_file,
    set_linked_deploy_toml,
    validate_environment_deployed,
)


def cmd_link(args) -> int:
    """Link an environment to a deploy.toml file."""
    environment = args.environment
    deploy_toml_path = Path(args.deploy_toml).expanduser().resolve()

    # Validate environment exists
    _, error = validate_environment_deployed(environment)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    # Validate deploy.toml exists
    if not deploy_toml_path.exists():
        print(f"Error: File not found: {deploy_toml_path}", file=sys.stderr)
        return 1

    if not deploy_toml_path.name.endswith(".toml"):
        print(f"Warning: File does not end with .toml: {deploy_toml_path}", file=sys.stderr)

    # Save the link
    set_linked_deploy_toml(environment, deploy_toml_path)

    # Display with ~ for readability
    display_path = str(deploy_toml_path)
    home = str(Path.home())
    if display_path.startswith(home):
        display_path = "~" + display_path[len(home) :]

    print(f"Linked: {environment} -> {display_path}")
    return 0


def cmd_list(args) -> int:
    """List all environment links."""
    links = get_all_links()

    if not links:
        print("No environments linked.")
        print(f"\nTo link an environment:")
        print(f"  python bin/link-environments.py <environment> <path/to/deploy.toml>")
        return 0

    print("Environment links:")
    print("-" * 60)
    for env, path in sorted(links.items()):
        print(f"  {env} -> {path}")

    print(f"\nStored in: {get_links_file()}")
    return 0


def cmd_show_file(args) -> int:
    """Show the links file location."""
    links_file = get_links_file()
    print(f"Links file: {links_file}")
    print(f"Exists: {links_file.exists()}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Link environments to deploy.toml files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s myapp-staging ~/code/myapp/deploy.toml
  %(prog)s otherapp-staging ../otherapp/deploy.toml
  %(prog)s --list
  %(prog)s --show-file

Links are stored in local/environments.toml (gitignored).
Once linked, you can omit --deploy-toml from ecs-run.py, deploy.py, etc.
        """,
    )

    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all environment links",
    )
    parser.add_argument(
        "--show-file",
        action="store_true",
        help="Show the links file location",
    )
    parser.add_argument(
        "environment",
        nargs="?",
        help="Environment name (e.g., myapp-staging)",
    )
    parser.add_argument(
        "deploy_toml",
        nargs="?",
        help="Path to deploy.toml file",
    )

    args = parser.parse_args()

    # Handle flags
    if args.list:
        return cmd_list(args)

    if args.show_file:
        return cmd_show_file(args)

    # Handle link command
    if not args.environment or not args.deploy_toml:
        parser.print_help()
        return 1

    return cmd_link(args)


if __name__ == "__main__":
    sys.exit(main())
