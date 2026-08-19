"""Characterization pins for bin/emergency.py's restore-db command.

These are characterization pins, not endorsements. They record what
``cmd_restore_db()`` and ``_print_restore_success()`` do **today**; where the
behaviour looks wrong it is pinned anyway and called out in a comment on the
test. Nothing here is a fix.

``cmd_restore_db`` was **entirely uncovered** before this file, and so was
``_print_restore_success``. ``test_emergency_cli.py`` pins the rollback, scale
and force-deploy commands; ``test_emergency_rds.py`` pins the RDS *producers*
against moto. Between those two files nothing executed the restore command,
which is where every subscript read of the restore payloads lives:
``result.status``, ``result.message``, ``result.instance_id`` (three times)
and ``rds_details.latest_restorable_time``.

That is what makes this the Phase 53f gap. ``restore_from_snapshot()`` and
``restore_from_point_in_time()`` returned **dicts**, and ``cmd_restore_db`` is
their only production consumer. A ``dict -> NamedTuple`` conversion cannot be
shown to be behaviour-preserving against the producer pins alone. 53f-2 made
that conversion: both producers now answer with ``RestoreResult`` and
``get_rds_instance_details`` with ``RdsInstanceDetails``, so the reads above
are attribute reads. Every assertion below is the one it was before the
conversion; only the access form moved.

What is pinned here:

* Both ``--snapshot`` and ``--time`` arms, each in all three of its shapes:
  the ``status == "creating"`` success, the ``status == "error"`` payload, and
  the ``None`` return. The two arms are near-identical code that prints
  identical text; both are pinned separately so a de-duplication of them is
  verifiable.
* ``_print_restore_success()``'s whole block, including the fact that it reads
  ``instance_id`` three times and ``message`` once, and that it logs through
  *two* logger channels (``rds`` and ``success``) before printing. The pin that
  a payload missing either field blows up survives the conversion, moved from
  a read-time ``KeyError`` to a construction-time ``TypeError``.
* The ``--time`` arm's ISO parsing, including the ``Z`` -> ``+00:00`` rewrite
  and the ``ValueError`` message.
* The interactive path: the numbered snapshot menu, the point-in-time
  availability line and its two absent arms, the cancel arm, and **both**
  recursive re-entries -- a bare number re-enters as ``--snapshot``, anything
  else re-enters as ``--time``.
* Two behaviours that look like latent bugs, pinned as-is rather than fixed:
  the ``idx < 0`` guard is unreachable because ``str.isdigit()`` already
  rejects a leading minus, and a non-``DBInstanceAlreadyExists`` ``ClientError``
  propagates out of the command as an exception rather than an exit status.

Stubbing is at the outermost boundary -- 53d-2a's recorded rule -- so the pins
survive code motion inside the package. Concretely:

* ``TestCmdRestoreDbAgainstMoto`` stubs **nothing** below the CLI: the real
  ``restore_from_snapshot`` / ``get_rds_snapshots`` / ``get_rds_instance_details``
  run against moto through a real boto3 client, so the payloads the command
  consumes are the ones botocore actually produces.
* The remaining classes stub those four producers at ``bin/emergency.py``'s own
  bindings, because the error arms (a ``None`` return, an ``already exists``
  payload) are awkward or impossible to provoke through moto.
* ``_load_emergency_context`` is stubbed everywhere so no config file is read
  and no AWS profile is configured.
* The interactive prompt is fed through ``builtins.input()`` rather than
  through ``prompt_or_exit()``, so the pins survive the prompt moving into a
  shared helper -- the same choice ``test_emergency_cli.py`` made.

Nothing here re-pins a swallow-or-return contract in ``emergency/rds.py``;
those belong to ``test_emergency_rds.py``. One pin below did move with them:
a ``None`` from ``get_rds_instance_details`` now means only "there is no such
source instance", so the "generic failure" arm is reached by a genuinely
missing instance rather than by any read failure.
"""

import sys
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

from deployer.emergency.rds import RdsInstanceDetails, RestoreResult

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("emergency_restore", bin_dir / "emergency.py")
emergency = module_from_spec(_spec)
_spec.loader.exec_module(emergency)

