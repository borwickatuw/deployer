"""Capacity analysis and right-sizing recommendations for ECS services."""

from dataclasses import dataclass

from ..config import TfvarsService

# Thresholds for classification
OVER_PROVISIONED_AVG = 30  # avg < 30%
OVER_PROVISIONED_P95 = 50  # AND p95 < 50%
UNDER_PROVISIONED_AVG = 70  # avg > 70%
UNDER_PROVISIONED_P95 = 90  # OR p95 > 90%
BURSTY_AVG = 30  # avg < 30%
BURSTY_P95 = 70  # BUT p95 > 70%


@dataclass
class ServiceMetrics:
    """Container for service utilization metrics."""

    service_name: str
    cpu_allocated: int  # CPU units
    memory_allocated: int  # MB
    cpu_avg: float
    cpu_p95: float
    cpu_max: float
    memory_avg: float
    memory_p95: float
    memory_max: float
    status: str  # OK, OVER_PROVISIONED, UNDER_PROVISIONED, BURSTY, OOM_KILLS
    cpu_recommendation: int | None = None
    memory_recommendation: int | None = None
    # tfvars comparison
    tfvars_cpu: int | None = None
    tfvars_memory: int | None = None
    tfvars_replicas: int | None = None
    # OOM detection
    oom_kill_count: int = 0
    oom_events: list | None = None
    # Deployment info
    last_deployment_at: str | None = None  # ISO format datetime


def calculate_percentile(values: list[float], percentile: float) -> float:
    """Calculate the given percentile from a list of values.

    Args:
        values: List of numeric values.
        percentile: Percentile to calculate (0-100).

    Returns:
        The calculated percentile value, or 0.0 for empty input.
    """
    if not values:
        return 0.0
    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * (percentile / 100)
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_values) else f
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def classify_service(metrics: ServiceMetrics) -> str:
    """Classify service utilization status.

    Args:
        metrics: Service metrics to classify.

    Returns:
        One of: "OK", "OVER_PROVISIONED", "UNDER_PROVISIONED", "BURSTY", "OOM_KILLS"
    """
    # Check for OOM kills first - this takes highest priority because it indicates
    # the service is actually running out of memory, even if metrics look low
    # (OOM kills happen too fast to be captured in utilization metrics)
    if metrics.oom_kill_count > 0:
        return "OOM_KILLS"

    # Check for under-provisioned (performance risk takes priority)
    if (
        metrics.cpu_avg > UNDER_PROVISIONED_AVG
        or metrics.cpu_p95 > UNDER_PROVISIONED_P95
        or metrics.memory_avg > UNDER_PROVISIONED_AVG
        or metrics.memory_p95 > UNDER_PROVISIONED_P95
    ):
        return "UNDER_PROVISIONED"

    # Check for bursty workload
    if (metrics.cpu_avg < BURSTY_AVG and metrics.cpu_p95 > BURSTY_P95) or (
        metrics.memory_avg < BURSTY_AVG and metrics.memory_p95 > BURSTY_P95
    ):
        return "BURSTY"

    # Check for over-provisioned
    if (
        metrics.cpu_avg < OVER_PROVISIONED_AVG
        and metrics.cpu_p95 < OVER_PROVISIONED_P95
        and metrics.memory_avg < OVER_PROVISIONED_AVG
        and metrics.memory_p95 < OVER_PROVISIONED_P95
    ):
        return "OVER_PROVISIONED"

    return "OK"


def get_recommended_cpu(current: int, avg_util: float, p95_util: float) -> int | None:
    """Calculate recommended CPU based on utilization.

    Args:
        current: Current CPU allocation in units.
        avg_util: Average CPU utilization percentage.
        p95_util: 95th percentile CPU utilization percentage.

    Returns:
        Recommended CPU units, or None if no change needed.
    """
    # Target p95 utilization of ~70%
    if p95_util == 0:
        return None

    # Calculate what CPU would give us 70% utilization at current p95
    needed = int((current * p95_util) / 70)

    # Round to valid Fargate CPU values
    valid_values = [256, 512, 1024, 2048, 4096, 8192, 16384]
    for v in valid_values:
        if v >= needed:
            return v if v != current else None

    return valid_values[-1] if valid_values[-1] != current else None


def get_recommended_memory(
    current: int, avg_util: float, p95_util: float, cpu: int
) -> int | None:
    """Calculate recommended memory based on utilization and CPU.

    Args:
        current: Current memory allocation in MB.
        avg_util: Average memory utilization percentage.
        p95_util: 95th percentile memory utilization percentage.
        cpu: CPU allocation in units (for Fargate compatibility).

    Returns:
        Recommended memory in MB, or None if no change needed.
    """
    if p95_util == 0:
        return None

    # Target p95 utilization of ~70%
    needed_mb = int((current * p95_util) / 70)

    # Fargate memory must be compatible with CPU
    # https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html
    cpu_memory_map = {
        256: [512, 1024, 2048],
        512: [1024, 2048, 3072, 4096],
        1024: [2048, 3072, 4096, 5120, 6144, 7168, 8192],
        2048: list(range(4096, 16385, 1024)),
        4096: list(range(8192, 30721, 1024)),
        8192: list(range(16384, 61441, 4096)),
        16384: list(range(32768, 122881, 8192)),
    }

    valid_values = cpu_memory_map.get(cpu, [512, 1024, 2048])
    for v in valid_values:
        if v >= needed_mb:
            return v if v != current else None

    return valid_values[-1] if valid_values[-1] != current else None


