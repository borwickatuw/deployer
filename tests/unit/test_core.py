"""Tests for deployer.core package."""

import string

import pytest

from deployer.core import (
    ServiceMetrics,
    audit_env_vars,
    audit_images,
    audit_services,
    calculate_percentile,
    classify_service,
    estimate_savings,
    format_user,
    format_welcome_message,
    generate_temp_password,
    generate_tfvars_diff,
    get_recommended_cpu,
    get_recommended_memory,
    run_audit,
    topological_sort,
)
from deployer.config import TfvarsService


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


class TestClassifyService:
    """Tests for classify_service function."""

    def test_classify_ok(self):
        """Test OK classification."""
        metrics = ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=50.0,
            cpu_p95=60.0,
            cpu_max=70.0,
            memory_avg=50.0,
            memory_p95=60.0,
            memory_max=70.0,
            status="",
        )
        assert classify_service(metrics) == "OK"

    def test_classify_over_provisioned(self):
        """Test over-provisioned classification."""
        metrics = ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=10.0,
            cpu_p95=20.0,
            cpu_max=30.0,
            memory_avg=10.0,
            memory_p95=20.0,
            memory_max=30.0,
            status="",
        )
        assert classify_service(metrics) == "OVER_PROVISIONED"

    def test_classify_under_provisioned(self):
        """Test under-provisioned classification."""
        metrics = ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=75.0,
            cpu_p95=85.0,
            cpu_max=90.0,
            memory_avg=50.0,
            memory_p95=60.0,
            memory_max=70.0,
            status="",
        )
        assert classify_service(metrics) == "UNDER_PROVISIONED"

    def test_classify_bursty(self):
        """Test bursty classification."""
        metrics = ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=20.0,
            cpu_p95=80.0,
            cpu_max=95.0,
            memory_avg=50.0,
            memory_p95=60.0,
            memory_max=70.0,
            status="",
        )
        assert classify_service(metrics) == "BURSTY"


class TestCalculatePercentile:
    """Tests for calculate_percentile function."""

    def test_empty_list(self):
        """Test empty input returns 0."""
        assert calculate_percentile([], 95) == 0.0

    def test_single_value(self):
        """Test single value returns that value."""
        assert calculate_percentile([50.0], 50) == 50.0

    def test_percentile_50(self):
        """Test median calculation."""
        values = [10, 20, 30, 40, 50]
        assert calculate_percentile(values, 50) == 30.0


class TestGetRecommendedCpu:
    """Tests for get_recommended_cpu function."""

    def test_no_change_needed(self):
        """Test no recommendation when optimal."""
        result = get_recommended_cpu(256, 50.0, 70.0)
        assert result is None

    def test_zero_utilization(self):
        """Test no recommendation for zero utilization."""
        result = get_recommended_cpu(256, 0.0, 0.0)
        assert result is None

    def test_increase_recommended(self):
        """Test recommendation to increase CPU."""
        result = get_recommended_cpu(256, 80.0, 90.0)
        assert result == 512


class TestGetRecommendedMemory:
    """Tests for get_recommended_memory function."""

    def test_no_change_needed(self):
        """Test no recommendation when optimal."""
        result = get_recommended_memory(512, 50.0, 70.0, 256)
        assert result is None

    def test_zero_utilization(self):
        """Test no recommendation for zero utilization."""
        result = get_recommended_memory(512, 0.0, 0.0, 256)
        assert result is None


class TestEstimateSavings:
    """Tests for estimate_savings function."""

    def test_no_savings(self):
        """Test zero savings when no over-provisioned services."""
        services = [
            ServiceMetrics(
                service_name="test",
                cpu_allocated=256,
                memory_allocated=512,
                cpu_avg=50.0,
                cpu_p95=60.0,
                cpu_max=70.0,
                memory_avg=50.0,
                memory_p95=60.0,
                memory_max=70.0,
                status="OK",
            )
        ]
        assert estimate_savings(services) == 0.0

    def test_savings_calculated(self):
        """Test savings calculation for over-provisioned service."""
        services = [
            ServiceMetrics(
                service_name="test",
                cpu_allocated=1024,
                memory_allocated=2048,
                cpu_avg=10.0,
                cpu_p95=20.0,
                cpu_max=30.0,
                memory_avg=10.0,
                memory_p95=20.0,
                memory_max=30.0,
                status="OVER_PROVISIONED",
                cpu_recommendation=256,
            )
        ]
        assert estimate_savings(services) > 0


class TestGenerateTfvarsDiff:
    """Tests for generate_tfvars_diff function."""

    def test_no_diff(self):
        """Test no diff when matching."""
        services = [
            ServiceMetrics(
                service_name="web",
                cpu_allocated=256,
                memory_allocated=512,
                cpu_avg=50.0,
                cpu_p95=60.0,
                cpu_max=70.0,
                memory_avg=50.0,
                memory_p95=60.0,
                memory_max=70.0,
                status="OK",
            )
        ]
        tfvars = {"web": TfvarsService(cpu=256, memory=512, replicas=1)}
        assert generate_tfvars_diff(services, tfvars) == []


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
            username="user@example.com",
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
            username="user@example.com",
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
        config = {"ignore_services": set(), "service_mapping": {}}
        assert audit_services(compose, deploy, config) == []

    def test_missing_service(self):
        """Test detection of missing service."""
        compose = {"web": {"has_build": True, "profiles": []}}
        deploy = {}
        config = {"ignore_services": set(), "service_mapping": {}}
        issues = audit_services(compose, deploy, config)
        assert len(issues) == 1
        assert "web" in issues[0]


class TestAuditImages:
    """Tests for audit_images function."""

    def test_all_accounted_for(self):
        """Test no issues when all images accounted for."""
        compose = {"web": {"has_build": True, "build_context": "web", "profiles": []}}
        images = {"web": {"context": "web"}}
        config = {"ignore_services": set(), "ignore_images": set()}
        assert audit_images(compose, images, config) == []


class TestAuditEnvVars:
    """Tests for audit_env_vars function."""

    def test_all_accounted_for(self):
        """Test no issues when all env vars accounted for."""
        compose = {
            "web": {"has_build": True, "environment": ["DATABASE_URL"], "profiles": []}
        }
        env_vars = {"DATABASE_URL"}
        config = {"ignore_env_vars": set(), "ignore_services": set()}
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
        result = get_secrets_from_config(config, "staging")

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

    def test_module_format_without_env_config_returns_empty(self):
        """Test that module format returns empty dict if env_config not provided."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        result = get_secrets_from_config(config, "staging")  # No env_config

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
        result = get_secrets_from_config(config, "staging")

        assert result == {}

    def test_empty_names_list_returns_empty(self):
        """Test that empty names list returns empty dict."""
        from deployer.core.ssm_secrets import get_secrets_from_config

        config = {"secrets": {"names": []}}
        env_config = {"secrets": {"path_prefix": "/app/staging"}}
        result = get_secrets_from_config(config, "staging", env_config)

        assert result == {}
