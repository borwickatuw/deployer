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
Every test here pins **today's** behaviour so the boundary fix in 53i-3c is
visible as a diff. Where a site already catches correctly it is pinned too,
because those are the sites the fix must leave alone.

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


class TestUnsetEnvironmentsDirIsATraceback:
    """The six bin/ sites that let the RuntimeError escape."""

    def test_capacity_report_cli(self, environments_dir_unset, monkeypatch):
        monkeypatch.setattr(capacity, "configure_aws_profile", lambda _op: None)

        result = CliRunner().invoke(capacity.cli, [])

        assert result.exit_code == 1
        assert isinstance(result.exception, RuntimeError)
        assert UNSET_ADVICE in str(result.exception)

    def test_cognito_environment_discovery(self, environments_dir_unset):
        with pytest.raises(RuntimeError, match=UNSET_ADVICE):
            cognito.get_cognito_environments()

    def test_deploy_config_load(self, environments_dir_unset):
        with pytest.raises(RuntimeError, match=UNSET_ADVICE):
            deploy._load_env_config_or_exit(ENV)

    def test_environment_status(self, environments_dir_unset):
        with pytest.raises(RuntimeError, match=UNSET_ADVICE):
            environment.cmd_status(None)

    def test_init_environment_target(self, environments_dir_unset):
        with pytest.raises(RuntimeError, match=UNSET_ADVICE):
            init._resolve_environment_target("myapp", "standalone-staging", dry_run=True)

    def test_init_environment_file_write(self, environments_dir_unset, monkeypatch, tmp_path):
        monkeypatch.setattr(init, "ensure_environments_symlinks", lambda: ["modules"])

        with pytest.raises(RuntimeError, match=UNSET_ADVICE):
            init._write_environment_files(tmp_path / ENV, {}, "standalone-staging")


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

    def test_a_failed_resolution_is_an_uncaught_traceback(self, env_dir, monkeypatch):
        # Pinned, not endorsed: the fourth unhandled load_environment_config
        # site. resolve-config.py is the CI config resolver, so this surfaces
        # in a pipeline log rather than in front of a person (Phase 53i).
        def _raise(_env_path):
            raise RuntimeError("Failed to get tofu outputs")

        monkeypatch.setattr(resolve_config, "load_environment_config", _raise)

        with pytest.raises(RuntimeError, match="Failed to get tofu outputs"):
            resolve_config.resolve_config(ENV)


class TestDeployBroadCatch:
    """bin/deploy.py:79 — the broad `except Exception` 53i-3c narrows."""

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

    def test_an_undocumented_error_is_swallowed_by_the_broad_catch(
        self, env_dir, monkeypatch, capsys
    ):
        # Pinned, not endorsed: a TypeError from inside the resolver is a bug,
        # not an operator-fixable condition, and the broad `except Exception`
        # reports it as "Failed to load deployment config" with no traceback
        # (Phase 53i).
        def _raise(_env_path):
            raise TypeError("unhashable type: 'dict'")

        monkeypatch.setattr(deploy, "load_environment_config", _raise)

        with pytest.raises(SystemExit) as exit_info:
            deploy._load_env_config_or_exit(ENV)
        assert exit_info.value.code == 1
        assert "unhashable type" in capsys.readouterr().out
