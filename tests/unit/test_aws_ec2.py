"""Tests for deployer.aws.ec2: the GPU container instance's lifecycle calls."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from deployer.aws import ec2

INSTANCE = "i-0123456789abcdef0"


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "no"}}, operation)


@pytest.fixture
def client():
    return MagicMock()


class TestGetInstanceState:
    def test_returns_the_state_name(self, client):
        client.describe_instances.return_value = {
            "Reservations": [
                {"Instances": [{"InstanceId": INSTANCE, "State": {"Name": "stopped"}}]}
            ]
        }
        assert ec2.get_instance_state(INSTANCE, ec2_client=client) == "stopped"
        client.describe_instances.assert_called_once_with(InstanceIds=[INSTANCE])

    def test_no_such_instance_is_none(self, client):
        client.describe_instances.side_effect = _client_error(
            "InvalidInstanceID.NotFound", "DescribeInstances"
        )
        assert ec2.get_instance_state(INSTANCE, ec2_client=client) is None

    def test_an_empty_answer_is_none(self, client):
        client.describe_instances.return_value = {"Reservations": []}
        assert ec2.get_instance_state(INSTANCE, ec2_client=client) is None

    def test_any_other_failure_raises(self, client):
        """A denied describe is not "no such instance"."""
        client.describe_instances.side_effect = _client_error(
            "UnauthorizedOperation", "DescribeInstances"
        )
        with pytest.raises(RuntimeError, match=INSTANCE):
            ec2.get_instance_state(INSTANCE, ec2_client=client)


class TestStartAndStop:
    def test_start_initiates_and_reports_true(self, client):
        assert ec2.start_instance(INSTANCE, ec2_client=client) is True
        client.start_instances.assert_called_once_with(InstanceIds=[INSTANCE])

    def test_stop_initiates_and_reports_true(self, client):
        assert ec2.stop_instance(INSTANCE, ec2_client=client) is True
        client.stop_instances.assert_called_once_with(InstanceIds=[INSTANCE])

    def test_a_refusal_is_false(self, client):
        client.start_instances.side_effect = _client_error(
            "IncorrectInstanceState", "StartInstances"
        )
        client.stop_instances.side_effect = _client_error("IncorrectInstanceState", "StopInstances")
        assert ec2.start_instance(INSTANCE, ec2_client=client) is False
        assert ec2.stop_instance(INSTANCE, ec2_client=client) is False
