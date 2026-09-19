"""Tests for deployer.emergency.ecs module.

Backed by moto (``mocked_aws`` fixture) so request shapes are validated by
botocore; MagicMock is used only where moto has no equivalent (task
definition registration timestamps) or where an AWS-side error cannot be
provoked otherwise.

The error contract these tests assert is docs/internal/DECISIONS.md
§ "2026-08-18: Error Contracts" — raise, absence sentinel, or failure
sentinel, and one sentinel never means both:

* The four **queries** and ``wait_for_deployment`` raise ``RuntimeError``
  when a read fails, and keep ``None``/``[]``/``{}`` only for genuine
  absence — describe-services succeeding with an empty list.
* The three **mutators** keep ``False``, which is a documented *failure*
  sentinel their callers already branch on.
* ``compare_task_definitions`` has no ``except`` of its own and inherits the
  first rule, which is what stops an unreadable revision rendering as an
  empty diff in the rollback preview.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError

from deployer.emergency import ecs as ecs_module
from deployer.emergency.ecs import (
    _get_task_definition_details,
    compare_task_definitions,
    force_new_deployment,
    get_all_services_state,
    get_service_state,
    list_task_definition_revisions,
    scale_service,
    update_service_task_definition,
    wait_for_deployment,
)

REGION = "us-west-2"
CLUSTER = "test-cluster"
FAMILY = "web"
SERVICE = "web"

# Environment variables per task definition revision. Revision 2 changes FOO,
# drops REMOVED and adds ADDED; revision 3 introduces a second container.
REVISION_CONTAINERS = [
    [{"name": "app", "environment": {"FOO": "bar", "REMOVED": "x"}}],
    [{"name": "app", "environment": {"FOO": "baz", "ADDED": "y"}}],
    [
        {"name": "app", "environment": {"FOO": "baz", "ADDED": "y"}},
        {"name": "sidecar", "environment": {"SIDECAR": "on"}},
    ],
]


def register_revision(client, containers: list[dict]) -> str:
    """Register one task definition revision; return its ARN."""
    response = client.register_task_definition(
        family=FAMILY,
        cpu="256",
        memory="512",
        containerDefinitions=[
            {
                "name": container["name"],
                "image": "public.ecr.aws/nginx/nginx:latest",
                "environment": [
                    {"name": key, "value": value} for key, value in container["environment"].items()
                ],
            }
            for container in containers
        ],
    )
    return response["taskDefinition"]["taskDefinitionArn"]


@pytest.fixture
def ecs_cluster(mocked_aws) -> dict:
    """Create a cluster, three task definition revisions, and a service.

    Returns:
        Dict with cluster, service and the list of revision ARNs (oldest first).
    """
    client = boto3.client("ecs", region_name=REGION)
    client.create_cluster(clusterName=CLUSTER)

    arns = [register_revision(client, containers) for containers in REVISION_CONTAINERS]

    client.create_service(
        cluster=CLUSTER,
        serviceName=SERVICE,
        taskDefinition=arns[0],
        desiredCount=2,
    )

    return {"cluster": CLUSTER, "service": SERVICE, "arns": arns}


def _denied_client():
    """An ECS client whose every call is refused, as a permissions gap would.

    moto answers an unknown family with ClientError already, but a *known*
    revision the caller may not read is the case that matters here, and moto
    has no way to express it.
    """

    class _Denied:
        def __getattr__(self, _name):
            def _call(*_args, **_kwargs):
                raise ClientError(
                    {"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
                    "DescribeTaskDefinition",
                )

            return _call

    return _Denied()


class FakeClock:
    """Stand-in for the ``time`` module inside wait_for_deployment.

    Without this, the timeout tests would take the full 300 seconds of
    wall-clock that the production default asks for.
    """

    def __init__(self, start: float = 1_000.0):
        self.now = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TestGetServiceState:
    def test_returns_state(self, ecs_cluster):
        state = get_service_state(CLUSTER, SERVICE)

        assert state.task_definition == ecs_cluster["arns"][0]
        assert state.desired_count == 2
        assert state.running_count == 0

    def test_unknown_service_returns_none(self, ecs_cluster):
        assert get_service_state(CLUSTER, "no-such-service") is None

    def test_unknown_cluster_raises(self, mocked_aws):
        """A typo'd cluster is a failure, not a missing service."""
        with pytest.raises(RuntimeError, match="Could not read service 'web'"):
            get_service_state("no-such-cluster", SERVICE)

    def test_a_cluster_without_the_service_still_returns_none(self, ecs_cluster):
        """Absence survives: describe-services answers with an empty list."""
        assert get_service_state(CLUSTER, "no-such-service") is None


