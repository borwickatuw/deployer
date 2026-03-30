"""Tests for deploy.py functions."""

import sys
from pathlib import Path

import pytest

# Add bin directory to path so we can import the script
bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

# Import Deployer class for integration tests
from importlib.util import module_from_spec, spec_from_file_location

# Import from the new module structure
from deployer.config import ImageConfig
from deployer.core.deploy import topological_sort
from deployer.deploy.context import DeploymentContext
from deployer.deploy.task_definition import get_environment_variables, get_service_sizing
from deployer.deploy.task_definition import _resolve_legacy_placeholders


def _make_ctx(**overrides) -> DeploymentContext:
    """Create a DeploymentContext with sensible test defaults."""
    defaults = {
        "ecs_client": None,
        "cluster_name": "test-cluster",
        "config": {},
        "service_config": {},
        "infra_config": {},
        "app_name": "testapp",
        "environment": "staging",
        "region": "us-west-2",
        "account_id": "123456789012",
        "env_config": {},
        "dry_run": False,
    }
    defaults.update(overrides)
    return DeploymentContext(**defaults)

_spec = spec_from_file_location("deploy", bin_dir / "deploy.py")
deploy = module_from_spec(_spec)
_spec.loader.exec_module(deploy)


class TestTopologicalSort:
    """Tests for topological_sort function."""

    def test_no_dependencies(self):
        """Test sorting images with no dependencies."""
        images = {
            "web": {"context": "."},
            "worker": {"context": "."},
        }

        result = topological_sort(images)

        # Both should be in result, order doesn't matter
        assert set(result) == {"web", "worker"}
        assert len(result) == 2

    def test_single_dependency(self):
        """Test that dependencies are built first."""
        images = {
            "web": {"context": ".", "depends_on": ["base"]},
            "base": {"context": "./base"},
        }

        result = topological_sort(images)

        assert result.index("base") < result.index("web")

    def test_chain_dependencies(self):
        """Test chain of dependencies: c depends on b, b depends on a."""
        images = {
            "c": {"context": ".", "depends_on": ["b"]},
            "b": {"context": ".", "depends_on": ["a"]},
            "a": {"context": "."},
        }

        result = topological_sort(images)

        assert result.index("a") < result.index("b")
        assert result.index("b") < result.index("c")

    def test_multiple_dependencies(self):
        """Test image depending on multiple others."""
        images = {
            "app": {"context": ".", "depends_on": ["base", "utils"]},
            "base": {"context": "."},
            "utils": {"context": "."},
        }

        result = topological_sort(images)

        assert result.index("base") < result.index("app")
        assert result.index("utils") < result.index("app")

    def test_circular_dependency_detected(self):
        """Test that circular dependencies raise an error."""
        images = {
            "a": {"context": ".", "depends_on": ["b"]},
            "b": {"context": ".", "depends_on": ["c"]},
            "c": {"context": ".", "depends_on": ["a"]},
        }

        with pytest.raises(ValueError, match="[Cc]ircular"):
            topological_sort(images)

    def test_unknown_dependency(self):
        """Test that unknown dependencies raise an error."""
        images = {
            "app": {"context": ".", "depends_on": ["nonexistent"]},
        }

        with pytest.raises(ValueError, match="unknown"):
            topological_sort(images)

    def test_empty_images(self):
        """Test empty images dict returns empty list."""
        result = topological_sort({})
        assert result == []

    def test_complex_dag(self):
        """Test a more complex dependency graph."""
        #        a
        #       / \
        #      b   c
        #       \ /
        #        d
        images = {
            "a": {"context": "."},
            "b": {"context": ".", "depends_on": ["a"]},
            "c": {"context": ".", "depends_on": ["a"]},
            "d": {"context": ".", "depends_on": ["b", "c"]},
        }

        result = topological_sort(images)

        assert result[0] == "a"
        assert result.index("b") < result.index("d")
        assert result.index("c") < result.index("d")


