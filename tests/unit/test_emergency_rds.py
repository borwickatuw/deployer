"""Tests for deployer.emergency.rds module.

Backed by moto (``mocked_aws`` fixture): the RDS calls run against moto's
in-memory backend rather than mocked boto3 objects, so the request shapes
these functions send are validated by botocore for real.

Several tests are marked "pinned, not endorsed": the functions under test
swallow ClientError and return None/[] , which makes "the call failed" and
"there is nothing there" indistinguishable to callers. That is a real design
question, tracked as claude-meta Phase 53i (raise-vs-return policy); these
tests pin today's behavior so 53i's change is visible when it happens.
"""

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError

from deployer.emergency import rds as rds_module
from deployer.emergency.rds import (
    _handle_restore_error,
    _prepare_restore,
    create_emergency_snapshot,
    generate_emergency_snapshot_id,
    get_rds_instance_details,
    get_rds_snapshots,
    restore_from_point_in_time,
    restore_from_snapshot,
)

REGION = "us-west-2"
INSTANCE_ID = "myapp-production-db"
SUBNET_GROUP = "test-subnet-group"


@pytest.fixture
def rds_instance(mocked_aws) -> dict:
    """Create a postgres RDS instance in moto, with the VPC scaffolding.

    Restores pass the source instance's DBSubnetGroupName straight through to
    boto3, which rejects None -- so the instance must be created inside a real
    subnet group for the restore paths to be exercisable at all.

    Returns:
        Dict with instance_id, security_group_id and subnet_group.
    """
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet_ids = [
        ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=az)["Subnet"]["SubnetId"]
        for cidr, az in (("10.0.1.0/24", f"{REGION}a"), ("10.0.2.0/24", f"{REGION}b"))
    ]
    security_group_id = ec2.create_security_group(
        GroupName="db-sg", Description="test db security group", VpcId=vpc_id
    )["GroupId"]

    client = boto3.client("rds", region_name=REGION)
    client.create_db_subnet_group(
        DBSubnetGroupName=SUBNET_GROUP,
        DBSubnetGroupDescription="test subnet group",
        SubnetIds=subnet_ids,
    )
    client.create_db_instance(
        DBInstanceIdentifier=INSTANCE_ID,
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        EngineVersion="16.3",
        AllocatedStorage=20,
        MasterUsername="dbadmin",
        MasterUserPassword="testing-password",  # pragma: allowlist secret
        DBSubnetGroupName=SUBNET_GROUP,
        VpcSecurityGroupIds=[security_group_id],
    )

    return {
        "instance_id": INSTANCE_ID,
        "security_group_id": security_group_id,
        "subnet_group": SUBNET_GROUP,
    }


def make_client_error(code: str, operation: str = "RestoreDBInstanceFromDBSnapshot") -> ClientError:
    """Build a ClientError with a specific AWS error code."""
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class TestGenerateEmergencySnapshotId:
    def test_shape(self):
        snapshot_id = generate_emergency_snapshot_id(INSTANCE_ID)

        assert re.match(rf"^{INSTANCE_ID}-emergency-\d{{4}}-\d{{2}}-\d{{2}}-\d{{6}}$", snapshot_id)

    def test_uses_current_utc_date(self):
        today = datetime.now(UTC).strftime("%Y-%m-%d")

        assert generate_emergency_snapshot_id("db").startswith(f"db-emergency-{today}-")


class TestCreateEmergencySnapshot:
    def test_creates_snapshot_without_waiting(self, rds_instance):
        snapshot_id = create_emergency_snapshot(INSTANCE_ID, wait=False)

        assert snapshot_id is not None
        assert snapshot_id.startswith(f"{INSTANCE_ID}-emergency-")
        client = boto3.client("rds", region_name=REGION)
        snapshots = client.describe_db_snapshots(
            DBInstanceIdentifier=INSTANCE_ID, SnapshotType="manual"
        )["DBSnapshots"]
        assert [s["DBSnapshotIdentifier"] for s in snapshots] == [snapshot_id]

    def test_missing_instance_returns_none(self, mocked_aws):
        # Pinned, not endorsed: a nonexistent instance is indistinguishable
        # from any other ClientError. See module docstring (Phase 53i).
        assert create_emergency_snapshot("no-such-instance", wait=False) is None

    def test_duplicate_snapshot_returns_none(self, rds_instance, mocker):
        """A colliding snapshot id yields None rather than raising."""
        mocker.patch.object(
            rds_module,
            "generate_emergency_snapshot_id",
            return_value="fixed-snapshot-id",
        )

        assert create_emergency_snapshot(INSTANCE_ID, wait=False) == "fixed-snapshot-id"
        # Pinned, not endorsed: DBSnapshotAlreadyExists is swallowed (Phase 53i).
        assert create_emergency_snapshot(INSTANCE_ID, wait=False) is None

    def test_wait_uses_snapshot_waiter(self, mocker):
        """The wait=True path drives the boto3 waiter with a derived MaxAttempts.

        The waiter is patched rather than run against moto: moto reports every
        snapshot as 'available' immediately, so a real waiter run would assert
        nothing while still costing wall-clock.
        """
        client = MagicMock()
        waiter = client.get_waiter.return_value
        mocker.patch.object(rds_module, "_get_rds_client", return_value=client)

        snapshot_id = create_emergency_snapshot(INSTANCE_ID, wait=True, timeout=600)

        client.get_waiter.assert_called_once_with("db_snapshot_available")
        waiter.wait.assert_called_once_with(
            DBSnapshotIdentifier=snapshot_id,
            WaiterConfig={"Delay": 15, "MaxAttempts": 40},
        )