class TestGetAllServicesState:
    def test_returns_all_services(self, ecs_cluster):
        client = boto3.client("ecs", region_name=REGION)
        client.create_service(
            cluster=CLUSTER,
            serviceName="worker",
            taskDefinition=ecs_cluster["arns"][1],
            desiredCount=1,
        )

        states = get_all_services_state(CLUSTER)

        assert set(states) == {SERVICE, "worker"}
        assert states[SERVICE].desired_count == 2
        assert states["worker"].desired_count == 1
        assert states["worker"].task_definition == ecs_cluster["arns"][1]

    def test_empty_cluster_returns_empty_dict(self, mocked_aws):
        boto3.client("ecs", region_name=REGION).create_cluster(clusterName="empty-cluster")

        assert get_all_services_state("empty-cluster") == {}

    def test_unknown_cluster_raises(self, mocked_aws):
        """Same rule as get_service_state: {} means empty, never unreadable."""
        with pytest.raises(RuntimeError, match="Could not read services in cluster"):
            get_all_services_state("no-such-cluster")


class TestListTaskDefinitionRevisions:
    def test_lists_revisions_with_arns(self, ecs_cluster):
        revisions = list_task_definition_revisions(FAMILY)

        assert {item["revision"] for item in revisions} == {1, 2, 3}
        assert {item["arn"] for item in revisions} == set(ecs_cluster["arns"])
        assert {item["family"] for item in revisions} == {FAMILY}

    def test_max_results_is_forwarded_to_aws(self, ecs_cluster, mocker):
        """Truncation is AWS-side; moto honors neither maxResults nor sort.

        The function does no client-side limiting, so the only thing that can
        be asserted is that the request carries the caller's parameters.
        """
        client = boto3.client("ecs", region_name=REGION)
        spy = mocker.spy(client, "list_task_definitions")
        mocker.patch.object(ecs_module, "_get_ecs_client", return_value=client)

        list_task_definition_revisions(FAMILY, max_results=2)

        spy.assert_called_once_with(familyPrefix=FAMILY, sort="DESC", maxResults=2)

    def test_unknown_family_returns_empty_list(self, ecs_cluster):
        assert list_task_definition_revisions("no-such-family") == []

    def test_registered_at_from_describe(self, mocker):
        """registered_at is filled in from describe_task_definition.

        moto does not populate registeredAt, so the describe response is
        mocked here -- this is the only way to cover the formatting branch.
        """
        client = MagicMock()
        client.list_task_definitions.return_value = {
            "taskDefinitionArns": ["arn:aws:ecs:us-west-2:123456789012:task-definition/web:7"]
        }
        client.describe_task_definition.return_value = {
            "taskDefinition": {"registeredAt": datetime(2026, 2, 4, 10, 30, tzinfo=UTC)}
        }
        mocker.patch.object(ecs_module, "_get_ecs_client", return_value=client)

        revisions = list_task_definition_revisions(FAMILY)

        assert revisions[0]["revision"] == 7
        assert revisions[0]["registered_at"] == "2026-02-04T10:30:00+00:00"

    def test_describe_failure_leaves_registered_at_none(self, mocker):
        client = MagicMock()
        client.list_task_definitions.return_value = {
            "taskDefinitionArns": ["arn:aws:ecs:us-west-2:123456789012:task-definition/web:7"]
        }
        client.describe_task_definition.side_effect = ClientError(
            {"Error": {"Code": "ClientException", "Message": "boom"}}, "DescribeTaskDefinition"
        )
        mocker.patch.object(ecs_module, "_get_ecs_client", return_value=client)

        revisions = list_task_definition_revisions(FAMILY)

        assert revisions[0]["registered_at"] is None

    def test_list_failure_raises(self, mocker):
        """An API failure is not "no revisions to roll back to"."""
        client = MagicMock()
        client.list_task_definitions.side_effect = ClientError(
            {"Error": {"Code": "ClientException", "Message": "boom"}}, "ListTaskDefinitions"
        )
        mocker.patch.object(ecs_module, "_get_ecs_client", return_value=client)

        with pytest.raises(
            RuntimeError, match=f"Could not list task definitions for family '{FAMILY}'"
        ):
            list_task_definition_revisions(FAMILY)


