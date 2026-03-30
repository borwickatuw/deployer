"""Tests for the resource module system."""

import pytest

from deployer.config import DeployConfig
from deployer.modules import (
    ModuleContext,
    ModuleOutput,
    ModuleRegistry,
    resolve_service_urls,
)
from deployer.modules.cache import CacheModule
from deployer.modules.database import DatabaseModule
from deployer.modules.secrets import SecretsModule, normalize_secret_name
from deployer.modules.storage import StorageModule


class TestModuleContext:
    """Tests for ModuleContext."""

    def test_create_context(self):
        """Test creating a module context."""
        ctx = ModuleContext(
            region="us-west-2",
            account_id="123456789",
            environment="staging",
            app_name="testapp",
            domain_name="test.example.com",
        )
        assert ctx.region == "us-west-2"
        assert ctx.account_id == "123456789"
        assert ctx.environment == "staging"
        assert ctx.app_name == "testapp"
        assert ctx.domain_name == "test.example.com"


class TestModuleOutput:
    """Tests for ModuleOutput."""

    def test_merge_outputs(self):
        """Test merging two ModuleOutputs."""
        from deployer.modules.base import EnvironmentVariable, SecretReference

        output1 = ModuleOutput(
            environment=[EnvironmentVariable("VAR1", "value1")],
            secrets=[SecretReference("SECRET1", "arn:aws:ssm:...")],
        )
        output2 = ModuleOutput(
            environment=[EnvironmentVariable("VAR2", "value2")],
            secrets=[SecretReference("SECRET2", "arn:aws:secretsmanager:...")],
        )

        merged = output1.merge(output2)

        assert len(merged.environment) == 2
        assert len(merged.secrets) == 2
        assert merged.environment[0].name == "VAR1"
        assert merged.environment[1].name == "VAR2"


class TestDatabaseModule:
    """Tests for DatabaseModule."""

    def test_validate_postgresql(self):
        """Test validation with valid postgresql config."""
        module = DatabaseModule()
        app_config = {"type": "postgresql"}
        env_config = {
            "host": "db.example.com",
            "port": "5432",
            "name": "testdb",
            "credentials": "secretsmanager",
            "app_username_secret": "arn:aws:secretsmanager:...:username",
            "app_password_secret": "arn:aws:secretsmanager:...:password",
            "migrate_username_secret": "arn:aws:secretsmanager:...:username",
            "migrate_password_secret": "arn:aws:secretsmanager:...:password",
        }

        errors = module.validate(app_config, env_config)

        assert len(errors) == 0

    def test_validate_extensions_requires_lambda(self):
        """Test validation fails when extensions declared but no extensions_lambda."""
        module = DatabaseModule()
        app_config = {"type": "postgresql", "extensions": ["unaccent", "pg_bigm"]}
        env_config = {
            "host": "db.example.com",
            "port": "5432",
            "name": "testdb",
            "credentials": "secretsmanager",
            "app_username_secret": "arn:...",
            "app_password_secret": "arn:...",
            "migrate_username_secret": "arn:...",
            "migrate_password_secret": "arn:...",
        }

        errors = module.validate(app_config, env_config)

        assert any("extensions_lambda" in e for e in errors)

    def test_validate_extensions_passes_with_lambda(self):
        """Test validation passes when extensions and extensions_lambda both present."""
        module = DatabaseModule()
        app_config = {"type": "postgresql", "extensions": ["unaccent"]}
        env_config = {
            "host": "db.example.com",
            "port": "5432",
            "name": "testdb",
            "credentials": "secretsmanager",
            "app_username_secret": "arn:...",
            "app_password_secret": "arn:...",
            "migrate_username_secret": "arn:...",
            "migrate_password_secret": "arn:...",
            "extensions_lambda": "myapp-staging-create-db-users",
        }

        errors = module.validate(app_config, env_config)

        assert len(errors) == 0

    def test_validate_no_extensions_no_lambda_ok(self):
        """Test validation passes when no extensions declared (lambda not required)."""
        module = DatabaseModule()
        app_config = {"type": "postgresql"}
        env_config = {
            "host": "db.example.com",
            "port": "5432",
            "name": "testdb",
            "credentials": "secretsmanager",
            "app_username_secret": "arn:...",
            "app_password_secret": "arn:...",
            "migrate_username_secret": "arn:...",
            "migrate_password_secret": "arn:...",
        }

        errors = module.validate(app_config, env_config)

        assert len(errors) == 0

    def test_validate_missing_credentials(self):
        """Test validation fails when credentials config missing."""
        module = DatabaseModule()
        app_config = {"type": "postgresql"}
        env_config = {
            "host": "db.example.com",
            "port": "5432",
            "name": "testdb",
        }

        errors = module.validate(app_config, env_config)

        assert any("credentials" in e for e in errors)

    def test_validate_missing_env_config(self):
        """Test validation fails when env_config is missing."""
        module = DatabaseModule()
        app_config = {"type": "postgresql"}

        errors = module.validate(app_config, {})

        assert any("missing from config.toml" in e for e in errors)

    def test_collect_secretsmanager(self):
        """Test collecting database config with Secrets Manager."""
        module = DatabaseModule()
        app_config = {"type": "postgresql"}
        env_config = {
            "host": "db.example.com",
            "port": 5432,
            "name": "testdb",
            "credentials": "secretsmanager",
            "app_username_secret": "arn:aws:secretsmanager:us-west-2:123:secret:username",
            "app_password_secret": "arn:aws:secretsmanager:us-west-2:123:secret:password",
            "migrate_username_secret": "arn:aws:secretsmanager:us-west-2:123:secret:migrate-username",
            "migrate_password_secret": "arn:aws:secretsmanager:us-west-2:123:secret:migrate-password",
        }
        ctx = ModuleContext(
            region="us-west-2",
            account_id="123456789",
            environment="staging",
            app_name="testapp",
        )

        output = module.collect(app_config, env_config, ctx)

        # Check environment variables
        env_names = {e.name for e in output.environment}
        assert "DB_HOST" in env_names
        assert "DB_PORT" in env_names
        assert "DB_NAME" in env_names

        # Check secrets
        secret_names = {s.name for s in output.secrets}
        assert "DB_USERNAME" in secret_names
        assert "DB_PASSWORD" in secret_names


