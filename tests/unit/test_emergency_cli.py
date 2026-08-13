"""Characterization tests for bin/emergency.py's state-changing commands.

These pin today's behaviour of cmd_rollback(), cmd_scale() and
cmd_force_deploy() — return codes, operator-visible output, and the exact
arguments handed to each ECS/checkpoint mutator — so that a decomposition of
those commands can be shown to preserve it. AWS is never reached: the
collaborators are stubbed at the module boundary, and interactive prompts are
fed through builtins.input() rather than through prompt_or_exit(), so the
tests survive the prompt moving into a shared helper.

Several assertions are marked "pinned, not endorsed": cmd_rollback() returns 1
both when the operator declines and when the update fails, and cmd_scale() /
cmd_force_deploy() return 0 even when individual services fail. That is the
same raise-vs-return question tracked as claude-meta Phase 53i; these tests
pin today's behaviour so 53i's change is visible when it happens.
"""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from deployer.emergency.checkpoint import Checkpoint, ServiceState

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("emergency", bin_dir / "emergency.py")
emergency = module_from_spec(_spec)
_spec.loader.exec_module(emergency)

ENV = "myapp-production"
CLUSTER = "myapp-production-cluster"


def _arn(service: str, revision: int) -> str:
    """Build a task definition ARN the way get_all_services_state() reports it."""
    return f"arn:aws:ecs:us-west-2:111111111111:task-definition/{ENV}-{service}:{revision}"


def _services(**counts: int) -> dict[str, ServiceState]:
    """Build a services dict shaped like get_all_services_state()'s return."""
    return {
        name: ServiceState(task_definition=_arn(name, 7), desired_count=count, running_count=count)
        for name, count in counts.items()
    }


def _revisions(*numbers: int) -> list[dict]:
    """Build a revision list, newest (current) first."""
    return [
        {
            "revision": number,
            "arn": _arn("web", number),
            "registered_at": f"2026-08-{number:02d}T14:30:00Z",
        }
        for number in numbers
    ]


class _StubLogger:
    """Stand-in for EmergencyLogger that records lines instead of writing them.

    EmergencyLogger's methods all share one shape — category name, one message
    argument — so a single __getattr__ covers action/checkpoint/ecs/rds/
    success/error without repeating six identical stubs.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __getattr__(self, category: str):
        return lambda message: self.lines.append(f"{category}: {message}")


class _EcsCalls:
    """Records what each mutator was handed and returns canned results."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, str, str]] = []
        self.scales: list[tuple[str, str, int]] = []
        self.forced: list[tuple[str, str]] = []
        self.waits: list[tuple[str, str]] = []
        self.checkpoints: list[dict] = []
        self.cleanups: list[dict] = []
        self.progress_callback = None
        self.update_ok = True
        self.scale_ok = True
        self.force_ok = True
        self.deployment_ok = True
        self.running_count: int | None = 3

    def update_service_task_definition(self, cluster: str, service: str, arn: str) -> bool:
        self.updates.append((cluster, service, arn))
        return self.update_ok

    def scale_service(self, cluster: str, service: str, count: int) -> bool:
        self.scales.append((cluster, service, count))
        return self.scale_ok

    def force_new_deployment(self, cluster: str, service: str) -> bool:
        self.forced.append((cluster, service))
        return self.force_ok

    def wait_for_deployment(self, cluster: str, service: str, callback=None) -> bool:
        self.waits.append((cluster, service))
        self.progress_callback = callback
        return self.deployment_ok

    def get_service_state(self, cluster: str, service: str) -> ServiceState | None:
        if self.running_count is None:
            return None
        return ServiceState(
            task_definition=_arn(service, 6),
            desired_count=self.running_count,
            running_count=self.running_count,
        )

    def create_checkpoint(self, **kwargs) -> Checkpoint:
        self.checkpoints.append(kwargs)
        return Checkpoint(
            timestamp="2026-08-13T09:00:00Z",
            environment=kwargs["environment"],
            action=kwargs["action"],
            reason=kwargs["reason"],
            filename="2026-08-13-0900-rollback.json",
        )

    def cleanup_old_checkpoints(self, **kwargs) -> None:
        self.cleanups.append(kwargs)


@pytest.fixture
def ecs(monkeypatch):
    """Stub every mutator emergency.py can reach and record its arguments."""
    calls = _EcsCalls()
    for name in (
        "update_service_task_definition",
        "scale_service",
        "force_new_deployment",
        "wait_for_deployment",
        "get_service_state",
        "create_checkpoint",
        "cleanup_old_checkpoints",
    ):
        monkeypatch.setattr(emergency, name, getattr(calls, name))
    return calls


