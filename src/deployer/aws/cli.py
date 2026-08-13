"""The shared surface for this package's `aws` CLI commands.

Three modules here drive AWS through the `aws` CLI rather than boto3 —
cognito (7 commands), rds (3) and cloudwatch (1) — and every one of those 11
hand-built the same argv shape, ending in `--region AWS_REGION`. These helpers
are that shape, in one place.

aws/ecs.py and aws/ssm.py are deliberately absent: they take a boto3 client and
never shell out, so they have no argv to share.
"""

import json

from ..utils import AWS_REGION, run_command


def aws_command(*args: str) -> list[str]:
    """Build an `aws` CLI argv with the standard --region flag.

    Args:
        args: Command words and flags, e.g. ("rds", "stop-db-instance",
            "--db-instance-identifier", instance_id).

    Returns:
        The full argv, starting with "aws" and ending with the region flag.
    """
    return ["aws", *args, "--region", AWS_REGION]


def run_aws(*args: str) -> tuple[bool, str]:
    """Run an `aws` CLI command with --region.

    Args:
        args: Command words and flags, as for aws_command().

    Returns:
        Tuple of (success, output). On failure output is the error text, which
        callers grep for specific AWS exception names — that is why this, and
        not run_aws_json(), is the workhorse.
    """
    return run_command(aws_command(*args))


# The parsed dict is the answer to a query; None means "no answer" for either
# reason it can fail. Whether query helpers should raise instead is Phase 53i's
# call for the whole repo — see the standing suppression on rds.get_status().
# That suppression is not needed here: measured, the check does not fire.
def run_aws_json(*args: str) -> dict | None:
    """Run an `aws` CLI command and parse its JSON output.

    Args:
        args: Command words and flags, as for aws_command().

    Returns:
        The parsed response, or None if the command failed or its output was
        not valid JSON.
    """
    success, output = run_aws(*args)
    if not success:
        return None

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None
