"""Tests for deployer.emergency.checkpoint module.

Pure-logic tests: no AWS involved. The checkpoint directory is redirected
into tmp_path by the ``checkpoint_dir`` fixture (tests/conftest.py).
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from deployer.emergency.checkpoint import (
    Checkpoint,
    RdsState,
    ServiceState,
    cleanup_old_checkpoints,
    create_checkpoint,
    generate_checkpoint_filename,
    get_checkpoint_dir,
    list_checkpoints,
    load_checkpoint,
)

FILENAME_PATTERN = re.compile(r"^emergency-\d{4}-\d{2}-\d{2}-\d{6}\.json$")


def make_payload(
    *,
    timestamp: str = "2026-02-04T12:00:00Z",
    environment: str = "production",
    services: dict | None = None,
    rds: dict | None = None,
) -> dict:
    """Build a checkpoint JSON payload as it appears on disk."""
    state: dict = {"services": services if services is not None else {}}
    if rds is not None:
        state["rds"] = rds
    return {
        "timestamp": timestamp,
        "environment": environment,
        "action": "rollback",
        "reason": "bad deploy",
        "state": state,
    }


def write_checkpoint_file(directory: Path, filename: str, payload: dict | str) -> Path:
    """Write a checkpoint file (dict is JSON-encoded, str is written verbatim)."""
    directory.mkdir(parents=True, exist_ok=True)
    filepath = directory / filename
    text = payload if isinstance(payload, str) else json.dumps(payload)
    filepath.write_text(text, encoding="utf-8")
    return filepath


def timestamp_days_ago(days: float) -> str:
    """Checkpoint-format timestamp for `days` ago."""
    moment = datetime.now(UTC) - timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestCheckpointSerialization:
    """Checkpoint.to_dict / from_dict."""

    def test_to_dict_includes_services_and_rds(self):
        checkpoint = Checkpoint(
            timestamp="2026-02-04T12:00:00Z",
            environment="production",
            action="rollback",
            reason="bad deploy",
            services={"web": ServiceState("arn:web:42", desired_count=2, running_count=2)},
            rds=RdsState(instance_id="myapp-production-db", status="available"),
        )

        data = checkpoint.to_dict()

        assert data["timestamp"] == "2026-02-04T12:00:00Z"
        assert data["environment"] == "production"
        assert data["action"] == "rollback"
        assert data["reason"] == "bad deploy"
        assert data["state"]["services"] == {
            "web": {
                "task_definition": "arn:web:42",
                "desired_count": 2,
                "running_count": 2,
            }
        }
        assert data["state"]["rds"] == {
            "instance_id": "myapp-production-db",
            "status": "available",
        }

    def test_to_dict_omits_rds_when_absent(self):
        checkpoint = Checkpoint(
            timestamp="2026-02-04T12:00:00Z",
            environment="staging",
            action="scale",
            reason="load spike",
        )

        data = checkpoint.to_dict()

        assert "rds" not in data["state"]
        assert data["state"]["services"] == {}

    def test_round_trip_with_rds(self):
        original = Checkpoint(
            timestamp="2026-02-04T12:00:00Z",
            environment="production",
            action="rollback",
            reason="bad deploy",
            services={"web": ServiceState("arn:web:42", desired_count=2, running_count=1)},
            rds=RdsState(instance_id="myapp-production-db", status="available"),
        )

        restored = Checkpoint.from_dict(original.to_dict(), filename="emergency-x.json")

        assert restored.services == original.services
        assert restored.rds == original.rds
        assert restored.timestamp == original.timestamp
        assert restored.filename == "emergency-x.json"

    def test_round_trip_without_rds(self):
        original = Checkpoint(
            timestamp="2026-02-04T12:00:00Z",
            environment="staging",
            action="scale",
            reason="load spike",
            services={"worker": ServiceState("arn:worker:7", desired_count=4, running_count=4)},
        )

        restored = Checkpoint.from_dict(original.to_dict())

        assert restored.rds is None
        assert restored.services == original.services
        assert restored.filename is None

    def test_from_dict_without_state_key(self):
        """A payload with no 'state' key loads as an empty-state checkpoint."""
        data = {
            "timestamp": "2026-02-04T12:00:00Z",
            "environment": "production",
            "action": "rollback",
            "reason": "bad deploy",
        }

        checkpoint = Checkpoint.from_dict(data)

        assert checkpoint.services == {}
        assert checkpoint.rds is None

    def test_from_dict_missing_required_key_raises(self):
        """Missing top-level keys raise KeyError -- list_checkpoints relies on this."""
        with pytest.raises(KeyError):
            Checkpoint.from_dict({"timestamp": "2026-02-04T12:00:00Z"})


class TestGenerateCheckpointFilename:
    def test_filename_shape(self):
        assert FILENAME_PATTERN.match(generate_checkpoint_filename())

    def test_filename_uses_current_utc_date(self):
        filename = generate_checkpoint_filename()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        assert filename.startswith(f"emergency-{today}-")


class TestGetCheckpointDir:
    def test_dir_is_under_deployer_root(self, checkpoint_dir: Path, tmp_path: Path):
        assert get_checkpoint_dir() == tmp_path / "local" / "checkpoints"
        assert get_checkpoint_dir() == checkpoint_dir


class TestCreateCheckpoint:
    def test_creates_directory_and_parseable_file(self, checkpoint_dir: Path):
        assert not checkpoint_dir.exists()

        checkpoint = create_checkpoint(
            environment="production",
            action="rollback",
            reason="bad deploy",
            services={"web": ServiceState("arn:web:42", desired_count=2, running_count=2)},
            rds=RdsState(instance_id="myapp-production-db", status="available"),
        )

        assert checkpoint_dir.is_dir()
        assert checkpoint.filename is not None
        assert FILENAME_PATTERN.match(checkpoint.filename)

        filepath = checkpoint_dir / checkpoint.filename
        assert filepath.exists()
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data == checkpoint.to_dict()
        assert data["state"]["rds"]["instance_id"] == "myapp-production-db"

    def test_written_file_is_the_only_one(self, checkpoint_dir: Path):
        checkpoint = create_checkpoint(
            environment="production",
            action="scale",
            reason="load spike",
            services={},
            rds=None,
        )

        assert [p.name for p in checkpoint_dir.iterdir()] == [checkpoint.filename]

    def test_timestamp_is_utc_iso_seconds(self, checkpoint_dir: Path):
        checkpoint = create_checkpoint(
            environment="production",
            action="scale",
            reason="load spike",
            services={},
            rds=None,
        )

        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", checkpoint.timestamp)


class TestLoadCheckpoint:
    def test_loads_created_checkpoint(self, checkpoint_dir: Path):
        created = create_checkpoint(
            environment="production",
            action="rollback",
            reason="bad deploy",
            services={"web": ServiceState("arn:web:42", desired_count=3, running_count=3)},
            rds=None,
        )

        loaded = load_checkpoint(created.filename)

        assert loaded.environment == "production"
        assert loaded.action == "rollback"
        assert loaded.services["web"].desired_count == 3
        assert loaded.filename == created.filename

    def test_missing_file_raises(self, checkpoint_dir: Path):
        with pytest.raises(FileNotFoundError):
            load_checkpoint("emergency-2026-02-04-120000.json")


class TestListCheckpoints:
    def test_returns_empty_when_dir_missing(self, checkpoint_dir: Path):
        assert not checkpoint_dir.exists()
        assert list_checkpoints("production") == []

    def test_filters_by_environment(self, checkpoint_dir: Path):
        write_checkpoint_file(
            checkpoint_dir,
            "emergency-2026-02-04-120000.json",
            make_payload(environment="production"),
        )
        write_checkpoint_file(
            checkpoint_dir,
            "emergency-2026-02-04-130000.json",
            make_payload(environment="staging"),
        )

        result = list_checkpoints("production")

        assert [c.filename for c in result] == ["emergency-2026-02-04-120000.json"]
        assert result[0].environment == "production"

    def test_sorts_newest_first(self, checkpoint_dir: Path):
        for name, ts in (
            ("emergency-2026-02-04-120000.json", "2026-02-04T12:00:00Z"),
            ("emergency-2026-02-06-090000.json", "2026-02-06T09:00:00Z"),
            ("emergency-2026-02-05-180000.json", "2026-02-05T18:00:00Z"),
        ):
            write_checkpoint_file(checkpoint_dir, name, make_payload(timestamp=ts))

        result = list_checkpoints("production")

        assert [c.timestamp for c in result] == [
            "2026-02-06T09:00:00Z",
            "2026-02-05T18:00:00Z",
            "2026-02-04T12:00:00Z",
        ]

    def test_skips_malformed_files(self, checkpoint_dir: Path):
        write_checkpoint_file(checkpoint_dir, "emergency-2026-02-04-120000.json", make_payload())
        # Unparseable JSON -> json.JSONDecodeError
        write_checkpoint_file(checkpoint_dir, "emergency-2026-02-05-120000.json", "{not json")
        # Valid JSON, missing a required key -> KeyError
        write_checkpoint_file(
            checkpoint_dir,
            "emergency-2026-02-06-120000.json",
            {"timestamp": "2026-02-06T12:00:00Z"},
        )

        result = list_checkpoints("production")

        assert [c.filename for c in result] == ["emergency-2026-02-04-120000.json"]

    def test_ignores_non_checkpoint_filenames(self, checkpoint_dir: Path):
        write_checkpoint_file(checkpoint_dir, "notes.json", make_payload())
        write_checkpoint_file(checkpoint_dir, "emergency-2026-02-04-120000.json", make_payload())

        assert len(list_checkpoints("production")) == 1


class TestCleanupOldCheckpoints:
    def test_returns_empty_when_nothing_to_list(self, checkpoint_dir: Path):
        assert cleanup_old_checkpoints(environment="production") == []

    def test_keeps_recent_count_regardless_of_age(self, checkpoint_dir: Path):
        """The keep_count window wins over keep_days: nothing is deleted."""
        for i in range(3):
            write_checkpoint_file(
                checkpoint_dir,
                f"emergency-2020-01-0{i + 1}-120000.json",
                make_payload(timestamp=timestamp_days_ago(500 + i)),
            )

        deleted = cleanup_old_checkpoints(keep_count=10, keep_days=7, environment="production")

        assert deleted == []
        assert len(list(checkpoint_dir.glob("emergency-*.json"))) == 3

    def test_deletes_old_checkpoints_outside_keep_count(self, checkpoint_dir: Path):
        recent = "emergency-2026-02-10-120000.json"
        write_checkpoint_file(checkpoint_dir, recent, make_payload(timestamp=timestamp_days_ago(0)))
        old_names = ["emergency-2026-01-01-120000.json", "emergency-2026-01-02-120000.json"]
        for offset, name in enumerate(old_names):
            write_checkpoint_file(
                checkpoint_dir, name, make_payload(timestamp=timestamp_days_ago(30 + offset))
            )

        deleted = cleanup_old_checkpoints(keep_count=1, keep_days=7, environment="production")

        assert sorted(deleted) == sorted(old_names)
        assert [p.name for p in checkpoint_dir.glob("emergency-*.json")] == [recent]

    def test_keeps_young_checkpoints_outside_keep_count(self, checkpoint_dir: Path):
        """Outside keep_count but inside keep_days -> kept."""
        for i in range(3):
            write_checkpoint_file(
                checkpoint_dir,
                f"emergency-2026-02-0{i + 1}-120000.json",
                make_payload(timestamp=timestamp_days_ago(i)),
            )

        deleted = cleanup_old_checkpoints(keep_count=1, keep_days=7, environment="production")

        assert deleted == []
        assert len(list(checkpoint_dir.glob("emergency-*.json"))) == 3

    def test_ignores_other_environments(self, checkpoint_dir: Path):
        write_checkpoint_file(
            checkpoint_dir,
            "emergency-2026-01-01-120000.json",
            make_payload(environment="staging", timestamp=timestamp_days_ago(30)),
        )

        deleted = cleanup_old_checkpoints(keep_count=0, keep_days=7, environment="production")

        assert deleted == []
        assert (checkpoint_dir / "emergency-2026-01-01-120000.json").exists()

    def test_skips_unparseable_timestamp(self, checkpoint_dir: Path):
        write_checkpoint_file(
            checkpoint_dir,
            "emergency-2026-01-01-120000.json",
            make_payload(timestamp="not-a-timestamp"),
        )

        deleted = cleanup_old_checkpoints(keep_count=0, keep_days=7, environment="production")

        assert deleted == []
        assert (checkpoint_dir / "emergency-2026-01-01-120000.json").exists()
