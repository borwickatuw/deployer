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

    # Verify tool versions and AWS profiles
    python bin/init.py verify

    # Generate AWS CLI profiles for deployer roles
    python bin/init.py setup-profiles
    python bin/init.py setup-profiles --dry-run

"""

import os
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
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
    generate_bootstrap,
    prompt_account_id_and_region,
    uncomment_backend_block,
)
from deployer.init.deploy_toml import format_deploy_toml
from deployer.init.environment import create_deployer_tf_symlink, get_next_listener_priority
from deployer.init.setup_profiles import cmd_setup_profiles
from deployer.init.template import extract_env_type
from deployer.init.verify import cmd_verify
from deployer.utils import (
    ensure_environments_symlinks,
    exit_on,
    get_environments_dir,
)

# =============================================================================
# Helpers
# =============================================================================

_ENVIRONMENTS_DIR_UNSET = (
    "Error: DEPLOYER_ENVIRONMENTS_DIR is not set.\n"
    "Add it to your .env file, e.g.:\n"
    "  DEPLOYER_ENVIRONMENTS_DIR=~/deployer-environments"
)


def _print_dry_run_preview(
    caption: str, payload: str | dict[str, str], max_lines: int | None = None
) -> None:
    """Print the dry-run banner followed by what would have been written.

    Args:
        caption: The "Would ..." line shown inside the banner.
        payload: Either one blob of text, or a mapping of filename to content.
        max_lines: Truncate each file's content to this many lines.
    """
    print("=" * 60)
    print(caption)
    print("=" * 60)
    print()

    if isinstance(payload, str):
        print(payload)
        return

    for filename, content in payload.items():
        print(f"--- {filename} ---")
        lines = content.split("\n")
        for line in lines if max_lines is None else lines[:max_lines]:
            print(line)
        if max_lines is not None and len(lines) > max_lines:
            print(f"... ({len(lines) - max_lines} more lines)")
        print()


def _numbered_steps(heading: str, *steps: Sequence[str]) -> None:
    """Print a heading, then a blank-separated numbered list.

    Args:
        heading: The line printed above the list, e.g. "Next steps:".
        steps: One sequence of lines per step. The first line of each is
            numbered; the rest print verbatim, so callers keep their own
            continuation indent.
    """
    print(heading)
    for number, lines in enumerate(steps, start=1):
        if number > 1:
            print()
        first, *rest = lines
        print(f"  {number}. {first}")
        for line in rest:
            print(line)


def _run_tofu(subcommand: str, env_path: Path, admin_profile: str) -> bool:
    """Run an interactive `tofu <subcommand>` in an environment directory.

    utils.run_command is not a substitute here: it captures output, and
    `tofu apply` is interactive.

    Returns:
        True if tofu exited 0, False otherwise (the error is printed).
    """
    print(f"\nRunning: tofu {subcommand} (in {env_path})")
    result = subprocess.run(  # noqa: PLW1510
        ["tofu", subcommand],
        cwd=str(env_path),
        env={**os.environ, "AWS_PROFILE": admin_profile},
    )
    if result.returncode != 0:
        print(f"Error: 'tofu {subcommand}' failed.", file=sys.stderr)
        return False
    return True


# =============================================================================
# Commands
# =============================================================================


@dataclass(frozen=True)
class _BootstrapInputs:
    """The answers cmd_bootstrap() collects before it generates anything."""

    account_id: str
    region: str
    env_label: str
    project_prefixes: list[str]
    trusted_user_arns: list[str]
    include_cognito: bool
    cognito_app_domains: dict[str, str] | None

    @property
    def env_name(self) -> str:
        """Name of the bootstrap directory these inputs describe."""
        return f"bootstrap-{self.env_label}"


def _prompt_bootstrap_inputs() -> _BootstrapInputs | None:
    """Collect every bootstrap answer interactively.

    Returns:
        The collected inputs, or None if an answer was rejected (the reason
        is printed).
    """
    account_id, region = prompt_account_id_and_region()
    env_label = click.prompt(
        "Environment label (e.g., staging, production)", default="staging", type=str
    ).strip()

    prefixes_str = click.prompt("Project prefixes (comma-separated)", type=str).strip()
    project_prefixes = [p.strip() for p in prefixes_str.split(",") if p.strip()]
    if not project_prefixes:
        print("Error: At least one project prefix is required.", file=sys.stderr)
        return None

    default_arn = f"arn:aws:iam::{account_id}:user/deployer"
    arns_str = click.prompt(
        "Trusted IAM user ARNs (comma-separated)", default=default_arn, type=str
    ).strip()
    trusted_user_arns = [a.strip() for a in arns_str.split(",") if a.strip()]

    include_cognito = click.confirm("Include shared Cognito user pool?", default=False)
    cognito_app_domains = None
    if include_cognito:
        domains_str = click.prompt(
            "App domains (appname=domain, comma-separated)",
            type=str,
        ).strip()
        cognito_app_domains = {}
        for pair in domains_str.split(","):
            stripped = pair.strip()
            if "=" not in stripped:
                print(
                    f"Error: Invalid app domain format '{stripped}'. Expected appname=domain.",
                    file=sys.stderr,
                )
                return None
            app, domain = stripped.split("=", 1)
            cognito_app_domains[app.strip()] = domain.strip()

    return _BootstrapInputs(
        account_id=account_id,
        region=region,
        env_label=env_label,
        project_prefixes=project_prefixes,
        trusted_user_arns=trusted_user_arns,
        include_cognito=include_cognito,
        cognito_app_domains=cognito_app_domains,
    )


def _resolve_bootstrap_path(env_name: str, dry_run: bool) -> Path | None:
    """Work out where the bootstrap directory goes and check it is free.

    Returns:
        The directory to create, or None if the environments directory is
        unconfigured or the target already exists (the reason is printed).
    """
    try:
        env_dir = get_environments_dir()
    except RuntimeError:
        print(_ENVIRONMENTS_DIR_UNSET, file=sys.stderr)
        return None

    env_path = env_dir / env_name
    if env_path.exists() and not dry_run:
        print(f"Error: Directory already exists: {env_path}", file=sys.stderr)
        return None
    return env_path


def _write_bootstrap_files(env_path: Path, files: dict[str, str]) -> None:
    """Create the bootstrap directory, write its files and mark the script +x."""
    env_dir = env_path.parent
    env_dir.mkdir(parents=True, exist_ok=True)
    created_symlinks = ensure_environments_symlinks()
    if created_symlinks:
        print(f"Created symlinks in {env_dir}: {', '.join(created_symlinks)}")

    env_path.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        filepath = env_path / filename
        filepath.write_text(content)
        print(f"Created: {filepath}")

    import_script = env_path / "import-existing.sh"
    if import_script.exists():
        import_script.chmod(
            import_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )


def _apply_bootstrap(env_path: Path, env_name: str) -> int:
    """Offer to run tofu init/apply, then enable the S3 backend.

    Declining prints the manual checklist instead. Returns the exit code.
    """
    if not click.confirm("Run 'tofu init && tofu apply' now?", default=False):
        _numbered_steps(
            "Next steps:",
            [f"cd {env_path}"],
            ["AWS_PROFILE=admin tofu init"],
            ["AWS_PROFILE=admin tofu apply"],
        )
        print()
        print("  After successful apply, enable S3 backend:")
        print(f"    uv run python bin/init.py bootstrap --migrate-state {env_name}")
        return 0

    admin_profile = click.prompt("AWS admin profile name", default="admin", type=str).strip()

    if not _run_tofu("init", env_path, admin_profile):
        return 1

    if not _run_tofu("apply", env_path, admin_profile):
        return 1

    print("\nApply succeeded. Enabling S3 backend...")
    migrate_result = cmd_bootstrap_migrate(env_name, dry_run=False)
    if migrate_result != 0:
        return migrate_result

    print("Next step:")
    print(f"  cd {env_path}")
    print(f"  AWS_PROFILE={admin_profile} tofu init -migrate-state")
    print('  (answer "yes" to copy state to S3)')
    return 0


def cmd_bootstrap(dry_run: bool) -> int:
    """Interactively set up bootstrap infrastructure for a new AWS account."""
    print("Setting up deployer bootstrap for a new AWS account.\n")

    inputs = _prompt_bootstrap_inputs()
    if inputs is None:
        return 1

    env_path = _resolve_bootstrap_path(inputs.env_name, dry_run)
    if env_path is None:
        return 1

    try:
        files = generate_bootstrap(
            account_id=inputs.account_id,
            region=inputs.region,
            env_label=inputs.env_label,
            project_prefixes=inputs.project_prefixes,
            trusted_user_arns=inputs.trusted_user_arns,
            include_cognito=inputs.include_cognito,
            cognito_app_domains=inputs.cognito_app_domains,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if dry_run:
        _print_dry_run_preview(f"Would create directory: {env_path}", files)
        return 0

    _write_bootstrap_files(env_path, files)
    print()
    return _apply_bootstrap(env_path, inputs.env_name)


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

    with exit_on(ValueError):
        updated = uncomment_backend_block(content)

    if dry_run:
        _print_dry_run_preview(f"Would update: {main_tf}", updated)
        return 0

    main_tf.write_text(updated)
    print(f"S3 backend enabled in {main_tf}")
    print()
    _numbered_steps(
        "Next steps:",
        [f"cd {env_path}"],
        [
            "AWS_PROFILE=admin tofu init -migrate-state",
            '     (answer "yes" to copy state to S3)',
        ],
        ["AWS_PROFILE=admin tofu plan", '     (should show "No changes")'],
    )
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
        _print_dry_run_preview(f"Would write to: {output_path}", content)
        return 0

    # Write file
    output_path.write_text(content)
    print(f"Generated: {output_path}")
    print()
    _numbered_steps(
        "Next steps:",
        ["Review and customize the generated deploy.toml"],
        [
            "Create environment directory:",
            f"     uv run python bin/init.py environment --app-name {config['application']['name']} --template standalone-staging",
        ],
    )
    return 0


def _list_available_templates() -> int:
    """Print the available template names."""
    print("Available templates:")
    for name in list_templates():
        print(f"  {name}")
    return 0


def _require_bootstrap() -> bool:
    """Check that bootstrap has been run, since every environment depends on it.

    Returns:
        True if an environment can be created, False otherwise (the reason is
        printed).
    """
    try:
        get_environments_dir()
    except RuntimeError:
        print(_ENVIRONMENTS_DIR_UNSET, file=sys.stderr)
        return False

    if not bootstrap_dir_exists():
        print(
            "Error: No bootstrap directory found.\n"
            "Bootstrap creates IAM roles and S3 state bucket that all environments depend on.\n"
            "Run 'uv run python bin/init.py bootstrap' first.",
            file=sys.stderr,
        )
        return False
    return True


def _resolve_environment_target(
    app_name: str | None, template_name: str | None, dry_run: bool
) -> tuple[str, Path] | None:
    """Validate the template and --app-name pair, and locate the target directory.

    Returns:
        The environment type and the directory to create, or None if an
        argument is missing or the directory already exists (the reason is
        printed).

    Raises:
        SystemExit: If the template name carries no environment type.
    """
    if not template_name:
        print(
            "Error: --template is required (use --list-templates to see options)", file=sys.stderr
        )
        return None

    with exit_on(ValueError):
        env_type = extract_env_type(template_name)

    is_shared_infra = template_name.startswith("shared-infra-")
    if not is_shared_infra and not app_name:
        print("Error: --app-name is required for non-shared-infra templates", file=sys.stderr)
        return None

    env_name = template_name if is_shared_infra else f"{app_name}-{env_type}"
    env_path = get_environments_dir() / env_name
    if env_path.exists() and not dry_run:
        print(f"Error: Environment directory already exists: {env_path}", file=sys.stderr)
        print("Remove it first or use a different name.", file=sys.stderr)
        return None

    return env_type, env_path


def _write_environment_files(env_path: Path, files: dict[str, str], template_name: str) -> None:
    """Create the environment directory, write its files and link deployer.tf.

    The symlinks are standalone-only: shared templates take their module
    sources from the shared-infra environment instead.
    """
    is_standalone = template_name.startswith("standalone-")
    if is_standalone:
        created_symlinks = ensure_environments_symlinks()
        if created_symlinks:
            print(f"Created symlinks in {get_environments_dir()}: {', '.join(created_symlinks)}")

    env_path.mkdir(parents=True, exist_ok=True)
    for filepath, content in files.items():
        Path(filepath).write_text(content)
        print(f"Created: {filepath}")

    if is_standalone and create_deployer_tf_symlink(env_path):
        print(f"Created: {env_path}/deployer.tf -> shared environment config")


def cmd_environment(app_name, template, list_templates_flag, deploy_toml, domain, dry_run) -> int:
    """Create environment directory structure."""
    if list_templates_flag:
        return _list_available_templates()

    if not _require_bootstrap():
        return 1

    target = _resolve_environment_target(app_name, template, dry_run)
    if target is None:
        return 1
    env_type, env_path = target

    deploy_toml_path = None
    if deploy_toml:
        deploy_toml_path = Path(deploy_toml).resolve()
        if not deploy_toml_path.exists():
            print(f"Error: deploy.toml not found at {deploy_toml_path}", file=sys.stderr)
            return 1

    # Auto-assign listener priority for shared-app templates
    listener_priority = None
    if template.startswith("shared-app-"):
        listener_priority = get_next_listener_priority(env_type)

    try:
        files = generate_environment(
            app_name=app_name,
            template_name=template,
            deploy_toml_path=deploy_toml_path,
            domain=domain,
            listener_priority=listener_priority,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if dry_run:
        _print_dry_run_preview(f"Would create directory: {env_path}", files, max_lines=50)
        return 0

    _write_environment_files(env_path, files, template)

    print()
    _print_next_steps(env_path.name, env_path, env_type, app_name, template)
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

    if is_standalone:
        steps = [
            [f"Edit {env_path}/terraform.tfvars:", "     - Set database credentials"],
            [
                f"Edit {env_path}/services.auto.tfvars:",
                "     - Configure domain and Route53 zone ID",
                "     - Adjust service sizing if needed",
            ],
        ]
    elif is_shared_infra:
        steps = [
            [
                f"Edit {env_path}/terraform.tfvars:",
                "     - Set domain and Route53 zone ID",
                "     - Configure Cognito if needed",
            ]
        ]
    else:
        steps = [
            [
                f"Edit {env_path}/terraform.tfvars:",
                "     - Set database credentials",
                "     - Configure domain and Route53 zone ID",
                "     - Verify listener_rule_priority is unique",
            ]
        ]

    steps.append(
        [
            "Deploy infrastructure:",
            f"     ./bin/tofu.sh plan {env_name}",
            f"     ./bin/tofu.sh apply {env_name}",
        ]
    )

    if not is_shared_infra and app_name:
        steps.append(
            [
                "Create SSM secrets and deploy:",
                f'     aws ssm put-parameter --name "/{app_name}/{env_type}/secret-key" --value "..." --type SecureString',
                f"     uv run python bin/deploy.py {env_name}",
            ]
        )

    _numbered_steps("Next steps:", *steps)


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
@click.option(
    "--migrate-state",
    metavar="ENV_NAME",
    help="Phase 2: Enable S3 backend in an existing bootstrap directory",
)
@click.option(
    "--dry-run", "-n", is_flag=True, help="Show what would be generated without writing files"
)
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
@click.option(
    "--from-compose",
    metavar="PATH",
    help="Path to docker-compose.yml (default: ./docker-compose.yml)",
)
@click.option("--app-name", metavar="NAME", help="Application name (default: directory name)")
@click.option(
    "--output",
    "-o",
    metavar="PATH",
    help="Output path for deploy.toml (default: same directory as docker-compose.yml)",
)
@click.option(
    "--dry-run", "-n", is_flag=True, help="Show what would be generated without writing files"
)
def deploy_toml_cmd(from_compose, app_name, output, dry_run):
    """Generate deploy.toml from docker-compose.yml."""
    sys.exit(cmd_deploy_toml(from_compose, app_name, output, dry_run))


@cli.command("environment")
@click.option(
    "--app-name",
    metavar="NAME",
    help="Application name (required except for shared-infra templates)",
)
@click.option(
    "--template",
    "-t",
    metavar="NAME",
    help="Template to use (e.g., standalone-staging). Use --list-templates to see options.",
)
@click.option("--list-templates", is_flag=True, help="List available templates and exit")
@click.option(
    "--deploy-toml", metavar="PATH", help="Path to deploy.toml to read service configuration from"
)
@click.option("--domain", metavar="DOMAIN", help="Domain name for the environment")
@click.option(
    "--dry-run", "-n", is_flag=True, help="Show what would be created without writing files"
)
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


@cli.command("verify")
def verify_cmd():
    """Check tool versions and AWS profile configuration."""
    sys.exit(cmd_verify())


@cli.command("setup-profiles")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would be added without writing")
def setup_profiles_cmd(dry_run):
    """Generate AWS CLI profiles for deployer roles."""
    sys.exit(cmd_setup_profiles(dry_run))


if __name__ == "__main__":
    cli()
