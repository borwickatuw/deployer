"""Characterization tests for the DEPLOY path of deployer.deploy.service.

These are characterization pins, not endorsements. They record what
``deploy/service.py`` does **today** so that the 53e-5b refactor can be shown to
be behaviour-preserving. Where the current behaviour looks wrong it is pinned
anyway and called out in a comment on the test; nothing here is a fix.

``service.py`` had **no test file at all** before this one. ``test_deploy_deployer.py``
monkeypatches this module's names at ``deployer.py``'s bindings and asserts
orchestration, so not one line of the module's own bodies was executed.

What is pinned here -- the seven deploy-path functions:
``_get_deployment_config``, ``DeploymentError.__init__``,
``_ensure_az_rebalancing_disabled``, ``service_exists``,
``register_task_definition``, ``create_service`` and ``deploy_services``.
The wait path (``wait_for_services``/``_wait_for_service_stable`` and friends)
belongs to ``test_deploy_service_wait.py`` and is deliberately untouched here.

Eight pins exist specifically to make 53e-5b's extractions verifiable:

1. **Both dry-run arms** of ``create_service`` and ``deploy_services``, and both
   **interruptible** arms of ``create_service``. The dry-run arms are early
   returns that skip *different* amounts of work in the two functions --
   ``create_service`` returns after building the whole parameter dict, so its
   network guard still fires under ``--dry-run``; ``deploy_services`` short-circuits
   ``service_exists`` to ``True`` and therefore prints an **update** even for a
   service that does not exist.
2. **Circuit breaker on and off**, at both injection points -- ``create_service``
   and the update arm of ``deploy_services``. Both mutate the *already-built*
   ``deploymentConfiguration`` sub-dict in place.
3. **Per-service vs default target group**, including the ``or`` fallthrough on an
   empty per-service entry, and the health-check grace period -- which lives
   *inside* the ``if target_group_arn`` block, so no target group means no grace
   period either.
4. **Service discovery on and off** -- the ``serviceRegistries`` injection in both
   ``create_service`` and the update arm of ``deploy_services``. Only
   ``registryArn`` is sent; no ``containerPort``.
5. **The missing-network ``RuntimeError``** in ``create_service``, for each of the
   three falsy combinations, and its ``log_error`` line.
6. **The ``max_percent <= 100`` AZ-rebalance trigger** -- on ``create_service``
   after creation, and on the update arm of ``deploy_services`` where it is
   additionally gated on ``not ctx.dry_run``.
7. **The ``continue``-on-``ClientError`` swallow** at the end of the update arm.
   The ``continue`` is already a no-op -- it is the last statement in the loop
   body -- so what actually matters is that the exception is swallowed and the
   *next* service still deploys. That is pinned directly.
8. **The ``create`` vs ``update`` branch**, including the extra ``log_status``
   ``deploy_services`` prints after ``create_service`` has already logged its own
   success line.

Also pinned deliberately, without fixing: ``DeploymentError.service_name`` and
``.error_type`` are set by ``__init__`` and **read by nobody**. The four raise
sites in this module all pass them; ``bin/deploy.py`` catches bare ``Exception``.
``TestDeploymentError`` is the only reader in the repo, and exists so a later
slice can decide their fate against a pin rather than a guess.

Stubbing is at the outermost boundary -- 53d-2a's recorded rule, so the pins
survive code motion inside the package. Concretely:

* ECS is **moto**, reached through a real ``boto3`` client wrapped in
  ``_RecordingEcs``. moto validates the request against the real ECS API model
  (it rejects a subnet that does not exist, for instance), and the wrapper
  records the exact kwargs -- necessary because moto's ``DescribeServices`` does
  not echo back ``deploymentConfiguration``, ``capacityProviderStrategy`` or
  ``healthCheckGracePeriodSeconds`` from a ``CreateService``. Real VPC subnets
  and security groups are created in moto EC2 for the same reason.
* ``subprocess.run`` is stubbed at the stdlib, autouse, so no test can reach a
  real ``aws`` binary.
* ``_DescribeServicesStub`` is the one hand-rolled client. moto's ECS backend
  does not model ``availabilityZoneRebalancing`` at all, so the ``ENABLED`` arm of
  ``_ensure_az_rebalancing_disabled`` -- the only arm that runs the AWS CLI -- is
  unreachable through moto. Everything else in that function is pinned twice,
  once against the stub and once against moto.

Phase 53f-1 added ``TestHealthCheckConfigSource`` at the end of the file. It is
the odd one out here: it drives ``_build_infra_config`` -- the real producer,
which lives in ``deployer.py`` -- into ``create_service``. 53f-1 pinned that the
``health_check_config`` key ``_load_balancer_params`` reads was one no production
``infra_config`` had ever contained; 53f-4 wired its documented source. See that
class's own docstring.
"""

import re
import subprocess
from dataclasses import asdict, replace

import boto3
import pytest
from botocore.exceptions import ClientError

from deployer.deploy.context import DeploymentContext, InfraConfig
from deployer.deploy.deployer import _build_infra_config
from deployer.deploy.service import (
    DeployedServices,
    DeploymentConfig,
    DeploymentError,
    _compute_service_state_hash,
    _ensure_az_rebalancing_disabled,
    _get_deployment_config,
    _service_is_unchanged,
    create_service,
    deploy_services,
    get_stored_service_state,
    register_task_definition,
    service_exists,
    store_service_state,
    store_service_state_hashes,
)
from deployer.utils.logging import is_verbose, set_verbose

APP = "testapp"
ENVIRONMENT = "staging"
REGION = "us-west-2"
ACCOUNT_ID = "123456789012"
CLUSTER = f"{APP}-{ENVIRONMENT}"
IMAGE_URI = f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{CLUSTER}/web:abc123"

DEFAULT_TG = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT_ID}:targetgroup/default/1111"
WEB_TG = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT_ID}:targetgroup/web/2222"
REGISTRY_ARN = f"arn:aws:servicediscovery:{REGION}:{ACCOUNT_ID}:service/srv-web"

_ANSI = re.compile(r"\033\[[0-9;]*m")


def _plain(captured: str) -> str:
    """Strip ANSI colour from a captured stream."""
    return _ANSI.sub("", captured)


def _lines(captured: str) -> list[str]:
    """Return a captured stream's non-blank lines, without ANSI colour."""
    return [line for line in _plain(captured).splitlines() if line.strip()]


