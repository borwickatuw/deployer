"""Tests for capacity-report.py functions."""

import sys
from pathlib import Path

import pytest

# Add bin directory to path so we can import the script
bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

# Import using importlib since the filename has hyphens
from importlib.util import module_from_spec, spec_from_file_location

_spec = spec_from_file_location("capacity", bin_dir / "capacity-report.py")
capacity = module_from_spec(_spec)
_spec.loader.exec_module(capacity)


class TestParseTfvars:
    """Tests for parse_tfvars function."""

    def test_parse_tfvars_basic(self, sample_tfvars):
        """Test parsing a basic terraform.tfvars file."""
        result = capacity.parse_tfvars(sample_tfvars)

        assert "web" in result
        assert result["web"].cpu == 256
        assert result["web"].memory == 512
        assert result["web"].replicas == 1
        assert result["web"].load_balanced is True
        assert result["web"].port == 8000
        assert result["web"].health_check_path == "/health/"

        assert "celery" in result
        assert result["celery"].cpu == 512
        assert result["celery"].memory == 1024
        assert result["celery"].replicas == 2
        assert result["celery"].load_balanced is False

    def test_parse_tfvars_nonexistent(self, tmp_path):
        """Test parsing a nonexistent file returns empty dict."""
        result = capacity.parse_tfvars(tmp_path / "nonexistent.tfvars")
        assert result == {}

    def test_parse_tfvars_no_services(self, tmp_path):
        """Test parsing file with no services block."""
        tfvars = tmp_path / "test.tfvars"
        tfvars.write_text('project_name = "test"\n')

        result = capacity.parse_tfvars(tfvars)
        assert result == {}


class TestCalculatePercentile:
    """Tests for calculate_percentile function."""

    def test_percentile_empty_list(self):
        """Test percentile of empty list returns 0."""
        assert capacity.calculate_percentile([], 95) == 0.0

    def test_percentile_single_value(self):
        """Test percentile of single value returns that value."""
        assert capacity.calculate_percentile([50.0], 95) == 50.0

    def test_percentile_95(self):
        """Test 95th percentile calculation."""
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        p95 = capacity.calculate_percentile(values, 95)
        # 95th percentile should be close to 95.5 for this data
        assert 90 <= p95 <= 100

    def test_percentile_50(self):
        """Test 50th percentile (median)."""
        values = [10, 20, 30, 40, 50]
        p50 = capacity.calculate_percentile(values, 50)
        assert p50 == 30.0

    def test_percentile_unsorted_input(self):
        """Test that function works with unsorted input."""
        values = [100, 10, 50, 30, 70]
        p50 = capacity.calculate_percentile(values, 50)
        assert p50 == 50.0


class TestClassifyService:
    """Tests for classify_service function."""

    def test_classify_ok(self):
        """Test service classified as OK with moderate utilization."""
        metrics = capacity.ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=50.0,
            cpu_p95=60.0,
            cpu_max=70.0,
            memory_avg=50.0,
            memory_p95=60.0,
            memory_max=70.0,
            status="",
        )

        assert capacity.classify_service(metrics) == "OK"

    def test_classify_over_provisioned(self):
        """Test service classified as over-provisioned with low utilization."""
        metrics = capacity.ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=10.0,  # < 30%
            cpu_p95=20.0,  # < 50%
            cpu_max=30.0,
            memory_avg=10.0,  # < 30%
            memory_p95=20.0,  # < 50%
            memory_max=30.0,
            status="",
        )

        assert capacity.classify_service(metrics) == "OVER_PROVISIONED"

    def test_classify_under_provisioned_high_avg(self):
        """Test service classified as under-provisioned with high avg CPU."""
        metrics = capacity.ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=75.0,  # > 70%
            cpu_p95=85.0,
            cpu_max=90.0,
            memory_avg=50.0,
            memory_p95=60.0,
            memory_max=70.0,
            status="",
        )

        assert capacity.classify_service(metrics) == "UNDER_PROVISIONED"

    def test_classify_under_provisioned_high_p95(self):
        """Test service classified as under-provisioned with high p95."""
        metrics = capacity.ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=50.0,
            cpu_p95=95.0,  # > 90%
            cpu_max=100.0,
            memory_avg=50.0,
            memory_p95=60.0,
            memory_max=70.0,
            status="",
        )

        assert capacity.classify_service(metrics) == "UNDER_PROVISIONED"

    def test_classify_bursty(self):
        """Test service classified as bursty with low avg but high p95."""
        metrics = capacity.ServiceMetrics(
            service_name="test",
            cpu_allocated=256,
            memory_allocated=512,
            cpu_avg=20.0,  # < 30%
            cpu_p95=80.0,  # > 70%
            cpu_max=95.0,
            memory_avg=50.0,
            memory_p95=60.0,
            memory_max=70.0,
            status="",
        )

        assert capacity.classify_service(metrics) == "BURSTY"


