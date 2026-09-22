"""Tests for deployer.core package."""

import string
from pathlib import Path

import pytest

from deployer.config import AuditConfig, ImageConfig
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
from deployer.core.config import _resolve_tofu_placeholders
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
        assert result.username == "alice"
        assert result.email == "alice@example.com"
        assert result.status == "CONFIRMED"
        assert result.enabled is True


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
        compose = {
            "web": {
                "has_build": True,
                "build_context": "web",
                "additional_contexts": {},
                "profiles": [],
            }
        }
        images = {"web": ImageConfig(name="web", context="web")}
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
    """Tests for _get_secrets_from_config function."""

    def test_the_removed_explicit_path_form_is_rejected(self):
        """UPDATED: this used to assert the explicit form resolved normally.

        53h-2a removed it. Rejecting is not fussiness -- returning {} here is
        how `bin/ssm-secrets.py check` comes to print "Required: 0" and advise
        deleting every live parameter under the prefix.
        """
        from deployer.core.ssm_secrets import _get_secrets_from_config

        config = {
            "secrets": {
                "SECRET_KEY": "ssm:/myapp/${environment}/secret-key",  # pragma: allowlist secret
                "API_KEY": "ssm:/shared/api-key",  # pragma: allowlist secret
            }
        }
        with pytest.raises(ValueError, match=r"names = \["):
            _get_secrets_from_config(config, {})

    def test_module_format_with_names_list(self):
        """Test new module format with names = [...] and path_prefix."""
        from deployer.core.ssm_secrets import _get_secrets_from_config

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
        result = _get_secrets_from_config(config, env_config)

        assert result == {
            "SECRET_KEY": "/myapp/staging/secret-key",
            "SIGNED_URL_SECRET": "/myapp/staging/signed-url-secret",
            "DATACITE_PASSWORD": "/myapp/staging/datacite-password",
        }

    def test_module_format_normalizes_path_prefix(self):
        """Test that path_prefix is normalized (adds leading /, removes trailing /)."""
        from deployer.core.ssm_secrets import _get_secrets_from_config

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"path_prefix": "myapp/staging/"}}  # No leading /, has trailing /
        result = _get_secrets_from_config(config, env_config)

        assert result == {"SECRET_KEY": "/myapp/staging/secret-key"}

    def test_module_format_without_secrets_config_returns_empty(self):
        """Test that module format returns empty dict if env_config has no secrets."""
        from deployer.core.ssm_secrets import _get_secrets_from_config

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        result = _get_secrets_from_config(config, {})

        assert result == {}

    def test_module_format_without_path_prefix_returns_empty(self):
        """Test that module format returns empty dict if path_prefix missing."""
        from deployer.core.ssm_secrets import _get_secrets_from_config

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"provider": "ssm"}}  # No path_prefix
        result = _get_secrets_from_config(config, env_config)

        assert result == {}

    def test_mixing_the_two_forms_is_rejected_rather_than_merged(self):
        """UPDATED: the two forms used to be merged into one result.

        Merging here while `get_secrets` in the task definition dropped the
        explicit half was precisely how the drop stayed invisible: preflight
        confirmed the SSM parameters existed and passed, and the container
        started without them.
        """
        from deployer.core.ssm_secrets import _get_secrets_from_config

        config = {
            "secrets": {
                "names": ["SECRET_KEY"],
                "LEGACY_SECRET": "ssm:/legacy/path",  # pragma: allowlist secret
            }
        }
        env_config = {"secrets": {"path_prefix": "/app/staging"}}
        with pytest.raises(ValueError, match="LEGACY_SECRET"):
            _get_secrets_from_config(config, env_config)

    def test_no_secrets_section_returns_empty(self):
        """Test that missing secrets section returns empty dict."""
        from deployer.core.ssm_secrets import _get_secrets_from_config

        config = {}
        result = _get_secrets_from_config(config, {})

        assert result == {}

    def test_empty_names_list_returns_empty(self):
        """Test that empty names list returns empty dict."""
        from deployer.core.ssm_secrets import _get_secrets_from_config

        config = {"secrets": {"names": []}}
        env_config = {"secrets": {"path_prefix": "/app/staging"}}
        result = _get_secrets_from_config(config, env_config)

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


ENV_PATH = Path("/environments/myapp-staging")

TOFU_OUTPUTS = {
    "cluster_name": "myapp-staging-cluster",
    "private_subnet_ids": ["subnet-aaa", "subnet-bbb"],
    "s3_bucket_names": {"uploads": "myapp-staging-uploads", "static": "myapp-staging-static"},
    "desired_count": 2,
    "cognito_enabled": True,
}

