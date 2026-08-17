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

# Which config.toml key holds each injected credential, per credential mode.
# validate() names the same eight keys; keeping both literal is what makes a
# key greppable from either end.
_SECRETSMANAGER_KEYS = {
    "app": (
        ("DB_USERNAME", "app_username_secret"),
        ("DB_PASSWORD", "app_password_secret"),
    ),
    "migrate": (
        ("DB_USERNAME", "migrate_username_secret"),
        ("DB_PASSWORD", "migrate_password_secret"),
    ),
}

_SSM_KEYS = {
    "app": (
        ("DB_USERNAME", "app_username_param"),
        ("DB_PASSWORD", "app_password_param"),
    ),
    "migrate": (
        ("DB_USERNAME", "migrate_username_param"),
        ("DB_PASSWORD", "migrate_password_param"),
    ),
}


class DatabaseModule(ResourceModule):
    """PostgreSQL database module."""

    @property
    @override
    def name(self) -> str:
        return "database"

    @override
    def validate(  # noqa: C901 — validates many credential/config combinations
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate database configuration."""
        errors, ok = self._validate_common(app_config, env_config, ["postgresql"])
        if not ok:
            return errors

        required = ["host", "port", "name"]
        for field in required:
            if not env_config.get(field):
                errors.append(f"[database] section missing '{field}' in config.toml")

        # Check extensions_lambda is present if app declares extensions
        extensions = app_config.get("extensions", [])
        if extensions and not env_config.get("extensions_lambda"):
            errors.append(
                "[database] deploy.toml declares extensions but config.toml "
                "is missing 'extensions_lambda' "
                '(add: extensions_lambda = "${tofu:db_users_lambda_function_name}")'
            )

        # Check credentials configuration
        credentials = env_config.get("credentials")
        if credentials == "secretsmanager":
            # Two-account model: require app and migrate credentials
            if not env_config.get("app_username_secret"):
                errors.append(
                    "[database] using secretsmanager but missing "
                    "'app_username_secret' in config.toml"
                )
            if not env_config.get("app_password_secret"):
                errors.append(
                    "[database] using secretsmanager but missing "
                    "'app_password_secret' in config.toml"
                )
            if not env_config.get("migrate_username_secret"):
                errors.append(
                    "[database] using secretsmanager but missing "
                    "'migrate_username_secret' in config.toml"
                )
            if not env_config.get("migrate_password_secret"):
                errors.append(
                    "[database] using secretsmanager but missing "
                    "'migrate_password_secret' in config.toml"
                )
        elif credentials == "ssm":
            # SSM mode: require app and migrate params
            if not env_config.get("app_username_param"):
                errors.append(
                    "[database] using ssm but missing 'app_username_param' in config.toml"
                )
            if not env_config.get("app_password_param"):
                errors.append(
                    "[database] using ssm but missing 'app_password_param' in config.toml"
                )
            if not env_config.get("migrate_username_param"):
                errors.append(
                    "[database] using ssm but missing 'migrate_username_param' in config.toml"
                )
            if not env_config.get("migrate_password_param"):
                errors.append(
                    "[database] using ssm but missing 'migrate_password_param' in config.toml"
                )
        elif credentials:
            errors.append(
                f"[database] credentials '{credentials}' not supported "
                "(use 'secretsmanager' or 'ssm')"
            )
        else:
            errors.append(
                "[database] section missing 'credentials' in config.toml "
                "(use 'secretsmanager' or 'ssm')"
            )

        return errors

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
                SecretReference(var, env_config[key]) for var, key in _SECRETSMANAGER_KEYS[mode]
            ]
        elif credentials == "ssm":
            # SSM configures parameter paths; the ARN is the deployment's.
            secrets = [
                SecretReference(var, context.ssm_parameter_arn(env_config[key]))
                for var, key in _SSM_KEYS[mode]
            ]
        # Any other value of `credentials` is rejected by validate(); collect()
        # does not re-check it, and emits no credentials at all.

        return ModuleOutput(environment=env_vars, secrets=secrets)
