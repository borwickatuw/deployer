"""Template loading and substitution for environment generation.

This module loads .example files from templates/ (at the project root)
and substitutes {{placeholder}} values to generate environment configurations.

Templates are self-contained directories. Adding a new template = copying a
directory and editing files. No Python changes needed.
"""

import re
from pathlib import Path
from typing import Any


def _get_templates_dir() -> Path:
    """Get the path to the templates directory.

    Returns:
        Path to the templates/ directory at the project root.

    Raises:
        FileNotFoundError: If the templates directory doesn't exist.
    """
    # Navigate from src/deployer/init/ to project root
    module_dir = Path(__file__).parent
    project_root = module_dir.parent.parent.parent
    templates_dir = project_root / "templates"

    if not templates_dir.exists():
        raise FileNotFoundError(
            f"Templates directory not found: {templates_dir}\n"
            "This directory should exist in the deployer project root."
        )

    return templates_dir


def list_templates() -> list[str]:
    """List available template names.

    Returns:
        Sorted list of template directory names under templates/.
    """
    templates_dir = _get_templates_dir()
    return sorted(
        d.name for d in templates_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
    )


def get_template_dir(template_name: str) -> Path:
    """Get path to a template directory.

    Args:
        template_name: Name of the template (directory name under templates/).

    Returns:
        Path to the template directory.

    Raises:
        ValueError: If template not found, listing available templates.
    """
    templates_dir = _get_templates_dir()
    template_dir = templates_dir / template_name

    if not template_dir.is_dir():
        available = list_templates()
        raise ValueError(
            f"Template not found: {template_name}\n" f"Available templates: {', '.join(available)}"
        )

    return template_dir


def load_all_templates(template_name: str) -> dict[str, str]:
    """Load all .example files from a template directory.

    Returns a dict mapping output filenames (with .example stripped) to content.
    For example, 'main.tf.example' -> key 'main.tf', value is file content.

    Args:
        template_name: Name of the template directory.

    Returns:
        Dict of {output_filename: content}.
    """
    template_dir = get_template_dir(template_name)

    files = {}
    for path in sorted(template_dir.iterdir()):
        if path.is_file() and path.name.endswith(".example"):
            output_name = path.name.removesuffix(".example")
            files[output_name] = path.read_text()

    if not files:
        raise FileNotFoundError(f"No .example files found in template: {template_dir}")

    return files


_PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)(?:\s*\|\s*(\w+))?\}\}")


def _apply_filter(value: str, filter_name: str | None) -> str:
    """Apply a named filter to a template value.

    Args:
        value: The string value to filter.
        filter_name: Filter name (title, upper, lower) or None.

    Returns:
        Filtered string value.

    Raises:
        ValueError: If filter_name is not recognized.
    """
    if filter_name is None:
        return value
    if filter_name == "title":
        return value.title()
    if filter_name == "upper":
        return value.upper()
    if filter_name == "lower":
        return value.lower()
    raise ValueError(f"Unknown filter: {filter_name}")


# pysmelly: ignore inconsistent-error-handling — raises KeyError for missing placeholders;
# callers pass validated data or catch at their boundary.
def substitute(template: str, **kwargs: Any) -> str:
    """Replace {{placeholder}} patterns with provided values.

    Supports simple substitution and basic filters:
    - {{name}} -> replaces with kwargs['name']
    - {{name | title}} -> replaces with kwargs['name'].title()
    - {{name | upper}} -> replaces with kwargs['name'].upper()
    - {{name | lower}} -> replaces with kwargs['name'].lower()

    Args:
        template: Template string with {{placeholder}} patterns.
        **kwargs: Values to substitute. Keys should match placeholder names.

    Returns:
        Template with placeholders replaced.

    Raises:
        KeyError: If a required placeholder value is missing.
    """

    def replace_match(match: re.Match) -> str:
        name = match.group(1)
        if name not in kwargs:
            raise KeyError(
                f"Missing template value for placeholder: {{{{{name}}}}}\n"
                f"Available values: {list(kwargs.keys())}"
            )
        return _apply_filter(str(kwargs[name]), match.group(2))

    return _PLACEHOLDER_PATTERN.sub(replace_match, template)


