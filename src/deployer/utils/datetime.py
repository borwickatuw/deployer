"""Datetime formatting utilities for boto3 responses."""

from datetime import datetime


# Leaf utility called within boto3 response processing; callers handle
# ClientError at their own boundaries.
# pysmelly: ignore inconsistent-error-handling  (re-evaluate-by: 2026-11 review)
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


def format_timestamp(value: str, fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    """Parse an ISO timestamp and reformat it, returning the original on failure.

    Args:
        value: ISO 8601 timestamp string (a trailing "Z" is accepted).
        fmt: strftime format for the result.

    Returns:
        The reformatted timestamp, or ``value`` unchanged if it cannot be parsed.
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime(fmt)
    except (ValueError, AttributeError):
        return value
