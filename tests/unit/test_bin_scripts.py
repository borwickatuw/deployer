"""Tests for bin/ scripts (cognito.py, environment.py)."""

import string
import sys
from pathlib import Path

import pytest

# Add bin directory to path
bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

# Import using importlib for bin scripts
from importlib.util import module_from_spec, spec_from_file_location

_access_spec = spec_from_file_location("cognito", bin_dir / "cognito.py")
access = module_from_spec(_access_spec)
_access_spec.loader.exec_module(access)

_env_spec = spec_from_file_location("environment", bin_dir / "environment.py")
env_mgr = module_from_spec(_env_spec)
_env_spec.loader.exec_module(env_mgr)


class TestGenerateTempPassword:
    """Tests for generate_temp_password function."""

    def test_password_length(self):
        """Test that password has correct length."""
        password = access.generate_temp_password(16)
        assert len(password) == 16

        password = access.generate_temp_password(20)
        assert len(password) == 20

    def test_password_has_uppercase(self):
        """Test that password contains at least one uppercase letter."""
        # Generate multiple passwords to ensure consistency
        for _ in range(10):
            password = access.generate_temp_password(16)
            assert any(c in string.ascii_uppercase for c in password)

    def test_password_has_lowercase(self):
        """Test that password contains at least one lowercase letter."""
        for _ in range(10):
            password = access.generate_temp_password(16)
            assert any(c in string.ascii_lowercase for c in password)

    def test_password_has_digit(self):
        """Test that password contains at least one digit."""
        for _ in range(10):
            password = access.generate_temp_password(16)
            assert any(c in string.digits for c in password)

    def test_password_is_random(self):
        """Test that passwords are different each time."""
        passwords = [access.generate_temp_password(16) for _ in range(10)]
        unique_passwords = set(passwords)
        # Should be highly unlikely to get duplicates
        assert len(unique_passwords) == 10


class TestRunCommand:
    """Tests for run_command function."""

    def test_successful_command(self):
        """Test running a successful command."""
        from deployer.utils import run_command

        success, output = run_command(["echo", "hello"])
        assert success is True
        assert "hello" in output

    def test_failed_command(self):
        """Test running a command that fails."""
        from deployer.utils import run_command

        success, output = run_command(["false"])
        assert success is False

    def test_command_with_cwd(self, tmp_path):
        """Test running a command with working directory."""
        from deployer.utils import run_command

        success, output = run_command(["pwd"], cwd=str(tmp_path))
        assert success is True
        assert str(tmp_path) in output

    def test_nonexistent_command(self):
        """Test running a command that doesn't exist."""
        from deployer.utils import run_command

        success, output = run_command(["nonexistent_command_12345"])
        assert success is False


class TestFormatWelcomeMessage:
    """Tests for format_welcome_message function."""

    def test_basic_message(self):
        """Test basic welcome message formatting."""
        message = access.format_welcome_message(
            environment="myapp-staging",
            email="alice@example.com",
            password="SecurePass123",
            url="https://staging.example.com",
            is_temporary=True,
        )

        assert "myapp-staging" in message
        assert "alice@example.com" in message
        assert "SecurePass123" in message
        assert "https://staging.example.com" in message
        assert "new password" in message.lower()  # prompt about new password

    def test_permanent_password_message(self):
        """Test message when password is permanent."""
        message = access.format_welcome_message(
            environment="staging",
            email="bob@example.com",
            password="Permanent123",
            url=None,
            is_temporary=False,
        )

        assert "bob@example.com" in message
        # Should not mention changing password
        assert "prompted to" not in message.lower() or "change" not in message.lower()

    def test_message_without_url(self):
        """Test message when URL is not provided."""
        message = access.format_welcome_message(
            environment="staging",
            email="user@example.com",
            password="Pass123",
            url=None,
            is_temporary=True,
        )

        assert "Login URL" not in message


class TestFormatUser:
    """Tests for format_user function."""

    def test_format_user_with_all_fields(self):
        """Test formatting a user with all fields present."""
        user = {
            "Username": "alice@example.com",
            "Attributes": [
                {"Name": "email", "Value": "alice@example.com"},
            ],
            "UserStatus": "CONFIRMED",
            "Enabled": True,
            "UserCreateDate": 1704067200,  # 2024-01-01
            "UserLastModifiedDate": 1704153600,  # 2024-01-02
        }

        result = access.format_user(user)

        assert result["username"] == "alice@example.com"
        assert result["email"] == "alice@example.com"
        assert result["status"] == "CONFIRMED"
        assert result["enabled"] is True

    def test_format_user_missing_attributes(self):
        """Test formatting a user with missing attributes."""
        user = {
            "Username": "bob",
            "UserStatus": "UNCONFIRMED",
            "Enabled": False,
        }

        result = access.format_user(user)

        assert result["username"] == "bob"
        assert result["email"] == ""
        assert result["status"] == "UNCONFIRMED"
        assert result["enabled"] is False


class TestStagingRunCommand:
    """Tests verifying run_command works correctly."""

    def test_run_command_exists_in_utils(self):
        """Test that run_command is available from deployer.utils."""
        from deployer.utils import run_command

        # Function should be importable and callable
        assert callable(run_command)


class TestEnvironmentGetAllEnvironments:
    """Test get_all_environments in environment.py."""

    def test_function_exists_and_works(self, tmp_path):
        """Verify the function exists in environment.py and works."""
        (tmp_path / "test-staging").mkdir()
        (tmp_path / "test-staging" / "config.toml").write_text("")
        (tmp_path / "test-production").mkdir()
        (tmp_path / "test-production" / "config.toml").write_text("")

        result = env_mgr.get_all_environments(tmp_path)

        assert "test-staging" in result
        assert "test-production" in result
