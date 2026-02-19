"""Tests for the database extensions module."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from deployer.deploy.extensions import create_database_extensions


class TestCreateDatabaseExtensions:
    """Tests for create_database_extensions()."""

    def test_skips_when_no_extensions(self):
        """No-op when deploy.toml has no extensions."""
        config = {"database": {"type": "postgresql"}}
        env_config = {"database": {}}

        # Should return without doing anything
        create_database_extensions(config, env_config, "us-west-2")

    def test_skips_when_no_database_section(self):
        """No-op when deploy.toml has no database section."""
        config = {}
        env_config = {}

        create_database_extensions(config, env_config, "us-west-2")

    def test_skips_when_extensions_empty(self):
        """No-op when extensions list is empty."""
        config = {"database": {"type": "postgresql", "extensions": []}}
        env_config = {"database": {}}

        create_database_extensions(config, env_config, "us-west-2")

    def test_fails_fast_when_lambda_missing(self):
        """Fails with clear error when extensions declared but no lambda name."""
        config = {"database": {"type": "postgresql", "extensions": ["unaccent"]}}
        env_config = {"database": {"host": "db.example.com"}}

        with pytest.raises(RuntimeError, match="Missing extensions_lambda"):
            create_database_extensions(config, env_config, "us-west-2")

    def test_dry_run_skips_invocation(self):
        """Dry run logs but does not invoke Lambda."""
        config = {"database": {"extensions": ["unaccent", "pg_bigm"]}}
        env_config = {"database": {"extensions_lambda": "myapp-staging-create-db-users"}}

        with patch("deployer.deploy.extensions.boto3") as mock_boto3:
            create_database_extensions(config, env_config, "us-west-2", dry_run=True)
            mock_boto3.client.assert_not_called()

    @patch("deployer.deploy.extensions.boto3")
    def test_invokes_lambda_with_correct_payload(self, mock_boto3):
        """Invokes Lambda with action=create_extensions and the extensions list."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        # Mock successful response
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(
            {"status": "success", "extensions": ["unaccent", "pg_bigm"]}
        ).encode()
        mock_client.invoke.return_value = {"Payload": mock_payload}

        config = {"database": {"extensions": ["unaccent", "pg_bigm"]}}
        env_config = {"database": {"extensions_lambda": "myapp-staging-create-db-users"}}

        create_database_extensions(config, env_config, "us-west-2")

        mock_boto3.client.assert_called_once_with("lambda", region_name="us-west-2")
        mock_client.invoke.assert_called_once_with(
            FunctionName="myapp-staging-create-db-users",
            InvocationType="RequestResponse",
            Payload=json.dumps(
                {
                    "action": "create_extensions",
                    "extensions": ["unaccent", "pg_bigm"],
                }
            ),
        )

    @patch("deployer.deploy.extensions.boto3")
    def test_handles_resource_not_found(self, mock_boto3):
        """Clear error when Lambda function doesn't exist."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        mock_client.invoke.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Function not found"}},
            "Invoke",
        )

        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": "nonexistent-lambda"}}

        with pytest.raises(RuntimeError, match="not found"):
            create_database_extensions(config, env_config, "us-west-2")

    @patch("deployer.deploy.extensions.boto3")
    def test_handles_access_denied(self, mock_boto3):
        """Clear error when IAM permissions are missing."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        mock_client.invoke.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}},
            "Invoke",
        )

        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": "myapp-staging-create-db-users"}}

        with pytest.raises(RuntimeError, match="Access denied"):
            create_database_extensions(config, env_config, "us-west-2")

    @patch("deployer.deploy.extensions.boto3")
    def test_handles_lambda_function_error(self, mock_boto3):
        """Clear error when Lambda runs but returns a function error."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(
            {"errorType": "DatabaseError", "errorMessage": "could not connect"}
        ).encode()
        mock_client.invoke.return_value = {
            "FunctionError": "Unhandled",
            "Payload": mock_payload,
        }

        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": "myapp-staging-create-db-users"}}

        with pytest.raises(RuntimeError, match="Extensions Lambda failed"):
            create_database_extensions(config, env_config, "us-west-2")

    @patch("deployer.deploy.extensions.boto3")
    def test_handles_generic_client_error(self, mock_boto3):
        """Clear error for unexpected AWS errors."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        mock_client.invoke.side_effect = ClientError(
            {"Error": {"Code": "ServiceException", "Message": "Internal error"}},
            "Invoke",
        )

        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": "myapp-staging-create-db-users"}}

        with pytest.raises(RuntimeError, match="Lambda invocation failed"):
            create_database_extensions(config, env_config, "us-west-2")
