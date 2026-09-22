"""Characterization pins for bin/environment.py (status / stop / start).

These are characterization pins, not endorsements. They record what
``bin/environment.py`` does **today**; where the behaviour looks surprising it
is pinned anyway and called out in the test's docstring. Nothing here is a fix.

``bin/environment.py`` was at **22%** before this file: ``test_bin_scripts.py``
imports it but exercises only ``deployer.utils`` helpers through it, and
``test_bin_error_boundaries.py`` covers the unset-environments-dir exit.

What is pinned:

* ``cmd_status`` -- the service table, the three "can't tell" lines (no cluster
  name, no RDS id, no services), the in-place RDS error and no-such-instance
  lines, and a config load failure that skips one environment but not the rest.
* ``cmd_stop`` -- ECS scale-to-0 before RDS, every RDS state arm, and that an
  unreadable RDS aborts only *after* ECS has already been scaled down.
* ``cmd_start`` / ``_ensure_rds_available`` -- every RDS arm (available,
  stopped, stopping, some other state, missing, unreadable, both timeouts, a
  refused start), the configured-replicas-else-1 rule, and that every RDS
  failure arm except "unreadable" still goes on to scale ECS up.
* The Click wiring: each command's exit status, and the ``infra`` profile the
  group selects.

Stubbing is at the outermost boundary so the pins survive code motion:

* ECS is moto (``mocked_aws``); the real ``get_services`` and
  ``scale_service`` run. The one exception is a refused ``UpdateService``,
  which moto cannot express, so a thin proxy over the moto client raises it.
* RDS is the shared ``aws_cli`` fixture at the ``deployer.aws.cli`` seam, and
  RDS waits run against a fake clock so a timeout costs nothing.
* The filesystem is real: ``DEPLOYER_ENVIRONMENTS_DIR`` points at ``tmp_path``.
  ``load_environment_config`` is the one stub -- it shells out to ``tofu`` --
  replaced both where ``cmd_status`` imported it and where
  ``load_environment_infrastructure`` imports it at call time.
"""

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from click.testing import CliRunner

from deployer.aws import ecs as ecs_module
from deployer.aws import rds as rds_module
from deployer.core import config as core_config

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("environment_cli", bin_dir / "environment.py")
environment = module_from_spec(_spec)
_spec.loader.exec_module(environment)

ENV = "myapp-staging"
OTHER_ENV = "otherapp-staging"
CLUSTER = "myapp-staging-cluster"
RDS_ID = "myapp-staging-db"
REGION = "us-west-2"


def describe(status: str) -> str:
    """A describe-db-instances response for RDS_ID in the given state."""
    return json.dumps(
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": RDS_ID,
                    "DBInstanceStatus": status,
                    "DBInstanceClass": "db.t4g.micro",
                    "Engine": "postgres",
                    "EngineVersion": "16.3",
                }
            ]
        }
    )


NOT_FOUND = (False, "An error occurred (DBInstanceNotFound) when calling DescribeDBInstances")
THROTTLED = (False, "An error occurred (Throttling) when calling DescribeDBInstances")
OK = (True, "{}")
REFUSED = (False, "An error occurred (InvalidDBInstanceState)")


def _config(
    cluster: str | None = CLUSTER, rds_id: str | None = RDS_ID, replicas: dict | None = None
) -> dict:
    """A resolved config shaped the way load_environment_config() returns one."""
    infrastructure: dict = {}
    if cluster is not None:
        infrastructure["cluster_name"] = cluster
    if rds_id is not None:
        infrastructure["rds_instance_id"] = rds_id
    config: dict = {"infrastructure": infrastructure}
    if replicas is not None:
        config["services"] = {"config": {name: {"replicas": n} for name, n in replicas.items()}}
    return config


@pytest.fixture
def environments(tmp_path, monkeypatch):
    """A real environments directory whose config loading is stubbed.

    Returns a callable that registers an environment: the name, the config
    load_environment_config() should answer with (or an exception to raise),
    and whether the environment is deployed (has terraform.tfstate).
    """
    monkeypatch.setenv("DEPLOYER_ENVIRONMENTS_DIR", str(tmp_path))
    configs: dict[str, object] = {}

    def _load(env_path: Path) -> dict:
        answer = configs[env_path.name]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(environment, "load_environment_config", _load)
    monkeypatch.setattr(core_config, "load_environment_config", _load)

    def _register(name: str, config: object, deployed: bool = True) -> Path:
        env_dir = tmp_path / name
        env_dir.mkdir()
        (env_dir / "config.toml").write_text("", encoding="utf-8")
        if deployed:
            (env_dir / "terraform.tfstate").write_text("{}", encoding="utf-8")
        configs[name] = config
        return env_dir

    return _register


