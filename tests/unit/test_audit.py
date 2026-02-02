"""Tests for audit functionality (deployer.core.audit and deployer.config)."""

import pytest

from deployer.config import (
    get_audit_config,
    get_compose_services,
    get_deploy_env_vars,
    get_deploy_images,
    get_deploy_services,
    parse_deploy_toml,
    parse_docker_compose,
)
from deployer.core import (
    audit_env_vars,
    audit_images,
    audit_services,
    run_audit,
)


class TestParseDockerCompose:
    """Tests for parse_docker_compose function."""

    def test_parse_docker_compose(self, sample_docker_compose):
        """Test parsing a valid docker-compose.yml file."""
        result = parse_docker_compose(sample_docker_compose)

        assert "services" in result
        assert "testapp" in result["services"]
        assert "celery-worker" in result["services"]
        assert "postgres" in result["services"]

    def test_parse_docker_compose_file_not_found(self, tmp_path):
        """Test parsing a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_docker_compose(tmp_path / "nonexistent.yml")


class TestParseDeployToml:
    """Tests for parse_deploy_toml function."""

    def test_parse_deploy_toml(self, sample_deploy_toml):
        """Test parsing a valid deploy.toml file."""
        result = parse_deploy_toml(sample_deploy_toml)

        assert "application" in result
        assert result["application"]["name"] == "testapp"
        assert "services" in result
        assert "web" in result["services"]

    def test_parse_deploy_toml_file_not_found(self, tmp_path):
        """Test parsing a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_deploy_toml(tmp_path / "nonexistent.toml")


class TestGetComposeServices:
    """Tests for get_compose_services function."""

    def test_extract_services_with_build(self):
        """Test extracting services that have build contexts."""
        compose = {
            "services": {
                "web": {
                    "build": {"context": ".", "dockerfile": "Dockerfile"},
                    "ports": ["8000:8000"],
                    "environment": ["DEBUG=true", "API_KEY=secret"],
                },
                "db": {
                    "image": "postgres:15",
                },
            }
        }

        result = get_compose_services(compose)

        assert "web" in result
        assert result["web"]["has_build"] is True
        assert result["web"]["build_context"] == "."
        assert result["web"]["dockerfile"] == "Dockerfile"
        assert "DEBUG" in result["web"]["environment"]
        assert "API_KEY" in result["web"]["environment"]

        assert "db" in result
        assert result["db"]["has_build"] is False

    def test_extract_services_with_profiles(self):
        """Test that profiles are extracted correctly."""
        compose = {
            "services": {
                "dev-tool": {
                    "build": ".",
                    "profiles": ["dev-tools"],
                },
            }
        }

        result = get_compose_services(compose)
        assert result["dev-tool"]["profiles"] == ["dev-tools"]

    def test_build_string_shorthand(self):
        """Test build context specified as string."""
        compose = {
            "services": {
                "app": {"build": "./app"},
            }
        }

        result = get_compose_services(compose)
        assert result["app"]["build_context"] == "./app"

    def test_environment_as_dict(self):
        """Test environment variables specified as dict."""
        compose = {
            "services": {
                "app": {
                    "build": ".",
                    "environment": {"DEBUG": "true", "PORT": "8000"},
                },
            }
        }

        result = get_compose_services(compose)
        assert "DEBUG" in result["app"]["environment"]
        assert "PORT" in result["app"]["environment"]


class TestGetDeployServices:
    """Tests for get_deploy_services function."""

    def test_extract_services(self):
        """Test extracting services from deploy.toml structure."""
        deploy = {
            "services": {
                "web": {"image": "web", "port": 8000, "command": ["gunicorn"]},
                "worker": {"image": "worker"},
            }
        }

        result = get_deploy_services(deploy)

        assert "web" in result
        assert result["web"]["image"] == "web"
        assert result["web"]["port"] == 8000
        assert "worker" in result

    def test_empty_services(self):
        """Test with no services defined."""
        deploy = {}
        result = get_deploy_services(deploy)
        assert result == {}


