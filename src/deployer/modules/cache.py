"""Cache module for Redis connections.

Application declares:
    [cache]
    type = "redis"

Environment provides:
    [cache]
    url = "${tofu:redis_url}"

Injects: REDIS_URL
"""

from typing import Any

from .base import (
    EnvironmentVariable,
    ModuleContext,
    ModuleOutput,
    ResourceModule,
)


class CacheModule(ResourceModule):
    """Redis cache module."""

    @property
    def name(self) -> str:
        return "cache"

    def validate(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate cache configuration."""
        errors, ok = self._validate_common(app_config, env_config, ["redis"])
        if not ok:
            return errors

        if not env_config.get("url"):
            errors.append("[cache] section missing 'url' in config.toml")

        return errors

    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
    ) -> ModuleOutput:
        """Collect cache environment variables."""
        if not app_config or not app_config.get("type"):
            return ModuleOutput()

        return ModuleOutput(
            environment=[
                EnvironmentVariable("REDIS_URL", env_config["url"]),
            ]
        )
