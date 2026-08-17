"""Secrets module for SSM Parameter Store secrets.

Application declares:
    [secrets]
    names = ["SECRET_KEY", "SIGNED_URL_SECRET", "DATACITE_PASSWORD"]

Environment provides:
    [secrets]
    provider = "ssm"
    path_prefix = "/myapp/staging"

Name normalization: SECRET_KEY -> secret-key, SIGNED_URL_SECRET -> signed-url-secret

Injects: Each named secret from SSM
"""

import re
from typing import Any, override

from deployer.utils import advice_block

from .base import (
    ModuleContext,
    ModuleOutput,
    ResourceModule,
    SecretReference,
)


def explicit_path_keys(app_config: dict[str, Any]) -> list[str]:
    """The ``[secrets]`` keys using the removed explicit-path form.

    ``names`` is the only key a deploy.toml ``[secrets]`` section may carry.
    Anything else is a variable name mapped to an ``ssm:`` path or a
    ``secretsmanager:`` ARN -- the form ``deployer init`` generated until
    53h-2a, and the one that silently produced no secrets at all whenever the
    same deploy.toml also declared a module section.

    This is the single place that rule is written down; ``preflight`` and
    ``core.ssm_secrets`` both ask here rather than each deciding for
    themselves.

    Args:
        app_config: The ``[secrets]`` section from deploy.toml.

    Returns:
        The offending keys, sorted. Empty when the section is well formed.
    """
    return sorted(key for key in app_config if key != "names")


def explicit_path_error(keys: list[str]) -> str:
    """The migration message for a deploy.toml still using the explicit form.

    Args:
        keys: The offending keys, as returned by ``explicit_path_keys``.

    Returns:
        A formatted message naming the replacement, ready to become whichever
        exception the caller raises.
    """
    return advice_block(
        "deploy.toml [secrets] uses the explicit-path form, which was removed:",
        keys,
        (
            "Declare what the application needs instead:",
            "",
            "  [secrets]",
            "  names = [" + ", ".join(f'"{key}"' for key in keys) + "]",
            "",
            "Where those secrets live is the environment's answer, not the",
            "application's. Every generated config.toml already carries it:",
            "",
            "  [secrets]",
            '  provider = "ssm"',
            '  path_prefix = "/myapp/staging"',
            "",
            "SECRET_KEY then resolves to /myapp/staging/secret-key. See",
            "docs/CONFIG-REFERENCE.md and",
            "docs/internal/removed-features/explicit-secret-paths.md.",
        ),
        bullet="  - ",
    )


def normalize_secret_name(name: str) -> str:
    """Convert SECRET_KEY to secret-key format.

    Examples:
        SECRET_KEY -> secret-key
        SIGNED_URL_SECRET -> signed-url-secret
        DATACITE_PASSWORD -> datacite-password
    """
    # Replace underscores with hyphens and lowercase
    return name.replace("_", "-").lower()


class SecretsModule(ResourceModule):
    """SSM Parameter Store secrets module."""

    @property
    @override
    def name(self) -> str:
        return "secrets"

    @override
    def validate(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate secrets configuration."""
        errors = []

        if not app_config:
            return []  # Secrets not declared - not an error

        names = app_config.get("names", [])
        if not names:
            return []  # Empty names list is valid (no secrets needed)

        if not isinstance(names, list):
            errors.append("[secrets] 'names' must be a list in deploy.toml")
            return errors

        # Validate that names look like environment variable names
        env_var_pattern = re.compile(r"^[A-Z][A-Z0-9_]*$")
        for name in names:
            if not isinstance(name, str):
                errors.append(f"[secrets] name must be a string, got {type(name).__name__}")
            elif not env_var_pattern.match(name):
                errors.append(
                    f"[secrets] name '{name}' should be uppercase "
                    "with underscores (e.g., SECRET_KEY)"
                )

        # Check env config provides required fields
        if not env_config:
            errors.append("[secrets] section missing from config.toml")
            return errors

        provider = env_config.get("provider")
        if provider != "ssm":
            errors.append(f"[secrets] provider '{provider}' not supported (only 'ssm')")

        if not env_config.get("path_prefix"):
            errors.append("[secrets] section missing 'path_prefix' in config.toml")

        return errors

    @override
    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
    ) -> ModuleOutput:
        """Collect secrets references."""
        if not app_config:
            return ModuleOutput()

        names = app_config.get("names", [])
        if not names:
            return ModuleOutput()

        path_prefix = env_config["path_prefix"]
        # Ensure path_prefix starts with / and doesn't end with /
        if not path_prefix.startswith("/"):
            path_prefix = "/" + path_prefix
        path_prefix = path_prefix.rstrip("/")

        secrets = []
        for name in names:
            # Convert SECRET_KEY -> secret-key
            param_name = normalize_secret_name(name)
            param_path = f"{path_prefix}/{param_name}"

            secrets.append(SecretReference(name, context.ssm_parameter_arn(param_path)))

        return ModuleOutput(secrets=secrets)
