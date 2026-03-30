#!/usr/bin/env python3
"""
Initialize deployment configuration for new applications.

Provides utilities to generate deploy.toml from docker-compose.yml and create
environment directories with proper scaffolding.

Usage:
    # Set up bootstrap infrastructure for a new AWS account
    python bin/init.py bootstrap
    python bin/init.py bootstrap --migrate-state bootstrap-staging

    # Generate deploy.toml from docker-compose.yml
    python bin/init.py deploy-toml --from-compose docker-compose.yml --dry-run
    python bin/init.py deploy-toml --from-compose docker-compose.yml --app-name myapp

    # List available templates
    python bin/init.py environment --list-templates

    # Create environment directory from template
    python bin/init.py environment --app-name myapp --template standalone-staging --dry-run
    python bin/init.py environment --app-name myapp --template standalone-staging --domain myapp.example.com
    python bin/init.py environment --app-name myapp --template standalone-staging --deploy-toml /path/to/deploy.toml

    # Shared infrastructure (two steps)
    python bin/init.py environment --template shared-infra-staging --domain staging.example.com
    python bin/init.py environment --app-name myapp --template shared-app-staging --domain myapp.staging.example.com

    # Update services in existing environment from deploy.toml
    python bin/init.py update-services myapp-staging --deploy-toml /path/to/deploy.toml --dry-run
"""

import os
import stat
import sys
from pathlib import Path

import click

from deployer.init import (
    generate_deploy_toml,
    generate_environment,
    list_templates,
    update_services,
)
from deployer.init.bootstrap import (
    bootstrap_dir_exists,
    detect_aws_account_id,
    generate_bootstrap,
    uncomment_backend_block,
)
from deployer.init.deploy_toml import format_deploy_toml
from deployer.init.environment import create_deployer_tf_symlink, get_next_listener_priority
from deployer.init.template import extract_env_type
from deployer.utils import ensure_environments_symlinks, get_environments_dir

# =============================================================================
# Commands
# =============================================================================