class TestGetServiceSizing:
    """Tests for get_service_sizing function."""

    def test_get_service_sizing_defaults(self):
        """Test that defaults are applied when no config exists."""
        config = {"services": {"web": {}}}
        service_config = {}

        sizing = get_service_sizing("web", config, service_config)

        assert sizing["cpu"] == 256
        assert sizing["memory"] == 512
        assert sizing["replicas"] == 1
        assert sizing["load_balanced"] is False

    def test_get_service_sizing_from_env(self):
        """Test that SERVICE_CONFIG overrides deploy.toml."""
        config = {"services": {"web": {"cpu": 256, "memory": 512}}}
        service_config = {"web": {"cpu": 1024, "memory": 2048, "replicas": 2}}

        sizing = get_service_sizing("web", config, service_config)

        assert sizing["cpu"] == 1024
        assert sizing["memory"] == 2048
        assert sizing["replicas"] == 2

    def test_min_cpu_validation_passes(self):
        """Test that config meeting minimum CPU passes validation."""
        config = {"services": {"web": {"min_cpu": 512}}}
        service_config = {"web": {"cpu": 1024, "memory": 2048}}

        sizing = get_service_sizing("web", config, service_config)

        assert sizing["cpu"] == 1024
        assert sizing["memory"] == 2048

    def test_min_cpu_validation_fails(self):
        """Test that config below minimum CPU raises ValueError."""
        config = {"services": {"web": {"min_cpu": 512}}}
        service_config = {"web": {"cpu": 256, "memory": 512}}

        with pytest.raises(ValueError) as exc_info:
            get_service_sizing("web", config, service_config)

        assert "CPU (256) is below minimum required (512)" in str(exc_info.value)
        assert "terraform.tfvars" in str(exc_info.value)

    def test_min_memory_validation_fails(self):
        """Test that config below minimum memory raises ValueError."""
        config = {"services": {"worker": {"min_memory": 1024}}}
        service_config = {"worker": {"cpu": 512, "memory": 512}}

        with pytest.raises(ValueError) as exc_info:
            get_service_sizing("worker", config, service_config)

        assert "memory (512) is below minimum required (1024)" in str(exc_info.value)
        assert "terraform.tfvars" in str(exc_info.value)

    def test_no_minimum_specified(self):
        """Test that validation is skipped when no minimums are specified."""
        config = {"services": {"web": {}}}
        service_config = {}

        # Should not raise - defaults apply and no minimums to check
        sizing = get_service_sizing("web", config, service_config)

        assert sizing["cpu"] == 256
        assert sizing["memory"] == 512

    def test_min_cpu_with_default(self):
        """Test that min_cpu validates against default cpu=256."""
        config = {"services": {"transcoder": {"min_cpu": 512}}}
        # No service_config means default cpu=256 will be used
        service_config = {}

        with pytest.raises(ValueError) as exc_info:
            get_service_sizing("transcoder", config, service_config)

        assert "CPU (256) is below minimum required (512)" in str(exc_info.value)


class TestResolveLegacyPlaceholders:
    """Tests for _resolve_legacy_placeholders function (backward compatibility)."""

    def test_resolve_placeholders(self):
        """Test that ${placeholder} syntax is resolved."""
        infra_config = {
            "database_url": "postgres://localhost/test",
            "redis_url": "redis://localhost:6379",
        }

        env_vars = {
            "DATABASE_URL": "${database_url}",
            "REDIS_URL": "${redis_url}",
            "STATIC_VALUE": "fixed",
        }

        resolved = _resolve_legacy_placeholders(env_vars, "us-west-2", "staging", infra_config)

        assert resolved["DATABASE_URL"] == "postgres://localhost/test"
        assert resolved["REDIS_URL"] == "redis://localhost:6379"
        assert resolved["STATIC_VALUE"] == "fixed"

    def test_resolve_unknown_placeholder(self):
        """Test that unknown placeholders remain unchanged."""
        env_vars = {"UNKNOWN": "${unknown_var}"}
        resolved = _resolve_legacy_placeholders(env_vars, "us-west-2", "staging", {})

        assert resolved["UNKNOWN"] == "${unknown_var}"


