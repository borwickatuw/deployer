"""Tests for bin/ops.py — the read-only monitoring tool.

The cmd_status() tests are characterization tests: they pin the section
layout, the ordering and the fallback lines that the command prints today,
so a decomposition into per-section helpers can be shown to preserve it.

cmd_health(), cmd_maintenance(), cmd_ecr() and cmd_incident_start() have the
same, and three cmd_status() pins drive the *real* emergency/ producers against
a client that refuses every call rather than stubbing them to return their
sentinel — so they would fail if a producer went back to swallowing.

Those three assert the rule in docs/internal/DECISIONS.md
§ "2026-08-18: Error Contracts": this file is read-only, so it catches at each
render boundary and reports the failure **in place**, following
_print_rds_status(). One unreadable section must not delete itself from the
report, and must not stop the others printing.

cmd_logs(), cmd_audit(), cmd_incident_list() and the Click layer are pinned at
the end of the file. The boto3 collectors these commands read from are pinned
separately, against botocore's Stubber, in test_ops_aws.py.
"""

import sys
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from click.testing import CliRunner

from deployer.aws.rds import RdsStatus
from deployer.emergency import ecs
from deployer.emergency import rds as emergency_rds
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
        incident.write_text(_incident("09:00 Incident started"), encoding="utf-8")
        monkeypatch.setattr(ops, "_require_open_incident", lambda: incident)
        return incident

    def test_note_appends_to_the_timeline(self, monkeypatch, tmp_path):
        incident = self._open_incident(monkeypatch, tmp_path)
        assert ops.cmd_incident_note("scaled web to 4") == 0

        body = incident.read_text(encoding="utf-8")
        timeline = body.split("## Timeline\n")[1].split("\n\n## Resolution")[0]
        assert "Incident started" in timeline
        assert "scaled web to 4" in timeline
        assert body.count("## Resolution\n") == 1

    def test_resolve_appends_a_note_and_stamps_the_resolution(self, monkeypatch, tmp_path):
        incident = self._open_incident(monkeypatch, tmp_path)
        assert ops.cmd_incident_resolve() == 0

        body = incident.read_text(encoding="utf-8")
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

        timeline = incident.read_text(encoding="utf-8").split("## Resolution\n")[0]
        assert timeline.index("first note") < timeline.index("Incident resolved")
        assert "Incident started" in timeline


ENV = "myapp-production"
CLUSTER = "myapp-production-cluster"
RDS_ID = "myapp-production-db"


def _arn(service: str, revision: int) -> str:
    """Build a task definition ARN the way get_all_services_state() reports it."""
    return f"arn:aws:ecs:us-west-2:111111111111:task-definition/{ENV}-{service}:{revision}"


class _DeniedClient:
    """A boto3 client whose every call is refused, as a permissions gap would.

    Lets the swallow-a-ClientError pins run the *real* producer rather than a
    stub that returns the sentinel directly, so what is pinned is the
    end-to-end "failure reads as absence" and not the test's own shortcut.
    """

    def __init__(self, operation: str) -> None:
        self._operation = operation

    def __getattr__(self, _name: str):
        def _call(*_args, **_kwargs):
            raise ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
                self._operation,
            )

        return _call


def _denied_ecs_client():
    return _DeniedClient("DescribeServices")


def _denied_rds_client():
    return _DeniedClient("DescribeDBSnapshots")


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
        "rds_status": RdsStatus(
            identifier=RDS_ID,
            status="available",
            instance_class="db.t4g.micro",
            engine="postgres",
        ),
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
    monkeypatch.setattr(
        ops, "_describe_live_scaling", lambda _cluster, _service: "live: min=2, max=8"
    )
    return env


