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
"""

import sys
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

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
        """Stub the pending-maintenance lookup; return the calls it received."""
        calls: list[tuple] = []

        def _get(rds_instance_id, elasticache_cluster_id):
            calls.append((rds_instance_id, elasticache_cluster_id))
            return {"rds": list(rds_items), "elasticache": list(cache_items)}

        monkeypatch.setattr(ops, "get_all_pending_maintenance", _get)
        return calls

    def test_neither_resource_configured_says_so_and_returns_0(
        self, env_config, monkeypatch, capsys
    ):
        calls = self._maintenance(monkeypatch)

        assert ops.cmd_maintenance(ENV) == 0
        out = capsys.readouterr().out
        assert calls == [(None, None)]
        assert "RDS: Not configured" in out
        assert "ElastiCache: Not configured" in out
        assert "No pending maintenance" in out

    def test_a_cache_url_derives_the_elasticache_cluster_id(self, env_config, monkeypatch):
        env_config["infrastructure"] = {"rds_instance_id": RDS_ID}
        env_config["cache"] = {"url": "redis://cache:6379"}
        calls = self._maintenance(monkeypatch)

        assert ops.cmd_maintenance(ENV) == 0
        assert calls == [(RDS_ID, f"{ENV}-cache")]

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

    def test_no_configured_services_asks_for_every_repository(self, env_config, monkeypatch):
        seen = self._ecr(monkeypatch)

        assert ops.cmd_ecr(ENV, verbose=False) == 0
        assert seen == [None]

    def test_a_repository_with_no_scan_summary_is_skipped(self, env_config, monkeypatch, capsys):
        self._ecr(monkeypatch, repos=[f"{ENV}-web"], summaries={})

        assert ops.cmd_ecr(ENV, verbose=False) == 0
        out = capsys.readouterr().out
        assert "  web:" not in out
        assert "  CRITICAL: 0" in out

    def test_a_clean_scan_returns_0(self, env_config, monkeypatch, capsys):
        self._ecr(monkeypatch, repos=[f"{ENV}-web"], summaries={f"{ENV}-web": self._summary()})

        assert ops.cmd_ecr(ENV, verbose=False) == 0
        out = capsys.readouterr().out
        assert "  web:" in out
        assert "    Tag: latest" in out
        assert "    Scan: COMPLETE" in out
        assert "No critical or high vulnerabilities" in out

    def test_high_findings_warn_but_still_return_0(self, env_config, monkeypatch, capsys):
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
        return path.read_text()

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
