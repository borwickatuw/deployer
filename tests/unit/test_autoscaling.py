"""Tests for deploy/autoscaling.py: schema validation and the apply step.

The apply step is driven with MagicMock boto3 clients — the module's job is
to translate one scaling block into the right sequence of Application Auto
Scaling and CloudWatch calls, and that translation is what is asserted.
"""

from unittest.mock import MagicMock

import pytest

from deployer.deploy.autoscaling import (
    apply_autoscaling,
    validate_scaling_config,
)
from deployer.deploy.context import DeploymentContext, InfraConfig
from deployer.deploy.task_definition import get_environment_variables

CONFIG = {"services": {"transcoder": {"min_replicas": 0}, "web": {}}}

SCALING = {
    "transcoder": {
        "min": 0,
        "max": 2,
        "steps": [{"depth": 1, "workers": 1}, {"depth": 25, "workers": 2}],
    }
}

# The fast-lane shape: one worker while anything is queued; a second when the
# queue is deep OR its head job has waited ten minutes. Step dicts arrive from
# tofu output, which emits null for an unset optional age_seconds.
SCALING_WITH_AGE = {
    "transcoder": {
        "min": 0,
        "max": 2,
        "steps": [
            {"depth": 1, "workers": 1, "age_seconds": None},
            {"depth": 25, "workers": 2, "age_seconds": 600},
        ],
    }
}

AGE_ALARM = "havoc-staging-transcoder-queue-depth-age-ge-600"


def _ctx(**overrides) -> DeploymentContext:
    defaults = {
        "ecs_client": None,
        "cluster_name": "havoc-staging-cluster",
        "config": CONFIG,
        "service_config": {},
        "infra_config": InfraConfig(),
        "app_name": "havoc",
        "environment": "staging",
        "region": "us-west-2",
        "account_id": "123456789012",
        "env_config": {},
        "dry_run": False,
        "scaling_config": SCALING,
    }
    defaults.update(overrides)
    return DeploymentContext(**defaults)


def _clients():
    """Autoscaling and CloudWatch mocks with clean live state."""
    autoscaling = MagicMock()
    autoscaling.put_scaling_policy.return_value = {"PolicyARN": "arn:policy"}
    autoscaling.describe_scaling_policies.return_value = {"ScalingPolicies": []}
    autoscaling.describe_scalable_targets.return_value = {"ScalableTargets": []}
    cloudwatch = MagicMock()
    cloudwatch.describe_alarms.return_value = {"MetricAlarms": []}
    return autoscaling, cloudwatch


class TestValidateScalingConfig:
    def test_valid_config_passes(self):
        validate_scaling_config(CONFIG, SCALING)

    def test_empty_config_passes(self):
        validate_scaling_config(CONFIG, {})

    def test_unknown_service_rejected(self):
        with pytest.raises(ValueError, match="unknown service 'worker'"):
            validate_scaling_config(CONFIG, {"worker": SCALING["transcoder"]})

    def test_missing_keys_rejected(self):
        with pytest.raises(ValueError, match=r"missing \['steps'\]"):
            validate_scaling_config(CONFIG, {"transcoder": {"min": 0, "max": 2}})

    def test_min_below_min_replicas_floor_rejected(self):
        # web declares no min_replicas, so the floor defaults to 1: an
        # environment may not scale it to zero.
        scaling = {"web": {"min": 0, "max": 2, "steps": [{"depth": 1, "workers": 1}]}}
        with pytest.raises(ValueError, match="below minimum required"):
            validate_scaling_config(CONFIG, scaling)

    def test_min_above_max_rejected(self):
        scaling = {"web": {"min": 3, "max": 2, "steps": [{"depth": 1, "workers": 3}]}}
        with pytest.raises(ValueError, match="exceeds max"):
            validate_scaling_config(CONFIG, scaling)

    def test_empty_steps_rejected(self):
        scaling = {"transcoder": {"min": 0, "max": 2, "steps": []}}
        with pytest.raises(ValueError, match="must not be empty"):
            validate_scaling_config(CONFIG, scaling)

    def test_step_workers_above_max_rejected(self):
        scaling = {"transcoder": {"min": 0, "max": 2, "steps": [{"depth": 1, "workers": 3}]}}
        with pytest.raises(ValueError, match="outside min..max"):
            validate_scaling_config(CONFIG, scaling)

    def test_non_increasing_depths_rejected(self):
        scaling = {
            "transcoder": {
                "min": 0,
                "max": 3,
                "steps": [{"depth": 10, "workers": 1}, {"depth": 10, "workers": 2}],
            }
        }
        with pytest.raises(ValueError, match="strictly increase"):
            validate_scaling_config(CONFIG, scaling)

    def test_non_increasing_workers_rejected(self):
        scaling = {
            "transcoder": {
                "min": 0,
                "max": 3,
                "steps": [{"depth": 1, "workers": 2}, {"depth": 25, "workers": 2}],
            }
        }
        with pytest.raises(ValueError, match="strictly increase"):
            validate_scaling_config(CONFIG, scaling)

    def test_zero_depth_rejected(self):
        # Depth 0 would collide with the scale-in alarm's empty-queue test.
        scaling = {"transcoder": {"min": 0, "max": 2, "steps": [{"depth": 0, "workers": 1}]}}
        with pytest.raises(ValueError, match="depth must be >= 1"):
            validate_scaling_config(CONFIG, scaling)


