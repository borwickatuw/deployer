"""Tests for deployer.core package."""

import string

import pytest

from deployer.config import AuditConfig
from deployer.core.audit import (
    audit_env_vars,
    audit_images,
    audit_services,
    run_audit,
)
from deployer.core.cognito import (
    format_user,
    format_welcome_message,
    generate_temp_password,
)
from deployer.core.deploy import topological_sort


class TestTopologicalSort:
    """Tests for topological_sort function."""

    def test_no_dependencies(self):
        """Test sorting images with no dependencies."""
        images = {"a": {"context": "."}, "b": {"context": "."}}
        result = topological_sort(images)
        assert set(result) == {"a", "b"}

    def test_single_dependency(self):
        """Test that dependencies are built first."""
        images = {
            "app": {"context": ".", "depends_on": ["base"]},
            "base": {"context": "."},
        }
        result = topological_sort(images)
        assert result.index("base") < result.index("app")

    def test_chain_dependencies(self):
        """Test chain: c depends on b, b depends on a."""
        images = {
            "c": {"context": ".", "depends_on": ["b"]},
            "b": {"context": ".", "depends_on": ["a"]},
            "a": {"context": "."},
        }
        result = topological_sort(images)
        assert result.index("a") < result.index("b") < result.index("c")

    def test_circular_dependency(self):
        """Test circular dependency detection."""
        images = {
            "a": {"context": ".", "depends_on": ["b"]},
            "b": {"context": ".", "depends_on": ["a"]},
        }
        with pytest.raises(ValueError, match="[Cc]ircular"):
            topological_sort(images)

    def test_unknown_dependency(self):
        """Test unknown dependency detection."""
        images = {"a": {"context": ".", "depends_on": ["nonexistent"]}}
        with pytest.raises(ValueError, match="unknown"):
            topological_sort(images)

    def test_empty_images(self):
        """Test empty input."""
        assert topological_sort({}) == []


class TestGenerateTempPassword:
    """Tests for generate_temp_password function."""

    def test_length(self):
        """Test password length."""
        assert len(generate_temp_password(16)) == 16
        assert len(generate_temp_password(20)) == 20

    def test_has_uppercase(self):
        """Test password has uppercase."""
        for _ in range(10):
            pwd = generate_temp_password(16)
            assert any(c in string.ascii_uppercase for c in pwd)

    def test_has_lowercase(self):
        """Test password has lowercase."""
        for _ in range(10):
            pwd = generate_temp_password(16)
            assert any(c in string.ascii_lowercase for c in pwd)

    def test_has_digit(self):
        """Test password has digit."""
        for _ in range(10):
            pwd = generate_temp_password(16)
            assert any(c in string.digits for c in pwd)


class TestFormatWelcomeMessage:
    """Tests for format_welcome_message function."""

    def test_basic_message(self):
        """Test basic message formatting."""
        msg = format_welcome_message(
            environment="staging",
            email="user@example.com",
            password="Pass123",
            url="https://staging.example.com",
            is_temporary=True,
        )
        assert "staging" in msg
        assert "user@example.com" in msg
        assert "Pass123" in msg
        assert "https://staging.example.com" in msg

    def test_without_url(self):
        """Test message without URL."""
        msg = format_welcome_message(
            environment="staging",
            email="user@example.com",
            password="Pass123",
            url=None,
            is_temporary=True,
        )
        assert "Login URL" not in msg


class TestFormatUser:
    """Tests for format_user function."""

    def test_format_user(self):
        """Test formatting a Cognito user."""
        user = {
            "Username": "alice",
            "Attributes": [{"Name": "email", "Value": "alice@example.com"}],
            "UserStatus": "CONFIRMED",
            "Enabled": True,
        }
        result = format_user(user)
        assert result["username"] == "alice"
        assert result["email"] == "alice@example.com"
        assert result["status"] == "CONFIRMED"
        assert result["enabled"] is True


class TestAuditServices:
    """Tests for audit_services function."""

    def test_all_accounted_for(self):
        """Test no issues when all services accounted for."""
        compose = {"web": {"has_build": True, "profiles": []}}
        deploy = {"web": {}}
        config = AuditConfig(ignore_services=set(), service_mapping={})
        assert audit_services(compose, deploy, config) == []

    def test_missing_service(self):
        """Test detection of missing service."""
        compose = {"web": {"has_build": True, "profiles": []}}
        deploy = {}
        config = AuditConfig(ignore_services=set(), service_mapping={})
        issues = audit_services(compose, deploy, config)
        assert len(issues) == 1
        assert "web" in issues[0]


class TestAuditImages:
    """Tests for audit_images function."""

    def test_all_accounted_for(self):
        """Test no issues when all images accounted for."""
        compose = {"web": {"has_build": True, "build_context": "web", "profiles": []}}
        images = {"web": {"context": "web"}}
        config = AuditConfig(ignore_services=set(), ignore_images=set())
        assert audit_images(compose, images, config) == []