def _context(monkeypatch, services: dict, config: dict | None = None) -> _StubLogger:
    """Stub the cluster/context load so no config file or AWS call is needed."""
    logger = _StubLogger()
    ctx = emergency.EmergencyContext(
        config=config or {}, cluster_name=CLUSTER, rds_id=None, logger=logger
    )
    monkeypatch.setattr(emergency, "_load_cluster_services", lambda _env: (ctx, services))
    return logger


def _answers(monkeypatch, *replies: str) -> list[str]:
    """Feed canned stdin replies; return the list prompts are recorded into."""
    prompts: list[str] = []
    queue = list(replies)

    def _input(prompt: str = "") -> str:
        prompts.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", _input)
    return prompts


def _revision_list(monkeypatch, revisions: list[dict]) -> None:
    """Stub the task definition revision lookup."""
    monkeypatch.setattr(
        emergency, "list_task_definition_revisions", lambda _family, **_kw: revisions
    )


def _diff(monkeypatch, diff: dict) -> None:
    """Stub the task definition comparison."""
    monkeypatch.setattr(emergency, "compare_task_definitions", lambda _a, _b: diff)


class TestCmdRollbackExplicit:
    """cmd_rollback() with --service and --revision given."""

    def _setup(self, monkeypatch, diff=None):
        logger = _context(monkeypatch, _services(web=2, worker=1))
        _revision_list(monkeypatch, _revisions(7, 6, 5))
        _diff(monkeypatch, diff or {})
        return logger

    def test_updates_the_service_to_the_requested_revision(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)

        assert emergency.cmd_rollback(ENV, "web", 6, True) == 0
        assert ecs.updates == [(CLUSTER, "web", _arn("web", 6))]
        assert ecs.waits == [(CLUSTER, "web")]
        assert "Rollback complete: 3 tasks running" in capsys.readouterr().out

    def test_checkpoint_captures_every_service_before_the_update(self, monkeypatch, ecs):
        self._setup(monkeypatch)

        assert emergency.cmd_rollback(ENV, "web", 6, True) == 0
        (checkpoint,) = ecs.checkpoints
        assert checkpoint["environment"] == ENV
        assert checkpoint["action"] == "rollback"
        assert checkpoint["reason"] == "Rolling back web from revision 7 to 6"
        assert sorted(checkpoint["services"]) == ["web", "worker"]
        assert checkpoint["services"]["web"].desired_count == 2
        assert checkpoint["rds"] is None
        assert ecs.cleanups == [{"environment": ENV}]

    def test_the_plan_is_shown_before_the_confirmation(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)

        assert emergency.cmd_rollback(ENV, "web", 6, True) == 0
        out = capsys.readouterr().out
        assert "About to roll back web:" in out
        assert f"From: {ENV}-web:7" in out
        assert f"To:   {ENV}-web:6" in out

    def test_environment_variable_changes_are_listed(self, monkeypatch, ecs, capsys):
        self._setup(
            monkeypatch,
            diff={
                "app": {
                    "added": {"NEW": "1"},
                    "removed": {"OLD": "2"},
                    "changed": {"MODE": {"old": "a", "new": "b"}},
                }
            },
        )

        assert emergency.cmd_rollback(ENV, "web", 6, True) == 0
        out = capsys.readouterr().out
        assert "+ NEW=1" in out
        assert "- OLD=2" in out
        assert "~ MODE: a -> b" in out

    def test_progress_callback_reports_task_counts(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)
        emergency.cmd_rollback(ENV, "web", 6, True)
        capsys.readouterr()

        ecs.progress_callback(1, 2)
        assert "Waiting for deployment (1/2 tasks running)..." in capsys.readouterr().out

    def test_unknown_service_returns_1_and_lists_the_available_ones(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)

        assert emergency.cmd_rollback(ENV, "nope", 6, True) == 1
        assert "Service 'nope' not found. Available: web, worker" in capsys.readouterr().out
        assert ecs.updates == []

    def test_unknown_revision_returns_1(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)

        assert emergency.cmd_rollback(ENV, "web", 99, True) == 1
        assert "Revision 99 not found" in capsys.readouterr().out
        assert ecs.updates == []

    def test_a_single_revision_cannot_be_rolled_back(self, monkeypatch, ecs, capsys):
        _context(monkeypatch, _services(web=2))
        _revision_list(monkeypatch, _revisions(7))

        assert emergency.cmd_rollback(ENV, "web", None, True) == 1
        assert "Not enough revisions to roll back for web" in capsys.readouterr().out
        assert ecs.checkpoints == []

    def test_declining_the_confirmation_returns_1(self, monkeypatch, ecs):
        # Pinned, not endorsed: "operator said no" is reported with the same
        # exit status as "the update failed" (Phase 53i).
        self._setup(monkeypatch)
        _answers(monkeypatch, "n")

        assert emergency.cmd_rollback(ENV, "web", 6, False) == 1
        assert ecs.checkpoints == []
        assert ecs.updates == []

    def test_a_failed_update_returns_1_after_the_checkpoint_is_written(
        self, monkeypatch, ecs, capsys
    ):
        # Pinned, not endorsed: same 1 as the declined case above (Phase 53i).
        self._setup(monkeypatch)
        ecs.update_ok = False

        assert emergency.cmd_rollback(ENV, "web", 6, True) == 1
        assert len(ecs.checkpoints) == 1
        assert ecs.waits == []
        assert "Failed to update service" in capsys.readouterr().out

    def test_a_deployment_timeout_still_returns_0(self, monkeypatch, ecs, capsys):
        # Pinned, not endorsed: a timeout is a warning, not a failure (Phase 53i).
        self._setup(monkeypatch)
        ecs.deployment_ok = False

        assert emergency.cmd_rollback(ENV, "web", 6, True) == 0
        assert "Deployment still in progress (timed out waiting)" in capsys.readouterr().out
        assert ecs.cleanups == [{"environment": ENV}]

    def test_a_missing_post_deployment_state_reports_zero_tasks(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)
        ecs.running_count = None

        assert emergency.cmd_rollback(ENV, "web", 6, True) == 0
        assert "Rollback complete: 0 tasks running" in capsys.readouterr().out


