"""Tests for the database extensions module.

Every error branch of create_database_extensions() ends in a
"log_error, print remediation advice, raise RuntimeError" block, and the advice
text is the whole point of those blocks. The TestAdviceBlocks tests below pin
that text and its order so that adopting print_with_advice() and decomposing
the function can be shown to preserve it.

Blank-line placement is deliberately not pinned: _lines() drops blank lines the
way tests/unit/test_init_cli.py's helper of the same name does, because
print_with_advice() emits a leading blank line that today's code does not.

"pinned, not endorsed": the invoke path catches bare `Exception` and reports
every non-ClientError failure as "Unexpected error invoking Lambda", so a bug
raised inside boto3 is presented to the operator as a credentials or network
problem. Whether it should catch at all is an error-contract question decided
in docs/internal/DECISIONS.md § "2026-08-18: Error Contracts", layer 4: the
advice block is reserved for BotoCoreError, which is the connectivity and
configuration family it actually describes.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from deployer.deploy.extensions import create_database_extensions
from deployer.utils import Colors

EXTENSIONS_LAMBDA = "myapp-staging-create-db-users"


def _lines(capsys):
    """Return stdout's non-blank lines.

    Blank lines are dropped on purpose — see the module docstring.
    """
    return [line for line in capsys.readouterr().out.splitlines() if line.strip()]


def _error(message: str) -> str:
    """Return the line log_error() prints for `message`."""
    return f"  {Colors.RED}✗{Colors.NC} {message}"


def _info(message: str) -> str:
    """Return the line log() prints for `message`."""
    return f"{Colors.BLUE}{message}{Colors.NC}"


def _assert_blank_separated(capsys):
    """Assert the error line is framed by a blank line on each side.

    This is print_with_advice()'s framing, adopted in Phase 53e-1 commit 2.
    """
    out = capsys.readouterr().out.splitlines()
    error_index = next(i for i, line in enumerate(out) if "✗" in line)
    assert out[error_index - 1] == ""
    assert out[error_index + 1] == ""


def _success(message: str) -> str:
    """Return the line log_success() prints for `message`."""
    return f"  {message} {Colors.GREEN}[done]{Colors.NC}"


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


class TestAdviceBlocks:
    """Pin the operator-facing advice printed by every failure path."""

    @pytest.mark.parametrize(
        ("env_config", "side_effect", "match"),
        [
            ({"host": "db.example.com"}, None, "Missing extensions_lambda"),
            (
                {"extensions_lambda": EXTENSIONS_LAMBDA},
                ClientError(
                    {"Error": {"Code": "ResourceNotFoundException", "Message": "gone"}}, "Invoke"
                ),
                "not found",
            ),
            (
                {"extensions_lambda": EXTENSIONS_LAMBDA},
                ClientError(
                    {"Error": {"Code": "AccessDeniedException", "Message": "nope"}}, "Invoke"
                ),
                "Access denied",
            ),
            (
                {"extensions_lambda": EXTENSIONS_LAMBDA},
                EndpointConnectionError(endpoint_url="https://lambda.us-west-2.amazonaws.com"),
                "Failed to invoke extensions Lambda",
            ),
        ],
    )
    @patch("deployer.deploy.extensions.boto3")
    def test_advice_blocks_are_blank_separated(
        self, mock_boto3, env_config, side_effect, match, capsys
    ):
        """Every advice block opens with a blank line, the print_with_advice framing."""
        mock_boto3.client.return_value.invoke.side_effect = side_effect
        config = {"database": {"extensions": ["unaccent"]}}

        with pytest.raises(RuntimeError, match=match):
            create_database_extensions(config, {"database": env_config}, "us-west-2")

        _assert_blank_separated(capsys)

    @patch("deployer.deploy.extensions.boto3")
    def test_function_error_block_is_blank_separated(self, mock_boto3, capsys):
        """The fifth advice block gets the same framing as the four above."""
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps({"errorType": "DatabaseError"}).encode()
        mock_boto3.client.return_value.invoke.return_value = {
            "FunctionError": "Unhandled",
            "Payload": mock_payload,
        }
        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        with pytest.raises(RuntimeError, match="Extensions Lambda failed"):
            create_database_extensions(config, env_config, "us-west-2")

        _assert_blank_separated(capsys)

    def test_missing_lambda_advice(self, capsys):
        """Missing extensions_lambda prints the deploy.toml/config.toml fix."""
        config = {"database": {"extensions": ["unaccent", "pg_bigm"]}}
        env_config = {"database": {"host": "db.example.com"}}

        with pytest.raises(RuntimeError, match="Missing extensions_lambda"):
            create_database_extensions(config, env_config, "us-west-2")

        assert _lines(capsys) == [
            _info("Creating database extensions: unaccent, pg_bigm"),
            _error(
                "deploy.toml declares database extensions, but config.toml is missing "
                "[database] extensions_lambda."
            ),
            "  Your deploy.toml declares:",
            "    [database]",
            '    extensions = ["unaccent", "pg_bigm"]',
            "  But your environment's config.toml needs:",
            "    [database]",
            '    extensions_lambda = "${tofu:db_users_lambda_function_name}"',
            "  Steps to fix:",
            "    1. Add the extensions_lambda line to your config.toml",
            "    2. Add the db_users_lambda_function_name output to your main.tf",
            "    3. Run 'tofu apply' to create the output",
        ]

    @patch("deployer.deploy.extensions.boto3")
    def test_resource_not_found_advice(self, mock_boto3, capsys):
        """A missing Lambda points the operator at tofu apply."""
        mock_boto3.client.return_value.invoke.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Function not found"}},
            "Invoke",
        )
        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        with pytest.raises(RuntimeError, match="not found"):
            create_database_extensions(config, env_config, "us-west-2")

        assert _lines(capsys)[1:] == [
            _error(f"Lambda function '{EXTENSIONS_LAMBDA}' not found."),
            "  The extensions_lambda in your config.toml points to a Lambda",
            "  function that doesn't exist. This usually means:",
            "    - The tofu output has not been applied yet",
            "    - The Lambda function was deleted",
            "  Run 'tofu apply' in your environment directory, then retry.",
        ]

    @patch("deployer.deploy.extensions.boto3")
    def test_access_denied_advice(self, mock_boto3, capsys):
        """A permissions failure points the operator at the bootstrap IAM apply."""
        mock_boto3.client.return_value.invoke.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}},
            "Invoke",
        )
        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        with pytest.raises(RuntimeError, match="Access denied"):
            create_database_extensions(config, env_config, "us-west-2")

        assert _lines(capsys)[1:] == [
            _error(f"Permission denied invoking Lambda '{EXTENSIONS_LAMBDA}'."),
            "  The deploy role does not have lambda:InvokeFunction permission",
            "  for this Lambda function. Apply the bootstrap IAM changes:",
            "    cd deployer-environments/bootstrap-staging",
            "    tofu apply",
        ]

    @patch("deployer.deploy.extensions.boto3")
    def test_generic_client_error_advice(self, mock_boto3, capsys):
        """An unrecognised AWS error code is reported with no advice lines."""
        mock_boto3.client.return_value.invoke.side_effect = ClientError(
            {"Error": {"Code": "ServiceException", "Message": "Internal error"}},
            "Invoke",
        )
        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        with pytest.raises(RuntimeError, match="Lambda invocation failed"):
            create_database_extensions(config, env_config, "us-west-2")

        assert _lines(capsys)[1:] == [
            _error(
                f"Failed to invoke Lambda '{EXTENSIONS_LAMBDA}': "
                "ServiceException - Internal error"
            ),
        ]

    @patch("deployer.deploy.extensions.boto3")
    def test_a_connectivity_failure_gets_the_credentials_and_network_advice(
        self, mock_boto3, capsys
    ):
        """BotoCoreError *is* the connectivity family, so the advice fits."""
        error = EndpointConnectionError(endpoint_url="https://lambda.us-west-2.amazonaws.com")
        mock_boto3.client.return_value.invoke.side_effect = error
        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        with pytest.raises(RuntimeError, match="Failed to invoke extensions Lambda"):
            create_database_extensions(config, env_config, "us-west-2")

        assert _lines(capsys)[1:] == [
            _error(f"Could not reach Lambda '{EXTENSIONS_LAMBDA}': {error}"),
            "  Check your AWS credentials and network connectivity.",
        ]

    @patch("deployer.deploy.extensions.boto3")
    def test_an_unattributable_error_is_not_blamed_on_the_network(self, mock_boto3, capsys):
        """A bug inside boto3, or in this module, is no longer the operator's network.

        The old `except Exception` caught anything at all and printed "Check
        your AWS credentials and network connectivity", which is advice the
        operator cannot act on when the fault is in our code.
        """
        mock_boto3.client.return_value.invoke.side_effect = TypeError("not JSON serializable")
        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        with pytest.raises(TypeError, match="not JSON serializable"):
            create_database_extensions(config, env_config, "us-west-2")

        assert "network connectivity" not in capsys.readouterr().out

    @patch("deployer.deploy.extensions.boto3")
    def test_function_error_advice(self, mock_boto3, capsys):
        """A Lambda-level error prints its message and the CloudWatch tail command."""
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(
            {"errorType": "DatabaseError", "errorMessage": "could not connect"}
        ).encode()
        mock_boto3.client.return_value.invoke.return_value = {
            "FunctionError": "Unhandled",
            "Payload": mock_payload,
        }
        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        with pytest.raises(RuntimeError, match="Extensions Lambda failed"):
            create_database_extensions(config, env_config, "us-west-2")

        assert _lines(capsys)[1:] == [
            _error(f"Lambda '{EXTENSIONS_LAMBDA}' returned an error: DatabaseError"),
            "  could not connect",
            "  The Lambda function ran but failed to create extensions.",
            "  Check the Lambda's CloudWatch logs for details:",
            f"    aws logs tail /aws/lambda/{EXTENSIONS_LAMBDA} --since 5m",
        ]

    @patch("deployer.deploy.extensions.boto3")
    def test_success_logs_the_created_extensions(self, mock_boto3, capsys):
        """A successful invocation reports what the Lambda says it created."""
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(
            {"status": "success", "extensions": ["unaccent", "pg_bigm"]}
        ).encode()
        mock_boto3.client.return_value.invoke.return_value = {"Payload": mock_payload}
        config = {"database": {"extensions": ["unaccent", "pg_bigm"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        create_database_extensions(config, env_config, "us-west-2")

        assert _lines(capsys)[-1] == _success("Extensions ready: unaccent, pg_bigm")

    def test_dry_run_advice(self, capsys):
        """Dry run names the Lambda it would have invoked."""
        config = {"database": {"extensions": ["unaccent"]}}
        env_config = {"database": {"extensions_lambda": EXTENSIONS_LAMBDA}}

        with patch("deployer.deploy.extensions.boto3"):
            create_database_extensions(config, env_config, "us-west-2", dry_run=True)

        assert _lines(capsys)[-1] == (
            f"  {Colors.YELLOW}⚠{Colors.NC} DRY RUN: Would invoke Lambda "
            f"'{EXTENSIONS_LAMBDA}' to create extensions: ['unaccent']"
        )
