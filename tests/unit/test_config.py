"""Tests for deployer.config package."""

import pytest

from deployer.config import (
    AuditConfig,
    DeployConfig,
    ImageConfig,
    ServiceConfig,
    TfvarsService,
    get_compose_services,
    parse_deploy_config,
    parse_deploy_toml,
    parse_docker_compose,
    parse_tfvars,
)


class TestParseDeployToml:
    """Tests for parse_deploy_toml function."""

    def test_parse_valid_file(self, sample_deploy_toml):
        """Test parsing a valid deploy.toml file."""
        result = parse_deploy_toml(sample_deploy_toml)

        assert "application" in result
        assert result["application"]["name"] == "testapp"
        assert "services" in result
        assert "images" in result

    def test_parse_nonexistent_file(self, tmp_path):
        """Test parsing a non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            parse_deploy_toml(tmp_path / "nonexistent.toml")


class TestDeployConfigServices:
    """Tests for DeployConfig.services (replaces get_deploy_services)."""

    def test_extract_services(self, tmp_path):
        """Test extracting services from deploy config."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[services.web]
image = "web"
port = 8000

[services.worker]
image = "worker"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert "web" in config.services
        assert config.services["web"].image == "web"
        assert config.services["web"].port == 8000
        assert "worker" in config.services

    def test_empty_services(self, tmp_path):
        """Test with no services."""
        (tmp_path / "deploy.toml").write_text('[application]\nname = "test"')
        config = parse_deploy_config(tmp_path / "deploy.toml")
        assert config.services == {}


class TestDeployConfigImages:
    """Tests for DeployConfig.images (replaces get_deploy_images)."""

    def test_extract_images(self, tmp_path):
        """Test extracting images from deploy config."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[images.web]
context = "."
dockerfile = "Dockerfile.web"

[images.base]
context = "./base"
push = false
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert "web" in config.images
        assert config.images["web"].context == "."
        assert config.images["web"].dockerfile == "Dockerfile.web"
        assert config.images["web"].push is True

        assert config.images["base"].push is False

    def test_default_dockerfile(self, tmp_path):
        """Test default Dockerfile value."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[images.app]
context = "."
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        assert config.images["app"].dockerfile == "Dockerfile"


class TestDeployConfigEnvVars:
    """Tests for DeployConfig.get_all_env_var_names (replaces get_deploy_env_vars)."""

    def test_extract_env_vars(self, tmp_path):
        """Test extracting environment variables."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[environment]
DEBUG = "false"

[environment.staging]
DEBUG = "true"

[secrets]
API_KEY = "ssm:/key"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "DEBUG" in result
        assert "API_KEY" in result

    def test_module_injected_database_vars(self, tmp_path):
        """Test that database module vars are included."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[database]
type = "postgresql"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "DB_HOST" in result
        assert "DB_PORT" in result
        assert "DB_NAME" in result
        assert "DB_USERNAME" in result
        assert "DB_PASSWORD" in result

    def test_module_injected_cache_vars(self, tmp_path):
        """Test that cache module vars are included."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[cache]
type = "redis"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "REDIS_URL" in result

    def test_module_injected_storage_vars(self, tmp_path):
        """Test that storage module vars are included."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[storage]
type = "s3"
buckets = ["media", "originals"]
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "S3_MEDIA_BUCKET" in result
        assert "S3_MEDIA_BUCKET_REGION" in result
        assert "S3_ORIGINALS_BUCKET" in result
        assert "S3_ORIGINALS_BUCKET_REGION" in result

    def test_module_injected_cdn_vars(self, tmp_path):
        """Test that CDN module vars are included."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[cdn]
type = "cloudfront"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "CLOUDFRONT_DOMAIN" in result
        assert "CLOUDFRONT_KEY_ID" in result
        assert "CLOUDFRONT_PRIVATE_KEY" in result

    def test_module_injected_secrets_vars(self, tmp_path):
        """Test that secrets module vars are included."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[secrets]
names = ["SECRET_KEY", "API_TOKEN"]
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "SECRET_KEY" in result
        assert "API_TOKEN" in result

    def test_all_modules_combined(self, tmp_path):
        """Test that all modules work together."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[environment]
CUSTOM_VAR = "value"

[database]
type = "postgresql"

[cache]
type = "redis"

[storage]
type = "s3"
buckets = ["media"]

[cdn]
type = "cloudfront"

[secrets]
names = ["SECRET_KEY"]
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        # Explicit env var
        assert "CUSTOM_VAR" in result
        # Database
        assert "DB_HOST" in result
        # Cache
        assert "REDIS_URL" in result
        # Storage
        assert "S3_MEDIA_BUCKET" in result
        # CDN
        assert "CLOUDFRONT_DOMAIN" in result
        # Secrets
        assert "SECRET_KEY" in result


class TestDeployConfigAudit:
    """Tests for DeployConfig.audit (replaces get_audit_config)."""

    def test_extract_audit_config(self, tmp_path):
        """Test extracting audit configuration."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[audit]
ignore_services = ["db"]
service_mapping = { app = "web" }
ignore_env_vars = ["DEBUG"]
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        audit = config.audit

        assert "db" in audit.ignore_services
        assert audit.service_mapping["app"] == "web"
        assert "DEBUG" in audit.ignore_env_vars

    def test_empty_audit_config(self, tmp_path):
        """Test with no audit config."""
        (tmp_path / "deploy.toml").write_text('[application]\nname = "test"')
        config = parse_deploy_config(tmp_path / "deploy.toml")
        audit = config.audit
        assert audit.ignore_services == set()
        assert audit.service_mapping == {}