def _client_error(code: str, operation: str = "UpdateService") -> ClientError:
    """Build a botocore ClientError carrying the given error code."""
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class _RunRecorder:
    """Stands in for ``subprocess.run`` -- the outermost boundary for the AWS CLI."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        return subprocess.CompletedProcess(
            args=list(cmd),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )

    @property
    def argv(self) -> list[str]:
        """The single argv emitted, asserting there was exactly one call."""
        assert len(self.calls) == 1, f"expected 1 call, got {len(self.calls)}"
        return self.calls[0][0]


class _RecordingEcs:
    """Records every ECS call, then delegates to the real (moto-backed) client.

    moto is still the thing answering, so the request is validated against the
    real ECS API model. The recording exists because moto's DescribeServices
    drops several CreateService fields on the floor.

    ``fail_next`` queues an exception for an operation. It is the only way to
    make ``UpdateService`` fail for a service that ``DescribeServices`` reports
    ACTIVE, which is exactly the state ``deploy_services``' ``except ClientError``
    arm needs.
    """

    def __init__(self, client):
        self._client = client
        self.calls: list[tuple[str, dict]] = []
        self._errors: dict[str, list[Exception]] = {}

    def fail_next(self, operation: str, error: Exception) -> None:
        """Queue ``error`` to be raised by the next unqueued call to ``operation``."""
        self._errors.setdefault(operation, []).append(error)

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr

        def _record(**kwargs):
            self.calls.append((name, kwargs))
            queued = self._errors.get(name)
            if queued:
                raise queued.pop(0)
            return attr(**kwargs)

        return _record

    @property
    def operations(self) -> list[str]:
        return [name for name, _ in self.calls]

    def all_params(self, operation: str) -> list[dict]:
        return [kwargs for name, kwargs in self.calls if name == operation]

    def params(self, operation: str) -> dict:
        """The single set of kwargs for ``operation``, asserting there was one call."""
        matches = self.all_params(operation)
        assert len(matches) == 1, f"expected 1 {operation} call, got {len(matches)}"
        return matches[0]


class _DescribeServicesStub:
    """An ECS client that answers ``describe_services`` from a canned payload.

    Used only where moto cannot produce the shape -- see the module docstring on
    ``availabilityZoneRebalancing``.
    """

    def __init__(self, services: list[dict], error: Exception | None = None):
        self.services = services
        self.error = error
        self.calls: list[dict] = []

    def describe_services(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"services": self.services}


class _Aws:
    """The moto-backed AWS the deploy path talks to."""

    def __init__(self, client: _RecordingEcs, subnet_id: str, security_group_id: str):
        self.client = client
        self.subnet_id = subnet_id
        self.security_group_id = security_group_id


@pytest.fixture(autouse=True)
def _quiet_debug():
    """``log_debug`` reads a process-global; don't let another test's verbose leak in."""
    was = is_verbose()
    set_verbose(False)
    yield
    set_verbose(was)


@pytest.fixture(autouse=True)
def run(monkeypatch) -> _RunRecorder:
    """Stub ``subprocess.run`` at the stdlib. Autouse: no test may reach a real `aws`."""
    recorder = _RunRecorder()
    monkeypatch.setattr(subprocess, "run", recorder)
    return recorder


@pytest.fixture
def aws(mocked_aws) -> _Aws:
    """A moto cluster with a real VPC subnet and security group.

    moto's CreateService validates ``awsvpcConfiguration`` against EC2, so the
    ids have to exist -- a made-up ``subnet-1`` is rejected.
    """
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
    sg_id = ec2.create_security_group(
        GroupName="deployer-test", Description="deployer test", VpcId=vpc_id
    )["GroupId"]

    ecs = boto3.client("ecs", region_name=REGION)
    ecs.create_cluster(clusterName=CLUSTER)
    return _Aws(_RecordingEcs(ecs), subnet_id, sg_id)


def _ctx(
    aws: _Aws,
    *,
    services: dict | None = None,
    service_config: dict | None = None,
    infra: dict | None = None,
    dry_run: bool = False,
    cluster_name: str = CLUSTER,
    ecs_client=None,
) -> DeploymentContext:
    """A DeploymentContext wired to moto, with network config that works."""
    infra_config = replace(
        InfraConfig(subnet_ids=[aws.subnet_id], security_group_id=aws.security_group_id),
        **(infra or {}),
    )
    return DeploymentContext(
        ecs_client=aws.client if ecs_client is None else ecs_client,
        cluster_name=cluster_name,
        config={"services": services if services is not None else {"web": {}}},
        service_config=service_config or {},
        infra_config=infra_config,
        app_name=APP,
        environment=ENVIRONMENT,
        region=REGION,
        account_id=ACCOUNT_ID,
        # Empty env_config keeps the module system out of build_task_definition;
        # the module merge is task_definition.py's business, not service.py's.
        env_config={},
        dry_run=dry_run,
    )


def _make_service(aws: _Aws, name: str = "web", *, cluster: str = CLUSTER) -> str:
    """Create a real moto service so ``service_exists`` has something to find."""
    task_def = aws.client.register_task_definition(
        family=f"{CLUSTER}-{name}",
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256",
        memory="512",
        containerDefinitions=[{"name": name, "image": IMAGE_URI, "essential": True}],
    )
    arn = task_def["taskDefinition"]["taskDefinitionArn"]
    aws.client.create_service(
        cluster=cluster,
        serviceName=name,
        taskDefinition=arn,
        desiredCount=1,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": [aws.subnet_id],
                "securityGroups": [aws.security_group_id],
                "assignPublicIp": "DISABLED",
            }
        },
    )
    aws.client.calls.clear()
    return arn


class TestGetDeploymentConfig:
    """``_get_deployment_config`` -- the TOML-key to field-name translation."""

    def test_missing_section_gives_defaults(self):
        assert _get_deployment_config(InfraConfig()) == DeploymentConfig(
            min_healthy=100, max_percent=200, circuit_breaker=False, circuit_rollback=True
        )

    def test_empty_section_gives_defaults(self):
        assert _get_deployment_config(InfraConfig(deployment_config={})) == DeploymentConfig()

    def test_all_four_keys_are_translated(self):
        cfg = _get_deployment_config(
            InfraConfig(
                deployment_config={
                    "minimum_healthy_percent": 50,
                    "maximum_percent": 100,
                    "circuit_breaker_enabled": True,
                    "circuit_breaker_rollback": False,
                }
            )
        )
        assert cfg == DeploymentConfig(
            min_healthy=50, max_percent=100, circuit_breaker=True, circuit_rollback=False
        )

    def test_partial_section_keeps_other_defaults(self):
        cfg = _get_deployment_config(InfraConfig(deployment_config={"maximum_percent": 100}))
        assert cfg == DeploymentConfig(max_percent=100)

    def test_unknown_keys_are_ignored(self):
        cfg = _get_deployment_config(InfraConfig(deployment_config={"wait_for_steady_state": True}))
        assert cfg == DeploymentConfig()

    def test_field_names_are_not_accepted_as_keys(self):
        # The dict is keyed on the *TOML* name. Writing the dataclass field name
        # in deploy.toml is silently ignored rather than rejected.
        cfg = _get_deployment_config(InfraConfig(deployment_config={"min_healthy": 42}))
        assert cfg == DeploymentConfig(min_healthy=100)

    def test_values_are_not_validated(self):
        # No range check: a nonsense percentage reaches the ECS API unaltered.
        cfg = _get_deployment_config(InfraConfig(deployment_config={"minimum_healthy_percent": -5}))
        assert cfg.min_healthy == -5

    def test_other_infra_config_keys_are_untouched(self):
        # The `maximum_percent` half of this pin -- a deployment key written
        # at the top level instead of inside `deployment_config` -- is no
        # longer constructible: InfraConfig has no such field, so 53f-4 moved
        # that mistake from silently ignored to a TypeError at build time.
        cfg = _get_deployment_config(InfraConfig(subnet_ids=["subnet-x"]))
        assert cfg == DeploymentConfig()

    # -- per-service overrides ([services.X] minimum_healthy_percent /
    # -- maximum_percent, overlaid per key on the environment values) ----

    def test_none_service_toml_keeps_the_environment_values(self):
        infra = InfraConfig(
            deployment_config={"minimum_healthy_percent": 100, "maximum_percent": 200}
        )
        assert _get_deployment_config(infra, None) == _get_deployment_config(infra)

    def test_empty_service_toml_keeps_the_environment_values(self):
        infra = InfraConfig(
            deployment_config={"minimum_healthy_percent": 100, "maximum_percent": 200}
        )
        assert _get_deployment_config(infra, {}) == DeploymentConfig(
            min_healthy=100, max_percent=200
        )

    def test_service_overrides_both_keys(self):
        infra = InfraConfig(
            deployment_config={
                "minimum_healthy_percent": 100,
                "maximum_percent": 200,
                "circuit_breaker_enabled": True,
            }
        )
        cfg = _get_deployment_config(infra, {"minimum_healthy_percent": 0, "maximum_percent": 100})
        assert cfg == DeploymentConfig(min_healthy=0, max_percent=100, circuit_breaker=True)

    def test_service_setting_one_key_inherits_the_other(self):
        infra = InfraConfig(
            deployment_config={"minimum_healthy_percent": 100, "maximum_percent": 200}
        )
        cfg = _get_deployment_config(infra, {"minimum_healthy_percent": 0})
        assert cfg == DeploymentConfig(min_healthy=0, max_percent=200)

        cfg = _get_deployment_config(infra, {"maximum_percent": 150})
        assert cfg == DeploymentConfig(min_healthy=100, max_percent=150)

    def test_service_override_applies_over_environment_defaults_too(self):
        # No [deployment] section at all: the override still lands on top of
        # the dataclass defaults.
        cfg = _get_deployment_config(
            InfraConfig(), {"minimum_healthy_percent": 0, "maximum_percent": 100}
        )
        assert cfg == DeploymentConfig(min_healthy=0, max_percent=100)

    def test_unrelated_service_keys_are_ignored(self):
        cfg = _get_deployment_config(InfraConfig(), {"image": "web", "port": 8000})
        assert cfg == DeploymentConfig()

    def test_circuit_breaker_keys_have_no_per_service_form(self):
        cfg = _get_deployment_config(
            InfraConfig(), {"circuit_breaker_enabled": True, "circuit_breaker_rollback": False}
        )
        assert cfg == DeploymentConfig(circuit_breaker=False, circuit_rollback=True)


class TestDeploymentError:
    """``DeploymentError.__init__``.

    ``service_name`` and ``error_type`` are set at all four raise sites in
    ``service.py`` and read **nowhere** -- not in ``src``, not in ``bin``, not in
    ``tests`` other than right here. ``bin/deploy.py:79`` catches bare
    ``Exception``. Pinned so a later slice can delete or use them deliberately.
    """

    def test_message_reaches_str(self):
        assert str(DeploymentError("boom", "web")) == "boom"

    def test_service_name_is_stored(self):
        assert DeploymentError("boom", "web").service_name == "web"

    def test_error_type_defaults_to_none(self):
        assert DeploymentError("boom", "web").error_type is None

    def test_error_type_is_stored(self):
        assert DeploymentError("boom", "web", "missing_ssm_parameters").error_type == (
            "missing_ssm_parameters"
        )

    def test_is_an_exception(self):
        with pytest.raises(DeploymentError) as excinfo:
            raise DeploymentError("boom", "web", "no_capacity")
        assert isinstance(excinfo.value, Exception)
        assert excinfo.value.args == ("boom",)

    def test_service_name_is_required(self):
        with pytest.raises(TypeError):
            DeploymentError("boom")


class TestEnsureAzRebalancingDisabled:
    """``_ensure_az_rebalancing_disabled`` -- the AWS-CLI escape hatch."""

    def test_no_services_returns_false(self, run):
        client = _DescribeServicesStub([])
        assert _ensure_az_rebalancing_disabled(client, CLUSTER, "web") is False
        assert run.calls == []

    def test_describe_is_called_with_cluster_and_service(self, run):
        client = _DescribeServicesStub([])
        _ensure_az_rebalancing_disabled(client, CLUSTER, "web")
        assert client.calls == [{"cluster": CLUSTER, "services": ["web"]}]

    def test_inactive_service_returns_false(self, run):
        client = _DescribeServicesStub(
            [{"status": "INACTIVE", "availabilityZoneRebalancing": "ENABLED"}]
        )
        assert _ensure_az_rebalancing_disabled(client, CLUSTER, "web") is False
        assert run.calls == []

    def test_absent_field_defaults_to_disabled(self, run):
        client = _DescribeServicesStub([{"status": "ACTIVE"}])
        assert _ensure_az_rebalancing_disabled(client, CLUSTER, "web") is False
        assert run.calls == []

    def test_already_disabled_returns_false(self, run):
        client = _DescribeServicesStub(
            [{"status": "ACTIVE", "availabilityZoneRebalancing": "DISABLED"}]
        )
        assert _ensure_az_rebalancing_disabled(client, CLUSTER, "web") is False
        assert run.calls == []

    def test_only_the_first_service_is_inspected(self, run):
        # describe_services was asked for one name, but the code indexes [0]
        # unconditionally rather than matching on serviceName the way
        # service_exists() does.
        client = _DescribeServicesStub(
            [
                {"serviceName": "other", "status": "ACTIVE"},
                {
                    "serviceName": "web",
                    "status": "ACTIVE",
                    "availabilityZoneRebalancing": "ENABLED",
                },
            ]
        )
        assert _ensure_az_rebalancing_disabled(client, CLUSTER, "web") is False
        assert run.calls == []

    def test_enabled_runs_the_cli_and_returns_true(self, run, capsys):
        client = _DescribeServicesStub(
            [{"status": "ACTIVE", "availabilityZoneRebalancing": "ENABLED"}]
        )
        assert _ensure_az_rebalancing_disabled(client, CLUSTER, "web") is True
        assert run.argv == [
            "aws",
            "ecs",
            "update-service",
            "--cluster",
            CLUSTER,
            "--service",
            "web",
            "--availability-zone-rebalancing",
            "DISABLED",
            "--no-force-new-deployment",
            "--query",
            "service.serviceName",
            "--output",
            "text",
        ]
        assert _lines(capsys.readouterr().out) == ["  web [AZ rebalancing disabled]"]

    def test_cli_is_run_without_check_and_with_captured_text(self, run):
        client = _DescribeServicesStub(
            [{"status": "ACTIVE", "availabilityZoneRebalancing": "ENABLED"}]
        )
        _ensure_az_rebalancing_disabled(client, CLUSTER, "web")
        assert run.calls[0][1] == {"capture_output": True, "text": True, "check": False}

    def test_cli_failure_warns_and_returns_false(self, run, capsys):
        run.returncode = 254
        run.stderr = "AccessDenied"
        client = _DescribeServicesStub(
            [{"status": "ACTIVE", "availabilityZoneRebalancing": "ENABLED"}]
        )
        assert _ensure_az_rebalancing_disabled(client, CLUSTER, "web") is False
        assert _lines(capsys.readouterr().out) == [
            "  ⚠ Could not disable AZ rebalancing for web: AccessDenied"
        ]

    def test_client_error_is_swallowed(self, run):
        client = _DescribeServicesStub([], error=_client_error("AccessDenied", "DescribeServices"))
        assert _ensure_az_rebalancing_disabled(client, CLUSTER, "web") is False

    def test_non_client_error_propagates(self, run):
        # Only ClientError is caught; anything else escapes to the caller, which
        # in create_service()/deploy_services() means the whole deploy aborts.
        client = _DescribeServicesStub([], error=RuntimeError("socket"))
        with pytest.raises(RuntimeError):
            _ensure_az_rebalancing_disabled(client, CLUSTER, "web")

    def test_missing_cluster_is_swallowed_against_moto(self, aws, run):
        assert _ensure_az_rebalancing_disabled(aws.client, "no-such-cluster", "web") is False

    def test_real_service_reports_false_against_moto(self, aws, run):
        _make_service(aws)
        assert _ensure_az_rebalancing_disabled(aws.client, CLUSTER, "web") is False


class TestServiceExists:
    """``service_exists`` -- name match plus a not-INACTIVE check."""

    def test_existing_service_is_true(self, aws):
        _make_service(aws)
        assert service_exists(aws.client, CLUSTER, "web") is True

    def test_absent_service_is_false(self, aws):
        assert service_exists(aws.client, CLUSTER, "web") is False

    def test_deleted_service_is_false(self, aws):
        _make_service(aws)
        aws.client.delete_service(cluster=CLUSTER, service="web", force=True)
        assert service_exists(aws.client, CLUSTER, "web") is False

    def test_missing_cluster_raises_naming_the_cluster(self, aws):
        # A typo in the cluster name used to be answered False, which is what
        # "the service does not exist yet" looks like -- so deploy_services()
        # went on to *create* every service in a cluster that does not exist.
        _make_service(aws)
        with pytest.raises(RuntimeError, match="no-such-cluster"):
            service_exists(aws.client, "no-such-cluster", "web")

    def test_other_service_in_the_response_does_not_match(self):
        client = _DescribeServicesStub([{"serviceName": "worker", "status": "ACTIVE"}])
        assert service_exists(client, CLUSTER, "web") is False

    def test_match_is_found_past_the_first_entry(self):
        client = _DescribeServicesStub(
            [
                {"serviceName": "worker", "status": "ACTIVE"},
                {"serviceName": "web", "status": "ACTIVE"},
            ]
        )
        assert service_exists(client, CLUSTER, "web") is True

    def test_inactive_named_match_is_false(self):
        client = _DescribeServicesStub([{"serviceName": "web", "status": "INACTIVE"}])
        assert service_exists(client, CLUSTER, "web") is False

    def test_draining_named_match_is_true(self):
        # Anything that is not the literal "INACTIVE" counts as existing.
        client = _DescribeServicesStub([{"serviceName": "web", "status": "DRAINING"}])
        assert service_exists(client, CLUSTER, "web") is True

    def test_client_error_raises_and_chains(self):
        # describe_services answers a genuinely absent service with an empty
        # list, so every ClientError arriving here is a failure and none of them
        # is an absence -- there is nothing to report as False.
        error = _client_error("AccessDenied", "DescribeServices")
        client = _DescribeServicesStub([], error=error)

        with pytest.raises(RuntimeError, match="AccessDenied") as exc:
            service_exists(client, CLUSTER, "web")

        assert exc.value.__cause__ is error

    def test_query_shape(self, aws):
        service_exists(aws.client, CLUSTER, "web")
        assert aws.client.params("describe_services") == {
            "cluster": CLUSTER,
            "services": ["web"],
        }


class TestRegisterTaskDefinition:
    """``register_task_definition`` -- both dry-run arms."""

    def test_registers_and_returns_the_arn(self, aws, capsys):
        ctx = _ctx(aws)
        arn = register_task_definition(ctx, "web", IMAGE_URI)
        assert arn == f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task-definition/{CLUSTER}-web:1"
        assert _lines(capsys.readouterr().out) == ["  web task definition registered [done]"]

    def test_registered_family_and_sizing(self, aws):
        ctx = _ctx(aws)
        register_task_definition(ctx, "web", IMAGE_URI)
        params = aws.client.params("register_task_definition")
        assert params["family"] == f"{CLUSTER}-web"
        assert params["cpu"] == "256"
        assert params["memory"] == "512"
        assert params["networkMode"] == "awsvpc"
        assert params["containerDefinitions"][0]["image"] == IMAGE_URI

    def test_second_registration_is_a_new_revision(self, aws):
        ctx = _ctx(aws)
        register_task_definition(ctx, "web", IMAGE_URI)
        assert register_task_definition(ctx, "web", IMAGE_URI).endswith(":2")

    def test_dry_run_returns_a_fabricated_arn(self, aws, capsys):
        ctx = _ctx(aws, dry_run=True)
        arn = register_task_definition(ctx, "web", IMAGE_URI)
        # The ":dry-run" suffix stands where a revision number would be. It is
        # handed to create_service/update_service printers downstream.
        assert arn == f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task-definition/{CLUSTER}-web:dry-run"
        assert _lines(capsys.readouterr().out) == [
            f"  [dry-run] aws ecs register-task-definition --family {CLUSTER}-web"
        ]

    def test_dry_run_calls_no_aws(self, aws):
        ctx = _ctx(aws, dry_run=True)
        register_task_definition(ctx, "web", IMAGE_URI)
        assert aws.client.operations == []

    def test_dry_run_still_builds_the_task_definition(self, aws):
        # build_task_definition() runs before the dry_run check, so a sizing
        # error is raised under --dry-run too.
        ctx = _ctx(
            aws,
            services={"web": {"min_cpu": 1024}},
            service_config={"web": {"cpu": 256, "memory": 512}},
            dry_run=True,
        )
        with pytest.raises(ValueError, match="below minimum"):
            register_task_definition(ctx, "web", IMAGE_URI)

    def test_credential_mode_is_inert_without_modules(self, aws):
        # With no module sections and an empty env_config, "migrate" and "app"
        # produce byte-identical task definitions. The mode only matters once
        # task_definition.py's module system is engaged.
        ctx = _ctx(aws)
        register_task_definition(ctx, "web", IMAGE_URI, credential_mode="app")
        register_task_definition(ctx, "web", IMAGE_URI, credential_mode="migrate")
        first, second = aws.client.all_params("register_task_definition")
        assert first == second

    def test_service_name_names_the_container(self, aws):
        ctx = _ctx(aws, services={"worker": {}})
        register_task_definition(ctx, "worker", IMAGE_URI)
        params = aws.client.params("register_task_definition")
        assert params["containerDefinitions"][0]["name"] == "worker"
        assert params["family"] == f"{CLUSTER}-worker"


class TestCreateServiceNetworkGuard:
    """The missing-network ``RuntimeError`` -- must-pin #5."""

    @pytest.mark.parametrize(
        "infra",
        [
            pytest.param({"subnet_ids": []}, id="no-subnets"),
            pytest.param({"security_group_id": ""}, id="no-security-group"),
            pytest.param({"subnet_ids": [], "security_group_id": ""}, id="neither"),
        ],
    )
    def test_raises_runtime_error(self, aws, infra, capsys):
        ctx = _ctx(aws, infra=infra)
        with pytest.raises(RuntimeError, match="^Missing network configuration$"):
            create_service(ctx, "web", "arn:task-def")
        assert _lines(capsys.readouterr().out) == [
            "  ✗ Missing network configuration in infra_config (subnet_ids, security_group_id)."
        ]

    def test_absent_keys_also_raise(self, aws):
        ctx = _ctx(aws)
        ctx = replace(ctx, infra_config=InfraConfig())
        with pytest.raises(RuntimeError, match="Missing network configuration"):
            create_service(ctx, "web", "arn:task-def")

    def test_guard_fires_under_dry_run_too(self, aws):
        # The dry-run early return is at the *end* of create_service(), so
        # --dry-run does not bypass the guard. deploy_services() never reaches
        # create_service() under --dry-run, so this only bites direct callers.
        ctx = _ctx(aws, infra={"subnet_ids": []}, dry_run=True)
        with pytest.raises(RuntimeError, match="Missing network configuration"):
            create_service(ctx, "web", "arn:task-def")

    def test_guard_fires_before_any_aws_call(self, aws):
        ctx = _ctx(aws, infra={"subnet_ids": []})
        with pytest.raises(RuntimeError):
            create_service(ctx, "web", "arn:task-def")
        assert aws.client.operations == []


