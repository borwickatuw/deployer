"""Tests for deployer.utils package."""

from deployer.utils import (
    Colors,
    advice_block,
    format_timestamp,
    log,
    log_error,
    log_info,
    log_ok,
    log_section,
    log_status,
    log_success,
    log_warning,
    print_with_advice,
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


class TestAdviceBlock:
    """Tests for advice_block composition."""

    def test_heading_items_and_advice(self):
        """Items are bulleted and advice follows a blank line."""
        message = advice_block(
            "Audit found 2 issue(s):",
            ["first", "second"],
            ["To fix: edit deploy.toml", "        or use --ignore-audit"],
            bullet="  - ",
        )
        assert message == (
            "Audit found 2 issue(s):\n"
            "  - first\n"
            "  - second\n"
            "\n"
            "To fix: edit deploy.toml\n"
            "        or use --ignore-audit"
        )

    def test_default_bullet(self):
        """The default bullet is a four-space indent."""
        assert advice_block("Heading:", ["one"]) == "Heading:\n    one"

    def test_no_advice_means_no_blank_line(self):
        """A block with no advice ends at its last item."""
        assert advice_block("Heading:", ["one", "two"]) == "Heading:\n    one\n    two"

    def test_heading_only(self):
        """A heading with neither items nor advice is just the heading."""
        assert advice_block("Heading:") == "Heading:"

    def test_accepts_generators(self):
        """Items and advice may be any iterable, consumed once."""
        message = advice_block(
            "Heading:",
            (s for s in ["one"]),
            (s for s in ["advice"]),
        )
        assert message == "Heading:\n    one\n\nadvice"


class TestPrintWithAdvice:
    """Tests for print_with_advice terminal output."""

    def test_message_and_advice(self, capsys):
        """The message is logged as an error, advice printed verbatim."""
        print_with_advice("It broke", "  Try again.", "  Then check the network.")
        captured = capsys.readouterr()
        assert captured.out == (
            "\n"
            f"  {Colors.RED}✗{Colors.NC} It broke\n"
            "\n"
            "  Try again.\n"
            "  Then check the network.\n"
        )

    def test_empty_advice_line_is_a_separator(self, capsys):
        """An empty advice string prints a blank separator line."""
        print_with_advice("It broke", "  first", "", "  second")
        captured = capsys.readouterr()
        assert captured.out.endswith("  first\n\n  second\n")

    def test_no_advice(self, capsys):
        """With no advice, only the framing blank lines are printed."""
        print_with_advice("It broke")
        captured = capsys.readouterr()
        assert captured.out == f"\n  {Colors.RED}✗{Colors.NC} It broke\n\n"


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


class TestFormatTimestamp:
    """Tests for format_timestamp()."""

    def test_formats_iso_with_offset(self):
        """Test that an offset-aware ISO timestamp is reformatted."""
        assert format_timestamp("2026-08-13T14:05:00+00:00") == "2026-08-13 14:05 UTC"

    def test_accepts_trailing_z(self):
        """Test that a trailing Z is accepted (datetime.fromisoformat pre-3.11 style)."""
        assert format_timestamp("2026-08-13T14:05:00Z") == "2026-08-13 14:05 UTC"

    def test_custom_format(self):
        """Test that the strftime format is configurable."""
        assert format_timestamp("2026-08-13T14:05:00Z", "%Y-%m-%d") == "2026-08-13"

    def test_unparseable_string_passes_through(self):
        """Test that a non-ISO string is returned unchanged."""
        assert format_timestamp("unknown") == "unknown"

    def test_non_string_passes_through(self):
        """Test that a non-string value is returned unchanged (AttributeError path)."""
        assert format_timestamp(None) is None