class TestGetRdsSnapshots:
    @staticmethod
    def create_manual_snapshots(count: int) -> list[str]:
        client = boto3.client("rds", region_name=REGION)
        ids = []
        for i in range(count):
            snapshot_id = f"manual-snap-{i}"
            client.create_db_snapshot(
                DBSnapshotIdentifier=snapshot_id, DBInstanceIdentifier=INSTANCE_ID
            )
            ids.append(snapshot_id)
        return ids

    def test_manual_only(self, rds_instance):
        self.create_manual_snapshots(2)

        result = get_rds_snapshots(INSTANCE_ID, include_automated=False)

        assert {s["type"] for s in result} == {"manual"}
        assert {s["id"] for s in result} == {"manual-snap-0", "manual-snap-1"}
        assert result[0]["engine"] == "postgres"
        assert result[0]["status"] == "available"
        assert result[0]["storage_gb"] == 20

    def test_includes_automated_backups(self, rds_instance):
        self.create_manual_snapshots(1)

        result = get_rds_snapshots(INSTANCE_ID, include_automated=True)

        # moto creates one automated snapshot when the instance is created.
        assert {s["type"] for s in result} == {"manual", "automated"}

    def test_sorted_newest_first(self, rds_instance):
        created = self.create_manual_snapshots(3)

        result = get_rds_snapshots(INSTANCE_ID, include_automated=False)

        assert [s["id"] for s in result] == list(reversed(created))
        timestamps = [s["created_at"] for s in result]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_max_results_truncates_to_newest(self, rds_instance):
        created = self.create_manual_snapshots(4)

        result = get_rds_snapshots(INSTANCE_ID, max_results=2, include_automated=False)

        assert [s["id"] for s in result] == list(reversed(created))[:2]

    def test_missing_instance_returns_empty_list(self, mocked_aws):
        """moto answers an unknown instance with an empty snapshot list."""
        assert get_rds_snapshots("no-such-instance") == []

    def test_client_error_returns_empty_list(self, mocker):
        client = MagicMock()
        client.describe_db_snapshots.side_effect = make_client_error(
            "AccessDenied", "DescribeDBSnapshots"
        )
        mocker.patch.object(rds_module, "_get_rds_client", return_value=client)

        # Pinned, not endorsed: an API failure reads as "no snapshots exist",
        # which is the worst possible answer during an incident (Phase 53i).
        assert get_rds_snapshots(INSTANCE_ID) == []


class TestGetRdsInstanceDetails:
    def test_returns_details(self, rds_instance):
        details = get_rds_instance_details(INSTANCE_ID)

        assert details["id"] == INSTANCE_ID
        assert details["status"] == "available"
        assert details["instance_class"] == "db.t3.micro"
        assert details["engine"] == "postgres"
        assert details["engine_version"] == "16.3"
        assert details["endpoint"].endswith(".rds.amazonaws.com")
        assert details["port"] == 5432
        assert details["vpc_security_groups"] == [rds_instance["security_group_id"]]
        assert details["db_subnet_group"] == SUBNET_GROUP
        assert isinstance(details["latest_restorable_time"], datetime)

    def test_missing_instance_returns_none(self, mocked_aws):
        assert get_rds_instance_details("no-such-instance") is None

    def test_empty_response_returns_none(self, mocker):
        """AWS can answer with an empty DBInstances list instead of raising."""
        client = MagicMock()
        client.describe_db_instances.return_value = {"DBInstances": []}
        mocker.patch.object(rds_module, "_get_rds_client", return_value=client)

        assert get_rds_instance_details(INSTANCE_ID) is None


class TestPrepareRestore:
    def test_builds_target_id_and_source_details(self, rds_instance):
        target_id, source_details = _prepare_restore(INSTANCE_ID, "-restore")

        assert target_id == f"{INSTANCE_ID}-restore"
        assert source_details["instance_class"] == "db.t3.micro"

    def test_missing_source_returns_none(self, mocked_aws):
        assert _prepare_restore("no-such-instance", "-restore") is None