ENV = "myapp-production"
RDS_ID = "myapp-production-db"
TARGET_ID = f"{RDS_ID}-restore"
REGION = "us-west-2"
SUBNET_GROUP = "restore-subnet-group"


class _StubLogger:
    """Stand-in for EmergencyLogger that records lines instead of writing them."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __getattr__(self, category: str):
        return lambda message: self.lines.append(f"{category}: {message}")


@pytest.fixture
def logger(monkeypatch) -> _StubLogger:
    """Stub the RDS context load so no config file or AWS profile is needed."""
    stub = _StubLogger()
    ctx = emergency.EmergencyContext(config={}, cluster_name=None, rds_id=RDS_ID, logger=stub)
    monkeypatch.setattr(emergency, "_load_emergency_context", lambda _env, **_kw: ctx)
    return stub


def _answers(monkeypatch, *replies: str) -> list[str]:
    """Feed canned stdin replies; return the list prompts are recorded into."""
    prompts: list[str] = []
    queue = list(replies)

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", _input)
    return prompts


def _creating(instance_id: str = TARGET_ID, message: str = "Restore initiated.") -> RestoreResult:
    """A success payload shaped like restore_from_snapshot()'s return."""
    return RestoreResult(
        instance_id=instance_id,
        status="creating",
        source_snapshot="snap-1",
        message=message,
    )


def _error(message: str = "Instance already exists.") -> RestoreResult:
    """An error payload shaped like _handle_restore_error()'s return."""
    return RestoreResult(instance_id=TARGET_ID, status="error", message=message)


def _details(**overrides) -> RdsInstanceDetails:
    """An instance record shaped like get_rds_instance_details()'s return."""
    fields = {
        "id": RDS_ID,
        "status": "available",
        "instance_class": "db.t3.micro",
        "engine": "postgres",
        "engine_version": "16.3",
        "endpoint": f"{RDS_ID}.abc123.{REGION}.rds.amazonaws.com",
        "port": 5432,
        "vpc_security_groups": ["sg-123"],
        "db_subnet_group": SUBNET_GROUP,
        "latest_restorable_time": None,
    }
    fields.update(overrides)
    return RdsInstanceDetails(**fields)


def _snapshot(snapshot_id: str, created_at: str = "2026-08-13T09:00:00+00:00") -> dict:
    """A snapshot entry shaped like get_rds_snapshots()'s return."""
    return {
        "id": snapshot_id,
        "created_at": created_at,
        "status": "available",
        "type": "manual",
        "engine": "postgres",
    }


@pytest.fixture
def restore(monkeypatch):
    """Stub the four RDS producers cmd_restore_db reaches, and record its calls."""

    class _Calls:
        def __init__(self) -> None:
            self.from_snapshot: list[tuple[str, str]] = []
            self.from_time: list[tuple[str, datetime]] = []
            self.snapshot_result: object = _creating()
            self.time_result: object = _creating()
            self.snapshots: list[dict] = []
            self.details: RdsInstanceDetails | None = None

        def restore_from_snapshot(self, rds_id: str, snapshot_id: str):
            self.from_snapshot.append((rds_id, snapshot_id))
            if isinstance(self.snapshot_result, Exception):
                raise self.snapshot_result
            return self.snapshot_result

        def restore_from_point_in_time(self, rds_id: str, when: datetime):
            self.from_time.append((rds_id, when))
            return self.time_result

        def get_rds_snapshots(self, _rds_id: str, **_kw) -> list[dict]:
            return self.snapshots

        def get_rds_instance_details(self, _rds_id: str) -> RdsInstanceDetails | None:
            return self.details

    calls = _Calls()
    for name in (
        "restore_from_snapshot",
        "restore_from_point_in_time",
        "get_rds_snapshots",
        "get_rds_instance_details",
    ):
        monkeypatch.setattr(emergency, name, getattr(calls, name))
    return calls


