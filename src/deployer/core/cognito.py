"""Cognito user management utilities."""

import platform
import secrets
import string
import subprocess
from datetime import datetime


def generate_temp_password(length: int = 16) -> str:
    """Generate a secure temporary password that meets Cognito requirements.

    The password will contain at least one uppercase letter, one lowercase
    letter, and one digit.

    Args:
        length: Desired password length (minimum 8).

    Returns:
        Generated password string.
    """
    # Ensure we have at least one of each required character type
    password = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
    ]

    # Fill the rest with a mix of allowed characters
    alphabet = string.ascii_letters + string.digits
    password.extend(secrets.choice(alphabet) for _ in range(length - 3))

    # Shuffle to avoid predictable positions
    password_list = list(password)
    secrets.SystemRandom().shuffle(password_list)

    return "".join(password_list)


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard.

    Args:
        text: Text to copy.

    Returns:
        True on success, False otherwise.
    """
    system = platform.system()

    try:
        if system == "Darwin":  # macOS
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
        elif system == "Linux":
            # Try xclip first, then xsel
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode(),
                    check=True,
                )
                return True
            except FileNotFoundError:
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode(),
                    check=True,
                )
                return True
        elif system == "Windows":
            subprocess.run(["clip"], input=text.encode(), check=True)
            return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return False


def format_welcome_message(
    environment: str,
    email: str,
    password: str,
    url: str | None,
    is_temporary: bool = True,
) -> str:
    """Format a welcome message for a new user.

    Args:
        environment: Environment name.
        email: Email address for the new user.
        password: Password for the new user.
        url: Login URL (optional).
        is_temporary: Whether the password is temporary.

    Returns:
        Formatted welcome message string.
    """
    lines = [
        f"You've been granted access to the {environment} environment.",
        "",
        "Your credentials:",
        f"  Email: {email}",
        f"  Password: {password}",
    ]

    if is_temporary:
        lines.append("")
        lines.append("You'll be prompted to set a new password on first login.")

    if url:
        lines.append("")
        lines.append(f"Login URL: {url}")

    return "\n".join(lines)


def format_user(user: dict) -> dict:
    """Extract relevant fields from a Cognito user record.

    Args:
        user: Raw Cognito user record.

    Returns:
        Dictionary with extracted user fields.
    """
    attributes = {attr["Name"]: attr["Value"] for attr in user.get("Attributes", [])}

    created = user.get("UserCreateDate")
    if created and isinstance(created, (int, float)):
        created = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M")

    last_modified = user.get("UserLastModifiedDate")
    if last_modified and isinstance(last_modified, (int, float)):
        last_modified = datetime.fromtimestamp(last_modified).strftime("%Y-%m-%d %H:%M")

    return {
        "username": user.get("Username", ""),
        "email": attributes.get("email", ""),
        "status": user.get("UserStatus", ""),
        "enabled": user.get("Enabled", True),
        "created": created,
        "last_modified": last_modified,
    }