class TestHandleRestoreError:
    def test_already_exists_returns_error_dict(self):
        result = _handle_restore_error(make_client_error("DBInstanceAlreadyExists"), "db-restore")

        assert result["instance_id"] == "db-restore"
        assert result["status"] == "error"
        assert "already exists" in result["message"]
        assert "aws rds delete-db-instance" in result["message"]

    def test_other_errors_reraise(self):
        error = make_client_error("InvalidParameterValue")

        with pytest.raises(ClientError) as excinfo:
            _handle_restore_error(error, "db-restore")

        assert excinfo.value is error


class TestRestoreFromSnapshot:
    def test_creates_restore_instance(self, rds_instance):
        snapshot_id = create_emergency_snapshot(INSTANCE_ID, wait=False)

        result = restore_from_snapshot(INSTANCE_ID, snapshot_id)

        assert result["instance_id"] == f"{INSTANCE_ID}-restore"
        assert result["status"] == "creating"
        assert result["source_snapshot"] == snapshot_id
        assert f"{INSTANCE_ID}-restore" in result["message"]

        client = boto3.client("rds", region_name=REGION)
        restored = client.describe_db_instances(DBInstanceIdentifier=f"{INSTANCE_ID}-restore")
        assert restored["DBInstances"][0]["DBInstanceClass"] == "db.t3.micro"

    def test_custom_target_suffix(self, rds_instance):
        snapshot_id = create_emergency_snapshot(INSTANCE_ID, wait=False)

        result = restore_from_snapshot(INSTANCE_ID, snapshot_id, target_suffix="-recovery")

        assert result["instance_id"] == f"{INSTANCE_ID}-recovery"

    def test_missing_source_returns_none(self, mocked_aws):
        assert restore_from_snapshot("no-such-instance", "some-snapshot") is None

    def test_unexpected_client_error_propagates(self, rds_instance):
        """A non-already-exists ClientError is re-raised, not swallowed."""
        with pytest.raises(ClientError) as excinfo:
            restore_from_snapshot(INSTANCE_ID, "no-such-snapshot")

        assert excinfo.value.response["Error"]["Code"] == "DBSnapshotNotFound"

    def test_target_already_exists_returns_error_dict(self, rds_instance, mocker):
        """moto allows a duplicate restore target, so the collision is injected.

        Real RDS raises DBInstanceAlreadyExists here; the point of the test is
        that restore_from_snapshot routes it through _handle_restore_error
        instead of letting it escape.
        """
        client = MagicMock()
        client.describe_db_instances.return_value = {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": INSTANCE_ID,
                    "DBInstanceStatus": "available",
                    "DBInstanceClass": "db.t3.micro",
                    "Engine": "postgres",
                    "DBSubnetGroup": {"DBSubnetGroupName": SUBNET_GROUP},
                    "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-123"}],
                }
            ]
        }
        client.restore_db_instance_from_db_snapshot.side_effect = make_client_error(
            "DBInstanceAlreadyExists"
        )
        mocker.patch.object(rds_module, "_get_rds_client", return_value=client)

        result = restore_from_snapshot(INSTANCE_ID, "some-snapshot")

        assert result["status"] == "error"
        assert result["instance_id"] == f"{INSTANCE_ID}-restore"


class TestRestoreFromPointInTime:
    def test_creates_restore_instance(self, rds_instance):
        restore_time = datetime.now(UTC) - timedelta(hours=1)

        result = restore_from_point_in_time(INSTANCE_ID, restore_time)

        assert result["instance_id"] == f"{INSTANCE_ID}-restore"
        assert result["status"] == "creating"
        assert result["restore_time"] == restore_time.isoformat()

        client = boto3.client("rds", region_name=REGION)
        restored = client.describe_db_instances(DBInstanceIdentifier=f"{INSTANCE_ID}-restore")
        assert restored["DBInstances"][0]["DBInstanceClass"] == "db.t3.micro"

    def test_missing_source_returns_none(self, mocked_aws):
        assert restore_from_point_in_time("no-such-instance", datetime.now(UTC)) is None

    def test_restore_time_after_latest_restorable_returns_error(self, rds_instance):
        too_late = datetime.now(UTC) + timedelta(days=1)

        result = restore_from_point_in_time(INSTANCE_ID, too_late)

        assert result["status"] == "error"
        assert "after the latest" in result["message"]

    def test_target_already_exists_returns_error_dict(self, rds_instance):
        restore_time = datetime.now(UTC) - timedelta(hours=1)
        restore_from_point_in_time(INSTANCE_ID, restore_time)

        result = restore_from_point_in_time(INSTANCE_ID, restore_time)

        assert result["status"] == "error"
        assert result["instance_id"] == f"{INSTANCE_ID}-restore"
        assert "already exists" in result["message"]