class TestPrintRestoreSuccess:
    """_print_restore_success() -- the block that reads the payload four times."""

    def test_the_instance_id_is_printed_and_logged_and_repeated_in_the_delete_hint(self, capsys):
        stub = _StubLogger()
        emergency._print_restore_success(stub, _creating(instance_id="db-restore"))
        out = capsys.readouterr().out
        assert "New instance: db-restore" in out
        assert (
            "aws rds delete-db-instance --db-instance-identifier db-restore "
            "--skip-final-snapshot"
        ) in out
        assert stub.lines == ["rds: Restore initiated: db-restore", "success: Restore initiated"]

    def test_the_payload_message_is_printed_verbatim(self, capsys):
        emergency._print_restore_success(_StubLogger(), _creating(message="line one\nline two"))
        out = capsys.readouterr().out
        assert "line one\nline two" in out

    def test_the_operator_warnings_are_printed(self, capsys):
        emergency._print_restore_success(_StubLogger(), _creating())
        out = capsys.readouterr().out
        assert "Restore initiated" in out
        assert "The original database is NOT modified" in out
        assert "update your application's DATABASE_URL" in out

    @pytest.mark.parametrize("missing", ["instance_id", "message"])
    def test_a_payload_missing_either_field_cannot_be_built(self, missing):
        # 53f-2 decided the question this pin was written to force. While the
        # payload was a dict, both reads were subscripts and a missing key
        # raised KeyError inside _print_restore_success -- after the header
        # lines had already been printed. RestoreResult moves the same failure
        # to construction time, so the reads can no longer raise at all.
        fields = {
            "instance_id": TARGET_ID,
            "status": "creating",
            "message": "Restore initiated.",
        }
        del fields[missing]
        with pytest.raises(TypeError, match=missing):
            RestoreResult(**fields)


class TestCmdRestoreDbFromSnapshot:
    """cmd_restore_db(--snapshot) -- all three shapes of the payload."""

    def test_a_created_instance_returns_0_and_prints_the_success_block(
        self, logger, restore, capsys
    ):
        assert emergency.cmd_restore_db(ENV, snapshot="snap-1", time=None) == 0
        assert restore.from_snapshot == [(RDS_ID, "snap-1")]
        out = capsys.readouterr().out
        assert f"New instance: {TARGET_ID}" in out
        assert logger.lines[0] == "action: restore-db"
        assert "rds: Restoring from snapshot snap-1" in logger.lines

    def test_an_error_payload_returns_1_and_prints_its_message(self, logger, restore, capsys):
        restore.snapshot_result = _error("Instance 'x' already exists.")
        assert emergency.cmd_restore_db(ENV, snapshot="snap-1", time=None) == 1
        assert "Instance 'x' already exists." in capsys.readouterr().out
        assert "error: Instance 'x' already exists." in logger.lines

    def test_a_none_return_returns_1_with_a_generic_message(self, logger, restore, capsys):
        # The producer's None means only "there is no such source
        # instance" -- a lookup that *failed* now raises and stops at the CLI
        # boundary -- so "Failed to initiate restore" is an accurate message.
        restore.snapshot_result = None
        assert emergency.cmd_restore_db(ENV, snapshot="snap-1", time=None) == 1
        assert "Failed to initiate restore" in capsys.readouterr().out
        assert "error: Failed to initiate restore" in logger.lines

    def test_a_non_already_exists_client_error_propagates_out_of_the_command(self, logger, restore):
        # Pinned, not endorsed: _handle_restore_error re-raises everything but
        # DBInstanceAlreadyExists, and cmd_restore_db catches nothing, so the
        # CLI exits on a traceback rather than an exit status.
        restore.snapshot_result = ClientError(
            {"Error": {"Code": "InvalidDBSnapshotState", "Message": "bad state"}},
            "RestoreDBInstanceFromDBSnapshot",
        )
        with pytest.raises(ClientError, match="InvalidDBSnapshotState"):
            emergency.cmd_restore_db(ENV, snapshot="snap-1", time=None)

    def test_snapshot_wins_over_time_when_both_are_given(self, logger, restore):
        assert emergency.cmd_restore_db(ENV, snapshot="snap-1", time="not-a-time") == 0
        assert restore.from_snapshot == [(RDS_ID, "snap-1")]
        assert restore.from_time == []