class TestCmdRollbackInteractive:
    """cmd_rollback()'s two numbered prompts."""

    def _setup(self, monkeypatch):
        _context(monkeypatch, _services(web=2, worker=1))
        _revision_list(monkeypatch, _revisions(7, 6, 5))
        _diff(monkeypatch, {})

    def test_services_are_listed_1_based_and_sorted(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)
        prompts = _answers(monkeypatch, "2")

        assert emergency.cmd_rollback(ENV, None, 6, True) == 0
        out = capsys.readouterr().out
        assert "  1. web (current revision: 7)" in out
        assert "  2. worker (current revision: 7)" in out
        assert prompts == ["Select service to roll back (number): "]
        assert ecs.updates == [(CLUSTER, "worker", _arn("web", 6))]

    def test_a_non_numeric_service_choice_returns_1(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)
        _answers(monkeypatch, "web")

        assert emergency.cmd_rollback(ENV, None, 6, True) == 1
        assert "Invalid selection" in capsys.readouterr().out
        assert ecs.updates == []

    def test_an_out_of_range_service_choice_returns_1(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)
        _answers(monkeypatch, "3")

        assert emergency.cmd_rollback(ENV, None, 6, True) == 1
        assert "Invalid selection" in capsys.readouterr().out

    def test_revisions_are_listed_0_based_with_the_current_one_marked(
        self, monkeypatch, ecs, capsys
    ):
        self._setup(monkeypatch)
        prompts = _answers(monkeypatch, "2")

        assert emergency.cmd_rollback(ENV, "web", None, True) == 0
        out = capsys.readouterr().out
        assert "  0. revision   7 - 2026-08-07 14:30 UTC (current)" in out
        assert "  1. revision   6 - 2026-08-06 14:30 UTC" in out
        assert prompts == ["Select revision to roll back to (number, default=1 for previous): "]
        assert ecs.updates == [(CLUSTER, "web", _arn("web", 5))]

    def test_an_unparseable_registered_at_is_shown_verbatim(self, monkeypatch, ecs, capsys):
        _context(monkeypatch, _services(web=2))
        _revision_list(
            monkeypatch,
            [
                {"revision": 7, "arn": _arn("web", 7), "registered_at": "unknown"},
                {"revision": 6, "arn": _arn("web", 6)},
            ],
        )
        _diff(monkeypatch, {})
        _answers(monkeypatch, "1")

        assert emergency.cmd_rollback(ENV, "web", None, True) == 0
        out = capsys.readouterr().out
        assert "  0. revision   7 - unknown (current)" in out
        assert "  1. revision   6 - unknown" in out

    def test_an_empty_revision_choice_selects_the_previous_revision(self, monkeypatch, ecs):
        self._setup(monkeypatch)
        _answers(monkeypatch, "")

        assert emergency.cmd_rollback(ENV, "web", None, True) == 0
        assert ecs.updates == [(CLUSTER, "web", _arn("web", 6))]

    def test_the_current_revision_cannot_be_selected(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)
        _answers(monkeypatch, "0")

        assert emergency.cmd_rollback(ENV, "web", None, True) == 1
        assert "Invalid selection (cannot select current revision)" in capsys.readouterr().out
        assert ecs.updates == []

    def test_an_out_of_range_revision_choice_returns_1(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)
        _answers(monkeypatch, "9")

        assert emergency.cmd_rollback(ENV, "web", None, True) == 1
        assert "Invalid selection (cannot select current revision)" in capsys.readouterr().out

    def test_a_non_numeric_revision_choice_returns_1(self, monkeypatch, ecs, capsys):
        self._setup(monkeypatch)
        _answers(monkeypatch, "previous")

        assert emergency.cmd_rollback(ENV, "web", None, True) == 1
        assert "Invalid selection (cannot select current revision)" in capsys.readouterr().out


