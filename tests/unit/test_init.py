"""Tests for deployer.init package — template loading, substitution, environment generation."""

import os
from unittest.mock import patch

import pytest

from deployer.init.template import (
    build_services_block,
    extract_env_type,
    get_template_dir,
    list_templates,
    load_all_templates,
    parse_services_sizing,
    replace_hcl_services_block,
    substitute,
    substitute_optional,
)

# =============================================================================
# Template discovery
# =============================================================================


class TestListTemplates:
    """Tests for list_templates() and template discovery."""

    def test_lists_all_templates(self):
        templates = list_templates()
        assert "standalone-staging" in templates
        assert "standalone-production" in templates
        assert "shared-app-staging" in templates
        assert "shared-app-production" in templates
        assert "shared-infra-staging" in templates
        assert "shared-infra-production" in templates

    def test_returns_sorted(self):
        templates = list_templates()
        assert templates == sorted(templates)

    def test_exactly_seven_templates(self):
        templates = list_templates()
        assert len(templates) == 7


class TestGetTemplateDir:
    """Tests for get_template_dir()."""

    def test_valid_template(self):
        path = get_template_dir("standalone-staging")
        assert path.is_dir()
        assert path.name == "standalone-staging"

    def test_invalid_template(self):
        with pytest.raises(ValueError, match="Template not found"):
            get_template_dir("nonexistent-template")

    def test_error_lists_available(self):
        with pytest.raises(ValueError, match="standalone-staging"):
            get_template_dir("nonexistent")


class TestLoadAllTemplates:
    """Tests for load_all_templates()."""

    def test_loads_standalone_staging(self):
        files = load_all_templates("standalone-staging")
        assert "main.tf" in files
        assert "config.toml" in files
        assert "services.auto.tfvars" in files
        assert "terraform.tfvars" in files
        assert "README.md" in files

    def test_strips_example_suffix(self):
        files = load_all_templates("standalone-staging")
        for key in files:
            assert not key.endswith(".example")

    def test_shared_infra_has_no_config_toml(self):
        files = load_all_templates("shared-infra-staging")
        assert "config.toml" not in files
        assert "main.tf" in files
        assert "terraform.tfvars" in files

    def test_shared_app_has_config_toml(self):
        files = load_all_templates("shared-app-staging")
        assert "config.toml" in files
        assert "main.tf" in files
        assert "terraform.tfvars" in files

    def test_invalid_template(self):
        with pytest.raises(ValueError, match="Template not found"):
            load_all_templates("nonexistent")


# =============================================================================
# Substitution
# =============================================================================


class TestSubstitute:
    """Tests for substitute()."""

    def test_simple_replacement(self):
        result = substitute("hello {{name}}", name="world")
        assert result == "hello world"

    def test_multiple_replacements(self):
        result = substitute("{{a}}-{{b}}", a="x", b="y")
        assert result == "x-y"

    def test_filter_title(self):
        result = substitute("{{name | title}}", name="hello world")
        assert result == "Hello World"

    def test_filter_upper(self):
        result = substitute("{{name | upper}}", name="hello")
        assert result == "HELLO"

    def test_filter_lower(self):
        result = substitute("{{name | lower}}", name="HELLO")
        assert result == "hello"

    def test_missing_placeholder_raises(self):
        with pytest.raises(KeyError, match="Missing template value"):
            substitute("{{missing}}", name="value")

    def test_integer_value(self):
        result = substitute("priority = {{priority}}", priority=100)
        assert result == "priority = 100"

    def test_preserves_tofu_placeholders(self):
        """${tofu:...} uses $ not {{ so should pass through untouched."""
        template = "${tofu:cluster_name} and {{name}}"
        result = substitute(template, name="test")
        assert result == "${tofu:cluster_name} and test"


class TestSubstituteOptional:
    """Tests for substitute_optional()."""

    def test_known_replaced(self):
        result = substitute_optional("{{a}} {{b}}", a="x")
        assert result == "x {{b}}"

    def test_unknown_left_unchanged(self):
        result = substitute_optional("{{unknown}}")
        assert result == "{{unknown}}"

    def test_filter_on_optional(self):
        result = substitute_optional("{{name | title}}", name="hello")
        assert result == "Hello"