class TestGetTaskDefinitionDetails:
    def test_returns_details_with_environment(self, ecs_cluster):
        details = _get_task_definition_details(ecs_cluster["arns"][2])

        assert details.arn == ecs_cluster["arns"][2]
        assert details.family == FAMILY
        assert details.revision == 3
        assert details.cpu == "256"
        assert details.memory == "512"
        assert details.environment_variables == {
            "app": {"FOO": "baz", "ADDED": "y"},
            "sidecar": {"SIDECAR": "on"},
        }

    def test_unknown_task_definition_raises(self, ecs_cluster):
        """The caller got this ARN from a listing; failing to read it is an error."""
        with pytest.raises(RuntimeError, match="Could not read task definition"):
            _get_task_definition_details("no-such-family:1")


class TestCompareTaskDefinitions:
    def test_detects_added_removed_and_changed(self, ecs_cluster):
        arn1, arn2 = ecs_cluster["arns"][0], ecs_cluster["arns"][1]

        diff = compare_task_definitions(arn1, arn2)

        assert diff == {
            "app": {
                "added": {"ADDED": "y"},
                "removed": {"REMOVED": "x"},
                "changed": {"FOO": {"old": "bar", "new": "baz"}},
            }
        }

    def test_detects_new_container(self, ecs_cluster):
        diff = compare_task_definitions(ecs_cluster["arns"][1], ecs_cluster["arns"][2])

        assert diff == {"sidecar": {"added": {"SIDECAR": "on"}, "removed": {}, "changed": {}}}

    def test_identical_definitions_produce_no_diff(self, ecs_cluster):
        assert compare_task_definitions(ecs_cluster["arns"][0], ecs_cluster["arns"][0]) == {}

    def test_unknown_definition_raises(self, ecs_cluster):
        """An unreadable side is no longer reported as "nothing changed"."""
        with pytest.raises(RuntimeError, match="Could not read task definition"):
            compare_task_definitions(ecs_cluster["arns"][0], "no-such-family:1")

    def test_a_denied_read_raises_rather_than_producing_an_empty_diff(self, ecs_cluster, mocker):
        """Fixed for free by the producer, having no except of its own.

        compare_task_definitions() has no except of its own — it inherited
        _get_task_definition_details()'s sentinel, so a permissions gap and
        "identical revisions" were the same answer. bin/emergency.py renders
        this diff to the operator immediately before a production rollback.
        """
        arns = ecs_cluster["arns"]
        mocker.patch.object(ecs_module, "_get_ecs_client", return_value=_denied_client())

        with pytest.raises(RuntimeError, match="Could not read task definition"):
            compare_task_definitions(arns[0], arns[1])


class TestUpdateServiceTaskDefinition:
    def test_updates_service(self, ecs_cluster):
        target = ecs_cluster["arns"][2]

        assert update_service_task_definition(CLUSTER, SERVICE, target) is True

        assert get_service_state(CLUSTER, SERVICE).task_definition == target

    def test_unknown_cluster_returns_false(self, ecs_cluster):
        # A mutator: False is the documented failure sentinel, and cmd_rollback
        # branches on it to log "Failed to update service" and exit 1.
        assert update_service_task_definition("no-such-cluster", SERVICE, "web:1") is False