def _with_steps(steps: list[dict], max_capacity: int = 3) -> dict:
    return {"transcoder": {"min": 0, "max": max_capacity, "steps": steps}}


class TestValidateStepAges:
    def test_age_on_a_later_step_passes(self):
        validate_scaling_config(CONFIG, SCALING_WITH_AGE)

    def test_null_age_everywhere_passes(self):
        steps = [
            {"depth": 1, "workers": 1, "age_seconds": None},
            {"depth": 25, "workers": 2, "age_seconds": None},
        ]
        validate_scaling_config(CONFIG, _with_steps(steps))

    def test_increasing_ages_on_non_adjacent_steps_pass(self):
        steps = [
            {"depth": 1, "workers": 1},
            {"depth": 10, "workers": 2, "age_seconds": 300},
            {"depth": 25, "workers": 3},
            {"depth": 50, "workers": 4, "age_seconds": 900},
        ]
        validate_scaling_config(CONFIG, _with_steps(steps, max_capacity=4))

    @pytest.mark.parametrize("age", [0, -60, 600.5, "600", True])
    def test_non_positive_integer_age_rejected(self, age):
        steps = [{"depth": 1, "workers": 1}, {"depth": 25, "workers": 2, "age_seconds": age}]
        with pytest.raises(ValueError, match="age_seconds must be a positive integer"):
            validate_scaling_config(CONFIG, _with_steps(steps))

    def test_age_on_depth_one_step_rejected(self):
        # An age datum exists only while a job is queued, when depth >= 1
        # already breaches: the trigger could never change anything.
        steps = [{"depth": 1, "workers": 1, "age_seconds": 600}]
        with pytest.raises(ValueError, match="depth-1 step can never scale"):
            validate_scaling_config(CONFIG, _with_steps(steps))

    @pytest.mark.parametrize("second_age", [600, 900])
    def test_non_increasing_ages_rejected(self, second_age):
        steps = [
            {"depth": 1, "workers": 1},
            {"depth": 10, "workers": 2, "age_seconds": 900},
            {"depth": 25, "workers": 3, "age_seconds": second_age},
        ]
        with pytest.raises(ValueError, match="age_seconds must strictly increase"):
            validate_scaling_config(CONFIG, _with_steps(steps))