class TestCacheModule:
    """Tests for CacheModule."""

    def test_validate_redis(self):
        """Test validation with valid redis config."""
        module = CacheModule()
        app_config = {"type": "redis"}
        env_config = {"url": "redis://localhost:6379"}

        errors = module.validate(app_config, env_config)

        assert len(errors) == 0

    def test_validate_missing_url(self):
        """Test validation fails when URL missing."""
        module = CacheModule()
        app_config = {"type": "redis"}
        env_config = {"other_key": "value"}  # Section present but missing url

        errors = module.validate(app_config, env_config)

        assert any("url" in e for e in errors)

    def test_collect(self):
        """Test collecting cache config."""
        module = CacheModule()
        app_config = {"type": "redis"}
        env_config = {"url": "redis://localhost:6379"}
        ctx = ModuleContext(
            region="us-west-2",
            account_id="123456789",
            environment="staging",
            app_name="testapp",
        )

        output = module.collect(app_config, env_config, ctx)

        assert len(output.environment) == 1
        assert output.environment[0].name == "REDIS_URL"
        assert output.environment[0].value == "redis://localhost:6379"


class TestStorageModule:
    """Tests for StorageModule."""

    def test_validate_s3_single_bucket(self):
        """Test validation with single S3 bucket."""
        module = StorageModule()
        app_config = {"type": "s3", "buckets": ["media"]}
        env_config = {"media_bucket": "my-media-bucket"}

        errors = module.validate(app_config, env_config)

        assert len(errors) == 0

    def test_validate_s3_multiple_buckets(self):
        """Test validation with multiple S3 buckets."""
        module = StorageModule()
        app_config = {"type": "s3", "buckets": ["originals", "media"]}
        env_config = {
            "originals_bucket": "my-originals-bucket",
            "media_bucket": "my-media-bucket",
        }

        errors = module.validate(app_config, env_config)

        assert len(errors) == 0

    def test_validate_missing_bucket(self):
        """Test validation fails when bucket config missing."""
        module = StorageModule()
        app_config = {"type": "s3", "buckets": ["originals", "media"]}
        env_config = {"media_bucket": "my-media-bucket"}

        errors = module.validate(app_config, env_config)

        assert any("originals_bucket" in e for e in errors)

    def test_collect_multiple_buckets(self):
        """Test collecting storage config with multiple buckets."""
        module = StorageModule()
        app_config = {"type": "s3", "buckets": ["originals", "media"]}
        env_config = {
            "originals_bucket": "my-originals-bucket",
            "media_bucket": "my-media-bucket",
        }
        ctx = ModuleContext(
            region="us-west-2",
            account_id="123456789",
            environment="staging",
            app_name="testapp",
        )

        output = module.collect(app_config, env_config, ctx)

        env_names = {e.name: e.value for e in output.environment}
        assert env_names["S3_ORIGINALS_BUCKET"] == "my-originals-bucket"
        assert env_names["S3_MEDIA_BUCKET"] == "my-media-bucket"


