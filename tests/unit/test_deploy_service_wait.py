"""Characterization tests for the migration + wait path of deployer.deploy.service.

These are **characterization pins, not endorsements**. They record what
``deploy/service.py`` does **today** so that the 53e-5c and 53e-5d refactors can
be shown to be behaviour-preserving. Where the current behaviour looks wrong it
is pinned anyway, and called out in a comment on the test.

Scope: the migration + wait half of the module. Before this file existed the
whole module was 11% covered with **zero** of its 120 branches taken, and these
ten functions were 100% unexecuted::

    start_migrations              wait_for_stable
    wait_for_migrations           _wait_for_service_stable
    _display_migration_logs       _get_service_target_group
    _check_for_fatal_errors       _wait_for_target_group_healthy
    _get_deployment_status        _wait_for_service_and_targets

Nine pins exist specifically to make 53e-5c/5d verifiable:

1. **All seven ``FATAL_ERROR_PATTERNS``**, one parametrized case each, plus the
   ``match.group(1)`` substitution that only the first pattern exercises
   (``TestCheckForFatalErrors``). Reordering, retyping or rewording any entry
   changes an operator-facing error message; the table is pinned literally.
2. **The no-op ``try/except DeploymentError: raise``** at service.py:891-894.
   It is behaviourally identical to calling ``_check_for_fatal_errors`` bare.
   ``TestFailureThresholdFatalPassthrough`` pins that a fatal pattern found on
   the failure-threshold path escapes with its *pattern* error_type, not the
   generic ``task_failures``, so deleting the wrapper stays visible.
3. **The tautological success condition** at service.py:874-879:
   ``(rollout_state == "COMPLETED" or running == desired)`` — but
   ``running == desired`` is already required two lines above, so the whole
   parenthesis is always true and ``rolloutState`` is not actually consulted.
   ``TestStableSuccessIgnoresRolloutState`` pins success for
   ``IN_PROGRESS``/``FAILED``/missing alike. Pin, do not fix.
4. **The timeout ``RuntimeError`` message format** (service.py:923) — the
   ``max_attempts * poll_interval`` arithmetic and the trailing
   ``Last status: ...`` are asserted literally in ``TestStableTimeout``.
5. **``ThreadPoolExecutor`` first-error-wins** in ``wait_for_stable``: the first
   *completed* failing future re-raises, and the remaining futures still run to
   completion because the ``with`` block joins on exit (``TestWaitForStable``).
6. **Health-check timeout produces ``health_check_failed=True`` WITH
   ``success=True``** — a non-obvious combination, pinned both at the
   ``_wait_for_service_and_targets`` level and end-to-end through
   ``wait_for_stable`` against a real (empty) moto target group.
7. **The migration skip-hash paths** (service.py:504-512), against a real git
   repo and a real moto SSM parameter: the migrate task definition is
   registered *even when the migration is skipped*, and ``run_task`` is not
   called (``TestStartMigrationsSkipHash``).
8. **The ``str | None`` vs ``Path`` annotation mismatch.**
   ``start_migrations(source_dir: str | None)`` is annotated for ``str``, but
   ``deployer.py:117`` builds a resolved ``Path`` and
   ``migrations.should_skip_migrations(source_dir: Path, ...)`` wants a
   ``Path``. **Production always passes a ``Path``.** The annotation is wrong,
   not the code: ``compute_migrations_hash`` does ``Path(source_dir).resolve()``
   so both types work, and ``TestSourceDirTypes`` pins that they agree.
9. **Both polling loops' sleep behaviour** — ``time.sleep`` is stubbed at the
   stdlib module (the ``sleeps`` fixture), and the recorded interval *and*
   call count are asserted for ``_wait_for_service_stable`` and
   ``_wait_for_target_group_healthy``.

Stubbing follows 53d-2a's recorded rule — outermost boundary only, never a
``deployer`` binding:

* ECS is the ``ctx.ecs_client`` / ``ecs_client`` **parameter**, so the scripted
  client here is injected, not monkeypatched. moto is used for the module's two
  un-injected AWS reaches: ``boto3.client("elbv2")`` inside ``wait_for_stable``
  and the SSM parameter behind ``store_migrations_hash``/``should_skip_migrations``.
  moto is deliberately *not* used for ``ecs.run_task``: moto's
  ``_calculate_task_resource_requirements`` sums per-container ``memory``, and
  ``build_task_definition`` sets cpu/memory at the **task** level only (legal on
  real Fargate), so moto raises ``TypeError`` on the exact task-definition shape
  production emits.
* ``time.sleep`` is patched on the stdlib ``time`` module.
* The CloudWatch fetch in ``_display_migration_logs`` uses conftest's
  ``aws_cli`` fixture, which patches ``deployer.aws.cli.run_command`` — the one
  seam every ``aws`` CLI caller funnels through.
* The migrations hash runs against a **real** git repo under ``tmp_path``.

``service.py`` does not import ``..timing`` and must not start: 53e-3b's
``NullTimer`` has no ``_current_step``, so wiring ``get_timer()`` into these
polling loops would reproduce the ``AttributeError`` already pinned in
``test_deploy_images.py``. Nothing here introduces it.
"""

import re
import subprocess
import threading
import time
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

from deployer.deploy.context import DeploymentContext, StabilityConfig
from deployer.deploy.migrations import store_migrations_hash
from deployer.deploy.service import (
    FATAL_ERROR_PATTERNS,
    DeploymentError,
    MigrationTask,
    ServiceWaitResult,
    _check_for_fatal_errors,
    _display_migration_logs,
    _get_deployment_status,
    _get_service_target_group,
    _wait_for_service_and_targets,
    _wait_for_service_stable,
    _wait_for_target_group_healthy,
    start_migrations,
    wait_for_migrations,
    wait_for_stable,
)

APP_NAME = "testapp"
ENVIRONMENT = "staging"
CLUSTER = "testapp-staging"
REGION = "us-west-2"
ACCOUNT_ID = "123456789012"
TASK_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task/{CLUSTER}/abc123def456"
TG_ARN = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT_ID}:targetgroup/tg/0123456789abcdef"

_ANSI = re.compile(r"\033\[[0-9;]*m")


def _plain(captured: str) -> str:
    """Strip ANSI colour from a captured stream."""
    return _ANSI.sub("", captured)


def _lines(captured: str) -> list[str]:
    """Return a captured stream's non-blank lines, without ANSI colour."""
    return [line.rstrip() for line in _plain(captured).splitlines() if line.strip()]


def _client_error(code: str = "ClusterNotFoundException", operation: str = "DescribeServices"):
    """Build a botocore ClientError carrying the given error code."""
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


# ---------------------------------------------------------------------------
# Scripted AWS clients. These stand in for the *injected* boto3 clients, which
# is the module's outermost boundary for ECS and (in the helpers) ELBv2.
# ---------------------------------------------------------------------------


class FakeWaiter:
    """Records the kwargs a boto3 waiter was driven with."""

    def __init__(self, name: str):
        self.name = name
        self.waits: list[dict] = []

    def wait(self, **kwargs):
        """Stand in for waiter.wait(); records and returns immediately."""
        self.waits.append(kwargs)


class ScriptedClient:
    """A boto3-shaped client whose every operation is scripted.

    Each keyword is an operation name mapped to a result or a list of results;
    the final result repeats. An ``Exception`` instance in the script is raised
    instead of returned. Any operation that was not scripted is an error, so a
    test that forgets to script a call fails loudly rather than silently.

    ``wait_for_stable`` drives one of these from several threads, so the script
    cursor is taken under a lock. A *multi-service* threaded test must still use
    a single repeating result: which thread reaches the client first is not
    something ThreadPoolExecutor promises, and pinning it would pin the
    scheduler rather than the module.
    """

    def __init__(self, **scripts):
        self._scripts = {
            name: list(value) if isinstance(value, list) else [value]
            for name, value in scripts.items()
        }
        self._lock = threading.Lock()
        self.calls: list[tuple[str, dict]] = []
        self.waiters: list[FakeWaiter] = []

    def get_waiter(self, name: str) -> FakeWaiter:
        """Stand in for boto3's get_waiter()."""
        self.calls.append(("get_waiter", {"name": name}))
        waiter = FakeWaiter(name)
        self.waiters.append(waiter)
        return waiter

    def calls_to(self, operation: str) -> list[dict]:
        """Every kwargs dict the given operation was called with."""
        return [kwargs for name, kwargs in self.calls if name == operation]

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        script = self._scripts.get(name)
        if script is None:
            raise AssertionError(f"unscripted client operation: {name}")

        def _operation(**kwargs):
            with self._lock:
                self.calls.append((name, kwargs))
                result = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(result, Exception):
                raise result
            return result

        return _operation


