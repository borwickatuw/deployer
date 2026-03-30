"""Subprocess utilities for running shell commands."""

import subprocess


def run_command(cmd: list[str], cwd: str | None = None) -> tuple[bool, str]:
    """Run a command and return (success, output).

    Args:
        cmd: Command and arguments as a list of strings.
        cwd: Optional working directory for the command.

    Returns:
        Tuple of (success: bool, output: str).
        On success, output is stdout.
        On failure, output is stderr or exception message.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        if result.returncode != 0:
            return False, result.stderr
        return True, result.stdout
    except Exception as e:
        return False, str(e)