# =============================================================================
# HCL block replacement
# =============================================================================


class TestReplaceHclServicesBlock:
    """Tests for replace_hcl_services_block()."""

    def test_simple_replacement(self):
        content = "services = {\n  web = {}\n}\n\nscaling = {}"
        result = replace_hcl_services_block(content, "services = {\n  api = {}\n}")
        assert "api" in result
        assert "web" not in result
        assert "scaling = {}" in result

    def test_nested_braces(self):
        content = (
            "services = {\n"
            "  web = {\n"
            "    cpu = 256\n"
            "    memory = 512\n"
            "  }\n"
            "  worker = {\n"
            "    cpu = 128\n"
            "  }\n"
            "}\n"
            '\nother = "value"'
        )
        new = "services = {\n  api = {\n    cpu = 1024\n  }\n}"
        result = replace_hcl_services_block(content, new)
        assert "api" in result
        assert "web" not in result
        assert 'other = "value"' in result

    def test_block_not_found(self):
        with pytest.raises(ValueError, match="Block 'services' not found"):
            replace_hcl_services_block("scaling = {}", "services = {}")

    def test_preserves_surrounding_content(self):
        content = "# header\n\nservices = {\n  web = {}\n}\n\n# footer"
        result = replace_hcl_services_block(content, "services = {}")
        assert "# header" in result
        assert "# footer" in result

    def test_empty_block(self):
        content = "services = {}"
        result = replace_hcl_services_block(content, "services = {\n  web = {}\n}")
        assert "web" in result


# =============================================================================
# Services block building
# =============================================================================


class TestBuildServicesBlock:
    """Tests for build_services_block()."""

    def test_default_web_service(self):
        result = build_services_block({}, {"cpu": 256, "memory": 512, "replicas": 1})
        assert "services = {" in result
        assert "web = {" in result
        assert "cpu               = 256" in result
        assert "port              = 8000" in result

    def test_from_deploy_config(self):
        config = {
            "services": {
                "web": {"port": 3000, "health_check_path": "/ready"},
                "worker": {},
            }
        }
        result = build_services_block(config, {"cpu": 512, "memory": 1024, "replicas": 2})
        assert "web = {" in result
        assert "worker = {" in result
        assert "port              = 3000" in result
        assert 'health_check_path = "/ready"' in result
        assert "cpu               = 512" in result
        # worker should not be load_balanced
        lines = result.split("\n")
        worker_section = False
        for line in lines:
            if "worker = {" in line:
                worker_section = True
            if worker_section and "load_balanced" in line:
                assert "false" in line
                break

    def test_with_path_pattern(self):
        config = {
            "services": {
                "api": {"port": 8000, "path_pattern": "/api/*"},
            }
        }
        result = build_services_block(config, {"cpu": 256, "memory": 512, "replicas": 1})
        assert 'path_pattern      = "/api/*"' in result

    def test_with_service_discovery(self):
        config = {
            "services": {
                "web": {"port": 8000, "service_discovery": True},
            }
        }
        result = build_services_block(config, {"cpu": 256, "memory": 512, "replicas": 1})
        assert "service_discovery = true" in result


# =============================================================================
# Extract env type
# =============================================================================


class TestExtractEnvType:
    """Tests for extract_env_type()."""

    def test_staging(self):
        assert extract_env_type("standalone-staging") == "staging"

    def test_production(self):
        assert extract_env_type("standalone-production") == "production"

    def test_shared_infra_staging(self):
        assert extract_env_type("shared-infra-staging") == "staging"

    def test_shared_app_production(self):
        assert extract_env_type("shared-app-production") == "production"

    def test_custom_template(self):
        assert extract_env_type("standalone-staging-cloudfront") == "staging"

    def test_invalid(self):
        with pytest.raises(ValueError, match="Cannot determine environment type"):
            extract_env_type("something-invalid")


# =============================================================================
# Parse services sizing
# =============================================================================


