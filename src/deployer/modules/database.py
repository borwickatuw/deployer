"""Database module for PostgreSQL connections.

Application declares:
    [database]
    type = "postgresql"

Environment provides:
    [database]
    host = "${tofu:db_host}"
    port = "${tofu:db_port}"
    name = "${tofu:db_name}"
    credentials = "secretsmanager"  # or "ssm"
    username_secret = "${tofu:db_username_secret_arn}"
    password_secret = "${tofu:db_password_secret_arn}"

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

    def validate(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
    ) -> list[str]:
        """Validate database configuration."""
        errors = []

        # Check app declares a type
        if not app_config:
            return []  # Database not declared - not an error

        db_type = app_config.get("type")
        if not db_type:
            errors.append("[database] section missing 'type' in deploy.toml")
            return errors

        if db_type != "postgresql":
            errors.append(f"[database] type '{db_type}' not supported (only 'postgresql')")
            return errors

        # Check env config provides required fields
        if not env_config:
            errors.append("[database] section missing from config.toml")
            return errors

        required = ["host", "port", "name"]
        for field in required:
            if not env_config.get(field):
                errors.append(f"[database] section missing '{field}' in config.toml")

        # Check credentials configuration
        credentials = env_config.get("credentials")
        if credentials == "secretsmanager":
            if not env_config.get("username_secret"):
                errors.append("[database] using secretsmanager but missing 'username_secret' in config.toml")
            if not env_config.get("password_secret"):
                errors.append("[database] using secretsmanager but missing 'password_secret' in config.toml")
        elif credentials == "ssm":
            if not env_config.get("username_param"):
                errors.append("[database] using ssm but missing 'username_param' in config.toml")
            if not env_config.get("password_param"):
                errors.append("[database] using ssm but missing 'password_param' in config.toml")
        elif credentials:
            errors.append(f"[database] credentials '{credentials}' not supported (use 'secretsmanager' or 'ssm')")
        else:
            errors.append("[database] section missing 'credentials' in config.toml (use 'secretsmanager' or 'ssm')")

        return errors

    def collect(
        self,
        app_config: dict[str, Any],
        env_config: dict[str, Any],
        context: ModuleContext,
    ) -> ModuleOutput:
        """Collect database environment variables and secrets."""
        if not app_config or not app_config.get("type"):
            return ModuleOutput()

        env_vars = [
            EnvironmentVariable("DB_HOST", env_config["host"]),
            EnvironmentVariable("DB_PORT", str(env_config["port"])),
            EnvironmentVariable("DB_NAME", env_config["name"]),
        ]

        secrets = []
        credentials = env_config.get("credentials")

        if credentials == "secretsmanager":
            # Secrets Manager ARNs are used directly
            secrets.append(SecretReference(
                "DB_USERNAME",
                env_config["username_secret"]
            ))
            secrets.append(SecretReference(
                "DB_PASSWORD",
                env_config["password_secret"]
            ))
        elif credentials == "ssm":
            # SSM paths need to be converted to ARNs
            username_param = env_config["username_param"]
            password_param = env_config["password_param"]
            secrets.append(SecretReference(
                "DB_USERNAME",
                f"arn:aws:ssm:{context.region}:{context.account_id}:parameter{username_param}"
            ))
            secrets.append(SecretReference(
                "DB_PASSWORD",
                f"arn:aws:ssm:{context.region}:{context.account_id}:parameter{password_param}"
            ))

        return ModuleOutput(environment=env_vars, secrets=secrets)
