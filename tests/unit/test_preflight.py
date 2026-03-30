"""Tests for deployer.deploy.preflight module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from deployer.config import parse_deploy_config
from deployer.core.ssm_secrets import check_secrets_drift
from deployer.deploy.preflight import (
    PreflightError,
    PreflightOptions,
    check_ecr_repositories,
    check_ecs_cluster,
    check_environment_config,
    check_ssm_secrets,
    run_preflight_checks,
)

# --- Minimal valid env_config for tests ---


def make_env_config(**overrides):
    """Create a minimal valid env config dict."""
    config = {
        "infrastructure": {
            "cluster_name": "test-cluster",
            "ecr_prefix": "test",
            "execution_role_arn": "arn:aws:iam::123:role/exec",
            "task_role_arn": "arn:aws:iam::123:role/task",
            "security_group_id": "sg-123",
            "private_subnet_ids": ["subnet-1", "subnet-2"],
        },
    }
    config.update(overrides)
    return config


class TestCheckEnvironmentConfig:
    """Tests for check_environment_config."""

    def test_valid_config_passes(self):
        """Valid config should not raise."""
        check_environment_config(make_env_config())

    def test_missing_field_raises(self):
        """Missing required field should raise PreflightError."""
        config = make_env_config()
        del config["infrastructure"]["cluster_name"]
        with pytest.raises(PreflightError, match="Missing required field"):
            check_environment_config(config)

    def test_missing_infrastructure_section_raises(self):
        """Missing entire infrastructure section should raise."""
        with pytest.raises(PreflightError, match="Missing required field"):
            check_environment_config({})

    def test_empty_subnet_list_raises(self):
        """Empty subnet list should raise."""
        config = make_env_config()
        config["infrastructure"]["private_subnet_ids"] = []
        with pytest.raises(PreflightError, match="missing required fields"):
            check_environment_config(config)


class TestCheckEcrRepositories:
    """Tests for check_ecr_repositories."""

    def test_skips_when_no_ecr_prefix(self, tmp_path):
        """Should skip check gracefully when ecr_prefix is missing."""
        config = make_env_config()
        del config["infrastructure"]["ecr_prefix"]

        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text("""
[application]
name = "test"

[images.web]
context = "."
""")
        deploy_config = parse_deploy_config(deploy_toml)

        # Should not raise
        check_ecr_repositories(deploy_config, config, "test-staging")

    @patch("deployer.deploy.preflight.boto3")
    @patch("deployer.deploy.preflight.validate_ecr_repositories")
    def test_missing_repos_raises(self, mock_validate, mock_boto3, tmp_path):
        """Should raise PreflightError when repos are missing."""
        mock_validate.return_value = ["test-web"]

        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text("""
[application]
name = "test"

[images.web]
context = "."
""")
        deploy_config = parse_deploy_config(deploy_toml)

        with pytest.raises(PreflightError):
            check_ecr_repositories(deploy_config, make_env_config(), "test-staging")

    @patch("deployer.deploy.preflight.boto3")
    @patch("deployer.deploy.preflight.validate_ecr_repositories")
    def test_all_repos_exist_passes(self, mock_validate, mock_boto3, tmp_path):
        """Should pass when all repos exist."""
        mock_validate.return_value = []

        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text("""
[application]
name = "test"

[images.web]
context = "."
""")
        deploy_config = parse_deploy_config(deploy_toml)

        # Should not raise
        check_ecr_repositories(deploy_config, make_env_config(), "test-staging")


class TestCheckEcsCluster:
    """Tests for check_ecs_cluster."""

    def test_skips_when_no_cluster_name(self):
        """Should skip check when cluster_name is missing."""
        config = make_env_config()
        del config["infrastructure"]["cluster_name"]
        # Should not raise
        check_ecs_cluster(config)

    @patch("deployer.deploy.preflight.boto3")
    @patch("deployer.deploy.preflight.validate_ecs_cluster")
    def test_cluster_not_found_raises(self, mock_validate, mock_boto3):
        """Should raise PreflightError when cluster doesn't exist."""
        mock_validate.return_value = (False, "Cluster not found")

        with pytest.raises(PreflightError, match="Cluster not found"):
            check_ecs_cluster(make_env_config())

    @patch("deployer.deploy.preflight.boto3")
    @patch("deployer.deploy.preflight.validate_ecs_cluster")
    def test_cluster_exists_passes(self, mock_validate, mock_boto3):
        """Should pass when cluster exists."""
        mock_validate.return_value = (True, None)

        # Should not raise
        check_ecs_cluster(make_env_config())