def substitute_optional(template: str, **kwargs: Any) -> str:
    """Replace {{placeholder}} patterns, leaving unknown placeholders unchanged.

    This is useful for templates that contain placeholders meant for other
    systems (like ${tofu:...} placeholders in config.toml).

    Args:
        template: Template string with {{placeholder}} patterns.
        **kwargs: Values to substitute. Unknown placeholders are left as-is.

    Returns:
        Template with known placeholders replaced.
    """

    def replace_match(match: re.Match) -> str:
        name = match.group(1)
        if name not in kwargs:
            return match.group(0)
        return _apply_filter(str(kwargs[name]), match.group(2))

    return _PLACEHOLDER_PATTERN.sub(replace_match, template)


def replace_hcl_services_block(content: str, new_block: str) -> str:
    """Replace the top-level 'services = { ... }' HCL block.

    Uses brace-counting to find the matching close brace.

    Args:
        content: File content containing the services block.
        new_block: Replacement block text (should include 'services = { ... }').

    Returns:
        Content with the services block replaced.

    Raises:
        ValueError: If services block not found in content.
    """
    # Match "services = {" at the start of a line (with optional whitespace)
    pattern = re.compile(r"^(services\s*=\s*\{)", re.MULTILINE)
    match = pattern.search(content)
    if not match:
        raise ValueError("Block 'services' not found in content")

    start = match.start()

    # Count braces from the opening brace to find the matching close
    brace_count = 0
    pos = match.start(1)
    found_open = False
    end = len(content)

    for i in range(pos, len(content)):
        if content[i] == "{":
            brace_count += 1
            found_open = True
        elif content[i] == "}":
            brace_count -= 1
            if found_open and brace_count == 0:
                end = i + 1
                break

    return content[:start] + new_block + content[end:]


def build_services_block(deploy_config: dict, default_sizing: dict) -> str:
    """Build a services = { ... } HCL block from deploy.toml config.

    Args:
        deploy_config: Parsed deploy.toml dict (must have 'services' key).
        default_sizing: Default sizing dict with 'cpu', 'memory', 'replicas'.

    Returns:
        Formatted HCL services block string.
    """
    services = deploy_config.get("services", {})
    if not services:
        # Default to a single web service
        return (
            "services = {\n"
            "  web = {\n"
            f"    cpu               = {default_sizing['cpu']}\n"
            f"    memory            = {default_sizing['memory']}\n"
            f"    replicas          = {default_sizing['replicas']}\n"
            "    load_balanced     = true\n"
            "    port              = 8000\n"
            '    health_check_path = "/health/"\n'
            "  }\n"
            "}"
        )

    lines = ["services = {"]
    for name, svc in services.items():
        lines.append(f"  {name} = {{")
        lines.append(f"    cpu               = {default_sizing['cpu']}")
        lines.append(f"    memory            = {default_sizing['memory']}")
        lines.append(f"    replicas          = {default_sizing['replicas']}")

        if svc.get("port"):
            lines.append("    load_balanced     = true")
            lines.append(f"    port              = {svc['port']}")
            health_path = svc.get("health_check_path", "/health/")
            lines.append(f'    health_check_path = "{health_path}"')
        else:
            lines.append("    load_balanced     = false")

        if svc.get("path_pattern"):
            lines.append(f'    path_pattern      = "{svc["path_pattern"]}"')
        if svc.get("health_check_matcher"):
            lines.append(f'    health_check_matcher = "{svc["health_check_matcher"]}"')
        if svc.get("service_discovery"):
            lines.append("    service_discovery = true")

        lines.append("  }")
    lines.append("}")
    return "\n".join(lines)


def extract_env_type(template_name: str) -> str:
    """Extract the environment type from a template name.

    The env_type is the last segment that matches 'staging' or 'production'.

    Args:
        template_name: Template name (e.g., 'standalone-staging').

    Returns:
        'staging' or 'production'.

    Raises:
        ValueError: If no valid env_type found in template name.
    """
    parts = template_name.split("-")
    for part in reversed(parts):
        if part in ("staging", "production"):
            return part

    raise ValueError(
        f"Cannot determine environment type from template '{template_name}'. "
        "Template name must contain 'staging' or 'production'."
    )


def parse_services_sizing(content: str) -> dict[str, int]:
    """Parse default sizing from an existing services block.

    Extracts cpu, memory, replicas from the first (web) service definition.

    Args:
        content: File content containing a services = { ... } block.

    Returns:
        Dict with 'cpu', 'memory', 'replicas' keys.
    """
    defaults = {"cpu": 256, "memory": 512, "replicas": 1}

    for key in ("cpu", "memory", "replicas"):
        match = re.search(rf"{key}\s*=\s*(\d+)", content)
        if match:
            defaults[key] = int(match.group(1))

    return defaults
