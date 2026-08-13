"""Tests for deployer.aws.cognito.

These assert on the argv emitted to the `aws` CLI. The `aws_cli` fixture
patches run_command at the deployer.aws.cli seam every module here shares, so
no `aws` binary and no credentials are involved.
"""

import json

import pytest

from deployer.aws import cognito
from deployer.utils import AWS_REGION

POOL = "us-west-2_abc123"
USER = "alice@example.com"


class TestGetUserPoolName:
    """Tests for get_user_pool_name."""

    def test_emitted_argv(self, aws_cli):
        """describe-user-pool is called with the pool id and the region last."""
        aws_cli.replies((True, json.dumps({"UserPool": {"Name": "myapp-staging"}})))
        cognito.get_user_pool_name(POOL)
        assert aws_cli.argv == [
            "aws",
            "cognito-idp",
            "describe-user-pool",
            "--user-pool-id",
            POOL,
            "--region",
            AWS_REGION,
        ]

    def test_returns_pool_name(self, aws_cli):
        """The name is read out of the UserPool object."""
        aws_cli.replies((True, json.dumps({"UserPool": {"Name": "myapp-staging"}})))
        assert cognito.get_user_pool_name(POOL) == "myapp-staging"

    def test_none_when_command_fails(self, aws_cli):
        """A failed describe answers None."""
        aws_cli.replies((False, "AccessDeniedException"))
        assert cognito.get_user_pool_name(POOL) is None

    def test_none_when_output_is_not_json(self, aws_cli):
        """Unparseable output answers None instead of raising JSONDecodeError."""
        aws_cli.replies((True, "not json"))
        assert cognito.get_user_pool_name(POOL) is None

    def test_none_when_pool_has_no_name(self, aws_cli):
        """A response without a Name answers None."""
        aws_cli.replies((True, json.dumps({"UserPool": {}})))
        assert cognito.get_user_pool_name(POOL) is None


class TestListUsers:
    """Tests for list_users."""

    def test_emitted_argv(self, aws_cli):
        """list-users is called with the pool id and the region last."""
        aws_cli.replies((True, json.dumps({"Users": []})))
        cognito.list_users(POOL)
        assert aws_cli.argv == [
            "aws",
            "cognito-idp",
            "list-users",
            "--user-pool-id",
            POOL,
            "--region",
            AWS_REGION,
        ]

    def test_returns_users(self, aws_cli):
        """The Users list comes back as-is."""
        aws_cli.replies((True, json.dumps({"Users": [{"Username": USER}]})))
        assert cognito.list_users(POOL) == [{"Username": USER}]

    def test_follows_pagination(self, aws_cli):
        """A PaginationToken drives a second call carrying that token."""
        aws_cli.replies(
            (True, json.dumps({"Users": [{"Username": "one"}], "PaginationToken": "tok"})),
            (True, json.dumps({"Users": [{"Username": "two"}]})),
        )
        users = cognito.list_users(POOL)
        assert users == [{"Username": "one"}, {"Username": "two"}]
        assert aws_cli.calls[1] == [
            "aws",
            "cognito-idp",
            "list-users",
            "--user-pool-id",
            POOL,
            "--pagination-token",
            "tok",
            "--region",
            AWS_REGION,
        ]

    def test_returns_what_it_has_on_failure(self, aws_cli):
        """A failed page ends the walk, keeping the pages already collected."""
        aws_cli.replies(
            (True, json.dumps({"Users": [{"Username": "one"}], "PaginationToken": "tok"})),
            (False, "ThrottlingException"),
        )
        assert cognito.list_users(POOL) == [{"Username": "one"}]