class TestImageConfigGetBuildArgs:
    """Tests for ImageConfig.get_build_args method."""

    def test_no_build_args(self):
        """Test image with no build args returns empty dict."""
        img = ImageConfig(name="web", context=".", dockerfile="Dockerfile")

        build_args = img.get_build_args("staging")

        assert build_args == {}

    def test_base_build_args(self):
        """Test image with base build args only."""
        img = ImageConfig(
            name="web",
            context=".",
            dockerfile="Dockerfile",
            build_args={"PYTHON_VERSION": "3.12", "NODE_VERSION": "20"},
        )

        build_args = img.get_build_args("staging")

        assert build_args == {"PYTHON_VERSION": "3.12", "NODE_VERSION": "20"}

    def test_environment_specific_build_args(self):
        """Test that environment-specific build args override base."""
        img = ImageConfig(
            name="web",
            context=".",
            dockerfile="Dockerfile",
            build_args={
                "PYTHON_VERSION": "3.12",
                "staging": {"UV_INSTALL_ARGS": "--group dev"},
                "production": {"UV_INSTALL_ARGS": ""},
            },
        )

        # Test staging
        build_args = img.get_build_args("staging")
        assert build_args["PYTHON_VERSION"] == "3.12"
        assert build_args["UV_INSTALL_ARGS"] == "--group dev"

        # Test production
        build_args_prod = img.get_build_args("production")
        assert build_args_prod["PYTHON_VERSION"] == "3.12"
        assert build_args_prod["UV_INSTALL_ARGS"] == ""

    def test_environment_override_base_arg(self):
        """Test that environment-specific build args can override base args."""
        img = ImageConfig(
            name="web",
            context=".",
            dockerfile="Dockerfile",
            build_args={"DEBUG": "false", "staging": {"DEBUG": "true"}},
        )

        build_args = img.get_build_args("staging")

        assert build_args["DEBUG"] == "true"


class TestGetEnvironmentVariables:
    """Tests for get_environment_variables function."""

    def test_merge_environment_overrides(self):
        """Test that environment-specific values override base values."""
        config = {
            "environment": {"DEBUG": "false", "LOG_LEVEL": "info", "staging": {"DEBUG": "true"}}
        }
        ctx = _make_ctx(config=config)

        env_vars = get_environment_variables(ctx)

        assert env_vars["DEBUG"] == "true"  # staging override
        assert env_vars["LOG_LEVEL"] == "info"  # base value

    def test_service_specific_environment(self):
        """Test service-specific environment variables."""
        config = {
            "environment": {"APP_NAME": "testapp"},
            "services": {"web": {"environment": {"WORKER_COUNT": "4"}}},
        }
        ctx = _make_ctx(config=config)

        env_vars = get_environment_variables(ctx, service_name="web")

        assert env_vars["APP_NAME"] == "testapp"
        assert env_vars["WORKER_COUNT"] == "4"


class TestDeployerIntegration:
    """Integration tests for the Deployer class."""

    def test_deployer_initialization(self, tmp_path, mocker):
        """Test that Deployer initializes correctly with valid config."""
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text("""
[application]
name = "testapp"
source = "."

[services.web]
cpu = 256
memory = 512
""")

        mocker.patch("boto3.client")
        mocker.patch("boto3.session.Session")

        deployment_config = {
            "infrastructure": {
                "ecr_prefix": "testapp-staging",
                "execution_role_arn": "arn:aws:iam::123456789:role/test-execution",
                "task_role_arn": "arn:aws:iam::123456789:role/test-task",
                "security_group_id": "sg-12345",
                "private_subnet_ids": ["subnet-1", "subnet-2"],
                "target_group_arn": "arn:aws:elasticloadbalancing::tg/test",
            },
            "services": {
                "config": {"web": {"cpu": 256, "memory": 512, "replicas": 1}},
                "scaling": {},
                "health_check": {},
            },
            "database": {},
            "cache": {},
            "storage": {},
            "deployment": {},
            "scheduler": {},
        }

        deployer = deploy.Deployer(str(deploy_toml), "staging", deployment_config, dry_run=True)

        assert deployer.app_name == "testapp"
        assert deployer.environment == "staging"
        assert deployer.dry_run is True
