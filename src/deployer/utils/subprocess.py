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
        On failure, output is stderr, or the launch error when the command
        could not be started at all (not installed, not executable).

    Raises:
        Anything that is not a launch failure -- a ValueError from bad
        arguments, a UnicodeDecodeError from the output. Those are bugs or
        surprises, not "the command failed", and reporting them as a failed
        command sent the operator to debug a command that never ran wrong.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
        if result.returncode != 0:
            return False, result.stderr
        return True, result.stdout
    except OSError as e:
        return False, str(e)
