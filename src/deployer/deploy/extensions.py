"""Create PostgreSQL extensions via Lambda before running migrations.

Some extensions (e.g., pg_bigm) require rds_superuser to CREATE, which the
migrate user does not have. The db-users Lambda connects as the RDS master
user and can create extensions on our behalf.

This module is called early in the deploy pipeline, before migrations run.
"""

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ..aws import ssm
from ..utils import log, log_error, log_success, log_warning, print_with_advice


def _extensions_state_param_name(app_name: str, environment: str) -> str:
    """SSM parameter recording the extensions last created for a database."""
    return f"/{app_name}/{environment}/db-extensions"


def _current_extensions_state(env_config: dict, extensions: list[str]) -> str:
    """The declared extensions plus the database they were created in.

    Including the database identity makes a pointed-at-a-new-database deploy
    re-run the Lambda instead of skipping on a stale record.
    """
    database = env_config.get("database", {})
    return json.dumps(
        {
            "db": [database.get("host"), database.get("name")],
            "extensions": sorted(extensions),
        },
        sort_keys=True,
    )


def create_database_extensions(
    config: dict,
    env_config: dict,
    region: str,
    *,
    app_name: str,
    environment: str,
    dry_run: bool = False,
) -> None:
    """Invoke the db-users Lambda to create PostgreSQL extensions.

    Args:
        config: Raw deploy.toml dict (has config["database"]["extensions"])
        env_config: Resolved config.toml dict (has env_config["database"]["extensions_lambda"])
        region: AWS region
        app_name: Application name; keys the SSM record backing the
            skip-when-unchanged check.
        environment: Environment name; the other half of that key.
        dry_run: If True, log what would happen without invoking

    Raises:
        RuntimeError: If the Lambda invocation fails or returns an error
    """
    extensions = config.get("database", {}).get("extensions", [])
    if not extensions:
        return

    log(f"Creating database extensions: {', '.join(extensions)}")

    lambda_name = _require_lambda_name(env_config, extensions)

    if dry_run:
        log_warning(
            f"DRY RUN: Would invoke Lambda '{lambda_name}' to create extensions: {extensions}"
        )
        return

    param_name = _extensions_state_param_name(app_name, environment)
    state = _current_extensions_state(env_config, extensions)
    stored, _error = ssm.get_parameter(param_name)
    if stored == state:
        log_success(f"Extensions unchanged ({', '.join(sorted(extensions))}), skipping")
        return

    response = _invoke_extensions_lambda(lambda_name, extensions, region)
    _raise_on_function_error(response, lambda_name)

    result = json.loads(response["Payload"].read().decode())
    created = result.get("extensions", [])
    log_success(f"Extensions ready: {', '.join(created)}")

    success, error = ssm.put_parameter(
        name=param_name,
        value=state,
        description="Extensions created by the last deploy (skip detection)",
        overwrite=True,
    )
    if not success:
        log_warning(f"Failed to store extensions state: {error}")


def _require_lambda_name(env_config: dict, extensions: list[str]) -> str:
    """Read the extensions Lambda name from config.toml.

    Args:
        env_config: Resolved config.toml dict.
        extensions: The extensions deploy.toml declared, echoed back in the advice.

    Returns:
        The Lambda function name.

    Raises:
        RuntimeError: If config.toml has no [database] extensions_lambda.
    """
    lambda_name = env_config.get("database", {}).get("extensions_lambda")
    if lambda_name:
        return lambda_name

    print_with_advice(
        "deploy.toml declares database extensions, but config.toml is missing "
        "[database] extensions_lambda.",
        "  Your deploy.toml declares:",
        "    [database]",
        f"    extensions = {json.dumps(extensions)}",
        "",
        "  But your environment's config.toml needs:",
        "    [database]",
        '    extensions_lambda = "${tofu:db_users_lambda_function_name}"',
        "",
        "  Steps to fix:",
        "    1. Add the extensions_lambda line to your config.toml",
        "    2. Add the db_users_lambda_function_name output to your main.tf",
        "    3. Run 'tofu apply' to create the output",
    )
    raise RuntimeError("Missing extensions_lambda in config.toml [database] section")


def _invoke_extensions_lambda(lambda_name: str, extensions: list[str], region: str) -> dict:
    """Invoke the db-users Lambda synchronously and return its raw response.

    Args:
        lambda_name: The Lambda function to invoke.
        extensions: Extension names passed in the create_extensions payload.
        region: AWS region.

    Returns:
        The boto3 invoke() response. A Lambda-level failure is reported in its
        FunctionError key, not raised here — see _raise_on_function_error().

    Raises:
        RuntimeError: If the invocation itself fails.
    """
    payload = {
        "action": "create_extensions",
        "extensions": extensions,
    }

    try:
        client = boto3.client("lambda", region_name=region)
        return client.invoke(
            FunctionName=lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_message = e.response["Error"]["Message"]

        if error_code == "ResourceNotFoundException":
            print_with_advice(
                f"Lambda function '{lambda_name}' not found.",
                "  The extensions_lambda in your config.toml points to a Lambda",
                "  function that doesn't exist. This usually means:",
                "    - The tofu output has not been applied yet",
                "    - The Lambda function was deleted",
                "",
                "  Run 'tofu apply' in your environment directory, then retry.",
            )
            raise RuntimeError(f"Lambda function '{lambda_name}' not found") from e

        if error_code == "AccessDeniedException":
            print_with_advice(
                f"Permission denied invoking Lambda '{lambda_name}'.",
                "  The deploy role does not have lambda:InvokeFunction permission",
                "  for this Lambda function. Apply the bootstrap IAM changes:",
                "    cd deployer-environments/bootstrap-staging",
                "    tofu apply",
            )
            raise RuntimeError(f"Access denied invoking Lambda '{lambda_name}'") from e

        log_error(f"Failed to invoke Lambda '{lambda_name}': {error_code} - {error_message}")
        raise RuntimeError(f"Lambda invocation failed: {error_code} - {error_message}") from e
    except BotoCoreError as e:
        # Narrowed from `except Exception`: BotoCoreError *is* the connectivity
        # and configuration family, so this advice is now attributable. Under
        # the old catch a bug inside boto3, or a TypeError in this module, was
        # blamed on the operator's network.
        print_with_advice(
            f"Could not reach Lambda '{lambda_name}': {e}",
            "  Check your AWS credentials and network connectivity.",
        )
        raise RuntimeError(f"Failed to invoke extensions Lambda: {e}") from e


def _raise_on_function_error(response: dict, lambda_name: str) -> None:
    """Raise if the Lambda ran but reported an error.

    A function error is distinct from an invocation error: the call succeeded,
    so boto3 raised nothing, but the handler failed.

    Args:
        response: The boto3 invoke() response.
        lambda_name: The Lambda that was invoked, named in the advice.

    Raises:
        RuntimeError: If the response carries a FunctionError.
    """
    if "FunctionError" not in response:
        return

    error_payload = json.loads(response["Payload"].read().decode())
    error_type = error_payload.get("errorType", "Unknown")
    error_message = error_payload.get("errorMessage", "No details")

    print_with_advice(
        f"Lambda '{lambda_name}' returned an error: {error_type}",
        f"  {error_message}",
        "",
        "  The Lambda function ran but failed to create extensions.",
        "  Check the Lambda's CloudWatch logs for details:",
        f"    aws logs tail /aws/lambda/{lambda_name} --since 5m",
    )
    raise RuntimeError(f"Extensions Lambda failed: {error_type} - {error_message}")
