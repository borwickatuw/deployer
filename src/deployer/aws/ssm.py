"""AWS SSM Parameter Store operations."""

import boto3
from botocore.exceptions import ClientError


def _get_client():
    return boto3.client("ssm")


# pysmelly: ignore inconsistent-error-handling — returns (success, error) tuple, callers check
def put_parameter(
    name: str,
    value: str,
    description: str = "",
    overwrite: bool = True,
) -> tuple[bool, str | None]:
    """Create or update an SSM parameter.

    Args:
        name: Parameter name (e.g., /myapp/staging/SECRET_KEY)
        value: Parameter value
        description: Optional description
        overwrite: Whether to overwrite existing parameter

    Returns:
        Tuple of (success, error_message)
    """
    client = _get_client()

    try:
        kwargs = {
            "Name": name,
            "Value": value,
            "Type": "SecureString",
            "Overwrite": overwrite,
        }
        if description:
            kwargs["Description"] = description

        client.put_parameter(**kwargs)
        return True, None
    except ClientError as e:
        return False, str(e)


# pysmelly: ignore inconsistent-error-handling — returns (value, error) tuple, callers check
def get_parameter(name: str) -> tuple[str | None, str | None]:
    """Get an SSM parameter value.

    Args:
        name: Parameter name

    Returns:
        Tuple of (value, error_message). Value is None on error.
    """
    client = _get_client()

    try:
        response = client.get_parameter(Name=name, WithDecryption=True)
        return response["Parameter"]["Value"], None
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return None, f"Parameter not found: {name}"
        return None, str(e)


def delete_parameter(name: str) -> tuple[bool, str | None]:
    """Delete an SSM parameter.

    Args:
        name: Parameter name

    Returns:
        Tuple of (success, error_message)
    """
    client = _get_client()

    try:
        client.delete_parameter(Name=name)
        return True, None
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return False, f"Parameter not found: {name}"
        return False, str(e)


def list_parameters(path_prefix: str) -> tuple[list[dict], str | None]:
    """List SSM parameters under a path prefix.

    Args:
        path_prefix: Parameter path prefix (e.g., /myapp/staging)

    Returns:
        Tuple of (parameters, error_message). Each parameter is a dict with
        name, description, type, last_modified, and version.
    """
    client = _get_client()

    try:
        parameters = []
        paginator = client.get_paginator("describe_parameters")

        # Ensure path_prefix starts with /
        if not path_prefix.startswith("/"):
            path_prefix = f"/{path_prefix}"

        for page in paginator.paginate(
            ParameterFilters=[
                {
                    "Key": "Path",
                    "Option": "Recursive",
                    "Values": [path_prefix],
                }
            ]
        ):
            for param in page["Parameters"]:
                parameters.append(
                    {
                        "name": param["Name"],
                        "description": param.get("Description", ""),
                        "type": param["Type"],
                        "last_modified": param.get("LastModifiedDate"),
                        "version": param.get("Version"),
                    }
                )

        return parameters, None
    except ClientError as e:
        return [], str(e)


def parameter_exists(name: str) -> bool:
    """Check if an SSM parameter exists.

    Args:
        name: Parameter name

    Returns:
        True if parameter exists, False otherwise
    """
    client = _get_client()

    try:
        client.get_parameter(Name=name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return False
        raise
