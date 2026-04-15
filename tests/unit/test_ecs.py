"""Tests for deployer.aws.ecs module."""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, WaiterError

from deployer.aws import ecs

# Sample AWS response data for mocking
SAMPLE_SERVICE_RESPONSE = {
    "services": [
        {
            "serviceName": "web",
            "serviceArn": "arn:aws:ecs:us-west-2:123456789:service/test-cluster/web",
            "desiredCount": 2,
            "runningCount": 2,
            "status": "ACTIVE",
            "taskDefinition": "arn:aws:ecs:us-west-2:123456789:task-definition/web:42",
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": ["subnet-111", "subnet-222"],
                    "securityGroups": ["sg-123"],
                    "assignPublicIp": "DISABLED",
                }
            },
            "deployments": [
                {
                    "status": "PRIMARY",
                    "updatedAt": "2024-01-15T10:30:00Z",
                }
            ],
        }
    ]
}

SAMPLE_TASK_DEFINITION_RESPONSE = {
    "taskDefinition": {
        "taskDefinitionArn": "arn:aws:ecs:us-west-2:123456789:task-definition/web:42",
        "family": "web",
        "cpu": "256",
        "memory": "512",
        "containerDefinitions": [
            {
                "name": "web",
                "image": "123456789.dkr.ecr.us-west-2.amazonaws.com/app:latest",
                "essential": True,
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": "/ecs/myapp-staging",
                        "awslogs-region": "us-west-2",
                        "awslogs-stream-prefix": "web",
                    },
                },
            },
            {
                "name": "sidecar",
                "image": "datadog/agent:latest",
                "essential": False,
            },
        ],
    }
}

SAMPLE_RUN_TASK_RESPONSE = {
    "tasks": [
        {
            "taskArn": "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            "lastStatus": "PENDING",
        }
    ],
    "failures": [],
}

SAMPLE_DESCRIBE_TASKS_RUNNING = {
    "tasks": [
        {
            "taskArn": "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            "lastStatus": "RUNNING",
            "containers": [
                {"name": "web", "lastStatus": "RUNNING"},
            ],
        }
    ]
}

SAMPLE_DESCRIBE_TASKS_STOPPED = {
    "tasks": [
        {
            "taskArn": "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            "lastStatus": "STOPPED",
            "stoppedReason": "Essential container exited",
            "containers": [
                {"name": "web", "lastStatus": "STOPPED", "exitCode": 0},
            ],
        }
    ]
}

SAMPLE_DESCRIBE_TASKS_FAILED = {
    "tasks": [
        {
            "taskArn": "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            "lastStatus": "STOPPED",
            "stoppedReason": "Task failed to start",
            "containers": [
                {"name": "web", "lastStatus": "STOPPED", "exitCode": 1},
            ],
        }
    ]
}


@pytest.fixture
def mock_ecs_client():
    """Create a mock ECS client for testing."""
    return MagicMock()


class TestGetTaskContainers:
    """Tests for get_task_containers function."""

    def test_returns_container_list(self, mock_ecs_client):
        """Test successful retrieval of container definitions."""
        mock_ecs_client.describe_task_definition.return_value = SAMPLE_TASK_DEFINITION_RESPONSE

        result = ecs.get_task_containers("web:42", ecs_client=mock_ecs_client)

        assert len(result) == 2
        assert result[0]["name"] == "web"
        assert result[0]["essential"] is True
        assert result[0]["logConfiguration"]["logDriver"] == "awslogs"
        assert result[1]["name"] == "sidecar"
        assert result[1]["essential"] is False

    def test_returns_empty_on_failure(self, mock_ecs_client):
        """Test returns empty list when API fails."""
        mock_ecs_client.describe_task_definition.side_effect = ClientError(
            {"Error": {"Code": "TaskDefinitionNotFound", "Message": "Not found"}},
            "DescribeTaskDefinition",
        )

        result = ecs.get_task_containers("web:42", ecs_client=mock_ecs_client)

        assert result == []

    def test_returns_empty_when_no_containers(self, mock_ecs_client):
        """Test returns empty list when no containers defined."""
        mock_ecs_client.describe_task_definition.return_value = {
            "taskDefinition": {"containerDefinitions": []}
        }

        result = ecs.get_task_containers("web:42", ecs_client=mock_ecs_client)

        assert result == []


