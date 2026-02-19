"""CDN module for CloudFront.

Application declares:
    [cdn]
    type = "cloudfront"

Environment provides:
    [cdn]
    domain = "${tofu:cloudfront_domain}"
    key_id = "${tofu:cloudfront_key_id}"
    private_key_param = "/myapp/staging/cloudfront-private-key"

Injects: CLOUDFRONT_DOMAIN, CLOUDFRONT_KEY_ID, CLOUDFRONT_PRIVATE_KEY (secret)
"""

from typing import Any

from .base import (
    EnvironmentVariable,
    ModuleContext,
    ModuleOutput,
    ResourceModule,
    SecretReference,
)


class CdnModule(ResourceModule):
    """CloudFront CDN module."""

    @property
    def name(self) -> str:
        return "cdn"

    def validate(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate CDN configuration."""
        errors = []

        if not app_config:
            return []  # CDN not declared - not an error

        cdn_type = app_config.get("type")
        if not cdn_type:
            errors.append("[cdn] section missing 'type' in deploy.toml")
            return errors

        if cdn_type != "cloudfront":
            errors.append(f"[cdn] type '{cdn_type}' not supported (only 'cloudfront')")
            return errors

        # Check env config provides required fields
        if not env_config:
            errors.append("[cdn] section missing from config.toml")
            return errors

        if not env_config.get("domain"):
            errors.append("[cdn] section missing 'domain' in config.toml")

        if not env_config.get("key_id"):
            errors.append("[cdn] section missing 'key_id' in config.toml")

        if not env_config.get("private_key_param"):
            errors.append("[cdn] section missing 'private_key_param' in config.toml")

        return errors

    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
    ) -> ModuleOutput:
        """Collect CDN environment variables and secrets."""
        if not app_config or not app_config.get("type"):
            return ModuleOutput()

        env_vars = [
            EnvironmentVariable("CLOUDFRONT_DOMAIN", env_config["domain"]),
            EnvironmentVariable("CLOUDFRONT_KEY_ID", env_config["key_id"]),
        ]

        # Private key from SSM
        private_key_param = env_config["private_key_param"]
        secrets = [
            SecretReference(
                "CLOUDFRONT_PRIVATE_KEY",
                f"arn:aws:ssm:{context.region}:{context.account_id}:parameter{private_key_param}",
            )
        ]

        return ModuleOutput(environment=env_vars, secrets=secrets)