class TestCmdStatus:
    """Characterization tests for cmd_status()'s five report sections."""

    def test_every_section_is_rendered(self, status_env, capsys):
        status_env["config"] = {
            "services": {
                "scaling": {
                    "web": {
                        "min": 2,
                        "max": 8,
                        "steps": [{"depth": 1, "workers": 2}, {"depth": 25, "workers": 8}],
                    }
                }
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
        assert "  web: min=2, max=8 (depth>=1 -> 2, depth>=25 -> 8)" in out
        assert "    live: min=2, max=8" in out

    def test_sections_appear_in_report_order(self, status_env, capsys):
        status_env["config"] = {"services": {"scaling": {"web": {}}}}

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        headings = [
            "ECS Services:",
            "Recent Task Definitions:",
            f"RDS Instance: {RDS_ID}",
            "Recent Snapshots:",
            "Auto-Scaling Configuration (queue depth):",
        ]
        assert [out.index(h) for h in headings] == sorted(out.index(h) for h in headings)

    def test_missing_scaling_values_render_as_question_marks(self, status_env, capsys):
        status_env["config"] = {"services": {"scaling": {"web": {}}}}

        assert ops.cmd_status(ENV) == 0
        assert "  web: min=?, max=? (no steps)" in capsys.readouterr().out

    def test_scaling_without_a_cluster_prints_the_config_but_no_live_line(
        self, status_env, monkeypatch, capsys
    ):
        status_env["cluster_name"] = None
        status_env["config"] = {"services": {"scaling": {"worker": {"min": 1, "max": 4}}}}

        def no_live_lookup(_cluster, _service):
            raise AssertionError("no cluster to ask")

        monkeypatch.setattr(ops, "_describe_live_scaling", no_live_lookup)

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "  worker: min=1, max=4 (no steps)" in out
        assert "live:" not in out

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

    def test_an_unreadable_rds_status_is_reported_in_place(self, status_env, monkeypatch, capsys):
        """A describe that failed is reported as such, and the rest still prints."""

        def unreadable(_id):
            raise RuntimeError("ThrottlingException")

        monkeypatch.setattr(ops.rds, "get_status", unreadable)

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert f"RDS Instance: {RDS_ID}" in out
        assert "  Unable to retrieve status (ThrottlingException)" in out
        assert "Recent Snapshots:" in out

    def test_an_absent_rds_instance_is_distinguished_from_an_unreadable_one(
        self, status_env, capsys
    ):
        """None means the instance is gone, which is not the same as a failed look."""
        status_env["rds_status"] = None

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert f"RDS Instance: {RDS_ID}" in out
        assert "  No such instance" in out
        assert "Unable to retrieve status" not in out

    def test_no_snapshots_prints_no_snapshot_section(self, status_env, capsys):
        status_env["snapshots"] = []

        assert ops.cmd_status(ENV) == 0
        assert "Recent Snapshots:" not in capsys.readouterr().out

    def test_an_unreadable_cluster_is_reported_and_the_rest_still_prints(
        self, status_env, monkeypatch, capsys
    ):
        """Reported in place, not rendered as an empty cluster.

        The two ECS sections are skipped — there is nothing to put in them —
        but the RDS sections below still print. An ECS permissions gap is not
        a reason to withhold the rest of the status report.
        """
        monkeypatch.setattr(ops, "get_all_services_state", ecs.get_all_services_state)
        monkeypatch.setattr(ecs, "_get_ecs_client", _denied_ecs_client)

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "Unable to read ECS services" in out
        assert "AccessDeniedException" in out
        assert "  No services found" not in out
        assert f"RDS Instance: {RDS_ID}" in out

    def test_unreadable_task_definitions_are_reported_per_service(
        self, status_env, monkeypatch, capsys
    ):
        """Named in place, so one unreadable family does not stop the rest."""
        monkeypatch.setattr(
            ops, "list_task_definition_revisions", ecs.list_task_definition_revisions
        )
        monkeypatch.setattr(ecs, "_get_ecs_client", _denied_ecs_client)

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "Recent Task Definitions:" in out
        assert "  web: unable to list revisions" in out
        assert "AccessDeniedException" in out

    def test_unreadable_snapshots_keep_the_section_and_say_why(
        self, status_env, monkeypatch, capsys
    ):
        """The asymmetry with _print_rds_status(), closed.

        This section used to vanish entirely on a denied describe-db-snapshots,
        which reads as "there are no backups" — the answer that matters least
        when it is wrong.
        """
        monkeypatch.setattr(ops, "get_rds_snapshots", emergency_rds.get_rds_snapshots)
        monkeypatch.setattr(emergency_rds, "_get_rds_client", _denied_rds_client)

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert f"RDS Instance: {RDS_ID}" in out
        assert "Recent Snapshots:" in out
        assert "  Unable to retrieve snapshots" in out

    def test_unparseable_timestamps_are_shown_verbatim(self, status_env, capsys):
        status_env["revisions"] = [{"revision": 7}]
        status_env["snapshots"] = [{"id": "manual-snap"}]

        assert ops.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "    revision   7 - unknown" in out
        assert "manual-snap" in out
        assert out.count("unknown") == 2


# =============================================================================
# health / maintenance / ecr — the three commands with no coverage at all
# =============================================================================


@pytest.fixture
def env_config(monkeypatch, tmp_path):
    """Wire the get_environment_path + load_environment_config pair.

    cmd_health(), cmd_maintenance() and cmd_ecr() each read their config
    through this pair rather than through load_environment_infrastructure(),
    so status_env does not reach them. Returns the mutable config dict.

    Setting it to an exception makes load_environment_config raise, which is
    how the "unhandled" half of core/config.py's error contract is pinned.
    """
    config: dict = {}

    def _load(_env_path):
        if isinstance(config.get("_raise"), Exception):
            raise config["_raise"]
        return config

    monkeypatch.setattr(ops, "get_environment_path", lambda _e: tmp_path / _e)
    monkeypatch.setattr(ops, "load_environment_config", _load)
    return config


class TestCmdHealth:
    """Characterization tests for cmd_health()'s ALB target report."""

    def _targets(self, monkeypatch, targets: list[dict]) -> list[str]:
        """Stub the ALB lookup; return the list of ARNs it was asked about."""
        seen: list[str] = []
        monkeypatch.setattr(ops, "get_target_health", lambda arn: (seen.append(arn), targets)[1])
        return seen

    def _target(self, state="healthy", reason=None, description=None, target_id="10.0.1.5"):
        return {
            "target_id": target_id,
            "port": 8000,
            "health_state": state,
            "reason": reason,
            "description": description,
        }

    def test_an_unconfigured_target_group_returns_1(self, env_config, monkeypatch, capsys):
        self._targets(monkeypatch, [])

        assert ops.cmd_health(ENV) == 1
        assert "Target group ARN not configured" in capsys.readouterr().out

    def test_an_unreadable_target_group_returns_1_not_no_targets(
        self, env_config, monkeypatch, capsys
    ):
        """A refused describe used to print "No targets registered" and pass."""
        env_config["infrastructure"] = {"target_group_arn": "arn:tg"}

        def _refused(_arn):
            raise RuntimeError("Could not read target health for arn:tg: AccessDenied")

        monkeypatch.setattr(ops, "get_target_health", _refused)

        assert ops.cmd_health(ENV) == 1
        out = capsys.readouterr().out
        assert "Unable to read target health: " in out
        assert "AccessDenied" in out
        assert "No targets registered" not in out

    def test_no_registered_targets_returns_0(self, env_config, monkeypatch, capsys):
        env_config["infrastructure"] = {"target_group_arn": "arn:tg"}
        seen = self._targets(monkeypatch, [])

        assert ops.cmd_health(ENV) == 0
        assert seen == ["arn:tg"]
        assert "  No targets registered" in capsys.readouterr().out

    def test_all_healthy_targets_return_0(self, env_config, monkeypatch, capsys):
        env_config["infrastructure"] = {"target_group_arn": "arn:tg"}
        self._targets(monkeypatch, [self._target(), self._target(target_id="10.0.2.7")])

        assert ops.cmd_health(ENV) == 0
        out = capsys.readouterr().out
        assert "10.0.1.5:8000 - " in out
        assert "healthy" in out
        assert "2 healthy" in out
        assert "0 unhealthy" in out

    def test_an_unhealthy_target_returns_1_with_its_reason_and_details(
        self, env_config, monkeypatch, capsys
    ):
        env_config["infrastructure"] = {"target_group_arn": "arn:tg"}
        self._targets(
            monkeypatch,
            [self._target(state="unhealthy", reason="Target.Timeout", description="timed out")],
        )

        assert ops.cmd_health(ENV) == 1
        out = capsys.readouterr().out
        assert "unhealthy" in out
        assert "    Reason: Target.Timeout" in out
        assert "    Details: timed out" in out
        assert "1 unhealthy" in out

    def test_an_unreadable_config_exits_1_with_the_error_text(
        self, env_config, monkeypatch, capsys
    ):
        """One of the three sites that used to let this become a traceback.

        exit_on() wraps the single call that can fail, which is the boundary
        rule the error-contract ADR chose over a per-caller try.
        """
        env_config["_raise"] = FileNotFoundError("Config file not found: config.toml")

        with pytest.raises(SystemExit) as exit_info:
            ops.cmd_health(ENV)
        assert exit_info.value.code == 1
        assert "Config file not found: config.toml" in capsys.readouterr().err


class TestCmdMaintenance:
    """Characterization tests for cmd_maintenance()'s two-resource report."""

    def _maintenance(self, monkeypatch, rds_items=(), cache_items=()) -> list[tuple]:
        """Stub both pending-maintenance collectors; return the calls they received.

        An Exception instance in place of the items is raised instead.
        """
        calls: list[tuple] = []

        def _collector(kind, items):
            def _get(resource_id):
                calls.append((kind, resource_id))
                if isinstance(items, Exception):
                    raise items
                return list(items)

            return _get

        monkeypatch.setattr(ops, "get_rds_pending_maintenance", _collector("rds", rds_items))
        monkeypatch.setattr(
            ops, "get_elasticache_pending_maintenance", _collector("elasticache", cache_items)
        )
        return calls

    def test_neither_resource_configured_says_so_and_returns_0(
        self, env_config, monkeypatch, capsys
    ):
        calls = self._maintenance(monkeypatch)

        assert ops.cmd_maintenance(ENV) == 0
        out = capsys.readouterr().out
        assert calls == []
        assert "RDS: Not configured" in out
        assert "ElastiCache: Not configured" in out
        assert "No pending maintenance" in out

    def test_a_cache_url_derives_the_elasticache_cluster_id(self, env_config, monkeypatch):
        env_config["infrastructure"] = {"rds_instance_id": RDS_ID}
        env_config["cache"] = {"url": "redis://cache:6379"}
        calls = self._maintenance(monkeypatch)

        assert ops.cmd_maintenance(ENV) == 0
        assert calls == [("rds", RDS_ID), ("elasticache", f"{ENV}-cache")]

    def test_an_unreadable_resource_is_reported_in_place_and_fails(
        self, env_config, monkeypatch, capsys
    ):
        """A refused read is not "No pending maintenance", and does not hide the other."""
        env_config["infrastructure"] = {"rds_instance_id": RDS_ID}
        env_config["cache"] = {"url": "redis://cache:6379"}
        self._maintenance(
            monkeypatch,
            rds_items=RuntimeError("AccessDenied"),
            cache_items=[{"action": "update-1", "description": "Engine patch", "severity": None}],
        )

        assert ops.cmd_maintenance(ENV) == 1
        out = capsys.readouterr().out
        assert f"RDS ({RDS_ID}):" in out
        assert "unable to read (AccessDenied)" in out
        assert "No pending maintenance" not in out
        assert "  - update-1: Engine patch" in out
        assert "Could not read pending maintenance for 1 resource(s)" in out

    def test_nothing_pending_reports_each_resource_as_clear(self, env_config, monkeypatch, capsys):
        env_config["infrastructure"] = {"rds_instance_id": RDS_ID}
        env_config["cache"] = {"url": "redis://cache:6379"}
        self._maintenance(monkeypatch)

        assert ops.cmd_maintenance(ENV) == 0
        out = capsys.readouterr().out
        assert f"RDS ({RDS_ID}): No pending maintenance" in out
        assert f"ElastiCache ({ENV}-cache): No pending maintenance" in out

    def test_pending_rds_actions_are_listed_with_their_dates(self, env_config, monkeypatch, capsys):
        env_config["infrastructure"] = {"rds_instance_id": RDS_ID}
        self._maintenance(
            monkeypatch,
            rds_items=[
                {
                    "action": "system-update",
                    "description": "Operating system update",
                    "auto_apply_after": "2026-09-01T00:00:00Z",
                    "current_apply_date": "2026-09-05T00:00:00Z",
                }
            ],
        )

        assert ops.cmd_maintenance(ENV) == 0
        out = capsys.readouterr().out
        assert "  - system-update: Operating system update" in out
        assert "    Auto-apply after: 2026-09-01T00:00:00Z" in out
        assert "    Scheduled: 2026-09-05T00:00:00Z" in out
        assert "Pending maintenance found" in out

    def test_an_rds_action_with_no_dates_prints_no_date_lines(
        self, env_config, monkeypatch, capsys
    ):
        """get_rds_pending_maintenance() reports an absent date as None."""
        env_config["infrastructure"] = {"rds_instance_id": RDS_ID}
        self._maintenance(
            monkeypatch,
            rds_items=[
                {
                    "action": "db-upgrade",
                    "description": "",
                    "auto_apply_after": None,
                    "current_apply_date": None,
                }
            ],
        )

        assert ops.cmd_maintenance(ENV) == 0
        out = capsys.readouterr().out
        assert "  - db-upgrade: \n" in out
        assert "Auto-apply after:" not in out
        assert "Scheduled:" not in out

    def test_a_cache_update_severity_is_shown_in_brackets_when_present(
        self, env_config, monkeypatch, capsys
    ):
        env_config["cache"] = {"url": "redis://cache:6379"}
        self._maintenance(
            monkeypatch,
            cache_items=[
                {"action": "update-1", "description": "Engine patch", "severity": "important"},
                {"action": "update-2", "description": "Node resize", "severity": None},
            ],
        )

        assert ops.cmd_maintenance(ENV) == 0
        out = capsys.readouterr().out
        assert "  - [important] update-1: Engine patch" in out
        assert "  - update-2: Node resize" in out

    def test_an_unreadable_config_exits_1_with_the_error_text(
        self, env_config, monkeypatch, capsys
    ):
        env_config["_raise"] = FileNotFoundError("Config file not found: config.toml")

        with pytest.raises(SystemExit) as exit_info:
            ops.cmd_maintenance(ENV)
        assert exit_info.value.code == 1
        assert "Config file not found: config.toml" in capsys.readouterr().err


class TestCmdEcr:
    """Characterization tests for cmd_ecr()'s vulnerability report."""

    def _ecr(self, monkeypatch, repos=(), summaries=None, findings=None) -> list[list[str] | None]:
        """Stub the three ECR lookups; return the service-name lists seen."""
        seen: list[list[str] | None] = []

        def _repos(_env, service_names):
            seen.append(service_names)
            return list(repos)

        monkeypatch.setattr(ops, "list_repositories_for_environment", _repos)
        monkeypatch.setattr(
            ops, "get_repository_scan_summary", lambda repo, **_kw: (summaries or {}).get(repo, [])
        )
        monkeypatch.setattr(
            ops, "get_image_scan_findings", lambda *_a, **_kw: findings or {"findings": []}
        )
        return seen

    def _summary(self, critical=0, high=0, tag="latest", status="COMPLETE"):
        return [
            {
                "image_tag": tag,
                "scan_status": status,
                "critical_count": critical,
                "high_count": high,
            }
        ]

    def test_no_repositories_returns_0_and_names_what_was_checked(
        self, env_config, monkeypatch, capsys
    ):
        env_config["services"] = {"config": {"web": {}, "worker": {}}}
        seen = self._ecr(monkeypatch)

        assert ops.cmd_ecr(ENV, verbose=False) == 0
        out = capsys.readouterr().out
        assert seen == [["web", "worker"]]
        assert f"No ECR repositories found for {ENV}" in out
        assert f"Checked: {ENV}-web, {ENV}-worker" in out

    def test_no_configured_services_scans_nothing(self, env_config, monkeypatch, capsys):
        """An environment with no [services.config] has no repositories to ask about.

        This used to pass service_names=None through to
        list_repositories_for_environment, whose ``for svc in service_names``
        raises TypeError on None -- the old test stubbed that function out, so
        the crash was never reached. Empty is also not "ask for everything":
        describe_repositories with an empty name list answers with every
        repository in the account.
        """
        seen = self._ecr(monkeypatch)

        assert ops.cmd_ecr(ENV, verbose=False) == 0
        assert seen == []
        assert f"No services configured for {ENV}" in capsys.readouterr().out

    def test_an_unlistable_environment_returns_1_not_no_repositories(
        self, env_config, monkeypatch, capsys
    ):
        """A refused lookup used to print "No ECR repositories found" and exit 0."""
        env_config["services"] = {"config": {"web": {}}}

        def _refused(_env, _names):
            raise RuntimeError("AccessDeniedException")

        monkeypatch.setattr(ops, "list_repositories_for_environment", _refused)

        assert ops.cmd_ecr(ENV, verbose=False) == 1
        out = capsys.readouterr().out
        assert "Unable to list ECR repositories: AccessDeniedException" in out
        assert "No ECR repositories found" not in out

    def test_an_unreadable_repository_is_reported_and_fails_the_scan(
        self, env_config, monkeypatch, capsys
    ):
        """A repository whose images cannot be read is not silently skipped.

        Skipping it used to leave the summary at "No critical or high
        vulnerabilities" with exit 0 -- the reassuring answer, on a scan that
        did not happen.
        """
        env_config["services"] = {"config": {"web": {}, "worker": {}}}
        self._ecr(
            monkeypatch,
            repos=[f"{ENV}-web", f"{ENV}-worker"],
            summaries={f"{ENV}-worker": self._summary()},
        )

        def _summary(repo, **_kw):
            if repo == f"{ENV}-web":
                raise RuntimeError("AccessDeniedException")
            return self._summary()

        monkeypatch.setattr(ops, "get_repository_scan_summary", _summary)

        assert ops.cmd_ecr(ENV, verbose=False) == 1
        out = capsys.readouterr().out
        assert "  web: unable to read scan results (AccessDeniedException)" in out
        assert "  worker:" in out
        assert "UNREADABLE: 1 repository(ies)" in out
        assert "Could not read scan results for 1 repository(ies)" in out
        assert "No critical or high vulnerabilities" not in out

    def test_a_repository_with_no_scan_summary_is_skipped(self, env_config, monkeypatch, capsys):
        env_config["services"] = {"config": {"web": {}}}
        self._ecr(monkeypatch, repos=[f"{ENV}-web"], summaries={})

        assert ops.cmd_ecr(ENV, verbose=False) == 0
        out = capsys.readouterr().out
        assert "  web:" not in out
        assert "  CRITICAL: 0" in out

    def test_a_clean_scan_returns_0(self, env_config, monkeypatch, capsys):
        env_config["services"] = {"config": {"web": {}}}
        self._ecr(monkeypatch, repos=[f"{ENV}-web"], summaries={f"{ENV}-web": self._summary()})

        assert ops.cmd_ecr(ENV, verbose=False) == 0
        out = capsys.readouterr().out
        assert "  web:" in out
        assert "    Tag: latest" in out
        assert "    Scan: COMPLETE" in out
        assert "No critical or high vulnerabilities" in out

    def test_high_findings_warn_but_still_return_0(self, env_config, monkeypatch, capsys):
        env_config["services"] = {"config": {"web": {}}}
        self._ecr(
            monkeypatch,
            repos=[f"{ENV}-web"],
            summaries={f"{ENV}-web": self._summary(high=3)},
        )

        assert ops.cmd_ecr(ENV, verbose=False) == 0
        out = capsys.readouterr().out
        assert "HIGH: 3" in out
        assert "High severity vulnerabilities found" in out

    def test_critical_findings_return_1(self, env_config, monkeypatch, capsys):
        env_config["services"] = {"config": {"web": {}}}
        self._ecr(
            monkeypatch,
            repos=[f"{ENV}-web"],
            summaries={f"{ENV}-web": self._summary(critical=2, high=3)},
        )

        assert ops.cmd_ecr(ENV, verbose=False) == 1
        out = capsys.readouterr().out
        assert "CRITICAL: 2" in out
        assert "Critical vulnerabilities found" in out

    def test_verbose_lists_the_first_five_findings_and_counts_the_rest(
        self, env_config, monkeypatch, capsys
    ):
        env_config["services"] = {"config": {"web": {}}}
        self._ecr(
            monkeypatch,
            repos=[f"{ENV}-web"],
            summaries={f"{ENV}-web": self._summary(critical=6)},
            findings={
                "findings": [{"severity": "CRITICAL", "name": f"CVE-2026-000{n}"} for n in range(6)]
            },
        )

        assert ops.cmd_ecr(ENV, verbose=True) == 1
        out = capsys.readouterr().out
        assert "      - [CRITICAL] CVE-2026-0000" in out
        assert "      - [CRITICAL] CVE-2026-0004" in out
        assert "      - [CRITICAL] CVE-2026-0005" not in out
        assert "      ... and 1 more" in out

    def test_verbose_with_five_or_fewer_findings_prints_no_remainder(
        self, env_config, monkeypatch, capsys
    ):
        env_config["services"] = {"config": {"web": {}}}
        self._ecr(
            monkeypatch,
            repos=[f"{ENV}-web"],
            summaries={f"{ENV}-web": self._summary(high=1)},
            findings={"findings": [{"severity": "HIGH", "name": "CVE-2026-0001"}]},
        )

        assert ops.cmd_ecr(ENV, verbose=True) == 0
        out = capsys.readouterr().out
        assert "      - [HIGH] CVE-2026-0001" in out
        assert "more" not in out

    def test_an_unreadable_config_exits_1_with_the_error_text(
        self, env_config, monkeypatch, capsys
    ):
        env_config["_raise"] = FileNotFoundError("Config file not found: config.toml")

        with pytest.raises(SystemExit) as exit_info:
            ops.cmd_ecr(ENV, verbose=False)
        assert exit_info.value.code == 1
        assert "Config file not found: config.toml" in capsys.readouterr().err


class TestCmdIncidentStart:
    """cmd_incident_start()'s initial-state capture — the ops.py:874 call site."""

    @pytest.fixture(autouse=True)
    def incidents_dir(self, monkeypatch, tmp_path):
        """Write incident files under tmp_path instead of local/incidents/."""
        monkeypatch.setattr(ops, "INCIDENTS_DIR", tmp_path / "incidents")
        return tmp_path / "incidents"

    def _written(self, incidents_dir) -> str:
        (path,) = list(incidents_dir.glob("*.md"))
        return path.read_text(encoding="utf-8")

    def test_the_running_services_are_captured_as_the_initial_state(
        self, env_config, incidents_dir, monkeypatch
    ):
        env_config["infrastructure"] = {"cluster_name": CLUSTER}
        monkeypatch.setattr(
            ops,
            "get_all_services_state",
            lambda _c: {
                "web": ServiceState(_arn("web", 7), desired_count=2, running_count=1),
            },
        )

        assert ops.cmd_incident_start(ENV, "web 500s") == 0
        body = self._written(incidents_dir)
        assert "- web: 1/2 running" in body
        assert "Status: OPEN" in body

    def test_a_cluster_with_no_services_leaves_the_state_empty(
        self, env_config, incidents_dir, monkeypatch
    ):
        env_config["infrastructure"] = {"cluster_name": CLUSTER}
        monkeypatch.setattr(ops, "get_all_services_state", lambda _c: {})

        assert ops.cmd_incident_start(ENV, "web 500s") == 0
        assert "(no services found)" in self._written(incidents_dir)

    def test_an_unconfigured_cluster_leaves_the_state_empty(
        self, env_config, incidents_dir, monkeypatch
    ):
        monkeypatch.setattr(ops, "get_all_services_state", lambda _c: {})

        assert ops.cmd_incident_start(ENV, "web 500s") == 0
        assert "(no services found)" in self._written(incidents_dir)

    def test_an_unreadable_cluster_is_recorded_with_its_reason(
        self, env_config, incidents_dir, monkeypatch
    ):
        """The broad except Exception here is deliberate, and stays.

        It is honest degradation, not misattribution: the incident file is
        still written, and now that get_all_services_state() raises it records
        *why* the state is missing instead of claiming the cluster was empty.
        Starting an incident must never fail because the environment it is
        about is unreachable.
        """
        env_config["infrastructure"] = {"cluster_name": CLUSTER}
        monkeypatch.setattr(ops, "get_all_services_state", ecs.get_all_services_state)
        monkeypatch.setattr(ecs, "_get_ecs_client", _denied_ecs_client)

        assert ops.cmd_incident_start(ENV, "web 500s") == 0
        body = self._written(incidents_dir)
        assert "(Could not capture state: Could not read services in cluster" in body
        assert "(no services found)" not in body

    def test_a_failed_config_read_is_recorded_in_the_incident_file(self, env_config, incidents_dir):
        # This one is honest degradation, not misattribution: the incident file
        # still gets written and says why the state is missing.
        env_config["_raise"] = FileNotFoundError("Config file not found: config.toml")

        assert ops.cmd_incident_start(ENV, "web 500s") == 0
        body = self._written(incidents_dir)
        assert "(Could not capture state: Config file not found: config.toml)" in body
        assert "Status: OPEN" in body


# =============================================================================
# logs / audit / incident list, and the Click wiring above every command
# =============================================================================


class TestCmdLogs:
    """Characterization tests for cmd_logs()'s per-log-group error report."""

    def _logs(self, monkeypatch, groups, events: dict) -> list[tuple]:
        """Stub both log collectors; return the scan calls received.

        An Exception instance in place of ``groups`` or of one group's events
        is raised instead of returned.
        """
        calls: list[tuple] = []

        def _scan(log_group, lookback_minutes, max_results):
            calls.append((log_group, lookback_minutes, max_results))
            result = events.get(log_group, [])
            if isinstance(result, Exception):
                raise result
            return result

        def _groups(_env):
            if isinstance(groups, Exception):
                raise groups
            return groups

        monkeypatch.setattr(ops, "get_log_groups_for_environment", _groups)
        monkeypatch.setattr(ops, "scan_logs_for_errors", _scan)
        return calls

    def test_an_unlistable_environment_returns_1_not_no_log_groups(self, monkeypatch, capsys):
        calls = self._logs(monkeypatch, RuntimeError("AccessDeniedException"), {})

        assert ops.cmd_logs(ENV, minutes=60, limit=100) == 1
        out = capsys.readouterr().out
        assert calls == []
        assert "Unable to list log groups: AccessDeniedException" in out
        assert "No log groups found" not in out

    def test_an_unsearchable_group_is_reported_in_place_and_the_rest_still_scan(
        self, monkeypatch, capsys
    ):
        """A denied search used to end logs (and audit) with a traceback."""
        groups = [f"/ecs/{ENV}-web", f"/ecs/{ENV}-worker"]
        calls = self._logs(
            monkeypatch,
            groups,
            {groups[0]: RuntimeError("AccessDeniedException"), groups[1]: [self._event()]},
        )

        assert ops.cmd_logs(ENV, minutes=60, limit=100) == 1
        out = capsys.readouterr().out
        assert [c[0] for c in calls] == groups
        assert "web:" in out
        assert "unable to search (AccessDeniedException)" in out
        assert "worker:" in out
        assert "(1 errors)" in out
        assert "Could not search 1 of 2 log group(s)" in out
        assert "No errors found" not in out

    def _event(self, message="ERROR boom", timestamp="2026-08-13T09:15:30+00:00") -> dict:
        return {"timestamp": timestamp, "message": message, "log_stream": "web/web/abc"}

    def test_no_log_groups_explains_why_and_returns_0(self, monkeypatch, capsys):
        calls = self._logs(monkeypatch, [], {})

        assert ops.cmd_logs(ENV, minutes=60, limit=100) == 0
        out = capsys.readouterr().out
        assert calls == []
        assert f"No log groups found with prefix /ecs/{ENV}" in out
        assert "Log groups are created when ECS tasks first run" in out

    def test_every_group_is_scanned_with_the_window_and_limit(self, monkeypatch, capsys):
        groups = [f"/ecs/{ENV}-web", f"/ecs/{ENV}-worker"]
        calls = self._logs(monkeypatch, groups, {})

        assert ops.cmd_logs(ENV, minutes=15, limit=25) == 0
        out = capsys.readouterr().out
        assert calls == [(groups[0], 15, 25), (groups[1], 15, 25)]
        assert "Scanning logs for errors (last 15 minutes):" in out
        assert f"Log groups: /ecs/{ENV}-web, /ecs/{ENV}-worker" in out
        assert "No errors found" in out

    def test_errors_are_grouped_under_the_service_name_with_a_time(self, monkeypatch, capsys):
        self._logs(
            monkeypatch,
            [f"/ecs/{ENV}-web", f"/ecs/{ENV}-worker"],
            {f"/ecs/{ENV}-worker": [self._event(), self._event("Traceback")]},
        )

        assert ops.cmd_logs(ENV, minutes=60, limit=100) == 0
        out = capsys.readouterr().out
        assert "worker:" in out
        assert "(2 errors)" in out
        assert "web:" not in out
        assert "  [09:15:30] ERROR boom" in out
        assert "  [09:15:30] Traceback" in out
        assert "Found 2 error(s)" in out

    def test_a_long_message_is_cut_at_200_characters(self, monkeypatch, capsys):
        self._logs(
            monkeypatch,
            [f"/ecs/{ENV}-web"],
            {f"/ecs/{ENV}-web": [self._event("E" * 200), self._event("F" * 201)]},
        )

        assert ops.cmd_logs(ENV, minutes=60, limit=100) == 0
        out = capsys.readouterr().out
        assert f"] {'E' * 200}\n" in out
        assert f"] {'F' * 200}...\n" in out

    def test_only_the_first_ten_events_are_shown_but_all_are_counted(self, monkeypatch, capsys):
        events = [self._event(f"ERROR number {n}") for n in range(12)]
        self._logs(monkeypatch, [f"/ecs/{ENV}-web"], {f"/ecs/{ENV}-web": events})

        assert ops.cmd_logs(ENV, minutes=60, limit=100) == 0
        out = capsys.readouterr().out
        assert "ERROR number 9\n" in out
        assert "ERROR number 10" not in out
        assert "  ... and 2 more" in out
        assert "Found 12 error(s)" in out

    def test_an_event_with_no_timestamp_prints_it_verbatim(self, monkeypatch, capsys):
        """scan_logs_for_errors() leaves a missing timestamp as the integer 0."""
        self._logs(
            monkeypatch, [f"/ecs/{ENV}-web"], {f"/ecs/{ENV}-web": [self._event(timestamp=0)]}
        )

        assert ops.cmd_logs(ENV, minutes=60, limit=100) == 0
        assert "  [0] ERROR boom" in capsys.readouterr().out


class TestCmdAudit:
    """cmd_audit() runs the five checks in order and folds all five exit codes.

    logs and maintenance answer 1 only when a read failed, so folding them
    means an audit that could not look is not reported as clean.
    """

    @pytest.fixture
    def checks(self, monkeypatch) -> dict:
        """Stub the five commands; return their exit codes and the call log."""
        state: dict = {
            "calls": [],
            "status": 0,
            "health": 0,
            "logs": 0,
            "maintenance": 0,
            "ecr": 0,
        }

        def _record(name):
            def _cmd(*args, **kwargs):
                state["calls"].append((name, args, kwargs))
                return state[name]

            return _cmd

        for name in ("status", "health", "logs", "maintenance", "ecr"):
            monkeypatch.setattr(ops, f"cmd_{name}", _record(name))
        return state

    def test_the_five_checks_run_in_order_with_audit_s_arguments(self, checks, capsys):
        assert ops.cmd_audit(ENV) == 0
        assert checks["calls"] == [
            ("status", (ENV,), {}),
            ("health", (ENV,), {}),
            ("logs", (ENV,), {"minutes": 60, "limit": 50}),
            ("maintenance", (ENV,), {}),
            ("ecr", (ENV,), {"verbose": False}),
        ]
        out = capsys.readouterr().out
        assert f"Production Audit: {ENV}" in out
        headings = [
            "[1/5] Environment Status",
            "[2/5] ALB Target Health",
            "[3/5] Recent Errors (last 60 minutes)",
            "[4/5] Pending Maintenance",
            "[5/5] ECR Vulnerability Findings",
        ]
        assert [out.index(h) for h in headings] == sorted(out.index(h) for h in headings)
        assert "Audit completed - no critical issues found" in out

    @pytest.mark.parametrize("failing", ["status", "health", "logs", "maintenance", "ecr"])
    def test_a_failing_check_fails_the_audit_but_the_rest_still_run(self, checks, capsys, failing):
        checks[failing] = 1

        assert ops.cmd_audit(ENV) == 1
        assert len(checks["calls"]) == 5
        assert "Audit completed - issues found that require attention" in capsys.readouterr().out


class TestIncidentLookupAndList:
    """_get_open_incident(), _require_open_incident() and cmd_incident_list()."""

    @pytest.fixture(autouse=True)
    def incidents_dir(self, monkeypatch, tmp_path) -> Path:
        monkeypatch.setattr(ops, "INCIDENTS_DIR", tmp_path / "incidents")
        return tmp_path / "incidents"

    def _write(self, incidents_dir: Path, name: str, title: str, status: str) -> Path:
        incidents_dir.mkdir(exist_ok=True)
        path = incidents_dir / name
        path.write_text(
            f"# Incident: {title}\nStarted: x\nEnvironment: {ENV}\nStatus: {status}\n",
            encoding="utf-8",
        )
        return path

    def test_no_directory_means_no_open_incident(self):
        assert ops._get_open_incident() is None

    def test_the_newest_open_incident_is_chosen_over_older_and_resolved_ones(self, incidents_dir):
        self._write(incidents_dir, "2026-08-01-0900-a.md", "a", "OPEN")
        wanted = self._write(incidents_dir, "2026-08-02-0900-b.md", "b", "OPEN")
        self._write(incidents_dir, "2026-08-03-0900-c.md", "c", "RESOLVED")

        assert ops._get_open_incident() == wanted

    def test_only_resolved_incidents_means_none_open(self, incidents_dir):
        self._write(incidents_dir, "2026-08-03-0900-c.md", "c", "RESOLVED")

        assert ops._get_open_incident() is None

    def test_note_lands_in_the_open_incident_the_lookup_finds(self, incidents_dir):
        incidents_dir.mkdir()
        older = incidents_dir / "2026-08-01-0900-db-down.md"
        older.write_text(_incident("09:00 Incident started"), encoding="utf-8")
        newer = incidents_dir / "2026-08-13-0900-web-500s.md"
        newer.write_text(_incident("09:00 Incident started"), encoding="utf-8")

        assert ops.cmd_incident_note("scaled web to 4") == 0
        assert "scaled web to 4" in newer.read_text(encoding="utf-8")
        assert "scaled web to 4" not in older.read_text(encoding="utf-8")

    def test_note_with_no_open_incident_exits_1_and_says_how_to_start_one(self, capsys):
        with pytest.raises(SystemExit) as exit_info:
            ops.cmd_incident_note("anything")
        assert exit_info.value.code == 1
        assert "No open incident found. Start one with: ops.py incident start" in (
            capsys.readouterr().out
        )

    def test_list_with_no_directory_says_none_recorded(self, capsys):
        assert ops.cmd_incident_list() == 0
        assert "No incidents recorded" in capsys.readouterr().out

    def test_list_with_an_empty_directory_says_none_recorded(self, incidents_dir, capsys):
        incidents_dir.mkdir()

        assert ops.cmd_incident_list() == 0
        assert "No incidents recorded" in capsys.readouterr().out

    def test_list_marks_each_incident_newest_first_and_counts_the_open_ones(
        self, incidents_dir, capsys
    ):
        self._write(incidents_dir, "2026-08-01-0900-db-down.md", "db down", "RESOLVED")
        self._write(incidents_dir, "2026-08-02-0900-web-500s.md", "web 500s", "OPEN")

        assert ops.cmd_incident_list() == 0
        out = capsys.readouterr().out
        assert "[OPEN]" in out
        assert "2026-08-02-0900-web-500s.md  web 500s" in out
        assert "[RESOLVED]" in out
        assert "2026-08-01-0900-db-down.md  db down" in out
        assert out.index("web-500s") < out.index("db-down")
        assert "\n1 open incident(s)" in out

    def test_list_with_nothing_open_prints_no_count(self, incidents_dir, capsys):
        self._write(incidents_dir, "2026-08-01-0900-db-down.md", "db down", "RESOLVED")

        assert ops.cmd_incident_list() == 0
        assert "open incident(s)" not in capsys.readouterr().out

    def test_list_shows_only_the_twenty_newest(self, incidents_dir, capsys):
        for day in range(1, 22):
            self._write(incidents_dir, f"2026-08-{day:02d}-0900-i.md", f"i{day}", "RESOLVED")

        assert ops.cmd_incident_list() == 0
        out = capsys.readouterr().out
        assert "2026-08-21-0900-i.md" in out
        assert "2026-08-02-0900-i.md" in out
        assert "2026-08-01-0900-i.md" not in out


class TestCli:
    """The Click layer: argument parsing, the deployed-environment gate, exit codes.

    Each command's body is ``_validate_and_configure(env)`` then
    ``sys.exit(cmd_x(...))``. The environment directory and terraform.tfstate
    are real so the gate runs for real; AWS_PROFILE short-circuits the profile
    lookup. The cmd_* functions are stubbed; their output is pinned above.
    """

    @pytest.fixture
    def deployed(self, monkeypatch, tmp_path) -> Path:
        monkeypatch.setenv("DEPLOYER_ENVIRONMENTS_DIR", str(tmp_path))
        monkeypatch.setenv("AWS_PROFILE", "test")
        env_dir = tmp_path / ENV
        env_dir.mkdir()
        state = env_dir / "terraform.tfstate"
        state.write_text("{}", encoding="utf-8")
        return state

    @pytest.fixture
    def commands(self, monkeypatch) -> list[tuple]:
        """Stub every cmd_* the CLI dispatches to; each returns 3."""
        calls: list[tuple] = []
        for name in (
            "cmd_status",
            "cmd_health",
            "cmd_logs",
            "cmd_maintenance",
            "cmd_ecr",
            "cmd_audit",
            "cmd_incident_start",
            "cmd_incident_note",
            "cmd_incident_resolve",
            "cmd_incident_list",
        ):
            monkeypatch.setattr(
                ops, name, lambda *args, _name=name: (calls.append((_name, args)), 3)[1]
            )
        return calls

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["status", ENV], ("cmd_status", (ENV,))),
            (["health", ENV], ("cmd_health", (ENV,))),
            (["logs", ENV], ("cmd_logs", (ENV, 60, 100))),
            (["logs", ENV, "-m", "15", "-l", "20"], ("cmd_logs", (ENV, 15, 20))),
            (["maintenance", ENV], ("cmd_maintenance", (ENV,))),
            (["ecr", ENV], ("cmd_ecr", (ENV, False))),
            (["ecr", ENV, "--verbose"], ("cmd_ecr", (ENV, True))),
            (["audit", ENV], ("cmd_audit", (ENV,))),
            (["incident", "start", ENV, "web 500s"], ("cmd_incident_start", (ENV, "web 500s"))),
        ],
    )
    def test_an_environment_command_runs_after_the_gate_and_exits_with_its_code(
        self, deployed, commands, argv, expected
    ):
        result = CliRunner().invoke(ops.cli, argv)

        assert result.exit_code == 3
        assert commands == [expected]
        assert "ops.py: Production monitoring tool (read-only)" in result.output

    @pytest.mark.parametrize(
        "argv",
        [
            ["status", ENV],
            ["health", ENV],
            ["logs", ENV],
            ["maintenance", ENV],
            ["ecr", ENV],
            ["audit", ENV],
            ["incident", "start", ENV, "web 500s"],
        ],
    )
    def test_an_undeployed_environment_exits_1_before_the_command_runs(
        self, deployed, commands, argv
    ):
        deployed.unlink()

        result = CliRunner().invoke(ops.cli, argv)

        assert result.exit_code == 1
        assert commands == []
        assert f"Environment '{ENV}' is not deployed" in result.output

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["incident", "note", "scaled web"], ("cmd_incident_note", ("scaled web",))),
            (["incident", "resolve"], ("cmd_incident_resolve", ())),
            (["incident", "list"], ("cmd_incident_list", ())),
        ],
    )
    def test_the_environment_free_incident_commands_skip_the_gate(
        self, commands, monkeypatch, argv, expected
    ):
        monkeypatch.delenv("DEPLOYER_ENVIRONMENTS_DIR", raising=False)

        result = CliRunner().invoke(ops.cli, argv)

        assert result.exit_code == 3
        assert commands == [expected]
        assert "ops.py: Production monitoring tool" not in result.output
