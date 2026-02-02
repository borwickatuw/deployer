"""Template loading and substitution for environment generation.

This module loads .example files from example-deployer-environments/ and
substitutes {{placeholder}} values to generate environment configurations.

The example files serve as the single source of truth - they are both
documentation (valid copy-paste examples) and templates for generation.
"""

import re
from pathlib import Path
from typing import Any


def get_templates_dir() -> Path:
    """Get the path to the example-deployer-environments directory.

    Returns:
        Path to the templates directory.

    Raises:
        FileNotFoundError: If the templates directory doesn't exist.
    """
    # Navigate from src/deployer/init/ to project root
    module_dir = Path(__file__).parent
    project_root = module_dir.parent.parent.parent
    templates_dir = project_root / "example-deployer-environments"

    if not templates_dir.exists():
        raise FileNotFoundError(
            f"Templates directory not found: {templates_dir}\n"
            "This directory should exist in the deployer project root."
        )

    return templates_dir


def load_template(template_type: str, env_type: str, filename: str) -> str:
    """Load a template file from example-deployer-environments.

    Args:
        template_type: Type of template directory:
            - "standalone" -> myapp-{env_type}/
            - "shared-infra" -> shared-infra-{env_type}/
            - "shared-app" -> app-on-shared-{env_type}/
        env_type: Environment type ('staging' or 'production').
        filename: Name of the file to load (e.g., 'main.tf.example').

    Returns:
        Template content as string.

    Raises:
        FileNotFoundError: If template file doesn't exist.
        ValueError: If template_type is invalid.
    """
    templates_dir = get_templates_dir()

    # Map template type to directory name
    if template_type == "standalone":
        dir_name = f"myapp-{env_type}"
    elif template_type == "shared-infra":
        dir_name = f"shared-infra-{env_type}"
    elif template_type == "shared-app":
        dir_name = f"app-on-shared-{env_type}"
    else:
        raise ValueError(
            f"Invalid template_type: {template_type}. "
            "Must be 'standalone', 'shared-infra', or 'shared-app'."
        )

    template_path = templates_dir / dir_name / filename
    if not template_path.exists():
        raise FileNotFoundError(
            f"Template not found: {template_path}\n"
            f"Expected template at: {template_path}"
        )

    return template_path.read_text()


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
    # Pattern matches {{name}} or {{name | filter}}
    pattern = re.compile(r'\{\{(\w+)(?:\s*\|\s*(\w+))?\}\}')

    def replace_match(match: re.Match) -> str:
        name = match.group(1)
        filter_name = match.group(2)

        if name not in kwargs:
            raise KeyError(
                f"Missing template value for placeholder: {{{{{name}}}}}\n"
                f"Available values: {list(kwargs.keys())}"
            )

        value = kwargs[name]

        # Apply filter if specified
        if filter_name:
            if filter_name == "title":
                value = str(value).title()
            elif filter_name == "upper":
                value = str(value).upper()
            elif filter_name == "lower":
                value = str(value).lower()
            else:
                raise ValueError(f"Unknown filter: {filter_name}")

        return str(value)

    return pattern.sub(replace_match, template)


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
    pattern = re.compile(r'\{\{(\w+)(?:\s*\|\s*(\w+))?\}\}')

    def replace_match(match: re.Match) -> str:
        name = match.group(1)
        filter_name = match.group(2)

        if name not in kwargs:
            # Leave unknown placeholders unchanged
            return match.group(0)

        value = kwargs[name]

        if filter_name:
            if filter_name == "title":
                value = str(value).title()
            elif filter_name == "upper":
                value = str(value).upper()
            elif filter_name == "lower":
                value = str(value).lower()

        return str(value)

    return pattern.sub(replace_match, template)