class TestCreateService:
    """``create_service`` -- the parameter dict, arm by arm."""

    def test_baseline_parameters(self, aws, capsys):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws)
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service") == {
            "cluster": CLUSTER,
            "serviceName": "web",
            "taskDefinition": arn,
            "desiredCount": 1,
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": [aws.subnet_id],
                    "securityGroups": [aws.security_group_id],
                    "assignPublicIp": "DISABLED",
                }
            },
            "deploymentConfiguration": {
                "minimumHealthyPercent": 100,
                "maximumPercent": 200,
            },
            "launchType": "FARGATE",
        }
        assert _lines(capsys.readouterr().out) == ["  web service created [done]"]

    def test_service_really_exists_afterwards(self, aws):
        arn = _make_service(aws, "seed")
        create_service(_ctx(aws), "web", arn)
        assert service_exists(aws.client, CLUSTER, "web") is True

    def test_desired_count_comes_from_merged_sizing(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, services={"web": {"replicas": 2}}, service_config={"web": {"replicas": 4}})
        create_service(ctx, "web", arn)
        # Environment sizing wins over deploy.toml.
        assert aws.client.params("create_service")["desiredCount"] == 4

    def test_deployment_config_is_threaded_through(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            infra={
                "deployment_config": {"minimum_healthy_percent": 50, "maximum_percent": 150},
            },
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["deploymentConfiguration"] == {
            "minimumHealthyPercent": 50,
            "maximumPercent": 150,
        }

    def test_per_service_override_reaches_the_create_call(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"minimum_healthy_percent": 0}},
            infra={
                "deployment_config": {"minimum_healthy_percent": 100, "maximum_percent": 200},
            },
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["deploymentConfiguration"] == {
            "minimumHealthyPercent": 0,
            "maximumPercent": 200,
        }

    def test_per_service_max_percent_100_triggers_the_az_check_on_create(self, aws):
        # The AZ-rebalance gate keys off the merged dep_cfg, so a per-service
        # maximum_percent = 100 fires it even when the environment says 200.
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"maximum_percent": 100}},
            infra={"deployment_config": {"maximum_percent": 200}},
        )
        create_service(ctx, "web", arn)
        assert aws.client.operations == ["create_service", "describe_services"]

    # -- must-pin #1: interruptible arms ---------------------------------

    def test_interruptible_uses_capacity_provider_strategy(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, services={"web": {"interruptible": True}})
        create_service(ctx, "web", arn)
        params = aws.client.params("create_service")
        assert params["capacityProviderStrategy"] == [
            {"capacityProvider": "FARGATE", "base": 1, "weight": 0},
            {"capacityProvider": "FARGATE_SPOT", "weight": 1},
        ]
        assert "launchType" not in params

    def test_non_interruptible_uses_launch_type(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, services={"web": {"interruptible": False}})
        create_service(ctx, "web", arn)
        params = aws.client.params("create_service")
        assert params["launchType"] == "FARGATE"
        assert "capacityProviderStrategy" not in params

    def test_interruptible_is_read_from_deploy_toml_only(self, aws):
        # The flag is read off the raw [services.web] table, not the merged
        # sizing, so an environment override cannot turn Spot on.
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, services={"web": {}}, service_config={"web": {"interruptible": True}})
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["launchType"] == "FARGATE"

    # -- must-pin #2: circuit breaker ------------------------------------

    def test_circuit_breaker_off_omits_the_key(self, aws):
        arn = _make_service(aws, "seed")
        create_service(_ctx(aws), "web", arn)
        cfg = aws.client.params("create_service")["deploymentConfiguration"]
        assert "deploymentCircuitBreaker" not in cfg

    def test_circuit_breaker_on_injects_enable_and_rollback(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, infra={"deployment_config": {"circuit_breaker_enabled": True}})
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["deploymentConfiguration"] == {
            "minimumHealthyPercent": 100,
            "maximumPercent": 200,
            "deploymentCircuitBreaker": {"enable": True, "rollback": True},
        }

    def test_circuit_rollback_can_be_turned_off(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            infra={
                "deployment_config": {
                    "circuit_breaker_enabled": True,
                    "circuit_breaker_rollback": False,
                }
            },
        )
        create_service(ctx, "web", arn)
        breaker = aws.client.params("create_service")["deploymentConfiguration"][
            "deploymentCircuitBreaker"
        ]
        assert breaker == {"enable": True, "rollback": False}

    def test_circuit_rollback_alone_does_nothing(self, aws):
        # rollback is only ever read inside the `if circuit_breaker` block.
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, infra={"deployment_config": {"circuit_breaker_rollback": False}})
        create_service(ctx, "web", arn)
        cfg = aws.client.params("create_service")["deploymentConfiguration"]
        assert "deploymentCircuitBreaker" not in cfg

    # -- must-pin #3: target groups and grace period ---------------------

    def test_per_service_target_group_wins(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True, "port": 8000}},
            infra={"service_target_groups": {"web": WEB_TG}, "target_group_arn": DEFAULT_TG},
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["loadBalancers"] == [
            {"targetGroupArn": WEB_TG, "containerName": "web", "containerPort": 8000}
        ]

    def test_default_target_group_is_the_fallback(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True, "port": 8000}},
            infra={"service_target_groups": {"worker": WEB_TG}, "target_group_arn": DEFAULT_TG},
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["loadBalancers"][0]["targetGroupArn"] == (
            DEFAULT_TG
        )

    def test_empty_per_service_entry_falls_through(self, aws):
        # `.get(name) or .get("target_group_arn")` -- an empty string is falsy,
        # so an explicitly blank per-service entry silently uses the default.
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True, "port": 8000}},
            infra={"service_target_groups": {"web": ""}, "target_group_arn": DEFAULT_TG},
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["loadBalancers"][0]["targetGroupArn"] == (
            DEFAULT_TG
        )

    def test_no_target_group_means_no_load_balancer_block(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, services={"web": {"load_balanced": True, "port": 8000}})
        create_service(ctx, "web", arn)
        params = aws.client.params("create_service")
        assert "loadBalancers" not in params
        # The grace period lives inside the `if target_group_arn` block, so it
        # disappears with the load balancer. A load-balanced service with no
        # target group is created silently, with no warning.
        assert "healthCheckGracePeriodSeconds" not in params

    def test_grace_period_defaults_to_sixty(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True, "port": 8000}},
            infra={"target_group_arn": DEFAULT_TG},
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["healthCheckGracePeriodSeconds"] == 60

    def test_grace_period_is_configurable(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True, "port": 8000}},
            infra={
                "target_group_arn": DEFAULT_TG,
                "health_check_config": {"grace_period": 300},
            },
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["healthCheckGracePeriodSeconds"] == 300

    def test_not_load_balanced_skips_the_block(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, services={"web": {"port": 8000}}, infra={"target_group_arn": DEFAULT_TG})
        create_service(ctx, "web", arn)
        assert "loadBalancers" not in aws.client.params("create_service")

    def test_load_balanced_without_a_port_skips_the_block(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True}},
            infra={"target_group_arn": DEFAULT_TG},
        )
        create_service(ctx, "web", arn)
        assert "loadBalancers" not in aws.client.params("create_service")

    def test_port_from_environment_sizing_is_not_seen(self, aws):
        # `"port" in service_toml` reads the raw [services.web] table while
        # load_balanced comes from the merged sizing. A port supplied only via
        # SERVICE_CONFIG therefore produces no load balancer registration --
        # the service comes up and never joins the target group.
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True}},
            service_config={"web": {"port": 8000}},
            infra={"target_group_arn": DEFAULT_TG},
        )
        create_service(ctx, "web", arn)
        assert "loadBalancers" not in aws.client.params("create_service")

    def test_container_port_ignores_environment_override(self, aws):
        # containerPort reads service_toml["port"], not the merged value, so an
        # environment override of `port` is applied to the task definition's
        # portMappings but *not* to the target group registration.
        arn = _make_service(aws, "seed")
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True, "port": 8000}},
            service_config={"web": {"port": 9000}},
            infra={"target_group_arn": DEFAULT_TG},
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["loadBalancers"][0]["containerPort"] == 8000

    # -- must-pin #4: service discovery ----------------------------------

    def test_service_discovery_injects_registry_arn_only(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, infra={"service_discovery_registries": {"web": REGISTRY_ARN}})
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["serviceRegistries"] == [
            {"registryArn": REGISTRY_ARN}
        ]

    def test_no_registry_omits_the_key(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, infra={"service_discovery_registries": {"worker": REGISTRY_ARN}})
        create_service(ctx, "web", arn)
        assert "serviceRegistries" not in aws.client.params("create_service")

    def test_empty_registry_arn_omits_the_key(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, infra={"service_discovery_registries": {"web": ""}})
        create_service(ctx, "web", arn)
        assert "serviceRegistries" not in aws.client.params("create_service")

    # -- must-pin #1: dry-run arm ----------------------------------------

    def test_dry_run_prints_and_creates_nothing(self, aws, capsys):
        ctx = _ctx(aws, dry_run=True)
        assert create_service(ctx, "web", "arn:task-def") is None
        assert aws.client.operations == []
        assert _lines(capsys.readouterr().out) == [
            "  [dry-run] aws ecs create-service --service-name web"
        ]

    def test_dry_run_prints_none_of_the_parameters(self, aws, capsys):
        # The whole parameter dict is built and then thrown away: --dry-run
        # tells you nothing about the target group, the breaker or the sizing.
        ctx = _ctx(
            aws,
            services={"web": {"load_balanced": True, "port": 8000, "interruptible": True}},
            infra={
                "target_group_arn": DEFAULT_TG,
                "deployment_config": {"circuit_breaker_enabled": True},
                "service_discovery_registries": {"web": REGISTRY_ARN},
            },
            dry_run=True,
        )
        create_service(ctx, "web", "arn:task-def")
        out = _plain(capsys.readouterr().out)
        assert DEFAULT_TG not in out
        assert REGISTRY_ARN not in out
        assert "FARGATE_SPOT" not in out

    # -- must-pin #6: AZ rebalance trigger -------------------------------

    def test_max_percent_at_100_triggers_the_az_check(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, infra={"deployment_config": {"maximum_percent": 100}})
        create_service(ctx, "web", arn)
        assert aws.client.operations == ["create_service", "describe_services"]

    def test_max_percent_below_100_triggers_the_az_check(self, aws):
        arn = _make_service(aws, "seed")
        ctx = _ctx(aws, infra={"deployment_config": {"maximum_percent": 50}})
        create_service(ctx, "web", arn)
        assert aws.client.operations == ["create_service", "describe_services"]

    def test_default_max_percent_skips_the_az_check(self, aws):
        arn = _make_service(aws, "seed")
        create_service(_ctx(aws), "web", arn)
        assert aws.client.operations == ["create_service"]

    def test_dry_run_never_reaches_the_az_check(self, aws, run):
        ctx = _ctx(aws, infra={"deployment_config": {"maximum_percent": 100}}, dry_run=True)
        create_service(ctx, "web", "arn:task-def")
        assert aws.client.operations == []
        assert run.calls == []


