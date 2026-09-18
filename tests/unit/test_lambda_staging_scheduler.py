"""Tests for ``modules/staging-scheduler/lambda/handler.py``.

This 180-line first-party Lambda had no tests and was not in PY_SOURCES, so no
linter or formatter had ever seen it either. It scales a whole staging
environment up and down on a schedule, unattended, with nobody reading the
output -- which is exactly why its error contract matters more than most:

- A step that failed must be reported as a failure. The handler used to write
  every error into the results dict and answer 200, so EventBridge recorded a
  success for an invocation that left the environment half-scaled.
- A failed status *lookup* must not read as a status. ``_get_rds_status``
  used to return "unknown" both for a state it does not handle and for an
  AWS call it could not make; the callers then "skipped" a running instance.
- Only AWS failures are absorbed. A bug in this handler escapes, so the
  invocation errors instead of reporting the bug as a scaling outcome.

The module is loaded by path (it is a Lambda bundle root, not a package) with
``boto3.client`` stubbed, because the handler builds both clients at import
time and the test environment has no region.
"""

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

_HANDLER_PATH = (
    Path(__file__).resolve().parents[2] / "modules" / "staging-scheduler" / "lambda" / "handler.py"
)
_spec = spec_from_file_location("staging_scheduler_handler", _HANDLER_PATH)
scheduler = module_from_spec(_spec)
with patch("boto3.client", lambda _service: None):
    _spec.loader.exec_module(scheduler)


def _client_error(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "not authorized"}}, operation)


