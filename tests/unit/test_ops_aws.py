"""Tests for bin/ops.py's boto3 collectors — the functions the commands read from.

test_ops.py pins what each command *prints*, stubbing these collectors out.
This file pins the collectors themselves: which AWS call each makes, with
which parameters, how the response is flattened, and which failures are
raised on a refused read versus answered empty for genuine absence.

Backed by botocore's Stubber rather than moto: several of these operations
(describe_pending_maintenance_actions, describe_service_updates,
describe_image_scan_findings with findings) have no useful moto backend, and
the Stubber still validates every request and canned response against the
service model. ``ops.boto3`` is swapped for a namespace whose ``client()``
hands back the stubbed client, so no credentials or network are involved.
"""

import sys
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_ops_spec = spec_from_file_location("ops", bin_dir / "ops.py")
ops = module_from_spec(_ops_spec)
_ops_spec.loader.exec_module(ops)

ENV = "myapp-production"
RDS_ID = "myapp-production-db"
RDS_ARN = f"arn:aws:rds:us-west-2:123456789012:db:{RDS_ID}"
CACHE_ID = "myapp-production-cache"


@pytest.fixture
def aws(monkeypatch):
    """Return a factory: ``aws("elbv2")`` gives the Stubber ops.py's client will use.

    Every stubber is checked for unconsumed responses at teardown, so a test
    that queues a call the code no longer makes fails rather than passing.
    """
    stubbers: dict[str, Stubber] = {}

    def _client(service: str):
        return stubbers[service].client

    def _stub(service: str) -> Stubber:
        client = boto3.client(
            service,
            region_name="us-west-2",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",  # noqa: S106 - fake credential
        )
        stubber = Stubber(client)
        stubber.activate()
        stubbers[service] = stubber
        return stubber

    monkeypatch.setattr(ops, "boto3", SimpleNamespace(client=_client))
    yield _stub
    for stubber in stubbers.values():
        stubber.assert_no_pending_responses()


# =============================================================================
# get_target_health — cmd_health's collector
# =============================================================================


class TestGetTargetHealth:
    def test_each_target_is_flattened_to_id_port_state_reason_description(self, aws):
        aws("elbv2").add_response(
            "describe_target_health",
            {
                "TargetHealthDescriptions": [
                    {
                        "Target": {"Id": "10.0.1.5", "Port": 8000},
                        "TargetHealth": {"State": "healthy"},
                    },
                    {
                        "Target": {"Id": "10.0.2.7", "Port": 8000},
                        "TargetHealth": {
                            "State": "unhealthy",
                            "Reason": "Target.Timeout",
                            "Description": "Request timed out",
                        },
                    },
                ]
            },
            {"TargetGroupArn": "arn:tg"},
        )

        assert ops.get_target_health("arn:tg") == [
            {
                "target_id": "10.0.1.5",
                "port": 8000,
                "health_state": "healthy",
                "reason": None,
                "description": None,
            },
            {
                "target_id": "10.0.2.7",
                "port": 8000,
                "health_state": "unhealthy",
                "reason": "Target.Timeout",
                "description": "Request timed out",
            },
        ]

    def test_a_target_with_no_health_block_reads_as_unknown(self, aws):
        aws("elbv2").add_response(
            "describe_target_health",
            {"TargetHealthDescriptions": [{"Target": {"Id": "10.0.1.5"}}]},
        )

        (target,) = ops.get_target_health("arn:tg")
        assert target["health_state"] == "unknown"
        assert target["port"] == 0

    def test_an_empty_target_group_is_an_empty_list(self, aws):
        aws("elbv2").add_response("describe_target_health", {"TargetHealthDescriptions": []})

        assert ops.get_target_health("arn:tg") == []

    def test_a_refused_describe_raises_rather_than_reading_as_no_targets(self, aws):
        """It used to answer [] -- indistinguishable from an empty target group.

        cmd_health() then printed "No targets registered" and returned 0; it now
        reports the read failure and returns 1 (test_ops.py pins that half).
        """
        aws("elbv2").add_client_error("describe_target_health", "AccessDenied")

        with pytest.raises(RuntimeError, match="Could not read target health for arn:tg"):
            ops.get_target_health("arn:tg")


# =============================================================================
# scan_logs_for_errors / get_log_groups_for_environment — cmd_logs's collectors
# =============================================================================

NOW = 1_788_000_000.0  # seconds since the epoch, pinned via ops.time


