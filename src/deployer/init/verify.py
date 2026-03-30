"""Verify tool versions and AWS profile configuration."""

import re
import sys

from ..utils.colors import Colors
from ..utils.subprocess import run_command

# (display_name, command, version_args, version_regex, min_version_tuple_or_None)
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


def _check_aws_profiles() -> bool:
    """Check AWS profile configuration. Returns True if all pass."""
    # Import here to avoid import errors when boto3 isn't available
    from ..utils.aws_profile import validate_aws_profile

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


def cmd_verify() -> int:
    """Check tool versions and AWS profiles. Returns 0 if all pass, 1 otherwise."""
    tools_ok = _check_tools()

    if not tools_ok:
        print(
            f"\n{Colors.RED}Tool checks failed.{Colors.NC} "
            "Install missing tools before continuing.",
            file=sys.stderr,
        )
        print("Skipping AWS profile checks.", file=sys.stderr)
        return 1

    aws_ok = _check_aws_profiles()

    if not aws_ok:
        print(
            f"\n{Colors.RED}AWS profile checks failed.{Colors.NC} "
            "Run 'uv run python bin/init.py setup-profiles' to configure profiles.",
            file=sys.stderr,
        )
        return 1

    print(f"\n{Colors.GREEN}All checks passed.{Colors.NC}")
    return 0
