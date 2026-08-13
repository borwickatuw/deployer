"""AWS Cognito user pool operations."""

from .cli import run_aws, run_aws_json


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


def _admin_user_action(operation: str, user_pool_id: str, username: str) -> tuple[bool, str]:
    """Run an admin operation whose only arguments are a pool and a username.

    Args:
        operation: The cognito-idp subcommand, e.g. "admin-delete-user".
        user_pool_id: The Cognito User Pool ID.
        username: The username to act on.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    success, output = run_aws(
        "cognito-idp",
        operation,
        "--user-pool-id",
        user_pool_id,
        "--username",
        username,
    )
    return _handle_user_not_found(success, output, username)


def get_user_pool_name(user_pool_id: str) -> str | None:
    """Get the display name of a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.

    Returns:
        Pool name string, or None if the pool can't be described
        (e.g., insufficient permissions).
    """
    data = run_aws_json("cognito-idp", "describe-user-pool", "--user-pool-id", user_pool_id)
    return data.get("UserPool", {}).get("Name") if data else None


def list_users(user_pool_id: str) -> list[dict]:
    """List all users in a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.

    Returns:
        List of raw user dicts from the Cognito API.
    """
    users: list[dict] = []
    pagination_token = None

    while True:
        args = ["cognito-idp", "list-users", "--user-pool-id", user_pool_id]
        if pagination_token:
            args.extend(["--pagination-token", pagination_token])

        data = run_aws_json(*args)
        if data is None:
            return users

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
    args = [
        "cognito-idp",
        "admin-create-user",
        "--user-pool-id",
        user_pool_id,
        "--username",
        username,
        "--user-attributes",
        f"Name=email,Value={email}",
        "Name=email_verified,Value=true",
    ]

    if suppress_email:
        args.extend(["--message-action", "SUPPRESS"])

    args.extend(["--temporary-password", password])

    success, output = run_aws(*args)

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
    return _admin_user_action("admin-delete-user", user_pool_id, username)


def disable_user(user_pool_id: str, username: str) -> tuple[bool, str]:
    """Disable a user in a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.
        username: The username to disable.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    return _admin_user_action("admin-disable-user", user_pool_id, username)


def enable_user(user_pool_id: str, username: str) -> tuple[bool, str]:
    """Enable a previously disabled user in a Cognito User Pool.

    Args:
        user_pool_id: The Cognito User Pool ID.
        username: The username to enable.

    Returns:
        Tuple of (success, error_message). Error message is empty on success.
    """
    return _admin_user_action("admin-enable-user", user_pool_id, username)


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
    args = [
        "cognito-idp",
        "admin-set-user-password",
        "--user-pool-id",
        user_pool_id,
        "--username",
        username,
        "--password",
        password,
    ]
    if permanent:
        args.append("--permanent")

    success, output = run_aws(*args)
    return _handle_user_not_found(success, output, username)