def _scale(monkeypatch, services=None, config=None, **overrides):
    """Call cmd_scale() with the boilerplate arguments filled in."""
    _context(monkeypatch, services or _services(web=2, worker=4), config=config)
    kwargs = {
        "service": None,
        "count": None,
        "all_services": False,
        "multiplier": None,
        "reset": False,
        "yes": True,
    }
    kwargs.update(overrides)
    return emergency.cmd_scale(ENV, **kwargs)


class TestCmdScale:
    """cmd_scale()'s four-way target selection and its scaling loop."""

    def test_service_and_count_scale_one_service(self, monkeypatch, ecs, capsys):
        assert _scale(monkeypatch, service="web", count=5) == 0
        assert ecs.scales == [(CLUSTER, "web", 5)]
        assert "web: 2 -> 5" in capsys.readouterr().out

    def test_count_is_required_with_service(self, monkeypatch, ecs, capsys):
        assert _scale(monkeypatch, service="web") == 1
        assert "--count is required when using --service" in capsys.readouterr().out
        assert ecs.scales == []

    def test_an_unknown_service_is_rejected(self, monkeypatch, ecs, capsys):
        # The wording of the tail is deliberately not pinned — only that the
        # command refuses, names the service, and touches nothing.
        assert _scale(monkeypatch, service="nope", count=5) == 1
        assert "Service 'nope' not found" in capsys.readouterr().out
        assert ecs.scales == []

    def test_all_with_a_multiplier_rounds_down_but_never_below_one(self, monkeypatch, ecs):
        assert _scale(monkeypatch, all_services=True, multiplier=1.5) == 0
        assert sorted(ecs.scales) == [(CLUSTER, "web", 3), (CLUSTER, "worker", 6)]

    def test_a_multiplier_that_would_zero_a_service_keeps_one_task(self, monkeypatch, ecs):
        assert _scale(monkeypatch, all_services=True, multiplier=0.1) == 0
        assert sorted(ecs.scales) == [(CLUSTER, "web", 1), (CLUSTER, "worker", 1)]

    def test_all_with_a_count_sets_every_service(self, monkeypatch, ecs):
        assert _scale(monkeypatch, all_services=True, count=2) == 0
        assert sorted(ecs.scales) == [(CLUSTER, "web", 2), (CLUSTER, "worker", 2)]

    def test_all_with_a_zero_count_is_honoured(self, monkeypatch, ecs):
        assert _scale(monkeypatch, all_services=True, count=0) == 0
        assert sorted(ecs.scales) == [(CLUSTER, "web", 0), (CLUSTER, "worker", 0)]

    def test_all_without_a_multiplier_or_count_returns_1(self, monkeypatch, ecs, capsys):
        assert _scale(monkeypatch, all_services=True) == 1
        assert "--multiplier or --count is required with --all" in capsys.readouterr().out
        assert ecs.scales == []

    def test_reset_reads_the_configured_replicas(self, monkeypatch, ecs):
        config = {"services": {"config": {"web": {"replicas": 4}}}}
        assert _scale(monkeypatch, config=config, reset=True) == 0
        # worker has no configured replica count, so it falls back to 1.
        assert sorted(ecs.scales) == [(CLUSTER, "web", 4), (CLUSTER, "worker", 1)]

    def test_reset_wins_over_service_and_count(self, monkeypatch, ecs):
        config = {"services": {"config": {"web": {"replicas": 4}}}}
        assert _scale(monkeypatch, config=config, reset=True, service="web", count=9) == 0
        assert sorted(ecs.scales) == [(CLUSTER, "web", 4), (CLUSTER, "worker", 1)]

    def test_no_target_flags_returns_1(self, monkeypatch, ecs, capsys):
        assert _scale(monkeypatch) == 1
        assert "Specify --service, --all, or --reset" in capsys.readouterr().out

    def test_declining_the_confirmation_returns_1(self, monkeypatch, ecs):
        _answers(monkeypatch, "n")
        assert _scale(monkeypatch, service="web", count=5, yes=False) == 1
        assert ecs.checkpoints == []
        assert ecs.scales == []

    def test_a_checkpoint_names_every_service_being_scaled(self, monkeypatch, ecs):
        assert _scale(monkeypatch, all_services=True, count=2) == 0
        (checkpoint,) = ecs.checkpoints
        assert checkpoint["action"] == "scale"
        assert checkpoint["reason"] == "Scaling services: web, worker"
        assert sorted(checkpoint["services"]) == ["web", "worker"]
        assert ecs.cleanups == [{"environment": ENV}]

    def test_a_failed_scale_is_reported_but_the_command_still_returns_0(
        self, monkeypatch, ecs, capsys
    ):
        # Pinned, not endorsed: a service that failed to scale leaves the exit
        # status at 0 (Phase 53i).
        ecs.scale_ok = False
        assert _scale(monkeypatch, service="web", count=5) == 0
        out = capsys.readouterr().out
        assert "Failed to scale web" in out
        assert "Scale operation completed" in out


