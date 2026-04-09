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
from typing import Any

from .base import (
    ModuleContext,
    ModuleOutput,
    ResourceModule,
    SecretReference,
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
    def name(self) -> str:
        return "secrets"

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

            secrets.append(
                SecretReference(
                    name, f"arn:aws:ssm:{context.region}:{context.account_id}:parameter{param_path}"
                )
            )

        return ModuleOutput(secrets=secrets)