def _deployment(
    running: int = 0,
    desired: int = 0,
    pending: int = 0,
    failed: int = 0,
    rollout_state: str | None = "",
    status: str = "PRIMARY",
) -> dict:
    """Build one ECS deployment dict."""
    deployment = {
        "status": status,
        "runningCount": running,
        "desiredCount": desired,
        "pendingCount": pending,
        "failedTasks": failed,
    }
    if rollout_state is not None:
        deployment["rolloutState"] = rollout_state
    return deployment


def _describe_services(
    deployments: list[dict] | None = None,
    events: list[dict] | None = None,
    load_balancers: list[dict] | None = None,
    **service_extra,
) -> dict:
    """Build a describe_services response holding exactly one service."""
    service = {
        "serviceName": "web",
        "status": "ACTIVE",
        "deployments": [] if deployments is None else deployments,
        "events": [] if events is None else events,
    }
    if load_balancers is not None:
        service["loadBalancers"] = load_balancers
    service.update(service_extra)
    return {"services": [service]}


def _stable_response(running: int = 1, desired: int = 1, **kwargs) -> dict:
    """A describe_services response the stability loop treats as success."""
    return _describe_services(deployments=[_deployment(running=running, desired=desired, **kwargs)])


def _events(*messages: str) -> list[dict]:
    """Build a list of ECS service event dicts."""
    return [{"message": message} for message in messages]


def _ctx(ecs_client=None, config: dict | None = None, dry_run: bool = False):
    """Build a DeploymentContext with the fields this half of the module reads."""
    return DeploymentContext(
        ecs_client=ecs_client,
        cluster_name=CLUSTER,
        config={} if config is None else config,
        service_config={},
        infra_config={},
        app_name=APP_NAME,
        environment=ENVIRONMENT,
        region=REGION,
        account_id=ACCOUNT_ID,
        env_config={},
        dry_run=dry_run,
    )


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Record and swallow every ``time.sleep`` at the stdlib, the outer boundary.

    Both polling loops call ``time.sleep`` through the ``time`` module, so
    patching the module attribute covers them without touching a deployer name.
    """
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


# ===========================================================================
# _check_for_fatal_errors -- MUST-PIN 1
# ===========================================================================


class TestCheckForFatalErrors:
    """Pins the FATAL_ERROR_PATTERNS table and its help-text substitution."""

    def test_table_has_exactly_seven_entries(self):
        """The table's size is pinned: 53e-5c must not quietly add or drop one."""
        assert len(FATAL_ERROR_PATTERNS) == 7

    def test_table_error_types_in_order(self):
        """The error_type column, in table order."""
        assert [entry[1] for entry in FATAL_ERROR_PATTERNS] == [
            "missing_ssm_parameters",
            "ecr_repo_not_found",
            "image_not_found",
            "iam_secrets_access",
            "secrets_pull_failed",
            "no_capacity",
            "iam_role_assume",
        ]

    @pytest.mark.parametrize(
        ("message", "error_type", "help_fragment"),
        [
            (
                "invalid ssm parameters: /testapp/staging/SECRET_KEY",
                "missing_ssm_parameters",
                "Missing SSM parameters: /testapp/staging/SECRET_KEY.",
            ),
            (
                "CannotPullContainerError: ... repository testapp does not exist",
                "ecr_repo_not_found",
                "ECR repository not found.",
            ),
            (
                "CannotPullContainerError: ... manifest for testapp:v1 not found",
                "image_not_found",
                "Image tag not found in ECR.",
            ),
            (
                "unable to pull secrets or registry auth: AccessDeniedException",
                "iam_secrets_access",
                "IAM role lacks permission to access secrets.",
            ),
            (
                "ResourceInitializationError: unable to pull secrets or registry auth",
                "secrets_pull_failed",
                "Failed to pull secrets.",
            ),
            (
                "service web was unable to place a task because "
                "No Container Instances were found in your cluster",
                "no_capacity",
                "No container instances available.",
            ),
            (
                "ECS was unable to assume the role 'arn:aws:iam::1:role/x'",
                "iam_role_assume",
                "ECS cannot assume the task execution role.",
            ),
        ],
    )
    def test_each_pattern_raises_its_error_type(self, message, error_type, help_fragment):
        """Every one of the seven patterns, with its operator-facing help text."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(_events(message), "web")

        error = exc_info.value
        assert error.error_type == error_type
        assert error.service_name == "web"
        assert str(error).startswith("web: ")
        assert help_fragment in str(error)

    def test_group_one_is_substituted_into_the_help_text(self):
        """MUST-PIN: ``{match}`` is filled from ``match.group(1)``."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(_events("Invalid SSM parameters: /a/ONE, /a/TWO"), "web")

        assert "Missing SSM parameters: /a/ONE, /a/TWO." in str(exc_info.value)

    def test_singular_parameter_also_matches(self):
        """The ``parameters?`` optional plural is pinned."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(_events("invalid ssm parameter: /a/ONE"), "web")

        assert "Missing SSM parameters: /a/ONE." in str(exc_info.value)

    def test_remediation_command_is_part_of_the_message(self):
        """The SSM help text carries a copy-pasteable command; pinned literally."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(_events("invalid ssm parameters: /a/ONE"), "web")

        assert "aws ssm put-parameter --name <name> --type SecureString --value <value>" in str(
            exc_info.value
        )

    def test_matching_is_case_insensitive(self):
        """``re.IGNORECASE`` is pinned: shouty AWS event text still matches."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(_events("ECS WAS UNABLE TO ASSUME THE ROLE"), "web")

        assert exc_info.value.error_type == "iam_role_assume"

    def test_patterns_without_a_group_substitute_empty_string(self):
        """``match.lastindex`` is None for the group-less patterns; help is verbatim."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(_events("No Container Instances were found"), "web")

        assert str(exc_info.value) == (
            "web: No container instances available. "
            "For Fargate, check subnet/security group configuration."
        )

    def test_empty_events_is_a_no_op(self):
        """No events, no raise, no output."""
        assert _check_for_fatal_errors([], "web") is None

    def test_event_without_a_message_key_is_a_no_op(self):
        """A malformed event defaults to the empty message and matches nothing."""
        assert _check_for_fatal_errors([{"id": "1"}], "web") is None

    def test_benign_events_do_not_raise(self):
        """Normal ECS chatter passes through untouched."""
        assert (
            _check_for_fatal_errors(_events("has started 1 tasks", "registered 1 targets"), "web")
            is None
        )

    def test_first_matching_event_wins(self):
        """Events are scanned in order; the earliest match raises."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(
                _events(
                    "ECS was unable to assume the role",
                    "invalid ssm parameters: /a/ONE",
                ),
                "web",
            )

        assert exc_info.value.error_type == "iam_role_assume"

    def test_pattern_order_wins_within_one_event(self):
        """Within a single event the table order decides, not the text order."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(
                _events("ECS was unable to assume the role; invalid ssm parameters: /a/ONE"),
                "web",
            )

        assert exc_info.value.error_type == "missing_ssm_parameters"

    def test_service_name_is_carried_on_the_exception(self):
        """Both the message prefix and the attribute carry the service name."""
        with pytest.raises(DeploymentError) as exc_info:
            _check_for_fatal_errors(_events("invalid ssm parameters: /a/ONE"), "worker")

        assert exc_info.value.service_name == "worker"
        assert str(exc_info.value).startswith("worker: ")


# ===========================================================================
# _get_deployment_status
# ===========================================================================


class TestGetDeploymentStatus:
    """Pins the deployment-status extraction, including its missing-key defaults."""

    def test_no_deployments_returns_all_zero(self):
        """The service has never deployed: (None, 0, 0, 0)."""
        assert _get_deployment_status({}) == (None, 0, 0, 0)

    def test_empty_deployment_list_returns_all_zero(self):
        assert _get_deployment_status({"deployments": []}) == (None, 0, 0, 0)

    def test_no_primary_deployment_returns_all_zero(self):
        """Only ACTIVE/DRAINING deployments: still (None, 0, 0, 0)."""
        service = {"deployments": [_deployment(running=3, desired=3, status="ACTIVE")]}
        assert _get_deployment_status(service) == (None, 0, 0, 0)

    def test_primary_counts_are_returned(self):
        primary = _deployment(running=2, desired=3, pending=1, failed=4)
        result = _get_deployment_status({"deployments": [primary]})
        assert result == (primary, 2, 3, 4)

    def test_missing_counts_default_to_zero(self):
        """A PRIMARY deployment with no count keys reads as all zeroes."""
        primary = {"status": "PRIMARY"}
        assert _get_deployment_status({"deployments": [primary]}) == (primary, 0, 0, 0)

    def test_first_primary_wins(self):
        """``next()`` takes the first PRIMARY, not the last."""
        first = _deployment(running=1, desired=1)
        second = _deployment(running=9, desired=9)
        result = _get_deployment_status({"deployments": [first, second]})
        assert result[0] is first
        assert result[1:] == (1, 1, 0)

    def test_deployment_without_status_key_raises_keyerror(self):
        """Latent bug pinned: the comprehension uses ``d["status"]``, not ``.get``.

        An ECS deployment dict lacking ``status`` therefore propagates a bare
        KeyError out of a status *reader*. Pinned as-is.
        """
        with pytest.raises(KeyError, match="status"):
            _get_deployment_status({"deployments": [{"runningCount": 1}]})