class TestScanLogsForErrors:
    @pytest.fixture(autouse=True)
    def frozen_clock(self, monkeypatch):
        monkeypatch.setattr(ops, "time", SimpleNamespace(time=lambda: NOW))

    def test_the_filter_covers_the_four_default_patterns_over_the_lookback(self, aws):
        aws("logs").add_response(
            "filter_log_events",
            {"events": []},
            {
                "logGroupName": f"/ecs/{ENV}-web",
                "startTime": int((NOW - 30 * 60) * 1000),
                "filterPattern": '?"ERROR" ?"Exception" ?"Traceback" ?"CRITICAL"',
                "limit": 50,
            },
        )

        assert (
            ops.scan_logs_for_errors(f"/ecs/{ENV}-web", lookback_minutes=30, max_results=50) == []
        )

    def test_event_timestamps_become_utc_iso_strings(self, aws):
        stamp = datetime(2026, 8, 13, 9, 15, 30, tzinfo=UTC)
        aws("logs").add_response(
            "filter_log_events",
            {
                "events": [
                    {
                        "timestamp": int(stamp.timestamp() * 1000),
                        "message": "ERROR boom",
                        "logStreamName": "web/web/abc123",
                    },
                    {"message": "Traceback (most recent call last):"},
                ]
            },
        )

        assert ops.scan_logs_for_errors(f"/ecs/{ENV}-web") == [
            {
                "timestamp": "2026-08-13T09:15:30+00:00",
                "message": "ERROR boom",
                "log_stream": "web/web/abc123",
            },
            # A missing timestamp stays the integer 0; format_timestamp() in
            # cmd_logs then prints it verbatim.
            {"timestamp": 0, "message": "Traceback (most recent call last):", "log_stream": ""},
        ]

    def test_a_missing_log_group_reads_as_no_errors(self, aws):
        aws("logs").add_client_error("filter_log_events", "ResourceNotFoundException")

        assert ops.scan_logs_for_errors(f"/ecs/{ENV}-web") == []

    def test_any_other_failure_raises_runtime_error_with_the_aws_text(self, aws):
        """The collectors' shared contract: RuntimeError, which cmd_logs catches.

        It used to re-raise the raw ClientError, which nothing caught, so a
        denied filter_log_events ended logs (and audit) with a traceback.
        """
        aws("logs").add_client_error("filter_log_events", "AccessDeniedException")

        with pytest.raises(RuntimeError, match="AccessDeniedException") as exc_info:
            ops.scan_logs_for_errors(f"/ecs/{ENV}-web")
        assert isinstance(exc_info.value.__cause__, ClientError)


class TestGetLogGroupsForEnvironment:
    def test_every_page_under_the_ecs_prefix_is_collected(self, aws):
        stubber = aws("logs")
        stubber.add_response(
            "describe_log_groups",
            {
                "logGroups": [{"logGroupName": f"/ecs/{ENV}-web"}],
                "nextToken": "page-2",
            },
            {"logGroupNamePrefix": f"/ecs/{ENV}"},
        )
        stubber.add_response(
            "describe_log_groups",
            {"logGroups": [{"logGroupName": f"/ecs/{ENV}-worker"}]},
            {"logGroupNamePrefix": f"/ecs/{ENV}", "nextToken": "page-2"},
        )

        assert ops.get_log_groups_for_environment(ENV) == [
            f"/ecs/{ENV}-web",
            f"/ecs/{ENV}-worker",
        ]

    def test_a_refused_listing_raises_rather_than_reading_as_none(self, aws, capsys):
        """It used to warn on stderr and answer [], which cmd_logs then
        reported as "No log groups found" with exit 0."""
        aws("logs").add_client_error("describe_log_groups", "AccessDeniedException")

        with pytest.raises(RuntimeError, match=f"under /ecs/{ENV}: .*AccessDeniedException"):
            ops.get_log_groups_for_environment(ENV)
        assert capsys.readouterr().err == ""


# =============================================================================
# Pending maintenance — cmd_maintenance's collectors
# =============================================================================


