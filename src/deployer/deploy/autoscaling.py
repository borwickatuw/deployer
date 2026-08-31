"""Queue-depth auto-scaling for ECS services (Application Auto Scaling).

The privilege split (DESIGN.md "ECS Services and Task Definitions"):

- **The app signals, never scales.** A scheduled task in the application
  publishes its queue depth to CloudWatch. Its IAM role allows
  ``cloudwatch:PutMetricData`` and nothing else scaling-related.
- **AWS decides, within bounds.** Application Auto Scaling step policies
  react to the metric. They cannot exceed the registered ``max``.
- **The environment bounds.** ``min``/``max``/``steps`` come from the
  environment's tfvars (the ``scaling`` variable); changing the cost
  ceiling is a reviewed config change, never an app-code change.

Everything except those three knobs is convention, fixed here:

- Metric ``queue_depth`` in namespace ``{app}-{environment}``, dimension
  ``Service={service}``, published every 60s by the application.
- One exact-capacity step policy + alarm per scale-out step. When several
  alarms are in ALARM at once, Application Auto Scaling applies the policy
  that provides the largest capacity, so overlapping steps resolve to the
  highest matching worker count.
- One scale-in policy + alarm: depth 0 sustained for 15 minutes scales to
  ``min``. Workers holding a job set ECS task scale-in protection, so a
  scale-in waits for the busy task to go idle instead of wasting its job.
- ``TreatMissingData=notBreaching`` on every alarm: a stopped environment
  (staging scheduler) publishes no metric, and missing data must never
  scale a stopped service back up.
"""

from __future__ import annotations

from ..utils import Colors, log, log_success

METRIC_NAME = "queue_depth"
METRIC_DIMENSION = "Service"
# The application publishes every 60s (beat schedule); alarm periods match.
METRIC_PERIOD_SECONDS = 60
# Scale out on a single breaching datapoint — a deposit should get workers
# within minutes.
SCALE_OUT_EVALUATION_PERIODS = 1
SCALE_OUT_COOLDOWN_SECONDS = 60
# Scale in only after the queue has been empty this long, so a brief gap
# between jobs doesn't kill a warm worker.
SCALE_IN_SUSTAINED_PERIODS = 15
SCALE_IN_COOLDOWN_SECONDS = 60


def autoscale_namespace(app_name: str, environment: str) -> str:
    """CloudWatch namespace the application publishes queue depths into."""
    return f"{app_name}-{environment}"


def validate_scaling_config(config: dict, scaling_config: dict) -> None:
    """Fail fast on a scaling config that cannot be enacted as intended.

    Args:
        config: The deployment TOML configuration.
        scaling_config: The environment's ``scaling`` map
            (``{service: {min, max, steps: [{depth, workers}]}}``).

    Raises:
        ValueError: On an unknown service, a min below the deploy.toml
            ``min_replicas`` floor (default 1 — a service must declare
            ``min_replicas = 0`` before an environment may scale it to
            zero), inverted bounds, or steps that are empty, out of order,
            or outside the capacity bounds.
    """
    services = config.get("services", {})
    for service_name, cfg in scaling_config.items():
        if service_name not in services:
            raise ValueError(
                f"scaling config names unknown service '{service_name}'.\n"
                f"  Services in deploy.toml: {sorted(services)}"
            )

        missing = {"min", "max", "steps"} - set(cfg)
        if missing:
            raise ValueError(f"scaling config for '{service_name}' is missing {sorted(missing)}")

        min_capacity, max_capacity, steps = cfg["min"], cfg["max"], cfg["steps"]

        min_replicas = services[service_name].get("min_replicas", 1)
        if min_capacity < min_replicas:
            raise ValueError(
                f"Service '{service_name}' scaling min ({min_capacity}) is below "
                f"minimum required ({min_replicas}) from deploy.toml.\n"
                f"  A service must declare min_replicas = 0 in deploy.toml before "
                f"an environment may scale it to zero."
            )
        if min_capacity > max_capacity:
            raise ValueError(
                f"Service '{service_name}' scaling min ({min_capacity}) exceeds "
                f"max ({max_capacity})"
            )

        if not steps:
            raise ValueError(f"Service '{service_name}' scaling steps must not be empty")
        for previous, step in zip([None, *steps], steps, strict=False):
            if step["depth"] < 1:
                raise ValueError(
                    f"Service '{service_name}' scaling step depth must be >= 1, "
                    f"got {step['depth']}"
                )
            if not min_capacity <= step["workers"] <= max_capacity:
                raise ValueError(
                    f"Service '{service_name}' scaling step workers "
                    f"({step['workers']}) is outside min..max "
                    f"({min_capacity}..{max_capacity})"
                )
            if previous is not None and (
                step["depth"] <= previous["depth"] or step["workers"] <= previous["workers"]
            ):
                raise ValueError(
                    f"Service '{service_name}' scaling steps must strictly "
                    f"increase in both depth and workers"
                )


