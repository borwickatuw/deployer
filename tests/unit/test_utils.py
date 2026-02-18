"""Tests for deployer.utils package."""

import io
import sys
from pathlib import Path

import pytest

from deployer.utils import (
    Colors,
    get_staging_environments,
    log,
    log_error,
    log_info,
    log_ok,
    log_section,
    log_status,
    log_success,
    log_warning,
    run_command,
)


class TestColors:
    """Tests for Colors class."""

    def test_colors_defined(self):
        """Test that all expected colors are defined."""
        assert hasattr(Colors, "RED")
        assert hasattr(Colors, "GREEN")
        assert hasattr(Colors, "YELLOW")
        assert hasattr(Colors, "BLUE")
        assert hasattr(Colors, "CYAN")
        assert hasattr(Colors, "BOLD")
        assert hasattr(Colors, "NC")

    def test_colors_are_ansi(self):
        """Test that colors are ANSI escape sequences."""
        assert Colors.RED.startswith("\033[")
        assert Colors.NC == "\033[0m"


class TestLogging:
    """Tests for logging functions."""

    def test_log(self, capsys):
        """Test basic log function."""
        log("test message")
        captured = capsys.readouterr()
        assert "test message" in captured.out
        assert Colors.BLUE in captured.out

    def test_log_section(self, capsys):
        """Test section header logging."""
        log_section("Test Section")
        captured = capsys.readouterr()
        assert "Test Section" in captured.out
        assert "===" in captured.out

    def test_log_ok(self, capsys):
        """Test success logging with checkmark."""
        log_ok("completed")
        captured = capsys.readouterr()
        assert "completed" in captured.out
        assert "✓" in captured.out

    def test_log_success(self, capsys):
        """Test success logging with [done]."""
        log_success("task")
        captured = capsys.readouterr()
        assert "task" in captured.out
        assert "[done]" in captured.out

    def test_log_status(self, capsys):
        """Test status logging."""
        log_status("deploying", "in progress")
        captured = capsys.readouterr()
        assert "deploying" in captured.out
        assert "[in progress]" in captured.out

    def test_log_warning(self, capsys):
        """Test warning logging."""
        log_warning("be careful")
        captured = capsys.readouterr()
        assert "be careful" in captured.out
        assert "⚠" in captured.out

    def test_log_error(self, capsys):
        """Test error logging."""
        log_error("something broke")
        captured = capsys.readouterr()
        assert "something broke" in captured.out
        assert "✗" in captured.out

    def test_log_info(self, capsys):
        """Test info logging."""
        log_info("fyi")
        captured = capsys.readouterr()
        assert "fyi" in captured.out
        assert "ℹ" in captured.out


class TestRunCommand:
    """Tests for run_command function."""

    def test_successful_command(self):
        """Test running a successful command."""
        success, output = run_command(["echo", "hello world"])
        assert success is True
        assert "hello world" in output

    def test_failed_command(self):
        """Test running a command that fails."""
        success, output = run_command(["false"])
        assert success is False

    def test_command_with_cwd(self, tmp_path):
        """Test running a command with working directory."""
        success, output = run_command(["pwd"], cwd=str(tmp_path))
        assert success is True
        assert str(tmp_path) in output

    def test_nonexistent_command(self):
        """Test running a command that doesn't exist."""
        success, output = run_command(["nonexistent_command_xyz"])
        assert success is False

    def test_command_stderr(self):
        """Test that stderr is captured on failure."""
        success, output = run_command(["ls", "/nonexistent_dir_xyz"])
        assert success is False
        assert output  # Should have error message


class TestGetStagingEnvironments:
    """Tests for get_staging_environments function."""

    @staticmethod
    def _make_env(tmp_path, name, env_type="staging"):
        """Helper to create an environment directory with typed config.toml."""
        d = tmp_path / name
        d.mkdir()
        d.joinpath("config.toml").write_text(
            f'[environment]\ntype = "{env_type}"\n'
        )

    def test_find_staging_environments(self, tmp_path):
        """Test finding staging environment directories."""
        self._make_env(tmp_path, "app-staging", "staging")
        self._make_env(tmp_path, "other-staging", "staging")
        self._make_env(tmp_path, "production", "production")

        result = get_staging_environments(tmp_path)

        assert "app-staging" in result
        assert "other-staging" in result
        assert "production" not in result

    def test_no_environments_directory(self, tmp_path):
        """Test when environments directory doesn't exist."""
        nonexistent = tmp_path / "nonexistent"
        result = get_staging_environments(nonexistent)
        assert result == []

    def test_no_staging_environments(self, tmp_path):
        """Test when no staging environments exist."""
        self._make_env(tmp_path, "app-production", "production")
        self._make_env(tmp_path, "app-development", "development")

        result = get_staging_environments(tmp_path)
        assert result == []

    def test_results_are_sorted(self, tmp_path):
        """Test that results are returned in sorted order."""
        self._make_env(tmp_path, "z-staging", "staging")
        self._make_env(tmp_path, "a-staging", "staging")
        self._make_env(tmp_path, "m-staging", "staging")

        result = get_staging_environments(tmp_path)

        assert result == ["a-staging", "m-staging", "z-staging"]

    def test_only_directories(self, tmp_path):
        """Test that only directories are returned, not files."""
        self._make_env(tmp_path, "real-staging", "staging")
        (tmp_path / "fake-staging.txt").write_text("not a dir")

        result = get_staging_environments(tmp_path)

        assert result == ["real-staging"]

    def test_name_independent_of_type(self, tmp_path):
        """Test that environment type comes from config.toml, not the name."""
        self._make_env(tmp_path, "my-test-env", "staging")
        self._make_env(tmp_path, "staging-backup", "production")
        self._make_env(tmp_path, "real-staging", "staging")

        result = get_staging_environments(tmp_path)

        assert "my-test-env" in result
        assert "real-staging" in result
        assert "staging-backup" not in result

    def test_multi_hyphen_app_names(self, tmp_path):
        """Test that multi-hyphen app names work correctly."""
        self._make_env(tmp_path, "my-cool-app-staging", "staging")
        self._make_env(tmp_path, "api-v2-staging", "staging")
        self._make_env(tmp_path, "simple-staging", "staging")

        result = get_staging_environments(tmp_path)

        assert "my-cool-app-staging" in result
        assert "api-v2-staging" in result
        assert "simple-staging" in result
        assert len(result) == 3
