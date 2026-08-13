"""Tests for bin/ops.py — the read-only monitoring tool.

The cmd_status() tests are characterization tests: they pin the section
layout, the ordering and the fallback lines that the command prints today,
so a decomposition into per-section helpers can be shown to preserve it.
"""

import sys
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from deployer.emergency.checkpoint import ServiceState
from deployer.utils import EnvironmentInfrastructure

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_ops_spec = spec_from_file_location("ops", bin_dir / "ops.py")
ops = module_from_spec(_ops_spec)
_ops_spec.loader.exec_module(ops)


def _incident(*timeline: str) -> str:
    """Build an incident file body the way cmd_incident_start() writes it."""
    entries = "".join(f"- {line}\n" for line in timeline)
    return (
        "# Incident: web 500s\n"
        "Started: 2026-08-13T09:00:00\n"
        "Environment: myapp-production\n"
        "Status: OPEN\n"
        "\n"
        "## Initial State\n"
        "- web: 2/2 running\n"
        "\n"
        "## Timeline\n"
        f"{entries}"
        "\n"
        "## Resolution\n"
    )


AT_0915 = datetime(2026, 8, 13, 9, 15)
AT_0930 = datetime(2026, 8, 13, 9, 30)


class TestAppendTimelineNote:
    """Tests for _append_timeline_note()."""

    def test_note_lands_above_the_resolution_heading(self):
        result = ops._append_timeline_note(
            _incident("09:00 Incident started"), "rolled back web", AT_0915
        )
        assert result == _incident("09:00 Incident started", "09:15 rolled back web")

    def test_repeated_notes_stay_one_tight_list(self):
        content = _incident("09:00 Incident started")
        content = ops._append_timeline_note(content, "first", AT_0915)
        content = ops._append_timeline_note(content, "second", AT_0930)
        assert content == _incident("09:00 Incident started", "09:15 first", "09:30 second")

    def test_initial_state_section_is_untouched(self):
        result = ops._append_timeline_note(_incident("09:00 Incident started"), "note", AT_0915)
        assert "## Initial State\n- web: 2/2 running\n" in result
        assert result.count("## Resolution\n") == 1


class TestIncidentNoteAndResolve:
    """Tests for the two commands that splice into an incident file."""

    def _open_incident(self, monkeypatch, tmp_path) -> Path:
        incident = tmp_path / "2026-08-13-0900-web-500s.md"
        incident.write_text(_incident("09:00 Incident started"))
        monkeypatch.setattr(ops, "_require_open_incident", lambda: incident)
        return incident

    def test_note_appends_to_the_timeline(self, monkeypatch, tmp_path):
        incident = self._open_incident(monkeypatch, tmp_path)
        assert ops.cmd_incident_note("scaled web to 4") == 0

        body = incident.read_text()
        timeline = body.split("## Timeline\n")[1].split("\n\n## Resolution")[0]
        assert "Incident started" in timeline
        assert "scaled web to 4" in timeline
        assert body.count("## Resolution\n") == 1

    def test_resolve_appends_a_note_and_stamps_the_resolution(self, monkeypatch, tmp_path):
        incident = self._open_incident(monkeypatch, tmp_path)
        assert ops.cmd_incident_resolve() == 0

        body = incident.read_text()
        assert "Status: RESOLVED" in body
        assert "Status: OPEN" not in body

        timeline, resolution = body.split("## Resolution\n")
        assert "Incident resolved" in timeline
        assert resolution.startswith("Resolved: ")

    def test_resolve_still_appends_after_earlier_notes(self, monkeypatch, tmp_path):
        """The resolve note must land after existing notes, not replace them."""
        incident = self._open_incident(monkeypatch, tmp_path)
        ops.cmd_incident_note("first note")
        ops.cmd_incident_resolve()

        timeline = incident.read_text().split("## Resolution\n")[0]
        assert timeline.index("first note") < timeline.index("Incident resolved")
        assert "Incident started" in timeline


ENV = "myapp-production"
CLUSTER = "myapp-production-cluster"
RDS_ID = "myapp-production-db"


def _arn(service: str, revision: int) -> str:
    """Build a task definition ARN the way get_all_services_state() reports it."""
    return f"arn:aws:ecs:us-west-2:111111111111:task-definition/{ENV}-{service}:{revision}"