# ===========================================================================
# _get_service_target_group
# ===========================================================================


class TestGetServiceTargetGroup:
    """Pins the load-balancer lookup and its four None routes."""

    def test_returns_first_target_group_arn(self):
        client = ScriptedClient(
            describe_services=_describe_services(
                load_balancers=[{"targetGroupArn": TG_ARN}, {"targetGroupArn": "arn:other"}]
            )
        )
        assert _get_service_target_group(client, CLUSTER, "web") == TG_ARN

    def test_passes_cluster_and_service(self):
        client = ScriptedClient(describe_services=_describe_services())
        _get_service_target_group(client, CLUSTER, "web")
        assert client.calls_to("describe_services") == [{"cluster": CLUSTER, "services": ["web"]}]

    def test_client_error_returns_none_silently(self):
        """A ClientError is swallowed with no logging at all."""
        client = ScriptedClient(describe_services=_client_error())
        assert _get_service_target_group(client, CLUSTER, "web") is None

    def test_no_services_returns_none(self):
        client = ScriptedClient(describe_services={"services": []})
        assert _get_service_target_group(client, CLUSTER, "web") is None

    def test_no_load_balancers_key_returns_none(self):
        client = ScriptedClient(describe_services=_describe_services())
        assert _get_service_target_group(client, CLUSTER, "web") is None

    def test_empty_load_balancer_list_returns_none(self):
        client = ScriptedClient(describe_services=_describe_services(load_balancers=[]))
        assert _get_service_target_group(client, CLUSTER, "web") is None

    def test_load_balancer_without_arn_returns_none(self):
        """A load balancer entry with no ``targetGroupArn`` yields None, not KeyError."""
        client = ScriptedClient(
            describe_services=_describe_services(load_balancers=[{"containerName": "web"}])
        )
        assert _get_service_target_group(client, CLUSTER, "web") is None


# ===========================================================================
# _wait_for_target_group_healthy -- MUST-PIN 9 (second polling loop)
# ===========================================================================


def _target_health(*states: str) -> dict:
    """Build a describe_target_health response with the given target states."""
    return {"TargetHealthDescriptions": [{"TargetHealth": {"State": state}} for state in states]}


class TestWaitForTargetGroupHealthy:
    """Pins the ELBv2 polling loop, its dedup printing and its timeout."""

    def test_all_healthy_returns_true(self, sleeps, capsys):
        client = ScriptedClient(describe_target_health=_target_health("healthy", "healthy"))
        assert _wait_for_target_group_healthy(client, TG_ARN, "web") is True
        assert sleeps == []
        assert "web target group: healthy=2/2" in _plain(capsys.readouterr().out)

    def test_success_logs_done(self, sleeps, capsys):
        client = ScriptedClient(describe_target_health=_target_health("healthy"))
        _wait_for_target_group_healthy(client, TG_ARN, "web")
        assert "web targets healthy [done]" in _plain(capsys.readouterr().out)

    def test_client_error_returns_true_without_failing_the_deploy(self, sleeps, capsys):
        """Pinned: an unreadable target group is treated as healthy, not as a failure."""
        client = ScriptedClient(
            describe_target_health=_client_error("AccessDenied", "DescribeTargetHealth")
        )
        assert _wait_for_target_group_healthy(client, TG_ARN, "web") is True
        assert "Could not check target health" in _plain(capsys.readouterr().out)
        assert sleeps == []

    def test_becomes_healthy_after_polling(self, sleeps):
        client = ScriptedClient(
            describe_target_health=[
                _target_health("initial", "initial"),
                _target_health("healthy", "initial"),
                _target_health("healthy", "healthy"),
            ]
        )
        assert _wait_for_target_group_healthy(client, TG_ARN, "web") is True
        assert sleeps == [10, 10]

    def test_status_line_printed_only_when_it_changes(self, sleeps, capsys):
        client = ScriptedClient(
            describe_target_health=[
                _target_health("initial", "initial"),
                _target_health("initial", "initial"),
                _target_health("healthy", "healthy"),
            ]
        )
        _wait_for_target_group_healthy(client, TG_ARN, "web")
        printed = [line for line in _lines(capsys.readouterr().out) if "target group:" in line]
        assert printed == ["  web target group: healthy=0/2", "  web target group: healthy=2/2"]

    def test_partial_health_never_succeeds(self, sleeps):
        """``healthy == total`` is required, so 1-of-2 polls to the timeout."""
        client = ScriptedClient(describe_target_health=_target_health("healthy", "unhealthy"))
        assert _wait_for_target_group_healthy(client, TG_ARN, "web", max_attempts=3) is False

    def test_empty_target_group_never_succeeds(self, sleeps):
        """Latent bug pinned: 0 healthy of 0 targets fails ``healthy > 0``.

        An empty target group can therefore never satisfy the loop, so a service
        whose tasks never register always burns the full timeout.
        """
        client = ScriptedClient(describe_target_health={"TargetHealthDescriptions": []})
        assert _wait_for_target_group_healthy(client, TG_ARN, "web", max_attempts=2) is False

    def test_timeout_returns_false_and_warns(self, sleeps, capsys):
        client = ScriptedClient(describe_target_health=_target_health("unhealthy"))
        assert _wait_for_target_group_healthy(client, TG_ARN, "web", max_attempts=4) is False
        out = _plain(capsys.readouterr().out)
        assert "web: Target group health check timed out (last: healthy=0/1)" in out

    def test_default_poll_interval_and_attempts(self, sleeps):
        """Defaults are 10s x 30 attempts; the loop sleeps once per attempt."""
        client = ScriptedClient(describe_target_health=_target_health("unhealthy"))
        _wait_for_target_group_healthy(client, TG_ARN, "web")
        assert sleeps == [10] * 30

    def test_custom_poll_interval_is_used(self, sleeps):
        client = ScriptedClient(describe_target_health=_target_health("unhealthy"))
        _wait_for_target_group_healthy(client, TG_ARN, "web", poll_interval=3, max_attempts=5)
        assert sleeps == [3] * 5

    def test_target_group_arn_is_passed_through(self, sleeps):
        client = ScriptedClient(describe_target_health=_target_health("healthy"))
        _wait_for_target_group_healthy(client, TG_ARN, "web")
        assert client.calls_to("describe_target_health") == [{"TargetGroupArn": TG_ARN}]

    def test_missing_target_health_key_counts_as_unhealthy(self, sleeps):
        """A target with no ``TargetHealth`` block is not counted as healthy."""
        client = ScriptedClient(
            describe_target_health={"TargetHealthDescriptions": [{"Target": {"Id": "1"}}]}
        )
        assert _wait_for_target_group_healthy(client, TG_ARN, "web", max_attempts=1) is False


# ===========================================================================
# _wait_for_service_stable -- MUST-PIN 2, 3, 4, 9
# ===========================================================================

FAST = StabilityConfig(poll_interval=1, max_attempts=3, failure_threshold=3)


