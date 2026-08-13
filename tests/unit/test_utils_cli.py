"""Tests for deployer.utils.cli — the shared bin/ helper surface."""

from pathlib import Path

import pytest

from deployer.utils import cli as cli_utils
from deployer.utils.cli import (
    EnvironmentConfigError,
    configure_aws_for_operation,
    configure_profile_or_exit,
    confirm_action,
    exit_on,
    iter_deployed_environments,
    load_environment_infrastructure,
    prompt_or_exit,
    require_environment,
    require_validated_environment,
    resolve_deploy_toml_or_exit,
    validate_and_configure,
)


def _answer(monkeypatch, *responses):
    """Feed the given responses to input(); an exception type is raised instead."""
    queue = list(responses)

    def fake_input(prompt=""):
        value = queue.pop(0)
        if isinstance(value, type) and issubclass(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", fake_input)


class TestPromptOrExit:
    """Tests for prompt_or_exit()."""

    def test_returns_stripped_value(self, monkeypatch):
        _answer(monkeypatch, "  myapp-staging  ")
        assert prompt_or_exit("Name: ") == "myapp-staging"

    def test_empty_input_is_not_a_cancellation(self, monkeypatch):
        _answer(monkeypatch, "")
        assert prompt_or_exit("Name: ") == ""

    @pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
    def test_cancellation_exits_1(self, monkeypatch, capsys, interrupt):
        _answer(monkeypatch, interrupt)
        with pytest.raises(SystemExit) as exc_info:
            prompt_or_exit("Name: ")
        assert exc_info.value.code == 1
        assert "Cancelled" in capsys.readouterr().out


class TestConfirmAction:
    """Tests for confirm_action()."""

    def test_skip_bypasses_prompt(self):
        assert confirm_action(skip=True) is True

    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
    def test_accepts_yes(self, monkeypatch, answer):
        _answer(monkeypatch, answer)
        assert confirm_action() is True

    @pytest.mark.parametrize("answer", ["", "n", "no", "maybe"])
    def test_rejects_anything_else(self, monkeypatch, answer, capsys):
        _answer(monkeypatch, answer)
        assert confirm_action() is False
        assert "Cancelled" in capsys.readouterr().out

    def test_eof_exits_1(self, monkeypatch, capsys):
        """EOF/Ctrl-C exits rather than returning False; callers turned False into exit(1)."""
        _answer(monkeypatch, EOFError)
        with pytest.raises(SystemExit) as exc_info:
            confirm_action()
        assert exc_info.value.code == 1
        assert "Cancelled" in capsys.readouterr().out


class TestExitOn:
    """Tests for the exit_on() context manager."""

    def test_passes_through_when_nothing_raises(self):
        with exit_on(ValueError):
            result = 1 + 1
        assert result == 2

    def test_converts_listed_exception_to_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info, exit_on(ValueError):
            raise ValueError("bad template")
        assert exc_info.value.code == 1
        assert "bad template" in capsys.readouterr().err

    def test_prefix_is_prepended(self, capsys):
        with pytest.raises(SystemExit), exit_on(ValueError, prefix="parsing deploy.toml: "):
            raise ValueError("boom")
        assert "parsing deploy.toml: boom" in capsys.readouterr().err

    def test_unlisted_exception_propagates(self):
        with pytest.raises(KeyError), exit_on(ValueError):
            raise KeyError("untouched")


class TestResolveDeployTomlOrExit:
    """Tests for resolve_deploy_toml_or_exit()."""

    HINTS = {
        "specify_hint": "ssm-secrets.py check myapp-staging --deploy-toml /path/to/deploy.toml",
        "link_benefit": "check with just: ssm-secrets.py check myapp-staging",
    }

    def _resolve(self, environment, deploy_toml):
        return resolve_deploy_toml_or_exit(environment, deploy_toml, **self.HINTS)

    def test_explicit_flag_wins_over_the_link_registry(self, tmp_path, monkeypatch):
        explicit = tmp_path / "explicit.toml"
        explicit.write_text("")
        linked = tmp_path / "linked.toml"
        linked.write_text("")
        monkeypatch.setattr(cli_utils, "get_linked_deploy_toml", lambda _env: linked)

        assert self._resolve("myapp-staging", str(explicit)) == explicit.resolve()

    def test_explicit_flag_prints_the_link_tip(self, tmp_path, capsys):
        explicit = tmp_path / "deploy.toml"
        explicit.write_text("")

        self._resolve("myapp-staging", str(explicit))

        out = capsys.readouterr().out
        assert "link-environments.py myapp-staging" in out
        assert "check with just: ssm-secrets.py check myapp-staging" in out

    def test_explicit_flag_expands_user_and_resolves(self, tmp_path, monkeypatch):
        explicit = tmp_path / "deploy.toml"
        explicit.write_text("")
        monkeypatch.setenv("HOME", str(tmp_path))

        assert self._resolve("myapp-staging", "~/deploy.toml") == explicit.resolve()

    def test_falls_back_to_the_link_registry(self, tmp_path, monkeypatch, capsys):
        linked = tmp_path / "linked.toml"
        linked.write_text("")
        monkeypatch.setattr(cli_utils, "get_linked_deploy_toml", lambda _env: linked)

        assert self._resolve("myapp-staging", None) == linked

        out = capsys.readouterr().out
        assert f"Using linked deploy.toml: {linked}" in out
        assert "Tip:" not in out

    def test_no_link_exits_1_with_stderr_guidance(self, monkeypatch, capsys):
        monkeypatch.setattr(cli_utils, "get_linked_deploy_toml", lambda _env: None)

        with pytest.raises(SystemExit) as exc_info:
            self._resolve("myapp-staging", None)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "No deploy.toml linked for 'myapp-staging'" in captured.err
        assert "link-environments.py myapp-staging /path/to/deploy.toml" in captured.err
        assert f"Or specify: {self.HINTS['specify_hint']}" in captured.err

    def test_missing_path_exits_1(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            self._resolve("myapp-staging", str(tmp_path / "absent.toml"))
        assert exc_info.value.code == 1
        assert "Config file not found" in capsys.readouterr().err

    def test_directory_exits_1(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            self._resolve("myapp-staging", str(tmp_path))
        assert exc_info.value.code == 1
        assert "is a directory" in capsys.readouterr().err

    def test_wrong_suffix_exits_1(self, tmp_path, capsys):
        wrong = tmp_path / "deploy.yaml"
        wrong.write_text("")
        with pytest.raises(SystemExit) as exc_info:
            self._resolve("myapp-staging", str(wrong))
        assert exc_info.value.code == 1
        assert "must be a .toml file" in capsys.readouterr().err

    def test_linked_path_is_validated_too(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            cli_utils, "get_linked_deploy_toml", lambda _env: tmp_path / "vanished.toml"
        )
        with pytest.raises(SystemExit) as exc_info:
            self._resolve("myapp-staging", None)
        assert exc_info.value.code == 1
        assert "Config file not found" in capsys.readouterr().err


class TestConfigureProfileOrExit:
    """Tests for configure_profile_or_exit()."""

    def test_forwards_operation_and_validates(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cli_utils,
            "configure_aws_profile_for_environment",
            lambda op, env, validate=False: calls.append((op, env, validate)),
        )
        configure_profile_or_exit("deploy", "myapp-staging")
        assert calls == [("deploy", "myapp-staging", True)]

    def test_runtime_error_exits_1(self, monkeypatch, capsys):
        def boom(op, env, validate=False):
            raise RuntimeError("profile 'deployer-app' not found")

        monkeypatch.setattr(cli_utils, "configure_aws_profile_for_environment", boom)
        with pytest.raises(SystemExit) as exc_info:
            configure_profile_or_exit("deploy", "myapp-staging")
        assert exc_info.value.code == 1
        assert "not found" in capsys.readouterr().out


class TestConfigureAwsForOperation:
    """Tests for configure_aws_for_operation()."""

    def test_with_environment_uses_environment_profile(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cli_utils,
            "configure_aws_profile_for_environment",
            lambda op, env: calls.append((op, env)),
        )
        configure_aws_for_operation("cognito", "myapp-staging")
        assert calls == [("cognito", "myapp-staging")]

    def test_without_environment_uses_operation_default(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cli_utils, "configure_aws_profile", calls.append)
        configure_aws_for_operation("secrets", None)
        assert calls == ["secrets"]


class TestValidateAndConfigure:
    """Tests for validate_and_configure()."""

    def test_configures_infra_profile_when_deployed(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cli_utils, "validate_environment_deployed", lambda env: (Path("/envs") / env, None)
        )
        monkeypatch.setattr(
            cli_utils,
            "configure_aws_profile_for_environment",
            lambda op, env: calls.append((op, env)),
        )
        validate_and_configure("myapp-production")
        assert calls == [("infra", "myapp-production")]

    def test_exits_when_not_deployed(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli_utils, "validate_environment_deployed", lambda _env: (None, "Not deployed")
        )
        with pytest.raises(SystemExit) as exc_info:
            validate_and_configure("myapp-production")
        assert exc_info.value.code == 1
        assert "Not deployed" in capsys.readouterr().out


def _stub_config(monkeypatch, tmp_path, config):
    """Point load_environment_infrastructure at an in-memory config."""
    monkeypatch.setattr(cli_utils, "get_environment_path", lambda env: tmp_path / env)
    monkeypatch.setattr(
        "deployer.core.config.load_environment_config",
        lambda _env_path: config,
    )


class TestLoadEnvironmentInfrastructure:
    """Tests for load_environment_infrastructure()."""

    def test_reads_cluster_and_rds(self, monkeypatch, tmp_path):
        _stub_config(
            monkeypatch,
            tmp_path,
            {"infrastructure": {"cluster_name": "myapp-cluster", "rds_instance_id": "myapp-db"}},
        )
        infra = load_environment_infrastructure("myapp-staging")
        assert infra.cluster_name == "myapp-cluster"
        assert infra.rds_id == "myapp-db"
        assert infra.config["infrastructure"]["cluster_name"] == "myapp-cluster"

    def test_missing_infrastructure_block_yields_none(self, monkeypatch, tmp_path):
        _stub_config(monkeypatch, tmp_path, {})
        infra = load_environment_infrastructure("myapp-staging")
        assert infra.cluster_name is None
        assert infra.rds_id is None

    def test_require_cluster_exits(self, monkeypatch, tmp_path, capsys):
        _stub_config(monkeypatch, tmp_path, {"infrastructure": {"rds_instance_id": "db"}})
        with pytest.raises(SystemExit) as exc_info:
            load_environment_infrastructure("myapp-staging", require_cluster=True)
        assert exc_info.value.code == 1
        assert "ECS cluster name" in capsys.readouterr().out

    def test_require_rds_exits(self, monkeypatch, tmp_path, capsys):
        _stub_config(monkeypatch, tmp_path, {"infrastructure": {"cluster_name": "c"}})
        with pytest.raises(SystemExit) as exc_info:
            load_environment_infrastructure("myapp-staging", require_rds=True)
        assert exc_info.value.code == 1
        assert "RDS instance not configured" in capsys.readouterr().out

    def test_unloadable_config_exits(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(cli_utils, "get_environment_path", lambda env: tmp_path / env)

        def boom(env_path):
            raise FileNotFoundError("config.toml not found")

        monkeypatch.setattr("deployer.core.config.load_environment_config", boom)
        with pytest.raises(SystemExit) as exc_info:
            load_environment_infrastructure("myapp-staging")
        assert exc_info.value.code == 1
        assert "Failed to load config" in capsys.readouterr().out

    def test_result_is_frozen(self, monkeypatch, tmp_path):
        _stub_config(monkeypatch, tmp_path, {"infrastructure": {"cluster_name": "c"}})
        infra = load_environment_infrastructure("myapp-staging")
        with pytest.raises(AttributeError):
            infra.cluster_name = "other"


class TestIterDeployedEnvironments:
    """Tests for iter_deployed_environments()."""

    @staticmethod
    def _make(tmp_path, name, *, deployed=True):
        env_path = tmp_path / name
        env_path.mkdir()
        if deployed:
            (env_path / "terraform.tfstate").write_text("{}")
        return env_path

    def test_yields_only_deployed(self, monkeypatch, tmp_path, capsys):
        self._make(tmp_path, "deployed-env")
        self._make(tmp_path, "undeployed-env", deployed=False)
        monkeypatch.setattr(cli_utils, "get_environment_path", lambda env: tmp_path / env)

        yielded = list(
            iter_deployed_environments(["deployed-env", "undeployed-env", "missing-env"])
        )

        assert yielded == [("deployed-env", tmp_path / "deployed-env")]
        out = capsys.readouterr().out
        assert "Environment: deployed-env" in out
        assert "Status: Not deployed" in out
        assert "Directory not found" in out
        assert out.count("=" * 60) == 6

    def test_header_suffix_is_appended(self, monkeypatch, tmp_path, capsys):
        self._make(tmp_path, "myapp-staging")
        monkeypatch.setattr(cli_utils, "get_environment_path", lambda env: tmp_path / env)

        list(iter_deployed_environments(["myapp-staging"], header_suffix=" (last 14 days)"))

        assert "Environment: myapp-staging (last 14 days)" in capsys.readouterr().out


class TestRequireEnvironment:
    """Tests for require_environment() and require_validated_environment()."""

    def test_require_environment_returns_path_and_config(self, monkeypatch, tmp_path):
        _stub_config(monkeypatch, tmp_path, {"infrastructure": {}})
        env_path, config = require_environment("myapp-staging")
        assert env_path == tmp_path / "myapp-staging"
        assert config == {"infrastructure": {}}

    def test_require_environment_wraps_load_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli_utils, "get_environment_path", lambda env: tmp_path / env)

        def boom(env_path):
            raise RuntimeError("tofu output failed")

        monkeypatch.setattr("deployer.core.config.load_environment_config", boom)
        with pytest.raises(EnvironmentConfigError, match="tofu output failed"):
            require_environment("myapp-staging")

    def test_require_validated_environment_rejects_undeployed(self, monkeypatch):
        monkeypatch.setattr(
            cli_utils,
            "validate_environment_deployed",
            lambda _env: (None, "Environment not deployed: myapp-staging"),
        )
        with pytest.raises(EnvironmentConfigError, match="not deployed"):
            require_validated_environment("myapp-staging")

    def test_require_validated_environment_returns_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            cli_utils, "validate_environment_deployed", lambda env: (tmp_path / env, None)
        )
        monkeypatch.setattr(
            "deployer.core.config.load_environment_config", lambda _env_path: {"app": "x"}
        )
        env_path, config = require_validated_environment("myapp-staging")
        assert env_path == tmp_path / "myapp-staging"
        assert config == {"app": "x"}
