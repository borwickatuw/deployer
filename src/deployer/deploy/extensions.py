"""Create PostgreSQL extensions via Lambda before running migrations.

Some extensions (e.g., pg_bigm) require rds_superuser to CREATE, which the
migrate user does not have. The db-users Lambda connects as the RDS master
user and can create extensions on our behalf.

This module is called early in the deploy pipeline, before migrations run.
"""

import json

import boto3
from botocore.exceptions import ClientError

from ..utils import log, log_error, log_success, log_warning


def create_database_extensions(
    config: dict,
    env_config: dict,
    region: str,
    dry_run: bool = False,
) -> None:
    """Invoke the db-users Lambda to create PostgreSQL extensions.

    Args:
        config: Raw deploy.toml dict (has config["database"]["extensions"])
        env_config: Resolved config.toml dict (has env_config["database"]["extensions_lambda"])
        region: AWS region
        dry_run: If True, log what would happen without invoking

    Raises:
        RuntimeError: If the Lambda invocation fails or returns an error
    """
    # Read extensions from deploy.toml
    extensions = config.get("database", {}).get("extensions", [])
    if not extensions:
        return

    log(f"Creating database extensions: {', '.join(extensions)}")

    # Read lambda function name from config.toml
    lambda_name = env_config.get("database", {}).get("extensions_lambda")
    if not lambda_name:
        log_error(
            "deploy.toml declares database extensions, but config.toml is missing "
            "[database] extensions_lambda."
        )
        print()
        print("  Your deploy.toml declares:")
        print("    [database]")
        print(f"    extensions = {json.dumps(extensions)}")
        print()
        print("  But your environment's config.toml needs:")
        print("    [database]")
        print('    extensions_lambda = "${tofu:db_users_lambda_function_name}"')
        print()
        print("  Steps to fix:")
        print("    1. Add the extensions_lambda line to your config.toml")
        print("    2. Add the db_users_lambda_function_name output to your main.tf")
        print("    3. Run 'tofu apply' to create the output")
        raise RuntimeError("Missing extensions_lambda in config.toml [database] section")

    if dry_run:
        log_warning(
            f"DRY RUN: Would invoke Lambda '{lambda_name}' to create extensions: {extensions}"
        )
        return

    payload = {
        "action": "create_extensions",
        "extensions": extensions,
    }

    try:
        client = boto3.client("lambda", region_name=region)
        response = client.invoke(
            FunctionName=lambda_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_message = e.response["Error"]["Message"]

        if error_code == "ResourceNotFoundException":
            log_error(f"Lambda function '{lambda_name}' not found.")
            print()
            print("  The extensions_lambda in your config.toml points to a Lambda")
            print("  function that doesn't exist. This usually means:")
            print("    - The tofu output has not been applied yet")
            print("    - The Lambda function was deleted")
            print()
            print("  Run 'tofu apply' in your environment directory, then retry.")
            raise RuntimeError(f"Lambda function '{lambda_name}' not found") from e

        if error_code == "AccessDeniedException":
            log_error(f"Permission denied invoking Lambda '{lambda_name}'.")
            print()
            print("  The deploy role does not have lambda:InvokeFunction permission")
            print("  for this Lambda function. Apply the bootstrap IAM changes:")
            print("    cd deployer-environments/bootstrap-staging")
            print("    tofu apply")
            raise RuntimeError(f"Access denied invoking Lambda '{lambda_name}'") from e

        log_error(f"Failed to invoke Lambda '{lambda_name}': {error_code} - {error_message}")
        raise RuntimeError(f"Lambda invocation failed: {error_code} - {error_message}") from e
    except Exception as e:
        log_error(f"Unexpected error invoking Lambda '{lambda_name}': {e}")
        print()
        print("  Check your AWS credentials and network connectivity.")
        raise RuntimeError(f"Failed to invoke extensions Lambda: {e}") from e

    # Check for Lambda-level errors (function error, not invocation error)
    if "FunctionError" in response:
        error_payload = json.loads(response["Payload"].read().decode())
        error_type = error_payload.get("errorType", "Unknown")
        error_message = error_payload.get("errorMessage", "No details")

        log_error(f"Lambda '{lambda_name}' returned an error: {error_type}")
        print(f"  {error_message}")
        print()
        print("  The Lambda function ran but failed to create extensions.")
        print("  Check the Lambda's CloudWatch logs for details:")
        print(f"    aws logs tail /aws/lambda/{lambda_name} --since 5m")
        raise RuntimeError(f"Extensions Lambda failed: {error_type} - {error_message}")

    # Parse successful response
    result = json.loads(response["Payload"].read().decode())
    created = result.get("extensions", [])
    log_success(f"Extensions ready: {', '.join(created)}")