def _resource_id(cluster_name: str, service_name: str) -> str:
    return f"service/{cluster_name}/{service_name}"


def _alarm_prefix(app_name: str, environment: str, service_name: str) -> str:
    """Prefix owning every alarm this module manages for one service.

    Drift correction lists alarms by this prefix and deletes any it did not
    intend, so nothing else may create alarms under it.
    """
    return f"{app_name}-{environment}-{service_name}-queue-depth-"


def _scale_out_alarm_name(prefix: str, depth: int) -> str:
    return f"{prefix}ge-{depth}"


def _scale_in_alarm_name(prefix: str) -> str:
    return f"{prefix}zero"


def _metric_alarm_common(app_name: str, environment: str, service_name: str) -> dict:
    """Alarm parameters shared by scale-out and scale-in alarms."""
    return {
        "Namespace": autoscale_namespace(app_name, environment),
        "MetricName": METRIC_NAME,
        "Dimensions": [{"Name": METRIC_DIMENSION, "Value": service_name}],
        "Statistic": "Maximum",
        "Period": METRIC_PERIOD_SECONDS,
        # A stopped environment publishes no metric; missing data must never
        # scale a stopped service back up (or down).
        "TreatMissingData": "notBreaching",
    }


def _put_exact_capacity_policy(
    autoscaling_client, resource_id: str, policy_name: str, capacity: int, cooldown: int
) -> str:
    """Create or update a one-step exact-capacity step policy; return its ARN.

    The single step adjustment covers the alarm's entire breach range, so
    the policy always answers "set desired count to ``capacity``".
    """
    response = autoscaling_client.put_scaling_policy(
        PolicyName=policy_name,
        ServiceNamespace="ecs",
        ResourceId=resource_id,
        ScalableDimension="ecs:service:DesiredCount",
        PolicyType="StepScaling",
        StepScalingPolicyConfiguration={
            "AdjustmentType": "ExactCapacity",
            "StepAdjustments": [{"MetricIntervalLowerBound": 0, "ScalingAdjustment": capacity}],
            "Cooldown": cooldown,
            "MetricAggregationType": "Maximum",
        },
    )
    return response["PolicyARN"]


def _delete_unmanaged(
    autoscaling_client, cloudwatch_client, resource_id: str, prefix: str, keep: set[str]
) -> None:
    """Delete policies and alarms for a service that this apply did not intend.

    Live state that disagrees with config is corrected, not warned about:
    a stale step alarm from an old threshold would keep scaling to a worker
    count nobody configured any more.

    Args:
        autoscaling_client: boto3 application-autoscaling client.
        cloudwatch_client: boto3 cloudwatch client.
        resource_id: The scalable target's resource id.
        prefix: The service's managed alarm-name prefix.
        keep: Policy and alarm names this apply created or kept.
    """
    policies = autoscaling_client.describe_scaling_policies(
        ServiceNamespace="ecs", ResourceId=resource_id
    ).get("ScalingPolicies", [])
    for policy in policies:
        if policy["PolicyName"] not in keep:
            autoscaling_client.delete_scaling_policy(
                PolicyName=policy["PolicyName"],
                ServiceNamespace="ecs",
                ResourceId=resource_id,
                ScalableDimension="ecs:service:DesiredCount",
            )

    alarms = cloudwatch_client.describe_alarms(AlarmNamePrefix=prefix).get("MetricAlarms", [])
    stale = [a["AlarmName"] for a in alarms if a["AlarmName"] not in keep]
    if stale:
        cloudwatch_client.delete_alarms(AlarmNames=stale)


