"""Datetime formatting utilities for boto3 responses."""

from datetime import datetime


def format_iso(value: object) -> str | None:
    """Format a value as ISO 8601 string if it's a datetime.

    Handles boto3 responses that may return datetime objects or strings.
    Returns None if the value is None.

    Args:
        value: A datetime object, string, None, or other value.

    Returns:
        ISO 8601 string, str(value) for non-datetime, or None for None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