class TestGetRdsPendingMaintenance:
    def test_actions_are_looked_up_by_the_instance_arn(self, aws):
        stubber = aws("rds")
        stubber.add_response(
            "describe_db_instances",
            {"DBInstances": [{"DBInstanceIdentifier": RDS_ID, "DBInstanceArn": RDS_ARN}]},
            {"DBInstanceIdentifier": RDS_ID},
        )
        stubber.add_response(
            "describe_pending_maintenance_actions",
            {
                "PendingMaintenanceActions": [
                    {
                        "ResourceIdentifier": RDS_ARN,
                        "PendingMaintenanceActionDetails": [
                            {
                                "Action": "system-update",
                                "Description": "Operating system update",
                                "AutoAppliedAfterDate": datetime(2026, 9, 1, tzinfo=UTC),
                                "OptInStatus": "pending",
                            },
                            {"Action": "db-upgrade"},
                        ],
                    }
                ]
            },
            {"ResourceIdentifier": RDS_ARN},
        )

        assert ops.get_rds_pending_maintenance(RDS_ID) == [
            {
                "action": "system-update",
                "description": "Operating system update",
                "auto_apply_after": "2026-09-01T00:00:00+00:00",
                "current_apply_date": None,
                "opt_in_status": "pending",
            },
            {
                "action": "db-upgrade",
                "description": "",
                "auto_apply_after": None,
                "current_apply_date": None,
                "opt_in_status": "",
            },
        ]

    def test_no_such_instance_asks_nothing_further(self, aws):
        aws("rds").add_response("describe_db_instances", {"DBInstances": []})

        assert ops.get_rds_pending_maintenance(RDS_ID) == []

    def test_a_refused_describe_raises_rather_than_reading_as_nothing_pending(self, aws):
        """A configured instance AWS will not describe is not "nothing pending"."""
        aws("rds").add_client_error("describe_db_instances", "DBInstanceNotFound")

        with pytest.raises(RuntimeError, match=f"{RDS_ID}: .*DBInstanceNotFound"):
            ops.get_rds_pending_maintenance(RDS_ID)

    def test_a_refused_action_listing_raises(self, aws):
        stubber = aws("rds")
        stubber.add_response(
            "describe_db_instances",
            {"DBInstances": [{"DBInstanceIdentifier": RDS_ID, "DBInstanceArn": RDS_ARN}]},
        )
        stubber.add_client_error("describe_pending_maintenance_actions", "AccessDenied")

        with pytest.raises(RuntimeError, match="AccessDenied"):
            ops.get_rds_pending_maintenance(RDS_ID)


class TestGetElasticachePendingMaintenance:
    def _cluster(self, stubber: Stubber, pending: dict | None = None) -> None:
        cluster = {"CacheClusterId": CACHE_ID}
        if pending is not None:
            cluster["PendingModifiedValues"] = pending
        stubber.add_response(
            "describe_cache_clusters",
            {"CacheClusters": [cluster]},
            {"CacheClusterId": CACHE_ID, "ShowCacheNodeInfo": True},
        )

    def _updates(self, stubber: Stubber, updates: list[dict]) -> None:
        stubber.add_response(
            "describe_service_updates",
            {"ServiceUpdates": updates},
            {"ServiceUpdateStatus": ["available", "scheduled"]},
        )

    def test_truthy_pending_modifications_come_first_then_service_updates(self, aws):
        """Service updates are listed account-wide: nothing filters them to this cluster."""
        stubber = aws("elasticache")
        self._cluster(
            stubber,
            {"NumCacheNodes": 3, "CacheNodeType": "cache.t4g.small", "EngineVersion": ""},
        )
        self._updates(
            stubber,
            [
                {
                    "ServiceUpdateName": "elasticache-20260901-001",
                    "ServiceUpdateDescription": "Engine patch",
                    "ServiceUpdateSeverity": "important",
                }
            ],
        )

        assert ops.get_elasticache_pending_maintenance(CACHE_ID) == [
            {
                "action": "modify",
                "description": "Pending NumCacheNodes change to 3",
                "severity": None,
            },
            {
                "action": "modify",
                "description": "Pending CacheNodeType change to cache.t4g.small",
                "severity": None,
            },
            {
                "action": "elasticache-20260901-001",
                "description": "Engine patch",
                "severity": "important",
            },
        ]

    def test_a_refused_service_update_listing_raises_rather_than_answering_half(self, aws):
        """It used to keep the modifications and drop the updates silently,
        reporting a half-read answer as the whole one."""
        stubber = aws("elasticache")
        self._cluster(stubber, {"NumCacheNodes": 3})
        stubber.add_client_error("describe_service_updates", "AccessDenied")

        with pytest.raises(RuntimeError, match="service updates: .*AccessDenied"):
            ops.get_elasticache_pending_maintenance(CACHE_ID)

    def test_a_cluster_with_nothing_pending_still_lists_service_updates(self, aws):
        stubber = aws("elasticache")
        self._cluster(stubber)
        self._updates(stubber, [])

        assert ops.get_elasticache_pending_maintenance(CACHE_ID) == []

    def test_no_such_cluster_asks_nothing_further(self, aws):
        aws("elasticache").add_response("describe_cache_clusters", {"CacheClusters": []})

        assert ops.get_elasticache_pending_maintenance(CACHE_ID) == []

    def test_a_refused_describe_raises_rather_than_reading_as_nothing_pending(self, aws):
        aws("elasticache").add_client_error("describe_cache_clusters", "CacheClusterNotFound")

        with pytest.raises(RuntimeError, match=f"{CACHE_ID}: .*CacheClusterNotFound"):
            ops.get_elasticache_pending_maintenance(CACHE_ID)


