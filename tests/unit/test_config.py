"""Tests for deployer.config package."""

import pytest

from deployer.config import (
    TfvarsService,
    get_audit_config,
    get_compose_services,
    get_deploy_env_vars,
    get_deploy_images,
    get_deploy_services,
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


class TestGetDeployServices:
    """Tests for get_deploy_services function."""

    def test_extract_services(self):
        """Test extracting services from deploy config."""
        deploy = {
            "services": {
                "web": {"image": "web", "port": 8000},
                "worker": {"image": "worker"},
            }
        }

        result = get_deploy_services(deploy)

        assert "web" in result
        assert result["web"]["image"] == "web"
        assert result["web"]["port"] == 8000
        assert "worker" in result

    def test_empty_services(self):
        """Test with no services."""
        result = get_deploy_services({})
        assert result == {}


class TestGetDeployImages:
    """Tests for get_deploy_images function."""

    def test_extract_images(self):
        """Test extracting images from deploy config."""
        deploy = {
            "images": {
                "web": {"context": ".", "dockerfile": "Dockerfile.web"},
                "base": {"context": "./base", "push": False},
            }
        }

        result = get_deploy_images(deploy)

        assert "web" in result
        assert result["web"]["context"] == "."
        assert result["web"]["dockerfile"] == "Dockerfile.web"
        assert result["web"]["push"] is True

        assert result["base"]["push"] is False

    def test_default_dockerfile(self):
        """Test default Dockerfile value."""
        deploy = {"images": {"app": {"context": "."}}}
        result = get_deploy_images(deploy)
        assert result["app"]["dockerfile"] == "Dockerfile"


class TestGetDeployEnvVars:
    """Tests for get_deploy_env_vars function."""

    def test_extract_env_vars(self):
        """Test extracting environment variables."""
        deploy = {
            "environment": {
                "DEBUG": "false",
                "staging": {"DEBUG": "true"},
            },
            "secrets": {"API_KEY": "ssm:/key"},
        }

        result = get_deploy_env_vars(deploy)

        assert "DEBUG" in result
        assert "API_KEY" in result

    def test_module_injected_database_vars(self):
        """Test that database module vars are included."""
        deploy = {"database": {"type": "postgresql"}}
        result = get_deploy_env_vars(deploy)

        assert "DB_HOST" in result
        assert "DB_PORT" in result
        assert "DB_NAME" in result
        assert "DB_USERNAME" in result
        assert "DB_PASSWORD" in result

    def test_module_injected_cache_vars(self):
        """Test that cache module vars are included."""
        deploy = {"cache": {"type": "redis"}}
        result = get_deploy_env_vars(deploy)

        assert "REDIS_URL" in result

    def test_module_injected_storage_vars(self):
        """Test that storage module vars are included."""
        deploy = {"storage": {"type": "s3", "buckets": ["media", "originals"]}}
        result = get_deploy_env_vars(deploy)

        assert "S3_MEDIA_BUCKET" in result
        assert "S3_MEDIA_BUCKET_REGION" in result
        assert "S3_ORIGINALS_BUCKET" in result
        assert "S3_ORIGINALS_BUCKET_REGION" in result

    def test_module_injected_cdn_vars(self):
        """Test that CDN module vars are included."""
        deploy = {"cdn": {"type": "cloudfront"}}
        result = get_deploy_env_vars(deploy)

        assert "CLOUDFRONT_DOMAIN" in result
        assert "CLOUDFRONT_KEY_ID" in result
        assert "CLOUDFRONT_PRIVATE_KEY" in result

    def test_module_injected_secrets_vars(self):
        """Test that secrets module vars are included."""
        deploy = {"secrets": {"names": ["SECRET_KEY", "API_TOKEN"]}}
        result = get_deploy_env_vars(deploy)

        assert "SECRET_KEY" in result
        assert "API_TOKEN" in result

    def test_all_modules_combined(self):
        """Test that all modules work together."""
        deploy = {
            "environment": {"CUSTOM_VAR": "value"},
            "database": {"type": "postgresql"},
            "cache": {"type": "redis"},
            "storage": {"type": "s3", "buckets": ["media"]},
            "cdn": {"type": "cloudfront"},
            "secrets": {"names": ["SECRET_KEY"]},
        }
        result = get_deploy_env_vars(deploy)

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


class TestGetAuditConfig:
    """Tests for get_audit_config function."""

    def test_extract_audit_config(self):
        """Test extracting audit configuration."""
        deploy = {
            "audit": {
                "ignore_services": ["db"],
                "service_mapping": {"app": "web"},
                "ignore_env_vars": ["DEBUG"],
            }
        }

        result = get_audit_config(deploy)

        assert "db" in result["ignore_services"]
        assert result["service_mapping"]["app"] == "web"
        assert "DEBUG" in result["ignore_env_vars"]

    def test_empty_audit_config(self):
        """Test with no audit config."""
        result = get_audit_config({})
        assert result["ignore_services"] == set()
        assert result["service_mapping"] == {}


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
            "services": {
                "app": {"build": ".", "environment": {"PORT": "8000", "DEBUG": "true"}}
            }
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
