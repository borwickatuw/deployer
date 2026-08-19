"""Characterization pins for the bin/ entry points' error boundaries.

Two library functions have a documented, actionable failure mode that some
``bin/`` entry points let reach the operator as a traceback instead:

* ``utils/environment.get_environments_dir`` raises ``RuntimeError`` carrying
  "Set it in your .env file"; six ``bin/`` sites do not catch it.
* ``core/config.load_environment_config`` raises ``FileNotFoundError`` /
  ``RuntimeError``; four ``bin/`` sites do not catch it, and two catch broad
  ``Exception``.

That is the ``inconsistent-error-handling`` pair decided in
``docs/internal/DECISIONS.md`` § "2026-08-18: Error Contracts", layer 2.
The fix is the boundary rule the ADR chose over a per-caller ``try``: wrap the
single call that can fail in ``utils/cli.py exit_on``. Sites that already
caught correctly are pinned too, because those are the ones the fix had to
leave alone.

**One of the ADR's eleven sites was not a defect.** It recorded
``bin/resolve-config.py:109`` as a traceback "in the CI config resolver".
Measured: ``resolve_config()`` is a library function with a documented
``Raises:``, and ``cli()`` already catches ``FileNotFoundError``,
``RuntimeError`` and ``ValueError`` around it — exit 1, clean message, no
traceback. The check keys on the immediately enclosing function rather than on
the boundary, which is the same mechanic that made
``utils/aws_profile.py:85`` a false positive. Both halves are pinned below.

The three ``bin/ops.py`` ``load_environment_config`` sites live in
``test_ops.py`` alongside the rest of that file's command tests.
"""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from click.testing import CliRunner

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))


def _load(module_name: str, filename: str):
    """Import a bin/ script under its own module name."""
    spec = spec_from_file_location(module_name, bin_dir / filename)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capacity = _load("boundary_capacity_report", "capacity-report.py")
cognito = _load("boundary_cognito", "cognito.py")
deploy = _load("boundary_deploy", "deploy.py")
environment = _load("boundary_environment", "environment.py")
init = _load("boundary_init", "init.py")
resolve_config = _load("boundary_resolve_config", "resolve-config.py")

ENV = "myapp-staging"

# The text get_environments_dir() puts in its RuntimeError. It is actionable —
# which is exactly why letting it become a traceback wastes it.
UNSET_ADVICE = "DEPLOYER_ENVIRONMENTS_DIR environment variable is not set."


@pytest.fixture
def environments_dir_unset(monkeypatch):
    """Unset DEPLOYER_ENVIRONMENTS_DIR so get_environments_dir() raises."""
    monkeypatch.delenv("DEPLOYER_ENVIRONMENTS_DIR", raising=False)


class TestUnsetEnvironmentsDirExitsCleanly:
    """The six bin/ sites that used to let the RuntimeError escape.

    Each now exits 1 with the RuntimeError's own actionable text on stderr —
    which is the point of the fix: the message already said "Set it in your
    .env file", and a traceback wasted it.
    """

    def _assert_declines(self, capsys) -> None:
        assert UNSET_ADVICE in capsys.readouterr().err

    def test_capacity_report_cli(self, environments_dir_unset, monkeypatch):
        monkeypatch.setattr(capacity, "configure_aws_profile", lambda _op: None)

        result = CliRunner().invoke(capacity.cli, [])

        assert result.exit_code == 1
        assert not isinstance(result.exception, RuntimeError)
        assert UNSET_ADVICE in result.output

    def test_cognito_environment_discovery(self, environments_dir_unset, capsys):
        with pytest.raises(SystemExit) as exit_info:
            cognito.get_cognito_environments()
        assert exit_info.value.code == 1
        self._assert_declines(capsys)

    def test_deploy_config_load(self, environments_dir_unset, capsys):
        with pytest.raises(SystemExit) as exit_info:
            deploy._load_env_config_or_exit(ENV)
        assert exit_info.value.code == 1
        self._assert_declines(capsys)

    def test_environment_status(self, environments_dir_unset, capsys):
        with pytest.raises(SystemExit) as exit_info:
            environment.cmd_status(None)
        assert exit_info.value.code == 1
        self._assert_declines(capsys)

    def test_init_environment_target(self, environments_dir_unset, capsys):
        with pytest.raises(SystemExit) as exit_info:
            init._resolve_environment_target("myapp", "standalone-staging", dry_run=True)
        assert exit_info.value.code == 1
        self._assert_declines(capsys)

    def test_init_environment_file_write(self, environments_dir_unset, monkeypatch, tmp_path):
        """The sixth site had no exit_on to add: the call was only for a message.

        _write_environment_files() called get_environments_dir() to name the
        directory it had just been handed. env_path.parent is the same value
        and cannot fail, so the site is gone rather than guarded.
        """
        monkeypatch.setattr(init, "ensure_environments_symlinks", lambda: ["modules"])
        env_path = tmp_path / ENV

        init._write_environment_files(env_path, {}, "standalone-staging")

        assert env_path.is_dir()