class TestApplyAutoscaling:
    def test_registers_bounded_scalable_target(self):
        autoscaling, cloudwatch = _clients()
        apply_autoscaling(_ctx(), SCALING, autoscaling, cloudwatch)

        autoscaling.register_scalable_target.assert_called_once_with(
            ServiceNamespace="ecs",
            ResourceId="service/havoc-staging-cluster/transcoder",
            ScalableDimension="ecs:service:DesiredCount",
            MinCapacity=0,
            MaxCapacity=2,
        )

    def test_creates_one_exact_capacity_policy_and_alarm_per_step_plus_scale_in(self):
        autoscaling, cloudwatch = _clients()
        apply_autoscaling(_ctx(), SCALING, autoscaling, cloudwatch)

        policies = {
            call.kwargs["PolicyName"]: call.kwargs
            for call in autoscaling.put_scaling_policy.call_args_list
        }
        assert set(policies) == {"queue-depth-ge-1", "queue-depth-ge-25", "queue-depth-scale-in"}
        for name, capacity in [
            ("queue-depth-ge-1", 1),
            ("queue-depth-ge-25", 2),
            ("queue-depth-scale-in", 0),
        ]:
            step_cfg = policies[name]["StepScalingPolicyConfiguration"]
            assert step_cfg["AdjustmentType"] == "ExactCapacity"
            assert step_cfg["StepAdjustments"] == [
                {"MetricIntervalLowerBound": 0, "ScalingAdjustment": capacity}
            ]

        alarms = {
            call.kwargs["AlarmName"]: call.kwargs
            for call in cloudwatch.put_metric_alarm.call_args_list
        }
        assert set(alarms) == {
            "havoc-staging-transcoder-queue-depth-ge-1",
            "havoc-staging-transcoder-queue-depth-ge-25",
            "havoc-staging-transcoder-queue-depth-zero",
        }

    def test_alarms_watch_the_conventional_metric(self):
        autoscaling, cloudwatch = _clients()
        apply_autoscaling(_ctx(), SCALING, autoscaling, cloudwatch)

        for call in cloudwatch.put_metric_alarm.call_args_list:
            kwargs = call.kwargs
            assert kwargs["Namespace"] == "havoc-staging"
            assert kwargs["MetricName"] == "queue_depth"
            assert kwargs["Dimensions"] == [{"Name": "Service", "Value": "transcoder"}]
            # A stopped environment publishes no metric and must never
            # scale itself back up.
            assert kwargs["TreatMissingData"] == "notBreaching"

    def test_scale_in_alarm_requires_sustained_empty_queue(self):
        autoscaling, cloudwatch = _clients()
        apply_autoscaling(_ctx(), SCALING, autoscaling, cloudwatch)

        alarm = next(
            call.kwargs
            for call in cloudwatch.put_metric_alarm.call_args_list
            if call.kwargs["AlarmName"].endswith("-zero")
        )
        assert alarm["ComparisonOperator"] == "LessThanOrEqualToThreshold"
        assert alarm["Threshold"] == 0
        assert alarm["EvaluationPeriods"] > 1

    def test_stale_policies_and_alarms_are_deleted(self):
        autoscaling, cloudwatch = _clients()
        autoscaling.describe_scaling_policies.return_value = {
            "ScalingPolicies": [{"PolicyName": "queue-depth-ge-50"}]
        }
        stale_alarm = "havoc-staging-transcoder-queue-depth-ge-50"

        def describe_alarms(AlarmNamePrefix):  # noqa: N803 — boto3 kwarg name
            matches = stale_alarm.startswith(AlarmNamePrefix)
            return {"MetricAlarms": [{"AlarmName": stale_alarm}] if matches else []}

        cloudwatch.describe_alarms.side_effect = describe_alarms

        apply_autoscaling(_ctx(), SCALING, autoscaling, cloudwatch)

        autoscaling.delete_scaling_policy.assert_called_once_with(
            PolicyName="queue-depth-ge-50",
            ServiceNamespace="ecs",
            ResourceId="service/havoc-staging-cluster/transcoder",
            ScalableDimension="ecs:service:DesiredCount",
        )
        cloudwatch.delete_alarms.assert_called_once_with(
            AlarmNames=["havoc-staging-transcoder-queue-depth-ge-50"]
        )

    def test_removed_scaling_block_deregisters_target_and_deletes_alarms(self):
        autoscaling, cloudwatch = _clients()
        autoscaling.describe_scalable_targets.return_value = {
            "ScalableTargets": [{"ResourceId": "service/havoc-staging-cluster/transcoder"}]
        }
        cloudwatch.describe_alarms.return_value = {
            "MetricAlarms": [{"AlarmName": "havoc-staging-transcoder-queue-depth-ge-1"}]
        }

        apply_autoscaling(_ctx(scaling_config={}), {}, autoscaling, cloudwatch)

        assert autoscaling.deregister_scalable_target.call_count == 2  # both services
        autoscaling.register_scalable_target.assert_not_called()
        assert cloudwatch.delete_alarms.called

    def test_unscaled_service_with_no_live_state_is_untouched(self):
        autoscaling, cloudwatch = _clients()
        apply_autoscaling(_ctx(scaling_config={}), {}, autoscaling, cloudwatch)

        autoscaling.deregister_scalable_target.assert_not_called()
        cloudwatch.delete_alarms.assert_not_called()

    def test_dry_run_makes_no_aws_calls(self):
        autoscaling, cloudwatch = _clients()
        apply_autoscaling(_ctx(dry_run=True), SCALING, autoscaling, cloudwatch)

        autoscaling.register_scalable_target.assert_not_called()
        autoscaling.put_scaling_policy.assert_not_called()
        cloudwatch.put_metric_alarm.assert_not_called()


class TestAutoscaleEnvironmentInjection:
    """The scaling block is the single autoscaling switch: it injects the
    AUTOSCALE_* variables into the task environment (task_definition.py)."""

    def test_scaling_block_injects_namespace_and_services(self):
        env = get_environment_variables(_ctx(), "transcoder")

        assert env["AUTOSCALE_NAMESPACE"] == "havoc-staging"
        assert env["AUTOSCALE_SERVICES"] == "transcoder"

    def test_injection_overrides_deploy_toml_defaults(self):
        ctx = _ctx(
            config={
                **CONFIG,
                "environment": {"AUTOSCALE_NAMESPACE": "", "AUTOSCALE_SERVICES": ""},
            }
        )
        env = get_environment_variables(ctx, "web")

        assert env["AUTOSCALE_NAMESPACE"] == "havoc-staging"
        assert env["AUTOSCALE_SERVICES"] == "transcoder"

    def test_no_scaling_block_keeps_deploy_toml_defaults(self):
        ctx = _ctx(
            config={
                **CONFIG,
                "environment": {"AUTOSCALE_NAMESPACE": "", "AUTOSCALE_SERVICES": ""},
            },
            scaling_config={},
        )
        env = get_environment_variables(ctx, "web")

        assert env["AUTOSCALE_NAMESPACE"] == ""
        assert env["AUTOSCALE_SERVICES"] == ""