@pytest.fixture
def cluster(mocked_aws):
    """A moto ECS cluster; returns a callable that adds a service at a count."""
    client = boto3.client("ecs", region_name=REGION)
    client.create_cluster(clusterName=CLUSTER)
    task_def = client.register_task_definition(
        family="myapp",
        containerDefinitions=[
            {"name": "app", "image": "public.ecr.aws/nginx/nginx:latest", "memory": 512}
        ],
    )["taskDefinition"]["taskDefinitionArn"]

    def _add(name: str, desired: int) -> None:
        client.create_service(
            cluster=CLUSTER, serviceName=name, taskDefinition=task_def, desiredCount=desired
        )

    return _add


def desired_counts() -> dict[str, int]:
    """Each service's desiredCount as moto now holds it."""
    client = boto3.client("ecs", region_name=REGION)
    arns = client.list_services(cluster=CLUSTER)["serviceArns"]
    services = client.describe_services(cluster=CLUSTER, services=arns)["services"]
    return {s["serviceName"]: s["desiredCount"] for s in services}


@pytest.fixture
def refuse_update_for(monkeypatch):
    """Make UpdateService fail for the named services, as AWS would refuse it."""

    def _refuse(*names: str) -> None:
        real = ecs_module._get_ecs_client

        class _Refusing:
            def __init__(self):
                self._client = real()

            def __getattr__(self, attr):
                return getattr(self._client, attr)

            def update_service(self, **kwargs):
                if kwargs["service"] in names:
                    raise ClientError(
                        {"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                        "UpdateService",
                    )
                return self._client.update_service(**kwargs)

        monkeypatch.setattr(ecs_module, "_get_ecs_client", _Refusing)

    return _refuse


class FakeClock:
    """Stand-in for the ``time`` module inside rds.wait_for_status."""

    def __init__(self):
        self.now = 1_000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch) -> FakeClock:
    """No RDS wait in this file sleeps for real; a timeout arrives instantly."""
    clock = FakeClock()
    monkeypatch.setattr(rds_module, "time", clock)
    return clock


def rds_ops(aws_cli) -> list[str]:
    """The rds subcommands issued, in order (e.g. 'describe-db-instances')."""
    return [argv[2] for argv in aws_cli.calls]


# =============================================================================
# status
# =============================================================================


class TestStatus:
    def test_renders_the_service_table_and_rds_details(
        self, environments, cluster, aws_cli, capsys
    ):
        environments(ENV, _config())
        cluster("web", 2)
        cluster("worker", 1)
        aws_cli.replies((True, describe("available")))

        assert environment.cmd_status(ENV) == 0

        out = capsys.readouterr().out
        assert f"Environment: {ENV}" in out
        assert f"ECS Cluster: {CLUSTER}" in out
        assert f"  {'Service':<30} {'Desired':<10} {'Running':<10} {'Status':<15}" in out
        assert f"  {'web':<30} {2:<10}" in out
        assert f"  {'worker':<30} {1:<10}" in out
        assert f"RDS Instance: {RDS_ID}" in out
        assert "    Status: available" in out
        assert "    Class: db.t4g.micro" in out
        assert "    Engine: postgres 16.3" in out

    def test_a_cluster_with_no_services(self, environments, cluster, aws_cli, capsys):
        environments(ENV, _config())
        aws_cli.replies((True, describe("stopped")))

        assert environment.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "  No ECS services found" in out
        assert "Service" not in out

    def test_a_cluster_that_does_not_exist_reads_as_no_services(
        self, environments, mocked_aws, aws_cli, capsys
    ):
        """get_services() maps ClusterNotFoundException to [], so a wrong
        cluster name is indistinguishable from an empty cluster here."""
        environments(ENV, _config())
        aws_cli.replies((True, describe("available")))

        assert environment.cmd_status(ENV) == 0
        assert "  No ECS services found" in capsys.readouterr().out

    def test_no_cluster_name_and_no_rds_id(self, environments, aws_cli, capsys):
        environments(ENV, _config(cluster=None, rds_id=None))

        assert environment.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "  ECS: Unable to determine cluster name" in out
        assert "  RDS: Not configured or unable to determine instance ID" in out
        assert aws_cli.calls == []

    def test_no_such_rds_instance(self, environments, aws_cli, capsys):
        environments(ENV, _config(cluster=None))
        aws_cli.replies(NOT_FOUND)

        assert environment.cmd_status(ENV) == 0
        assert "    Status: No such instance" in capsys.readouterr().out

    def test_an_unreadable_rds_instance_is_reported_in_place(self, environments, aws_cli, capsys):
        environments(ENV, _config(cluster=None))
        aws_cli.replies(THROTTLED)

        assert environment.cmd_status(ENV) == 0
        out = capsys.readouterr().out
        assert "    Status: Unable to retrieve (Could not describe RDS instance" in out
        assert "Throttling" in out

    def test_all_environments_skip_the_unloadable_and_undeployed(
        self, environments, aws_cli, capsys
    ):
        """No argument walks every environment in sorted order; one config
        load failure prints in place and the walk continues."""
        environments(OTHER_ENV, RuntimeError("Failed to get tofu outputs"))
        environments(ENV, _config(cluster=None, rds_id=None))
        environments("anotherapp-staging", _config(), deployed=False)

        assert environment.cmd_status(None) == 0

        out = capsys.readouterr().out
        headers = [line for line in out.splitlines() if line.startswith("Environment: ")]
        assert headers == [
            "Environment: anotherapp-staging",
            f"Environment: {ENV}",
            f"Environment: {OTHER_ENV}",
        ]
        assert "  Status: Not deployed" in out
        assert "  Error loading config: Failed to get tofu outputs" in out
        assert "  ECS: Unable to determine cluster name" in out

    def test_an_empty_environments_directory_exits_1(self, environments, capsys):
        with pytest.raises(SystemExit) as exc:
            environment.cmd_status(None)
        assert exc.value.code == 1
        assert "No environments found." in capsys.readouterr().err


