"""CloudWatch Logs operations for emergency response.

Provides functions for:
- Scanning recent logs for errors
- Filtering log events
"""

import time
from typing import Any

import boto3
from botocore.exceptions import ClientError


def get_logs_client() -> Any:
    """Get boto3 CloudWatch Logs client."""
    return boto3.client("logs")


def scan_logs_for_errors(
    log_group_name: str,
    lookback_minutes: int = 60,
    patterns: list[str] | None = None,
    max_results: int = 100,
) -> list[dict]:
    """Scan CloudWatch logs for error patterns.

    Args:
        log_group_name: CloudWatch log group name
        lookback_minutes: How far back to scan
        patterns: List of patterns to search for (default: ERROR, Exception, Traceback)
        max_results: Maximum number of events to return

    Returns:
        List of matching log events:
        [
            {
                "timestamp": "2026-02-04T12:00:00Z",
                "message": "ERROR: Something went wrong",
                "log_stream": "ecs/web/abc123",
            },
            ...
        ]
    """
    if patterns is None:
        patterns = ["ERROR", "Exception", "Traceback", "CRITICAL"]

    client = get_logs_client()
    result = []

    # Calculate start time
    start_time = int((time.time() - (lookback_minutes * 60)) * 1000)

    # Build filter pattern - CloudWatch uses space-separated OR
    filter_pattern = " ".join(f'?"{p}"' for p in patterns)

    try:
        response = client.filter_log_events(
            logGroupName=log_group_name,
            startTime=start_time,
            filterPattern=filter_pattern,
            limit=max_results,
        )

        for event in response.get("events", []):
            timestamp = event.get("timestamp", 0)
            # Convert milliseconds to ISO format
            if timestamp:
                from datetime import datetime, timezone

                dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
                timestamp = dt.isoformat()

            result.append(
                {
                    "timestamp": timestamp,
                    "message": event.get("message", ""),
                    "log_stream": event.get("logStreamName", ""),
                }
            )

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            # Log group doesn't exist
            pass
        else:
            raise

    return result


def get_log_groups_for_environment(environment: str) -> list[str]:
    """Get CloudWatch log group names for an environment.

    Args:
        environment: Environment name

    Returns:
        List of log group names
    """
    client = get_logs_client()
    result = []
    prefix = f"/ecs/{environment}"

    try:
        paginator = client.get_paginator("describe_log_groups")
        for page in paginator.paginate(logGroupNamePrefix=prefix):
            for group in page.get("logGroups", []):
                result.append(group.get("logGroupName", ""))
    except ClientError as e:
        # Log the error for debugging but don't fail
        import sys

        print(f"  Warning: Error listing log groups: {e}", file=sys.stderr)

    return result