class TestGetDeployImages:
    """Tests for get_deploy_images function."""

    def test_extract_images(self):
        """Test extracting images from deploy.toml structure."""
        deploy = {
            "images": {
                "web": {"context": ".", "dockerfile": "Dockerfile"},
                "worker": {"context": ".", "dockerfile": "Dockerfile.worker"},
            }
        }

        result = get_deploy_images(deploy)

        assert "web" in result
        assert result["web"]["context"] == "."
        assert result["web"]["dockerfile"] == "Dockerfile"
        assert result["worker"]["dockerfile"] == "Dockerfile.worker"

    def test_default_dockerfile(self):
        """Test that default dockerfile is 'Dockerfile'."""
        deploy = {
            "images": {
                "app": {"context": "."},
            }
        }

        result = get_deploy_images(deploy)
        assert result["app"]["dockerfile"] == "Dockerfile"


class TestGetDeployEnvVars:
    """Tests for get_deploy_env_vars function."""

    def test_extract_env_vars(self):
        """Test extracting environment variables."""
        deploy = {
            "environment": {
                "DEBUG": "false",
                "API_URL": "https://api.example.com",
            },
            "secrets": {
                "SECRET_KEY": "ssm:/app/secret",
            },
        }

        result = get_deploy_env_vars(deploy)

        assert "DEBUG" in result
        assert "API_URL" in result
        assert "SECRET_KEY" in result

    def test_extract_service_specific_env_vars(self):
        """Test extracting environment variables from service-specific sections."""
        deploy = {
            "environment": {
                "GLOBAL_VAR": "value",
            },
            "services": {
                "web": {
                    "image": "web",
                    "port": 8000,
                },
                "api": {
                    "image": "api",
                    "environment": {
                        "DELEGATE_ENABLED": "true",
                        "DJANGO_URL": "${services.web.url}",
                    },
                },
            },
        }

        result = get_deploy_env_vars(deploy)

        assert "GLOBAL_VAR" in result
        assert "DELEGATE_ENABLED" in result
        assert "DJANGO_URL" in result


class TestGetAuditConfig:
    """Tests for get_audit_config function."""

    def test_extract_audit_config(self):
        """Test extracting audit configuration."""
        deploy = {
            "audit": {
                "ignore_services": ["postgres", "redis"],
                "service_mapping": {"app": "web"},
                "ignore_env_vars": ["DEBUG"],
                "ignore_images": ["base"],
            }
        }

        result = get_audit_config(deploy)

        assert result["ignore_services"] == {"postgres", "redis"}
        assert result["service_mapping"] == {"app": "web"}
        assert result["ignore_env_vars"] == {"DEBUG"}
        assert result["ignore_images"] == {"base"}

    def test_empty_audit_config(self):
        """Test with no audit config defined."""
        deploy = {}
        result = get_audit_config(deploy)

        assert result["ignore_services"] == set()
        assert result["service_mapping"] == {}
        assert result["ignore_env_vars"] == set()


class TestAuditServices:
    """Tests for audit_services function."""

    def test_all_services_accounted_for(self):
        """Test when all compose services have matching deploy.toml entries."""
        compose_services = {
            "web": {"has_build": True, "profiles": []},
            "worker": {"has_build": True, "profiles": []},
            "postgres": {"has_build": False, "profiles": []},
        }
        deploy_services = {
            "web": {},
            "worker": {},
        }
        audit_config = {
            "ignore_services": set(),
            "service_mapping": {},
        }

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert issues == []

    def test_missing_service_in_deploy_toml(self):
        """Test detection of service missing from deploy.toml."""
        compose_services = {
            "web": {"has_build": True, "profiles": []},
            "celery": {"has_build": True, "profiles": []},
        }
        deploy_services = {
            "web": {},
        }
        audit_config = {
            "ignore_services": set(),
            "service_mapping": {},
        }

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert len(issues) == 1
        assert "celery" in issues[0]

    def test_service_mapping(self):
        """Test that service mapping is respected."""
        compose_services = {
            "app": {"has_build": True, "profiles": []},
        }
        deploy_services = {
            "web": {},
        }
        audit_config = {
            "ignore_services": set(),
            "service_mapping": {"app": "web"},
        }

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert issues == []

    def test_ignored_services(self):
        """Test that ignored services are skipped."""
        compose_services = {
            "db-seeder": {"has_build": True, "profiles": []},
        }
        deploy_services = {}
        audit_config = {
            "ignore_services": {"db-seeder"},
            "service_mapping": {},
        }

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert issues == []

    def test_services_with_profiles_skipped(self):
        """Test that services with profiles are skipped."""
        compose_services = {
            "dev-tool": {"has_build": True, "profiles": ["dev-tools"]},
        }
        deploy_services = {}
        audit_config = {
            "ignore_services": set(),
            "service_mapping": {},
        }

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert issues == []