class TestAuditEnvVars:
    """Tests for audit_env_vars function."""

    def test_all_accounted_for(self):
        """Test no issues when all env vars accounted for."""
        compose = {"web": {"has_build": True, "environment": ["DATABASE_URL"], "profiles": []}}
        env_vars = {"DATABASE_URL"}
        config = AuditConfig(ignore_env_vars=set(), ignore_services=set())
        assert audit_env_vars(compose, env_vars, config) == []


class TestRunAudit:
    """Tests for run_audit function."""

    def test_run_audit(self, temp_project_dir):
        """Test run_audit executes without error."""
        count, issues = run_audit(temp_project_dir, verbose=False)
        assert count >= 0

    def test_missing_files(self, tmp_path):
        """Test handling of missing files."""
        count, issues = run_audit(tmp_path, verbose=False)
        assert count == -1
        assert "not found" in issues[0].lower()


class TestGetSecretsFromConfig:
    """Tests for get_secrets_from_config function."""

    def test_legacy_format_with_environment_placeholder(self):
        """Test legacy ssm:/path format with ${environment} substitution."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {
            "secrets": {
                "SECRET_KEY": "ssm:/myapp/${environment}/secret-key",
                "API_KEY": "ssm:/shared/api-key",
            }
        }
        result = get_secrets_from_config(config, "staging", {})

        assert result == {
            "SECRET_KEY": "/myapp/staging/secret-key",
            "API_KEY": "/shared/api-key",
        }

    def test_module_format_with_names_list(self):
        """Test new module format with names = [...] and path_prefix."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {
            "secrets": {
                "names": ["SECRET_KEY", "SIGNED_URL_SECRET", "DATACITE_PASSWORD"],
            }
        }
        env_config = {
            "secrets": {
                "provider": "ssm",
                "path_prefix": "/myapp/staging",
            }
        }
        result = get_secrets_from_config(config, "staging", env_config)

        assert result == {
            "SECRET_KEY": "/myapp/staging/secret-key",
            "SIGNED_URL_SECRET": "/myapp/staging/signed-url-secret",
            "DATACITE_PASSWORD": "/myapp/staging/datacite-password",
        }

    def test_module_format_normalizes_path_prefix(self):
        """Test that path_prefix is normalized (adds leading /, removes trailing /)."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"path_prefix": "myapp/staging/"}}  # No leading /, has trailing /
        result = get_secrets_from_config(config, "staging", env_config)

        assert result == {"SECRET_KEY": "/myapp/staging/secret-key"}

    def test_module_format_without_secrets_config_returns_empty(self):
        """Test that module format returns empty dict if env_config has no secrets."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        result = get_secrets_from_config(config, "staging", {})

        assert result == {}

    def test_module_format_without_path_prefix_returns_empty(self):
        """Test that module format returns empty dict if path_prefix missing."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"provider": "ssm"}}  # No path_prefix
        result = get_secrets_from_config(config, "staging", env_config)

        assert result == {}

    def test_combined_legacy_and_module_format(self):
        """Test that both legacy and module formats work together."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {
            "secrets": {
                "names": ["SECRET_KEY"],
                "LEGACY_SECRET": "ssm:/legacy/path",
            }
        }
        env_config = {"secrets": {"path_prefix": "/app/staging"}}
        result = get_secrets_from_config(config, "staging", env_config)

        assert result == {
            "SECRET_KEY": "/app/staging/secret-key",
            "LEGACY_SECRET": "/legacy/path",
        }

    def test_no_secrets_section_returns_empty(self):
        """Test that missing secrets section returns empty dict."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {}
        result = get_secrets_from_config(config, "staging", {})

        assert result == {}

    def test_empty_names_list_returns_empty(self):
        """Test that empty names list returns empty dict."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {"secrets": {"names": []}}
        env_config = {"secrets": {"path_prefix": "/app/staging"}}
        result = get_secrets_from_config(config, "staging", env_config)

        assert result == {}


