"""Generate environment directory structure for deployer.

This module provides the main entry points for generating environments from
templates. Templates are self-contained directories under
templates/.

Two-pass approach:
  Pass 1 (template): Load .example files, substitute {{placeholders}}.
  Pass 2 (deploy-toml, optional): Replace services block from deploy.toml.
"""

import os
import re
import tomllib
from pathlib import Path

from deployer.utils import get_deployer_root, get_environments_dir

from .template import (
    build_services_block,
    extract_env_type,
    load_all_templates,
    parse_services_sizing,
    replace_hcl_services_block,
    substitute,
    substitute_optional,
)


def get_next_listener_priority(env_type: str) -> int:
    """Find next available listener priority for shared environment.

    Scans existing app environments using shared-infra-{env_type} and
    returns next available priority (100, 200, 300, ...).

    Args:
        env_type: Environment type ('staging' or 'production').

    Returns:
        Next available listener priority.
    """
    env_dir = get_environments_dir()
    shared_infra_name = f"shared-infra-{env_type}"

    # Find all environments that reference this shared infra
    existing_priorities = []
    for env_path in env_dir.iterdir():
        if env_path.is_dir() and env_path.name != shared_infra_name:
            tfvars_path = env_path / "terraform.tfvars"
            if tfvars_path.exists():
                try:
                    content = tfvars_path.read_text()
                    # Parse listener_rule_priority from tfvars
                    match = re.search(r"listener_rule_priority\s*=\s*(\d+)", content)
                    if match:
                        existing_priorities.append(int(match.group(1)))
                except Exception:  # noqa: BLE001, S110 — best-effort priority scan
                    pass

    # Return next available (100, 200, 300, ...)
    if not existing_priorities:
        return 100
    return max(existing_priorities) + 100


def create_deployer_tf_symlink(env_dir: Path) -> bool:
    """Create deployer.tf symlink in an environment directory.

    Creates a relative symlink from env_dir/deployer.tf to the shared
    deployer/environments/deployer.tf, computing the relative path
    dynamically from get_deployer_root().

    Args:
        env_dir: Path to the environment directory.

    Returns:
        True if symlink was created, False if it already exists.
    """
    link_path = env_dir / "deployer.tf"
    if link_path.exists():
        return False

    deployer_root = get_deployer_root()
    target = deployer_root / "environments" / "deployer.tf"

    try:
        relative_target = os.path.relpath(target, env_dir)
        link_path.symlink_to(relative_target)
        return True
    except OSError:
        return False


def generate_environment(
    app_name: str | None,
    template_name: str,
    deploy_toml_path: Path | None,
    domain: str | None,
    listener_priority: int | None = None,
) -> dict[str, str]:
    """Generate environment directory structure from a template.

    Two-pass approach:
      Pass 1: Load template files, substitute placeholders.
      Pass 2 (optional): If deploy_toml_path provided, replace services block.

    Args:
        app_name: Application name. Required for non-shared-infra templates.
        template_name: Template to use (e.g., 'standalone-staging').
        deploy_toml_path: Optional path to deploy.toml for service info.
        domain: Optional domain name for the environment.
        listener_priority: ALB listener rule priority (for shared-app templates).

    Returns:
        Dictionary mapping file paths to their contents.
    """
    env_type = extract_env_type(template_name)
    is_shared_infra = template_name.startswith("shared-infra-")

    # Compute env_name
    if is_shared_infra:
        env_name = template_name
    else:
        if not app_name:
            raise ValueError("--app-name is required for non-shared-infra templates")
        env_name = f"{app_name}-{env_type}"

    env_dir = get_environments_dir() / env_name

    # Default domain
    if domain is None:
        if is_shared_infra:
            domain = f"{env_type}.example.com"
        elif template_name.startswith("shared-app-"):
            domain = f"{app_name}.{env_type}.example.com"
        else:
            domain = f"{app_name}-{env_type}.example.com"

    # Default listener_priority for shared-app
    if listener_priority is None and template_name.startswith("shared-app-"):
        listener_priority = 100

    # Build substitution context
    context = {
        "env_type": env_type,
        "env_name": env_name,
        "domain": domain,
    }
    if app_name:
        context["app_name"] = app_name
    if listener_priority is not None:
        context["listener_priority"] = listener_priority

    # --- Pass 1: Template ---
    raw_templates = load_all_templates(template_name)
    files = {}

    for output_name, content in raw_templates.items():
        # Use substitute_optional to preserve ${tofu:...} placeholders
        # but catch missing required {{...}} placeholders
        try:
            rendered = substitute(content, **context)
        except KeyError:
            # Some templates may have placeholders not in context; use optional
            rendered = substitute_optional(content, **context)

        filepath = str(env_dir / output_name)
        files[filepath] = rendered

        # For standalone templates, terraform.tfvars also gets written as
        # terraform.tfvars.example (the gitignored secrets file needs an
        # example copy for team members)
        if output_name == "terraform.tfvars" and template_name.startswith("standalone-"):
            files[str(env_dir / "terraform.tfvars.example")] = rendered

    # --- Pass 2: Deploy-toml (optional) ---
    if deploy_toml_path and deploy_toml_path.exists():
        with open(deploy_toml_path, "rb") as f:
            deploy_config = tomllib.load(f)

        if "services" in deploy_config:
            _apply_deploy_toml_services(files, deploy_config, env_type)

    return files