class TestAgeTrigger:
    """A step's age_seconds adds a second alarm on the step's own policy."""

    @staticmethod
    def _clients_with_distinct_policy_arns():
        autoscaling, cloudwatch = _clients()
        autoscaling.put_scaling_policy.side_effect = lambda **kw: {
            "PolicyARN": f"arn:policy/{kw['PolicyName']}"
        }
        return autoscaling, cloudwatch

    def test_null_age_leaves_every_call_unchanged(self):
        without_key = _clients()
        with_null = _clients()
        null_scaling = {
            "transcoder": {
                **SCALING["transcoder"],
                "steps": [{**step, "age_seconds": None} for step in SCALING["transcoder"]["steps"]],
            }
        }

        apply_autoscaling(_ctx(), SCALING, *without_key)
        apply_autoscaling(_ctx(scaling_config=null_scaling), null_scaling, *with_null)

        for before, after in zip(without_key, with_null, strict=True):
            assert before.mock_calls == after.mock_calls

    def test_age_alarm_triggers_the_steps_policy(self):
        autoscaling, cloudwatch = self._clients_with_distinct_policy_arns()
        apply_autoscaling(
            _ctx(scaling_config=SCALING_WITH_AGE), SCALING_WITH_AGE, autoscaling, cloudwatch
        )

        policies = [
            call.kwargs["PolicyName"] for call in autoscaling.put_scaling_policy.call_args_list
        ]
        assert sorted(policies) == ["queue-depth-ge-1", "queue-depth-ge-25", "queue-depth-scale-in"]

        alarms = {
            call.kwargs["AlarmName"]: call.kwargs
            for call in cloudwatch.put_metric_alarm.call_args_list
        }
        assert set(alarms) == {
            "havoc-staging-transcoder-queue-depth-ge-1",
            "havoc-staging-transcoder-queue-depth-ge-25",
            AGE_ALARM,
            "havoc-staging-transcoder-queue-depth-zero",
        }
        age = alarms[AGE_ALARM]
        assert age["AlarmActions"] == ["arn:policy/queue-depth-ge-25"]
        assert age["MetricName"] == "oldest_job_age_seconds"
        assert age["Namespace"] == "havoc-staging"
        assert age["Dimensions"] == [{"Name": "Service", "Value": "transcoder"}]
        assert age["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
        assert age["Threshold"] == 600
        assert age["EvaluationPeriods"] == 1
        # The datum is omitted while the queue is empty: missing data must
        # read as "no scale", never as a breach.
        assert age["TreatMissingData"] == "notBreaching"

    def test_depth_and_scale_in_alarms_stay_on_queue_depth(self):
        autoscaling, cloudwatch = self._clients_with_distinct_policy_arns()
        apply_autoscaling(
            _ctx(scaling_config=SCALING_WITH_AGE), SCALING_WITH_AGE, autoscaling, cloudwatch
        )

        for call in cloudwatch.put_metric_alarm.call_args_list:
            if call.kwargs["AlarmName"] != AGE_ALARM:
                assert call.kwargs["MetricName"] == "queue_depth"

    @staticmethod
    def _live_alarm(cloudwatch, name: str) -> None:
        def describe_alarms(AlarmNamePrefix):  # noqa: N803 — boto3 kwarg name
            matches = name.startswith(AlarmNamePrefix)
            return {"MetricAlarms": [{"AlarmName": name}] if matches else []}

        cloudwatch.describe_alarms.side_effect = describe_alarms

    def test_configured_age_alarm_survives_drift_correction(self):
        autoscaling, cloudwatch = _clients()
        self._live_alarm(cloudwatch, AGE_ALARM)

        apply_autoscaling(
            _ctx(scaling_config=SCALING_WITH_AGE), SCALING_WITH_AGE, autoscaling, cloudwatch
        )

        cloudwatch.delete_alarms.assert_not_called()

    def test_age_alarm_is_deleted_once_dropped_from_config(self):
        autoscaling, cloudwatch = _clients()
        self._live_alarm(cloudwatch, AGE_ALARM)

        apply_autoscaling(_ctx(), SCALING, autoscaling, cloudwatch)

        cloudwatch.delete_alarms.assert_called_once_with(AlarmNames=[AGE_ALARM])
