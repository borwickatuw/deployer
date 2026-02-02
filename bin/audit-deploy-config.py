#!/usr/bin/env python3
"""
Audit deploy.toml against docker-compose.yml to find discrepancies.

This script compares an application's deployment configuration with its
docker-compose.yml to identify potential gaps:
- Services in docker-compose that aren't in deploy.toml
- Build contexts/images that might be missing
- Environment variables that aren't accounted for

Use the [audit] section in deploy.toml to acknowledge intentional differences.

Usage:
    python audit-deploy-config.py <project-dir>
    python audit-deploy-config.py ~/code/myapp

Example [audit] section in deploy.toml:

    [audit]
    # Services to ignore (infrastructure, dev-only tools)
    ignore_services = ["postgres", "redis", "a11y"]

    # Map docker-compose service names to deploy.toml service names
    service_mapping = { "myapp" = "web", "celery-worker" = "celery" }

    # Environment variables that are intentionally different or dev-only
    ignore_env_vars = ["DEBUG", "UV_LINK_MODE"]
"""

import argparse
import sys

from deployer.core import run_audit
from deployer.utils import Colors


def main():
    parser = argparse.ArgumentParser(
        description="Audit deploy.toml against docker-compose.yml"
    )
    parser.add_argument(
        "project_dir",
        help="Path to project directory containing deploy.toml and docker-compose.yml",
    )
    parser.add_argument(
        "--docker-compose",
        default="docker-compose.yml",
        help="Name of docker-compose file (default: docker-compose.yml)",
    )
    parser.add_argument(
        "--deploy-toml",
        default="deploy.toml",
        help="Name of deploy.toml file (default: deploy.toml)",
    )

    args = parser.parse_args()

    issue_count, issues = run_audit(
        args.project_dir,
        compose_filename=args.docker_compose,
        deploy_filename=args.deploy_toml,
        verbose=True,
    )

    if issue_count < 0:
        # Error case (file not found)
        print(f"{Colors.RED}Error: {issues[0]}{Colors.NC}")
        sys.exit(1)
    elif issue_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