MISSING_OUTPUT_HINT = f"Hint: If you recently added this output, run 'tofu apply' in {ENV_PATH}"


class TestResolveTofuPlaceholders:
    """Characterization of _resolve_tofu_placeholders."""

    def test_whole_string_scalar(self):
        result = _resolve_tofu_placeholders("${tofu:cluster_name}", ENV_PATH, TOFU_OUTPUTS)
        assert result == "myapp-staging-cluster"

    def test_whole_string_preserves_list(self):
        result = _resolve_tofu_placeholders("${tofu:private_subnet_ids}", ENV_PATH, TOFU_OUTPUTS)
        assert result == ["subnet-aaa", "subnet-bbb"]

    def test_whole_string_preserves_dict(self):
        result = _resolve_tofu_placeholders("${tofu:s3_bucket_names}", ENV_PATH, TOFU_OUTPUTS)
        assert result == {"uploads": "myapp-staging-uploads", "static": "myapp-staging-static"}

    def test_whole_string_preserves_int_and_bool(self):
        assert _resolve_tofu_placeholders("${tofu:desired_count}", ENV_PATH, TOFU_OUTPUTS) == 2
        assert _resolve_tofu_placeholders("${tofu:cognito_enabled}", ENV_PATH, TOFU_OUTPUTS) is True

    def test_embedded_scalar_is_stringified(self):
        result = _resolve_tofu_placeholders(
            "cluster=${tofu:cluster_name} count=${tofu:desired_count}", ENV_PATH, TOFU_OUTPUTS
        )
        assert result == "cluster=myapp-staging-cluster count=2"

    def test_embedded_list_and_dict_are_json_dumped(self):
        result = _resolve_tofu_placeholders(
            "subnets=${tofu:private_subnet_ids} buckets=${tofu:s3_bucket_names}",
            ENV_PATH,
            TOFU_OUTPUTS,
        )
        assert result == (
            'subnets=["subnet-aaa", "subnet-bbb"] '
            'buckets={"uploads": "myapp-staging-uploads", "static": "myapp-staging-static"}'
        )

    def test_non_string_values_pass_through(self):
        assert _resolve_tofu_placeholders(3, ENV_PATH, TOFU_OUTPUTS) == 3
        assert _resolve_tofu_placeholders(False, ENV_PATH, TOFU_OUTPUTS) is False
        assert _resolve_tofu_placeholders(None, ENV_PATH, TOFU_OUTPUTS) is None

    def test_string_without_placeholder_unchanged(self):
        assert _resolve_tofu_placeholders("plain", ENV_PATH, TOFU_OUTPUTS) == "plain"

    def test_recurses_into_dicts_and_lists(self):
        config = {
            "infrastructure": {
                "cluster_name": "${tofu:cluster_name}",
                "private_subnet_ids": "${tofu:private_subnet_ids}",
            },
            "extra": ["${tofu:desired_count}", "n=${tofu:desired_count}", 7],
        }
        assert _resolve_tofu_placeholders(config, ENV_PATH, TOFU_OUTPUTS) == {
            "infrastructure": {
                "cluster_name": "myapp-staging-cluster",
                "private_subnet_ids": ["subnet-aaa", "subnet-bbb"],
            },
            "extra": [2, "n=2", 7],
        }

    def test_whole_string_missing_output_raises(self):
        with pytest.raises(RuntimeError) as exc_info:
            _resolve_tofu_placeholders("${tofu:no_such_output}", ENV_PATH, TOFU_OUTPUTS)
        assert str(exc_info.value) == (
            f"Could not resolve tofu output: no_such_output\n{MISSING_OUTPUT_HINT}"
        )

    def test_embedded_missing_output_raises(self):
        with pytest.raises(RuntimeError) as exc_info:
            _resolve_tofu_placeholders("prefix-${tofu:no_such_output}", ENV_PATH, TOFU_OUTPUTS)
        assert str(exc_info.value) == (
            f"Could not resolve tofu output: no_such_output\n{MISSING_OUTPUT_HINT}"
        )

    def test_null_valued_output_is_treated_as_missing(self):
        with pytest.raises(RuntimeError, match="Could not resolve tofu output: nullable"):
            _resolve_tofu_placeholders("${tofu:nullable}", ENV_PATH, {"nullable": None})


NESTED_OUTPUTS = {
    **TOFU_OUTPUTS,
    "cache": {"primary": {"endpoint": "cache.example.com", "port": 6379}},
}