class FakeEcs:
    """Record every update_service call; raise for services named in `fails`."""

    def __init__(self, fails=()):
        self.calls: list[dict] = []
        self.fails = set(fails)

    def update_service(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["service"] in self.fails:
            raise _client_error("UpdateService")
        return {}


class FakeRds:
    """Answer describe/start/stop with canned statuses or canned exceptions."""

    def __init__(self, status="available", describe_error=None, action_error=None):
        self.status = status
        self.describe_error = describe_error
        self.action_error = action_error
        self.stopped: list[str] = []
        self.started: list[str] = []

    # boto3 passes DBInstanceIdentifier as a PascalCase keyword, which is not a
    # legal parameter name under pep8-naming; take it out of **kwargs instead.
    def describe_db_instances(self, **kwargs):
        if self.describe_error:
            raise self.describe_error
        if self.status is None:
            return {"DBInstances": []}
        return {"DBInstances": [{"DBInstanceStatus": self.status}]}

    def stop_db_instance(self, **kwargs):
        if self.action_error:
            raise self.action_error
        self.stopped.append(kwargs["DBInstanceIdentifier"])
        return {}

    def start_db_instance(self, **kwargs):
        if self.action_error:
            raise self.action_error
        self.started.append(kwargs["DBInstanceIdentifier"])
        return {}


@pytest.fixture
def aws(monkeypatch):
    """Install fake ECS/RDS clients; return a setter for per-test variants."""
    installed: dict = {}

    def _install(ecs=None, rds=None):
        installed["ecs"] = ecs or FakeEcs()
        installed["rds"] = rds or FakeRds()
        monkeypatch.setattr(scheduler, "ecs_client", installed["ecs"])
        monkeypatch.setattr(scheduler, "rds_client", installed["rds"])
        monkeypatch.setattr(scheduler.time, "sleep", lambda _s: None)
        return installed

    return _install


@pytest.fixture
def env(monkeypatch):
    """Set the three environment variables the handler reads."""
    monkeypatch.setenv("ECS_CLUSTER_NAME", "myapp-staging-cluster")
    monkeypatch.setenv("ECS_SERVICES", json.dumps({"web": {"replicas": 2}, "worker": {}}))
    monkeypatch.setenv("RDS_INSTANCE_ID", "myapp-staging-db")


def _body(response: dict) -> dict:
    return json.loads(response["body"])


class TestGetEnvVars:
    """Tests for get_env_vars()."""

    def test_reads_all_three(self, env):
        cluster, services, rds_id = scheduler.get_env_vars()
        assert cluster == "myapp-staging-cluster"
        assert services == {"web": {"replicas": 2}, "worker": {}}
        assert rds_id == "myapp-staging-db"

    @pytest.mark.parametrize("missing", ["ECS_CLUSTER_NAME", "ECS_SERVICES", "RDS_INSTANCE_ID"])
    def test_a_missing_variable_is_a_value_error(self, env, monkeypatch, missing):
        """ECS_SERVICES used to raise KeyError while its two siblings raised ValueError.

        Only ValueError is turned into a 500 with the reason, so the odd one
        out escaped as an unexplained invocation failure.
        """
        monkeypatch.delenv(missing)
        with pytest.raises(ValueError, match=missing):
            scheduler.get_env_vars()

    def test_unparseable_services_json_names_the_variable(self, env, monkeypatch):
        monkeypatch.setenv("ECS_SERVICES", "{not json")
        with pytest.raises(ValueError, match="Invalid ECS_SERVICES JSON"):
            scheduler.get_env_vars()


class TestGetRdsStatus:
    """A failed lookup is never reported as a status."""

    def test_returns_the_status(self, aws):
        aws(rds=FakeRds(status="available"))
        assert scheduler._get_rds_status("db") == "available"

    def test_a_describe_failure_raises_rather_than_answering_unknown(self, aws):
        aws(rds=FakeRds(describe_error=_client_error("DescribeDBInstances")))
        with pytest.raises(ClientError):
            scheduler._get_rds_status("db")

    def test_no_described_instance_raises(self, aws):
        aws(rds=FakeRds(status=None))
        with pytest.raises(RuntimeError, match="No RDS instance described"):
            scheduler._get_rds_status("db")


class TestStopEnvironment:
    """Tests for stop_environment()."""

    def test_scales_every_service_to_zero_and_stops_the_database(self, aws):
        state = aws(rds=FakeRds(status="available"))
        results = scheduler.stop_environment("c", {"web": {"replicas": 2}, "worker": {}}, "db")

        assert [c["desiredCount"] for c in state["ecs"].calls] == [0, 0]
        assert state["rds"].stopped == ["db"]
        assert results == {
            "ecs": {"web": "scaled to 0", "worker": "scaled to 0"},
            "rds": "stop initiated",
        }

    def test_an_already_stopped_database_is_left_alone(self, aws):
        state = aws(rds=FakeRds(status="stopped"))
        results = scheduler.stop_environment("c", {}, "db")
        assert results["rds"] == "already stopped"
        assert state["rds"].stopped == []

    def test_an_unhandled_state_is_skipped_and_named(self, aws):
        aws(rds=FakeRds(status="backing-up"))
        assert scheduler.stop_environment("c", {}, "db")["rds"] == "skipped (status: backing-up)"

    def test_a_status_read_failure_is_an_error_not_a_skip(self, aws):
        """The distinction the old "unknown" sentinel erased.

        A running instance whose status could not be read used to come back as
        "skipped (status: unknown)" -- indistinguishable from a deliberate
        skip, and billed for all night.
        """
        aws(rds=FakeRds(describe_error=_client_error("DescribeDBInstances")))
        assert scheduler.stop_environment("c", {}, "db")["rds"].startswith("error: ")

    def test_one_failing_service_does_not_strand_the_others(self, aws):
        state = aws(ecs=FakeEcs(fails={"web"}))
        results = scheduler.stop_environment("c", {"web": {}, "worker": {}}, "db")

        assert [c["service"] for c in state["ecs"].calls] == ["web", "worker"]
        assert results["ecs"]["web"].startswith("error: ")
        assert results["ecs"]["worker"] == "scaled to 0"


class TestStartEnvironment:
    """Tests for start_environment()."""

    def test_starts_the_database_before_scaling_and_honours_replicas(self, aws):
        state = aws(rds=FakeRds(status="stopped"))
        results = scheduler.start_environment("c", {"web": {"replicas": 3}, "worker": {}}, "db")

        assert state["rds"].started == ["db"]
        assert [c["desiredCount"] for c in state["ecs"].calls] == [3, 1]
        assert results == {
            "ecs": {"web": "scaled to 3", "worker": "scaled to 1"},
            "rds": "start initiated",
        }

    def test_an_already_running_database_is_left_alone(self, aws):
        state = aws(rds=FakeRds(status="available"))
        assert scheduler.start_environment("c", {}, "db")["rds"] == "already running"
        assert state["rds"].started == []

    def test_a_start_failure_is_recorded_as_an_error(self, aws):
        aws(rds=FakeRds(status="stopped", action_error=_client_error("StartDBInstance")))
        assert scheduler.start_environment("c", {}, "db")["rds"].startswith("error: ")

    def test_a_connectivity_failure_is_caught_like_a_client_error(self, aws):
        """BotoCoreError, not just ClientError: a timeout is still an AWS failure."""
        aws(rds=FakeRds(describe_error=EndpointConnectionError(endpoint_url="https://rds")))
        assert scheduler.start_environment("c", {}, "db")["rds"].startswith("error: ")


class TestUnexpectedErrorsEscape:
    """Only the AWS failure modes are absorbed; a bug in the handler is loud."""

    def test_a_non_aws_exception_from_ecs_propagates(self, aws):
        class Broken:
            def update_service(self, **_kwargs):
                raise TypeError("update_service() got an unexpected keyword argument")

        aws(ecs=Broken())
        with pytest.raises(TypeError):
            scheduler.stop_environment("c", {"web": {}}, "db")

    def test_a_non_aws_exception_from_rds_propagates(self, aws):
        aws(rds=FakeRds(status=None))
        with pytest.raises(RuntimeError, match="No RDS instance described"):
            scheduler.stop_environment("c", {}, "db")


class TestHandler:
    """Tests for handler()'s status codes."""

    @pytest.mark.parametrize("action", ["", "restart", None])
    def test_an_unknown_action_is_400(self, action):
        event = {} if action is None else {"action": action}
        response = scheduler.handler(event, None)
        assert response["statusCode"] == 400
        assert "Invalid action" in _body(response)["error"]

    def test_the_action_is_case_insensitive(self, aws, env):
        aws(rds=FakeRds(status="available"))
        assert scheduler.handler({"action": "STOP"}, None)["statusCode"] == 200

    def test_a_configuration_error_is_500_with_the_reason(self, aws, env, monkeypatch):
        aws()
        monkeypatch.delenv("RDS_INSTANCE_ID")
        response = scheduler.handler({"action": "stop"}, None)
        assert response["statusCode"] == 500
        assert "RDS_INSTANCE_ID" in _body(response)["error"]

    def test_a_fully_successful_stop_is_200_with_no_failures(self, aws, env):
        aws(rds=FakeRds(status="available"))
        response = scheduler.handler({"action": "stop"}, None)
        assert response["statusCode"] == 200
        assert _body(response)["failures"] == []
        assert _body(response)["results"]["rds"] == "stop initiated"

    def test_a_failed_service_makes_the_whole_invocation_500(self, aws, env):
        """The contract this file exists to pin.

        Every error used to be written into the results dict under a 200, so
        an invocation that left half the environment running looked to
        EventBridge exactly like one that worked.
        """
        aws(ecs=FakeEcs(fails={"web"}), rds=FakeRds(status="available"))
        response = scheduler.handler({"action": "stop"}, None)

        assert response["statusCode"] == 500
        assert _body(response)["failures"] == ["ecs:web"]
        assert _body(response)["results"]["ecs"]["worker"] == "scaled to 0"

    def test_a_failed_database_step_makes_the_invocation_500(self, aws, env):
        aws(rds=FakeRds(status="available", action_error=_client_error("StopDBInstance")))
        response = scheduler.handler({"action": "stop"}, None)
        assert response["statusCode"] == 500
        assert _body(response)["failures"] == ["rds"]

    def test_a_skipped_database_state_is_not_a_failure(self, aws, env):
        aws(rds=FakeRds(status="backing-up"))
        response = scheduler.handler({"action": "stop"}, None)
        assert response["statusCode"] == 200
        assert _body(response)["failures"] == []

    def test_start_reports_its_action_back(self, aws, env):
        aws(rds=FakeRds(status="stopped"))
        response = scheduler.handler({"action": "start"}, None)
        assert response["statusCode"] == 200
        assert _body(response)["action"] == "start"