def estimate_savings(
    services: list[ServiceMetrics], hourly_rate_per_cpu: float = 0.04048
) -> float:
    """Estimate monthly savings from right-sizing recommendations.

    Default rate is Fargate on-demand pricing for us-west-2 (~$0.04048/vCPU/hour).
    Memory pricing is approximately proportional.

    Note: Services with OOM kills are excluded from savings estimates since they
    actually need MORE resources, not less. The low utilization metrics are
    misleading because OOM kills happen too fast to be captured.

    Args:
        services: List of service metrics with recommendations.
        hourly_rate_per_cpu: Hourly cost per vCPU.

    Returns:
        Estimated monthly savings in USD.
    """
    savings = 0.0
    hours_per_month = 730  # Average

    for svc in services:
        # Only count OVER_PROVISIONED services (not OOM_KILLS which appear over-provisioned
        # but are actually under-provisioned)
        if svc.status == "OVER_PROVISIONED" and svc.cpu_recommendation:
            cpu_reduction = svc.cpu_allocated - svc.cpu_recommendation
            # Convert CPU units to vCPU (1024 units = 1 vCPU)
            vcpu_reduction = cpu_reduction / 1024
            savings += vcpu_reduction * hourly_rate_per_cpu * hours_per_month

    return savings


def generate_tfvars_diff(
    services: list[ServiceMetrics],
    tfvars_config: dict[str, TfvarsService],
) -> list[str]:
    """Generate a list of differences between tfvars and recommendations.

    Args:
        services: List of service metrics with recommendations.
        tfvars_config: Parsed tfvars configuration.

    Returns:
        List of strings describing differences.
    """
    diffs = []

    for svc in sorted(services, key=lambda s: s.service_name):
        tfvars = tfvars_config.get(svc.service_name)
        if not tfvars:
            continue

        service_diffs = []

        # Compare tfvars CPU vs running
        if tfvars.cpu != svc.cpu_allocated:
            service_diffs.append(
                f"  cpu: tfvars={tfvars.cpu} → running={svc.cpu_allocated}"
            )

        # Compare tfvars memory vs running
        if tfvars.memory != svc.memory_allocated:
            service_diffs.append(
                f"  memory: tfvars={tfvars.memory} → running={svc.memory_allocated}"
            )

        # Show recommendations if different from tfvars
        if svc.cpu_recommendation and svc.cpu_recommendation != tfvars.cpu:
            service_diffs.append(
                f"  cpu: tfvars={tfvars.cpu} → recommended={svc.cpu_recommendation}"
            )

        if svc.memory_recommendation and svc.memory_recommendation != tfvars.memory:
            service_diffs.append(
                f"  memory: tfvars={tfvars.memory} → recommended={svc.memory_recommendation}"
            )

        if service_diffs:
            diffs.append(f"{svc.service_name}:")
            diffs.extend(service_diffs)

    return diffs


def generate_suggested_tfvars(
    services: list[ServiceMetrics],
    tfvars_config: dict[str, TfvarsService],
) -> str:
    """Generate suggested tfvars content with recommended values.

    Args:
        services: List of service metrics with recommendations.
        tfvars_config: Parsed tfvars configuration.

    Returns:
        String containing suggested tfvars content.
    """
    lines = ["services = {"]

    for svc in sorted(services, key=lambda s: s.service_name):
        tfvars = tfvars_config.get(svc.service_name)
        if not tfvars:
            continue

        # Use recommendation if available, otherwise keep current tfvars value
        cpu = svc.cpu_recommendation if svc.cpu_recommendation else tfvars.cpu
        memory = svc.memory_recommendation if svc.memory_recommendation else tfvars.memory

        lines.append(f"  {svc.service_name} = {{")
        lines.append(f"    cpu           = {cpu}")
        lines.append(f"    memory        = {memory}")
        lines.append(f"    replicas      = {tfvars.replicas}")
        lines.append(f"    load_balanced = {'true' if tfvars.load_balanced else 'false'}")

        if tfvars.port:
            lines.append(f"    port          = {tfvars.port}")
        if tfvars.health_check_path != "/":
            lines.append(f'    health_check_path = "{tfvars.health_check_path}"')
        if tfvars.path_pattern:
            lines.append(f'    path_pattern  = "{tfvars.path_pattern}"')

        lines.append("  }")

    lines.append("}")
    return "\n".join(lines)