def cmd_bootstrap(dry_run: bool) -> int:
    """Interactively set up bootstrap infrastructure for a new AWS account."""
    print("Setting up deployer bootstrap for a new AWS account.\n")

    # Auto-detect account ID
    detected_id = detect_aws_account_id()

    # Collect inputs
    account_id = click.prompt("AWS Account ID", default=detected_id or "", type=str).strip()
    if not account_id or not account_id.isdigit() or len(account_id) != 12:
        print("Error: AWS Account ID must be exactly 12 digits.", file=sys.stderr)
        return 1

    region = click.prompt("AWS Region", default="us-west-2", type=str).strip()
    env_label = click.prompt("Environment label (e.g., staging, production)", default="staging", type=str).strip()
    env_name = f"bootstrap-{env_label}"

    prefixes_str = click.prompt("Project prefixes (comma-separated)", type=str).strip()
    project_prefixes = [p.strip() for p in prefixes_str.split(",") if p.strip()]
    if not project_prefixes:
        print("Error: At least one project prefix is required.", file=sys.stderr)
        return 1

    default_arn = f"arn:aws:iam::{account_id}:user/deployer"
    arns_str = click.prompt("Trusted IAM user ARNs (comma-separated)", default=default_arn, type=str).strip()
    trusted_user_arns = [a.strip() for a in arns_str.split(",") if a.strip()]

    # Cognito (optional)
    include_cognito = click.confirm("Include shared Cognito user pool?", default=False)
    cognito_app_domains = None
    if include_cognito:
        domains_str = click.prompt(
            "App domains (appname=domain, comma-separated)",
            type=str,
        ).strip()
        cognito_app_domains = {}
        for pair in domains_str.split(","):
            pair = pair.strip()
            if "=" not in pair:
                print(f"Error: Invalid app domain format '{pair}'. Expected appname=domain.", file=sys.stderr)
                return 1
            app, domain = pair.split("=", 1)
            cognito_app_domains[app.strip()] = domain.strip()

    # Check for existing directory
    try:
        env_dir = get_environments_dir()
    except RuntimeError:
        print(
            "Error: DEPLOYER_ENVIRONMENTS_DIR is not set.\n"
            "Add it to your .env file, e.g.:\n"
            "  DEPLOYER_ENVIRONMENTS_DIR=~/code/deployer-environments",
            file=sys.stderr,
        )
        return 1

    env_path = env_dir / env_name
    if env_path.exists() and not dry_run:
        print(f"Error: Directory already exists: {env_path}", file=sys.stderr)
        return 1

    # Generate files
    try:
        files = generate_bootstrap(
            account_id=account_id,
            region=region,
            env_label=env_label,
            project_prefixes=project_prefixes,
            trusted_user_arns=trusted_user_arns,
            include_cognito=include_cognito,
            cognito_app_domains=cognito_app_domains,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if dry_run:
        print("=" * 60)
        print(f"Would create directory: {env_path}")
        print("=" * 60)
        print()
        for filename, content in files.items():
            print(f"--- {filename} ---")
            print(content)
            print()
        return 0

    # Create environments directory and symlinks
    env_dir.mkdir(parents=True, exist_ok=True)
    created_symlinks = ensure_environments_symlinks()
    if created_symlinks:
        print(f"Created symlinks in {env_dir}: {', '.join(created_symlinks)}")

    # Create bootstrap directory and write files
    env_path.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        filepath = env_path / filename
        filepath.write_text(content)
        print(f"Created: {filepath}")

    # Make import-existing.sh executable
    import_script = env_path / "import-existing.sh"
    if import_script.exists():
        import_script.chmod(import_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print()
    print("Next steps:")
    print(f"  1. cd {env_path}")
    print("  2. AWS_PROFILE=admin tofu init")
    print("  3. AWS_PROFILE=admin tofu apply")
    print()
    print("  After successful apply, enable S3 backend:")
    print(f"    uv run python bin/init.py bootstrap --migrate-state {env_name}")
    return 0


def cmd_bootstrap_migrate(env_name: str, dry_run: bool) -> int:
    """Enable S3 backend in an existing bootstrap directory."""
    try:
        env_dir = get_environments_dir()
    except RuntimeError:
        print("Error: DEPLOYER_ENVIRONMENTS_DIR is not set.", file=sys.stderr)
        return 1

    env_path = env_dir / env_name
    main_tf = env_path / "main.tf"

    if not main_tf.exists():
        print(f"Error: {main_tf} not found.", file=sys.stderr)
        return 1

    content = main_tf.read_text()

    try:
        updated = uncomment_backend_block(content)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if dry_run:
        print("=" * 60)
        print(f"Would update: {main_tf}")
        print("=" * 60)
        print()
        print(updated)
        return 0

    main_tf.write_text(updated)
    print(f"S3 backend enabled in {main_tf}")
    print()
    print("Next steps:")
    print(f"  1. cd {env_path}")
    print("  2. AWS_PROFILE=admin tofu init -migrate-state")
    print('     (answer "yes" to copy state to S3)')
    print("  3. AWS_PROFILE=admin tofu plan")
    print('     (should show "No changes")')
    return 0


def cmd_deploy_toml(from_compose, app_name, output, dry_run) -> int:
    """Generate deploy.toml from docker-compose.yml."""
    # Determine compose path
    compose_path = None
    if from_compose:
        compose_path = Path(from_compose).resolve()
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
            app_name=app_name,
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
    output_path = Path(output) if output else compose_path.parent / "deploy.toml"

    if dry_run:
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
    print(
        f"     uv run python bin/init.py environment --app-name {config['application']['name']} --template standalone-staging"
    )
    return 0


def cmd_environment(app_name, template, list_templates_flag, deploy_toml, domain, dry_run) -> int:
    """Create environment directory structure."""
    # Handle --list-templates
    if list_templates_flag:
        templates = list_templates()
        print("Available templates:")
        for name in templates:
            print(f"  {name}")
        return 0

    # Check that bootstrap has been run
    try:
        if not bootstrap_dir_exists():
            print(
                "Error: No bootstrap directory found.\n"
                "Bootstrap creates IAM roles and S3 state bucket that all environments depend on.\n"
                "Run 'uv run python bin/init.py bootstrap' first.",
                file=sys.stderr,
            )
            return 1
    except RuntimeError:
        pass  # DEPLOYER_ENVIRONMENTS_DIR not set — let the existing error handling below catch it

    template_name = template
    if not template_name:
        print(
            "Error: --template is required (use --list-templates to see options)", file=sys.stderr
        )
        return 1

    # Validate template exists
    try:
        env_type = extract_env_type(template_name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    is_shared_infra = template_name.startswith("shared-infra-")
    is_shared_app = template_name.startswith("shared-app-")

    # Validate --app-name
    if not is_shared_infra and not app_name:
        print("Error: --app-name is required for non-shared-infra templates", file=sys.stderr)
        return 1

    # Compute env_name for existence check
    if is_shared_infra:
        env_name = template_name
    else:
        env_name = f"{app_name}-{env_type}"

    env_path = get_environments_dir() / env_name
    if env_path.exists() and not dry_run:
        print(f"Error: Environment directory already exists: {env_path}", file=sys.stderr)
        print("Remove it first or use a different name.", file=sys.stderr)
        return 1

    # Load deploy.toml if provided
    deploy_toml_path = None
    if deploy_toml:
        deploy_toml_path = Path(deploy_toml).resolve()
        if not deploy_toml_path.exists():
            print(f"Error: deploy.toml not found at {deploy_toml_path}", file=sys.stderr)
            return 1

    # Auto-assign listener priority for shared-app templates
    listener_priority = None
    if is_shared_app:
        listener_priority = get_next_listener_priority(env_type)

    # Generate environment files
    try:
        files = generate_environment(
            app_name=app_name,
            template_name=template_name,
            deploy_toml_path=deploy_toml_path,
            domain=domain,
            listener_priority=listener_priority,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if dry_run:
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

    # Ensure root-level symlinks exist for external environments directory
    if template_name.startswith("standalone-"):
        created_symlinks = ensure_environments_symlinks()
        if created_symlinks:
            print(f"Created symlinks in {get_environments_dir()}: {', '.join(created_symlinks)}")

    # Create directory
    env_path.mkdir(parents=True, exist_ok=True)

    # Write files
    for filepath, content in files.items():
        Path(filepath).write_text(content)
        print(f"Created: {filepath}")

    # Create deployer.tf symlink (standalone templates only)
    if template_name.startswith("standalone-"):
        if create_deployer_tf_symlink(env_path):
            print(f"Created: {env_path}/deployer.tf -> shared environment config")

    print()
    _print_next_steps(env_name, env_path, env_type, app_name, template_name)
    return 0


def _print_next_steps(
    env_name: str,
    env_path: Path,
    env_type: str,
    app_name: str | None,
    template_name: str,
) -> None:
    """Print next steps after environment creation."""
    is_shared_infra = template_name.startswith("shared-infra-")
    is_standalone = template_name.startswith("standalone-")

    print("Next steps:")
    step = 1

    if is_standalone:
        print(f"  {step}. Edit {env_path}/terraform.tfvars:")
        print("     - Set database credentials")
        step += 1
        print()
        print(f"  {step}. Edit {env_path}/services.auto.tfvars:")
        print("     - Configure domain and Route53 zone ID")
        print("     - Adjust service sizing if needed")
        step += 1
    elif is_shared_infra:
        print(f"  {step}. Edit {env_path}/terraform.tfvars:")
        print("     - Set domain and Route53 zone ID")
        print("     - Configure Cognito if needed")
        step += 1
    else:
        print(f"  {step}. Edit {env_path}/terraform.tfvars:")
        print("     - Set database credentials")
        print("     - Configure domain and Route53 zone ID")
        print("     - Verify listener_rule_priority is unique")
        step += 1

    print()
    print(f"  {step}. Deploy infrastructure:")
    print(f"     ./bin/tofu.sh plan {env_name}")
    print(f"     ./bin/tofu.sh apply {env_name}")
    step += 1

    if not is_shared_infra and app_name:
        print()
        print(f"  {step}. Create SSM secrets and deploy:")
        print(
            f'     aws ssm put-parameter --name "/{app_name}/{env_type}/secret-key" --value "..." --type SecureString'
        )
        print(f"     uv run python bin/deploy.py {env_name}")


def cmd_update_services(env_name, deploy_toml, dry_run) -> int:
    """Update services block in an existing environment from deploy.toml."""
    deploy_toml_path = Path(deploy_toml).resolve()
    if not deploy_toml_path.exists():
        print(f"Error: deploy.toml not found at {deploy_toml_path}", file=sys.stderr)
        return 1

    try:
        update_services(
            env_name=env_name,
            deploy_toml_path=deploy_toml_path,
            dry_run=dry_run,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


# =============================================================================
# CLI
# =============================================================================


@click.group()
def cli():
    """Initialize deployment configuration for new applications."""


@cli.command("bootstrap")
@click.option("--migrate-state", metavar="ENV_NAME", help="Phase 2: Enable S3 backend in an existing bootstrap directory")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would be generated without writing files")
def bootstrap_cmd(migrate_state, dry_run):
    """Set up bootstrap infrastructure for a new AWS account.

    \b
    Phase 1 (default): Interactively create bootstrap directory
      init.py bootstrap

    \b
    Phase 2: Enable S3 backend after first apply
      init.py bootstrap --migrate-state bootstrap-staging
    """
    if migrate_state:
        sys.exit(cmd_bootstrap_migrate(migrate_state, dry_run))
    else:
        sys.exit(cmd_bootstrap(dry_run))


@cli.command("deploy-toml")
@click.option("--from-compose", metavar="PATH", help="Path to docker-compose.yml (default: ./docker-compose.yml)")
@click.option("--app-name", metavar="NAME", help="Application name (default: directory name)")
@click.option("--output", "-o", metavar="PATH", help="Output path for deploy.toml (default: same directory as docker-compose.yml)")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would be generated without writing files")
def deploy_toml_cmd(from_compose, app_name, output, dry_run):
    """Generate deploy.toml from docker-compose.yml."""
    sys.exit(cmd_deploy_toml(from_compose, app_name, output, dry_run))


@cli.command("environment")
@click.option("--app-name", metavar="NAME", help="Application name (required except for shared-infra templates)")
@click.option("--template", "-t", metavar="NAME", help="Template to use (e.g., standalone-staging). Use --list-templates to see options.")
@click.option("--list-templates", is_flag=True, help="List available templates and exit")
@click.option("--deploy-toml", metavar="PATH", help="Path to deploy.toml to read service configuration from")
@click.option("--domain", metavar="DOMAIN", help="Domain name for the environment")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would be created without writing files")
def environment_cmd(app_name, template, list_templates, deploy_toml, domain, dry_run):
    """Create environment directory from a template.

    \b
    Examples:
      init.py environment --app-name myapp --template standalone-staging
      init.py environment --template shared-infra-staging
      init.py environment --app-name myapp --template shared-app-staging
    """
    sys.exit(cmd_environment(app_name, template, list_templates, deploy_toml, domain, dry_run))


@cli.command("update-services")
@click.argument("env_name")
@click.option("--deploy-toml", required=True, metavar="PATH", help="Path to deploy.toml")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would change without writing files")
def update_services_cmd(env_name, deploy_toml, dry_run):
    """Update services block in an existing environment from deploy.toml."""
    sys.exit(cmd_update_services(env_name, deploy_toml, dry_run))


if __name__ == "__main__":
    cli()