# =============================================================================
# stop
# =============================================================================


class TestStopPreconditions:
    def test_a_missing_environment_exits_1(self, environments, capsys):
        with pytest.raises(SystemExit) as exc:
            environment.cmd_stop(ENV)
        assert exc.value.code == 1
        assert "Environment directory not found" in capsys.readouterr().out

    def test_an_undeployed_environment_exits_1(self, environments, capsys):
        environments(ENV, _config(), deployed=False)
        with pytest.raises(SystemExit) as exc:
            environment.cmd_stop(ENV)
        assert exc.value.code == 1
        assert f"Environment '{ENV}' is not deployed" in capsys.readouterr().out

    def test_an_environment_without_rds_cannot_be_stopped(self, environments, aws_cli, capsys):
        """Both a cluster and an RDS instance are required, so an app with no
        database cannot use stop/start at all."""
        environments(ENV, _config(rds_id=None))
        with pytest.raises(SystemExit) as exc:
            environment.cmd_stop(ENV)
        assert exc.value.code == 1
        assert "RDS instance not configured for this environment" in capsys.readouterr().out
        assert aws_cli.calls == []

    def test_an_environment_without_a_cluster_cannot_be_stopped(self, environments, capsys):
        environments(ENV, _config(cluster=None))
        with pytest.raises(SystemExit) as exc:
            environment.cmd_stop(ENV)
        assert exc.value.code == 1
        assert "Unable to determine ECS cluster name" in capsys.readouterr().out