class TestDeployServices:
    """``deploy_services`` -- the loop, both branches, both dry-run arms."""

    def test_empty_service_map_logs_only_the_banner(self, aws, capsys):
        ctx = _ctx(aws, services={})
        assert deploy_services(ctx, {}) == DeployedServices()
        assert aws.client.operations == []
        assert _lines(capsys.readouterr().out) == ["Deploying ECS services..."]

    def test_returns_the_registered_arn_per_service(self, aws):
        """The updated map is what wait_for_stable verifies PRIMARY against."""
        _make_service(aws, "web")
        ctx = _ctx(aws, services={"web": {}, "worker": {}})
        deployed = deploy_services(ctx, {"web": IMAGE_URI, "worker": IMAGE_URI})
        assert set(deployed.updated) == {"web", "worker"}
        assert deployed.updated["web"] == (
            aws.client.all_params("update_service")[0]["taskDefinition"]
        )
        # The create branch reports its ARN too.
        assert deployed.updated["worker"] == aws.client.params("create_service")["taskDefinition"]
        # Both carry a state hash for post-stability storage; nothing skipped.
        assert set(deployed.state_hashes) == {"web", "worker"}
        assert deployed.skipped == []

    def test_dry_run_returns_the_fabricated_arns_and_no_hashes(self, aws):
        ctx = _ctx(aws, dry_run=True)
        deployed = deploy_services(ctx, {"web": IMAGE_URI})
        assert deployed.updated == {
            "web": f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task-definition/{CLUSTER}-web:dry-run"
        }
        # Dry runs never hash (and never read SSM), so nothing to store later.
        assert deployed.state_hashes == {}

    def test_missing_image_uri_skips_the_service_and_fails_the_deploy(self, aws, capsys):
        """A skipped service is a service that was not deployed.

        The skip is right -- there is nothing to deploy -- but the run used to
        return normally, so a deploy missing a service looked complete.
        """
        ctx = _ctx(aws)
        with pytest.raises(RuntimeError, match=r"Failed to deploy service\(s\): web"):
            deploy_services(ctx, {})
        assert aws.client.operations == []
        assert _lines(capsys.readouterr().out) == [
            "Deploying ECS services...",
            "  ✗ No image URI for service web (image: web)",
        ]

    def test_missing_image_uri_does_not_stop_the_loop(self, aws):
        """Every service is still attempted; only the report at the end changes."""
        ctx = _ctx(aws, services={"web": {}, "worker": {}})
        with pytest.raises(RuntimeError, match=r"Failed to deploy service\(s\): web"):
            deploy_services(ctx, {"worker": IMAGE_URI})
        assert service_exists(aws.client, CLUSTER, "worker") is True
        assert service_exists(aws.client, CLUSTER, "web") is False

    def test_image_alias_is_honoured(self, aws):
        ctx = _ctx(aws, services={"web": {"image": "app"}})
        deploy_services(ctx, {"app": IMAGE_URI})
        params = aws.client.params("register_task_definition")
        assert params["containerDefinitions"][0]["image"] == IMAGE_URI

    def test_image_alias_miss_names_both_in_the_error(self, aws, capsys):
        ctx = _ctx(aws, services={"web": {"image": "app"}})
        with pytest.raises(RuntimeError, match=r"Failed to deploy service\(s\): web"):
            deploy_services(ctx, {"web": IMAGE_URI})
        assert "  ✗ No image URI for service web (image: app)" in _lines(capsys.readouterr().out)

    # -- must-pin #8: create vs update -----------------------------------

    def test_absent_service_takes_the_create_branch(self, aws, capsys):
        # The describe now comes first: the skip-unchanged check needs the
        # live service state before a new task-definition revision exists.
        ctx = _ctx(aws)
        deploy_services(ctx, {"web": IMAGE_URI})
        assert aws.client.operations == [
            "describe_services",
            "register_task_definition",
            "create_service",
        ]
        # create_service() logs its own success line and deploy_services() logs
        # a second, near-identical one right after it.
        assert _lines(capsys.readouterr().out) == [
            "Deploying ECS services...",
            "  web task definition registered [done]",
            "  web service created [done]",
            "  web [service created]",
        ]

    def test_a_mistyped_cluster_aborts_instead_of_creating_services(self, aws, capsys):
        """The cluster typo must stop the run, not route it to CREATE.

        It also must not land in the per-service failure collector: the failure
        is cluster-level, so every service would report it identically. It
        propagates out of deploy_services() and aborts the deploy.
        """
        ctx = _ctx(aws, services={"web": {}, "worker": {}}, cluster_name="no-such-cluster")

        with pytest.raises(RuntimeError, match="no-such-cluster"):
            deploy_services(ctx, {"web": IMAGE_URI, "worker": IMAGE_URI})

        assert "create_service" not in aws.client.operations
        assert "Failed to deploy service(s)" not in capsys.readouterr().out

    def test_existing_service_takes_the_update_branch(self, aws, capsys):
        _make_service(aws)
        ctx = _ctx(aws)
        deploy_services(ctx, {"web": IMAGE_URI})
        assert aws.client.operations == [
            "describe_services",
            "register_task_definition",
            "update_service",
        ]
        assert _lines(capsys.readouterr().out) == [
            "Deploying ECS services...",
            "  web task definition registered [done]",
            "  web [deployment started]",
        ]

    def test_update_parameters(self, aws):
        _make_service(aws)
        ctx = _ctx(aws)
        deploy_services(ctx, {"web": IMAGE_URI})
        task_def_arn = aws.client.all_params("update_service")[0]["taskDefinition"]
        assert aws.client.params("update_service") == {
            "cluster": CLUSTER,
            "service": "web",
            "taskDefinition": task_def_arn,
            "forceNewDeployment": True,
            "deploymentConfiguration": {
                "minimumHealthyPercent": 100,
                "maximumPercent": 200,
            },
        }
        # The update carries no network config, no load balancer and no launch
        # type: those are create-time only, so changing a target group or
        # switching a live service to Spot has no effect through this path.
        assert "networkConfiguration" not in aws.client.params("update_service")

    def test_update_uses_the_freshly_registered_revision(self, aws):
        _make_service(aws)
        ctx = _ctx(aws)
        deploy_services(ctx, {"web": IMAGE_URI})
        registered = aws.client.params("register_task_definition")
        assert aws.client.params("update_service")["taskDefinition"].endswith(
            f"task-definition/{registered['family']}:2"
        )

    # -- must-pin #2: circuit breaker on the update arm -------------------

    def test_update_circuit_breaker_off_omits_the_key(self, aws):
        _make_service(aws)
        deploy_services(_ctx(aws), {"web": IMAGE_URI})
        cfg = aws.client.params("update_service")["deploymentConfiguration"]
        assert "deploymentCircuitBreaker" not in cfg

    def test_update_circuit_breaker_on_injects_it(self, aws):
        _make_service(aws)
        ctx = _ctx(
            aws,
            infra={
                "deployment_config": {
                    "circuit_breaker_enabled": True,
                    "circuit_breaker_rollback": False,
                }
            },
        )
        deploy_services(ctx, {"web": IMAGE_URI})
        assert aws.client.params("update_service")["deploymentConfiguration"] == {
            "minimumHealthyPercent": 100,
            "maximumPercent": 200,
            "deploymentCircuitBreaker": {"enable": True, "rollback": False},
        }

    # -- must-pin #4: service discovery on the update arm -----------------

    def test_update_injects_service_registries(self, aws):
        _make_service(aws)
        ctx = _ctx(aws, infra={"service_discovery_registries": {"web": REGISTRY_ARN}})
        deploy_services(ctx, {"web": IMAGE_URI})
        assert aws.client.params("update_service")["serviceRegistries"] == [
            {"registryArn": REGISTRY_ARN}
        ]

    def test_update_omits_service_registries_when_unconfigured(self, aws):
        _make_service(aws)
        deploy_services(_ctx(aws), {"web": IMAGE_URI})
        assert "serviceRegistries" not in aws.client.params("update_service")

    def test_update_omits_service_registries_for_an_empty_arn(self, aws):
        _make_service(aws)
        ctx = _ctx(aws, infra={"service_discovery_registries": {"web": ""}})
        deploy_services(ctx, {"web": IMAGE_URI})
        assert "serviceRegistries" not in aws.client.params("update_service")

    # -- must-pin #6: AZ rebalance on the update arm ----------------------

    def test_update_at_max_percent_100_runs_the_az_check_first(self, aws):
        _make_service(aws)
        ctx = _ctx(aws, infra={"deployment_config": {"maximum_percent": 100}})
        deploy_services(ctx, {"web": IMAGE_URI})
        assert aws.client.operations == [
            "describe_services",  # live-service lookup (exists + skip check)
            "register_task_definition",
            "describe_services",  # _ensure_az_rebalancing_disabled
            "update_service",
        ]

    def test_update_at_default_max_percent_skips_the_az_check(self, aws):
        _make_service(aws)
        deploy_services(_ctx(aws), {"web": IMAGE_URI})
        assert aws.client.operations.count("describe_services") == 1

    # -- per-service deployment overrides on the update arm ----------------

    def test_update_applies_per_service_deployment_override(self, aws):
        _make_service(aws, "beat")
        _make_service(aws, "web")
        ctx = _ctx(
            aws,
            services={
                "beat": {"minimum_healthy_percent": 0, "maximum_percent": 100},
                "web": {},
            },
            infra={
                "deployment_config": {"minimum_healthy_percent": 100, "maximum_percent": 200},
            },
        )
        deploy_services(ctx, {"beat": IMAGE_URI, "web": IMAGE_URI})
        configs = {
            p["service"]: p["deploymentConfiguration"]
            for p in aws.client.all_params("update_service")
        }
        assert configs["beat"] == {"minimumHealthyPercent": 0, "maximumPercent": 100}
        assert configs["web"] == {"minimumHealthyPercent": 100, "maximumPercent": 200}

    def test_update_per_service_max_percent_100_runs_the_az_check(self, aws):
        _make_service(aws)
        ctx = _ctx(
            aws,
            services={"web": {"maximum_percent": 100}},
            infra={"deployment_config": {"maximum_percent": 200}},
        )
        deploy_services(ctx, {"web": IMAGE_URI})
        assert aws.client.operations == [
            "describe_services",  # live-service lookup (exists + skip check)
            "register_task_definition",
            "describe_services",  # _ensure_az_rebalancing_disabled
            "update_service",
        ]

    # -- must-pin #7: the per-service ClientError -------------------------

    def test_update_client_error_is_logged_and_fails_the_deploy(self, aws, capsys):
        """The ClientError is still caught per-service, but the run reports it.

        It used to be logged and nothing more, so a deploy in which a service
        was never updated ended as a success.
        """
        _make_service(aws)
        aws.client.fail_next("update_service", _client_error("InvalidParameterException"))
        ctx = _ctx(aws)
        with pytest.raises(RuntimeError, match=r"Failed to deploy service\(s\): web"):
            deploy_services(ctx, {"web": IMAGE_URI})
        out = _lines(capsys.readouterr().out)
        assert any(line.startswith("  ✗ Failed to update service web:") for line in out)

    def test_update_client_error_does_not_stop_the_loop(self, aws):
        # Catching per-service is still right: stopping halfway leaves a worse
        # state than finishing. What changed is that the failures are collected
        # and raised once every service has been attempted.
        _make_service(aws, "web")
        aws.client.fail_next("update_service", _client_error("InvalidParameterException"))
        ctx = _ctx(aws, services={"web": {}, "worker": {}})
        with pytest.raises(RuntimeError, match=r"Failed to deploy service\(s\): web"):
            deploy_services(ctx, {"web": IMAGE_URI, "worker": IMAGE_URI})
        assert service_exists(aws.client, CLUSTER, "worker") is True
        assert len(aws.client.all_params("update_service")) == 1

    def test_every_failed_service_is_named_once(self, aws):
        """The message is a work list, so it must name all of them."""
        _make_service(aws, "web")
        _make_service(aws, "worker")
        aws.client.fail_next("update_service", _client_error("InvalidParameterException"))
        aws.client.fail_next("update_service", _client_error("InvalidParameterException"))
        ctx = _ctx(aws, services={"web": {}, "worker": {}})
        with pytest.raises(RuntimeError, match=r"Failed to deploy service\(s\): web, worker"):
            deploy_services(ctx, {"web": IMAGE_URI, "worker": IMAGE_URI})

    def test_non_client_error_propagates(self, aws):
        # Only ClientError is swallowed. A BotoCoreError (endpoint failure,
        # credential failure) aborts the whole loop.
        _make_service(aws)
        aws.client.fail_next("update_service", RuntimeError("socket"))
        with pytest.raises(RuntimeError):
            deploy_services(_ctx(aws), {"web": IMAGE_URI})

    def test_create_branch_errors_are_not_swallowed(self, aws):
        # The try/except only wraps the update arm; a failure in create_service
        # propagates.
        ctx = _ctx(aws, infra={"subnet_ids": []})
        with pytest.raises(RuntimeError, match="Missing network configuration"):
            deploy_services(ctx, {"web": IMAGE_URI})

    # -- must-pin #1: the dry-run arm -------------------------------------

    def test_dry_run_prints_the_update_block(self, aws, capsys):
        ctx = _ctx(aws, dry_run=True)
        deploy_services(ctx, {"web": IMAGE_URI})
        arn = f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task-definition/{CLUSTER}-web:dry-run"
        assert _lines(capsys.readouterr().out) == [
            "Deploying ECS services...",
            f"  [dry-run] aws ecs register-task-definition --family {CLUSTER}-web",
            f"  [dry-run] aws ecs update-service --service web --task-definition {arn}",
            "    cpu=256, memory=512, replicas=1",
            "    deployment: minHealthy=100%, maxPercent=200%, circuitBreaker=False",
        ]

    def test_dry_run_calls_no_aws_at_all(self, aws, run):
        ctx = _ctx(aws, dry_run=True)
        deploy_services(ctx, {"web": IMAGE_URI})
        assert aws.client.operations == []
        assert run.calls == []

    def test_dry_run_never_asks_whether_the_service_exists(self, aws, capsys):
        # `exists` is hard-coded True under --dry-run, so a first-time deploy is
        # previewed as an *update* of a service that does not exist. The
        # create_service() path is unreachable under --dry-run.
        ctx = _ctx(aws, dry_run=True)
        deploy_services(ctx, {"web": IMAGE_URI})
        out = _plain(capsys.readouterr().out)
        assert "create-service" not in out
        assert "update-service" in out

    def test_dry_run_reports_the_configured_deployment_settings(self, aws, capsys):
        ctx = _ctx(
            aws,
            services={"web": {"replicas": 3}},
            service_config={"web": {"cpu": 512, "memory": 1024}},
            infra={
                "deployment_config": {
                    "minimum_healthy_percent": 50,
                    "maximum_percent": 100,
                    "circuit_breaker_enabled": True,
                }
            },
            dry_run=True,
        )
        deploy_services(ctx, {"web": IMAGE_URI})
        out = _lines(capsys.readouterr().out)
        assert "    cpu=512, memory=1024, replicas=3" in out
        assert "    deployment: minHealthy=50%, maxPercent=100%, circuitBreaker=True" in out

    def test_dry_run_reports_per_service_deployment_overrides(self, aws, capsys):
        ctx = _ctx(
            aws,
            services={"beat": {"minimum_healthy_percent": 0, "maximum_percent": 100}},
            infra={
                "deployment_config": {"minimum_healthy_percent": 100, "maximum_percent": 200},
            },
            dry_run=True,
        )
        deploy_services(ctx, {"beat": IMAGE_URI})
        out = _lines(capsys.readouterr().out)
        assert "    deployment: minHealthy=0%, maxPercent=100%, circuitBreaker=False" in out

    def test_dry_run_at_max_percent_100_skips_the_az_check(self, aws, run):
        ctx = _ctx(aws, infra={"deployment_config": {"maximum_percent": 100}}, dry_run=True)
        deploy_services(ctx, {"web": IMAGE_URI})
        assert run.calls == []
        assert aws.client.operations == []

    # -- ordering and multi-service ---------------------------------------

    def test_services_are_processed_in_config_order(self, aws):
        _make_service(aws, "web")
        ctx = _ctx(aws, services={"worker": {}, "web": {}})
        deploy_services(ctx, {"web": IMAGE_URI, "worker": IMAGE_URI})
        families = [p["family"] for p in aws.client.all_params("register_task_definition")]
        assert families == [f"{CLUSTER}-worker", f"{CLUSTER}-web"]

    def test_mixed_create_and_update_in_one_pass(self, aws, capsys):
        _make_service(aws, "web")
        ctx = _ctx(aws, services={"web": {}, "worker": {}})
        deploy_services(ctx, {"web": IMAGE_URI, "worker": IMAGE_URI})
        assert len(aws.client.all_params("update_service")) == 1
        assert aws.client.params("create_service")["serviceName"] == "worker"
        out = _lines(capsys.readouterr().out)
        assert "  web [deployment started]" in out
        assert "  worker [service created]" in out

    def test_deployment_config_is_recomputed_per_service(self, aws):
        # _get_deployment_config() is called inside the loop, once per service,
        # from the same infra_config every time.
        _make_service(aws, "web")
        _make_service(aws, "worker")
        ctx = _ctx(
            aws,
            services={"web": {}, "worker": {}},
            infra={"deployment_config": {"maximum_percent": 150}},
        )
        deploy_services(ctx, {"web": IMAGE_URI, "worker": IMAGE_URI})
        percents = [
            p["deploymentConfiguration"]["maximumPercent"]
            for p in aws.client.all_params("update_service")
        ]
        assert percents == [150, 150]

    def test_sizing_errors_abort_the_whole_deploy(self, aws):
        # get_service_sizing() is called at the top of the loop body, outside
        # any try/except, so one bad service stops the rest.
        ctx = _ctx(
            aws,
            services={"web": {"min_memory": 4096}, "worker": {}},
            service_config={"web": {"memory": 512}},
        )
        with pytest.raises(ValueError, match="below minimum"):
            deploy_services(ctx, {"web": IMAGE_URI, "worker": IMAGE_URI})
        assert aws.client.operations == []


