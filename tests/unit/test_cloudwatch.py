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
