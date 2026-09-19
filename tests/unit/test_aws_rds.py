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
        assert rds.get_status(INSTANCE) == rds.RdsStatus(
            identifier=INSTANCE,
            status="available",
            instance_class="db.t4g.micro",
            engine="postgres 16.3",
        )

    def test_answers_a_named_tuple_not_a_dict(self, aws_cli):
        """Readers in bin/ reach the fields by attribute, not by key."""
        aws_cli.replies((True, AVAILABLE))
        result = rds.get_status(INSTANCE)
        assert isinstance(result, rds.RdsStatus)
        assert (result.status, result.instance_class) == ("available", "db.t4g.micro")
        with pytest.raises(TypeError):
            result["status"]

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
        assert rds.get_status(INSTANCE).engine == "postgres "

    def test_none_when_the_instance_does_not_exist(self, aws_cli):
        """Only DBInstanceNotFound answers None: absence, not failure."""
        aws_cli.replies((False, "An error occurred (DBInstanceNotFound) when calling ..."))
        assert rds.get_status(INSTANCE) is None

    def test_raises_when_the_describe_fails_for_any_other_reason(self, aws_cli):
        """A throttle or an expired credential is "I could not look", not "absent"."""
        aws_cli.replies((False, "An error occurred (ThrottlingException) when calling ..."))
        with pytest.raises(RuntimeError, match="Could not describe RDS instance"):
            rds.get_status(INSTANCE)

    def test_none_when_no_instances(self, aws_cli):
        """An empty DBInstances list answers None."""
        aws_cli.replies((True, json.dumps({"DBInstances": []})))
        assert rds.get_status(INSTANCE) is None

    def test_raises_when_output_is_not_json(self, aws_cli):
        """Unparseable output is a failure to look, not an absent instance."""
        aws_cli.replies((True, "not json"))
        with pytest.raises(RuntimeError, match="Could not parse"):
            rds.get_status(INSTANCE)


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

    def test_default_cadence_comes_from_the_module_constant(self, aws_cli, monkeypatch):
        """The default sleep is POLL_INTERVAL_SECONDS, not a literal 15.

        Retuning the cadence has to move this wait; a literal in the signature
        would let it drift away from the constant emergency.rds shares.
        """
        slept: list[int] = []
        monkeypatch.setattr(rds.time, "sleep", slept.append)
        aws_cli.replies(
            (True, describe("stopping")),
            (True, AVAILABLE),
        )

        assert rds.wait_for_status(INSTANCE, "available", None) is True
        assert slept == [rds.POLL_INTERVAL_SECONDS]
