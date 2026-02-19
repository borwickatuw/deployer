"""Tests for deployer.utils package."""

import io
import sys
from pathlib import Path

import pytest

from deployer.utils import (
    Colors,
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
