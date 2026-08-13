"""Tests for deployer.aws.cli — the shared `aws` CLI surface.

These assert on the emitted argv. The `aws_cli` fixture patches run_command at
this module's seam, so no `aws` binary and no credentials are involved.
"""

from deployer.aws import cli
from deployer.utils import AWS_REGION


class TestAwsCommand:
    """Tests for aws_command argv construction."""

    def test_prefixes_aws_and_appends_region(self):
        """The argv starts with 'aws' and ends with the region flag."""
        assert cli.aws_command("rds", "describe-db-instances") == [
            "aws",
            "rds",
            "describe-db-instances",
            "--region",
            AWS_REGION,
        ]

    def test_preserves_argument_order(self):
        """Arguments land between 'aws' and --region in the order given."""
        assert cli.aws_command("logs", "get-log-events", "--limit", "100") == [
            "aws",
            "logs",
            "get-log-events",
            "--limit",
            "100",
            "--region",
            AWS_REGION,
        ]

    def test_no_arguments(self):
        """With no command words the argv is just 'aws' and the region."""
        assert cli.aws_command() == ["aws", "--region", AWS_REGION]


class TestRunAws:
    """Tests for run_aws."""

    def test_emits_the_built_argv(self, aws_cli):
        """run_aws runs exactly what aws_command builds."""
        cli.run_aws("rds", "stop-db-instance", "--db-instance-identifier", "db-1")
        assert aws_cli.argv == [
            "aws",
            "rds",
            "stop-db-instance",
            "--db-instance-identifier",
            "db-1",
            "--region",
            AWS_REGION,
        ]

    def test_returns_output_on_success(self, aws_cli):
        """Success passes stdout through unchanged."""
        aws_cli.replies((True, '{"ok": true}'))
        assert cli.run_aws("sts", "get-caller-identity") == (True, '{"ok": true}')

    def test_returns_error_text_on_failure(self, aws_cli):
        """Failure passes the error text through — callers grep it."""
        aws_cli.replies((False, "An error occurred: UserNotFoundException"))
        success, output = cli.run_aws("cognito-idp", "admin-delete-user")
        assert success is False
        assert "UserNotFoundException" in output


class TestRunAwsJson:
    """Tests for run_aws_json."""

    def test_parses_json_output(self, aws_cli):
        """A successful command's JSON body is returned as a dict."""
        aws_cli.replies((True, '{"DBInstances": [{"DBInstanceStatus": "available"}]}'))
        assert cli.run_aws_json("rds", "describe-db-instances") == {
            "DBInstances": [{"DBInstanceStatus": "available"}]
        }

    def test_emits_the_built_argv(self, aws_cli):
        """The argv is built the same way as for run_aws."""
        cli.run_aws_json("cognito-idp", "describe-user-pool", "--user-pool-id", "pool-1")
        assert aws_cli.argv == [
            "aws",
            "cognito-idp",
            "describe-user-pool",
            "--user-pool-id",
            "pool-1",
            "--region",
            AWS_REGION,
        ]

    def test_none_when_command_fails(self, aws_cli):
        """A failed command answers None rather than raising."""
        aws_cli.replies((False, "AccessDeniedException"))
        assert cli.run_aws_json("rds", "describe-db-instances") is None

    def test_none_when_output_is_not_json(self, aws_cli):
        """Unparseable output answers None too — both failures look the same."""
        aws_cli.replies((True, "not json at all"))
        assert cli.run_aws_json("rds", "describe-db-instances") is None

    def test_none_when_output_is_empty(self, aws_cli):
        """An empty body is not valid JSON, so it answers None."""
        aws_cli.replies((True, ""))
        assert cli.run_aws_json("rds", "describe-db-instances") is None