class TestCmdRestoreDbPointInTime:
    """cmd_restore_db(--time) -- ISO parsing and all three payload shapes."""

    def test_a_zulu_timestamp_is_parsed_as_utc(self, logger, restore):
        assert emergency.cmd_restore_db(ENV, snapshot=None, time="2026-08-13T12:00:00Z") == 0
        rds_id, when = restore.from_time[0]
        assert rds_id == RDS_ID
        assert when == datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def test_an_offset_timestamp_is_parsed_as_given(self, logger, restore):
        assert emergency.cmd_restore_db(ENV, snapshot=None, time="2026-08-13T12:00:00+00:00") == 0
        assert restore.from_time[0][1] == datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def test_a_naive_timestamp_is_accepted_without_a_timezone(self, logger, restore):
        # Pinned, not endorsed: no tzinfo is attached, so the value handed to
        # boto3 is naive and interpreted as local time by the SDK.
        assert emergency.cmd_restore_db(ENV, snapshot=None, time="2026-08-13T12:00:00") == 0
        assert restore.from_time[0][1] == datetime(2026, 8, 13, 12, 0)  # noqa: DTZ001

    def test_an_unparseable_timestamp_returns_1_before_any_aws_call(self, logger, restore, capsys):
        assert emergency.cmd_restore_db(ENV, snapshot=None, time="yesterday") == 1
        out = capsys.readouterr().out
        assert "Invalid time format: yesterday" in out
        assert "Expected ISO format: 2026-02-04T12:00:00Z" in out
        assert restore.from_time == []

    def test_a_created_instance_returns_0_and_prints_the_success_block(
        self, logger, restore, capsys
    ):
        assert emergency.cmd_restore_db(ENV, snapshot=None, time="2026-08-13T12:00:00Z") == 0
        assert f"New instance: {TARGET_ID}" in capsys.readouterr().out
        assert "rds: Restoring to point in time 2026-08-13T12:00:00+00:00" in logger.lines

    def test_an_error_payload_returns_1_and_prints_its_message(self, logger, restore, capsys):
        restore.time_result = _error("Restore time is after the latest restorable time.")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time="2026-08-13T12:00:00Z") == 1
        assert "after the latest restorable time" in capsys.readouterr().out

    def test_a_none_return_returns_1_with_a_generic_message(self, logger, restore, capsys):
        restore.time_result = None
        assert emergency.cmd_restore_db(ENV, snapshot=None, time="2026-08-13T12:00:00Z") == 1
        assert "Failed to initiate restore" in capsys.readouterr().out