class TestCreateUser:
    """Tests for create_user."""

    def test_emitted_argv_suppresses_email_by_default(self, aws_cli):
        """Attributes, the SUPPRESS action and the password all precede --region."""
        cognito.create_user(POOL, USER, USER, "TempPass1!")
        assert aws_cli.argv == [
            "aws",
            "cognito-idp",
            "admin-create-user",
            "--user-pool-id",
            POOL,
            "--username",
            USER,
            "--user-attributes",
            f"Name=email,Value={USER}",
            "Name=email_verified,Value=true",
            "--message-action",
            "SUPPRESS",
            "--temporary-password",
            "TempPass1!",
            "--region",
            AWS_REGION,
        ]

    def test_message_action_omitted_when_not_suppressing(self, aws_cli):
        """Without suppress_email, Cognito sends its own welcome email."""
        cognito.create_user(POOL, USER, USER, "TempPass1!", suppress_email=False)
        assert "--message-action" not in aws_cli.argv

    def test_success(self, aws_cli):
        """A created user reports success with no error text."""
        assert cognito.create_user(POOL, USER, USER, "TempPass1!") == (True, "")

    def test_existing_user_gets_a_readable_message(self, aws_cli):
        """UsernameExistsException is translated for the operator."""
        aws_cli.replies((False, "An error occurred (UsernameExistsException) when calling"))
        success, error = cognito.create_user(POOL, USER, USER, "TempPass1!")
        assert success is False
        assert error == f"User '{USER}' already exists"

    def test_other_errors_pass_through(self, aws_cli):
        """Any other failure returns the raw AWS error text."""
        aws_cli.replies((False, "InvalidPasswordException: too short"))
        assert cognito.create_user(POOL, USER, USER, "x") == (
            False,
            "InvalidPasswordException: too short",
        )


class TestAdminUserActions:
    """Tests for the three pool-and-username admin operations."""

    @pytest.mark.parametrize(
        ("func", "operation"),
        [
            (cognito.delete_user, "admin-delete-user"),
            (cognito.disable_user, "admin-disable-user"),
            (cognito.enable_user, "admin-enable-user"),
        ],
    )
    def test_emitted_argv(self, aws_cli, func, operation):
        """Each maps to its own cognito-idp subcommand and nothing else."""
        func(POOL, USER)
        assert aws_cli.argv == [
            "aws",
            "cognito-idp",
            operation,
            "--user-pool-id",
            POOL,
            "--username",
            USER,
            "--region",
            AWS_REGION,
        ]

    @pytest.mark.parametrize(
        "func",
        [cognito.delete_user, cognito.disable_user, cognito.enable_user],
    )
    def test_success(self, aws_cli, func):
        """Success reports no error text."""
        assert func(POOL, USER) == (True, "")

    @pytest.mark.parametrize(
        "func",
        [cognito.delete_user, cognito.disable_user, cognito.enable_user],
    )
    def test_missing_user_gets_a_readable_message(self, aws_cli, func):
        """UserNotFoundException is translated for all three."""
        aws_cli.replies((False, "An error occurred (UserNotFoundException) when calling"))
        assert func(POOL, USER) == (False, f"User '{USER}' not found")

    @pytest.mark.parametrize(
        "func",
        [cognito.delete_user, cognito.disable_user, cognito.enable_user],
    )
    def test_other_errors_pass_through(self, aws_cli, func):
        """Any other failure returns the raw AWS error text."""
        aws_cli.replies((False, "AccessDeniedException"))
        assert func(POOL, USER) == (False, "AccessDeniedException")


class TestSetUserPassword:
    """Tests for set_user_password."""

    def test_emitted_argv_temporary_by_default(self, aws_cli):
        """Without permanent=True the --permanent flag is absent."""
        cognito.set_user_password(POOL, USER, "NewPass1!")
        assert aws_cli.argv == [
            "aws",
            "cognito-idp",
            "admin-set-user-password",
            "--user-pool-id",
            POOL,
            "--username",
            USER,
            "--password",
            "NewPass1!",
            "--region",
            AWS_REGION,
        ]

    def test_permanent_flag_precedes_region(self, aws_cli):
        """--permanent is appended before the region flag."""
        cognito.set_user_password(POOL, USER, "NewPass1!", permanent=True)
        assert aws_cli.argv[-3:] == ["--permanent", "--region", AWS_REGION]

    def test_missing_user_gets_a_readable_message(self, aws_cli):
        """UserNotFoundException is translated here too."""
        aws_cli.replies((False, "An error occurred (UserNotFoundException) when calling"))
        assert cognito.set_user_password(POOL, USER, "NewPass1!") == (
            False,
            f"User '{USER}' not found",
        )
