"""Tests for deployer.deploy.images module."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from deployer.config import ImageConfig
from deployer.deploy.images import format_missing_ecr_error, validate_ecr_repositories


class TestValidateEcrRepositories:
    """Tests for validate_ecr_repositories function."""

    def test_all_repositories_exist(self):
        """Test that empty list is returned when all repos exist."""
        mock_ecr = MagicMock()
        # describe_repositories succeeds for both repos
        mock_ecr.describe_repositories.return_value = {
            "repositories": [{"repositoryName": "myapp-web"}]
        }

        config = {
            "images": {
                "web": {"context": ".", "push": True},
                "worker": {"context": ".", "push": True},
            }
        }

        missing = validate_ecr_repositories(mock_ecr, config, "myapp")

        assert missing == []
        assert mock_ecr.describe_repositories.call_count == 2

    def test_missing_repositories(self):
        """Test that missing repository names are returned."""
        mock_ecr = MagicMock()

        def describe_repos(repositoryNames):  # noqa: N803 — matches boto3 API
            repo_name = repositoryNames[0]
            if repo_name == "myapp-web":
                return {"repositories": [{"repositoryName": repo_name}]}
            else:
                raise ClientError(
                    {"Error": {"Code": "RepositoryNotFoundException", "Message": "not found"}},
                    "DescribeRepositories",
                )

        mock_ecr.describe_repositories.side_effect = describe_repos

        config = {
            "images": {
                "web": {"context": "."},
                "worker": {"context": "."},
            }
        }

        missing = validate_ecr_repositories(mock_ecr, config, "myapp")

        assert missing == ["myapp-worker"]

    def test_skip_local_only_images(self):
        """Test that images with push=False are not checked."""
        mock_ecr = MagicMock()
        mock_ecr.describe_repositories.return_value = {
            "repositories": [{"repositoryName": "myapp-web"}]
        }

        config = {
            "images": {
                "base": {"context": "./base", "push": False},  # Local only
                "web": {"context": ".", "push": True},
            }
        }

        missing = validate_ecr_repositories(mock_ecr, config, "myapp")

        assert missing == []
        # Only web should be checked, not base
        assert mock_ecr.describe_repositories.call_count == 1
        mock_ecr.describe_repositories.assert_called_with(repositoryNames=["myapp-web"])

    def test_empty_config(self):
        """Test that empty images config returns empty list."""
        mock_ecr = MagicMock()

        config = {"images": {}}

        missing = validate_ecr_repositories(mock_ecr, config, "myapp")

        assert missing == []
        mock_ecr.describe_repositories.assert_not_called()

    def test_no_images_section(self):
        """Test that missing images section returns empty list."""
        mock_ecr = MagicMock()

        config = {}

        missing = validate_ecr_repositories(mock_ecr, config, "myapp")

        assert missing == []
        mock_ecr.describe_repositories.assert_not_called()

    def test_default_push_value(self):
        """Test that missing push key defaults to True (image is checked)."""
        mock_ecr = MagicMock()
        mock_ecr.describe_repositories.side_effect = ClientError(
            {"Error": {"Code": "RepositoryNotFoundException", "Message": "not found"}},
            "DescribeRepositories",
        )

        # No push key specified - should default to True
        config = {
            "images": {
                "web": {"context": "."},
            }
        }

        missing = validate_ecr_repositories(mock_ecr, config, "myapp")

        # Should check because push defaults to True
        assert missing == ["myapp-web"]
        mock_ecr.describe_repositories.assert_called_once()

    def test_unexpected_client_error_propagates(self):
        """Test that unexpected AWS errors are re-raised."""
        mock_ecr = MagicMock()
        mock_ecr.describe_repositories.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access Denied"}},
            "DescribeRepositories",
        )

        config = {
            "images": {
                "web": {"context": "."},
            }
        }

        with pytest.raises(ClientError) as exc_info:
            validate_ecr_repositories(mock_ecr, config, "myapp")

        assert exc_info.value.response["Error"]["Code"] == "AccessDeniedException"


class TestFormatMissingEcrError:
    """Tests for format_missing_ecr_error function."""

    def test_single_missing_repo(self):
        """Test error message for single missing repository."""
        message = format_missing_ecr_error(["myapp-web"], "myapp-staging")

        assert "myapp-web" in message
        assert "myapp-staging" in message
        assert "tofu.sh" in message or "tofu" in message.lower()
        assert "aws ecr create-repository" in message

    def test_multiple_missing_repos(self):
        """Test error message for multiple missing repositories."""
        message = format_missing_ecr_error(["myapp-web", "myapp-worker"], "myapp-staging")

        assert "myapp-web" in message
        assert "myapp-worker" in message
        assert message.count("aws ecr create-repository") == 2


class TestImageConfigGetTarget:
    """Tests for ImageConfig.get_target method."""

    def test_no_target_returns_none(self):
        """Test that missing target returns None."""
        img = ImageConfig(name="web", context=".")
        assert img.get_target("staging") is None

    def test_string_target_returns_value(self):
        """Test that string target returns the value for any environment."""
        img = ImageConfig(name="web", context=".", target="prod")
        assert img.get_target("staging") == "prod"
        assert img.get_target("production") == "prod"

    def test_dict_target_returns_environment_value(self):
        """Test that dict target returns environment-specific value."""
        img = ImageConfig(
            name="web",
            context=".",
            target={"staging": "dev", "production": "prod"},
        )
        assert img.get_target("staging") == "dev"
        assert img.get_target("production") == "prod"

    def test_dict_target_missing_environment_returns_none(self):
        """Test that dict target returns None for undefined environment."""
        img = ImageConfig(
            name="web",
            context=".",
            target={"staging": "dev"},
        )
        assert img.get_target("production") is None
