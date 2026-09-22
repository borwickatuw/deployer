"""Tests for bin/ecs-run.py — running commands in ECS containers."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("ecs_run", bin_dir / "ecs-run.py")
ecs_run = module_from_spec(_spec)
_spec.loader.exec_module(ecs_run)


def _resolves_to(monkeypatch, path):
    """Make the shared deploy.toml resolution return `path` without touching disk."""
    monkeypatch.setattr(ecs_run, "resolve_deploy_toml_or_exit", lambda *_a, **_kw: path)


def _loads(monkeypatch, deploy_toml):
    """Make load_deploy_toml() return the given parsed dict."""
    monkeypatch.setattr(ecs_run, "load_deploy_toml", lambda _path: deploy_toml)


def _run(environment=None, command_name=None, **overrides):
    """Call cmd_run() with the boilerplate arguments filled in."""
    kwargs = {
        "deploy_toml": None,
        "list_commands": False,
        "extra_args": (),
        "service": "web",
        "container": None,
        "no_wait": False,
        "no_logs": False,
        "timeout": 300,
    }
    kwargs.update(overrides)
    return ecs_run.cmd_run(environment, command_name, **kwargs)


class TestPrintAvailableCommands:
    """Tests for _print_available_commands()."""

    def test_lists_each_command(self, capsys):
        assert (
            ecs_run._print_available_commands(
                {"commands": {"migrate": ["python", "manage.py", "migrate"]}}
            )
            == 0
        )
        assert "  migrate: python manage.py migrate" in capsys.readouterr().out

    def test_string_command_is_printed_verbatim(self, capsys):
        assert ecs_run._print_available_commands({"commands": {"shell": "bash"}}) == 0
        assert "  shell: bash" in capsys.readouterr().out

    def test_empty_section_returns_1(self, capsys):
        assert ecs_run._print_available_commands({"commands": {}}) == 1
        assert "No commands defined" in capsys.readouterr().err

    def test_absent_section_returns_1(self, capsys):
        assert ecs_run._print_available_commands({}) == 1
        assert "No commands defined" in capsys.readouterr().err


class TestResolveEnvironment:
    """Tests for resolve_environment()."""

    def test_returns_path_and_cluster(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ecs_run, "validate_environment_deployed", lambda _e: (tmp_path, None))
        monkeypatch.setattr(
            ecs_run,
            "load_environment_config",
            lambda _p: {"infrastructure": {"cluster_name": "myapp-staging-cluster"}},
        )

        assert ecs_run.resolve_environment("myapp-staging") == (
            tmp_path,
            "myapp-staging-cluster",
        )

    def test_undeployed_environment_returns_none(self, monkeypatch, capsys):
        monkeypatch.setattr(
            ecs_run, "validate_environment_deployed", lambda _e: (None, "not deployed")
        )
        assert ecs_run.resolve_environment("myapp-staging") is None
        assert "not deployed" in capsys.readouterr().err

    def test_unloadable_config_returns_none(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(ecs_run, "validate_environment_deployed", lambda _e: (tmp_path, None))

        def boom(_path):
            raise RuntimeError("tofu output failed")

        monkeypatch.setattr(ecs_run, "load_environment_config", boom)

        assert ecs_run.resolve_environment("myapp-staging") is None
        assert "tofu output failed" in capsys.readouterr().err

    def test_missing_cluster_name_returns_none(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(ecs_run, "validate_environment_deployed", lambda _e: (tmp_path, None))
        monkeypatch.setattr(ecs_run, "load_environment_config", lambda _p: {})

        assert ecs_run.resolve_environment("myapp-staging") is None
        assert "Could not get ECS cluster name" in capsys.readouterr().err

    def test_neither_path_nor_reason_is_reported_as_a_contract_violation(self, monkeypatch, capsys):
        """(None, None) is not "deployed": say so rather than pass None onward.

        validate_environment_deployed promises (path, None) or (None, reason).
        Guarding on the reason alone let a (None, None) answer through to
        load_environment_config(None). Guarding on the path too names the bug.
        """
        monkeypatch.setattr(ecs_run, "validate_environment_deployed", lambda _e: (None, None))

        assert ecs_run.resolve_environment("myapp-staging") is None
        assert "neither a path nor a reason" in capsys.readouterr().err


class TestCmdRunArgumentGuards:
    """Tests for cmd_run()'s pre-resolution and post-resolution usage errors."""

    def test_no_environment_and_no_flag_is_a_usage_error(self, capsys):
        assert _run(None, None) == 1
        err = capsys.readouterr().err
        assert "environment is required" in err
        assert "Usage: ecs-run.py run" in err

    def test_list_commands_without_environment_or_flag_names_deploy_toml(self, capsys):
        assert _run(None, None, list_commands=True) == 1
        assert "--deploy-toml is required" in capsys.readouterr().err

    def test_list_commands_in_extra_args_is_honoured(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")
        _loads(monkeypatch, {"commands": {"migrate": ["migrate"]}})

        assert _run("myapp-staging", None, extra_args=("--list-commands",)) == 0
        assert "Available commands:" in capsys.readouterr().out

    def test_missing_command_name_returns_1(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")
        _loads(monkeypatch, {"commands": {"migrate": ["migrate"]}})

        assert _run("myapp-staging", None) == 1
        assert "command_name is required" in capsys.readouterr().err

    def test_unreadable_deploy_toml_returns_1(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")

        def boom(_path):
            raise FileNotFoundError("deploy.toml vanished")

        monkeypatch.setattr(ecs_run, "load_deploy_toml", boom)

        assert _run("myapp-staging", "migrate") == 1
        assert "deploy.toml vanished" in capsys.readouterr().err


class TestCmdRunHappyPath:
    """Tests for cmd_run()'s dispatch into _run_ecs_command()."""

    def _stub_pipeline(self, monkeypatch, tmp_path, deploy_toml):
        calls = {}
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")
        _loads(monkeypatch, deploy_toml)
        monkeypatch.setattr(
            ecs_run, "resolve_environment", lambda _e: (tmp_path, "myapp-staging-cluster")
        )
        monkeypatch.setattr(
            ecs_run,
            "_run_ecs_command",
            lambda **kwargs: (calls.update(kwargs), 0)[1],
        )
        return calls

    def test_forwards_the_resolved_command_and_cluster(self, monkeypatch, tmp_path):
        calls = self._stub_pipeline(
            monkeypatch, tmp_path, {"commands": {"migrate": ["python", "manage.py", "migrate"]}}
        )

        assert _run("myapp-staging", "migrate") == 0
        assert calls["cluster_name"] == "myapp-staging-cluster"
        assert calls["command"] == ["python", "manage.py", "migrate"]
        assert calls["use_migrate_credentials"] is False

    def test_extra_args_are_appended(self, monkeypatch, tmp_path):
        calls = self._stub_pipeline(
            monkeypatch, tmp_path, {"commands": {"migrate": ["manage.py", "migrate"]}}
        )

        assert _run("myapp-staging", "migrate", extra_args=("--noinput",)) == 0
        assert calls["command"] == ["manage.py", "migrate", "--noinput"]

    def test_ddl_commands_request_migrate_credentials(self, monkeypatch, tmp_path):
        calls = self._stub_pipeline(
            monkeypatch,
            tmp_path,
            {"commands": {"migrate": ["manage.py", "migrate"]}, "migrations": {"enabled": True}},
        )
        monkeypatch.setattr(ecs_run, "command_requires_ddl", lambda _dt, _name: True)

        assert _run("myapp-staging", "migrate") == 0
        assert calls["use_migrate_credentials"] is True

    def test_flags_map_onto_wait_and_logs(self, monkeypatch, tmp_path):
        calls = self._stub_pipeline(
            monkeypatch, tmp_path, {"commands": {"migrate": ["manage.py", "migrate"]}}
        )

        assert _run("myapp-staging", "migrate", no_wait=True, no_logs=True, timeout=60) == 0
        assert calls["wait"] is False
        assert calls["show_logs"] is False
        assert calls["timeout"] == 60

    def test_unknown_command_exits_1(self, monkeypatch, tmp_path, capsys):
        self._stub_pipeline(monkeypatch, tmp_path, {"commands": {"migrate": ["migrate"]}})

        with pytest.raises(SystemExit) as exc_info:
            _run("myapp-staging", "nosuchcommand")

        assert exc_info.value.code == 1
        assert capsys.readouterr().err != ""

    def test_undeployed_environment_returns_1(self, monkeypatch, tmp_path):
        self._stub_pipeline(monkeypatch, tmp_path, {"commands": {"migrate": ["migrate"]}})
        monkeypatch.setattr(ecs_run, "resolve_environment", lambda _e: None)

        assert _run("myapp-staging", "migrate") == 1


class TestCmdExec:
    """Tests for cmd_exec()."""

    def test_forwards_the_literal_command(self, monkeypatch, tmp_path):
        calls = {}
        monkeypatch.setattr(
            ecs_run, "resolve_environment", lambda _e: (tmp_path, "myapp-staging-cluster")
        )
        monkeypatch.setattr(
            ecs_run, "_run_ecs_command", lambda **kwargs: (calls.update(kwargs), 0)[1]
        )

        assert (
            ecs_run.cmd_exec("myapp-staging", ("echo", "hi"), "web", None, False, False, 300) == 0
        )
        assert calls["command"] == ["echo", "hi"]

    def test_no_command_returns_1(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(
            ecs_run, "resolve_environment", lambda _e: (tmp_path, "myapp-staging-cluster")
        )

        assert ecs_run.cmd_exec("myapp-staging", (), "web", None, False, False, 300) == 1
        assert "No command specified" in capsys.readouterr().err

    def test_undeployed_environment_returns_1(self, monkeypatch):
        monkeypatch.setattr(ecs_run, "resolve_environment", lambda _e: None)
        assert ecs_run.cmd_exec("myapp-staging", ("echo",), "web", None, False, False, 300) == 1


class TestCmdList:
    """Tests for cmd_list()."""

    def test_no_services_returns_0(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(
            ecs_run, "resolve_environment", lambda _e: (tmp_path, "myapp-staging-cluster")
        )
        monkeypatch.setattr(ecs_run.ecs, "get_services", lambda _cluster: [])

        assert ecs_run.cmd_list("myapp-staging") == 0
        assert "No services found" in capsys.readouterr().out

    def test_renders_each_service(self, monkeypatch, tmp_path, capsys):
        class _Service:
            name = "web"
            status = "ACTIVE"
            running_count = 2
            desired_count = 2
            task_definition = "myapp-web:7"

        monkeypatch.setattr(
            ecs_run, "resolve_environment", lambda _e: (tmp_path, "myapp-staging-cluster")
        )
        monkeypatch.setattr(ecs_run.ecs, "get_services", lambda _cluster: [_Service()])
        monkeypatch.setattr(
            ecs_run.ecs, "get_task_containers", lambda _td: [{"name": "web"}, {"name": "nginx"}]
        )

        assert ecs_run.cmd_list("myapp-staging") == 0
        out = capsys.readouterr().out
        assert "[+] web" in out
        assert "Running: 2/2" in out
        assert "Containers: web, nginx" in out

    def test_an_unreadable_task_definition_reports_itself_in_place(
        self, monkeypatch, tmp_path, capsys
    ):
        """The listing keeps rendering, and never reports a failed read as "no containers"."""

        class _Service:
            name = "web"
            status = "ACTIVE"
            running_count = 2
            desired_count = 2
            task_definition = "myapp-web:7"

        def boom(_td):
            raise RuntimeError("AccessDenied")

        monkeypatch.setattr(
            ecs_run, "resolve_environment", lambda _e: (tmp_path, "myapp-staging-cluster")
        )
        monkeypatch.setattr(ecs_run.ecs, "get_services", lambda _cluster: [_Service()])
        monkeypatch.setattr(ecs_run.ecs, "get_task_containers", boom)

        assert ecs_run.cmd_list("myapp-staging") == 0
        out = capsys.readouterr().out
        assert "[+] web" in out
        assert "Containers: unable to read (AccessDenied)" in out

    def test_undeployed_environment_returns_1(self, monkeypatch):
        monkeypatch.setattr(ecs_run, "resolve_environment", lambda _e: None)
        assert ecs_run.cmd_list("myapp-staging") == 1

    def test_an_unlistable_cluster_returns_1_not_no_services(self, monkeypatch, tmp_path, capsys):
        """A missing cluster used to read as "No services found in cluster." and exit 0."""

        def unlistable(_cluster):
            raise RuntimeError("ClusterNotFoundException")

        monkeypatch.setattr(
            ecs_run, "resolve_environment", lambda _e: (tmp_path, "myapp-staging-cluster")
        )
        monkeypatch.setattr(ecs_run.ecs, "get_services", unlistable)

        assert ecs_run.cmd_list("myapp-staging") == 1
        captured = capsys.readouterr()
        assert "Error: unable to list services: ClusterNotFoundException" in captured.err
        assert "No services found" not in captured.out


class TestRunEcsCommandContainerResolution:
    """Tests for _run_ecs_command()'s choice of container.

    The whole body was previously unreachable from tests -- every caller test
    stubs _run_ecs_command out -- so the container-name resolution had no pin
    at all.
    """

    def _stub_aws(self, monkeypatch, containers, *, run_task_arn="arn:aws:ecs:::task/c/abc123"):
        """Stub every AWS touch _run_ecs_command makes; return the recorded calls."""
        calls: dict = {}

        monkeypatch.setattr(ecs_run.boto3, "client", lambda _svc: object())
        monkeypatch.setattr(
            ecs_run.ecs,
            "get_service_info",
            lambda *_a, **_kw: ({"subnets": []}, "myapp-web:7"),
        )
        monkeypatch.setattr(ecs_run.ecs, "get_task_containers", lambda *_a, **_kw: containers)

        def _run_task(**kwargs):
            calls["run_task"] = kwargs
            return run_task_arn

        monkeypatch.setattr(ecs_run.ecs, "run_task", _run_task)

        def _logs_location(_containers, container_name):
            calls["logs_container"] = container_name
            return None

        monkeypatch.setattr(ecs_run.ecs, "get_logs_location_from_containers", _logs_location)
        monkeypatch.setattr(ecs_run.ecs, "wait_for_task", lambda *_a, **_kw: 0)
        return calls

    def _invoke(self, container_name):
        return ecs_run._run_ecs_command(
            cluster_name="myapp-staging-cluster",
            service_name="web",
            container_name=container_name,
            command=["echo", "hi"],
        )

    def test_an_explicit_container_name_is_used_as_given(self, monkeypatch):
        calls = self._stub_aws(monkeypatch, [{"name": "web"}, {"name": "sidecar"}])

        assert self._invoke("sidecar") == 0
        assert calls["run_task"]["container_name"] == "sidecar"
        assert calls["logs_container"] == "sidecar"

    def test_no_container_name_takes_the_first_from_the_task_definition(self, monkeypatch):
        calls = self._stub_aws(monkeypatch, [{"name": "web"}, {"name": "sidecar"}])

        assert self._invoke(None) == 0
        assert calls["run_task"]["container_name"] == "web"
        assert calls["logs_container"] == "web"

    def test_no_container_name_and_no_containers_is_an_error(self, monkeypatch, capsys):
        calls = self._stub_aws(monkeypatch, [])

        assert self._invoke(None) == 1
        assert "run_task" not in calls
        assert "No containers found in task definition" in capsys.readouterr().err


class TestMigrateTaskDefinition:
    """Tests for _migrate_task_definition() -- the DDL task definition derivation.

    This is the one piece of _run_ecs_command() that computed a name rather
    than reading one: a wrong answer here points a migration at a task
    definition that does not exist, or (worse) at one with the wrong
    credentials. It is pure, so it is pinned directly.
    """

    def test_family_and_revision_drops_the_service_suffix(self):
        assert ecs_run._migrate_task_definition("myapp-staging-web:7") == "myapp-staging-migrate"

    def test_full_arn_is_reduced_to_the_family(self):
        arn = "arn:aws:ecs:us-west-2:123456789012:task-definition/myapp-staging-web:12"
        assert ecs_run._migrate_task_definition(arn) == "myapp-staging-migrate"

    def test_the_result_is_unversioned_so_ecs_picks_the_latest_revision(self):
        assert ":" not in ecs_run._migrate_task_definition("myapp-staging-celery:3")

    def test_a_bare_family_needs_no_revision(self):
        assert ecs_run._migrate_task_definition("myapp-staging-web") == "myapp-staging-migrate"


class TestPlanTaskLaunchMigrateCredentials:
    """_plan_task_launch() sends DDL commands to the migrate task definition."""

    def _plan(self, monkeypatch, *, use_migrate_credentials, container_override=None):
        seen: dict = {}

        monkeypatch.setattr(
            ecs_run.ecs,
            "get_service_info",
            lambda *_a, **_kw: ({"subnets": []}, "myapp-staging-web:7"),
        )

        def _containers(task_definition, **_kw):
            seen["task_definition"] = task_definition
            return [{"name": "web"}, {"name": "sidecar"}]

        monkeypatch.setattr(ecs_run.ecs, "get_task_containers", _containers)

        plan = ecs_run._plan_task_launch(
            "myapp-staging-cluster",
            "web",
            container_override,
            use_migrate_credentials=use_migrate_credentials,
            ecs_client=object(),
        )
        return plan, seen

    def test_ddl_runs_the_migrate_task_definition_and_container(self, monkeypatch):
        plan, seen = self._plan(monkeypatch, use_migrate_credentials=True)

        assert plan.task_definition == "myapp-staging-migrate"
        assert plan.container_name == "migrate"
        assert seen["task_definition"] == "myapp-staging-migrate"

    def test_a_container_override_does_not_escape_the_migrate_container(self, monkeypatch):
        plan, _ = self._plan(
            monkeypatch, use_migrate_credentials=True, container_override="sidecar"
        )

        assert plan.container_name == "migrate"

    def test_without_ddl_the_service_task_definition_is_used_as_reported(self, monkeypatch):
        plan, seen = self._plan(monkeypatch, use_migrate_credentials=False)

        assert plan.task_definition == "myapp-staging-web:7"
        assert plan.container_name == "web"
        assert seen["task_definition"] == "myapp-staging-web:7"

    def test_an_unusable_service_reports_the_reason_and_returns_none(self, monkeypatch, capsys):
        monkeypatch.setattr(ecs_run.ecs, "get_service_info", lambda *_a, **_kw: (None, None))

        assert (
            ecs_run._plan_task_launch(
                "myapp-staging-cluster",
                "web",
                None,
                use_migrate_credentials=False,
                ecs_client=object(),
            )
            is None
        )
        assert "Could not get network config" in capsys.readouterr().err


class TestAwaitTask:
    """_await_task() reports the outcome and only tails logs when asked."""

    def _await(self, monkeypatch, exit_code, logs_info):
        tailed: list = []

        monkeypatch.setattr(ecs_run.ecs, "wait_for_task", lambda *_a, **_kw: exit_code)
        monkeypatch.setattr(ecs_run, "_display_task_logs", lambda *args: tailed.append(args))

        returned = ecs_run._await_task(
            cluster_name="myapp-staging-cluster",
            task_arn="arn:aws:ecs:::task/c/abc123",
            task_id="abc123",
            container_name="web",
            logs_info=logs_info,
            timeout=300,
            ecs_client=object(),
        )
        return returned, tailed

    def test_success_returns_zero_and_tails_the_log_stream(self, monkeypatch, capsys):
        returned, tailed = self._await(monkeypatch, 0, ("/ecs/myapp", "myapp"))

        assert returned == 0
        assert tailed == [("/ecs/myapp", "myapp", "web", "abc123")]
        assert "Task completed successfully" in capsys.readouterr().out

    def test_a_timeout_is_reported_as_failure_and_keeps_its_sentinel(self, monkeypatch, capsys):
        returned, _ = self._await(monkeypatch, -1, None)

        assert returned == -1
        assert "Task failed or timed out" in capsys.readouterr().err

    def test_a_nonzero_exit_code_is_returned_verbatim(self, monkeypatch, capsys):
        returned, _ = self._await(monkeypatch, 3, None)

        assert returned == 3
        assert "Task exited with code 3" in capsys.readouterr().err

    def test_no_logs_info_means_no_log_tail(self, monkeypatch):
        _, tailed = self._await(monkeypatch, 0, None)

        assert tailed == []
