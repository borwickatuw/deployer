"""Tests for AWS profile validation utilities."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

from deployer.utils import aws_profile
from deployer.utils.aws_profile import get_environment_aws_profile, validate_aws_profile


class TestGetEnvironmentAwsProfile:
    """None means "no profile configured"; an unreadable config.toml is not that."""

    def test_the_configured_profile_is_returned(self, tmp_path):
        (tmp_path / "config.toml").write_text(
            '[aws]\ninfra_profile = "myapp-infra"\n', encoding="utf-8"
        )
        assert get_environment_aws_profile(tmp_path, "infra") == "myapp-infra"

    def test_no_config_toml_is_none(self, tmp_path):
        assert get_environment_aws_profile(tmp_path, "infra") is None

    def test_no_profile_key_is_none(self, tmp_path):
        (tmp_path / "config.toml").write_text("[aws]\n", encoding="utf-8")
        assert get_environment_aws_profile(tmp_path, "deploy") is None

    def test_a_malformed_config_toml_raises_rather_than_reading_as_unset(self, tmp_path):
        """It used to answer None, silently selecting the default profile."""
        (tmp_path / "config.toml").write_text("[aws\ninfra_profile = ", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Could not read the AWS profile from"):
            get_environment_aws_profile(tmp_path, "infra")

    def test_configure_does_not_fall_back_to_the_default_profile(self, tmp_path, monkeypatch):
        """The boundary half: configure_profile_or_exit catches this RuntimeError."""
        (tmp_path / "config.toml").write_text("not = [valid", encoding="utf-8")
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        monkeypatch.setattr(aws_profile, "get_environment_path", lambda _env: tmp_path)

        with pytest.raises(RuntimeError, match="config.toml"):
            aws_profile.configure_aws_profile_for_environment("infra", "myapp-staging")
        assert "AWS_PROFILE" not in aws_profile.os.environ


class TestValidateAwsProfile:
    """Tests for validate_aws_profile function."""

    def test_valid_profile(self):
        """Test successful validation with working credentials."""
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/test",
            "UserId": "AIDAEXAMPLE",
        }

        mock_session = MagicMock()
        mock_session.client.return_value = mock_sts

        with patch("boto3.Session", return_value=mock_session):
            success, error = validate_aws_profile("my-profile")

        assert success is True
        assert error is None
        mock_session.client.assert_called_once_with("sts")
        mock_sts.get_caller_identity.assert_called_once()

    def test_profile_not_found(self):
        """Test error handling when profile doesn't exist."""
        with patch(
            "boto3.Session",
            side_effect=ProfileNotFound(profile="missing-profile"),
        ):
            success, error = validate_aws_profile("missing-profile")

        assert success is False
        assert error is not None
        assert "missing-profile" in error
        assert "not found" in error
        assert "~/.aws/config" in error

    def test_no_credentials_error(self):
        """Test error handling when profile has no credentials."""
        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = NoCredentialsError()
        mock_session.client.return_value = mock_sts

        with patch("boto3.Session", return_value=mock_session):
            success, error = validate_aws_profile("no-creds-profile")

        assert success is False
        assert error is not None
        assert "no-creds-profile" in error
        assert "No credentials found" in error

    def test_expired_token(self):
        """Test error handling when credentials have expired."""
        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ExpiredToken",
                    "Message": "The security token included in the request is expired",
                }
            },
            "GetCallerIdentity",
        )
        mock_session.client.return_value = mock_sts

        with patch("boto3.Session", return_value=mock_session):
            success, error = validate_aws_profile("expired-profile")

        assert success is False
        assert error is not None
        assert "expired-profile" in error
        assert "expired" in error
        assert "aws sso login" in error

    def test_access_denied(self):
        """Test error handling when credentials lack permissions."""
        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "User: arn:aws:iam::123:user/test is not authorized",
                }
            },
            "GetCallerIdentity",
        )
        mock_session.client.return_value = mock_sts

        with patch("boto3.Session", return_value=mock_session):
            success, error = validate_aws_profile("denied-profile")

        assert success is False
        assert error is not None
        assert "denied-profile" in error
        assert "invalid" in error.lower() or "denied" in error.lower()

    def test_other_client_error(self):
        """Test error handling for unexpected AWS errors."""
        mock_session = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = ClientError(
            {
                "Error": {
                    "Code": "ServiceException",
                    "Message": "Internal service error",
                }
            },
            "GetCallerIdentity",
        )
        mock_session.client.return_value = mock_sts

        with patch("boto3.Session", return_value=mock_session):
            success, error = validate_aws_profile("error-profile")

        assert success is False
        assert error is not None
        assert "error-profile" in error
