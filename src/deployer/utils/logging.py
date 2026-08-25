# These are print() wrappers with no error contract of their own. The four
# inconsistent-error-handling findings they carry report the *callers'*
# handling of their own unrelated work, not anything about these functions —
# the same check mechanic that made utils/aws_profile.py:85 a false positive.
#
# They are left visible rather than suppressed, per this repo's near-zero
# suppression convention: the five ignore lines that used to sit here carried
# a re-evaluate-by tag and no rationale, so the reasoning was 40 lines from
# the findings and one of the five had been dead for months. Adjudicated as
# leave-standing in docs/internal/PYSMELLY.md § 53p (operator, 2026-08-25).
"""Logging utilities for formatted console output."""

import sys
from collections.abc import Iterable

from .colors import Colors

# Global verbose mode flag
_verbose = False


def set_verbose(enabled: bool) -> None:
    """Enable or disable verbose mode.

    Args:
        enabled: If True, log_debug() calls will print output.
    """
    global _verbose  # noqa: PLW0603 — module-level verbose flag
    _verbose = enabled


def is_verbose() -> bool:
    """Check if verbose mode is enabled."""
    return _verbose


def log_debug(msg: str) -> None:
    """Print a debug message (only shown if verbose mode enabled).

    Args:
        msg: The debug message to print.
    """
    if _verbose:
        print(f"  {Colors.CYAN}[debug]{Colors.NC} {msg}")


def log(msg: str) -> None:
    """Print a message in blue."""
    print(f"{Colors.BLUE}{msg}{Colors.NC}")


def log_section(msg: str) -> None:
    """Print a section header."""
    print(f"\n{Colors.BLUE}=== {msg} ==={Colors.NC}")


def log_ok(msg: str) -> None:
    """Print a success message with green checkmark."""
    print(f"  {Colors.GREEN}✓{Colors.NC} {msg}")


def log_success(msg: str) -> None:
    """Print a message with [done] suffix in green."""
    print(f"  {msg} {Colors.GREEN}[done]{Colors.NC}")


def log_status(msg: str, status: str) -> None:
    """Print a message with a status suffix in yellow."""
    print(f"  {msg} {Colors.YELLOW}[{status}]{Colors.NC}")


def log_warning(msg: str) -> None:
    """Print a warning message with yellow indicator."""
    print(f"  {Colors.YELLOW}⚠{Colors.NC} {msg}")


def log_error(msg: str) -> None:
    """Print an error message with red indicator."""
    print(f"  {Colors.RED}✗{Colors.NC} {msg}")


def log_error_stderr(msg: str) -> None:
    """Print an error message to stderr."""
    print(f"{Colors.RED}Error: {msg}{Colors.NC}", file=sys.stderr)


def log_warning_stderr(msg: str) -> None:
    """Print a warning message to stderr."""
    print(f"{Colors.YELLOW}Warning: {msg}{Colors.NC}", file=sys.stderr)


def log_info(msg: str) -> None:
    """Print an info message with cyan indicator."""
    print(f"  {Colors.CYAN}ℹ{Colors.NC} {msg}")


# An error worth reporting is an error worth telling the reader how to fix.
# These two are the repo's vocabulary for "message, then what to do about it":
# advice_block() composes a string to hand to an exception, print_with_advice()
# writes to the terminal. Both keep the shape identical across call sites.


def advice_block(
    heading: str,
    items: Iterable[str] = (),
    advice: Iterable[str] = (),
    *,
    bullet: str = "    ",
) -> str:
    """Compose "heading / bulleted items / blank line / advice" as one string.

    Args:
        heading: First line, e.g. "Audit found 3 issue(s):".
        items: Detail lines, each prefixed with `bullet`.
        advice: Remediation lines, emitted verbatim after a blank line.
            When empty, no blank line is emitted either.
        bullet: Prefix applied to each item.

    Returns:
        The composed message, without a trailing newline.
    """
    lines = [heading]
    lines.extend(f"{bullet}{item}" for item in items)

    advice_lines = list(advice)
    if advice_lines:
        lines.append("")
        lines.extend(advice_lines)

    return "\n".join(lines)


def print_with_advice(message: str, *advice: str) -> None:
    """Print a blank line, an error message, a blank line, then advice lines.

    Args:
        message: The error message, printed via log_error().
        advice: Remediation lines, printed verbatim (already indented by the
            caller). An empty string prints a blank separator line.
    """
    print()
    log_error(message)
    print()
    for line in advice:
        print(line)