class TestCmdRestoreDbInteractive:
    """cmd_restore_db() with neither flag -- the menu and its two re-entries."""

    def test_no_snapshots_returns_1_before_prompting(self, logger, restore, capsys):
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 1
        assert "No snapshots found" in capsys.readouterr().out

    def test_snapshots_are_listed_zero_based_with_type_and_date(
        self, logger, restore, monkeypatch, capsys
    ):
        restore.snapshots = [_snapshot("snap-new"), _snapshot("snap-old")]
        _answers(monkeypatch, "0")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        out = capsys.readouterr().out
        assert "  0. snap-new" in out
        assert "  1. snap-old" in out
        assert "manual" in out
        assert "2026-08-13 09:00 UTC" in out

    def test_a_snapshot_with_no_created_at_or_type_still_lists(
        self, logger, restore, monkeypatch, capsys
    ):
        restore.snapshots = [{"id": "snap-bare"}]
        _answers(monkeypatch, "0")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        assert "  0. snap-bare" in capsys.readouterr().out

    def test_a_snapshot_entry_without_an_id_raises_key_error(self, logger, restore, monkeypatch):
        # Pinned, not endorsed: `snap['id']` is a subscript inside the listing
        # loop while `created_at` and `type` next to it are .get()s.
        restore.snapshots = [{"created_at": "2026-08-13T09:00:00+00:00"}]
        with pytest.raises(KeyError, match="id"):
            emergency.cmd_restore_db(ENV, snapshot=None, time=None)

    def test_the_latest_restorable_time_is_shown_when_available(
        self, logger, restore, monkeypatch, capsys
    ):
        restore.snapshots = [_snapshot("snap-1")]
        restore.details = _details(latest_restorable_time=datetime(2026, 8, 13, 11, 0, tzinfo=UTC))
        _answers(monkeypatch, "0")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        assert (
            "Point-in-time recovery is available up to: 2026-08-13T11:00:00+00:00"
            in capsys.readouterr().out
        )

    def test_no_instance_details_hides_the_point_in_time_line(
        self, logger, restore, monkeypatch, capsys
    ):
        restore.snapshots = [_snapshot("snap-1")]
        restore.details = None
        _answers(monkeypatch, "0")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        assert "Point-in-time recovery is available" not in capsys.readouterr().out

    def test_a_null_latest_restorable_time_hides_the_point_in_time_line(
        self, logger, restore, monkeypatch, capsys
    ):
        restore.snapshots = [_snapshot("snap-1")]
        restore.details = _details(latest_restorable_time=None)
        _answers(monkeypatch, "0")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        assert "Point-in-time recovery is available" not in capsys.readouterr().out

    def test_an_empty_selection_returns_declined(self, logger, restore, monkeypatch, capsys):
        """An empty answer at the menu is a decline, not a failed restore."""
        restore.snapshots = [_snapshot("snap-1")]
        prompts = _answers(monkeypatch, "")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == emergency.EXIT_DECLINED
        assert prompts == ["Selection: "]
        assert "Cancelled" in capsys.readouterr().out

    def test_a_number_re_enters_the_command_with_that_snapshot(self, logger, restore, monkeypatch):
        restore.snapshots = [_snapshot("snap-new"), _snapshot("snap-old")]
        _answers(monkeypatch, "1")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        assert restore.from_snapshot == [(RDS_ID, "snap-old")]

    def test_an_out_of_range_number_returns_1(self, logger, restore, monkeypatch, capsys):
        restore.snapshots = [_snapshot("snap-1")]
        _answers(monkeypatch, "9")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 1
        assert "Invalid selection" in capsys.readouterr().out
        assert restore.from_snapshot == []

    def test_a_negative_number_is_treated_as_a_timestamp_not_an_index(
        self, logger, restore, monkeypatch, capsys
    ):
        # Pinned, not endorsed: `str.isdigit()` is False for "-1", so the
        # `idx < 0` half of the range guard is unreachable -- a negative index
        # is routed into the point-in-time branch and fails ISO parsing there.
        restore.snapshots = [_snapshot("snap-1")]
        _answers(monkeypatch, "-1")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 1
        assert "Invalid time format: -1" in capsys.readouterr().out

    def test_a_timestamp_re_enters_the_command_as_a_point_in_time_restore(
        self, logger, restore, monkeypatch
    ):
        restore.snapshots = [_snapshot("snap-1")]
        _answers(monkeypatch, "2026-08-13T12:00:00Z")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        assert restore.from_time == [(RDS_ID, datetime(2026, 8, 13, 12, 0, tzinfo=UTC))]
        assert restore.from_snapshot == []

    def test_junk_text_re_enters_and_fails_iso_parsing(self, logger, restore, monkeypatch, capsys):
        restore.snapshots = [_snapshot("snap-1")]
        _answers(monkeypatch, "the other day")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 1
        assert "Invalid time format: the other day" in capsys.readouterr().out

    def test_the_re_entry_reloads_the_context_and_logs_the_action_twice(
        self, logger, restore, monkeypatch
    ):
        # Pinned, not endorsed: the recursion goes through _init_rds_command
        # again, so "restore-db" is written to the emergency log once per
        # re-entry rather than once per operator invocation.
        restore.snapshots = [_snapshot("snap-1")]
        _answers(monkeypatch, "0")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        assert logger.lines.count("action: restore-db") == 2