def _apply_deploy_toml_services(
    files: dict[str, str],
    deploy_config: dict,
    _env_type: str,
) -> None:
    """Apply deploy.toml service definitions to generated files (Pass 2).

    Finds the file containing 'services = {', parses default sizing,
    builds a new services block from deploy.toml, and replaces it.

    Args:
        files: Mutable dict of {filepath: content} to modify in-place.
        deploy_config: Parsed deploy.toml dict.
        env_type: Environment type for default sizing.
    """
    # Find the file containing the services block
    target_path = None
    target_content = None

    for filepath, content in files.items():
        basename = os.path.basename(filepath)
        if basename in ("services.auto.tfvars", "terraform.tfvars") and re.search(
            r"^services\s*=\s*\{", content, re.MULTILINE
        ):
            target_path = filepath
            target_content = content
            break

    if target_path is None or target_content is None:
        return

    # Parse existing sizing as defaults
    default_sizing = parse_services_sizing(target_content)

    # Build new services block
    new_block = build_services_block(deploy_config, default_sizing)

    # Replace in the target file
    updated = replace_hcl_services_block(target_content, new_block)
    files[target_path] = updated

    # Also update the .example copy if present (standalone templates)
    example_path = target_path.replace("terraform.tfvars", "terraform.tfvars.example")
    if example_path != target_path and example_path in files:
        files[example_path] = updated


def update_services(
    env_name: str,
    deploy_toml_path: Path,
    dry_run: bool = False,
) -> str | None:
    """Update services block in an existing environment from deploy.toml.

    Args:
        env_name: Environment name (e.g., 'myapp-staging').
        deploy_toml_path: Path to deploy.toml.
        dry_run: If True, print the result instead of writing.

    Returns:
        Modified file content, or None if dry_run.

    Raises:
        FileNotFoundError: If environment or deploy.toml not found.
        ValueError: If no services block found in environment.
    """
    env_dir = get_environments_dir() / env_name
    if not env_dir.exists():
        raise FileNotFoundError(f"Environment directory not found: {env_dir}")

    if not deploy_toml_path.exists():
        raise FileNotFoundError(f"deploy.toml not found: {deploy_toml_path}")

    with open(deploy_toml_path, "rb") as f:
        deploy_config = tomllib.load(f)

    if "services" not in deploy_config:
        raise ValueError(f"No [services] section found in {deploy_toml_path}")

    # Find the file containing the services block
    target_file = None
    for candidate in ("services.auto.tfvars", "terraform.tfvars"):
        path = env_dir / candidate
        if path.exists():
            content = path.read_text()
            if re.search(r"^services\s*=\s*\{", content, re.MULTILINE):
                target_file = path
                break

    if target_file is None:
        raise ValueError(
            f"No file with 'services = {{' found in {env_dir}.\n"
            "Expected services.auto.tfvars or terraform.tfvars."
        )

    content = target_file.read_text()

    # Parse existing sizing as defaults
    default_sizing = parse_services_sizing(content)

    # Build new services block
    new_block = build_services_block(deploy_config, default_sizing)

    # Replace
    updated = replace_hcl_services_block(content, new_block)

    if dry_run:
        print(f"Would update: {target_file}")
        print()
        print(updated)
        return None

    target_file.write_text(updated)
    print(f"Updated: {target_file}")
    return updated


def generate_shared_infrastructure(
    template_name: str,
    domain: str | None = None,
) -> dict[str, str]:
    """Generate files for shared infrastructure environment.

    Convenience wrapper around generate_environment() for shared-infra templates.

    Args:
        template_name: Template name (e.g., 'shared-infra-staging').
        domain: Base domain for wildcard cert (e.g., 'staging.example.com').

    Returns:
        Dict of {filepath: content}.
    """
    return generate_environment(
        app_name=None,
        template_name=template_name,
        deploy_toml_path=None,
        domain=domain,
    )