# get_all_pending_maintenance() is gone: cmd_maintenance() calls the two
# collectors itself so each is its own render boundary. Which resource is asked
# about is pinned by TestCmdMaintenance in test_ops.py.


# =============================================================================
# ECR — cmd_ecr's collectors
# =============================================================================

REPO = f"{ENV}-web"


class TestListRepositoriesForEnvironment:
    def test_one_call_names_every_service_repository(self, aws):
        aws("ecr").add_response(
            "describe_repositories",
            {
                "repositories": [
                    {"repositoryName": f"{ENV}-web"},
                    {"repositoryName": f"{ENV}-worker"},
                ]
            },
            {"repositoryNames": [f"{ENV}-web", f"{ENV}-worker"]},
        )

        assert ops.list_repositories_for_environment(ENV, ["web", "worker"]) == [
            f"{ENV}-web",
            f"{ENV}-worker",
        ]

    def test_one_missing_repository_falls_back_to_asking_one_at_a_time(self, aws):
        """AWS fails the whole batch if any name is missing; the survivors are kept."""
        stubber = aws("ecr")
        stubber.add_client_error("describe_repositories", "RepositoryNotFoundException")
        stubber.add_response(
            "describe_repositories",
            {"repositories": [{"repositoryName": f"{ENV}-web"}]},
            {"repositoryNames": [f"{ENV}-web"]},
        )
        stubber.add_client_error(
            "describe_repositories",
            "RepositoryNotFoundException",
            expected_params={"repositoryNames": [f"{ENV}-worker"]},
        )

        assert ops.list_repositories_for_environment(ENV, ["web", "worker"]) == [f"{ENV}-web"]

    def test_any_other_refusal_raises_rather_than_reading_as_no_repositories(self, aws):
        """It used to answer [], which cmd_ecr() reported as "No ECR repositories
        found" with exit 0 -- a security scan that passed without looking."""
        aws("ecr").add_client_error("describe_repositories", "AccessDeniedException")

        with pytest.raises(RuntimeError, match=f"repositories for {ENV}: .*AccessDenied"):
            ops.list_repositories_for_environment(ENV, ["web"])

    def test_a_refusal_during_the_one_at_a_time_fallback_raises(self, aws):
        """Only RepositoryNotFoundException means "absent"; nothing else is skipped."""
        stubber = aws("ecr")
        stubber.add_client_error("describe_repositories", "RepositoryNotFoundException")
        stubber.add_client_error(
            "describe_repositories",
            "AccessDeniedException",
            expected_params={"repositoryNames": [f"{ENV}-web"]},
        )

        with pytest.raises(RuntimeError, match=f"repository {ENV}-web: .*AccessDenied"):
            ops.list_repositories_for_environment(ENV, ["web", "worker"])


class TestGetRepositoryScanSummary:
    def test_the_newest_images_come_first_with_their_counts(self, aws):
        aws("ecr").add_response(
            "describe_images",
            {
                "imageDetails": [
                    {
                        "imageDigest": "sha256:old",
                        "imageTags": ["v1", "stable"],
                        "imagePushedAt": datetime(2026, 8, 1, tzinfo=UTC),
                        "imageScanStatus": {"status": "COMPLETE"},
                        "imageScanFindingsSummary": {
                            "findingSeverityCounts": {"CRITICAL": 1, "HIGH": 4, "LOW": 9}
                        },
                    },
                    {
                        "imageDigest": "sha256:new",
                        "imagePushedAt": datetime(2026, 8, 13, tzinfo=UTC),
                    },
                ]
            },
            {"repositoryName": REPO, "maxResults": 5},
        )

        assert ops.get_repository_scan_summary(REPO) == [
            {
                "image_tag": "(untagged)",
                "image_digest": "sha256:new",
                "pushed_at": "2026-08-13T00:00:00+00:00",
                "scan_status": "NOT_SCANNED",
                "critical_count": 0,
                "high_count": 0,
            },
            {
                "image_tag": "v1",
                "image_digest": "sha256:old",
                "pushed_at": "2026-08-01T00:00:00+00:00",
                "scan_status": "COMPLETE",
                "critical_count": 1,
                "high_count": 4,
            },
        ]

    def test_cmd_ecr_asks_for_one_image(self, aws):
        aws("ecr").add_response(
            "describe_images", {"imageDetails": []}, {"repositoryName": REPO, "maxResults": 1}
        )

        assert ops.get_repository_scan_summary(REPO, max_images=1) == []

    def test_a_refused_describe_raises_rather_than_reading_as_no_images(self, aws):
        """It used to answer [], and cmd_ecr() skipped the repository silently."""
        aws("ecr").add_client_error("describe_images", "AccessDeniedException")

        with pytest.raises(RuntimeError, match=f"images in {REPO}: .*AccessDenied"):
            ops.get_repository_scan_summary(REPO)