@pytest.fixture
def status_env(monkeypatch):
    """Wire cmd_status()'s five AWS collaborators to in-memory fixtures.

    Returns the mutable dict the stubs read from, so each test can vary one
    piece of the environment without restating the rest.
    """
    env = {
        "config": {},
        "cluster_name": CLUSTER,
        "rds_id": RDS_ID,
        "services": {
            "web": ServiceState(task_definition=_arn("web", 7), desired_count=2, running_count=2),
        },
        "revisions": [{"revision": 7, "registered_at": "2026-08-07T14:30:00Z"}],
        "rds_status": {
            "status": "available",
            "instance_class": "db.t4g.micro",
            "engine": "postgres",
        },
        "snapshots": [
            {
                "id": "rds:myapp-2026-08-07",
                "type": "automated",
                "created_at": "2026-08-07T06:00:00Z",
            }
        ],
    }

    monkeypatch.setattr(
        ops,
        "load_environment_infrastructure",
        lambda _e: EnvironmentInfrastructure(
            config=env["config"], cluster_name=env["cluster_name"], rds_id=env["rds_id"]
        ),
    )
    monkeypatch.setattr(ops, "get_all_services_state", lambda _cluster: env["services"])
    monkeypatch.setattr(
        ops, "list_task_definition_revisions", lambda _family, **_kw: env["revisions"]
    )
    monkeypatch.setattr(ops.rds, "get_status", lambda _id: env["rds_status"])
    monkeypatch.setattr(ops, "get_rds_snapshots", lambda _id, **_kw: env["snapshots"])
    return env


class TestCmdStatus:
    """Characterization tests for cmd_status()'s five report sections."""

    def test_every_section_is_rendered(self, status_env, capsys):
        status_env["config"] = {
            "services": {
                "scaling": {"web": {"min_replicas": 2, "max_replicas": 8, "cpu_target": 60}}
            }
        }

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert f"Environment: {ENV}" in out
        assert "ECS Services:" in out
        assert f"  web                       2          2          {ENV}-web:7" in out
        assert "Recent Task Definitions:" in out
        assert "  web:" in out
        assert "    revision   7 - 2026-08-07 14:30 UTC" in out
        assert f"RDS Instance: {RDS_ID}" in out
        assert "  Status: available" in out
        assert "  Class: db.t4g.micro" in out
        assert "  Engine: postgres" in out
        assert "Recent Snapshots:" in out
        assert "rds:myapp-2026-08-07" in out
        assert "2026-08-07 06:00 UTC" in out
        assert "  web: min=2, max=8, cpu_target=60%" in out

    def test_sections_appear_in_report_order(self, status_env, capsys):
        status_env["config"] = {"services": {"scaling": {"web": {}}}}

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        headings = [
            "ECS Services:",
            "Recent Task Definitions:",
            f"RDS Instance: {RDS_ID}",
            "Recent Snapshots:",
            "Auto-Scaling Configuration:",
        ]
        assert [out.index(h) for h in headings] == sorted(out.index(h) for h in headings)

    def test_missing_scaling_values_render_as_question_marks(self, status_env, capsys):
        status_env["config"] = {"services": {"scaling": {"web": {}}}}

        assert ops.cmd_status(ENV) == 0
        assert "  web: min=?, max=?, cpu_target=?%" in capsys.readouterr().out

    def test_absent_scaling_config_prints_no_section(self, status_env, capsys):
        assert ops.cmd_status(ENV) == 0
        assert "Auto-Scaling Configuration:" not in capsys.readouterr().out

    def test_an_unknown_cluster_skips_both_ecs_sections(self, status_env, capsys):
        status_env["cluster_name"] = None

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "Unable to determine ECS cluster name" in out
        assert "ECS Services:" not in out
        assert "Recent Task Definitions:" not in out

    def test_an_empty_cluster_still_prints_the_heading(self, status_env, capsys):
        status_env["services"] = {}

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "ECS Services:" in out
        assert "  No services found" in out
        assert "Recent Task Definitions:" in out

    def test_a_service_with_no_revisions_is_omitted_from_the_revision_list(
        self, status_env, capsys
    ):
        status_env["revisions"] = []

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "Recent Task Definitions:" in out
        assert "revision" not in out.split("Recent Task Definitions:")[1]

    def test_an_unconfigured_rds_skips_both_rds_sections(self, status_env, capsys):
        status_env["rds_id"] = None

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "RDS instance not configured" in out
        assert "RDS Instance:" not in out
        assert "Recent Snapshots:" not in out

    def test_an_unreadable_rds_status_is_reported_in_place(self, status_env, capsys):
        status_env["rds_status"] = None

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert f"RDS Instance: {RDS_ID}" in out
        assert "  Unable to retrieve status" in out

    def test_no_snapshots_prints_no_snapshot_section(self, status_env, capsys):
        status_env["snapshots"] = []

        assert ops.cmd_status(ENV) == 0
        assert "Recent Snapshots:" not in capsys.readouterr().out

    def test_unparseable_timestamps_are_shown_verbatim(self, status_env, capsys):
        status_env["revisions"] = [{"revision": 7}]
        status_env["snapshots"] = [{"id": "manual-snap"}]

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "    revision   7 - unknown" in out
        assert "manual-snap" in out
        assert out.count("unknown") == 2
