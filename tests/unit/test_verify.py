"""Tests for deployer.init.verify — tool version checks and AWS profile validation."""

from unittest.mock import patch

from deployer.init.verify import (
    _check_aws_profiles,
    _check_deployer_config,
    _check_tools,
    _parse_version,
    cmd_verify,
)


class TestParseVersion:
    def test_two_part(self):
        assert _parse_version("3.12") == (3, 12)

    def test_three_part(self):
        assert _parse_version("1.6.2") == (1, 6, 2)

    def test_single_part(self):
        assert _parse_version("2") == (2,)


class TestCheckTools:
    @patch("deployer.init.verify.run_command")
    def test_all_tools_pass(self, mock_run):
        mock_run.side_effect = [
            (True, "Python 3.12.9"),
            (True, "uv 0.6.0"),
            (True, "OpenTofu v1.8.0"),
            (True, "aws-cli/2.0.0"),
            (True, "Docker version 27.0.0"),
        ]
        assert _check_tools() is True

    @patch("deployer.init.verify.run_command")
    def test_missing_tool(self, mock_run):
        mock_run.side_effect = [
            (True, "Python 3.12.9"),
            (False, "command not found"),
            (True, "OpenTofu v1.8.0"),
            (True, "aws-cli/2.0.0"),
            (True, "Docker version 27.0.0"),
        ]
        assert _check_tools() is False

    @patch("deployer.init.verify.run_command")
    def test_version_too_low(self, mock_run):
        mock_run.side_effect = [
            (True, "Python 3.10.0"),  # requires >= 3.12
            (True, "uv 0.6.0"),
            (True, "OpenTofu v1.8.0"),
            (True, "aws-cli/2.0.0"),
            (True, "Docker version 27.0.0"),
        ]
        assert _check_tools() is False

    @patch("deployer.init.verify.run_command")
    def test_aws_v1_fails(self, mock_run):
        mock_run.side_effect = [
            (True, "Python 3.12.9"),
            (True, "uv 0.6.0"),
            (True, "OpenTofu v1.8.0"),
            (True, "aws-cli/1.29.0"),  # requires v2
            (True, "Docker version 27.0.0"),
        ]
        assert _check_tools() is False


class TestCheckDeployerConfig:
    @patch("deployer.init.bootstrap.bootstrap_dir_exists", return_value="bootstrap-staging")
    @patch("deployer.utils.environment.get_environments_dir")
    @patch.dict("os.environ", {"DEPLOYER_ENVIRONMENTS_DIR": "/tmp/envs"})
    def test_all_configured(self, mock_envdir, mock_bootstrap):
        from pathlib import Path

        mock_envdir.return_value = Path("/tmp/envs")
        assert _check_deployer_config() is True

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_env_var(self):
        # Remove DEPLOYER_ENVIRONMENTS_DIR if present
        import os

        os.environ.pop("DEPLOYER_ENVIRONMENTS_DIR", None)
        assert _check_deployer_config() is False

    @patch("deployer.init.bootstrap.bootstrap_dir_exists", return_value=None)
    @patch("deployer.utils.environment.get_environments_dir")
    @patch.dict("os.environ", {"DEPLOYER_ENVIRONMENTS_DIR": "/tmp/envs"})
    def test_no_bootstrap_dir(self, mock_envdir, mock_bootstrap):
        from pathlib import Path

        mock_envdir.return_value = Path("/tmp/envs")
        assert _check_deployer_config() is False


class TestCheckAwsProfiles:
    @patch("deployer.utils.aws_profile.validate_aws_profile")
    def test_all_profiles_pass(self, mock_validate):
        mock_validate.return_value = (True, None)
        assert _check_aws_profiles() is True
        assert mock_validate.call_count == 3

    @patch("deployer.utils.aws_profile.validate_aws_profile")
    def test_one_profile_fails(self, mock_validate):
        mock_validate.side_effect = [
            (True, None),
            (False, "Profile 'deployer-infra' not found."),
            (True, None),
        ]
        assert _check_aws_profiles() is False


class TestCmdVerify:
    @patch("deployer.init.verify._check_bootstrap_plan")
    @patch("deployer.init.verify._check_aws_profiles")
    @patch("deployer.init.verify._check_deployer_config")
    @patch("deployer.init.verify._check_tools")
    def test_all_pass(self, mock_tools, mock_config, mock_aws, mock_plan):
        mock_tools.return_value = True
        mock_config.return_value = True
        mock_aws.return_value = True
        mock_plan.return_value = True
        assert cmd_verify() == 0

    @patch("deployer.init.verify._check_deployer_config")
    @patch("deployer.init.verify._check_aws_profiles")
    @patch("deployer.init.verify._check_tools")
    def test_tools_fail_skips_rest(self, mock_tools, mock_aws, mock_config):
        mock_tools.return_value = False
        assert cmd_verify() == 1
        mock_config.assert_not_called()
        mock_aws.assert_not_called()

    @patch("deployer.init.verify._check_bootstrap_plan")
    @patch("deployer.init.verify._check_aws_profiles")
    @patch("deployer.init.verify._check_deployer_config")
    @patch("deployer.init.verify._check_tools")
    def test_aws_fail(self, mock_tools, mock_config, mock_aws, mock_plan):
        mock_tools.return_value = True
        mock_config.return_value = True
        mock_aws.return_value = False
        assert cmd_verify() == 1
        mock_plan.assert_not_called()

    @patch("deployer.init.verify._check_bootstrap_plan")
    @patch("deployer.init.verify._check_aws_profiles")
    @patch("deployer.init.verify._check_deployer_config")
    @patch("deployer.init.verify._check_tools")
    def test_plan_skipped_still_passes(self, mock_tools, mock_config, mock_aws, mock_plan):
        mock_tools.return_value = True
        mock_config.return_value = True
        mock_aws.return_value = True
        mock_plan.return_value = None  # skipped
        assert cmd_verify() == 0

    @patch("deployer.init.verify._check_bootstrap_plan")
    @patch("deployer.init.verify._check_aws_profiles")
    @patch("deployer.init.verify._check_deployer_config")
    @patch("deployer.init.verify._check_tools")
    def test_plan_drift_fails(self, mock_tools, mock_config, mock_aws, mock_plan):
        mock_tools.return_value = True
        mock_config.return_value = True
        mock_aws.return_value = True
        mock_plan.return_value = False  # drift detected
        assert cmd_verify() == 1
