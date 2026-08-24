"""Database module for PostgreSQL connections.

Implements a two-account model for security:
- App user (DML only): SELECT, INSERT, UPDATE, DELETE - used by runtime services
- Migrate user (DDL + DML): CREATE, ALTER, DROP, etc. - used for migrations

This reduces blast radius if the application is compromised.

Application declares:
    [database]
    type = "postgresql"

Environment provides:
    [database]
    host = "${tofu:db_host}"
    port = "${tofu:db_port}"
    name = "${tofu:db_name}"
    credentials = "secretsmanager"
    # App credentials (DML only - for runtime services)
    app_username_secret = "${tofu:db_app_username_secret_arn}"
    app_password_secret = "${tofu:db_app_password_secret_arn}"
    # Migrate credentials (DDL + DML - for migrations only)
    migrate_username_secret = "${tofu:db_migrate_username_secret_arn}"
    migrate_password_secret = "${tofu:db_migrate_password_secret_arn}"

Injects: DB_HOST, DB_PORT, DB_NAME, DB_USERNAME (secret), DB_PASSWORD (secret)
"""

from typing import Any, override

from .base import (
    EnvironmentVariable,
    ModuleContext,
    ModuleOutput,
    ResourceModule,
    SecretReference,
)

#: Which config.toml key holds each injected credential, by credential provider
#: and then by credential mode. **The one canonical statement of "which key
#: holds what"**: ``collect()`` reads a single mode's pairs out of it, and
#: ``validate()`` reads every key in a provider's table to decide what
#: config.toml must supply. 53h-1 introduced the two inner tables and left
#: validate() naming the same eight keys again in eight hand-written
#: conditions; 53h-2b removed that second copy.
_CREDENTIAL_KEYS = {
    "secretsmanager": {
        "app": (
            ("DB_USERNAME", "app_username_secret"),
            ("DB_PASSWORD", "app_password_secret"),
        ),
        "migrate": (
            ("DB_USERNAME", "migrate_username_secret"),
            ("DB_PASSWORD", "migrate_password_secret"),
        ),
    },
    "ssm": {
        "app": (
            ("DB_USERNAME", "app_username_param"),
            ("DB_PASSWORD", "app_password_param"),
        ),
        "migrate": (
            ("DB_USERNAME", "migrate_username_param"),
            ("DB_PASSWORD", "migrate_password_param"),
        ),
    },
}

_SUPPORTED_CREDENTIALS = "'" + "' or '".join(_CREDENTIAL_KEYS) + "'"

_CONNECTION_FIELDS = ("host", "port", "name")


def _connection_errors(env_config: dict[str, Any]) -> list[str]:
    """Report any missing host/port/name."""
    return [
        f"[database] section missing '{field}' in config.toml"
        for field in _CONNECTION_FIELDS
        if not env_config.get(field)
    ]


def _extension_errors(app_config: dict[str, Any], env_config: dict[str, Any]) -> list[str]:
    """Report a declared extension list the environment cannot install."""
    if not app_config.get("extensions") or env_config.get("extensions_lambda"):
        return []

    return [
        "[database] deploy.toml declares extensions but config.toml "
        "is missing 'extensions_lambda' "
        '(add: extensions_lambda = "${tofu:db_users_lambda_function_name}")'
    ]


def _credential_errors(env_config: dict[str, Any]) -> list[str]:
    """Report a missing, unsupported or incompletely configured credential source.

    The two-account model means a provider needs all four keys -- app and
    migrate, username and password -- and those four are exactly the keys
    ``_CREDENTIAL_KEYS`` already lists for ``collect()``.
    """
    credentials = env_config.get("credentials")

    if credentials in _CREDENTIAL_KEYS:
        return [
            f"[database] using {credentials} but missing '{key}' in config.toml"
            for pairs in _CREDENTIAL_KEYS[credentials].values()
            for _var, key in pairs
            if not env_config.get(key)
        ]

    if credentials:
        return [
            f"[database] credentials '{credentials}' not supported "
            f"(use {_SUPPORTED_CREDENTIALS})"
        ]

    return [
        "[database] section missing 'credentials' in config.toml " f"(use {_SUPPORTED_CREDENTIALS})"
    ]


class DatabaseModule(ResourceModule):
    """PostgreSQL database module."""

    @property
    @override
    def name(self) -> str:
        return "database"

    @override
    def validate(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate database configuration."""
        errors, ok = self._validate_common(app_config, env_config, ["postgresql"])
        if not ok:
            return errors

        return [
            *errors,
            *_connection_errors(env_config),
            *_extension_errors(app_config, env_config),
            *_credential_errors(env_config),
        ]

    @override
    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
    ) -> ModuleOutput:
        """Collect database environment variables and secrets.

        Which credential pair is injected comes from
        ``context.credential_mode``; see ``ModuleContext``.

        Args:
            app_config: Application's [database] section from deploy.toml
            env_config: Environment's [database] section from config.toml
            context: Module context with region, account_id and credential mode
        """
        if not app_config or not app_config.get("type"):
            return ModuleOutput()

        env_vars = [
            EnvironmentVariable("DB_HOST", env_config["host"]),
            EnvironmentVariable("DB_PORT", str(env_config["port"])),
            EnvironmentVariable("DB_NAME", env_config["name"]),
        ]

        mode = context.credential_mode
        credentials = env_config.get("credentials")
        secrets: list[SecretReference] = []

        if credentials == "secretsmanager":
            # Secrets Manager ARNs are configured whole.
            secrets = [
                SecretReference(var, env_config[key])
                for var, key in _CREDENTIAL_KEYS["secretsmanager"][mode]
            ]
        elif credentials == "ssm":
            # SSM configures parameter paths; the ARN is the deployment's.
            secrets = [
                SecretReference(var, context.ssm_parameter_arn(env_config[key]))
                for var, key in _CREDENTIAL_KEYS["ssm"][mode]
            ]
        else:
            # Unreachable: validate() rejects any other value, and
            # preflight's check_modules() is in run_preflight_checks'
            # always-run block, before Deployer is constructed. Reaching here
            # is a deployer bug, not an operator error -- and emitting a task
            # definition with connection details and no credentials would ship
            # that bug as a container that starts and cannot authenticate.
            raise RuntimeError(
                f"[database] collect() reached an unvalidated credentials value "
                f"{credentials!r}; validate() should have rejected it"
            )

        return ModuleOutput(environment=env_vars, secrets=secrets)

    @override
    def injected_names(self, app_config: dict[str, Any]) -> set[str]:
        """The connection triple plus the credential pair.

        The credential names do not depend on the provider or the mode --
        every column of ``_CREDENTIAL_KEYS`` maps to the same two variables --
        so this reads them off the table rather than repeating them.
        """
        if not app_config.get("type"):
            return set()

        return {"DB_HOST", "DB_PORT", "DB_NAME"} | {
            var
            for by_mode in _CREDENTIAL_KEYS.values()
            for pairs in by_mode.values()
            for var, _key in pairs
        }