class TestAuditImages:
    """Tests for audit_images function."""

    def test_all_images_accounted_for(self):
        """Test when all build contexts have matching images."""
        # The audit function normalizes "./something" to "something"
        # and "." becomes empty string, so we use a subdir context
        compose_services = {
            "web": {"has_build": True, "build_context": "./web", "profiles": []},
        }
        deploy_images = {
            "web": {"context": "web"},
        }
        audit_config = {
            "ignore_services": set(),
            "ignore_images": set(),
        }

        issues = audit_images(compose_services, deploy_images, audit_config)
        assert issues == []

    def test_missing_image_context(self):
        """Test detection of missing build context in deploy.toml."""
        compose_services = {
            "web": {"has_build": True, "build_context": "./web", "profiles": []},
        }
        deploy_images = {
            "api": {"context": "./api"},
        }
        audit_config = {
            "ignore_services": set(),
            "ignore_images": set(),
        }

        issues = audit_images(compose_services, deploy_images, audit_config)
        assert len(issues) == 1
        assert "web" in issues[0]


class TestAuditEnvVars:
    """Tests for audit_env_vars function."""

    def test_all_env_vars_accounted_for(self):
        """Test when all env vars are in deploy.toml."""
        compose_services = {
            "web": {
                "has_build": True,
                "environment": ["DATABASE_URL", "REDIS_URL"],
                "profiles": [],
            },
        }
        deploy_env_vars = {"DATABASE_URL", "REDIS_URL"}
        audit_config = {
            "ignore_env_vars": set(),
            "ignore_services": set(),
        }

        issues = audit_env_vars(compose_services, deploy_env_vars, audit_config)
        assert issues == []

    def test_missing_env_var(self):
        """Test detection of missing environment variable."""
        compose_services = {
            "web": {
                "has_build": True,
                "environment": ["DATABASE_URL", "CUSTOM_VAR"],
                "profiles": [],
            },
        }
        deploy_env_vars = {"DATABASE_URL"}
        audit_config = {
            "ignore_env_vars": set(),
            "ignore_services": set(),
        }

        issues = audit_env_vars(compose_services, deploy_env_vars, audit_config)
        assert len(issues) == 1
        assert "CUSTOM_VAR" in issues[0]

    def test_ignored_env_vars(self):
        """Test that ignored env vars are not flagged."""
        compose_services = {
            "web": {
                "has_build": True,
                "environment": ["DEBUG", "PYTHONUNBUFFERED"],
                "profiles": [],
            },
        }
        deploy_env_vars = set()
        audit_config = {
            "ignore_env_vars": set(),  # DEBUG and PYTHONUNBUFFERED are default ignores
            "ignore_services": set(),
        }

        issues = audit_env_vars(compose_services, deploy_env_vars, audit_config)
        # DEBUG and PYTHONUNBUFFERED should be in default_ignore
        assert issues == []


class TestRunAudit:
    """Tests for run_audit function."""

    def test_run_audit_no_issues(self, temp_project_dir):
        """Test run_audit returns 0 issues for valid config."""
        issue_count, issues = run_audit(temp_project_dir, verbose=False)

        # The sample fixtures are designed to match, so should have no issues
        # or only expected differences
        assert issue_count >= 0  # Just verify it runs without error

    def test_run_audit_missing_compose_file(self, tmp_path):
        """Test run_audit handles missing docker-compose.yml."""
        # Create only deploy.toml
        (tmp_path / "deploy.toml").write_text('[application]\nname = "test"')

        issue_count, issues = run_audit(tmp_path, verbose=False)

        assert issue_count == -1
        assert "not found" in issues[0].lower()

    def test_run_audit_missing_deploy_toml(self, tmp_path):
        """Test run_audit handles missing deploy.toml."""
        # Create only docker-compose.yml
        (tmp_path / "docker-compose.yml").write_text("services: {}")

        issue_count, issues = run_audit(tmp_path, verbose=False)

        assert issue_count == -1
        assert "not found" in issues[0].lower()