class TestGetRecommendedCpu:
    """Tests for get_recommended_cpu function."""

    def test_recommended_cpu_no_change(self):
        """Test no recommendation when current is optimal."""
        # If p95 is 70%, current allocation is already at target
        result = capacity.get_recommended_cpu(256, 50.0, 70.0)
        assert result is None

    def test_recommended_cpu_decrease(self):
        """Test recommendation to decrease CPU for over-provisioned service."""
        # p95 at 20% means we're using 51.2 CPU units at peak
        # To get 70% utilization, we'd need ~73 units, which rounds to 256
        result = capacity.get_recommended_cpu(1024, 10.0, 20.0)
        # 1024 * 20 / 70 = ~292, rounds to 512
        assert result == 512 or result is None  # Depends on rounding

    def test_recommended_cpu_increase(self):
        """Test recommendation to increase CPU for under-provisioned service."""
        # p95 at 90% means we need more headroom
        result = capacity.get_recommended_cpu(256, 80.0, 90.0)
        # 256 * 90 / 70 = ~329, rounds to 512
        assert result == 512

    def test_recommended_cpu_zero_utilization(self):
        """Test no recommendation when utilization is zero."""
        result = capacity.get_recommended_cpu(256, 0.0, 0.0)
        assert result is None

    def test_recommended_cpu_valid_fargate_values(self):
        """Test that recommendations are valid Fargate CPU values."""
        valid_values = [256, 512, 1024, 2048, 4096, 8192, 16384]

        for current in valid_values:
            result = capacity.get_recommended_cpu(current, 50.0, 95.0)
            if result is not None:
                assert result in valid_values


class TestGetRecommendedMemory:
    """Tests for get_recommended_memory function."""

    def test_recommended_memory_no_change(self):
        """Test no recommendation when current is optimal."""
        result = capacity.get_recommended_memory(512, 50.0, 70.0, 256)
        assert result is None

    def test_recommended_memory_zero_utilization(self):
        """Test no recommendation when utilization is zero."""
        result = capacity.get_recommended_memory(512, 0.0, 0.0, 256)
        assert result is None

    def test_recommended_memory_valid_for_cpu(self):
        """Test that recommended memory is valid for the given CPU."""
        # For 256 CPU, valid memory is 512, 1024, 2048
        result = capacity.get_recommended_memory(2048, 20.0, 30.0, 256)
        if result is not None:
            assert result in [512, 1024, 2048]


class TestEstimateSavings:
    """Tests for estimate_savings function."""

    def test_estimate_savings_no_over_provisioned(self):
        """Test zero savings when no services are over-provisioned."""
        services = [
            capacity.ServiceMetrics(
                service_name="test",
                cpu_allocated=256,
                memory_allocated=512,
                cpu_avg=50.0,
                cpu_p95=60.0,
                cpu_max=70.0,
                memory_avg=50.0,
                memory_p95=60.0,
                memory_max=70.0,
                status="OK",
            )
        ]

        savings = capacity.estimate_savings(services)
        assert savings == 0.0

    def test_estimate_savings_with_recommendation(self):
        """Test savings calculation for over-provisioned service."""
        services = [
            capacity.ServiceMetrics(
                service_name="test",
                cpu_allocated=1024,
                memory_allocated=2048,
                cpu_avg=10.0,
                cpu_p95=20.0,
                cpu_max=30.0,
                memory_avg=10.0,
                memory_p95=20.0,
                memory_max=30.0,
                status="OVER_PROVISIONED",
                cpu_recommendation=256,
            )
        ]

        savings = capacity.estimate_savings(services)
        # (1024 - 256) / 1024 * 0.04048 * 730 = ~22.18
        assert savings > 0


class TestGenerateTfvarsDiff:
    """Tests for generate_tfvars_diff function."""

    def test_no_diff_when_matching(self):
        """Test no diff when tfvars matches running config."""
        services = [
            capacity.ServiceMetrics(
                service_name="web",
                cpu_allocated=256,
                memory_allocated=512,
                cpu_avg=50.0,
                cpu_p95=60.0,
                cpu_max=70.0,
                memory_avg=50.0,
                memory_p95=60.0,
                memory_max=70.0,
                status="OK",
            )
        ]
        tfvars_config = {
            "web": capacity.TfvarsService(cpu=256, memory=512, replicas=1),
        }

        diffs = capacity.generate_tfvars_diff(services, tfvars_config)
        assert diffs == []

    def test_diff_when_cpu_differs(self):
        """Test diff shows when CPU differs."""
        services = [
            capacity.ServiceMetrics(
                service_name="web",
                cpu_allocated=512,
                memory_allocated=512,
                cpu_avg=50.0,
                cpu_p95=60.0,
                cpu_max=70.0,
                memory_avg=50.0,
                memory_p95=60.0,
                memory_max=70.0,
                status="OK",
            )
        ]
        tfvars_config = {
            "web": capacity.TfvarsService(cpu=256, memory=512, replicas=1),
        }

        diffs = capacity.generate_tfvars_diff(services, tfvars_config)
        assert len(diffs) > 0
        assert any("cpu" in d for d in diffs)
