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

    def test_undeployed_environment_returns_1(self, monkeypatch):
        monkeypatch.setattr(ecs_run, "resolve_environment", lambda _e: None)
        assert ecs_run.cmd_list("myapp-staging") == 1
