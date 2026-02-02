"""Logging utilities for formatted console output."""

import sys

from .colors import Colors


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