class TestParseServicesSizing:
    """Tests for parse_services_sizing()."""

    def test_extracts_from_content(self):
        content = (
            "services = {\n"
            "  web = {\n"
            "    cpu    = 1024\n"
            "    memory = 2048\n"
            "    replicas = 3\n"
            "  }\n"
            "}"
        )
        result = parse_services_sizing(content)
        assert result == {"cpu": 1024, "memory": 2048, "replicas": 3}

    def test_defaults_when_not_found(self):
        result = parse_services_sizing("no relevant content here")
        assert result == {"cpu": 256, "memory": 512, "replicas": 1}


# =============================================================================
# End-to-end generation
# =============================================================================


class TestGenerateEnvironment:
    """Tests for generate_environment() end-to-end."""

    @pytest.fixture(autouse=True)
    def setup_env_dir(self, tmp_path):
        """Set DEPLOYER_ENVIRONMENTS_DIR to a temp directory."""
        self.env_dir = tmp_path / "environments"
        self.env_dir.mkdir()
        with patch.dict(os.environ, {"DEPLOYER_ENVIRONMENTS_DIR": str(self.env_dir)}):
            yield

    def test_standalone_staging(self):
        from deployer.init.environment import generate_environment

        files = generate_environment(
            app_name="testapp",
            template_name="standalone-staging",
            deploy_toml_path=None,
            domain=None,
        )

        # Check expected files
        filenames = {os.path.basename(f) for f in files}
        assert "main.tf" in filenames
        assert "config.toml" in filenames
        assert "services.auto.tfvars" in filenames
        assert "terraform.tfvars" in filenames
        assert "terraform.tfvars.example" in filenames
        assert "README.md" in filenames

        # Check substitution happened
        main_tf = [v for k, v in files.items() if k.endswith("main.tf")][0]
        assert "testapp-staging" in main_tf
        assert "{{" not in main_tf

        # Check config.toml has tofu placeholders preserved
        config_toml = [v for k, v in files.items() if k.endswith("config.toml")][0]
        assert "${tofu:" in config_toml
        assert "{{" not in config_toml
        assert '"staging"' in config_toml

        # Check services.auto.tfvars has literal HCL (not {{services_block}})
        services_tfvars = [v for k, v in files.items() if k.endswith("services.auto.tfvars")][0]
        assert "services = {" in services_tfvars
        assert "{{" not in services_tfvars
        assert "cpu" in services_tfvars

    def test_standalone_production(self):
        from deployer.init.environment import generate_environment

        files = generate_environment(
            app_name="testapp",
            template_name="standalone-production",
            deploy_toml_path=None,
            domain=None,
        )

        filenames = {os.path.basename(f) for f in files}
        assert "services.auto.tfvars" in filenames

        config_toml = [v for k, v in files.items() if k.endswith("config.toml")][0]
        assert '"production"' in config_toml

    def test_shared_infra_staging(self):
        from deployer.init.environment import generate_environment

        files = generate_environment(
            app_name=None,
            template_name="shared-infra-staging",
            deploy_toml_path=None,
            domain="staging.example.com",
        )

        filenames = {os.path.basename(f) for f in files}
        assert "main.tf" in filenames
        assert "terraform.tfvars" in filenames
        assert "README.md" in filenames
        assert "config.toml" not in filenames

        # Check directory path uses template name as env_name
        paths = list(files.keys())
        assert any("shared-infra-staging" in p for p in paths)

    def test_shared_app_staging(self):
        from deployer.init.environment import generate_environment

        files = generate_environment(
            app_name="testapp",
            template_name="shared-app-staging",
            deploy_toml_path=None,
            domain=None,
            listener_priority=200,
        )

        filenames = {os.path.basename(f) for f in files}
        assert "main.tf" in filenames
        assert "config.toml" in filenames
        assert "terraform.tfvars" in filenames

        main_tf = [v for k, v in files.items() if k.endswith("main.tf")][0]
        assert "200" in main_tf  # listener_priority
        assert "testapp" in main_tf

    def test_missing_app_name_for_standalone(self):
        from deployer.init.environment import generate_environment

        with pytest.raises(ValueError, match="--app-name is required"):
            generate_environment(
                app_name=None,
                template_name="standalone-staging",
                deploy_toml_path=None,
                domain=None,
            )

    def test_with_deploy_toml(self, tmp_path):
        from deployer.init.environment import generate_environment

        # Create a deploy.toml with multiple services
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text(
            '[application]\nname = "testapp"\n\n'
            '[services.web]\nport = 3000\nhealth_check_path = "/ready"\n\n'
            "[services.worker]\n"
        )

        with patch.dict(os.environ, {"DEPLOYER_ENVIRONMENTS_DIR": str(self.env_dir)}):
            files = generate_environment(
                app_name="testapp",
                template_name="standalone-staging",
                deploy_toml_path=deploy_toml,
                domain=None,
            )

        services_tfvars = [v for k, v in files.items() if k.endswith("services.auto.tfvars")][0]
        assert "web = {" in services_tfvars
        assert "worker = {" in services_tfvars
        assert "port              = 3000" in services_tfvars

    def test_tfvars_example_matches_tfvars_for_standalone(self):
        from deployer.init.environment import generate_environment

        files = generate_environment(
            app_name="testapp",
            template_name="standalone-staging",
            deploy_toml_path=None,
            domain=None,
        )

        tfvars = [v for k, v in files.items() if k.endswith("/terraform.tfvars")][0]
        example = [v for k, v in files.items() if k.endswith("/terraform.tfvars.example")][0]
        assert tfvars == example

    def test_custom_domain(self):
        from deployer.init.environment import generate_environment

        files = generate_environment(
            app_name="testapp",
            template_name="standalone-staging",
            deploy_toml_path=None,
            domain="custom.example.com",
        )

        services_tfvars = [v for k, v in files.items() if k.endswith("services.auto.tfvars")][0]
        assert "custom.example.com" in services_tfvars