@pytest.fixture
def moto_rds(mocked_aws) -> str:
    """Create a postgres RDS instance in moto, with the VPC scaffolding.

    A restore passes the source instance's DBSubnetGroupName straight to
    boto3, which rejects None, so the source must live in a real subnet group.

    Returns:
        The identifier of a completed manual snapshot of the instance.
    """
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet_ids = [
        ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=az)["Subnet"]["SubnetId"]
        for cidr, az in (("10.0.1.0/24", f"{REGION}a"), ("10.0.2.0/24", f"{REGION}b"))
    ]
    security_group_id = ec2.create_security_group(
        GroupName="restore-sg", Description="test db security group", VpcId=vpc_id
    )["GroupId"]

    client = boto3.client("rds", region_name=REGION)
    client.create_db_subnet_group(
        DBSubnetGroupName=SUBNET_GROUP,
        DBSubnetGroupDescription="test subnet group",
        SubnetIds=subnet_ids,
    )
    client.create_db_instance(
        DBInstanceIdentifier=RDS_ID,
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        EngineVersion="16.3",
        AllocatedStorage=20,
        MasterUsername="dbadmin",
        MasterUserPassword="testing-password",  # pragma: allowlist secret
        DBSubnetGroupName=SUBNET_GROUP,
        VpcSecurityGroupIds=[security_group_id],
    )
    snapshot_id = f"{RDS_ID}-manual-1"
    client.create_db_snapshot(DBInstanceIdentifier=RDS_ID, DBSnapshotIdentifier=snapshot_id)
    return snapshot_id


class TestCmdRestoreDbAgainstMoto:
    """The command driven end to end against the real producers.

    Nothing below the CLI is stubbed here, so these pins tie the consumer's
    subscript reads to the payloads the producers really build.
    """

    def test_a_real_snapshot_restore_prints_the_real_payload(self, logger, moto_rds, capsys):
        assert emergency.cmd_restore_db(ENV, snapshot=moto_rds, time=None) == 0
        out = capsys.readouterr().out
        assert f"New instance: {TARGET_ID}" in out
        # The producer's `message` is multi-line and carries the follow-up
        # command; _print_restore_success prints it verbatim.
        assert f"Restore initiated. Instance '{TARGET_ID}' will be available" in out
        assert f"aws rds describe-db-instances --db-instance-identifier {TARGET_ID}" in out
        assert f"rds: Restore initiated: {TARGET_ID}" in logger.lines

    def test_restoring_twice_hits_the_already_exists_error_payload(self, logger, moto_rds, capsys):
        assert emergency.cmd_restore_db(ENV, snapshot=moto_rds, time=None) == 0
        capsys.readouterr()
        assert emergency.cmd_restore_db(ENV, snapshot=moto_rds, time=None) == 1
        assert f"Instance '{TARGET_ID}' already exists." in capsys.readouterr().out

    def test_a_missing_source_instance_answers_the_generic_failure(
        self, logger, mocked_aws, capsys, monkeypatch
    ):
        # No instance was created, so _prepare_restore's details lookup answers
        # None and the command reports only "Failed to initiate restore".
        assert emergency.cmd_restore_db(ENV, snapshot="no-such-snapshot", time=None) == 1
        assert "Failed to initiate restore" in capsys.readouterr().out

    def test_the_real_interactive_menu_lists_the_real_snapshot(
        self, logger, moto_rds, monkeypatch, capsys
    ):
        _answers(monkeypatch, "0")
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=None) == 0
        out = capsys.readouterr().out
        assert f"  0. {moto_rds}" in out
        assert "manual" in out
        assert f"New instance: {TARGET_ID}" in out

    def test_the_real_point_in_time_path_reports_an_unreachable_time(
        self, logger, moto_rds, capsys
    ):
        # moto reports a LatestRestorableTime, so the producer's own
        # "after the latest restorable time" error payload is reachable -- and
        # it is the one payload with status == "error" that the --time arm
        # reads.
        future = (datetime.now(UTC) + timedelta(days=365)).isoformat()
        assert emergency.cmd_restore_db(ENV, snapshot=None, time=future) == 1
        assert "is after the latest" in capsys.readouterr().out
