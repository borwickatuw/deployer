"""Tests for deployer.deploy.preflight module."""

from unittest.mock import patch

import pytest

from deployer.config import parse_deploy_config
from deployer.core.ssm_secrets import check_secrets_drift
from deployer.deploy.context import EnvironmentTarget
from deployer.deploy.preflight import (
    PreflightError,
    PreflightOptions,
    check_ecr_repositories,
    check_ecs_cluster,
    check_environment_config,
    check_modules,
    check_secrets_style,
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


def make_target(env_config=None, name="test-staging", env_type="staging"):
    """Wrap an env config in the EnvironmentTarget the checks now take."""
    return EnvironmentTarget(
        name, env_type, make_env_config() if env_config is None else env_config
    )


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
        check_ecr_repositories(deploy_config, make_target(config))

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
            check_ecr_repositories(deploy_config, make_target())

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
        check_ecr_repositories(deploy_config, make_target())


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
                target=make_target({}),  # Missing infrastructure section
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
            target=make_target(),
            project_dir=tmp_path,
            options=options,
        )

        mock_audit.assert_not_called()
        mock_ecr.assert_not_called()
        mock_secrets.assert_not_called()
        mock_cluster.assert_not_called()


class TestCheckSecretsStyle:
    """The check that rejects the removed explicit-secret-path form.

    This is what makes 53h-2a's deletion a hard error rather than a silent
    behaviour change. A deploy.toml written in the old style used to reach the
    task definition, where every secret was dropped if any module section was
    also declared -- and nothing caught it: preflight confirmed the SSM
    parameters existed, and the audit counted the explicit keys as provided.
    """

    @staticmethod
    def _config(tmp_path, toml: str):
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text(f'[application]\nname = "test"\n{toml}')
        return parse_deploy_config(deploy_toml)

    def test_the_names_form_passes(self, tmp_path):
        check_secrets_style(self._config(tmp_path, '[secrets]\nnames = ["SECRET_KEY"]\n'))

    def test_no_secrets_section_passes(self, tmp_path):
        check_secrets_style(self._config(tmp_path, ""))

    def test_an_empty_secrets_section_passes(self, tmp_path):
        check_secrets_style(self._config(tmp_path, "[secrets]\n"))

    def test_an_ssm_path_is_rejected(self, tmp_path):
        config = self._config(
            tmp_path,
            '[secrets]\nSECRET_KEY = "ssm:/app/staging/secret-key"\n',  # pragma: allowlist secret
        )
        with pytest.raises(PreflightError, match="explicit-path form"):
            check_secrets_style(config)

    def test_a_secretsmanager_arn_is_rejected(self, tmp_path):
        config = self._config(
            tmp_path,
            '[secrets]\nDB_PASSWORD = "secretsmanager:arn:aws:x"\n',  # pragma: allowlist secret
        )
        with pytest.raises(PreflightError, match="DB_PASSWORD"):
            check_secrets_style(config)

    def test_the_message_spells_out_the_replacement(self, tmp_path):
        config = self._config(
            tmp_path,
            '[secrets]\nSECRET_KEY = "ssm:/a"\nAPI_TOKEN = "ssm:/b"\n',  # pragma: allowlist secret
        )
        with pytest.raises(PreflightError) as excinfo:
            check_secrets_style(config)
        message = str(excinfo.value)
        assert 'names = ["API_TOKEN", "SECRET_KEY"]' in message
        assert "path_prefix" in message

    def test_mixing_the_two_forms_is_rejected_too(self, tmp_path):
        # The exact shape that silently dropped everything: a `names` list the
        # module system honours, plus explicit keys it never reads.
        config = self._config(
            tmp_path,
            '[secrets]\nnames = ["SECRET_KEY"]\nOTHER = "ssm:/b"\n',  # pragma: allowlist secret
        )
        with pytest.raises(PreflightError, match="OTHER"):
            check_secrets_style(config)

    def test_it_runs_before_the_module_and_secret_checks(self, tmp_path):
        # Ordering matters: check_ssm_secrets would otherwise raise the same
        # rejection from underneath, without the migration heading.
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text(
            '[application]\nname = "test"\n'
            '[secrets]\nSECRET_KEY = "ssm:/a"\n'  # pragma: allowlist secret
        )
        with pytest.raises(PreflightError, match="explicit-path form"):
            run_preflight_checks(
                deploy_config=parse_deploy_config(deploy_toml),
                target=make_target(),
                project_dir=tmp_path,
                options=PreflightOptions(),
            )


class TestCheckModules:
    """Characterization pins for check_modules.

    Phase 53h-2a wrote these before adding a second secrets-related check
    beside this one. ``check_modules`` had **no test at all** despite being one
    of only two checks ``run_preflight_checks`` always runs, so what it does
    and does not reject was unrecorded.

    Driven through ``check_modules(deploy_config, env_config)`` -- the outermost
    boundary -- with a real ``DeployConfig`` parsed from a real deploy.toml, so
    the pins also cover ``DeployConfig.get_raw_dict()``'s round trip of the
    module sections.
    """

    @staticmethod
    def _config(tmp_path, toml: str):
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text(f'[application]\nname = "test"\n{toml}')
        return parse_deploy_config(deploy_toml)

    def test_a_deploy_toml_declaring_no_modules_passes(self, tmp_path):
        check_modules(self._config(tmp_path, ""), make_env_config())

    def test_a_valid_module_pair_passes(self, tmp_path):
        deploy_config = self._config(tmp_path, '[secrets]\nnames = ["SECRET_KEY"]\n')
        env_config = make_env_config(secrets={"provider": "ssm", "path_prefix": "/myapp/staging"})
        check_modules(deploy_config, env_config)

    def test_a_module_the_environment_does_not_provide_is_rejected(self, tmp_path):
        deploy_config = self._config(tmp_path, '[secrets]\nnames = ["SECRET_KEY"]\n')
        with pytest.raises(PreflightError, match="missing from config.toml"):
            check_modules(deploy_config, make_env_config())

    def test_the_heading_names_the_failing_stage(self, tmp_path):
        deploy_config = self._config(tmp_path, '[secrets]\nnames = ["secret_key"]\n')
        with pytest.raises(PreflightError, match="Resource module validation failed"):
            check_modules(deploy_config, make_env_config())

    def test_every_module_s_errors_are_reported_together(self, tmp_path):
        deploy_config = self._config(
            tmp_path,
            '[secrets]\nnames = ["lowercase"]\n[database]\ntype = "postgresql"\n',
        )
        with pytest.raises(PreflightError) as excinfo:
            check_modules(deploy_config, make_env_config())
        message = str(excinfo.value)
        assert "[secrets]" in message
        assert "[database]" in message

    def test_a_section_the_registry_does_not_implement_is_never_validated(self, tmp_path):
        # Pinned, not endorsed: [cdn] was deleted at bdb5891 but a deploy.toml
        # may still carry the section, and nothing here objects to it. It does
        # not survive DeployConfig.get_raw_dict() either, so it cannot reach
        # the task definition -- it is simply inert.
        deploy_config = self._config(tmp_path, '[cdn]\ntype = "not-a-real-type"\n')
        check_modules(deploy_config, make_env_config())

    def test_an_empty_module_section_is_skipped_rather_than_validated(self, tmp_path):
        # validate_all only calls a module whose app section is truthy, so an
        # empty [secrets] table asks nothing of config.toml.
        check_modules(self._config(tmp_path, "[secrets]\n"), make_env_config())


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