class TestGetImageScanFindings:
    def test_only_critical_and_high_findings_are_kept(self, aws):
        aws("ecr").add_response(
            "describe_image_scan_findings",
            {
                "imageId": {"imageDigest": "sha256:abc", "imageTag": "v1"},
                "imageScanStatus": {"status": "COMPLETE"},
                "imageScanFindings": {
                    "findingSeverityCounts": {"CRITICAL": 1, "MEDIUM": 1},
                    "findings": [
                        {
                            "name": "CVE-2026-0001",
                            "severity": "CRITICAL",
                            "description": "Remote code execution",
                            "uri": "https://cve.example.com/CVE-2026-0001",
                        },
                        {"name": "CVE-2026-0002", "severity": "MEDIUM"},
                    ],
                },
            },
            {"repositoryName": REPO, "imageId": {"imageTag": "v1"}},
        )

        assert ops.get_image_scan_findings(REPO, "v1") == {
            "image_digest": "sha256:abc",
            "scan_status": "COMPLETE",
            "vulnerability_counts": {"CRITICAL": 1, "MEDIUM": 1},
            "findings": [
                {
                    "name": "CVE-2026-0001",
                    "severity": "CRITICAL",
                    "description": "Remote code execution",
                    "uri": "https://cve.example.com/CVE-2026-0001",
                }
            ],
        }

    @pytest.mark.parametrize(
        ("code", "status"),
        [
            ("ScanNotFoundException", "NOT_SCANNED"),
            ("ImageNotFoundException", "IMAGE_NOT_FOUND"),
            ("AccessDeniedException", "ERROR: AccessDeniedException"),
        ],
    )
    def test_a_failed_lookup_is_reported_in_the_scan_status(self, aws, code, status):
        aws("ecr").add_client_error("describe_image_scan_findings", code)

        assert ops.get_image_scan_findings(REPO, "v1") == {
            "image_digest": None,
            "scan_status": status,
            "vulnerability_counts": {},
            "findings": [],
        }


# =============================================================================
# _describe_live_scaling — cmd_status's one direct boto3 call
# =============================================================================


class TestDescribeLiveScaling:
    CLUSTER = "myapp-production-cluster"

    def _expected_params(self) -> dict:
        return {
            "ServiceNamespace": "ecs",
            "ResourceIds": [f"service/{self.CLUSTER}/worker"],
            "ScalableDimension": "ecs:service:DesiredCount",
        }

    def test_a_registered_target_reports_its_bounds(self, aws):
        aws("application-autoscaling").add_response(
            "describe_scalable_targets",
            {
                "ScalableTargets": [
                    {
                        "ServiceNamespace": "ecs",
                        "ResourceId": f"service/{self.CLUSTER}/worker",
                        "ScalableDimension": "ecs:service:DesiredCount",
                        "MinCapacity": 1,
                        "MaxCapacity": 8,
                        "RoleARN": "arn:aws:iam::123456789012:role/autoscaling",
                        "CreationTime": datetime(2026, 8, 1, tzinfo=UTC),
                    }
                ]
            },
            self._expected_params(),
        )

        assert ops._describe_live_scaling(self.CLUSTER, "worker") == "live: min=1, max=8"

    def test_no_registered_target_says_deploy_applies_it(self, aws):
        aws("application-autoscaling").add_response(
            "describe_scalable_targets", {"ScalableTargets": []}, self._expected_params()
        )

        assert ops._describe_live_scaling(self.CLUSTER, "worker") == (
            "live: no scalable target registered (deploy applies it)"
        )

    def test_a_refused_describe_is_reported_in_the_line(self, aws):
        aws("application-autoscaling").add_client_error(
            "describe_scalable_targets", "AccessDeniedException", "not authorized"
        )

        line = ops._describe_live_scaling(self.CLUSTER, "worker")
        assert line.startswith("live: unable to read (")
        assert "AccessDeniedException" in line
