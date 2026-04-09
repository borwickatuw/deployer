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

from typing import Any

from .base import (
    EnvironmentVariable,
    ModuleContext,
    ModuleOutput,
    ResourceModule,
    SecretReference,
)


class DatabaseModule(ResourceModule):
    """PostgreSQL database module."""

    @property
    def name(self) -> str:
        return "database"

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

    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
        credential_mode: str = "app",
    ) -> ModuleOutput:
        """Collect database environment variables and secrets.

        Args:
            app_config: Application's [database] section from deploy.toml
            env_config: Environment's [database] section from config.toml
            context: Module context with region, account_id, etc.
            credential_mode: Which credentials to use:
                - "app": DML-only credentials (default, for runtime services)
                - "migrate": DDL+DML credentials (for migrations)
        """
        if not app_config or not app_config.get("type"):
            return ModuleOutput()

        if credential_mode not in ("app", "migrate"):
            raise ValueError(f"credential_mode must be 'app' or 'migrate', got '{credential_mode}'")

        env_vars = [
            EnvironmentVariable("DB_HOST", env_config["host"]),
            EnvironmentVariable("DB_PORT", str(env_config["port"])),
            EnvironmentVariable("DB_NAME", env_config["name"]),
        ]

        secrets = []
        credentials = env_config.get("credentials")

        if credentials == "secretsmanager":
            # Select credentials based on mode
            if credential_mode == "app":
                username_secret = env_config["app_username_secret"]
                password_secret = env_config["app_password_secret"]
            else:  # migrate
                username_secret = env_config["migrate_username_secret"]
                password_secret = env_config["migrate_password_secret"]

            secrets.append(SecretReference("DB_USERNAME", username_secret))
            secrets.append(SecretReference("DB_PASSWORD", password_secret))

        elif credentials == "ssm":
            # Select credentials based on mode
            if credential_mode == "app":
                username_param = env_config["app_username_param"]
                password_param = env_config["app_password_param"]
            else:  # migrate
                username_param = env_config["migrate_username_param"]
                password_param = env_config["migrate_password_param"]

            secrets.append(
                SecretReference(
                    "DB_USERNAME",
                    f"arn:aws:ssm:{context.region}:{context.account_id}:parameter{username_param}",
                )
            )
            secrets.append(
                SecretReference(
                    "DB_PASSWORD",
                    f"arn:aws:ssm:{context.region}:{context.account_id}:parameter{password_param}",
                )
            )

        return ModuleOutput(environment=env_vars, secrets=secrets)
