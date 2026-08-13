"""Tests for bin/ops.py — the read-only monitoring tool."""

import sys
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

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