class TestUnsetEnvironmentsDirIsAlreadyHandled:
    """The three bin/init.py sites that already catch it — the model to follow."""

    def test_bootstrap_path_resolution(self, environments_dir_unset, capsys):
        assert init._resolve_bootstrap_path("bootstrap-staging", dry_run=True) is None
        assert "DEPLOYER_ENVIRONMENTS_DIR" in capsys.readouterr().err

    def test_bootstrap_migrate(self, environments_dir_unset, capsys):
        assert init.cmd_bootstrap_migrate("bootstrap-staging", dry_run=True) == 1
        assert "DEPLOYER_ENVIRONMENTS_DIR is not set" in capsys.readouterr().err

    def test_bootstrap_precondition(self, environments_dir_unset, capsys):
        assert init._require_bootstrap() is False
        assert "DEPLOYER_ENVIRONMENTS_DIR" in capsys.readouterr().err


class TestResolveConfigLoad:
    """bin/resolve-config.py's load_environment_config site, line 109."""

    @pytest.fixture
    def env_dir(self, monkeypatch, tmp_path):
        """A real environment directory with a config.toml, ready to resolve."""
        env_path = tmp_path / ENV
        env_path.mkdir()
        (env_path / "config.toml").write_text('[infrastructure]\ncluster_name = "c"\n')
        monkeypatch.setattr(resolve_config, "get_environment_path", lambda _e: env_path)
        monkeypatch.setattr(resolve_config, "get_all_tofu_outputs", lambda _p: {})
        return env_path

    def test_a_missing_environment_directory_raises_before_any_load(self, monkeypatch, tmp_path):
        monkeypatch.setattr(resolve_config, "get_environment_path", lambda _e: tmp_path / "gone")

        with pytest.raises(FileNotFoundError, match="Environment directory not found"):
            resolve_config.resolve_config(ENV)

    def test_a_failed_resolution_propagates_to_the_caller(self, env_dir, monkeypatch):
        """resolve_config() is a library function and documents this Raises:."""

        def _raise(_env_path):
            raise RuntimeError("Failed to get tofu outputs")

        monkeypatch.setattr(resolve_config, "load_environment_config", _raise)

        with pytest.raises(RuntimeError, match="Failed to get tofu outputs"):
            resolve_config.resolve_config(ENV)

    def test_the_cli_already_turns_that_into_a_clean_exit_1(self, env_dir, monkeypatch, capsys):
        """The ADR's "traceback in the CI config resolver" was not a defect.

        cli() already caught the documented triple around resolve_config().
        The finding keys on the immediately enclosing function, not on the
        boundary — the same mechanic behind the aws_profile.py false positive.
        """

        def _raise(_env_path):
            raise RuntimeError("Failed to get tofu outputs")

        monkeypatch.setattr(resolve_config, "load_environment_config", _raise)
        monkeypatch.setattr(resolve_config, "configure_profile_or_exit", lambda *_a: None)

        result = CliRunner().invoke(resolve_config.cli, [ENV])

        assert result.exit_code == 1
        assert not isinstance(result.exception, RuntimeError)
        assert "Failed to resolve config: Failed to get tofu outputs" in result.output


class TestDeployNarrowedCatch:
    """bin/deploy.py — the broad `except Exception`, narrowed to the documented pair."""

    @pytest.fixture
    def env_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEPLOYER_ENVIRONMENTS_DIR", str(tmp_path))
        (tmp_path / ENV).mkdir()
        return tmp_path / ENV

    def test_a_missing_config_exits_1_with_its_own_message(self, env_dir, monkeypatch, capsys):
        def _raise(_env_path):
            raise FileNotFoundError("Config file not found")

        monkeypatch.setattr(deploy, "load_environment_config", _raise)

        with pytest.raises(SystemExit) as exit_info:
            deploy._load_env_config_or_exit(ENV)
        assert exit_info.value.code == 1
        assert "Config file not found" in capsys.readouterr().out

    def test_a_documented_runtime_error_exits_1(self, env_dir, monkeypatch, capsys):
        def _raise(_env_path):
            raise RuntimeError("Failed to get tofu outputs")

        monkeypatch.setattr(deploy, "load_environment_config", _raise)

        with pytest.raises(SystemExit) as exit_info:
            deploy._load_env_config_or_exit(ENV)
        assert exit_info.value.code == 1
        assert "Failed to load deployment config" in capsys.readouterr().out

    def test_an_undocumented_error_is_no_longer_swallowed(self, env_dir, monkeypatch, capsys):
        """A TypeError from inside the resolver is a bug, not an operator problem.

        The broad `except Exception` reported it as "Failed to load deployment
        config" with no traceback, sending the operator to check a config.toml
        that was never at fault. Narrowed to the documented pair, it propagates
        with its stack intact.
        """

        def _raise(_env_path):
            raise TypeError("unhashable type: 'dict'")

        monkeypatch.setattr(deploy, "load_environment_config", _raise)

        with pytest.raises(TypeError, match="unhashable type"):
            deploy._load_env_config_or_exit(ENV)
        assert "Failed to load deployment config" not in capsys.readouterr().out