class TestScaleService:
    def test_scales_service(self, ecs_cluster):
        assert scale_service(CLUSTER, SERVICE, 5) is True

        assert get_service_state(CLUSTER, SERVICE).desired_count == 5

    def test_scale_to_zero(self, ecs_cluster):
        assert scale_service(CLUSTER, SERVICE, 0) is True

        assert get_service_state(CLUSTER, SERVICE).desired_count == 0

    def test_unknown_cluster_returns_false(self, ecs_cluster):
        # A mutator: False is the documented failure sentinel.
        assert scale_service("no-such-cluster", SERVICE, 1) is False

    def test_delegates_to_the_single_implementation(self, monkeypatch):
        """There is one update_service call for scaling, in deployer.aws.ecs.

        This module used to carry a byte-identical copy of it -- the repo's
        only pylint R0801 block. The pin is here so a second copy cannot come
        back without a test going red.
        """
        calls = []
        monkeypatch.setattr(
            ecs_module.aws_ecs, "scale_service", lambda *args: calls.append(args) or True
        )

        assert scale_service(CLUSTER, SERVICE, 3) is True
        assert calls == [(CLUSTER, SERVICE, 3)]


class TestForceNewDeployment:
    def test_forces_deployment(self, ecs_cluster):
        assert force_new_deployment(CLUSTER, SERVICE) is True

    def test_unknown_cluster_returns_false(self, ecs_cluster):
        # A mutator: False is the documented failure sentinel.
        assert force_new_deployment("no-such-cluster", SERVICE) is False


class TestWaitForDeployment:
    def test_returns_true_when_already_stable(self, ecs_cluster, mocker):
        clock = FakeClock()
        mocker.patch.object(ecs_module, "time", clock)
        scale_service(CLUSTER, SERVICE, 0)
        polls = []

        result = wait_for_deployment(
            CLUSTER, SERVICE, callback=lambda running, desired: polls.append((running, desired))
        )

        assert result is True
        assert polls == [(0, 0)]
        assert clock.sleeps == []

    def test_times_out_while_unstable(self, ecs_cluster, mocker):
        """desiredCount 2 with no container instances never reaches running == desired."""
        clock = FakeClock()
        mocker.patch.object(ecs_module, "time", clock)
        polls = []

        result = wait_for_deployment(
            CLUSTER,
            SERVICE,
            timeout=30,
            poll_interval=10,
            callback=lambda running, desired: polls.append((running, desired)),
        )

        assert result is False
        assert polls == [(0, 2), (0, 2), (0, 2)]
        assert clock.sleeps == [10, 10, 10]

    def test_missing_service_polls_until_timeout(self, ecs_cluster, mocker):
        """An absent service is retried for the full timeout, never reported."""
        clock = FakeClock()
        mocker.patch.object(ecs_module, "time", clock)
        polls = []

        result = wait_for_deployment(
            CLUSTER,
            "no-such-service",
            timeout=30,
            poll_interval=10,
            callback=lambda running, desired: polls.append((running, desired)),
        )

        # Left standing deliberately: describe-services *succeeds*
        # here and answers with an empty list, so this is the absence branch,
        # not the failure one. A service that has not appeared yet is exactly
        # what a caller waiting on a new deployment is waiting for.
        assert result is False
        assert polls == []
        assert clock.sleeps == [10, 10, 10]

    def test_client_error_raises_on_the_first_poll(self, mocked_aws, mocker):
        """The worst instance of the swallow, and the reason for the rule.

        This used to swallow the error and keep polling, so a service the
        caller could not see was watched for the entire timeout and then
        reported as "did not stabilize" — indistinguishable from a deployment
        that genuinely did not. It now fails on the first poll, and never
        sleeps: botocore has already retried a throttled request by the time
        a ClientError reaches here.
        """
        clock = FakeClock()
        mocker.patch.object(ecs_module, "time", clock)
        polls = []

        with pytest.raises(RuntimeError, match="Could not watch deployment of 'web'"):
            wait_for_deployment(
                "no-such-cluster",
                SERVICE,
                timeout=20,
                poll_interval=10,
                callback=lambda running, desired: polls.append((running, desired)),
            )

        assert polls == []
        assert clock.sleeps == []