class _HealthyLiveEcs:
    """Answers describe_services with a crafted healthy service; everything
    else (register_task_definition, update_service) delegates to the real
    moto-backed recording client. moto always reports runningCount=0, so a
    healthy live state has to be injected to reach the skip decision."""

    def __init__(self, delegate, live_service: dict):
        self._delegate = delegate
        self.live_service = live_service

    def describe_services(self, **kwargs):
        return {"services": [self.live_service]}

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def _healthy_live_service(task_def_arn: str) -> dict:
    return {
        "serviceName": "web",
        "status": "ACTIVE",
        "taskDefinition": task_def_arn,
        "deployments": [{"status": "PRIMARY", "runningCount": 1, "desiredCount": 1}],
    }


def _state_hash_for(ctx, service_name: str, image_uri: str) -> str:
    """The hash _deploy_one_service would compute for this service."""
    dep_cfg = _get_deployment_config(
        ctx.infra_config, ctx.config.get("services", {}).get(service_name, {})
    )
    return _compute_service_state_hash(ctx, service_name, image_uri, dep_cfg)


class TestSkipUnchangedServices:
    """The skip-unchanged decision: hash gate, live-state gates, escape hatches."""

    def test_unchanged_healthy_service_is_skipped(self, aws, capsys):
        arn = "arn:aws:ecs:us-west-2:123456789012:task-definition/testapp-staging-web:5"
        ctx = _ctx(aws, ecs_client=_HealthyLiveEcs(aws.client, _healthy_live_service(arn)))
        store_service_state(APP, ENVIRONMENT, "web", _state_hash_for(ctx, "web", IMAGE_URI), arn)

        deployed = deploy_services(ctx, {"web": IMAGE_URI})

        assert deployed.skipped == ["web"]
        assert deployed.updated == {}
        # Nothing registered, nothing updated: the delegate saw no calls.
        assert aws.client.operations == []
        assert "  web [unchanged, skipping]" in _lines(capsys.readouterr().out)

    def test_skipped_service_still_carries_its_state_hash_privately(self, aws):
        """A skipped service is absent from state_hashes — its stored hash is
        already correct, and rewriting it after wait_for_stable would be a
        pointless SSM write."""
        arn = "arn:aws:ecs:us-west-2:123456789012:task-definition/testapp-staging-web:5"
        ctx = _ctx(aws, ecs_client=_HealthyLiveEcs(aws.client, _healthy_live_service(arn)))
        store_service_state(APP, ENVIRONMENT, "web", _state_hash_for(ctx, "web", IMAGE_URI), arn)

        deployed = deploy_services(ctx, {"web": IMAGE_URI})

        assert "web" not in deployed.state_hashes

    def test_force_deploy_rolls_an_unchanged_service(self, aws):
        arn = _make_service(aws)
        ctx = _ctx(aws, ecs_client=_HealthyLiveEcs(aws.client, _healthy_live_service(arn)))
        store_service_state(APP, ENVIRONMENT, "web", _state_hash_for(ctx, "web", IMAGE_URI), arn)

        deployed = deploy_services(ctx, {"web": IMAGE_URI}, force_deploy=True)

        assert deployed.skipped == []
        assert "web" in deployed.updated
        assert "update_service" in aws.client.operations

    def test_no_stored_state_deploys(self, aws):
        arn = _make_service(aws)
        ctx = _ctx(aws, ecs_client=_HealthyLiveEcs(aws.client, _healthy_live_service(arn)))

        deployed = deploy_services(ctx, {"web": IMAGE_URI})

        assert deployed.skipped == []
        assert "update_service" in aws.client.operations

    def test_changed_hash_deploys(self, aws):
        arn = _make_service(aws)
        ctx = _ctx(aws, ecs_client=_HealthyLiveEcs(aws.client, _healthy_live_service(arn)))
        store_service_state(APP, ENVIRONMENT, "web", "0" * 64, arn)

        deployed = deploy_services(ctx, {"web": IMAGE_URI})

        assert deployed.skipped == []
        assert "update_service" in aws.client.operations

    def test_live_arn_drift_deploys(self, aws):
        """The live-state gate: a console change, emergency pin, or late
        rollback moved the service off the stored ARN — redeploy, don't
        silently skip."""
        arn = _make_service(aws)
        drifted = _healthy_live_service(arn.replace(":1", ":99"))
        ctx = _ctx(aws, ecs_client=_HealthyLiveEcs(aws.client, drifted))
        store_service_state(APP, ENVIRONMENT, "web", _state_hash_for(ctx, "web", IMAGE_URI), arn)

        deployed = deploy_services(ctx, {"web": IMAGE_URI})

        assert deployed.skipped == []
        assert "update_service" in aws.client.operations

    def test_moto_zero_running_fails_the_health_gate(self, aws):
        """Straight moto (runningCount always 0): matching hash and ARN are
        not enough — an unhealthy service redeploys."""
        arn = _make_service(aws)
        ctx = _ctx(aws)
        store_service_state(APP, ENVIRONMENT, "web", _state_hash_for(ctx, "web", IMAGE_URI), arn)

        deployed = deploy_services(ctx, {"web": IMAGE_URI})

        assert deployed.skipped == []
        assert "update_service" in aws.client.operations

    @pytest.mark.parametrize(
        "deployments",
        [
            pytest.param(
                [
                    {"status": "PRIMARY", "runningCount": 1, "desiredCount": 1},
                    {"status": "ACTIVE", "runningCount": 1, "desiredCount": 1},
                ],
                id="rollout-in-flight",
            ),
            pytest.param(
                [{"status": "ACTIVE", "runningCount": 1, "desiredCount": 1}], id="no-primary"
            ),
            pytest.param(
                [{"status": "PRIMARY", "runningCount": 1, "desiredCount": 2}], id="under-replicated"
            ),
            pytest.param(
                [{"status": "PRIMARY", "runningCount": 0, "desiredCount": 0}], id="scaled-to-zero"
            ),
            pytest.param([], id="no-deployments"),
        ],
    )
    def test_unsettled_live_state_is_not_unchanged(self, aws, deployments):
        ctx = _ctx(aws)
        arn = "arn:td:5"
        current_hash = "f" * 64
        store_service_state(APP, ENVIRONMENT, "web", current_hash, arn)
        live = _healthy_live_service(arn)
        live["deployments"] = deployments

        assert _service_is_unchanged(ctx, "web", current_hash, live) is False

    def test_settled_live_state_is_unchanged(self, aws):
        ctx = _ctx(aws)
        store_service_state(APP, ENVIRONMENT, "web", "f" * 64, "arn:td:5")

        assert _service_is_unchanged(ctx, "web", "f" * 64, _healthy_live_service("arn:td:5"))

    def test_dry_run_bypasses_the_skip_check(self, aws, capsys):
        """Dry runs narrate the update they would send and never read SSM —
        the skip decision needs live state a dry run must not depend on."""
        ctx = _ctx(aws, dry_run=True)
        store_service_state(APP, ENVIRONMENT, "web", _state_hash_for(ctx, "web", IMAGE_URI), "arn")

        deployed = deploy_services(ctx, {"web": IMAGE_URI})

        assert deployed.skipped == []
        assert "web" in deployed.updated
        assert aws.client.operations == []  # not even the live-state describe
        assert "update-service" in _plain(capsys.readouterr().out)


