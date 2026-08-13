"""Tests for bin/capacity-report.py — the OOM-since-last-deploy report."""

import sys
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("capacity_report", bin_dir / "capacity-report.py")
capacity = module_from_spec(_spec)
_spec.loader.exec_module(capacity)

DEPLOYED_AT = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)


class _Service:
    """Stand-in for the ecs.get_services() service record."""

    def __init__(self, name="web", last_deployment_at=None, task_definition="myapp-web:7"):
        self.name = name
        self.last_deployment_at = last_deployment_at
        self.task_definition = task_definition


class _EcsClient:
    """boto3 ECS client stub returning fixed task-definition sizing."""

    def __init__(self, cpu=256, memory=512, fail=False):
        self._cpu = cpu
        self._memory = memory
        self._fail = fail

    def describe_task_definition(self, taskDefinition):  # noqa: N803 — boto3's parameter name
        if self._fail:
            raise RuntimeError("task definition deregistered")
        return {"taskDefinition": {"cpu": self._cpu, "memory": self._memory}}


class TestRecommendMemory:
    """Tests for _recommend_memory()."""

    def test_picks_the_first_fargate_value_at_or_above_1_5x(self):
        assert capacity._recommend_memory(256, 512) == 1024

    def test_unknown_cpu_has_no_recommendation(self):
        assert capacity._recommend_memory(3, 512) is None

    def test_beyond_the_top_value_returns_the_top_value(self):
        valid = capacity.FARGATE_VALID_MEMORY[256]
        assert capacity._recommend_memory(256, valid[-1]) == valid[-1]


class TestDeploymentCutoff:
    """Tests for _deployment_cutoff()."""

    def test_none_is_none(self):
        assert capacity._deployment_cutoff(None) is None

    def test_datetime_passes_through(self):
        assert capacity._deployment_cutoff(DEPLOYED_AT) is DEPLOYED_AT

    def test_iso_string_is_parsed(self):
        assert capacity._deployment_cutoff("2026-08-10T14:30:00+00:00") == DEPLOYED_AT

    def test_trailing_z_is_parsed(self):
        assert capacity._deployment_cutoff("2026-08-10T14:30:00Z") == DEPLOYED_AT

    def test_unparseable_string_is_none(self):
        assert capacity._deployment_cutoff("last tuesday") is None

    def test_non_datetime_value_is_none(self):
        assert capacity._deployment_cutoff(1754836200) is None


class TestCountEcsOom:
    """Tests for _count_ecs_oom()."""

    def test_no_oom_reports_a_clean_line(self, monkeypatch, capsys):
        monkeypatch.setattr(capacity, "get_oom_events", lambda *_a, **_kw: [])

        total = capacity._count_ecs_oom(
            [_Service(last_deployment_at=DEPLOYED_AT)], "myapp-cluster", _EcsClient(), 7
        )

        assert total == 0
        assert "web: no OOM kills since deploy (2026-08-10 14:30 UTC)" in capsys.readouterr().out

    def test_missing_deployment_time_reports_unknown(self, monkeypatch, capsys):
        monkeypatch.setattr(capacity, "get_oom_events", lambda *_a, **_kw: [])

        capacity._count_ecs_oom([_Service()], "myapp-cluster", _EcsClient(), 7)

        assert "since deploy (unknown)" in capsys.readouterr().out

    def test_oom_events_are_counted_and_a_size_is_recommended(self, monkeypatch, capsys):
        monkeypatch.setattr(capacity, "get_oom_events", lambda *_a, **_kw: ["e1", "e2"])

        total = capacity._count_ecs_oom(
            [_Service(last_deployment_at=DEPLOYED_AT)], "myapp-cluster", _EcsClient(), 7
        )

        assert total == 2
        out = capsys.readouterr().out
        assert "2 OOM kill(s)" in out
        assert "Current memory: 512MB (recommend >= 1024MB)" in out

    def test_totals_across_services(self, monkeypatch, capsys):
        monkeypatch.setattr(capacity, "get_oom_events", lambda *_a, **_kw: ["e"])

        total = capacity._count_ecs_oom(
            [_Service("web"), _Service("worker")], "myapp-cluster", _EcsClient(), 7
        )

        assert total == 2
        assert capsys.readouterr().out.count("OOM kill(s)") == 2

    def test_undescribable_task_definition_falls_back_to_256_512(self, monkeypatch, capsys):
        monkeypatch.setattr(capacity, "get_oom_events", lambda *_a, **_kw: ["e"])

        total = capacity._count_ecs_oom([_Service()], "myapp-cluster", _EcsClient(fail=True), 7)

        assert total == 1
        assert "Current memory: 512MB" in capsys.readouterr().out

    def test_the_deployment_cutoff_is_passed_to_get_oom_events(self, monkeypatch):
        seen = {}

        def capture(_cluster, _service, since_hours, since_datetime, ecs_client):
            seen["since_hours"] = since_hours
            seen["since_datetime"] = since_datetime
            return []

        monkeypatch.setattr(capacity, "get_oom_events", capture)

        capacity._count_ecs_oom(
            [_Service(last_deployment_at=DEPLOYED_AT)], "myapp-cluster", _EcsClient(), 3
        )

        assert seen == {"since_hours": 72, "since_datetime": DEPLOYED_AT}


