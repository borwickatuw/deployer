"""Bootstrap infrastructure setup for new AWS accounts.

Generates the bootstrap directory that creates IAM roles, S3 state bucket,
and permissions boundary. All other environments depend on these resources.
"""

import json
import re

from ..utils import get_environments_dir, run_command
from .template import load_all_templates, substitute


def detect_aws_account_id() -> str | None:
    """Try to auto-detect AWS account ID via the AWS CLI.

    Returns:
        12-digit account ID string, or None if detection fails.
    """
    success, output = run_command(
        ["aws", "sts", "get-caller-identity", "--output", "json"]
    )
    if not success:
        return None
    try:
        data = json.loads(output)
        return data.get("Account")
    except (json.JSONDecodeError, KeyError):
        return None


def format_hcl_list(items: list[str]) -> str:
    """Format a Python list as an HCL list literal.

    Args:
        items: List of string values.

    Returns:
        HCL-formatted list, e.g. '["a", "b"]'.
    """
    quoted = [f'"{item}"' for item in items]
    return f'[{", ".join(quoted)}]'


def format_hcl_map(items: dict[str, str]) -> str:
    """Format a Python dict as an HCL map literal.

    Args:
        items: Dict of string key-value pairs.

    Returns:
        HCL-formatted map (multi-line with indentation).
    """
    if not items:
        return "{}"
    lines = ["{"]
    for key, value in items.items():
        lines.append(f'  {key} = "{value}"')
    lines.append("}")
    return "\n".join(lines)


_BACKEND_START = "# BOOTSTRAP-BACKEND-START"
_BACKEND_END = "# BOOTSTRAP-BACKEND-END"


def uncomment_backend_block(content: str) -> str:
    """Uncomment the S3 backend block in main.tf.

    Finds the BOOTSTRAP-BACKEND-START/END markers and removes the
    comment prefix from lines between them, enabling the S3 backend.

    Args:
        content: Content of main.tf with commented backend.

    Returns:
        Content with backend block uncommented and markers removed.

    Raises:
        ValueError: If markers are not found.
    """
    lines = content.splitlines()
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == _BACKEND_START:
            start_idx = i
        elif stripped == _BACKEND_END:
            end_idx = i

    if start_idx is None or end_idx is None:
        raise ValueError(
            "Could not find BOOTSTRAP-BACKEND-START/END markers in main.tf. "
            "Has the backend already been enabled?"
        )

    result = []
    for i, line in enumerate(lines):
        if i == start_idx or i == end_idx:
            continue  # Remove marker lines
        if start_idx < i < end_idx:
            # Remove leading "# " comment prefix (preserve indentation)
            result.append(re.sub(r"^(\s*)# ", r"\1", line))
        else:
            result.append(line)

    return "\n".join(result) + "\n"


def generate_bootstrap(
    account_id: str,
    region: str,
    env_label: str,
    project_prefixes: list[str],
    trusted_user_arns: list[str],
    include_cognito: bool = False,
    cognito_app_domains: dict[str, str] | None = None,
) -> dict[str, str]:
    """Generate bootstrap directory files from templates.

    Args:
        account_id: AWS account ID.
        region: AWS region.
        env_label: Environment label (e.g., "staging", "production").
        project_prefixes: List of project name prefixes.
        trusted_user_arns: List of IAM user ARNs.
        include_cognito: Whether to include shared Cognito pool.
        cognito_app_domains: Map of app name to domain (required if include_cognito).

    Returns:
        Dict mapping relative filenames to content.
    """
    env_name = f"bootstrap-{env_label}"

    context = {
        "account_id": account_id,
        "region": region,
        "env_type": env_label,
        "env_name": env_name,
        "project_prefixes_hcl": format_hcl_list(project_prefixes),
        "trusted_user_arns_hcl": format_hcl_list(trusted_user_arns),
    }

    # Load all template files
    templates = load_all_templates("bootstrap")

    # Handle Cognito fragment: pop it from templates, append to main.tf if requested
    cognito_fragment = templates.pop("main-cognito.tf", None)
    cognito_tfvars = templates.pop("cognito.auto.tfvars", None)

    if include_cognito and cognito_fragment:
        context["cognito_app_domains_hcl"] = format_hcl_map(
            cognito_app_domains or {}
        )
        templates["main.tf"] += substitute(cognito_fragment, **context)

    # Substitute placeholders in all remaining templates
    result = {}
    for filename, content in templates.items():
        result[filename] = substitute(content, **context)

    # Add cognito.auto.tfvars if Cognito enabled
    if include_cognito and cognito_tfvars:
        result["cognito.auto.tfvars"] = substitute(cognito_tfvars, **context)

    return result


def bootstrap_dir_exists() -> str | None:
    """Check if any bootstrap directory exists in the environments dir.

    Returns:
        Name of the first bootstrap directory found, or None.
    """
    try:
        env_dir = get_environments_dir()
    except RuntimeError:
        return None

    if not env_dir.exists():
        return None

    for path in sorted(env_dir.iterdir()):
        if path.is_dir() and path.name.startswith("bootstrap-"):
            return path.name

    return None
