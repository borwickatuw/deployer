#!/usr/bin/env python3
"""
Initialize deployment configuration for new applications.

Provides utilities to generate deploy.toml from docker-compose.yml and create
environment directories with proper scaffolding.

Usage:
    # Generate deploy.toml from docker-compose.yml
    python bin/init.py deploy-toml --from-compose docker-compose.yml --dry-run
    python bin/init.py deploy-toml --from-compose docker-compose.yml --app-name myapp

    # Create environment directory
    python bin/init.py environment --app-name myapp --env-type staging --dry-run
    python bin/init.py environment --app-name myapp --env-type staging --domain myapp.example.com
    python bin/init.py environment --app-name myapp --env-type staging --deploy-toml /path/to/deploy.toml
"""

import argparse
import sys
from pathlib import Path

from deployer.init import generate_deploy_toml, generate_environment, generate_shared_infrastructure
from deployer.init.deploy_toml import format_deploy_toml
from deployer.init.environment import get_next_listener_priority
from deployer.utils import ensure_environments_symlinks, get_environments_dir, load_dotenv_if_exists


# =============================================================================
# Commands
# =============================================================================


def cmd_deploy_toml(args) -> int:
    """Generate deploy.toml from docker-compose.yml."""
    # Determine compose path
    compose_path = None
    if args.from_compose:
        compose_path = Path(args.from_compose).resolve()
        if not compose_path.exists():
            print(f"Error: docker-compose.yml not found at {compose_path}", file=sys.stderr)
            return 1

    if compose_path is None:
        # Look for docker-compose.yml in current directory
        compose_path = Path.cwd() / "docker-compose.yml"
        if not compose_path.exists():
            print(
                "Error: No docker-compose.yml found. Use --from-compose to specify path.",
                file=sys.stderr,
            )
            return 1

    # Generate configuration
    try:
        config = generate_deploy_toml(
            compose_path=compose_path,
            app_name=args.app_name,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error parsing docker-compose.yml: {e}", file=sys.stderr)
        return 1

    # Format as TOML
    content = format_deploy_toml(config)

    # Determine output path
    output_path = Path(args.output) if args.output else compose_path.parent / "deploy.toml"

    if args.dry_run:
        print("=" * 60)
        print(f"Would write to: {output_path}")
        print("=" * 60)
        print()
        print(content)
        return 0

    # Write file
    output_path.write_text(content)
    print(f"Generated: {output_path}")
    print()
    print("Next steps:")
    print("  1. Review and customize the generated deploy.toml")
    print("  2. Create environment directory:")
    print(f"     uv run python bin/init.py environment --app-name {config['application']['name']} --env-type staging")
    return 0


def cmd_environment(args) -> int:
    """Create environment directory structure."""
    # Validate environment type
    if args.env_type not in ("staging", "production"):
        print(
            f"Error: --env-type must be 'staging' or 'production', got '{args.env_type}'",
            file=sys.stderr,
        )
        return 1

    # Check if environment already exists
    env_name = f"{args.app_name}-{args.env_type}"
    env_path = get_environments_dir() / env_name
    if env_path.exists() and not args.dry_run:
        print(f"Error: Environment directory already exists: {env_path}", file=sys.stderr)
        print("Remove it first or use a different name.", file=sys.stderr)
        return 1

    # Load deploy.toml if provided
    deploy_toml_path = None
    if args.deploy_toml:
        deploy_toml_path = Path(args.deploy_toml).resolve()
        if not deploy_toml_path.exists():
            print(f"Error: deploy.toml not found at {deploy_toml_path}", file=sys.stderr)
            return 1

    # Handle shared infrastructure mode
    shared_infra_created = False
    if args.shared:
        shared_infra_name = f"shared-infra-{args.env_type}"
        shared_infra_path = get_environments_dir() / shared_infra_name

        if not shared_infra_path.exists():
            # Prompt to create shared infrastructure
            if args.dry_run:
                print(f"Would create shared infrastructure: {shared_infra_path}")
            else:
                response = input(
                    f"Shared infrastructure '{shared_infra_name}' doesn't exist. Create it? [y/N] "
                )
                if response.lower() == "y":
                    # Generate shared infrastructure files
                    shared_files = generate_shared_infrastructure(
                        env_type=args.env_type,
                        domain_base=args.domain.rsplit(".", 2)[-2] + "." + args.domain.rsplit(".", 1)[-1]
                        if args.domain and args.domain.count(".") >= 2
                        else None,
                    )

                    # Create directory and write files
                    shared_infra_path.mkdir(parents=True, exist_ok=True)
                    for filepath, content in shared_files.items():
                        Path(filepath).write_text(content)
                        print(f"Created: {filepath}")

                    shared_infra_created = True
                    print()
                    print("Next steps for shared infrastructure:")
                    print(f"  1. Edit {shared_infra_path}/terraform.tfvars")
                    print("     - Set domain and Route53 zone ID")
                    print("     - Configure Cognito if needed")
                    print()
                    print(f"  2. Deploy: ./bin/tofu.sh -chdir={shared_infra_path} init && ./bin/tofu.sh -chdir={shared_infra_path} apply")
                    print()
                else:
                    print("Shared infrastructure is required for --shared mode.")
                    return 1
        else:
            print(f"Using existing shared infrastructure: {shared_infra_name}")

        # Get next available listener priority
        listener_priority = get_next_listener_priority(args.env_type)

        # Generate per-app environment using shared module
        files = generate_environment(
            app_name=args.app_name,
            env_type=args.env_type,
            deploy_toml_path=deploy_toml_path,
            domain=args.domain,
            shared=True,
            listener_priority=listener_priority,
        )
    else:
        # Generate standalone environment (existing behavior)
        files = generate_environment(
            app_name=args.app_name,
            env_type=args.env_type,
            deploy_toml_path=deploy_toml_path,
            domain=args.domain,
        )

    if args.dry_run:
        print("=" * 60)
        print(f"Would create directory: {env_path}")
        print("=" * 60)
        print()
        for filepath, content in files.items():
            print(f"--- {filepath} ---")
            # Show first 50 lines of each file
            lines = content.split("\n")
            for line in lines[:50]:
                print(line)
            if len(lines) > 50:
                print(f"... ({len(lines) - 50} more lines)")
            print()
        return 0

    # Ensure symlinks exist for external environments directory
    created_symlinks = ensure_environments_symlinks()
    if created_symlinks:
        print(f"Created symlinks in {get_environments_dir()}: {', '.join(created_symlinks)}")

    # Create directory
    env_path.mkdir(parents=True, exist_ok=True)

    # Write files
    for filepath, content in files.items():
        Path(filepath).write_text(content)
        print(f"Created: {filepath}")

    print()
    if args.shared:
        print("Next steps:")
        print(f"  1. Edit {env_path}/terraform.tfvars:")
        print("     - Set database credentials")
        print("     - Adjust service sizing if needed")
        print()
        if shared_infra_created:
            print("  2. First deploy shared infrastructure (see above)")
            print()
            print("  3. Then deploy app infrastructure:")
        else:
            print("  2. Deploy app infrastructure:")
        print(f"     ./bin/tofu.sh -chdir={env_path} init")
        print(f"     ./bin/tofu.sh -chdir={env_path} plan")
        print(f"     ./bin/tofu.sh -chdir={env_path} apply")
        print()
        print(f"  {'4' if shared_infra_created else '3'}. Create SSM secrets and deploy:")
        print(f"     aws ssm put-parameter --name \"/{args.app_name}/{args.env_type}/secret-key\" --value \"...\" --type SecureString")
        print(f"     aws logs create-log-group --log-group-name /ecs/{args.app_name}")
        print(f"     uv run python bin/deploy.py /path/to/deploy.toml {env_name}")
    else:
        print("Next steps:")
        print(f"  1. Edit {env_path}/terraform.tfvars:")
        print("     - Set database credentials")
        print("     - Configure domain and Route53 zone ID")
        print("     - Adjust service sizing if needed")
        print()
        print("  2. Deploy infrastructure:")
        print(f"     ./bin/tofu.sh -chdir={env_path} init")
        print(f"     ./bin/tofu.sh -chdir={env_path} plan")
        print(f"     ./bin/tofu.sh -chdir={env_path} apply")
        print()
        print("  3. Create SSM secrets and deploy:")
        print(f"     aws ssm put-parameter --name \"/{args.app_name}/{args.env_type}/secret-key\" --value \"...\" --type SecureString")
        print(f"     aws logs create-log-group --log-group-name /ecs/{args.app_name}")
        print(f"     uv run python bin/deploy.py /path/to/deploy.toml {env_name}")
    return 0


# =============================================================================
# Main
# =============================================================================


def main():
    # Load .env for DEPLOYER_ENVIRONMENTS_DIR
    load_dotenv_if_exists()

    parser = argparse.ArgumentParser(
        description="Initialize deployment configuration for new applications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate deploy.toml from docker-compose.yml (preview)
  %(prog)s deploy-toml --from-compose docker-compose.yml --dry-run

  # Generate deploy.toml with custom app name
  %(prog)s deploy-toml --from-compose docker-compose.yml --app-name myapp

  # Create staging environment directory (preview)
  %(prog)s environment --app-name myapp --env-type staging --dry-run

  # Create staging environment with deploy.toml for service sizing
  %(prog)s environment --app-name myapp --env-type staging \\
      --deploy-toml /path/to/deploy.toml --domain myapp-staging.example.com

For detailed guidance, see docs/DEPLOYMENT-GUIDE.md
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # deploy-toml subcommand
    deploy_toml_parser = subparsers.add_parser(
        "deploy-toml",
        help="Generate deploy.toml from docker-compose.yml",
        description="""
Generate a deploy.toml file from an existing docker-compose.yml.

The generator will:
- Extract services with 'build' configurations
- Filter out infrastructure services (postgres, redis, etc.)
- Detect your framework (Django, Rails, etc.) and set up migrations
- Identify potential secrets from environment variables
        """,
    )
    deploy_toml_parser.add_argument(
        "--from-compose",
        metavar="PATH",
        help="Path to docker-compose.yml (default: ./docker-compose.yml)",
    )
    deploy_toml_parser.add_argument(
        "--app-name",
        metavar="NAME",
        help="Application name (default: directory name)",
    )
    deploy_toml_parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        help="Output path for deploy.toml (default: same directory as docker-compose.yml)",
    )
    deploy_toml_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be generated without writing files",
    )

    # environment subcommand
    env_parser = subparsers.add_parser(
        "environment",
        help="Create environment directory in deployer",
        description="""
Create an environment directory with scaffolded configuration files.

Creates:
- main.tf - Infrastructure module configuration
- config.toml - Deployment configuration with ${tofu:...} placeholders
- terraform.tfvars - Service sizing (cpu, memory, replicas)
- README.md - Environment-specific notes
        """,
    )
    env_parser.add_argument(
        "--app-name",
        required=True,
        metavar="NAME",
        help="Application name",
    )
    env_parser.add_argument(
        "--env-type",
        required=True,
        choices=["staging", "production"],
        help="Environment type",
    )
    env_parser.add_argument(
        "--deploy-toml",
        metavar="PATH",
        help="Path to deploy.toml to read service configuration from",
    )
    env_parser.add_argument(
        "--domain",
        metavar="DOMAIN",
        help="Domain name for the environment",
    )
    env_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be created without writing files",
    )
    env_parser.add_argument(
        "--shared",
        action="store_true",
        help="Use shared infrastructure (creates shared-infra-{env_type} if needed)",
    )

    args = parser.parse_args()

    # Dispatch to command handler
    commands = {
        "deploy-toml": cmd_deploy_toml,
        "environment": cmd_environment,
    }

    handler = commands.get(args.command)
    if handler:
        sys.exit(handler(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