def _apply_service_scaling(
    ctx, autoscaling_client, cloudwatch_client, service_name: str, cfg: dict
) -> None:
    """Idempotently enact one service's scaling block."""
    resource_id = _resource_id(ctx.cluster_name, service_name)
    prefix = _alarm_prefix(ctx.app_name, ctx.environment, service_name)
    min_capacity, max_capacity = int(cfg["min"]), int(cfg["max"])
    steps = [(int(s["depth"]), int(s["workers"])) for s in cfg["steps"]]

    autoscaling_client.register_scalable_target(
        ServiceNamespace="ecs",
        ResourceId=resource_id,
        ScalableDimension="ecs:service:DesiredCount",
        MinCapacity=min_capacity,
        MaxCapacity=max_capacity,
    )

    common = _metric_alarm_common(ctx.app_name, ctx.environment, service_name)
    keep: set[str] = set()

    # One policy + alarm per scale-out step. Overlapping ALARM states
    # resolve to the largest capacity (Application Auto Scaling's
    # multiple-policies rule), so depth 30 with steps at 1 and 25 runs the
    # depth-25 worker count.
    for depth, workers in steps:
        policy_name = f"queue-depth-ge-{depth}"
        policy_arn = _put_exact_capacity_policy(
            autoscaling_client, resource_id, policy_name, workers, SCALE_OUT_COOLDOWN_SECONDS
        )
        alarm_name = _scale_out_alarm_name(prefix, depth)
        cloudwatch_client.put_metric_alarm(
            AlarmName=alarm_name,
            AlarmDescription=(f"{service_name} queue depth >= {depth}: run {workers} worker(s)"),
            ComparisonOperator="GreaterThanOrEqualToThreshold",
            Threshold=depth,
            EvaluationPeriods=SCALE_OUT_EVALUATION_PERIODS,
            AlarmActions=[policy_arn],
            **common,
        )
        keep.update({policy_name, alarm_name})

    # Scale in to min only when the queue has been empty for the sustained
    # window. Busy workers survive via ECS task scale-in protection.
    scale_in_policy = "queue-depth-scale-in"
    policy_arn = _put_exact_capacity_policy(
        autoscaling_client, resource_id, scale_in_policy, min_capacity, SCALE_IN_COOLDOWN_SECONDS
    )
    scale_in_alarm = _scale_in_alarm_name(prefix)
    cloudwatch_client.put_metric_alarm(
        AlarmName=scale_in_alarm,
        AlarmDescription=(
            f"{service_name} queue empty for "
            f"{SCALE_IN_SUSTAINED_PERIODS} minutes: scale to {min_capacity}"
        ),
        ComparisonOperator="LessThanOrEqualToThreshold",
        Threshold=0,
        EvaluationPeriods=SCALE_IN_SUSTAINED_PERIODS,
        AlarmActions=[policy_arn],
        **common,
    )
    keep.update({scale_in_policy, scale_in_alarm})

    _delete_unmanaged(autoscaling_client, cloudwatch_client, resource_id, prefix, keep)

    steps_desc = ", ".join(f"depth>={d} -> {w}" for d, w in steps)
    log_success(
        f"{service_name} autoscaling applied (min={min_capacity}, "
        f"max={max_capacity}, {steps_desc})"
    )


def _remove_service_scaling(ctx, autoscaling_client, cloudwatch_client, service_name: str) -> None:
    """Remove any scaling state for a service whose scaling block is gone.

    Deregistering the scalable target deletes its scaling policies; the
    CloudWatch alarms are ours to clean up by prefix. A service that never
    had scaling is a no-op.
    """
    resource_id = _resource_id(ctx.cluster_name, service_name)
    prefix = _alarm_prefix(ctx.app_name, ctx.environment, service_name)

    targets = autoscaling_client.describe_scalable_targets(
        ServiceNamespace="ecs",
        ResourceIds=[resource_id],
        ScalableDimension="ecs:service:DesiredCount",
    ).get("ScalableTargets", [])
    if targets:
        autoscaling_client.deregister_scalable_target(
            ServiceNamespace="ecs",
            ResourceId=resource_id,
            ScalableDimension="ecs:service:DesiredCount",
        )

    alarms = cloudwatch_client.describe_alarms(AlarmNamePrefix=prefix).get("MetricAlarms", [])
    if alarms:
        cloudwatch_client.delete_alarms(AlarmNames=[a["AlarmName"] for a in alarms])

    if targets or alarms:
        log_success(f"{service_name} autoscaling removed")


def apply_autoscaling(ctx, scaling_config: dict, autoscaling_client, cloudwatch_client) -> None:
    """Bring every service's Application Auto Scaling state in line with config.

    Runs after ``wait_for_stable``: policies must attach to services that
    exist and are healthy, and the first deploy of a new service must create
    it before a scalable target can reference it.

    Args:
        ctx: DeploymentContext with shared deployment parameters.
        scaling_config: The environment's ``scaling`` map (already validated
            by ``validate_scaling_config``).
        autoscaling_client: boto3 application-autoscaling client.
        cloudwatch_client: boto3 cloudwatch client.
    """
    if not scaling_config and ctx.dry_run:
        return

    log("Applying auto-scaling configuration...")

    if ctx.dry_run:
        for service_name in ctx.config.get("services", {}):
            if service_name in scaling_config:
                print(
                    f"  {Colors.YELLOW}[dry-run]{Colors.NC} aws application-autoscaling "
                    f"register-scalable-target + put-scaling-policy ({service_name})"
                )
        return

    for service_name in ctx.config.get("services", {}):
        if service_name in scaling_config:
            _apply_service_scaling(
                ctx,
                autoscaling_client,
                cloudwatch_client,
                service_name,
                scaling_config[service_name],
            )
        else:
            _remove_service_scaling(ctx, autoscaling_client, cloudwatch_client, service_name)
