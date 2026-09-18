"""AWS CloudWatch operations."""

import json
from typing import Any

from botocore.exceptions import ClientError

from .cli import run_aws


def get_log_events(
    log_group: str,
    log_stream: str,
    start_time: int | None = None,
    limit: int = 100,
) -> list[dict] | None:
    """Fetch log events from a CloudWatch log stream.

    Args:
        log_group: CloudWatch log group name.
        log_stream: CloudWatch log stream name.
        start_time: Optional start timestamp in milliseconds since epoch.
        limit: Maximum number of events to return (default: 100).

    Returns:
        List of log event dicts with 'timestamp' and 'message' keys. ``[]``
        when the stream exists but has logged nothing yet, and None when the
        stream (or its group) does not exist — a task that has not written its
        first line is a normal answer, not a failure.

    Raises:
        RuntimeError: if CloudWatch could not be read for any other reason
            (credentials, permissions, connectivity) or answered with output
            that is not JSON. A failure to look is never reported as "no logs";
            the caller decides whether that is fatal.
    """
    args = [
        "logs",
        "get-log-events",
        "--log-group-name",
        log_group,
        "--log-stream-name",
        log_stream,
        "--limit",
        str(limit),
    ]

    if start_time:
        args.extend(["--start-time", str(start_time)])

    success, output = run_aws(*args)
    if not success:
        if "ResourceNotFoundException" in output:
            return None
        raise RuntimeError(f"Could not read {log_group}/{log_stream} from CloudWatch: {output}")

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"CloudWatch returned output that is not JSON for {log_group}/{log_stream}"
        ) from exc

    return data.get("events", [])


def get_task_logs(
    log_group: str,
    stream_prefix: str,
    container_name: str,
    task_id: str,
    limit: int = 100,
) -> list[dict] | None:
    """Fetch logs for an ECS task from CloudWatch.

    Args:
        log_group: CloudWatch log group name.
        stream_prefix: Log stream prefix (from task definition).
        container_name: Container name.
        task_id: ECS task ID (last segment of task ARN).
        limit: Maximum number of events to return.

    Returns:
        List of log event dicts, ``[]`` if the stream exists but is empty, or
        None if the task has not created its log stream yet.

    Raises:
        RuntimeError: if CloudWatch could not be read. See
            :func:`get_log_events` -- the contract is inherited unchanged.
    """
    # ECS log stream format: {prefix}/{container_name}/{task_id}
    log_stream = f"{stream_prefix}/{container_name}/{task_id}"
    return get_log_events(log_group, log_stream, limit=limit)


def search_logs_for_oom(
    log_group: str,
    start_time_ms: int,
    end_time_ms: int,
    cloudwatch_client: Any,
    log_stream_prefix: str | None,
) -> list[dict]:
    """Search CloudWatch Logs for OOM-related errors.

    Searches for patterns that indicate out-of-memory kills:
    - SIGKILL / signal 9
    - Exit code 137 (128 + 9)
    - WorkerLostError
    - OutOfMemory / OOM

    Args:
        log_group: CloudWatch log group name.
        start_time_ms: Start time in milliseconds since epoch.
        end_time_ms: End time in milliseconds since epoch.
        cloudwatch_client: boto3 CloudWatch Logs client.
        log_stream_prefix: Optional prefix to filter log streams (e.g., "celery/").

    Returns:
        List of OOM event dicts with timestamp, message, and log_stream. Empty
        only when the window genuinely contains no matching events.

    Raises:
        RuntimeError: If the log group could not be searched. An empty result
            meaning both "no OOM events" and "I could not look" is what
            DECISIONS.md 2026-08-18 "Error Contracts" forbids.
    """
    # Patterns that indicate OOM kills
    filter_pattern = (
        '?"SIGKILL" ?"signal 9" ?"exit code 137"'
        ' ?"WorkerLostError" ?"OutOfMemory" ?"killed" ?"OOMKilled"'
    )

    return _search_logs_boto3(
        log_group,
        start_time_ms,
        end_time_ms,
        filter_pattern,
        cloudwatch_client,
        log_stream_prefix,
    )


def _search_logs_boto3(
    log_group: str,
    start_time_ms: int,
    end_time_ms: int,
    filter_pattern: str,
    client: Any,
    log_stream_prefix: str | None = None,
) -> list[dict]:
    """Search logs using boto3 client with pagination.

    Returns:
        Matching log events, newest page first. Empty means no matches.

    Raises:
        RuntimeError: If the log group could not be searched.
    """
    all_events: list[dict] = []
    max_pages = 10  # Limit pagination to avoid runaway queries

    try:
        kwargs: dict[str, Any] = {
            "logGroupName": log_group,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
            "filterPattern": filter_pattern,
            "limit": 100,
        }

        if log_stream_prefix:
            kwargs["logStreamNamePrefix"] = log_stream_prefix

        for _ in range(max_pages):
            response = client.filter_log_events(**kwargs)
            events = response.get("events", [])

            for e in events:
                all_events.append(
                    {
                        "timestamp": e.get("timestamp"),
                        "message": e.get("message", "").strip(),
                        "log_stream": e.get("logStreamName", ""),
                    }
                )

            # Check for more pages
            next_token = response.get("nextToken")
            if not next_token or len(all_events) >= 100:
                break

            kwargs["nextToken"] = next_token

        return all_events

    except ClientError as e:
        raise RuntimeError(f"Could not search CloudWatch log group '{log_group}': {e}") from e
