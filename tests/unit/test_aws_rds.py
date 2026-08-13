"""Tests for deployer.aws.rds.

These assert on the argv emitted to the `aws` CLI. The `aws_cli` fixture
patches run_command at the deployer.aws.cli seam, so no `aws` binary and no
credentials are involved.
"""

import json

import pytest

from deployer.aws import rds
from deployer.utils import AWS_REGION

INSTANCE = "myapp-staging-db"


def describe(status: str) -> str:
    """A describe-db-instances body for an instance in the given status."""
    return json.dumps(
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": INSTANCE,
                    "DBInstanceStatus": status,
                    "DBInstanceClass": "db.t4g.micro",
                    "Engine": "postgres",
                    "EngineVersion": "16.3",
                }
            ]
        }
    )


AVAILABLE = describe("available")


class TestGetStatus:
    """Tests for get_status."""

    def test_emitted_argv(self, aws_cli):
        """describe-db-instances is called with the identifier, region last."""
        aws_cli.replies((True, AVAILABLE))
        rds.get_status(INSTANCE)
        assert aws_cli.argv == [
            "aws",
            "rds",
            "describe-db-instances",
            "--db-instance-identifier",
            INSTANCE,
            "--region",
            AWS_REGION,
        ]

    def test_flattens_the_first_instance(self, aws_cli):
        """The four fields callers use are lifted out of the AWS response."""
        aws_cli.replies((True, AVAILABLE))
        assert rds.get_status(INSTANCE) == {
            "identifier": INSTANCE,
            "status": "available",
            "instance_class": "db.t4g.micro",
            "engine": "postgres 16.3",
        }

    def test_engine_without_version(self, aws_cli):
        """A response with no EngineVersion still produces an engine string."""
        aws_cli.replies(
            (
                True,
                json.dumps(
                    {
                        "DBInstances": [
                            {
                                "DBInstanceIdentifier": INSTANCE,
                                "DBInstanceStatus": "stopped",
                                "DBInstanceClass": "db.t4g.micro",
                                "Engine": "postgres",
                            }
                        ]
                    }
                ),
            )
        )
        assert rds.get_status(INSTANCE)["engine"] == "postgres "

    def test_none_when_command_fails(self, aws_cli):
        """A failed describe answers None — the instance may not exist."""
        aws_cli.replies((False, "DBInstanceNotFound"))
        assert rds.get_status(INSTANCE) is None

    def test_none_when_no_instances(self, aws_cli):
        """An empty DBInstances list answers None."""
        aws_cli.replies((True, json.dumps({"DBInstances": []})))
        assert rds.get_status(INSTANCE) is None

    def test_none_when_output_is_not_json(self, aws_cli):
        """Unparseable output answers None instead of raising JSONDecodeError."""
        aws_cli.replies((True, "not json"))
        assert rds.get_status(INSTANCE) is None


class TestInstanceActions:
    """Tests for the two identifier-only instance operations."""

    @pytest.mark.parametrize(
        ("func", "operation"),
        [(rds.stop, "stop-db-instance"), (rds.start, "start-db-instance")],
    )
    def test_emitted_argv(self, aws_cli, func, operation):
        """Each maps to its own rds subcommand and nothing else."""
        func(INSTANCE)
        assert aws_cli.argv == [
            "aws",
            "rds",
            operation,
            "--db-instance-identifier",
            INSTANCE,
            "--region",
            AWS_REGION,
        ]

    @pytest.mark.parametrize("func", [rds.stop, rds.start])
    def test_reports_success(self, aws_cli, func):
        """A successful command returns True."""
        assert func(INSTANCE) is True

    @pytest.mark.parametrize("func", [rds.stop, rds.start])
    def test_reports_failure(self, aws_cli, func):
        """A failed command returns False and swallows the error text."""
        aws_cli.replies((False, "InvalidDBInstanceState"))
        assert func(INSTANCE) is False


class TestWaitForStatus:
    """Tests for wait_for_status polling."""

    @pytest.fixture(autouse=True)
    def _no_sleeping(self, monkeypatch):
        """Make poll_interval free so the tests run at full speed."""
        monkeypatch.setattr(rds.time, "sleep", lambda _seconds: None)

    def test_returns_true_once_the_target_is_reached(self, aws_cli):
        """Polling stops as soon as the instance reports the target status."""
        aws_cli.replies(
            (True, describe("stopping")),
            (True, AVAILABLE),
        )
        assert rds.wait_for_status(INSTANCE, "available", None) is True
        assert len(aws_cli.calls) == 2

    def test_reports_each_status_to_the_callback(self, aws_cli):
        """The callback sees every non-target status, including 'unknown'."""
        seen = []
        aws_cli.replies(
            (False, "throttled"),
            (True, describe("stopping")),
            (True, AVAILABLE),
        )
        assert rds.wait_for_status(INSTANCE, "available", seen.append) is True
        assert seen == ["unknown", "stopping"]

    def test_returns_false_on_timeout(self, aws_cli):
        """A zero timeout never polls and reports failure."""
        aws_cli.replies((True, AVAILABLE))
        assert rds.wait_for_status(INSTANCE, "available", None, timeout=0) is False
        assert aws_cli.calls == []