class TestRunTask:
    """Tests for run_task function."""

    def test_runs_task_successfully(self, mock_ecs_client):
        """Test successful task execution."""
        mock_ecs_client.run_task.return_value = SAMPLE_RUN_TASK_RESPONSE

        network_config = {
            "awsvpcConfiguration": {
                "subnets": ["subnet-111"],
                "securityGroups": ["sg-123"],
                "assignPublicIp": "DISABLED",
            }
        }

        result = ecs.run_task(
            cluster_name="test-cluster",
            task_definition="web:42",
            network_config=network_config,
            container_name="web",
            command=["python", "manage.py", "migrate"],
            ecs_client=mock_ecs_client,
        )

        assert result == "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456"

        # Verify the boto3 call
        mock_ecs_client.run_task.assert_called_once()
        call_kwargs = mock_ecs_client.run_task.call_args[1]
        assert call_kwargs["cluster"] == "test-cluster"
        assert call_kwargs["taskDefinition"] == "web:42"
        assert call_kwargs["launchType"] == "FARGATE"

    def test_includes_environment_override(self, mock_ecs_client):
        """Test that environment variables are included in override."""
        mock_ecs_client.run_task.return_value = SAMPLE_RUN_TASK_RESPONSE

        network_config = {
            "awsvpcConfiguration": {
                "subnets": ["subnet-111"],
                "securityGroups": ["sg-123"],
                "assignPublicIp": "DISABLED",
            }
        }

        ecs.run_task(
            cluster_name="test-cluster",
            task_definition="web:42",
            network_config=network_config,
            container_name="web",
            command=["echo", "test"],
            environment=[{"name": "DEBUG", "value": "true"}],
            ecs_client=mock_ecs_client,
        )

        # Check that overrides include environment
        call_kwargs = mock_ecs_client.run_task.call_args[1]
        overrides = call_kwargs["overrides"]
        assert overrides["containerOverrides"][0]["environment"] == [
            {"name": "DEBUG", "value": "true"}
        ]

    def test_returns_none_on_failure(self, mock_ecs_client):
        """Test returns None when API fails."""
        mock_ecs_client.run_task.side_effect = ClientError(
            {"Error": {"Code": "ServiceException", "Message": "Error"}},
            "RunTask",
        )

        result = ecs.run_task(
            cluster_name="test-cluster",
            task_definition="web:42",
            network_config={},
            container_name="web",
            command=["echo"],
            ecs_client=mock_ecs_client,
        )

        assert result is None

    def test_returns_none_when_no_tasks(self, mock_ecs_client):
        """Test returns None when no tasks in response."""
        mock_ecs_client.run_task.return_value = {"tasks": [], "failures": []}

        result = ecs.run_task(
            cluster_name="test-cluster",
            task_definition="web:42",
            network_config={},
            container_name="web",
            command=["echo"],
            ecs_client=mock_ecs_client,
        )

        assert result is None


class TestWaitForTask:
    """Tests for wait_for_task function."""

    def test_waits_for_successful_completion(self, mock_ecs_client):
        """Test waiting for task that completes successfully."""
        # Set up waiter mock
        mock_waiter = MagicMock()
        mock_ecs_client.get_waiter.return_value = mock_waiter

        # Set up describe_tasks response for after waiter completes
        mock_ecs_client.describe_tasks.return_value = SAMPLE_DESCRIBE_TASKS_STOPPED

        result = ecs.wait_for_task(
            "test-cluster",
            "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            timeout=60,
            ecs_client=mock_ecs_client,
        )

        assert result == 0

        # Verify waiter was called
        mock_ecs_client.get_waiter.assert_called_once_with("tasks_stopped")
        mock_waiter.wait.assert_called_once()

    def test_returns_nonzero_exit_code(self, mock_ecs_client):
        """Test returns container exit code on failure."""
        mock_waiter = MagicMock()
        mock_ecs_client.get_waiter.return_value = mock_waiter
        mock_ecs_client.describe_tasks.return_value = SAMPLE_DESCRIBE_TASKS_FAILED

        result = ecs.wait_for_task(
            "test-cluster",
            "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            timeout=60,
            ecs_client=mock_ecs_client,
        )

        assert result == 1

    def test_returns_minus_one_on_timeout(self, mock_ecs_client):
        """Test returns -1 when task times out."""
        mock_waiter = MagicMock()
        mock_waiter.wait.side_effect = WaiterError("TasksStopped", "Max attempts exceeded", {})
        mock_ecs_client.get_waiter.return_value = mock_waiter

        result = ecs.wait_for_task(
            "test-cluster",
            "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            timeout=10,
            ecs_client=mock_ecs_client,
        )

        assert result == -1

    def test_returns_minus_one_on_api_failure(self, mock_ecs_client):
        """Test returns -1 when AWS API fails."""
        mock_waiter = MagicMock()
        mock_waiter.wait.side_effect = ClientError(
            {"Error": {"Code": "ServiceException", "Message": "Error"}},
            "DescribeTasks",
        )
        mock_ecs_client.get_waiter.return_value = mock_waiter

        result = ecs.wait_for_task(
            "test-cluster",
            "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            timeout=60,
            ecs_client=mock_ecs_client,
        )

        assert result == -1

    def test_returns_minus_one_when_no_tasks(self, mock_ecs_client):
        """Test returns -1 when task not found after waiter."""
        mock_waiter = MagicMock()
        mock_ecs_client.get_waiter.return_value = mock_waiter
        mock_ecs_client.describe_tasks.return_value = {"tasks": []}

        result = ecs.wait_for_task(
            "test-cluster",
            "arn:aws:ecs:us-west-2:123456789:task/test-cluster/abc123def456",
            timeout=60,
            ecs_client=mock_ecs_client,
        )

        assert result == -1