class TestCountLogOom:
    """Tests for _count_log_oom()."""

    WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
    END_MS = 1_760_000_000_000

    def test_no_events_totals_zero(self, monkeypatch, capsys):
        monkeypatch.setattr(capacity, "search_logs_for_oom", lambda *_a, **_kw: [])

        total = capacity._count_log_oom(
            [_Service()], "/ecs/myapp", None, self.WINDOW_START, self.END_MS
        )

        assert total == 0
        assert capsys.readouterr().out == ""

    def test_events_are_counted_and_reported(self, monkeypatch, capsys):
        monkeypatch.setattr(capacity, "search_logs_for_oom", lambda *_a, **_kw: ["e1", "e2", "e3"])

        total = capacity._count_log_oom(
            [_Service()], "/ecs/myapp", None, self.WINDOW_START, self.END_MS
        )

        assert total == 3
        assert "web: 3 OOM event(s) in CloudWatch Logs" in capsys.readouterr().out

    def test_deployment_time_narrows_the_window(self, monkeypatch):
        seen = {}

        def capture(_group, start_ms, _end_ms, cloudwatch_client, log_stream_prefix):
            seen["start_ms"] = start_ms
            seen["prefix"] = log_stream_prefix
            return []

        monkeypatch.setattr(capacity, "search_logs_for_oom", capture)

        capacity._count_log_oom(
            [_Service(last_deployment_at=DEPLOYED_AT)],
            "/ecs/myapp",
            None,
            self.WINDOW_START,
            self.END_MS,
        )

        assert seen["start_ms"] == int(DEPLOYED_AT.timestamp() * 1000)
        assert seen["prefix"] == "web/"

    def test_unparseable_deployment_time_falls_back_to_the_window_start(self, monkeypatch):
        seen = {}

        def capture(_group, start_ms, _end_ms, cloudwatch_client, log_stream_prefix):
            seen["start_ms"] = start_ms
            return []

        monkeypatch.setattr(capacity, "search_logs_for_oom", capture)

        capacity._count_log_oom(
            [_Service(last_deployment_at="last tuesday")],
            "/ecs/myapp",
            None,
            self.WINDOW_START,
            self.END_MS,
        )

        assert seen["start_ms"] == int(self.WINDOW_START.timestamp() * 1000)


class TestCheckEnvironment:
    """Tests for check_environment()'s control flow, with both scans stubbed."""

    def _stub_scans(self, monkeypatch, ecs_total=0, log_total=0):
        monkeypatch.setattr(capacity.boto3, "client", lambda _name: None)
        monkeypatch.setattr(capacity, "_count_ecs_oom", lambda *_a, **_kw: ecs_total)
        monkeypatch.setattr(capacity, "_count_log_oom", lambda *_a, **_kw: log_total)

    def _stub_cluster(self, monkeypatch, cluster="myapp-cluster", services=None):
        services = [_Service()] if services is None else list(services)
        monkeypatch.setattr(
            capacity,
            "load_environment_config",
            lambda _p: {"infrastructure": {"cluster_name": cluster}},
        )
        monkeypatch.setattr(capacity.ecs, "get_services", lambda *_a: services)

    def test_unloadable_config_returns_1(self, monkeypatch, tmp_path, capsys):
        def boom(_path):
            raise RuntimeError("tofu output failed")

        monkeypatch.setattr(capacity, "load_environment_config", boom)

        assert capacity.check_environment("myapp-staging", tmp_path, 7) == 1
        assert "Error loading config" in capsys.readouterr().err

    def test_missing_cluster_name_returns_1(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(capacity, "load_environment_config", lambda _p: {})

        assert capacity.check_environment("myapp-staging", tmp_path, 7) == 1
        assert "Unable to determine ECS cluster name" in capsys.readouterr().err

    def test_no_services_returns_1(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(capacity.boto3, "client", lambda _name: None)
        self._stub_cluster(monkeypatch, services=())

        assert capacity.check_environment("myapp-staging", tmp_path, 7) == 1
        assert "No services found in cluster" in capsys.readouterr().err

    def test_clean_environment_returns_0(self, monkeypatch, tmp_path, capsys):
        self._stub_scans(monkeypatch)
        self._stub_cluster(monkeypatch)

        assert capacity.check_environment("myapp-staging", tmp_path, 7) == 0
        assert "No OOM events found" in capsys.readouterr().out

    def test_oom_from_either_scan_returns_1(self, monkeypatch, tmp_path, capsys):
        self._stub_scans(monkeypatch, ecs_total=1, log_total=2)
        self._stub_cluster(monkeypatch)

        assert capacity.check_environment("myapp-staging", tmp_path, 7) == 1
        assert "Total: 3 OOM event(s) found" in capsys.readouterr().out

    def test_the_log_group_is_derived_from_the_cluster_name(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(capacity.boto3, "client", lambda _name: None)
        monkeypatch.setattr(capacity, "_count_ecs_oom", lambda *_a, **_kw: 0)
        monkeypatch.setattr(
            capacity,
            "_count_log_oom",
            lambda _services, log_group, *_a: (seen.update(log_group=log_group), 0)[1],
        )
        self._stub_cluster(monkeypatch, cluster="myapp-staging-cluster")

        capacity.check_environment("myapp-staging", tmp_path, 7)

        assert seen["log_group"] == "/ecs/myapp-staging"