class TestGetRunCommand:
    """Tests for get_run_command function."""

    def test_requires_deploy_toml(self):
        """Test that None deploy_toml raises ValueError."""
        from deployer.core.config import get_run_command

        with pytest.raises(ValueError, match="deploy.toml is required"):
            get_run_command(None, "migrate", None)

    def test_known_command(self):
        """Test looking up a defined command."""
        from deployer.core.config import get_run_command

        deploy_toml = {
            "commands": {
                "migrate": ["python", "manage.py", "migrate"],
            }
        }
        result = get_run_command(deploy_toml, "migrate", None)
        assert result == ["python", "manage.py", "migrate"]

    def test_unknown_command(self):
        """Test that unknown command raises ValueError with available list."""
        from deployer.core.config import get_run_command

        deploy_toml = {
            "commands": {
                "migrate": ["python", "manage.py", "migrate"],
            }
        }
        with pytest.raises(ValueError, match="Unknown command 'shell'.*migrate"):
            get_run_command(deploy_toml, "shell", None)

    def test_unknown_command_empty_commands(self):
        """Test error message when no commands defined."""
        from deployer.core.config import get_run_command

        deploy_toml = {"commands": {}}
        with pytest.raises(ValueError, match="No commands defined"):
            get_run_command(deploy_toml, "migrate", None)

    def test_extra_args(self):
        """Test extra args are appended."""
        from deployer.core.config import get_run_command

        deploy_toml = {
            "commands": {
                "migrate": ["python", "manage.py", "migrate"],
            }
        }
        result = get_run_command(deploy_toml, "migrate", ["--fake"])
        assert result == ["python", "manage.py", "migrate", "--fake"]

    def test_dict_format_command(self):
        """Test dict format with ddl flag."""
        from deployer.core.config import get_run_command

        deploy_toml = {
            "commands": {
                "migrate": {"command": ["python", "manage.py", "migrate"], "ddl": True},
            }
        }
        result = get_run_command(deploy_toml, "migrate", None)
        assert result == ["python", "manage.py", "migrate"]


class TestCommandRequiresDDL:
    """Tests for command_requires_ddl function."""

    def test_requires_deploy_toml(self):
        """Test that None deploy_toml raises ValueError."""
        from deployer.core.config import command_requires_ddl

        with pytest.raises(ValueError, match="deploy.toml is required"):
            command_requires_ddl(None, "migrate")

    def test_ddl_true(self):
        """Test command with ddl=true."""
        from deployer.core.config import command_requires_ddl

        deploy_toml = {
            "commands": {
                "migrate": {"command": ["python", "manage.py", "migrate"], "ddl": True},
            }
        }
        assert command_requires_ddl(deploy_toml, "migrate") is True

    def test_ddl_false_explicit(self):
        """Test command with ddl=false."""
        from deployer.core.config import command_requires_ddl

        deploy_toml = {
            "commands": {
                "migrate": {"command": ["python", "manage.py", "migrate"], "ddl": False},
            }
        }
        assert command_requires_ddl(deploy_toml, "migrate") is False

    def test_list_format_no_ddl(self):
        """Test command in list format has no DDL."""
        from deployer.core.config import command_requires_ddl

        deploy_toml = {
            "commands": {
                "shell": ["python", "manage.py", "shell"],
            }
        }
        assert command_requires_ddl(deploy_toml, "shell") is False

    def test_unknown_command_no_ddl(self):
        """Test that unknown command returns False (no fallback)."""
        from deployer.core.config import command_requires_ddl

        deploy_toml = {"commands": {}}
        assert command_requires_ddl(deploy_toml, "migrate") is False


class TestGetTofuDir:
    """Tests for get_tofu_dir function."""

    def test_no_tofu_section_returns_env_path(self, tmp_path):
        """Test that missing [tofu] section returns env_path."""
        from deployer.core.config import get_tofu_dir

        config = {"environment": {"type": "staging"}}
        assert get_tofu_dir(config, tmp_path) == tmp_path

    def test_empty_tofu_dir_returns_env_path(self, tmp_path):
        """Test that empty [tofu].dir returns env_path."""
        from deployer.core.config import get_tofu_dir

        config = {"tofu": {}}
        assert get_tofu_dir(config, tmp_path) == tmp_path

    def test_absolute_path(self, tmp_path):
        """Test absolute path for [tofu].dir."""
        from deployer.core.config import get_tofu_dir

        tofu_dir = tmp_path / "infra"
        tofu_dir.mkdir()
        config = {"tofu": {"dir": str(tofu_dir)}}
        assert get_tofu_dir(config, tmp_path) == tofu_dir

    def test_relative_path(self, tmp_path):
        """Test relative path resolved against env_path."""
        from deployer.core.config import get_tofu_dir

        env_path = tmp_path / "environments" / "myapp-staging"
        env_path.mkdir(parents=True)
        tofu_dir = tmp_path / "environments" / "myapp-staging" / "infra"
        tofu_dir.mkdir()
        config = {"tofu": {"dir": "infra"}}
        assert get_tofu_dir(config, env_path) == tofu_dir

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        """Test ~ expansion in [tofu].dir."""
        from deployer.core.config import get_tofu_dir

        infra_dir = tmp_path / "code" / "myapp" / "infra"
        infra_dir.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(tmp_path))
        config = {"tofu": {"dir": "~/code/myapp/infra"}}
        assert get_tofu_dir(config, tmp_path) == infra_dir

    def test_nonexistent_dir_raises(self, tmp_path):
        """Test that nonexistent directory raises FileNotFoundError."""
        from deployer.core.config import get_tofu_dir

        config = {"tofu": {"dir": "/nonexistent/path"}}
        with pytest.raises(FileNotFoundError, match="does not exist"):
            get_tofu_dir(config, tmp_path)
