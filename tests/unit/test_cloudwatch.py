"""Tests for deployer.aws.cloudwatch — the CloudWatch Logs search surface.

These pin the error contract DECISIONS.md 2026-08-18 "Error Contracts" sets
for query helpers: an empty result means "no matching events", and a failed
search raises rather than borrowing that answer.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from deployer.aws import cloudwatch


@pytest.fixture
def logs_client():
    """A boto3 CloudWatch Logs client stub."""
    return MagicMock()


class TestSearchLogsForOom:
    """Tests for search_logs_for_oom and the _search_logs_boto3 it delegates to."""

    def test_matching_events_are_flattened(self, logs_client):
        logs_client.filter_log_events.return_value = {
            "events": [
                {"timestamp": 1, "message": " Killed \n", "logStreamName": "web/abc"},
            ]
        }

        result = cloudwatch.search_logs_for_oom(
            "/ecs/myapp", 0, 1000, cloudwatch_client=logs_client, log_stream_prefix="web/"
        )

        assert result == [{"timestamp": 1, "message": "Killed", "log_stream": "web/abc"}]

    def test_the_stream_prefix_is_passed_through(self, logs_client):
        logs_client.filter_log_events.return_value = {"events": []}

        cloudwatch.search_logs_for_oom(
            "/ecs/myapp", 0, 1000, cloudwatch_client=logs_client, log_stream_prefix="worker/"
        )

        assert logs_client.filter_log_events.call_args.kwargs["logStreamNamePrefix"] == "worker/"

    def test_no_prefix_means_no_prefix_filter(self, logs_client):
        logs_client.filter_log_events.return_value = {"events": []}

        cloudwatch.search_logs_for_oom(
            "/ecs/myapp", 0, 1000, cloudwatch_client=logs_client, log_stream_prefix=None
        )

        assert "logStreamNamePrefix" not in logs_client.filter_log_events.call_args.kwargs

    def test_pagination_follows_the_next_token(self, logs_client):
        logs_client.filter_log_events.side_effect = [
            {
                "events": [{"timestamp": 1, "message": "a", "logStreamName": "s"}],
                "nextToken": "t2",
            },
            {"events": [{"timestamp": 2, "message": "b", "logStreamName": "s"}]},
        ]

        result = cloudwatch.search_logs_for_oom(
            "/ecs/myapp", 0, 1000, cloudwatch_client=logs_client, log_stream_prefix=None
        )

        assert [e["message"] for e in result] == ["a", "b"]
        assert logs_client.filter_log_events.call_count == 2

    def test_no_matches_is_an_empty_list(self, logs_client):
        logs_client.filter_log_events.return_value = {"events": []}

        result = cloudwatch.search_logs_for_oom(
            "/ecs/myapp", 0, 1000, cloudwatch_client=logs_client, log_stream_prefix=None
        )

        assert result == []

    def test_raises_on_failure_rather_than_reporting_no_matches(self, logs_client):
        """A permissions gap must not read as "no OOM events in the logs"."""
        logs_client.filter_log_events.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Denied"}},
            "FilterLogEvents",
        )

        with pytest.raises(RuntimeError, match="Could not search CloudWatch log group"):
            cloudwatch.search_logs_for_oom(
                "/ecs/myapp", 0, 1000, cloudwatch_client=logs_client, log_stream_prefix=None
            )


class TestGetLogEvents:
    """The three-way contract: events, absence (None), or a raise."""

    def test_events_are_returned(self, aws_cli):
        aws_cli.replies((True, '{"events": [{"timestamp": 1, "message": "hi"}]}'))
        assert cloudwatch.get_log_events("/ecs/app", "web/web/abc") == [
            {"timestamp": 1, "message": "hi"}
        ]

    def test_empty_stream_is_an_empty_list_not_none(self, aws_cli):
        aws_cli.replies((True, '{"events": []}'))
        assert cloudwatch.get_log_events("/ecs/app", "web/web/abc") == []

    def test_absent_events_key_is_an_empty_list(self, aws_cli):
        aws_cli.replies((True, "{}"))
        assert cloudwatch.get_log_events("/ecs/app", "web/web/abc") == []

    def test_missing_stream_is_none(self, aws_cli):
        aws_cli.replies((False, "An error occurred (ResourceNotFoundException) when calling ..."))
        assert cloudwatch.get_log_events("/ecs/app", "web/web/abc") is None

    def test_other_failures_raise_and_carry_the_aws_text(self, aws_cli):
        aws_cli.replies((False, "An error occurred (AccessDeniedException) when calling ..."))
        with pytest.raises(RuntimeError, match="AccessDeniedException"):
            cloudwatch.get_log_events("/ecs/app", "web/web/abc")

    def test_non_json_output_raises(self, aws_cli):
        aws_cli.replies((True, "not json"))
        with pytest.raises(RuntimeError, match="not JSON"):
            cloudwatch.get_log_events("/ecs/app", "web/web/abc")

    def test_limit_and_start_time_reach_the_cli(self, aws_cli):
        aws_cli.replies((True, '{"events": []}'))
        cloudwatch.get_log_events("/ecs/app", "web/web/abc", start_time=1700, limit=7)

        argv = aws_cli.argv
        assert argv[argv.index("--limit") + 1] == "7"
        assert argv[argv.index("--start-time") + 1] == "1700"

    def test_start_time_is_omitted_when_none(self, aws_cli):
        aws_cli.replies((True, '{"events": []}'))
        cloudwatch.get_log_events("/ecs/app", "web/web/abc")
        assert "--start-time" not in aws_cli.argv


class TestGetTaskLogs:
    """get_task_logs builds the ECS stream name and inherits the contract."""

    def test_stream_name_follows_the_ecs_convention(self, aws_cli):
        aws_cli.replies((True, '{"events": []}'))
        cloudwatch.get_task_logs("/ecs/app", "web", "django", "abc123")

        argv = aws_cli.argv
        assert argv[argv.index("--log-stream-name") + 1] == "web/django/abc123"

    def test_read_failure_propagates(self, aws_cli):
        aws_cli.replies((False, "An error occurred (AccessDeniedException) when calling ..."))
        with pytest.raises(RuntimeError):
            cloudwatch.get_task_logs("/ecs/app", "web", "django", "abc123")