class TestPreflightOptions:
    """Tests for PreflightOptions defaults."""

    def test_defaults(self):
        """All checks enabled by default."""
        opts = PreflightOptions()
        assert opts.skip_ecr_check is False
        assert opts.skip_secrets_check is False
        assert opts.skip_cluster_check is False
        assert opts.skip_audit is False

    def test_skip_all(self):
        """Can skip all checks."""
        opts = PreflightOptions(
            skip_ecr_check=True,
            skip_secrets_check=True,
            skip_cluster_check=True,
            skip_audit=True,
        )
        assert opts.skip_ecr_check is True
        assert opts.skip_audit is True


class TestRunPreflightChecks:
    """Tests for run_preflight_checks."""

    def test_invalid_config_fails(self, tmp_path):
        """Should fail immediately with invalid env config."""
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text('[application]\nname = "test"')
        deploy_config = parse_deploy_config(deploy_toml)

        with pytest.raises(PreflightError, match="Missing required field"):
            run_preflight_checks(
                deploy_config=deploy_config,
                env_config={},  # Missing infrastructure section
                environment="test-staging",
                environment_type="staging",
                project_dir=tmp_path,
                options=PreflightOptions(),
            )

    @patch("deployer.deploy.preflight.check_ecs_cluster")
    @patch("deployer.deploy.preflight.check_ssm_secrets")
    @patch("deployer.deploy.preflight.check_ecr_repositories")
    @patch("deployer.deploy.preflight.check_audit")
    def test_skip_all_only_validates_config(
        self, mock_audit, mock_ecr, mock_secrets, mock_cluster, tmp_path
    ):
        """With all checks skipped, only config validation runs."""
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text('[application]\nname = "test"')
        deploy_config = parse_deploy_config(deploy_toml)

        options = PreflightOptions(
            skip_ecr_check=True,
            skip_secrets_check=True,
            skip_cluster_check=True,
            skip_audit=True,
        )

        run_preflight_checks(
            deploy_config=deploy_config,
            env_config=make_env_config(),
            environment="test-staging",
            environment_type="staging",
            project_dir=tmp_path,
            options=options,
        )

        mock_audit.assert_not_called()
        mock_ecr.assert_not_called()
        mock_secrets.assert_not_called()
        mock_cluster.assert_not_called()


class TestCheckSecretsDrift:
    """Tests for check_secrets_drift function."""

    @patch("deployer.core.ssm_secrets.ssm.list_parameters")
    def test_detects_unreferenced_secrets(self, mock_list):
        """Should find SSM secrets not declared in deploy.toml."""
        mock_list.return_value = (
            [
                {"name": "/app/staging/secret-key"},
                {"name": "/app/staging/old-secret"},
                {"name": "/app/staging/another-old"},
            ],
            None,
        )

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"path_prefix": "/app/staging"}}

        result = check_secrets_drift(config, "staging", env_config)
        assert result == ["/app/staging/another-old", "/app/staging/old-secret"]

    @patch("deployer.core.ssm_secrets.ssm.list_parameters")
    def test_no_drift(self, mock_list):
        """Should return empty list when all secrets are declared."""
        mock_list.return_value = (
            [{"name": "/app/staging/secret-key"}],
            None,
        )

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"path_prefix": "/app/staging"}}

        result = check_secrets_drift(config, "staging", env_config)
        assert result == []

    def test_skips_legacy_format(self):
        """Should skip drift check for legacy ssm:/path format."""
        config = {"secrets": {"DB_PASSWORD": "ssm:/app/staging/db-password"}}
        env_config = {"secrets": {"path_prefix": "/app/staging"}}

        result = check_secrets_drift(config, "staging", env_config)
        assert result == []

    def test_skips_without_secrets_in_env_config(self):
        """Should skip drift check when env_config has no secrets config."""
        config = {"secrets": {"names": ["SECRET_KEY"]}}

        result = check_secrets_drift(config, "staging", {})
        assert result == []

    def test_skips_without_path_prefix(self):
        """Should skip drift check when path_prefix is missing."""
        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"provider": "ssm"}}

        result = check_secrets_drift(config, "staging", env_config)
        assert result == []

    @patch("deployer.core.ssm_secrets.ssm.list_parameters")
    def test_excludes_deployer_managed_parameters(self, mock_list):
        """Should exclude deployer-managed parameters like last-migrations-hash."""
        mock_list.return_value = (
            [
                {"name": "/app/staging/secret-key"},
                {"name": "/app/staging/last-migrations-hash"},
                {"name": "/app/staging/old-secret"},
            ],
            None,
        )

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"path_prefix": "/app/staging"}}

        result = check_secrets_drift(config, "staging", env_config)
        assert result == ["/app/staging/old-secret"]
        assert "/app/staging/last-migrations-hash" not in result

    @patch("deployer.core.ssm_secrets.ssm.list_parameters")
    def test_handles_list_error(self, mock_list):
        """Should return empty list on SSM list error."""
        mock_list.return_value = ([], "AccessDenied")

        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"path_prefix": "/app/staging"}}

        result = check_secrets_drift(config, "staging", env_config)
        assert result == []
