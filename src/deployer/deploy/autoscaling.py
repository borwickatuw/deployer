"""Queue-driven auto-scaling for ECS services (Application Auto Scaling).

The privilege split (DESIGN.md "ECS Services and Task Definitions"):

- **The app signals, never scales.** A scheduled task in the application
  publishes its queue metrics to CloudWatch. Its IAM role allows
  ``cloudwatch:PutMetricData`` and nothing else scaling-related.
- **AWS decides, within bounds.** Application Auto Scaling step policies
  react to the metric. They cannot exceed the registered ``max``.
- **The environment bounds.** ``min``/``max``/``steps`` come from the
  environment's tfvars (the ``scaling`` variable); changing the cost
  ceiling is a reviewed config change, never an app-code change.

Everything except those three knobs is convention, fixed here:

- Two metrics in namespace ``{app}-{environment}``, dimension
  ``Service={service}``, published every 60s by the application:
  ``queue_depth`` (jobs waiting) and ``oldest_job_age_seconds`` (seconds the
  job at the head of the queue has waited). The age datum is omitted while
  the queue is empty.
- One exact-capacity step policy + ``queue_depth >= depth`` alarm per
  scale-out step. A step that also sets ``age_seconds`` gets a second alarm,
  ``oldest_job_age_seconds >= age_seconds``, on the same policy: either
  signal runs that step's worker count, so a head job stuck behind a busy
  worker gets help before the queue grows deep. When several alarms are in
  ALARM at once, Application Auto Scaling applies the policy that provides
  the largest capacity, so overlapping steps resolve to the highest matching
  worker count.
- One scale-in policy + alarm: depth 0 sustained for 15 minutes scales to
  ``min``. Workers holding a job set ECS task scale-in protection, so a
  scale-in waits for the busy task to go idle instead of wasting its job.
  Scale-in watches depth only. The zero alarm and an age alarm are never
  in ALARM together: an age datum exists only while a job is queued, when
  depth is at least 1.
- When an age alarm clears, a lower step whose depth alarm is still in
  ALARM sets desired count back to its own worker count. Scale-in
  protection keeps busy tasks, so the extra worker lasts until it goes idle
  (it helps drain the backlog, then is released). If the head job waits
  ``age_seconds`` again, the age alarm fires again.
- ``TreatMissingData=notBreaching`` on every alarm: a stopped environment
  (staging scheduler) publishes no metric, and missing data must never
  scale a stopped service back up.
"""

from __future__ import annotations

from ..utils import Colors, log, log_success

DEPTH_METRIC_NAME = "queue_depth"
AGE_METRIC_NAME = "oldest_job_age_seconds"
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
            (``{service: {min, max, steps: [{depth, workers, age_seconds?}]}}``).
            An unset ``age_seconds`` arrives as None (tofu emits null for an
            unset ``optional()`` attribute) and means the step has no age
            trigger.

    Raises:
        ValueError: On an unknown service, a min below the deploy.toml
            ``min_replicas`` floor (default 1 — a service must declare
            ``min_replicas = 0`` before an environment may scale it to
            zero), inverted bounds, steps that are empty, out of order,
            or outside the capacity bounds, or an ``age_seconds`` that is
            not a positive integer, sits on a depth-1 step, or does not
            strictly increase across the steps that set it.
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
        _validate_step_ages(service_name, steps)


def _validate_step_ages(service_name: str, steps: list[dict]) -> None:
    """Fail fast on an ``age_seconds`` trigger that is malformed or dead.

    Dead triggers are rejected rather than tolerated, because each would
    read as working config while never changing the worker count:

    - On a depth-1 step: the age datum exists only while a job is queued,
      and then ``queue_depth >= 1`` is already breaching.
    - Not strictly increasing across the steps that set it (steps are
      already ordered by worker count): once the head job's age passes a
      higher-worker step's threshold, a lower-worker step with an equal or
      larger threshold can never be the largest capacity in ALARM. Equal
      thresholds would also share one alarm name.
    """
    previous_age: int | None = None
    for step in steps:
        age = step.get("age_seconds")
        if age is None:
            continue
        if isinstance(age, bool) or not isinstance(age, int) or age < 1:
            raise ValueError(
                f"Service '{service_name}' scaling step age_seconds must be a "
                f"positive integer, got {age!r}"
            )
        if step["depth"] == 1:
            raise ValueError(
                f"Service '{service_name}' scaling step age_seconds on the "
                f"depth-1 step can never scale: any queued job already "
                f"breaches depth >= 1"
            )
        if previous_age is not None and age <= previous_age:
            raise ValueError(
                f"Service '{service_name}' scaling step age_seconds must "
                f"strictly increase across the steps that set it "
                f"({previous_age} then {age})"
            )
        previous_age = age


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


def _age_alarm_name(prefix: str, age_seconds: int) -> str:
    # "age-ge-" cannot be mistaken for a depth alarm's "ge-" or the
    # scale-in "zero"; validation keeps each threshold unique per service.
    return f"{prefix}age-ge-{age_seconds}"


def _scale_in_alarm_name(prefix: str) -> str:
    return f"{prefix}zero"


def _metric_alarm_common(
    app_name: str, environment: str, service_name: str, metric_name: str
) -> dict:
    """Alarm parameters shared by every alarm, depth or age."""
    return {
        "Namespace": autoscale_namespace(app_name, environment),
        "MetricName": metric_name,
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
    steps = [
        (
            int(s["depth"]),
            int(s["workers"]),
            None if s.get("age_seconds") is None else int(s["age_seconds"]),
        )
        for s in cfg["steps"]
    ]

    autoscaling_client.register_scalable_target(
        ServiceNamespace="ecs",
        ResourceId=resource_id,
        ScalableDimension="ecs:service:DesiredCount",
        MinCapacity=min_capacity,
        MaxCapacity=max_capacity,
    )

    depth_common = _metric_alarm_common(
        ctx.app_name, ctx.environment, service_name, DEPTH_METRIC_NAME
    )
    age_common = _metric_alarm_common(ctx.app_name, ctx.environment, service_name, AGE_METRIC_NAME)
    keep: set[str] = set()

    # One policy + alarm per scale-out step, plus an age alarm on the same
    # policy when the step sets age_seconds. Overlapping ALARM states
    # resolve to the largest capacity (Application Auto Scaling's
    # multiple-policies rule), so depth 30 with steps at 1 and 25 runs the
    # depth-25 worker count.
    for depth, workers, age_seconds in steps:
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
            **depth_common,
        )
        keep.update({policy_name, alarm_name})

        if age_seconds is not None:
            age_alarm = _age_alarm_name(prefix, age_seconds)
            cloudwatch_client.put_metric_alarm(
                AlarmName=age_alarm,
                AlarmDescription=(
                    f"{service_name} oldest queued job waited >= {age_seconds}s: "
                    f"run {workers} worker(s)"
                ),
                ComparisonOperator="GreaterThanOrEqualToThreshold",
                Threshold=age_seconds,
                EvaluationPeriods=SCALE_OUT_EVALUATION_PERIODS,
                AlarmActions=[policy_arn],
                **age_common,
            )
            keep.add(age_alarm)

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
        **depth_common,
    )
    keep.update({scale_in_policy, scale_in_alarm})

    _delete_unmanaged(autoscaling_client, cloudwatch_client, resource_id, prefix, keep)

    steps_desc = ", ".join(
        f"depth>={depth}" + ("" if age is None else f" or age>={age}s") + f" -> {workers}"
        for depth, workers, age in steps
    )
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