class TestSecretsModule:
    """Tests for SecretsModule."""

    def test_normalize_secret_name(self):
        """Test secret name normalization."""
        assert normalize_secret_name("SECRET_KEY") == "secret-key"
        assert normalize_secret_name("SIGNED_URL_SECRET") == "signed-url-secret"
        assert normalize_secret_name("DATACITE_PASSWORD") == "datacite-password"

    def test_validate_ssm(self):
        """Test validation with valid SSM config."""
        module = SecretsModule()
        app_config = {"names": ["SECRET_KEY", "SIGNED_URL_SECRET"]}
        env_config = {
            "provider": "ssm",
            "path_prefix": "/app/staging",
        }

        errors = module.validate(app_config, env_config)

        assert len(errors) == 0

    def test_validate_missing_path_prefix(self):
        """Test validation fails when path_prefix missing."""
        module = SecretsModule()
        app_config = {"names": ["SECRET_KEY"]}
        env_config = {"provider": "ssm"}

        errors = module.validate(app_config, env_config)

        assert any("path_prefix" in e for e in errors)

    def test_collect(self):
        """Test collecting secrets."""
        module = SecretsModule()
        app_config = {"names": ["SECRET_KEY", "SIGNED_URL_SECRET"]}
        env_config = {
            "provider": "ssm",
            "path_prefix": "/app/staging",
        }
        ctx = ModuleContext(
            region="us-west-2",
            account_id="123456789",
            environment="staging",
            app_name="testapp",
        )

        output = module.collect(app_config, env_config, ctx)

        assert len(output.secrets) == 2
        secret_map = {s.name: s.value_from for s in output.secrets}
        assert "SECRET_KEY" in secret_map
        assert "SIGNED_URL_SECRET" in secret_map
        assert "/app/staging/secret-key" in secret_map["SECRET_KEY"]
        assert "/app/staging/signed-url-secret" in secret_map["SIGNED_URL_SECRET"]


class TestModuleRegistry:
    """Tests for ModuleRegistry."""

    def test_validate_all_passes(self):
        """Test validation passes with valid config."""
        app_config = {
            "database": {"type": "postgresql"},
            "cache": {"type": "redis"},
        }
        env_config = {
            "database": {
                "host": "db.example.com",
                "port": "5432",
                "name": "testdb",
                "credentials": "secretsmanager",
                "app_username_secret": "arn:...",
                "app_password_secret": "arn:...",
                "migrate_username_secret": "arn:...",
                "migrate_password_secret": "arn:...",
            },
            "cache": {"url": "redis://localhost:6379"},
        }

        errors = ModuleRegistry.validate_all(app_config, env_config)

        assert len(errors) == 0

    def test_validate_all_fails(self):
        """Test validation fails with missing config."""
        app_config = {
            "database": {"type": "postgresql"},
        }
        env_config = {}

        errors = ModuleRegistry.validate_all(app_config, env_config)

        assert len(errors) > 0

    def test_collect_all(self):
        """Test collecting from all modules."""
        app_config = {
            "database": {"type": "postgresql"},
            "cache": {"type": "redis"},
        }
        env_config = {
            "database": {
                "host": "db.example.com",
                "port": 5432,
                "name": "testdb",
                "credentials": "secretsmanager",
                "app_username_secret": "arn:aws:secretsmanager:us-west-2:123:secret:username",
                "app_password_secret": "arn:aws:secretsmanager:us-west-2:123:secret:password",
                "migrate_username_secret": "arn:aws:secretsmanager:us-west-2:123:secret:migrate-username",
                "migrate_password_secret": "arn:aws:secretsmanager:us-west-2:123:secret:migrate-password",
            },
            "cache": {"url": "redis://localhost:6379"},
        }
        ctx = ModuleContext(
            region="us-west-2",
            account_id="123456789",
            environment="staging",
            app_name="testapp",
        )

        output = ModuleRegistry.collect_all(app_config, env_config, ctx)

        env_names = {e.name for e in output.environment}
        assert "DB_HOST" in env_names
        assert "DB_PORT" in env_names
        assert "REDIS_URL" in env_names

        secret_names = {s.name for s in output.secrets}
        assert "DB_USERNAME" in secret_names
        assert "DB_PASSWORD" in secret_names