class TestServiceStateHash:
    """Determinism and sensitivity of the intended-state hash."""

    def test_identical_inputs_hash_identically(self, aws):
        ctx = _ctx(aws)
        assert _state_hash_for(ctx, "web", IMAGE_URI) == _state_hash_for(ctx, "web", IMAGE_URI)

    def test_environment_dict_order_does_not_change_the_hash(self, aws):
        """environment/secrets lists are sorted before hashing — dict-order
        lists would otherwise cause false redeploys."""
        base = _ctx(aws)
        cfg1 = {"services": {"web": {}}, "environment": {"A": "1", "B": "2"}}
        cfg2 = {"services": {"web": {}}, "environment": {"B": "2", "A": "1"}}
        h1 = _compute_service_state_hash(
            replace(base, config=cfg1), "web", IMAGE_URI, DeploymentConfig()
        )
        h2 = _compute_service_state_hash(
            replace(base, config=cfg2), "web", IMAGE_URI, DeploymentConfig()
        )
        assert h1 == h2

    def test_image_uri_changes_the_hash(self, aws):
        ctx = _ctx(aws)
        assert _state_hash_for(ctx, "web", IMAGE_URI) != _state_hash_for(
            ctx, "web", IMAGE_URI.replace("abc123", "def456")
        )

    def test_deployment_config_changes_the_hash(self, aws):
        ctx = _ctx(aws)
        h_default = _compute_service_state_hash(ctx, "web", IMAGE_URI, DeploymentConfig())
        h_override = _compute_service_state_hash(
            ctx, "web", IMAGE_URI, DeploymentConfig(min_healthy=0, max_percent=100)
        )
        assert h_default != h_override

    def test_environment_value_changes_the_hash(self, aws):
        base = _ctx(aws)
        cfg1 = {"services": {"web": {}}, "environment": {"A": "1"}}
        cfg2 = {"services": {"web": {}}, "environment": {"A": "2"}}
        assert _compute_service_state_hash(
            replace(base, config=cfg1), "web", IMAGE_URI, DeploymentConfig()
        ) != _compute_service_state_hash(
            replace(base, config=cfg2), "web", IMAGE_URI, DeploymentConfig()
        )


