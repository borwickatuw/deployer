"""Tests for deployer.aws.cli — the shared `aws` CLI surface.

These assert on the emitted argv. run_command is monkeypatched at the
deployer.aws.cli seam, so no `aws` binary and no credentials are involved.
"""

import pytest

from deployer.aws import cli
from deployer.utils import AWS_REGION


@pytest.fixture
def emitted(monkeypatch):
    """Capture the argv passed to run_command; replies success with no output."""
    calls = []

    def fake_run_command(cmd, cwd=None):
        calls.append(cmd)
        return True, ""

    monkeypatch.setattr(cli, "run_command", fake_run_command)
    return calls


def reply(monkeypatch, success, output):
    """Make run_command return a fixed (success, output) and record the argv."""
    calls = []

    def fake_run_command(cmd, cwd=None):
        calls.append(cmd)
        return success, output

    monkeypatch.setattr(cli, "run_command", fake_run_command)
    return calls


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

    def test_emits_the_built_argv(self, emitted):
        """run_aws runs exactly what aws_command builds."""
        cli.run_aws("rds", "stop-db-instance", "--db-instance-identifier", "db-1")
        assert emitted == [
            [
                "aws",
                "rds",
                "stop-db-instance",
                "--db-instance-identifier",
                "db-1",
                "--region",
                AWS_REGION,
            ]
        ]

    def test_returns_output_on_success(self, monkeypatch):
        """Success passes stdout through unchanged."""
        reply(monkeypatch, True, '{"ok": true}')
        assert cli.run_aws("sts", "get-caller-identity") == (True, '{"ok": true}')

    def test_returns_error_text_on_failure(self, monkeypatch):
        """Failure passes the error text through — callers grep it."""
        reply(monkeypatch, False, "An error occurred: UserNotFoundException")
        success, output = cli.run_aws("cognito-idp", "admin-delete-user")
        assert success is False
        assert "UserNotFoundException" in output


class TestRunAwsJson:
    """Tests for run_aws_json."""

    def test_parses_json_output(self, monkeypatch):
        """A successful command's JSON body is returned as a dict."""
        reply(monkeypatch, True, '{"DBInstances": [{"DBInstanceStatus": "available"}]}')
        assert cli.run_aws_json("rds", "describe-db-instances") == {
            "DBInstances": [{"DBInstanceStatus": "available"}]
        }

    def test_emits_the_built_argv(self, monkeypatch):
        """The argv is built the same way as for run_aws."""
        emitted = reply(monkeypatch, True, "{}")
        cli.run_aws_json("cognito-idp", "describe-user-pool", "--user-pool-id", "pool-1")
        assert emitted == [
            [
                "aws",
                "cognito-idp",
                "describe-user-pool",
                "--user-pool-id",
                "pool-1",
                "--region",
                AWS_REGION,
            ]
        ]

    def test_none_when_command_fails(self, monkeypatch):
        """A failed command answers None rather than raising."""
        reply(monkeypatch, False, "AccessDeniedException")
        assert cli.run_aws_json("rds", "describe-db-instances") is None

    def test_none_when_output_is_not_json(self, monkeypatch):
        """Unparseable output answers None too — both failures look the same."""
        reply(monkeypatch, True, "not json at all")
        assert cli.run_aws_json("rds", "describe-db-instances") is None

    def test_none_when_output_is_empty(self, monkeypatch):
        """An empty body is not valid JSON, so it answers None."""
        reply(monkeypatch, True, "")
        assert cli.run_aws_json("rds", "describe-db-instances") is None