class TestServiceUrlResolution:
    """Tests for service URL resolution."""

    def test_resolve_service_url(self):
        """Test resolving a service URL reference."""
        env_vars = {
            "API_BASE_URL": "${services.api.url}",
            "OTHER_VAR": "static_value",
        }
        services = {
            "api": {"path_pattern": "/api/*"},
        }

        resolved = resolve_service_urls(env_vars, services, "test.example.com", None)

        assert resolved["API_BASE_URL"] == "https://test.example.com/api"
        assert resolved["OTHER_VAR"] == "static_value"

    def test_resolve_service_url_no_domain(self):
        """Test that service URL is not resolved without domain."""
        env_vars = {"API_BASE_URL": "${services.api.url}"}
        services = {"api": {"path_pattern": "/api/*"}}

        resolved = resolve_service_urls(env_vars, services, None, None)

        # Should remain unresolved
        assert resolved["API_BASE_URL"] == "${services.api.url}"

    def test_resolve_service_url_no_path_pattern(self):
        """Test that service URL is not resolved without path_pattern."""
        env_vars = {"WEB_URL": "${services.web.url}"}
        services = {"web": {"port": 8000}}  # No path_pattern

        resolved = resolve_service_urls(env_vars, services, "test.example.com", None)

        # Should remain unresolved
        assert resolved["WEB_URL"] == "${services.web.url}"

    def test_resolve_internal_service_url(self):
        """Test resolving an internal service URL reference."""
        env_vars = {
            "DJANGO_URL": "${services.web.internal_url}",
            "OTHER_VAR": "static_value",
        }
        services = {
            "web": {"port": 8000},
        }

        resolved = resolve_service_urls(
            env_vars,
            services,
            "test.example.com",
            service_discovery_namespace="myapp-staging.local",
        )

        assert resolved["DJANGO_URL"] == "http://web.myapp-staging.local:8000"
        assert resolved["OTHER_VAR"] == "static_value"

    def test_resolve_internal_service_url_no_namespace(self):
        """Test that internal URL is not resolved without namespace."""
        env_vars = {"DJANGO_URL": "${services.web.internal_url}"}
        services = {"web": {"port": 8000}}

        resolved = resolve_service_urls(env_vars, services, "test.example.com", None)

        # Should remain unresolved
        assert resolved["DJANGO_URL"] == "${services.web.internal_url}"

    def test_resolve_internal_service_url_no_port(self):
        """Test that internal URL is not resolved without port."""
        env_vars = {"WORKER_URL": "${services.celery.internal_url}"}
        services = {"celery": {"command": ["celery", "-A", "config", "worker"]}}  # No port

        resolved = resolve_service_urls(
            env_vars,
            services,
            "test.example.com",
            service_discovery_namespace="myapp-staging.local",
        )

        # Should remain unresolved since service has no port
        assert resolved["WORKER_URL"] == "${services.celery.internal_url}"

    def test_resolve_both_internal_and_external_urls(self):
        """Test resolving both internal and external URLs in the same env vars."""
        env_vars = {
            "DJANGO_URL": "${services.web.internal_url}",
            "API_BASE_URL": "${services.api.url}",
        }
        services = {
            "web": {"port": 8000},
            "api": {"port": 8080, "path_pattern": "/api/*"},
        }

        resolved = resolve_service_urls(
            env_vars,
            services,
            "test.example.com",
            service_discovery_namespace="myapp-staging.local",
        )

        assert resolved["DJANGO_URL"] == "http://web.myapp-staging.local:8000"
        assert resolved["API_BASE_URL"] == "https://test.example.com/api"


class TestCheckModules:
    """Tests for the check_modules preflight step."""

    def test_check_modules_passes(self):
        """Test check_modules passes with valid config."""
        from deployer.deploy.preflight import check_modules

        deploy_config = DeployConfig.from_dict(
            {
                "application": {"name": "testapp"},
                "database": {"type": "postgresql"},
            }
        )
        env_config = {
            "database": {
                "host": "db.example.com",
                "port": "5432",
                "name": "testdb",
                "credentials": "secretsmanager",
                "app_username_secret": "arn:...",
                "app_password_secret": "arn:...",
                "migrate_username_secret": "arn:...",
                "migrate_password_secret": "arn:...",
            },
        }

        # Should not raise
        check_modules(deploy_config, env_config)

    def test_check_modules_fails(self):
        """Test check_modules fails with invalid config."""
        from deployer.deploy.preflight import PreflightError, check_modules

        deploy_config = DeployConfig.from_dict(
            {
                "application": {"name": "testapp"},
                "database": {"type": "postgresql"},
            }
        )
        env_config = {}  # Missing database section

        with pytest.raises(PreflightError, match="module validation failed"):
            check_modules(deploy_config, env_config)
