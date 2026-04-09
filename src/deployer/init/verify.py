"""Verify tool versions and AWS profile configuration."""

import os
import re
import subprocess
import sys

from ..utils.colors import Colors
from ..utils.subprocess import run_command

# Each entry: display_name, command, version_args, version_regex, min_version_tuple_or_None
TOOLS = [
    ("python3", "python3", ["--version"], r"(\d+\.\d+)", (3, 12)),
    ("uv", "uv", ["--version"], r"(\d+\.\d+\.\d+)", None),
    ("tofu", "tofu", ["--version"], r"(\d+\.\d+)", (1, 6)),
    ("aws", "aws", ["--version"], r"aws-cli/(\d+)", (2,)),
    ("docker", "docker", ["--version"], r"(\d+\.\d+\.\d+)", None),
]

# Profiles to check and their expected role names
AWS_PROFILES = [
    ("deployer-app", "deployer-app-deploy"),
    ("deployer-infra", "deployer-infra-admin"),
    ("deployer-cognito", "deployer-cognito-admin"),
]


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string like '3.12' or '1.6.2' into a tuple of ints."""
    return tuple(int(x) for x in version_str.split("."))


def _check_tools() -> bool:
    """Check tool versions. Returns True if all pass."""
    print("Checking tools...")
    all_ok = True

    for display_name, cmd, args, pattern, min_version in TOOLS:
        success, output = run_command([cmd] + args)
        if not success:
            print(f"  {display_name}: {Colors.RED}not found{Colors.NC}")
            all_ok = False
            continue

        match = re.search(pattern, output)
        if not match:
            print(f"  {display_name}: {Colors.RED}could not parse version{Colors.NC}")
            all_ok = False
            continue

        version_str = match.group(1)

        if min_version is not None:
            version_tuple = _parse_version(version_str)
            if version_tuple < min_version:
                min_str = ".".join(str(x) for x in min_version)
                print(
                    f"  {display_name}: {Colors.RED}{version_str} "
                    f"(requires >= {min_str}){Colors.NC}"
                )
                all_ok = False
                continue

        print(f"  {display_name}: {Colors.GREEN}{version_str}{Colors.NC}")

    return all_ok


def _check_deployer_config() -> bool:
    """Check .env configuration and bootstrap directory. Returns True if all pass."""
    from ..utils.environment import get_environments_dir  # noqa: PLC0415
    from .bootstrap import bootstrap_dir_exists  # noqa: PLC0415

    print("\nChecking deployer configuration...")
    all_ok = True

    # Check DEPLOYER_ENVIRONMENTS_DIR
    env_dir_value = os.environ.get("DEPLOYER_ENVIRONMENTS_DIR")
    if not env_dir_value:
        print(
            f"  .env: {Colors.RED}DEPLOYER_ENVIRONMENTS_DIR not set{Colors.NC}\n"
            f"        Run: cp .env.example .env"
        )
        return False

    try:
        env_dir = get_environments_dir()
    except RuntimeError:
        print(f"  .env: {Colors.RED}DEPLOYER_ENVIRONMENTS_DIR not set{Colors.NC}")
        return False

    print(f"  .env: {Colors.GREEN}DEPLOYER_ENVIRONMENTS_DIR = {env_dir}{Colors.NC}")

    # Check bootstrap directory
    bootstrap_name = bootstrap_dir_exists()
    if bootstrap_name:
        print(f"  bootstrap: {Colors.GREEN}{bootstrap_name}{Colors.NC}")
    else:
        print(
            f"  bootstrap: {Colors.RED}no bootstrap-* directory found{Colors.NC}\n"
            f"             Run: uv run python bin/init.py bootstrap"
        )
        all_ok = False

    return all_ok


def _check_aws_profiles() -> bool:
    """Check AWS profile configuration. Returns True if all pass."""
    from ..utils.aws_profile import validate_aws_profile  # noqa: PLC0415

    print("\nChecking AWS profiles...")
    all_ok = True

    for profile_name, role_name in AWS_PROFILES:
        success, error = validate_aws_profile(profile_name)
        if success:
            print(f"  {profile_name}: {Colors.GREEN}{role_name}{Colors.NC}")
        else:
            # Show just the first line of the error
            first_line = error.split("\n")[0] if error else "unknown error"
            print(f"  {profile_name}: {Colors.RED}{first_line}{Colors.NC}")
            all_ok = False

    return all_ok


def _check_bootstrap_plan() -> bool | None:
    """Run tofu plan on the bootstrap directory to check for drift.

    Returns True if no changes, False if there are changes or errors,
    None if the check was skipped (no bootstrap dir or not initialized).
    """
    from ..utils.environment import get_environments_dir  # noqa: PLC0415
    from .bootstrap import bootstrap_dir_exists  # noqa: PLC0415

    bootstrap_name = bootstrap_dir_exists()
    if not bootstrap_name:
        return None

    env_dir = get_environments_dir()
    bootstrap_path = env_dir / bootstrap_name

    # Check if tofu has been initialized
    if not (bootstrap_path / ".terraform").exists():
        print(f"\n  bootstrap plan: {Colors.YELLOW}not initialized (tofu init not run){Colors.NC}")
        return None

    print(f"\nChecking bootstrap for drift ({bootstrap_name})...")
    result = subprocess.run(
        ["tofu", "plan", "-detailed-exitcode", "-no-color"],
        cwd=str(bootstrap_path),
        capture_output=True,
        text=True,
        check=False,
    )

    # -detailed-exitcode: 0 = no changes, 1 = error, 2 = changes present
    if result.returncode == 0:
        print(f"  tofu plan: {Colors.GREEN}no changes{Colors.NC}")
        return True
    elif result.returncode == 2:
        print(f"  tofu plan: {Colors.YELLOW}changes detected{Colors.NC}")
        # Show a summary of what changed
        for line in result.stdout.splitlines():
            if line.strip().startswith(("~", "+", "-", "#")):
                print(f"    {line.rstrip()}")
        return False
    else:
        # Error running plan (e.g., missing admin credentials) — skip, don't fail
        stderr_lines = result.stderr.strip().splitlines()
        stderr_first_line = stderr_lines[0] if stderr_lines else "unknown error"
        print(
            f"  tofu plan: {Colors.YELLOW}skipped{Colors.NC} (could not run plan)\n"
            f"             {stderr_first_line}\n"
            f"             Set AWS_PROFILE to your admin profile and re-run to check for drift."
        )
        return None


def cmd_verify() -> int:
    """Check tool versions and AWS profiles. Returns 0 if all pass, 1 otherwise."""
    tools_ok = _check_tools()

    if not tools_ok:
        print(
            f"\n{Colors.RED}Tool checks failed.{Colors.NC} "
            "Install missing tools before continuing.",
            file=sys.stderr,
        )
        return 1

    config_ok = _check_deployer_config()
    aws_ok = _check_aws_profiles()

    if not config_ok or not aws_ok:
        if not aws_ok:
            print(
                f"\n{Colors.RED}AWS profile checks failed.{Colors.NC} "
                "Run 'uv run python bin/init.py setup-profiles' to configure profiles.",
                file=sys.stderr,
            )
        return 1

    # All core checks passed — try bootstrap plan check
    bootstrap_result = _check_bootstrap_plan()

    if bootstrap_result is False:
        print(
            f"\n{Colors.YELLOW}Bootstrap has drift.{Colors.NC} "
            "Run 'tofu plan' in your bootstrap directory to review.",
            file=sys.stderr,
        )
        return 1

    print(f"\n{Colors.GREEN}All checks passed.{Colors.NC}")
    return 0
