"""Storage module for S3 buckets.

Application declares:
    [storage]
    type = "s3"
    buckets = ["media"]  # or ["originals", "media"] for multiple buckets

Environment provides:
    [storage]
    media_bucket = "${tofu:s3_media_bucket}"
    originals_bucket = "${tofu:s3_originals_bucket}"  # if declared

Injects: S3_MEDIA_BUCKET, S3_ORIGINALS_BUCKET (if declared)
"""

from typing import Any

from .base import (
    EnvironmentVariable,
    ModuleContext,
    ModuleOutput,
    ResourceModule,
)


class StorageModule(ResourceModule):
    """S3 storage module."""

    @property
    def name(self) -> str:
        return "storage"

    def validate(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate storage configuration."""
        errors, ok = self._validate_common(app_config, env_config, ["s3"])
        if not ok:
            return errors

        buckets = app_config.get("buckets", [])
        if not buckets:
            errors.append("[storage] section missing 'buckets' list in deploy.toml")
            return errors

        if not isinstance(buckets, list):
            errors.append("[storage] 'buckets' must be a list in deploy.toml")
            return errors

        for bucket_name in buckets:
            config_key = f"{bucket_name}_bucket"
            if not env_config.get(config_key):
                errors.append(
                    f"[storage] missing '{config_key}' in config.toml for declared bucket '{bucket_name}'"
                )

        return errors

    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
    ) -> ModuleOutput:
        """Collect storage environment variables."""
        if not app_config or not app_config.get("type"):
            return ModuleOutput()

        buckets = app_config.get("buckets", [])
        env_vars = []

        for bucket_name in buckets:
            # Convert bucket name to env var format: "media" -> "S3_MEDIA_BUCKET"
            env_var_name = f"S3_{bucket_name.upper()}_BUCKET"
            config_key = f"{bucket_name}_bucket"
            env_vars.append(EnvironmentVariable(env_var_name, env_config[config_key]))

        return ModuleOutput(environment=env_vars)