class TestStableSuccess:
    """Pins the success route out of the stability loop."""

    def test_running_equals_desired_returns_none(self, sleeps, capsys):
        client = ScriptedClient(describe_services=_stable_response(1, 1, rollout_state="COMPLETED"))
        assert _wait_for_service_stable(_ctx(client), "web", FAST) is None
        assert sleeps == []
        assert "web (stable) [done]" in _plain(capsys.readouterr().out)

    def test_status_line_is_printed(self, sleeps, capsys):
        client = ScriptedClient(describe_services=_stable_response(2, 2, rollout_state="COMPLETED"))
        _wait_for_service_stable(_ctx(client), "web", FAST)
        assert "  web: running=2/2" in _lines(capsys.readouterr().out)

    def test_status_includes_failed_count(self, sleeps, capsys):
        client = ScriptedClient(
            describe_services=_stable_response(2, 2, failed=1, rollout_state="COMPLETED")
        )
        _wait_for_service_stable(_ctx(client), "web", FAST)
        assert "  web: running=2/2, failed=1" in _lines(capsys.readouterr().out)

    def test_status_line_printed_only_when_it_changes(self, sleeps, capsys):
        client = ScriptedClient(
            describe_services=[
                _stable_response(0, 2, pending=2),
                _stable_response(0, 2, pending=2),
                _stable_response(2, 2, rollout_state="COMPLETED"),
            ]
        )
        _wait_for_service_stable(_ctx(client), "web", FAST)
        printed = [line for line in _lines(capsys.readouterr().out) if line.startswith("  web: ")]
        assert printed == ["  web: running=0/2", "  web: running=2/2"]

    def test_pending_tasks_block_success(self, sleeps):
        """``pendingCount`` must be 0; otherwise the loop keeps polling to timeout."""
        client = ScriptedClient(describe_services=_stable_response(2, 2, pending=1))
        with pytest.raises(RuntimeError):
            _wait_for_service_stable(_ctx(client), "web", FAST)

    def test_zero_running_is_not_success(self, sleeps):
        """0/0 satisfies ``running == desired`` but fails ``running > 0``."""
        client = ScriptedClient(describe_services=_stable_response(0, 0))
        with pytest.raises(RuntimeError):
            _wait_for_service_stable(_ctx(client), "web", FAST)

    def test_sleeps_once_per_unsuccessful_poll(self, sleeps):
        """MUST-PIN 9: the interval and the count both come from StabilityConfig."""
        client = ScriptedClient(describe_services=_stable_response(0, 2, pending=2))
        with pytest.raises(RuntimeError):
            _wait_for_service_stable(_ctx(client), "web", StabilityConfig(7, 4, 99))
        assert sleeps == [7] * 4


class TestStableSuccessIgnoresRolloutState:
    """MUST-PIN 3: the success condition's last clause is tautological.

    service.py:874-879 reads::

        running == desired and pending == 0 and running > 0
        and (rollout_state == "COMPLETED" or running == desired)

    ``running == desired`` is already required, so the parenthesised clause is
    always true and ``rolloutState`` never actually gates success. These pins
    record that, so 53e-5d deleting the clause is provably a no-op and
    "fixing" it to require COMPLETED is provably not.
    """

    @pytest.mark.parametrize(
        "rollout_state", ["COMPLETED", "IN_PROGRESS", "FAILED", "", "GARBAGE", None]
    )
    def test_any_rollout_state_succeeds(self, rollout_state, sleeps):
        client = ScriptedClient(
            describe_services=_stable_response(1, 1, rollout_state=rollout_state)
        )
        assert _wait_for_service_stable(_ctx(client), "web", FAST) is None


class TestStableLookupFailures:
    """Pins the two ways the describe call itself ends the wait."""

    def test_client_error_is_logged_and_reraised(self, sleeps, capsys):
        client = ScriptedClient(describe_services=_client_error())
        with pytest.raises(ClientError):
            _wait_for_service_stable(_ctx(client), "web", FAST)
        assert "Failed to describe service web" in _plain(capsys.readouterr().out)

    def test_missing_service_raises_service_not_found(self, sleeps):
        client = ScriptedClient(describe_services={"services": []})
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", FAST)
        assert exc_info.value.error_type == "service_not_found"
        assert str(exc_info.value) == f"Service web not found in cluster {CLUSTER}"

    def test_describe_is_called_with_cluster_and_service(self, sleeps):
        client = ScriptedClient(describe_services=_stable_response(1, 1))
        _wait_for_service_stable(_ctx(client), "web", FAST)
        assert client.calls_to("describe_services") == [{"cluster": CLUSTER, "services": ["web"]}]


class TestStableFatalEvents:
    """Pins the fatal-event scan inside the loop."""

    def test_fatal_event_aborts_immediately(self, sleeps):
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=1, desired=1)],
                events=_events("invalid ssm parameters: /a/ONE"),
            )
        )
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", FAST)
        assert exc_info.value.error_type == "missing_ssm_parameters"
        assert sleeps == []

    def test_only_the_first_five_events_are_scanned(self, sleeps):
        """Pinned: ``events[:5]``. A fatal message in the 6th event is invisible."""
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=1, desired=1, rollout_state="COMPLETED")],
                events=_events(
                    "ok 1", "ok 2", "ok 3", "ok 4", "ok 5", "invalid ssm parameters: /a/ONE"
                ),
            )
        )
        assert _wait_for_service_stable(_ctx(client), "web", FAST) is None

    def test_fifth_event_is_still_scanned(self, sleeps):
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=1, desired=1)],
                events=_events("ok 1", "ok 2", "ok 3", "ok 4", "invalid ssm parameters: /a/ONE"),
            )
        )
        with pytest.raises(DeploymentError):
            _wait_for_service_stable(_ctx(client), "web", FAST)


class TestStableNoPrimaryDeployment:
    """Pins the 'no primary deployment' branch: warn, sleep, keep going."""

    def test_warns_and_keeps_polling(self, sleeps, capsys):
        client = ScriptedClient(describe_services=_describe_services(deployments=[]))
        with pytest.raises(RuntimeError):
            _wait_for_service_stable(_ctx(client), "web", FAST)
        out = _plain(capsys.readouterr().out)
        assert out.count("web: No primary deployment found") == 3
        assert sleeps == [1, 1, 1]

    def test_last_status_stays_empty_in_the_timeout_message(self, sleeps):
        """No status line was ever built, so the timeout ends with an empty tail."""
        client = ScriptedClient(describe_services=_describe_services(deployments=[]))
        with pytest.raises(RuntimeError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", FAST)
        assert str(exc_info.value).endswith("Last status: ")

    def test_recovers_when_a_primary_appears(self, sleeps):
        client = ScriptedClient(
            describe_services=[
                _describe_services(deployments=[]),
                _stable_response(1, 1, rollout_state="COMPLETED"),
            ]
        )
        assert _wait_for_service_stable(_ctx(client), "web", FAST) is None


class TestStableFailureThreshold:
    """Pins the ``failedTasks >= failure_threshold`` route."""

    def test_generic_task_failures_error(self, sleeps):
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=0, desired=2, failed=3)],
                events=_events("task stopped: Essential container in task exited"),
            )
        )
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", FAST)
        assert exc_info.value.error_type == "task_failures"
        assert str(exc_info.value) == (
            "web: 3 tasks failed. Latest event: " "task stopped: Essential container in task exited"
        )

    def test_no_events_uses_the_default_error_text(self, sleeps):
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=0, desired=2, failed=5)]
            )
        )
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", FAST)
        assert str(exc_info.value) == (
            "web: 5 tasks failed. Latest event: Tasks are failing repeatedly"
        )

    def test_below_threshold_keeps_polling(self, sleeps):
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=1, desired=2, failed=2)],
                events=_events("still trying"),
            )
        )
        with pytest.raises(RuntimeError):
            _wait_for_service_stable(_ctx(client), "web", FAST)

    def test_threshold_is_configurable(self, sleeps):
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=1, desired=2, failed=1)],
                events=_events("still trying"),
            )
        )
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(
                _ctx(client), "web", StabilityConfig(1, 3, failure_threshold=1)
            )
        assert exc_info.value.error_type == "task_failures"


class TestFailureThresholdFatalPassthrough:
    """MUST-PIN 2: the no-op ``try/except DeploymentError: raise`` at 891-894.

    The wrapper catches DeploymentError only to re-raise it, which is exactly
    what would happen without the try block. What matters behaviourally is that
    a fatal pattern found here escapes with its **own** error_type rather than
    being flattened into ``task_failures`` -- pinned below so that deleting the
    wrapper (53e-5d will want to) can be shown to change nothing.
    """

    def test_fatal_pattern_wins_over_the_generic_task_failures_error(self, sleeps):
        # The events reach _check_for_fatal_errors at the top of the loop too, so
        # to exercise the 891-894 call the fatal text must be outside events[:5].
        # It is not reachable there either -- which is the second half of the pin:
        # by construction the inner call can only ever re-find what the outer call
        # already found, making the whole block dead code.
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=0, desired=2, failed=3)],
                events=_events("invalid ssm parameters: /a/ONE"),
            )
        )
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", FAST)

        # Raised by the *outer* scan at service.py:850, never reaching 891.
        assert exc_info.value.error_type == "missing_ssm_parameters"

    def test_generic_error_is_what_the_wrapper_actually_falls_through_to(self, sleeps):
        """With no fatal pattern the try block is a pass-through to the generic raise."""
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=0, desired=2, failed=3)],
                events=_events("nothing recognisable here"),
            )
        )
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", FAST)
        assert exc_info.value.error_type == "task_failures"