class TestStop:
    @pytest.fixture(autouse=True)
    def _env(self, environments, cluster):
        environments(ENV, _config())
        cluster("web", 2)
        cluster("worker", 1)

    def test_scales_ecs_to_zero_then_stops_an_available_instance(self, aws_cli, capsys):
        aws_cli.replies((True, describe("available")), OK)

        assert environment.cmd_stop(ENV) == 0

        assert desired_counts() == {"web": 0, "worker": 0}
        assert rds_ops(aws_cli) == ["describe-db-instances", "stop-db-instance"]
        assert aws_cli.calls[1][3:5] == ["--db-instance-identifier", RDS_ID]
        out = capsys.readouterr().out
        assert out.index("1. Scaling ECS services to 0") < out.index("2. Stopping RDS instance")
        assert "   Scaled web to 0" in out
        assert "   RDS stop initiated (takes 5-10 minutes)" in out
        assert f"Environment {ENV} stop initiated." in out
        assert "ElastiCache and ALB continue running" in out

    def test_an_already_stopped_instance_is_left_alone(self, aws_cli, capsys):
        aws_cli.replies((True, describe("stopped")))

        assert environment.cmd_stop(ENV) == 0
        assert rds_ops(aws_cli) == ["describe-db-instances"]
        assert "   RDS instance already stopped" in capsys.readouterr().out

    def test_an_instance_in_another_state_is_reported_not_stopped(self, aws_cli, capsys):
        aws_cli.replies((True, describe("starting")))

        assert environment.cmd_stop(ENV) == 0
        assert rds_ops(aws_cli) == ["describe-db-instances"]
        assert "   RDS in unexpected state: starting" in capsys.readouterr().out

    def test_a_refused_stop_warns_and_still_exits_0(self, aws_cli, capsys):
        aws_cli.replies((True, describe("available")), REFUSED)

        assert environment.cmd_stop(ENV) == 0
        captured = capsys.readouterr()
        assert "   Warning: Failed to stop RDS instance" in captured.err
        assert f"Environment {ENV} stop initiated." in captured.out

    def test_a_missing_instance_warns_and_still_exits_0(self, aws_cli, capsys):
        aws_cli.replies(NOT_FOUND)

        assert environment.cmd_stop(ENV) == 0
        assert "   Warning: Unable to get RDS status" in capsys.readouterr().err

    def test_an_unreadable_instance_aborts_after_ecs_is_already_down(self, aws_cli, capsys):
        """The abort is not a rollback: ECS has already been scaled to 0."""
        aws_cli.replies(THROTTLED)

        with pytest.raises(SystemExit) as exc:
            environment.cmd_stop(ENV)

        assert exc.value.code == 1
        assert desired_counts() == {"web": 0, "worker": 0}
        assert "Cannot read RDS status, stop aborted: " in capsys.readouterr().err

    def test_a_refused_scale_warns_and_the_rest_still_scale(
        self, aws_cli, refuse_update_for, capsys
    ):
        refuse_update_for("web")
        aws_cli.replies((True, describe("stopped")))

        assert environment.cmd_stop(ENV) == 0
        assert desired_counts() == {"web": 2, "worker": 0}
        captured = capsys.readouterr()
        assert "   Warning: Failed to scale web" in captured.err
        assert "   Scaled worker to 0" in captured.out


# =============================================================================
# start
# =============================================================================


class TestStart:
    @pytest.fixture(autouse=True)
    def _env(self, environments, cluster):
        # web is configured for 3 replicas; worker has no services.config entry.
        environments(ENV, _config(replicas={"web": 3}))
        cluster("web", 0)
        cluster("worker", 0)

    def test_starts_a_stopped_instance_then_scales_to_configured_replicas(self, aws_cli, capsys):
        """A service missing from services.config comes back at 1."""
        aws_cli.replies((True, describe("stopped")), OK, (True, describe("available")))

        assert environment.cmd_start(ENV) == 0

        assert rds_ops(aws_cli) == [
            "describe-db-instances",
            "start-db-instance",
            "describe-db-instances",
        ]
        assert desired_counts() == {"web": 3, "worker": 1}
        out = capsys.readouterr().out
        assert out.index("   RDS is now available") < out.index("2. Scaling ECS services")
        assert "   RDS start initiated..." in out
        assert "   Scaled web to 3" in out
        assert "   Scaled worker to 1" in out
        assert f"Environment {ENV} started." in out

    def test_an_available_instance_is_not_started_again(self, aws_cli, capsys):
        aws_cli.replies((True, describe("available")))

        assert environment.cmd_start(ENV) == 0
        assert rds_ops(aws_cli) == ["describe-db-instances"]
        assert desired_counts() == {"web": 3, "worker": 1}
        assert "   RDS instance already running" in capsys.readouterr().out

    def test_a_stopping_instance_is_waited_out_then_started(self, aws_cli, capsys):
        aws_cli.replies(
            (True, describe("stopping")),
            (True, describe("stopping")),
            (True, describe("stopped")),
            OK,
            (True, describe("available")),
        )

        assert environment.cmd_start(ENV) == 0
        assert rds_ops(aws_cli)[3] == "start-db-instance"
        out = capsys.readouterr().out
        assert "   RDS is currently stopping, waiting for it to stop first..." in out
        assert "  RDS status: stopping..." in out
        assert "   RDS stopped, now starting..." in out
        assert "   RDS is now available" in out

    def test_an_instance_in_another_state_is_only_waited_for(self, aws_cli, capsys):
        aws_cli.replies(
            (True, describe("starting")),
            (True, describe("starting")),
            (True, describe("available")),
        )

        assert environment.cmd_start(ENV) == 0
        assert "start-db-instance" not in rds_ops(aws_cli)
        out = capsys.readouterr().out
        assert "   RDS in state: starting, waiting for available..." in out
        assert "  RDS status: starting..." in out

    def test_a_timeout_waiting_to_stop_still_scales_ecs(self, aws_cli, capsys):
        """Every RDS failure arm returns into cmd_start, which scales ECS up
        against a database that is not running."""
        aws_cli.replies((True, describe("stopping")))

        assert environment.cmd_start(ENV) == 0
        assert "start-db-instance" not in rds_ops(aws_cli)
        assert desired_counts() == {"web": 3, "worker": 1}
        assert "   Warning: Timeout waiting for RDS to stop" in capsys.readouterr().err

    def test_a_refused_start_still_scales_ecs(self, aws_cli, capsys):
        aws_cli.replies((True, describe("stopped")), REFUSED)

        assert environment.cmd_start(ENV) == 0
        assert rds_ops(aws_cli) == ["describe-db-instances", "start-db-instance"]
        assert desired_counts() == {"web": 3, "worker": 1}
        assert "   Warning: Failed to start RDS instance" in capsys.readouterr().err

    def test_a_timeout_waiting_for_available_still_scales_ecs(self, aws_cli, capsys):
        aws_cli.replies((True, describe("stopped")), OK, (True, describe("starting")))

        assert environment.cmd_start(ENV) == 0
        assert desired_counts() == {"web": 3, "worker": 1}
        assert "   Warning: Timeout waiting for RDS" in capsys.readouterr().err

    def test_a_missing_instance_still_scales_ecs(self, aws_cli, capsys):
        aws_cli.replies(NOT_FOUND)

        assert environment.cmd_start(ENV) == 0
        assert desired_counts() == {"web": 3, "worker": 1}
        assert "   Warning: no such RDS instance" in capsys.readouterr().err

    def test_an_unreadable_instance_aborts_before_ecs_is_touched(self, aws_cli, capsys):
        aws_cli.replies(THROTTLED)

        with pytest.raises(SystemExit) as exc:
            environment.cmd_start(ENV)

        assert exc.value.code == 1
        assert desired_counts() == {"web": 0, "worker": 0}
        assert "Cannot read RDS status, start aborted: " in capsys.readouterr().err

    def test_a_refused_scale_warns_and_the_rest_still_scale(
        self, aws_cli, refuse_update_for, capsys
    ):
        refuse_update_for("worker")
        aws_cli.replies((True, describe("available")))

        assert environment.cmd_start(ENV) == 0
        assert desired_counts() == {"web": 3, "worker": 0}
        assert "   Warning: Failed to scale worker" in capsys.readouterr().err