class TestServiceStateStorage:
    """SSM storage of (hash, ARN) per service, post-stability only."""

    def test_round_trip(self, aws):
        assert store_service_state(APP, ENVIRONMENT, "web", "h1", "arn:1") is True
        assert get_stored_service_state(APP, ENVIRONMENT, "web") == ("h1", "arn:1")

    def test_absent_parameter_reads_none(self, aws):
        assert get_stored_service_state(APP, ENVIRONMENT, "web") is None

    def test_unparseable_stored_value_reads_none(self, aws):
        from deployer.aws import ssm as ssm_module

        ssm_module.put_parameter(
            name=f"/{APP}/{ENVIRONMENT}/service-state-hash-web", value="not json"
        )
        assert get_stored_service_state(APP, ENVIRONMENT, "web") is None

    def test_store_service_state_hashes_stores_each_updated_service(self, aws):
        deployed = DeployedServices(
            updated={"web": "arn:1", "worker": "arn:2"},
            state_hashes={"web": "h1", "worker": "h2"},
        )
        store_service_state_hashes(APP, ENVIRONMENT, deployed, [])

        assert get_stored_service_state(APP, ENVIRONMENT, "web") == ("h1", "arn:1")
        assert get_stored_service_state(APP, ENVIRONMENT, "worker") == ("h2", "arn:2")

    def test_health_check_failures_are_excluded(self, aws):
        """Storing a failed service's hash would make 'redeploy after exit 2'
        silently no-op."""
        deployed = DeployedServices(
            updated={"web": "arn:1", "worker": "arn:2"},
            state_hashes={"web": "h1", "worker": "h2"},
        )
        store_service_state_hashes(APP, ENVIRONMENT, deployed, ["worker"])

        assert get_stored_service_state(APP, ENVIRONMENT, "web") == ("h1", "arn:1")
        assert get_stored_service_state(APP, ENVIRONMENT, "worker") is None

    def test_denied_write_warns_and_returns_false(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "deployer.aws.ssm.put_parameter", lambda **_kwargs: (False, "AccessDenied")
        )
        assert store_service_state(APP, ENVIRONMENT, "web", "h", "arn") is False
        assert "Failed to store service state hash for web" in _plain(capsys.readouterr().out)