class TestStableNoProgress:
    """Pins the consecutive-failure counter (running == 0 and failed > 0)."""

    def test_three_consecutive_polls_raise_no_progress(self, sleeps):
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=0, desired=2, failed=1)],
                events=_events("task stopped: exit 1"),
            )
        )
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(
                _ctx(client), "web", StabilityConfig(1, 10, failure_threshold=99)
            )
        assert exc_info.value.error_type == "no_progress"
        assert str(exc_info.value) == (
            "web: No tasks running after multiple attempts. "
            "Check ECS console for details. Latest: task stopped: exit 1"
        )
        assert sleeps == [1, 1]

    def test_two_polls_are_not_enough(self, sleeps):
        client = ScriptedClient(
            describe_services=[
                _describe_services(
                    deployments=[_deployment(running=0, desired=2, failed=1)],
                    events=_events("task stopped: exit 1"),
                ),
                _describe_services(
                    deployments=[_deployment(running=0, desired=2, failed=1)],
                    events=_events("task stopped: exit 1"),
                ),
                _stable_response(2, 2, rollout_state="COMPLETED"),
            ]
        )
        assert (
            _wait_for_service_stable(
                _ctx(client), "web", StabilityConfig(1, 10, failure_threshold=99)
            )
            is None
        )

    def test_counter_resets_when_a_task_runs(self, sleeps):
        """Latent behaviour pinned: any poll with running > 0 zeroes the counter."""
        failing = _describe_services(
            deployments=[_deployment(running=0, desired=2, failed=1)],
            events=_events("task stopped: exit 1"),
        )
        recovering = _describe_services(
            deployments=[_deployment(running=1, desired=2, failed=1)],
            events=_events("task stopped: exit 1"),
        )
        client = ScriptedClient(
            describe_services=[failing, failing, recovering, failing, failing, failing]
        )
        with pytest.raises(DeploymentError) as exc_info:
            _wait_for_service_stable(
                _ctx(client), "web", StabilityConfig(1, 10, failure_threshold=99)
            )
        assert exc_info.value.error_type == "no_progress"
        assert len(client.calls_to("describe_services")) == 6

    def test_no_events_means_no_progress_never_fires(self, sleeps):
        """``consecutive_failures >= 3 and events`` -- an empty event list times out."""
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=0, desired=2, failed=1)]
            )
        )
        with pytest.raises(RuntimeError):
            _wait_for_service_stable(
                _ctx(client), "web", StabilityConfig(1, 5, failure_threshold=99)
            )

    def test_failed_zero_does_not_count(self, sleeps):
        """running == 0 with no failures is a normal slow start, not no-progress."""
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=0, desired=2, pending=2)],
                events=_events("has started 2 tasks"),
            )
        )
        with pytest.raises(RuntimeError):
            _wait_for_service_stable(
                _ctx(client), "web", StabilityConfig(1, 5, failure_threshold=99)
            )