class TestUpdateServices:
    """Tests for update_services()."""

    @pytest.fixture(autouse=True)
    def setup_env(self, tmp_path):
        """Create a fake environment directory."""
        self.env_dir = tmp_path / "environments"
        self.app_dir = self.env_dir / "testapp-staging"
        self.app_dir.mkdir(parents=True)

        self.services_file = self.app_dir / "services.auto.tfvars"
        self.services_file.write_text(
            "# Service Configuration\n\n"
            "services = {\n"
            "  web = {\n"
            "    cpu    = 512\n"
            "    memory = 1024\n"
            "    replicas = 1\n"
            "    load_balanced = true\n"
            "    port = 8000\n"
            "  }\n"
            "}\n\n"
            "scaling = {}\n"
        )

        self.deploy_toml = tmp_path / "deploy.toml"
        self.deploy_toml.write_text(
            '[application]\nname = "testapp"\n\n'
            '[services.web]\nport = 8000\nhealth_check_path = "/health/"\n\n'
            '[services.api]\nport = 3000\nhealth_check_path = "/ready"\n\n'
            "[services.worker]\n"
        )

        with patch.dict(os.environ, {"DEPLOYER_ENVIRONMENTS_DIR": str(self.env_dir)}):
            yield

    def test_updates_services(self):
        from deployer.init.environment import update_services

        result = update_services(
            env_name="testapp-staging",
            deploy_toml_path=self.deploy_toml,
        )

        assert result is not None
        assert "web = {" in result
        assert "api = {" in result
        assert "worker = {" in result
        # Should use existing sizing as defaults
        assert "cpu               = 512" in result
        assert "scaling = {}" in result  # Preserved

    def test_dry_run(self, capsys):
        from deployer.init.environment import update_services

        result = update_services(
            env_name="testapp-staging",
            deploy_toml_path=self.deploy_toml,
            dry_run=True,
        )

        assert result is None
        captured = capsys.readouterr()
        assert "Would update:" in captured.out
        assert "api = {" in captured.out

        # File should be unchanged
        content = self.services_file.read_text()
        assert "api" not in content

    def test_missing_env(self, tmp_path):
        from deployer.init.environment import update_services

        with pytest.raises(FileNotFoundError, match="Environment directory not found"):
            update_services(
                env_name="nonexistent",
                deploy_toml_path=self.deploy_toml,
            )

    def test_missing_deploy_toml(self, tmp_path):
        from deployer.init.environment import update_services

        with pytest.raises(FileNotFoundError, match="deploy.toml not found"):
            update_services(
                env_name="testapp-staging",
                deploy_toml_path=tmp_path / "nonexistent.toml",
            )
