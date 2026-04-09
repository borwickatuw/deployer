"""Tests for deployment validation functions."""

from unittest.mock import MagicMock

from botocore.exceptions import ClientError

from deployer.core.config import validate_environment_config
from deployer.deploy.validation import validate_ecs_cluster


class TestValidateEcsCluster:
    """Tests for validate_ecs_cluster function."""

    def test_cluster_exists_and_active(self):
        """Test successful validation when cluster exists and is active."""
        mock_ecs = MagicMock()
        mock_ecs.describe_clusters.return_value = {
            "clusters": [
                {
                    "clusterName": "my-cluster",
                    "status": "ACTIVE",
                    "clusterArn": "arn:aws:ecs:us-west-2:123456789:cluster/my-cluster",
                }
            ],
            "failures": [],
        }

        exists, error = validate_ecs_cluster(mock_ecs, "my-cluster")

        assert exists is True
        assert error is None
        mock_ecs.describe_clusters.assert_called_once_with(clusters=["my-cluster"])

    def test_cluster_not_found(self):
        """Test error when cluster doesn't exist."""
        mock_ecs = MagicMock()
        mock_ecs.describe_clusters.return_value = {
            "clusters": [],
            "failures": [
                {
                    "arn": "arn:aws:ecs:us-west-2:123456789:cluster/missing-cluster",
                    "reason": "MISSING",
                }
            ],
        }

        exists, error = validate_ecs_cluster(mock_ecs, "missing-cluster")

        assert exists is False
        assert error is not None
        assert "missing-cluster" in error
        assert "not found" in error
        assert "tofu" in error.lower()

    def test_cluster_inactive(self):
        """Test error when cluster exists but is not active."""
        mock_ecs = MagicMock()
        mock_ecs.describe_clusters.return_value = {
            "clusters": [
                {
                    "clusterName": "inactive-cluster",
                    "status": "INACTIVE",
                }
            ],
            "failures": [],
        }

        exists, error = validate_ecs_cluster(mock_ecs, "inactive-cluster")

        assert exists is False
        assert error is not None
        assert "inactive-cluster" in error
        assert "INACTIVE" in error
        assert "expected ACTIVE" in error

    def test_cluster_provisioning(self):
        """Test error when cluster is still provisioning."""
        mock_ecs = MagicMock()
        mock_ecs.describe_clusters.return_value = {
            "clusters": [
                {
                    "clusterName": "new-cluster",
                    "status": "PROVISIONING",
                }
            ],
            "failures": [],
        }

        exists, error = validate_ecs_cluster(mock_ecs, "new-cluster")

        assert exists is False
        assert error is not None
        assert "PROVISIONING" in error

    def test_client_error(self):
        """Test error handling for AWS client errors."""
        mock_ecs = MagicMock()
        mock_ecs.describe_clusters.side_effect = ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "User is not authorized",
                }
            },
            "DescribeClusters",
        )

        exists, error = validate_ecs_cluster(mock_ecs, "my-cluster")

        assert exists is False
        assert error is not None
        assert "my-cluster" in error
        assert "AccessDeniedException" in error


class TestValidateEnvironmentConfig:
    """Tests for validate_environment_config function."""

    def test_valid_config(self):
        """Test that a complete config passes validation."""
        config = {
            "infrastructure": {
                "cluster_name": "my-cluster",
                "ecr_prefix": "myapp",
                "execution_role_arn": "arn:aws:iam::123:role/exec",
                "task_role_arn": "arn:aws:iam::123:role/task",
                "security_group_id": "sg-12345",
                "private_subnet_ids": ["subnet-1", "subnet-2"],
            }
        }

        errors = validate_environment_config(config)

        assert errors == []

    def test_missing_cluster_name(self):
        """Test error when cluster_name is missing."""
        config = {
            "infrastructure": {
                "ecr_prefix": "myapp",
                "execution_role_arn": "arn:aws:iam::123:role/exec",
                "task_role_arn": "arn:aws:iam::123:role/task",
                "security_group_id": "sg-12345",
                "private_subnet_ids": ["subnet-1"],
            }
        }

        errors = validate_environment_config(config)

        assert len(errors) == 1
        assert "[infrastructure].cluster_name" in errors[0]

    def test_missing_multiple_fields(self):
        """Test error when multiple required fields are missing."""
        config = {
            "infrastructure": {
                "cluster_name": "my-cluster",
            }
        }

        errors = validate_environment_config(config)

        # Should have errors for: ecr_prefix, execution_role_arn, task_role_arn,
        # security_group_id, private_subnet_ids
        assert len(errors) == 5
        field_names = [e.split(".")[-1] for e in errors]
        assert "ecr_prefix" in field_names
        assert "execution_role_arn" in field_names
        assert "task_role_arn" in field_names
        assert "security_group_id" in field_names
        assert "private_subnet_ids" in field_names

    def test_missing_infrastructure_section(self):
        """Test error when entire infrastructure section is missing."""
        config = {}

        errors = validate_environment_config(config)

        # Should have errors for all required fields
        assert len(errors) == 6  # All required fields

    def test_empty_subnet_list(self):
        """Test error when private_subnet_ids is an empty list."""
        config = {
            "infrastructure": {
                "cluster_name": "my-cluster",
                "ecr_prefix": "myapp",
                "execution_role_arn": "arn:aws:iam::123:role/exec",
                "task_role_arn": "arn:aws:iam::123:role/task",
                "security_group_id": "sg-12345",
                "private_subnet_ids": [],  # Empty list
            }
        }

        errors = validate_environment_config(config)

        assert len(errors) == 1
        assert "private_subnet_ids" in errors[0]
        # Empty list is considered a "missing" value
        assert "Empty list" in errors[0] or "Missing" in errors[0]

    def test_empty_string_value(self):
        """Test error when a required field is an empty string."""
        config = {
            "infrastructure": {
                "cluster_name": "",  # Empty string
                "ecr_prefix": "myapp",
                "execution_role_arn": "arn:aws:iam::123:role/exec",
                "task_role_arn": "arn:aws:iam::123:role/task",
                "security_group_id": "sg-12345",
                "private_subnet_ids": ["subnet-1"],
            }
        }

        errors = validate_environment_config(config)

        assert len(errors) == 1
        assert "cluster_name" in errors[0]
