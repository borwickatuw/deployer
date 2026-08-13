"""Tests for bin/ssm-secrets.py — the SSM Parameter Store CLI."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from deployer.core.ssm_secrets import format_missing_secrets_error, ssm_put_commands

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("ssm_secrets_cli", bin_dir / "ssm-secrets.py")
ssm_cli = module_from_spec(_spec)
_spec.loader.exec_module(ssm_cli)


def _resolves_to(monkeypatch, path):
    """Make cmd_check's deploy.toml resolution return `path` without touching disk."""
    monkeypatch.setattr(ssm_cli, "resolve_deploy_toml_or_exit", lambda *_a, **_kw: path)


class TestSsmPutCommands:
    """Tests for the shared put-command derivation."""

    def test_derives_the_secret_name_from_the_ssm_path(self):
        assert ssm_put_commands("myapp-staging", [("SECRET_KEY", "/myapp/staging/SECRET_KEY")]) == [
            "uv run python bin/ssm-secrets.py put myapp-staging SECRET_KEY"
        ]

    def test_empty_missing_list_yields_no_commands(self):
        assert ssm_put_commands("myapp-staging", []) == []

    def test_format_missing_secrets_error_uses_the_same_commands(self):
        missing = [
            ("SECRET_KEY", "/myapp/staging/SECRET_KEY"),
            ("DB_PASS", "/myapp/staging/DB_PASS"),
        ]
        message = format_missing_secrets_error(missing, "myapp-staging")
        for command in ssm_put_commands("myapp-staging", missing):
            assert f"  {command}" in message


class TestPrintSecretTable:
    """Tests for _print_secret_table()."""

    def test_each_status_gets_a_row(self, capsys):
        ssm_cli._print_secret_table(
            [("OK_VAR", "/myapp/staging/OK_VAR")],
            [("GONE", "/myapp/staging/GONE")],
            [("STRAY", "/myapp/staging/STRAY")],
        )
        out = capsys.readouterr().out
        assert "OK_VAR" in out and " OK" in out
        assert "GONE" in out and "MISSING" in out
        assert "STRAY" in out and "EXTRA" in out

    def test_summary_counts_required_as_present_plus_missing(self, capsys):
        ssm_cli._print_secret_table(
            [("A", "/p/e/A"), ("B", "/p/e/B")],
            [("C", "/p/e/C")],
            [("D", "/p/e/D")],
        )
        out = capsys.readouterr().out
        assert "Required: 3 secret(s)" in out
        assert "Present: 2, Missing: 1, Extra: 1" in out

    def test_column_widens_to_the_longest_name(self, capsys):
        long_name = "A_VERY_LONG_ENVIRONMENT_VARIABLE_NAME"
        ssm_cli._print_secret_table([(long_name, "/p/e/x")], [], [])
        header = capsys.readouterr().out.splitlines()[0]
        assert header.index("SSM Path") > len(long_name)

    def test_no_rows_still_prints_a_header(self, capsys):
        ssm_cli._print_secret_table([], [], [])
        out = capsys.readouterr().out
        assert "Environment Variable" in out
        assert "Required: 0 secret(s)" in out


class TestParseEnvironment:
    """Tests for the exit-on-error CLI wrapper around parse_environment()."""

    def test_splits_project_and_environment(self):
        assert ssm_cli.parse_environment("myapp-staging") == ("myapp", "staging")

    def test_invalid_name_exits_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            ssm_cli.parse_environment("nodashes")
        assert exc_info.value.code == 1
        assert "Invalid environment name" in capsys.readouterr().err