class TestGetServices:
    """Tests for get_services function."""

    def test_returns_formatted_services(self, mock_ecs_client):
        """Test successful retrieval of services list."""
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"serviceArns": ["arn:aws:ecs:us-west-2:123456789:service/test-cluster/web"]}
        ]
        mock_ecs_client.get_paginator.return_value = paginator
        mock_ecs_client.describe_services.return_value = SAMPLE_SERVICE_RESPONSE

        result = ecs.get_services("test-cluster", ecs_client=mock_ecs_client)

        assert len(result) == 1
        assert result[0].name == "web"
        assert result[0].desired_count == 2
        assert result[0].running_count == 2
        assert result[0].status == "ACTIVE"

    def test_returns_empty_on_no_services(self, mock_ecs_client):
        """Test returns empty list when no services exist."""
        paginator = MagicMock()
        paginator.paginate.return_value = [{"serviceArns": []}]
        mock_ecs_client.get_paginator.return_value = paginator

        result = ecs.get_services("test-cluster", ecs_client=mock_ecs_client)

        assert result == []

    def test_returns_empty_on_cluster_not_found(self, mock_ecs_client):
        """Test returns empty list when cluster not found."""
        paginator = MagicMock()
        paginator.paginate.side_effect = ClientError(
            {"Error": {"Code": "ClusterNotFoundException", "Message": "Not found"}},
            "ListServices",
        )
        mock_ecs_client.get_paginator.return_value = paginator

        result = ecs.get_services("test-cluster", ecs_client=mock_ecs_client)

        assert result == []


class TestGetServiceInfo:
    """Tests for get_service_info function (combined network + task def)."""

    def test_returns_both_network_and_task_def(self, mock_ecs_client):
        """Test successful retrieval of both network config and task definition."""
        mock_ecs_client.describe_services.return_value = SAMPLE_SERVICE_RESPONSE

        network_config, task_def = ecs.get_service_info(
            "test-cluster", "web", ecs_client=mock_ecs_client
        )

        # Verify only ONE API call was made
        assert mock_ecs_client.describe_services.call_count == 1

        # Verify network config
        assert network_config is not None
        assert network_config["awsvpcConfiguration"]["subnets"] == ["subnet-111", "subnet-222"]
        assert network_config["awsvpcConfiguration"]["securityGroups"] == ["sg-123"]

        # Verify task definition
        assert task_def == "arn:aws:ecs:us-west-2:123456789:task-definition/web:42"

    def test_returns_none_none_on_failure(self, mock_ecs_client):
        """Test returns None, None when API fails."""
        mock_ecs_client.describe_services.side_effect = ClientError(
            {"Error": {"Code": "ServiceException", "Message": "Error"}},
            "DescribeServices",
        )

        network_config, task_def = ecs.get_service_info(
            "test-cluster", "web", ecs_client=mock_ecs_client
        )

        assert network_config is None
        assert task_def is None

    def test_returns_none_none_when_no_services(self, mock_ecs_client):
        """Test returns None, None when no services found."""
        mock_ecs_client.describe_services.return_value = {"services": []}

        network_config, task_def = ecs.get_service_info(
            "test-cluster", "nonexistent", ecs_client=mock_ecs_client
        )

        assert network_config is None
        assert task_def is None

    def test_returns_task_def_without_network(self, mock_ecs_client):
        """Test returns task definition even if network config is missing."""
        response = {
            "services": [
                {
                    "serviceName": "web",
                    "taskDefinition": "web:42",
                    # No networkConfiguration
                }
            ]
        }
        mock_ecs_client.describe_services.return_value = response

        network_config, task_def = ecs.get_service_info(
            "test-cluster", "web", ecs_client=mock_ecs_client
        )

        assert network_config is None
        assert task_def == "web:42"


class TestGetLogsLocationFromContainers:
    """Tests for get_logs_location_from_containers function."""

    def test_returns_log_location(self):
        """Test extraction of log location from container list."""
        containers = [
            {
                "name": "web",
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": "/ecs/myapp",
                        "awslogs-stream-prefix": "web",
                    },
                },
            }
        ]

        result = ecs.get_logs_location_from_containers(containers, "web")

        assert result is not None
        log_group, prefix = result
        assert log_group == "/ecs/myapp"
        assert prefix == "web"

    def test_returns_none_for_unknown_container(self):
        """Test returns None when container not found."""
        containers = [{"name": "web"}]

        result = ecs.get_logs_location_from_containers(containers, "nonexistent")

        assert result is None

    def test_returns_none_for_non_awslogs_driver(self):
        """Test returns None when log driver is not awslogs."""
        containers = [
            {
                "name": "web",
                "logConfiguration": {
                    "logDriver": "json-file",
                },
            }
        ]

        result = ecs.get_logs_location_from_containers(containers, "web")

        assert result is None

    def test_returns_none_for_no_log_config(self):
        """Test returns None when container has no log configuration."""
        containers = [{"name": "web"}]

        result = ecs.get_logs_location_from_containers(containers, "web")

        assert result is None