class TestStableTimeout:
    """MUST-PIN 4: the timeout RuntimeError's message, verbatim."""

    def test_message_format(self, sleeps):
        client = ScriptedClient(describe_services=_stable_response(1, 3, pending=2))
        with pytest.raises(RuntimeError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", StabilityConfig(15, 40, 3))
        assert str(exc_info.value) == (
            "Service web did not stabilize after 600s. Last status: running=1/3"
        )

    def test_seconds_are_attempts_times_interval(self, sleeps):
        client = ScriptedClient(describe_services=_stable_response(1, 3, pending=2))
        with pytest.raises(RuntimeError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", StabilityConfig(2, 5, 99))
        assert "did not stabilize after 10s" in str(exc_info.value)

    def test_last_status_carries_the_failed_count(self, sleeps):
        client = ScriptedClient(
            describe_services=_describe_services(
                deployments=[_deployment(running=1, desired=3, pending=1, failed=1)],
                events=_events("still trying"),
            )
        )
        with pytest.raises(RuntimeError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", StabilityConfig(1, 2, 99))
        assert str(exc_info.value).endswith("Last status: running=1/3, failed=1")

    def test_it_is_a_runtimeerror_not_a_deploymenterror(self, sleeps):
        """The timeout is the one failure with no service_name/error_type attached."""
        client = ScriptedClient(describe_services=_stable_response(1, 3, pending=2))
        with pytest.raises(RuntimeError) as exc_info:
            _wait_for_service_stable(_ctx(client), "web", FAST)
        assert not isinstance(exc_info.value, DeploymentError)


# ===========================================================================
# _wait_for_service_and_targets -- MUST-PIN 6
# ===========================================================================


class TestWaitForServiceAndTargets:
    """Pins the thread-safe wrapper's result shapes."""

    def test_stable_and_not_load_balanced(self, sleeps):
        client = ScriptedClient(
            describe_services=[
                _stable_response(1, 1, rollout_state="COMPLETED"),
                _describe_services(),
            ]
        )
        elbv2 = ScriptedClient()
        result = _wait_for_service_and_targets(_ctx(client), elbv2, "web", FAST)
        assert result == ServiceWaitResult(service_name="web", success=True)
        assert elbv2.calls == []

    def test_stable_and_targets_healthy(self, sleeps):
        client = ScriptedClient(
            describe_services=[
                _stable_response(1, 1, rollout_state="COMPLETED"),
                _describe_services(load_balancers=[{"targetGroupArn": TG_ARN}]),
            ]
        )
        elbv2 = ScriptedClient(describe_target_health=_target_health("healthy"))
        result = _wait_for_service_and_targets(_ctx(client), elbv2, "web", FAST)
        assert result.success is True
        assert result.health_check_failed is False
        assert result.error is None

    def test_health_check_timeout_is_success_true_with_failure_flag(self, sleeps):
        """MUST-PIN 6: a health-check timeout is reported as a *successful* wait.

        ``success=True`` and ``health_check_failed=True`` travel together; only
        the second is surfaced by wait_for_stable, as a non-fatal warning list.
        """
        client = ScriptedClient(
            describe_services=[
                _stable_response(1, 1, rollout_state="COMPLETED"),
                _describe_services(load_balancers=[{"targetGroupArn": TG_ARN}]),
            ]
        )
        elbv2 = ScriptedClient(describe_target_health=_target_health("unhealthy"))
        result = _wait_for_service_and_targets(_ctx(client), elbv2, "web", FAST)
        assert result == ServiceWaitResult(
            service_name="web", success=True, health_check_failed=True, error=None
        )

    def test_deployment_error_is_captured_not_raised(self, sleeps):
        client = ScriptedClient(describe_services={"services": []})
        result = _wait_for_service_and_targets(_ctx(client), ScriptedClient(), "web", FAST)
        assert result.success is False
        assert isinstance(result.error, DeploymentError)
        assert result.error.error_type == "service_not_found"
        assert result.health_check_failed is False

    def test_runtime_error_is_captured_not_raised(self, sleeps):
        client = ScriptedClient(describe_services=_stable_response(0, 2, pending=2))
        result = _wait_for_service_and_targets(_ctx(client), ScriptedClient(), "web", FAST)
        assert result.success is False
        assert isinstance(result.error, RuntimeError)
        assert "did not stabilize" in str(result.error)

    def test_client_error_escapes_the_wrapper(self, sleeps):
        """Latent bug pinned: only DeploymentError/RuntimeError are captured.

        A ClientError from describe_services propagates out of the worker and
        surfaces from ``future.result()`` inside wait_for_stable's loop, so it
        bypasses the ServiceWaitResult collection entirely.
        """
        client = ScriptedClient(describe_services=_client_error())
        with pytest.raises(ClientError):
            _wait_for_service_and_targets(_ctx(client), ScriptedClient(), "web", FAST)

    def test_target_group_lookup_failure_is_treated_as_not_load_balanced(self, sleeps):
        client = ScriptedClient(
            describe_services=[
                _stable_response(1, 1, rollout_state="COMPLETED"),
                _client_error(),
            ]
        )
        elbv2 = ScriptedClient()
        result = _wait_for_service_and_targets(_ctx(client), elbv2, "web", FAST)
        assert result.success is True
        assert elbv2.calls == []


# ===========================================================================
# wait_for_stable -- MUST-PIN 5, 6
# ===========================================================================


class TestWaitForStableEarlyExits:
    """The two routes that never touch AWS."""

    def test_dry_run_returns_empty_and_prints(self, sleeps, capsys):
        result = wait_for_stable(
            _ctx(ScriptedClient(), config={"services": {"web": {}}}, dry_run=True)
        )
        assert result == []
        out = _plain(capsys.readouterr().out)
        assert "[dry-run] aws ecs wait services-stable" in out

    def test_no_services_returns_empty(self, sleeps):
        """The early return also protects ThreadPoolExecutor(max_workers=0)."""
        assert wait_for_stable(_ctx(ScriptedClient(), config={"services": {}})) == []

    def test_no_services_key_returns_empty(self, sleeps):
        assert wait_for_stable(_ctx(ScriptedClient())) == []

    def test_header_is_logged_before_the_dry_run_check(self, sleeps, capsys):
        wait_for_stable(_ctx(ScriptedClient(), dry_run=True))
        assert "Waiting for services to stabilize..." in _plain(capsys.readouterr().out)


@pytest.mark.usefixtures("mocked_aws")
class TestWaitForStable:
    """Pins the parallel wait. ``boto3.client("elbv2")`` is real, via moto."""

    def test_all_services_stable_returns_empty(self, sleeps):
        # One repeating response, because two workers share this client: it is
        # both "stable" for the stability loop and "not load balanced" for the
        # target-group lookup, so thread interleaving cannot change the outcome.
        client = ScriptedClient(describe_services=_stable_response(1, 1, rollout_state="COMPLETED"))
        ctx = _ctx(client, config={"services": {"web": {}, "worker": {}}})
        assert wait_for_stable(ctx) == []
        assert len(client.calls_to("describe_services")) == 4

    def test_health_check_failure_is_returned_not_raised(self, sleeps):
        """MUST-PIN 6 end-to-end, against a real (empty) moto target group."""
        ec2 = boto3.client("ec2", region_name=REGION)
        vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        elbv2 = boto3.client("elbv2", region_name=REGION)
        target_group_arn = elbv2.create_target_group(
            Name="tg", Protocol="HTTP", Port=80, VpcId=vpc_id, TargetType="ip"
        )["TargetGroups"][0]["TargetGroupArn"]

        client = ScriptedClient(
            describe_services=[
                _stable_response(1, 1, rollout_state="COMPLETED"),
                _describe_services(load_balancers=[{"targetGroupArn": target_group_arn}]),
            ]
        )
        ctx = _ctx(client, config={"services": {"web": {}}})
        assert wait_for_stable(ctx) == ["web"]

    def test_first_error_wins_and_propagates(self, sleeps):
        """MUST-PIN 5: a failing future re-raises its original exception."""
        client = ScriptedClient(describe_services={"services": []})
        ctx = _ctx(client, config={"services": {"web": {}}})
        with pytest.raises(DeploymentError) as exc_info:
            wait_for_stable(ctx)
        assert exc_info.value.error_type == "service_not_found"

    def test_every_service_is_visited_even_when_one_fails(self, sleeps):
        """The ``with`` block joins on exit, so no worker is abandoned mid-flight."""
        client = ScriptedClient(describe_services={"services": []})
        ctx = _ctx(client, config={"services": {"web": {}, "worker": {}, "beat": {}}})
        with pytest.raises(DeploymentError):
            wait_for_stable(ctx)
        assert len(client.calls_to("describe_services")) == 3

    def test_the_raised_error_is_one_of_the_failures(self, sleeps):
        """With several failures, whichever future completes first is the one raised."""
        client = ScriptedClient(describe_services={"services": []})
        ctx = _ctx(client, config={"services": {"web": {}, "worker": {}}})
        with pytest.raises(DeploymentError) as exc_info:
            wait_for_stable(ctx)
        assert exc_info.value.service_name in {"web", "worker"}

    def test_service_names_come_from_config_services_keys(self, sleeps):
        client = ScriptedClient(
            describe_services=[
                _stable_response(1, 1, rollout_state="COMPLETED"),
                _describe_services(),
            ]
        )
        ctx = _ctx(client, config={"services": {"beat": {"image": "web"}}})
        assert wait_for_stable(ctx) == []
        assert client.calls_to("describe_services")[0]["services"] == ["beat"]


# ===========================================================================
# start_migrations -- MUST-PIN 7, 8
# ===========================================================================


def _migration_config(**overrides) -> dict:
    """A deploy.toml-shaped config with migrations enabled."""
    migrations = {"enabled": True}
    migrations.update(overrides)
    return {"migrations": migrations, "services": {"web": {}}}


def _migration_ecs_client(**extra) -> ScriptedClient:
    """A scripted ECS client that can serve the whole start_migrations path."""
    scripts = {
        "register_task_definition": {
            "taskDefinition": {
                "taskDefinitionArn": (
                    f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task-definition/"
                    f"{APP_NAME}-{ENVIRONMENT}-migrate:7"
                )
            }
        },
        "describe_services": _describe_services(
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": ["subnet-1"],
                    "securityGroups": ["sg-1"],
                    "assignPublicIp": "DISABLED",
                }
            }
        ),
        "run_task": {"tasks": [{"taskArn": TASK_ARN}]},
    }
    scripts.update(extra)
    return ScriptedClient(**scripts)


@pytest.fixture
def migrations_repo(tmp_path: Path) -> Path:
    """A real git repo with one migration file, for the real hashing path."""
    (tmp_path / "app" / "migrations").mkdir(parents=True)
    (tmp_path / "app" / "migrations" / "0001_initial.py").write_text("# migration\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S603, S607
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)  # noqa: S603, S607
    return tmp_path


class TestStartMigrationsDisabled:
    """Pins the routes that return before anything is registered."""

    def test_no_migrations_section(self):
        client = ScriptedClient()
        assert start_migrations(_ctx(client, config={}), {}, None) is None
        assert client.calls == []

    def test_migrations_not_enabled(self):
        client = ScriptedClient()
        ctx = _ctx(client, config={"migrations": {"service": "web"}})
        assert start_migrations(ctx, {"web": "uri"}, None) is None
        assert client.calls == []

    def test_enabled_false(self):
        client = ScriptedClient()
        ctx = _ctx(client, config={"migrations": {"enabled": False}})
        assert start_migrations(ctx, {"web": "uri"}, None) is None
        assert client.calls == []

    def test_missing_image_uri_logs_and_returns_none(self, capsys):
        client = ScriptedClient()
        ctx = _ctx(client, config=_migration_config())
        assert start_migrations(ctx, {}, None) is None
        out = _plain(capsys.readouterr().out)
        assert "No image URI for migration service web (image: web)" in out
        assert client.calls == []


class TestStartMigrationsImageResolution:
    """Pins how the migration image is chosen."""

    def test_default_service_is_web(self, capsys):
        ctx = _ctx(ScriptedClient(), config={"migrations": {"enabled": True}})
        start_migrations(ctx, {}, None)
        assert "migration service web (image: web)" in _plain(capsys.readouterr().out)

    def test_migration_service_is_configurable(self, capsys):
        ctx = _ctx(ScriptedClient(), config={"migrations": {"enabled": True, "service": "api"}})
        start_migrations(ctx, {}, None)
        assert "migration service api (image: api)" in _plain(capsys.readouterr().out)

    def test_image_name_comes_from_the_service_config(self, capsys):
        ctx = _ctx(
            ScriptedClient(),
            config={
                "migrations": {"enabled": True, "service": "worker"},
                "services": {"worker": {"image": "web"}},
            },
        )
        start_migrations(ctx, {}, None)
        assert "migration service worker (image: web)" in _plain(capsys.readouterr().out)

    def test_shared_image_is_looked_up_by_image_name(self):
        client = _migration_ecs_client()
        ctx = _ctx(
            client,
            config={
                "migrations": {"enabled": True, "service": "worker"},
                "services": {"worker": {"image": "web"}},
            },
        )
        assert start_migrations(ctx, {"web": "img:web"}, None) is not None
        registered = client.calls_to("register_task_definition")[0]
        assert registered["containerDefinitions"][0]["image"] == "img:web"


class TestStartMigrationsDryRun:
    """A dry run still registers the task definition."""

    def test_returns_none_and_prints_the_run_task_line(self, capsys):
        client = ScriptedClient()
        ctx = _ctx(client, config=_migration_config(), dry_run=True)
        assert start_migrations(ctx, {"web": "img:web"}, None) is None
        out = _plain(capsys.readouterr().out)
        assert "[dry-run] aws ecs register-task-definition --family" in out
        assert "[dry-run] aws ecs run-task (migrate command)" in out

    def test_no_ecs_calls_are_made(self):
        client = ScriptedClient()
        ctx = _ctx(client, config=_migration_config(), dry_run=True)
        start_migrations(ctx, {"web": "img:web"}, None)
        assert client.calls == []

    def test_dry_run_short_circuits_before_the_skip_check(self, migrations_repo):
        """The hash is never computed on a dry run, so SSM is never read."""
        client = ScriptedClient()
        ctx = _ctx(client, config=_migration_config(), dry_run=True)
        assert start_migrations(ctx, {"web": "img:web"}, migrations_repo) is None


class TestStartMigrationsTaskDefinition:
    """Pins the migrate task definition's shape."""

    def test_family_is_app_env_migrate(self):
        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        start_migrations(ctx, {"web": "img:web"}, None)
        registered = client.calls_to("register_task_definition")[0]
        assert registered["family"] == f"{APP_NAME}-{ENVIRONMENT}-migrate"

    def test_container_name_is_migrate(self):
        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        start_migrations(ctx, {"web": "img:web"}, None)
        registered = client.calls_to("register_task_definition")[0]
        assert registered["containerDefinitions"][0]["name"] == "migrate"

    def test_registration_is_announced(self, capsys):
        ctx = _ctx(_migration_ecs_client(), config=_migration_config())
        start_migrations(ctx, {"web": "img:web"}, None)
        assert "Registering migrate task definition..." in _plain(capsys.readouterr().out)


class TestStartMigrationsSkipHash:
    """MUST-PIN 7: the skip-hash paths at service.py:504-512."""

    @pytest.mark.usefixtures("mocked_aws")
    def test_matching_hash_skips_the_run_but_keeps_the_task_definition(
        self, migrations_repo, capsys
    ):
        from deployer.deploy.migrations import compute_migrations_hash

        store_migrations_hash(APP_NAME, ENVIRONMENT, compute_migrations_hash(migrations_repo))

        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        assert start_migrations(ctx, {"web": "img:web"}, migrations_repo) is None

        assert len(client.calls_to("register_task_definition")) == 1
        assert client.calls_to("run_task") == []
        assert "Migrations unchanged" in _plain(capsys.readouterr().out)

    @pytest.mark.usefixtures("mocked_aws")
    def test_no_stored_hash_runs_the_migration_and_carries_the_hash(self, migrations_repo):
        from deployer.deploy.migrations import compute_migrations_hash

        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        task = start_migrations(ctx, {"web": "img:web"}, migrations_repo)

        assert task is not None
        assert task.current_hash == compute_migrations_hash(migrations_repo)
        assert len(client.calls_to("run_task")) == 1

    @pytest.mark.usefixtures("mocked_aws")
    def test_changed_migrations_run(self, migrations_repo):
        store_migrations_hash(APP_NAME, ENVIRONMENT, "0000000000000000")

        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        task = start_migrations(ctx, {"web": "img:web"}, migrations_repo)

        assert task is not None
        assert task.current_hash != "0000000000000000"

    @pytest.mark.usefixtures("mocked_aws")
    def test_unhashable_source_dir_runs_with_a_none_hash(self, tmp_path):
        """No migration files anywhere: the hash is None and the migration runs."""
        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        task = start_migrations(ctx, {"web": "img:web"}, tmp_path)

        assert task is not None
        assert task.current_hash is None

    def test_source_dir_none_skips_the_check_entirely(self):
        """``if source_dir:`` is falsy, so no hashing and no SSM read happens."""
        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        task = start_migrations(ctx, {"web": "img:web"}, None)

        assert task is not None
        assert task.current_hash is None


class TestSourceDirTypes:
    """MUST-PIN 8: the ``str | None`` annotation vs the ``Path`` production passes.

    ``deployer.py:117`` builds ``(config_path.parent / application.source).resolve()``
    -- a ``Path`` -- and ``should_skip_migrations`` is annotated ``Path``. The
    ``str | None`` on ``start_migrations`` is simply wrong. It is also harmless:
    ``compute_migrations_hash`` normalises with ``Path(source_dir).resolve()``.
    Both are pinned so 53e-5c can correct the annotation without guessing.
    """

    @pytest.mark.usefixtures("mocked_aws")
    def test_path_and_str_produce_the_same_hash(self, migrations_repo):
        path_task = start_migrations(
            _ctx(_migration_ecs_client(), config=_migration_config()),
            {"web": "img:web"},
            migrations_repo,
        )
        str_task = start_migrations(
            _ctx(_migration_ecs_client(), config=_migration_config()),
            {"web": "img:web"},
            str(migrations_repo),
        )
        assert path_task is not None
        assert str_task is not None
        assert path_task.current_hash == str_task.current_hash

    @pytest.mark.usefixtures("mocked_aws")
    def test_empty_string_source_dir_is_falsy_and_skips_the_check(self):
        """``""`` takes the ``source_dir`` falsy branch, exactly like None."""
        task = start_migrations(
            _ctx(_migration_ecs_client(), config=_migration_config()), {"web": "img:web"}, ""
        )
        assert task is not None
        assert task.current_hash is None


class TestStartMigrationsNetworkConfig:
    """Pins where the migration task's network configuration comes from."""

    def test_taken_from_the_migration_service(self):
        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        start_migrations(ctx, {"web": "img:web"}, None)
        assert client.calls_to("describe_services") == [{"cluster": CLUSTER, "services": ["web"]}]

    def test_no_service_found_returns_none(self, capsys):
        client = _migration_ecs_client(describe_services={"services": []})
        ctx = _ctx(client, config=_migration_config())
        assert start_migrations(ctx, {"web": "img:web"}, None) is None
        out = _plain(capsys.readouterr().out)
        assert "No web service found to get network configuration" in out

    def test_client_error_returns_none(self, capsys):
        client = _migration_ecs_client(describe_services=_client_error())
        ctx = _ctx(client, config=_migration_config())
        assert start_migrations(ctx, {"web": "img:web"}, None) is None
        assert "Could not get network configuration" in _plain(capsys.readouterr().out)

    def test_service_without_network_configuration_raises_keyerror(self):
        """Latent bug pinned: the read is ``["networkConfiguration"]`` outside the try.

        A non-awsvpc service therefore crashes the deploy with a bare KeyError
        rather than the module's usual log-and-return-None.
        """
        client = _migration_ecs_client(describe_services=_describe_services())
        ctx = _ctx(client, config=_migration_config())
        with pytest.raises(KeyError, match="networkConfiguration"):
            start_migrations(ctx, {"web": "img:web"}, None)


class TestStartMigrationsRunTask:
    """Pins the run_task call and the MigrationTask it produces."""

    def test_run_task_arguments(self):
        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        start_migrations(ctx, {"web": "img:web"}, None)

        call = client.calls_to("run_task")[0]
        assert call["cluster"] == CLUSTER
        assert call["launchType"] == "FARGATE"
        assert call["taskDefinition"].endswith(f"{APP_NAME}-{ENVIRONMENT}-migrate:7")
        assert call["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == ["subnet-1"]

    def test_default_command(self):
        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config())
        start_migrations(ctx, {"web": "img:web"}, None)
        assert client.calls_to("run_task")[0]["overrides"] == {
            "containerOverrides": [
                {"name": "migrate", "command": ["python", "manage.py", "migrate"]}
            ]
        }

    def test_custom_command(self):
        client = _migration_ecs_client()
        ctx = _ctx(client, config=_migration_config(command=["alembic", "upgrade", "head"]))
        start_migrations(ctx, {"web": "img:web"}, None)
        assert client.calls_to("run_task")[0]["overrides"]["containerOverrides"][0]["command"] == [
            "alembic",
            "upgrade",
            "head",
        ]

    def test_returns_a_migration_task(self):
        ctx = _ctx(_migration_ecs_client(), config=_migration_config())
        task = start_migrations(ctx, {"web": "img:web"}, None)
        assert task == MigrationTask(
            task_arn=TASK_ARN,
            cluster_name=CLUSTER,
            current_hash=None,
            app_name=APP_NAME,
            environment=ENVIRONMENT,
        )

    def test_task_arn_is_printed(self, capsys):
        ctx = _ctx(_migration_ecs_client(), config=_migration_config())
        start_migrations(ctx, {"web": "img:web"}, None)
        assert f"Migration task started: {TASK_ARN}" in _plain(capsys.readouterr().out)

    def test_starting_migrations_is_announced(self, capsys):
        ctx = _ctx(_migration_ecs_client(), config=_migration_config())
        start_migrations(ctx, {"web": "img:web"}, None)
        assert "Starting migrations..." in _plain(capsys.readouterr().out)


# ===========================================================================
# wait_for_migrations
# ===========================================================================


def _migration_task(current_hash: str | None = None) -> MigrationTask:
    return MigrationTask(
        task_arn=TASK_ARN,
        cluster_name=CLUSTER,
        current_hash=current_hash,
        app_name=APP_NAME,
        environment=ENVIRONMENT,
    )


def _describe_tasks(exit_code: int | None = 0) -> dict:
    container: dict = {"name": "migrate"}
    if exit_code is not None:
        container["exitCode"] = exit_code
    return {"tasks": [{"containers": [container]}]}


class TestWaitForMigrations:
    """Pins the migration wait, its waiter use and its exit-code handling."""

    def test_none_task_returns_immediately(self, capsys):
        client = ScriptedClient()
        assert wait_for_migrations(client, None) is None
        assert client.calls == []
        assert capsys.readouterr().out == ""

    def test_uses_the_tasks_stopped_waiter(self):
        client = ScriptedClient(describe_tasks=_describe_tasks(0))
        wait_for_migrations(client, _migration_task())
        assert client.calls_to("get_waiter") == [{"name": "tasks_stopped"}]
        assert client.waiters[0].waits == [{"cluster": CLUSTER, "tasks": [TASK_ARN]}]

    def test_success_logs_done(self, capsys):
        client = ScriptedClient(describe_tasks=_describe_tasks(0))
        assert wait_for_migrations(client, _migration_task()) is None
        out = _plain(capsys.readouterr().out)
        assert "Waiting for migrations to complete..." in out
        assert "Migrations complete [done]" in out

    def test_no_hash_means_no_ssm_write(self):
        """``current_hash`` None skips ``store_migrations_hash`` (which would need AWS)."""
        client = ScriptedClient(describe_tasks=_describe_tasks(0))
        assert wait_for_migrations(client, _migration_task(None)) is None

    @pytest.mark.usefixtures("mocked_aws")
    def test_hash_is_stored_on_success(self):
        client = ScriptedClient(describe_tasks=_describe_tasks(0))
        wait_for_migrations(client, _migration_task("deadbeefdeadbeef"))

        ssm = boto3.client("ssm", region_name=REGION)
        stored = ssm.get_parameter(
            Name=f"/{APP_NAME}/{ENVIRONMENT}/last-migrations-hash", WithDecryption=True
        )
        assert stored["Parameter"]["Value"] == "deadbeefdeadbeef"

    @pytest.mark.usefixtures("mocked_aws")
    def test_hash_is_not_stored_on_failure(self, aws_cli):
        client = ScriptedClient(describe_tasks=_describe_tasks(1))
        with pytest.raises(RuntimeError):
            wait_for_migrations(client, _migration_task("deadbeefdeadbeef"))

        ssm = boto3.client("ssm", region_name=REGION)
        with pytest.raises(ClientError):
            ssm.get_parameter(Name=f"/{APP_NAME}/{ENVIRONMENT}/last-migrations-hash")

    def test_nonzero_exit_code_raises(self, aws_cli, capsys):
        client = ScriptedClient(describe_tasks=_describe_tasks(3))
        with pytest.raises(RuntimeError) as exc_info:
            wait_for_migrations(client, _migration_task())
        assert str(exc_info.value) == "Migration failed with exit code 3"
        assert "Migration failed with exit code 3" in _plain(capsys.readouterr().out)

    def test_missing_exit_code_is_treated_as_failure(self, aws_cli):
        """Latent behaviour pinned: ``.get("exitCode", 1)`` defaults to *failure*.

        A task that stopped before its container reported an exit code (a
        capacity or pull failure) therefore raises rather than passing silently.
        """
        client = ScriptedClient(describe_tasks=_describe_tasks(None))
        with pytest.raises(RuntimeError, match="exit code 1"):
            wait_for_migrations(client, _migration_task())

    def test_failure_fetches_the_logs(self, aws_cli):
        client = ScriptedClient(describe_tasks=_describe_tasks(2))
        with pytest.raises(RuntimeError):
            wait_for_migrations(client, _migration_task())
        assert any("get-log-events" in call for call in aws_cli.calls)

    def test_only_the_first_container_is_inspected(self, aws_cli):
        """The exit code is read from ``containers[0]``, whatever else ran."""
        client = ScriptedClient(
            describe_tasks={"tasks": [{"containers": [{"exitCode": 0}, {"exitCode": 9}]}]}
        )
        assert wait_for_migrations(client, _migration_task()) is None

    def test_describe_tasks_arguments(self):
        client = ScriptedClient(describe_tasks=_describe_tasks(0))
        wait_for_migrations(client, _migration_task())
        assert client.calls_to("describe_tasks") == [{"cluster": CLUSTER, "tasks": [TASK_ARN]}]


# ===========================================================================
# _display_migration_logs
# ===========================================================================


class TestDisplayMigrationLogs:
    """Pins the CloudWatch fetch, which is best-effort by design."""

    def test_log_group_and_stream_are_ecs_convention(self, aws_cli):
        aws_cli.replies((True, '{"events": []}'))
        _display_migration_logs(_migration_task())

        argv = aws_cli.argv
        assert "--log-group-name" in argv
        assert argv[argv.index("--log-group-name") + 1] == f"/ecs/{APP_NAME}-{ENVIRONMENT}"
        assert argv[argv.index("--log-stream-name") + 1] == "migrate/migrate/abc123def456"

    def test_task_id_is_the_last_arn_segment(self, aws_cli):
        """The ARN is split on "/" and only the final segment is used."""
        aws_cli.replies((True, '{"events": []}'))
        task = MigrationTask(
            task_arn=f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task/{CLUSTER}/nested/deadbeef",
            cluster_name=CLUSTER,
            current_hash=None,
            app_name=APP_NAME,
            environment=ENVIRONMENT,
        )
        _display_migration_logs(task)

        argv = aws_cli.argv
        assert argv[argv.index("--log-stream-name") + 1] == "migrate/migrate/deadbeef"

    def test_arn_without_slashes_is_used_whole(self, aws_cli):
        """``split("/")[-1]`` on a slash-free ARN yields the ARN itself."""
        aws_cli.replies((True, '{"events": []}'))
        task = MigrationTask(
            task_arn="bare-task-id",
            cluster_name=CLUSTER,
            current_hash=None,
            app_name=APP_NAME,
            environment=ENVIRONMENT,
        )
        _display_migration_logs(task)

        argv = aws_cli.argv
        assert argv[argv.index("--log-stream-name") + 1] == "migrate/migrate/bare-task-id"

    def test_default_limit_is_fifty(self, aws_cli):
        aws_cli.replies((True, '{"events": []}'))
        _display_migration_logs(_migration_task())
        argv = aws_cli.argv
        assert argv[argv.index("--limit") + 1] == "50"

    def test_custom_limit_is_passed_and_echoed(self, aws_cli, capsys):
        aws_cli.replies((True, '{"events": [{"message": "line"}]}'))
        _display_migration_logs(_migration_task(), limit=5)
        argv = aws_cli.argv
        assert argv[argv.index("--limit") + 1] == "5"
        assert "--- Migration Logs (last 5 lines) ---" in _plain(capsys.readouterr().out)

    def test_events_are_printed_between_markers(self, aws_cli, capsys):
        aws_cli.replies(
            (True, '{"events": [{"message": "applying 0001\\n"}, {"message": "  OK  "}]}')
        )
        _display_migration_logs(_migration_task())

        lines = _lines(capsys.readouterr().out)
        assert "  --- Migration Logs (last 50 lines) ---" in lines
        assert "  applying 0001" in lines
        assert "    OK" in lines
        assert "  --- End of Logs ---" in lines

    def test_missing_message_key_prints_a_blank_line(self, aws_cli, capsys):
        aws_cli.replies((True, '{"events": [{"timestamp": 1}]}'))
        _display_migration_logs(_migration_task())
        assert "--- End of Logs ---" in _plain(capsys.readouterr().out)

    def test_no_events_warns_with_the_stream_name(self, aws_cli, capsys):
        aws_cli.replies((True, '{"events": []}'))
        _display_migration_logs(_migration_task())

        out = _plain(capsys.readouterr().out)
        assert f"No logs found. Check CloudWatch log group: /ecs/{APP_NAME}-{ENVIRONMENT}" in out
        assert "Stream: migrate/migrate/abc123def456" in out

    def test_cli_failure_takes_the_same_no_logs_branch(self, aws_cli, capsys):
        """``get_task_logs`` returns None on failure, which is falsy like ``[]``."""
        aws_cli.replies((False, "AccessDeniedException"))
        _display_migration_logs(_migration_task())
        assert "No logs found." in _plain(capsys.readouterr().out)

    def test_unparseable_output_takes_the_same_branch(self, aws_cli, capsys):
        aws_cli.replies((True, "not json"))
        _display_migration_logs(_migration_task())
        assert "No logs found." in _plain(capsys.readouterr().out)

    def test_unexpected_exception_is_swallowed(self, monkeypatch, capsys):
        """The blanket ``except Exception`` keeps a log-fetch failure non-fatal."""
        from deployer.aws import cli as aws_cli_module

        def _boom(cmd, cwd=None):
            raise OSError("no aws binary")

        monkeypatch.setattr(aws_cli_module, "run_command", _boom)
        assert _display_migration_logs(_migration_task()) is None

        out = _plain(capsys.readouterr().out)
        assert "Could not fetch logs: no aws binary" in out
        assert f"Check CloudWatch manually: /ecs/{APP_NAME}-{ENVIRONMENT}" in out

    def test_fetching_is_announced(self, aws_cli, capsys):
        aws_cli.replies((True, '{"events": []}'))
        _display_migration_logs(_migration_task())
        assert "Fetching migration logs..." in _plain(capsys.readouterr().out)