class TestDottedTofuPlaceholders:
    """${tofu:NAME.KEY...} indexes into map outputs."""

    def test_whole_string_single_level(self):
        result = _resolve_tofu_placeholders(
            "${tofu:s3_bucket_names.uploads}", ENV_PATH, NESTED_OUTPUTS
        )
        assert result == "myapp-staging-uploads"

    def test_whole_string_nested_preserves_type(self):
        assert (
            _resolve_tofu_placeholders("${tofu:cache.primary.port}", ENV_PATH, NESTED_OUTPUTS)
            == 6379
        )
        assert _resolve_tofu_placeholders("${tofu:cache.primary}", ENV_PATH, NESTED_OUTPUTS) == {
            "endpoint": "cache.example.com",
            "port": 6379,
        }

    def test_embedded_single_level_and_nested(self):
        result = _resolve_tofu_placeholders(
            "s3://${tofu:s3_bucket_names.static}/ redis://${tofu:cache.primary.endpoint}:"
            "${tofu:cache.primary.port} ${tofu:cache.primary}",
            ENV_PATH,
            NESTED_OUTPUTS,
        )
        assert result == (
            "s3://myapp-staging-static/ redis://cache.example.com:6379 "
            '{"endpoint": "cache.example.com", "port": 6379}'
        )

    @pytest.mark.parametrize(
        "value",
        ["${tofu:no_such_output.uploads}", "x-${tofu:no_such_output.uploads}"],
    )
    def test_missing_base_output_raises(self, value):
        with pytest.raises(RuntimeError) as exc_info:
            _resolve_tofu_placeholders(value, ENV_PATH, NESTED_OUTPUTS)
        assert str(exc_info.value) == (
            f"Could not resolve tofu output: no_such_output\n{MISSING_OUTPUT_HINT}"
        )

    @pytest.mark.parametrize(
        ("value", "detail"),
        [
            (
                "${tofu:cluster_name.uploads}",
                "'cluster_name' is str, not a map, so '.uploads' cannot index it",
            ),
            (
                "x-${tofu:private_subnet_ids.0}",
                "'private_subnet_ids' is list, not a map, so '.0' cannot index it",
            ),
            (
                "${tofu:cache.primary.port.x}",
                "'cache.primary.port' is int, not a map, so '.x' cannot index it",
            ),
        ],
    )
    def test_segment_on_non_map_raises(self, value, detail):
        placeholder = value.split("${tofu:")[1].rstrip("}")
        with pytest.raises(RuntimeError) as exc_info:
            _resolve_tofu_placeholders(value, ENV_PATH, NESTED_OUTPUTS)
        assert str(exc_info.value) == (
            f"Could not resolve tofu output: {placeholder}\n{detail}\n{MISSING_OUTPUT_HINT}"
        )

    @pytest.mark.parametrize(
        ("value", "detail"),
        [
            (
                "${tofu:s3_bucket_names.media}",
                "'s3_bucket_names' has no key 'media'. Available keys: static, uploads",
            ),
            (
                "x-${tofu:cache.primary.host}",
                "'cache.primary' has no key 'host'. Available keys: endpoint, port",
            ),
        ],
    )
    def test_absent_key_lists_available_keys(self, value, detail):
        placeholder = value.split("${tofu:")[1].rstrip("}")
        with pytest.raises(RuntimeError) as exc_info:
            _resolve_tofu_placeholders(value, ENV_PATH, NESTED_OUTPUTS)
        assert str(exc_info.value) == (
            f"Could not resolve tofu output: {placeholder}\n{detail}\n{MISSING_OUTPUT_HINT}"
        )

    def test_absent_key_in_empty_map(self):
        with pytest.raises(RuntimeError, match=r"'empty' has no key 'a'. Available keys: \(none\)"):
            _resolve_tofu_placeholders("${tofu:empty.a}", ENV_PATH, {"empty": {}})

    def test_absent_key_in_large_map_gives_count_not_keys(self):
        outputs = {"buckets": {f"bucket{i:02d}": f"myapp-{i}" for i in range(11)}}
        with pytest.raises(RuntimeError) as exc_info:
            _resolve_tofu_placeholders("${tofu:buckets.media}", ENV_PATH, outputs)
        assert "'buckets' has no key 'media'. 'buckets' has 11 keys\n" in str(exc_info.value)
        assert "bucket00" not in str(exc_info.value)

    def test_null_leaf_raises(self):
        with pytest.raises(RuntimeError) as exc_info:
            _resolve_tofu_placeholders(
                "x-${tofu:s3_bucket_names.media}", ENV_PATH, {"s3_bucket_names": {"media": None}}
            )
        assert str(exc_info.value) == (
            "Could not resolve tofu output: s3_bucket_names.media\n"
            f"'s3_bucket_names.media' is null\n{MISSING_OUTPUT_HINT}"
        )