class TestParseDockerCompose:
    """Tests for parse_docker_compose function."""

    def test_parse_valid_file(self, sample_docker_compose):
        """Test parsing a valid docker-compose.yml."""
        result = parse_docker_compose(sample_docker_compose)

        assert "services" in result
        assert "testapp" in result["services"]

    def test_parse_nonexistent_file(self, tmp_path):
        """Test parsing a non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            parse_docker_compose(tmp_path / "nonexistent.yml")


class TestGetComposeServices:
    """Tests for get_compose_services function."""

    def test_extract_services_with_build(self):
        """Test extracting services with build contexts."""
        compose = {
            "services": {
                "web": {
                    "build": {"context": "./app", "dockerfile": "Dockerfile.web"},
                    "environment": ["PORT=8000"],
                },
                "db": {"image": "postgres:15"},
            }
        }

        result = get_compose_services(compose)

        assert result["web"]["has_build"] is True
        assert result["web"]["build_context"] == "./app"
        assert result["web"]["dockerfile"] == "Dockerfile.web"
        assert "PORT" in result["web"]["environment"]

        assert result["db"]["has_build"] is False

    def test_build_string_shorthand(self):
        """Test build context as string."""
        compose = {"services": {"app": {"build": "./src"}}}
        result = get_compose_services(compose)
        assert result["app"]["build_context"] == "./src"

    def test_env_as_dict(self):
        """Test environment as dictionary."""
        compose = {
            "services": {"app": {"build": ".", "environment": {"PORT": "8000", "DEBUG": "true"}}}
        }
        result = get_compose_services(compose)
        assert "PORT" in result["app"]["environment"]
        assert "DEBUG" in result["app"]["environment"]

    def test_profiles(self):
        """Test profiles extraction."""
        compose = {"services": {"tool": {"build": ".", "profiles": ["dev"]}}}
        result = get_compose_services(compose)
        assert result["tool"]["profiles"] == ["dev"]


class TestParseTfvars:
    """Tests for parse_tfvars function."""

    def test_parse_valid_file(self, sample_tfvars):
        """Test parsing a valid terraform.tfvars."""
        result = parse_tfvars(sample_tfvars)

        assert "web" in result
        assert result["web"].cpu == 256
        assert result["web"].memory == 512
        assert result["web"].replicas == 1
        assert result["web"].load_balanced is True
        assert result["web"].port == 8000

        assert "celery" in result
        assert result["celery"].cpu == 512
        assert result["celery"].replicas == 2

    def test_parse_nonexistent_file(self, tmp_path):
        """Test parsing a non-existent file returns empty dict."""
        result = parse_tfvars(tmp_path / "nonexistent.tfvars")
        assert result == {}

    def test_parse_file_without_services(self, tmp_path):
        """Test parsing file without services block."""
        tfvars = tmp_path / "test.tfvars"
        tfvars.write_text('project = "test"\n')
        result = parse_tfvars(tfvars)
        assert result == {}


class TestTfvarsService:
    """Tests for TfvarsService dataclass."""

    def test_defaults(self):
        """Test default values."""
        svc = TfvarsService(cpu=256, memory=512, replicas=1)
        assert svc.load_balanced is False
        assert svc.port is None
        assert svc.health_check_path == "/"
        assert svc.path_pattern is None

    def test_all_fields(self):
        """Test with all fields specified."""
        svc = TfvarsService(
            cpu=1024,
            memory=2048,
            replicas=3,
            load_balanced=True,
            port=8080,
            health_check_path="/health",
            path_pattern="/api/*",
        )
        assert svc.cpu == 1024
        assert svc.memory == 2048
        assert svc.replicas == 3
        assert svc.load_balanced is True
        assert svc.port == 8080
        assert svc.health_check_path == "/health"
        assert svc.path_pattern == "/api/*"


class TestImageConfig:
    """Tests for ImageConfig dataclass."""

    def test_get_target_string(self):
        """Test get_target with string value."""
        img = ImageConfig.from_dict("web", {"context": ".", "target": "production"})
        assert img.get_target("staging") == "production"
        assert img.get_target("production") == "production"

    def test_get_target_dict(self):
        """Test get_target with environment-specific dict."""
        img = ImageConfig.from_dict(
            "web",
            {"context": ".", "target": {"staging": "development", "production": "production"}},
        )
        assert img.get_target("staging") == "development"
        assert img.get_target("production") == "production"

    def test_get_target_none(self):
        """Test get_target when not specified."""
        img = ImageConfig.from_dict("web", {"context": "."})
        assert img.get_target("staging") is None

    def test_get_build_args_base(self):
        """Test get_build_args with base args only."""
        img = ImageConfig.from_dict(
            "web", {"context": ".", "build_args": {"PYTHON_VERSION": "3.12", "DEBUG": "0"}}
        )
        args = img.get_build_args("staging")
        assert args == {"PYTHON_VERSION": "3.12", "DEBUG": "0"}

    def test_get_build_args_with_env_override(self):
        """Test get_build_args with environment-specific overrides."""
        img = ImageConfig.from_dict(
            "web",
            {
                "context": ".",
                "build_args": {
                    "PYTHON_VERSION": "3.12",
                    "staging": {"DEBUG": "1"},
                    "production": {"DEBUG": "0"},
                },
            },
        )
        staging_args = img.get_build_args("staging")
        assert staging_args == {"PYTHON_VERSION": "3.12", "DEBUG": "1"}

        prod_args = img.get_build_args("production")
        assert prod_args == {"PYTHON_VERSION": "3.12", "DEBUG": "0"}