def _force_deploy(monkeypatch, services=None, **overrides):
    """Call cmd_force_deploy() with the boilerplate arguments filled in."""
    _context(monkeypatch, services or _services(web=2, worker=4))
    kwargs = {"service": None, "all_services": False, "yes": True}
    kwargs.update(overrides)
    return emergency.cmd_force_deploy(ENV, **kwargs)


class TestCmdForceDeploy:
    """cmd_force_deploy()'s target selection and its deployment loop."""

    def test_a_named_service_is_redeployed(self, monkeypatch, ecs, capsys):
        assert _force_deploy(monkeypatch, service="web") == 0
        assert ecs.forced == [(CLUSTER, "web")]
        assert "Force deployment initiated for web" in capsys.readouterr().out

    def test_all_redeploys_every_service(self, monkeypatch, ecs):
        assert _force_deploy(monkeypatch, all_services=True) == 0
        assert sorted(ecs.forced) == [(CLUSTER, "web"), (CLUSTER, "worker")]

    def test_an_unknown_service_returns_1_and_lists_the_available_ones(
        self, monkeypatch, ecs, capsys
    ):
        assert _force_deploy(monkeypatch, service="nope") == 1
        assert "Service 'nope' not found. Available: web, worker" in capsys.readouterr().out
        assert ecs.forced == []

    def test_no_target_flags_returns_1(self, monkeypatch, ecs, capsys):
        assert _force_deploy(monkeypatch) == 1
        assert "Specify --service <name> or --all" in capsys.readouterr().out

    def test_declining_the_confirmation_returns_1(self, monkeypatch, ecs):
        _answers(monkeypatch, "n")
        assert _force_deploy(monkeypatch, service="web", yes=False) == 1
        assert ecs.forced == []

    def test_no_checkpoint_is_written(self, monkeypatch, ecs):
        # force-deploy replaces tasks with the same task definition, so there
        # is nothing to roll back to and no checkpoint is created.
        assert _force_deploy(monkeypatch, all_services=True) == 0
        assert ecs.checkpoints == []

    def test_a_failed_deployment_is_reported_but_the_command_still_returns_0(
        self, monkeypatch, ecs, capsys
    ):
        # Pinned, not endorsed: same swallow as cmd_scale() (Phase 53i).
        ecs.force_ok = False
        assert _force_deploy(monkeypatch, service="web") == 0
        out = capsys.readouterr().out
        assert "Failed to force deploy web" in out
        assert "Force deploy initiated" in out
