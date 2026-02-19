"""AWS CloudWatch operations."""

import json
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

from ..utils import AWS_REGION, run_command


def get_container_insights_metrics(
    cloudwatch_client: Any,
    cluster_name: str,
    service_name: str,
    metric_name: str,
    start_time: datetime,
    end_time: datetime,
    period: int = 3600,
) -> list[float]:
    """Query CloudWatch Container Insights metrics for an ECS service.

    Args:
        cloudwatch_client: boto3 CloudWatch client.
        cluster_name: Name of the ECS cluster.
        service_name: Name of the ECS service.
        metric_name: Metric to query (e.g., "CpuUtilized", "MemoryUtilized").
        start_time: Start of the time range.
        end_time: End of the time range.
        period: Aggregation period in seconds (default: 3600 = 1 hour).

    Returns:
        List of average values for each data point.
    """
    try:
        response = cloudwatch_client.get_metric_statistics(
            Namespace="ECS/ContainerInsights",
            MetricName=metric_name,
            Dimensions=[
                {"Name": "ClusterName", "Value": cluster_name},
                {"Name": "ServiceName", "Value": service_name},
            ],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=["Average", "Maximum"],
            Unit="Percent",
        )
        return [dp["Average"] for dp in response.get("Datapoints", [])]
    except ClientError:
        return []


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
        List of log event dicts with 'timestamp' and 'message' keys,
        or None if the stream doesn't exist or an error occurred.
    """
    cmd = [
        "aws",
        "logs",
        "get-log-events",
        "--log-group-name",
        log_group,
        "--log-stream-name",
        log_stream,
        "--limit",
        str(limit),
        "--region",
        AWS_REGION,
    ]

    if start_time:
        cmd.extend(["--start-time", str(start_time)])

    success, output = run_command(cmd)
    if not success:
        return None

    try:
        data = json.loads(output)
        return data.get("events", [])
    except json.JSONDecodeError:
        return None


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
        List of log event dicts, or None if not found.
    """
    # ECS log stream format: {prefix}/{container_name}/{task_id}
    log_stream = f"{stream_prefix}/{container_name}/{task_id}"
    return get_log_events(log_group, log_stream, limit=limit)


def search_logs_for_oom(
    log_group: str,
    start_time_ms: int,
    end_time_ms: int,
    cloudwatch_client: Any = None,
    log_stream_prefix: str | None = None,
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
        cloudwatch_client: Optional boto3 CloudWatch Logs client.
        log_stream_prefix: Optional prefix to filter log streams (e.g., "celery/").

    Returns:
        List of OOM event dicts with timestamp, message, and log_stream.
    """
    # Patterns that indicate OOM kills
    filter_pattern = '?"SIGKILL" ?"signal 9" ?"exit code 137" ?"WorkerLostError" ?"OutOfMemory" ?"killed" ?"OOMKilled"'

    if cloudwatch_client:
        return _search_logs_boto3(
            log_group,
            start_time_ms,
            end_time_ms,
            filter_pattern,
            cloudwatch_client,
            log_stream_prefix,
        )
    return _search_logs_cli(
        log_group, start_time_ms, end_time_ms, filter_pattern, log_stream_prefix
    )


def _search_logs_cli(
    log_group: str,
    start_time_ms: int,
    end_time_ms: int,
    filter_pattern: str,
    log_stream_prefix: str | None = None,
) -> list[dict]:
    """Search logs using AWS CLI."""
    cmd = [
        "aws",
        "logs",
        "filter-log-events",
        "--log-group-name",
        log_group,
        "--start-time",
        str(start_time_ms),
        "--end-time",
        str(end_time_ms),
        "--filter-pattern",
        filter_pattern,
        "--limit",
        "100",
        "--region",
        AWS_REGION,
    ]

    if log_stream_prefix:
        cmd.extend(["--log-stream-name-prefix", log_stream_prefix])

    success, output = run_command(cmd)
    if not success:
        return []

    try:
        data = json.loads(output)
        events = data.get("events", [])
        return [
            {
                "timestamp": e.get("timestamp"),
                "message": e.get("message", "").strip(),
                "log_stream": e.get("logStreamName", ""),
            }
            for e in events
        ]
    except json.JSONDecodeError:
        return []


def _search_logs_boto3(
    log_group: str,
    start_time_ms: int,
    end_time_ms: int,
    filter_pattern: str,
    client: Any,
    log_stream_prefix: str | None = None,
) -> list[dict]:
    """Search logs using boto3 client with pagination."""
    import sys

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

        for page_num in range(max_pages):
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
        print(f"  Warning: Could not search CloudWatch logs: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  Warning: Unexpected error searching logs: {e}", file=sys.stderr)
        return []
