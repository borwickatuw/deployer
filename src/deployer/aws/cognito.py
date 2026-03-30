"""AWS Cognito user pool operations."""

import json

from ..utils import AWS_REGION, run_command


def _handle_user_not_found(success: bool, output: str, username: str) -> tuple[bool, str]:
    """Process command result, handling UserNotFoundException consistently.

    Args:
        success: Whether the command succeeded.
        output: Command output (may contain error message).
        username: Username for error message.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    if not success:
        if "UserNotFoundException" in output:
            return False, f"User '{username}' not found"
        return False, output
    return True, ""


def get_user_pool_name(user_pool_id: str) -> str | None:
    """Get the display name of a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.

    Returns:
        Pool name string, or None if the pool can't be described
        (e.g., insufficient permissions).
    """
    cmd = [
        "aws",
        "cognito-idp",
        "describe-user-pool",
        "--user-pool-id",
        user_pool_id,
        "--region",
        AWS_REGION,
    ]
    success, output = run_command(cmd)
    if not success:
        return None
    data = json.loads(output)
    return data.get("UserPool", {}).get("Name")


def list_users(user_pool_id: str) -> list[dict]:
    """List all users in a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.

    Returns:
        List of raw user dicts from the Cognito API.
    """
    users = []
    pagination_token = None

    while True:
        cmd = [
            "aws",
            "cognito-idp",
            "list-users",
            "--user-pool-id",
            user_pool_id,
            "--region",
            AWS_REGION,
        ]

        if pagination_token:
            cmd.extend(["--pagination-token", pagination_token])

        success, output = run_command(cmd)

        if not success:
            return users

        data = json.loads(output)
        users.extend(data.get("Users", []))

        pagination_token = data.get("PaginationToken")
        if not pagination_token:
            break

    return users


def create_user(
    user_pool_id: str,
    username: str,
    email: str,
    password: str,
    suppress_email: bool = True,
) -> tuple[bool, str]:
    """Create a new user in a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.
        username: The username for the new user.
        email: The email address for the new user.
        password: Temporary password for the new user.
        suppress_email: If True, suppress the Cognito welcome email.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    cmd = [
        "aws",
        "cognito-idp",
        "admin-create-user",
        "--user-pool-id",
        user_pool_id,
        "--username",
        username,
        "--user-attributes",
        f"Name=email,Value={email}",
        "Name=email_verified,Value=true",
        "--region",
        AWS_REGION,
    ]

    if suppress_email:
        cmd.extend(["--message-action", "SUPPRESS"])

    cmd.extend(["--temporary-password", password])

    success, output = run_command(cmd)

    if not success:
        if "UsernameExistsException" in output:
            return False, f"User '{username}' already exists"
        return False, output

    return True, ""


def delete_user(user_pool_id: str, username: str) -> tuple[bool, str]:
    """Delete a user from a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.
        username: The username to delete.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    cmd = [
        "aws",
        "cognito-idp",
        "admin-delete-user",
        "--user-pool-id",
        user_pool_id,
        "--username",
        username,
        "--region",
        AWS_REGION,
    ]
    success, output = run_command(cmd)
    return _handle_user_not_found(success, output, username)


def disable_user(user_pool_id: str, username: str) -> tuple[bool, str]:
    """Disable a user in a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.
        username: The username to disable.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    cmd = [
        "aws",
        "cognito-idp",
        "admin-disable-user",
        "--user-pool-id",
        user_pool_id,
        "--username",
        username,
        "--region",
        AWS_REGION,
    ]
    success, output = run_command(cmd)
    return _handle_user_not_found(success, output, username)


def enable_user(user_pool_id: str, username: str) -> tuple[bool, str]:
    """Enable a previously disabled user in a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.
        username: The username to enable.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    cmd = [
        "aws",
        "cognito-idp",
        "admin-enable-user",
        "--user-pool-id",
        user_pool_id,
        "--username",
        username,
        "--region",
        AWS_REGION,
    ]
    success, output = run_command(cmd)
    return _handle_user_not_found(success, output, username)


def set_user_password(
    user_pool_id: str,
    username: str,
    password: str,
    permanent: bool = False,
) -> tuple[bool, str]:
    """Set or reset a user's password in a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.
        username: The username.
        password: The new password.
        permanent: If True, the password is permanent. If False, user must change on next login.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    cmd = [
        "aws",
        "cognito-idp",
        "admin-set-user-password",
        "--user-pool-id",
        user_pool_id,
        "--username",
        username,
        "--password",
        password,
        "--region",
        AWS_REGION,
    ]
    if permanent:
        cmd.append("--permanent")
    success, output = run_command(cmd)
    return _handle_user_not_found(success, output, username)