class TestCmdCheck:
    """Tests for cmd_check(), with every collaborator stubbed at its seam."""

    def test_nothing_declared_and_nothing_in_ssm_returns_0(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")
        monkeypatch.setattr(ssm_cli, "get_secrets_from_deploy_toml", lambda _p, _e: {})
        monkeypatch.setattr(ssm_cli.ssm, "list_parameters", lambda _prefix: ([], None))

        assert ssm_cli.cmd_check("myapp-staging", None) == 0
        assert "No SSM secrets defined" in capsys.readouterr().out

    def test_all_present_returns_0(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")
        monkeypatch.setattr(
            ssm_cli,
            "get_secrets_from_deploy_toml",
            lambda _p, _e: {"SECRET_KEY": "/myapp/staging/SECRET_KEY"},
        )
        monkeypatch.setattr(
            ssm_cli.ssm,
            "list_parameters",
            lambda _prefix: ([{"name": "/myapp/staging/SECRET_KEY"}], None),
        )

        assert ssm_cli.cmd_check("myapp-staging", None) == 0
        out = capsys.readouterr().out
        assert "Present: 1, Missing: 0, Extra: 0" in out
        assert "To set missing secrets" not in out

    def test_missing_secret_returns_1_and_prints_put_commands(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")
        monkeypatch.setattr(
            ssm_cli,
            "get_secrets_from_deploy_toml",
            lambda _p, _e: {"SECRET_KEY": "/myapp/staging/SECRET_KEY"},
        )
        monkeypatch.setattr(ssm_cli.ssm, "list_parameters", lambda _prefix: ([], None))

        assert ssm_cli.cmd_check("myapp-staging", None) == 1
        out = capsys.readouterr().out
        assert "MISSING" in out
        assert "  uv run python bin/ssm-secrets.py put myapp-staging SECRET_KEY" in out

    def test_extra_secret_returns_1_and_prints_delete_commands(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")
        monkeypatch.setattr(ssm_cli, "get_secrets_from_deploy_toml", lambda _p, _e: {})
        monkeypatch.setattr(
            ssm_cli.ssm,
            "list_parameters",
            lambda _prefix: ([{"name": "/myapp/staging/STRAY"}], None),
        )

        assert ssm_cli.cmd_check("myapp-staging", None) == 1
        out = capsys.readouterr().out
        assert "EXTRA" in out
        assert "  uv run python bin/ssm-secrets.py delete myapp-staging STRAY" in out

    def test_unparseable_deploy_toml_returns_1(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")

        def boom(_path, _env):
            raise ValueError("bad table")

        monkeypatch.setattr(ssm_cli, "get_secrets_from_deploy_toml", boom)

        assert ssm_cli.cmd_check("myapp-staging", None) == 1
        assert "Error parsing deploy.toml: bad table" in capsys.readouterr().err

    def test_ssm_list_failure_returns_1(self, monkeypatch, tmp_path, capsys):
        _resolves_to(monkeypatch, tmp_path / "deploy.toml")
        monkeypatch.setattr(ssm_cli, "get_secrets_from_deploy_toml", lambda _p, _e: {})
        monkeypatch.setattr(ssm_cli.ssm, "list_parameters", lambda _prefix: (None, "AccessDenied"))

        assert ssm_cli.cmd_check("myapp-staging", None) == 1
        assert "Error listing SSM parameters: AccessDenied" in capsys.readouterr().err


class TestCmdPut:
    """Tests for cmd_put()'s value-source branches."""

    def _capture_put(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ssm_cli.ssm, "parameter_exists", lambda _p: False)
        monkeypatch.setattr(
            ssm_cli.ssm,
            "put_parameter",
            lambda **kwargs: (calls.append(kwargs), (True, None))[1],
        )
        return calls

    def test_explicit_value_is_stored(self, monkeypatch):
        calls = self._capture_put(monkeypatch)
        assert ssm_cli.cmd_put("myapp-staging", "SECRET_KEY", "s3cret", None, None) == 0
        assert calls[0]["value"] == "s3cret"

    def test_from_file_reads_the_file(self, monkeypatch, tmp_path):
        calls = self._capture_put(monkeypatch)
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("from-disk")

        assert ssm_cli.cmd_put("myapp-staging", "SECRET_KEY", None, str(secret_file), None) == 0
        assert calls[0]["value"] == "from-disk"

    def test_missing_file_returns_1(self, monkeypatch, tmp_path, capsys):
        self._capture_put(monkeypatch)
        absent = str(tmp_path / "absent.txt")

        assert ssm_cli.cmd_put("myapp-staging", "SECRET_KEY", None, absent, None) == 1
        assert "File not found" in capsys.readouterr().err

    def test_random_generates_a_value_of_the_requested_length(self, monkeypatch):
        calls = self._capture_put(monkeypatch)
        assert ssm_cli.cmd_put("myapp-staging", "SECRET_KEY", None, None, 16) == 0
        assert len(calls[0]["value"]) == 16

    def test_put_failure_returns_1(self, monkeypatch, capsys):
        monkeypatch.setattr(ssm_cli.ssm, "parameter_exists", lambda _p: True)
        monkeypatch.setattr(ssm_cli.ssm, "put_parameter", lambda **_kwargs: (False, "AccessDenied"))

        assert ssm_cli.cmd_put("myapp-staging", "SECRET_KEY", "v", None, None) == 1
        assert "AccessDenied" in capsys.readouterr().err


class TestCmdGetListDelete:
    """Tests for the remaining read/write commands."""

    def test_get_quiet_prints_only_the_value(self, monkeypatch, capsys):
        monkeypatch.setattr(ssm_cli.ssm, "get_parameter", lambda _p: ("v4lue", None))
        assert ssm_cli.cmd_get("myapp-staging", "SECRET_KEY", True) == 0
        assert capsys.readouterr().out == "v4lue\n"

    def test_get_error_returns_1(self, monkeypatch, capsys):
        monkeypatch.setattr(ssm_cli.ssm, "get_parameter", lambda _p: (None, "ParameterNotFound"))
        assert ssm_cli.cmd_get("myapp-staging", "SECRET_KEY", False) == 1
        assert "ParameterNotFound" in capsys.readouterr().err

    def test_list_with_no_parameters_returns_0(self, monkeypatch, capsys):
        monkeypatch.setattr(ssm_cli.ssm, "list_parameters", lambda _prefix: ([], None))
        assert ssm_cli.cmd_list("myapp-staging") == 0
        assert "No secrets found." in capsys.readouterr().out

    def test_list_renders_rows(self, monkeypatch, capsys):
        monkeypatch.setattr(
            ssm_cli.ssm,
            "list_parameters",
            lambda _prefix: ([{"name": "/myapp/staging/SECRET_KEY", "description": "d"}], None),
        )
        assert ssm_cli.cmd_list("myapp-staging") == 0
        out = capsys.readouterr().out
        assert "SECRET_KEY" in out
        assert "N/A" in out
        assert "Total: 1 secret(s)" in out

    def test_delete_with_force_skips_the_prompt(self, monkeypatch, capsys):
        monkeypatch.setattr(ssm_cli.ssm, "delete_parameter", lambda _p: (True, None))
        assert ssm_cli.cmd_delete("myapp-staging", "SECRET_KEY", True) == 0
        assert "Secret deleted" in capsys.readouterr().out

    def test_delete_declined_returns_0_without_deleting(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

        def unexpected(_p):
            raise AssertionError("delete_parameter must not be called")

        monkeypatch.setattr(ssm_cli.ssm, "delete_parameter", unexpected)

        assert ssm_cli.cmd_delete("myapp-staging", "SECRET_KEY", False) == 0
        assert "Cancelled." in capsys.readouterr().out