class TestHealthCheckConfigSource:
    """Where ``infra_config.health_check_config`` comes from.

    53f-1 pinned this as *read here, produced nowhere*: ``_load_balancer_params``
    read ``health_check_config`` off ``infra_config``, but ``_build_infra_config``
    never emitted the key, so the production grace period was always 60 and the
    sole place the key existed was a test injecting it by hand.

    53f-4 settled it by **wiring the source rather than deleting the read**. The
    key is not invented: ``modules/app-in-shared-env`` declares
    ``var.health_check`` with a ``grace_period`` member defaulting to 60, exports
    it as the ``health_check_config`` output described "for deploy.py", every
    ``config.toml.example`` sets ``[services] health_check =
    "${tofu:health_check_config}"``, and CONFIG-REFERENCE documents the mapping.
    Only the last hop was missing. Deleting the read would have meant deleting a
    tofu variable, an output, four templates and two doc tables.

    The wiring is behaviour-preserving at the default: the standalone
    ``var.health_check`` has no ``grace_period`` member at all, and the shared-env
    one defaults it to 60, so the only deploys that change are those that set it
    explicitly -- and today those are silently ignored.

    What is still pinned: ``[infrastructure].health_check_config`` and a
    top-level ``health_check_config`` are *not* the source.
    """

    def test_neither_infrastructure_nor_top_level_is_the_source(self):
        # Every section _build_infra_config knows about, populated.
        infra = _build_infra_config(
            {
                "infrastructure": {
                    "execution_role_arn": "arn:role:exec",
                    "task_role_arn": "arn:role:task",
                    "security_group_id": "sg-1",
                    "private_subnet_ids": ["subnet-1"],
                    "target_group_arn": DEFAULT_TG,
                    "service_target_groups": {"web": DEFAULT_TG},
                    "service_discovery_registries": {"web": REGISTRY_ARN},
                    "rds_instance_id": "myapp-db",
                    # A health_check_config under [infrastructure] is not read.
                    "health_check_config": {"grace_period": 300},
                },
                "database": {"url": "postgres://db/app"},
                "cache": {"url": "redis://cache:6379/0"},
                "storage": {"media_bucket": "media"},
                "deployment": {"maximum_percent": 150},
                "scheduler": {"enabled": True},
                # Nor is a top-level one.
                "health_check_config": {"grace_period": 300},
            }
        )
        assert infra.health_check_config == {}

    def test_services_health_check_is_the_source(self):
        infra = _build_infra_config({"services": {"health_check": {"grace_period": 300}}})
        assert infra.health_check_config == {"grace_period": 300}

    def test_the_production_path_still_defaults_to_sixty_seconds(self, aws):
        arn = _make_service(aws, "seed")
        infra = _build_infra_config(
            {
                "infrastructure": {
                    "target_group_arn": DEFAULT_TG,
                    "private_subnet_ids": [aws.subnet_id],
                    "security_group_id": aws.security_group_id,
                }
            }
        )
        ctx = _ctx(
            aws, services={"web": {"load_balanced": True, "port": 8000}}, infra=asdict(infra)
        )
        create_service(ctx, "web", arn)
        assert aws.client.params("create_service")["healthCheckGracePeriodSeconds"] == 60