# =============================================================================
# Click wiring
# =============================================================================


class TestCli:
    @pytest.fixture(autouse=True)
    def _infra_profile(self, tmp_path, monkeypatch):
        """Define the default infra profile so boto3 can resolve it under moto.

        The group sets AWS_PROFILE in os.environ directly; mock_env_vars
        (under mocked_aws) restores the whole environment afterwards.
        """
        config_file = tmp_path / "aws-config"
        config_file.write_text("[profile deployer-infra]\nregion = us-west-2\n", encoding="utf-8")
        monkeypatch.setenv("AWS_CONFIG_FILE", str(config_file))

    def test_the_group_selects_the_default_infra_profile(self, environments, cluster, aws_cli):
        environments(ENV, _config())
        aws_cli.replies((True, describe("available")))

        result = CliRunner().invoke(environment.cli, ["status", ENV])

        assert result.exit_code == 0, result.output
        assert "Using AWS profile: deployer-infra (default)" in result.output
        assert f"ECS Cluster: {CLUSTER}" in result.output

    def test_stop_exits_with_cmd_stop_status(self, environments, cluster, aws_cli):
        environments(ENV, _config())
        cluster("web", 2)
        aws_cli.replies((True, describe("stopped")))

        result = CliRunner().invoke(environment.cli, ["stop", ENV])

        assert result.exit_code == 0, result.output
        assert desired_counts() == {"web": 0}

    def test_start_exits_with_cmd_start_status(self, environments, cluster, aws_cli):
        environments(ENV, _config())
        cluster("web", 0)
        aws_cli.replies((True, describe("available")))

        result = CliRunner().invoke(environment.cli, ["start", ENV])

        assert result.exit_code == 0, result.output
        assert desired_counts() == {"web": 1}

    def test_stop_and_start_require_an_environment(self, mock_env_vars):
        """mock_env_vars restores the AWS_PROFILE the group callback sets."""
        for command in ("stop", "start"):
            result = CliRunner().invoke(environment.cli, [command])
            assert result.exit_code == 2
            assert "Missing argument 'ENVIRONMENT'" in result.output
