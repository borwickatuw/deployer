"""Characterization tests for deployer.deploy.deployer.Deployer.

These are characterization pins, not endorsements. They record what the
Deployer does **today** so that the 53e-3b/53e-3c refactors can be shown to be
behaviour-preserving. Where the current behaviour looks wrong it is pinned
anyway, and called out in a comment on the test.

What is pinned here:

* ``deploy()`` — the exact order of the seven step functions, and the exact
  positional args and keyword args handed to each one, for every scenario:
  clean run, dry run, critical infrastructure (raise vs ``--force`` continue),
  a ``wait_for_migrations`` RuntimeError, and health-check failures.
* **Both timer arms.** Every ``deploy()`` scenario runs with a
  ``DeploymentTimer`` and with ``timer=None``, and
  ``TestDeployTimerArmsAgree`` asserts the two arms produce byte-identical
  step calls *and* byte-identical output. That is the pin that makes 53e-3b's
  collapse of the nine ``if self.timer: ... else: ...`` conditionals
  verifiable; without it the collapse cannot be shown to be safe.
* The ``print()`` interleaving around the steps. The step stubs print a
  ``[step_name]`` marker, so a blank line moving relative to a step is a test
  failure, not an invisible change.
* ``check_infrastructure_status()`` — all five return paths and both
  ``except`` arms.
* ``print_environment_config()`` — exactly which keys get masked and which do
  not, including the over-masking and the crash-on-non-string cases.
* ``__init__`` — the two ``ValueError`` guards, the cluster-name-present path
  as well as the fallback, the warnings and no-warnings paths, and the global
  timer registration.

The step functions are stubbed at their ``deployer.deploy.deployer`` module
bindings, never at their definition sites. That is 53d-2a's recorded rule:
stub at the outermost boundary so the pins survive code motion.

The AWS clients are fakes cached per name, so two Deployers built inside one
test share client objects and their DeploymentContexts compare equal.
"""

import re
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from deployer.deploy import deployer as deployer_mod
from deployer.deploy.context import DeployOptions
from deployer.deploy.deployer import Deployer, InfraStatus, common_deploy_options
from deployer.timing import DeploymentTimer, get_timer, set_timer

APP_NAME = "testapp"
ENVIRONMENT = "staging"
ACCOUNT_ID = "123456789012"
REGION = "us-west-2"
ECR_PREFIX = "testapp-staging"
FALLBACK_CLUSTER = f"{APP_NAME}-{ENVIRONMENT}-cluster"
RDS_ID = "testapp-staging-db"

IMAGE_URIS = {"web": f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/{ECR_PREFIX}/web:abc123"}
MIGRATION_TASK = f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:task/{ECR_PREFIX}/deadbeef"

# The order deploy() calls them in. Note that the timer step name for
# create_database_extensions is "create_extensions", not the function name.
STEP_NAMES = (
    "ecr_login",
    "build_and_push_images",
    "create_database_extensions",
    "start_migrations",
    "deploy_services",
    "wait_for_migrations",
    "wait_for_stable",
)
TIMER_STEP_NAMES = [
    "ecr_login",
    "build_and_push_images",
    "create_extensions",
    "start_migrations",
    "deploy_services",
    "wait_for_migrations",
    "wait_for_stable",
]

# A deploy.toml that produces NO config warnings. The pre-existing init test
# used cpu/memory under [services.web], neither of which is a known service
# key, so every Deployer ever constructed in a test carried two warnings.
CLEAN_TOML = """
[application]
name = "testapp"
source = "."

[services.web]
port = 8000
"""

# The pre-existing fixture's shape: cpu/memory are unknown service keys.
WARNING_TOML = """
[application]
name = "testapp"
source = "."

[services.web]
cpu = 256
memory = 512
"""

_ANSI = re.compile(r"\033\[[0-9;]*m")


def _plain(captured: str) -> str:
    """Strip ANSI colour from a captured stream."""
    return _ANSI.sub("", captured)


def _lines(captured: str) -> list[str]:
    """Return a captured stream's non-blank lines, without ANSI colour."""
    return [line for line in _plain(captured).splitlines() if line.strip()]


def _all_lines(captured: str) -> list[str]:
    """Return every line of a captured stream, blanks included, without colour."""
    return _plain(captured).splitlines()


def _env_config(*, infrastructure=None, **sections) -> dict:
    """Build a resolved environment config, overriding [infrastructure] keys."""
    config = {
        "infrastructure": {
            "ecr_prefix": ECR_PREFIX,
            "execution_role_arn": f"arn:aws:iam::{ACCOUNT_ID}:role/test-execution",
            "task_role_arn": f"arn:aws:iam::{ACCOUNT_ID}:role/test-task",
            "security_group_id": "sg-12345",
            "private_subnet_ids": ["subnet-1", "subnet-2"],
            "target_group_arn": "arn:aws:elasticloadbalancing::tg/test",
        },
        "services": {"config": {}, "scaling": {}, "health_check": {}},
        "database": {},
        "cache": {},
        "storage": {},
        "deployment": {},
        "scheduler": {},
    }
    if infrastructure is not None:
        config["infrastructure"].update(infrastructure)
    config.update(sections)
    return config


class _DBInstanceNotFoundFault(Exception):  # noqa: N818 — mirrors botocore's own name
    """Stands in for the botocore-generated RDS exception class."""


class _FakeRds:
    """Answers describe_db_instances from attributes the test sets."""

    exceptions = SimpleNamespace(DBInstanceNotFoundFault=_DBInstanceNotFoundFault)

    def __init__(self):
        self.response = {"DBInstances": [{"DBInstanceStatus": "available"}]}
        self.error: Exception | None = None
        self.calls: list[dict] = []

    def describe_db_instances(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _FakeSts:
    def get_caller_identity(self):
        return {"Account": ACCOUNT_ID, "Arn": f"arn:aws:iam::{ACCOUNT_ID}:user/test"}


class _StepRecorder:
    """Records every step call and prints a marker so interleaving is pinned."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.returns: dict[str, object] = {
            "build_and_push_images": IMAGE_URIS,
            "start_migrations": MIGRATION_TASK,
            "wait_for_stable": [],
        }
        self.raises: dict[str, Exception] = {}

    def stub(self, name: str):
        def _stub(*args, **kwargs):
            print(f"[{name}]")
            self.calls.append((name, args, kwargs))
            error = self.raises.get(name)
            if error is not None:
                raise error
            return self.returns.get(name)

        return _stub

    @property
    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture(autouse=True)
def _reset_global_timer():
    """__init__ sets a process-global timer; don't leak it into other tests."""
    yield
    set_timer(None)


@pytest.fixture
def aws(monkeypatch):
    """Replace boto3's client factory and Session with fakes cached per name."""
    clients = {"ecs": SimpleNamespace(name="ecs"), "ecr": SimpleNamespace(name="ecr")}
    clients["rds"] = _FakeRds()
    clients["sts"] = _FakeSts()
    monkeypatch.setattr(deployer_mod.boto3, "client", lambda name: clients[name])
    monkeypatch.setattr(
        deployer_mod.boto3.session, "Session", lambda: SimpleNamespace(region_name=REGION)
    )
    return clients


@pytest.fixture
def steps(monkeypatch):
    """Stub all seven step functions at their deployer-module bindings."""
    recorder = _StepRecorder()
    for name in STEP_NAMES:
        monkeypatch.setattr(deployer_mod, name, recorder.stub(name))
    return recorder


@pytest.fixture
def make_deployer(tmp_path, aws, steps):
    """Return a factory that builds a real Deployer against the fakes."""

    def _make(*, toml=CLEAN_TOML, env_config=None, options=None, timer=None):
        config_path = tmp_path / "deploy.toml"
        config_path.write_text(toml)
        return Deployer(
            str(config_path),
            ENVIRONMENT,
            _env_config() if env_config is None else env_config,
            options or DeployOptions(),
            timer,
        )

    return _make


@pytest.fixture(params=[True, False], ids=["timed", "untimed"])
def timer(request):
    """Every deploy() scenario runs on both timer arms."""
    return DeploymentTimer("run-1") if request.param else None


class TestDeployerInit:
    """Characterization pins for __init__'s guards and derived attributes."""

    def test_a_warning_free_config_prints_nothing_and_still_builds(self, make_deployer, capsys):
        """The clean-config path: no warnings, no blank line, full construction."""
        deployer = make_deployer()

        assert capsys.readouterr().out == ""
        assert deployer.deploy_config.get_warnings() == []
        assert deployer.app_name == APP_NAME
        assert deployer.environment == ENVIRONMENT
        assert deployer.account_id == ACCOUNT_ID
        assert deployer.region == REGION
        assert deployer.ecr_prefix == ECR_PREFIX

    def test_unknown_config_keys_warn_then_print_a_blank_line(self, make_deployer, capsys):
        """Unknown deploy.toml keys are reported one per line, then a separator."""
        make_deployer(toml=WARNING_TOML)

        captured = _plain(capsys.readouterr().out)
        assert sorted(_lines(captured)) == [
            "  ⚠ deploy.toml: Unknown key in [services.web]: cpu",
            "  ⚠ deploy.toml: Unknown key in [services.web]: memory",
        ]
        assert captured.endswith("\n\n")

    def test_missing_ecr_prefix_raises(self, make_deployer):
        """ecr_prefix is required and the error names the section to add it to."""
        with pytest.raises(ValueError, match="ecr_prefix not found in environment config"):
            make_deployer(env_config=_env_config(infrastructure={"ecr_prefix": None}))

    def test_no_configured_region_raises(self, make_deployer, monkeypatch):
        """A session with no region is fatal, not a silent default."""
        monkeypatch.setattr(
            deployer_mod.boto3.session, "Session", lambda: SimpleNamespace(region_name=None)
        )

        with pytest.raises(ValueError, match="No AWS region configured"):
            make_deployer()

    def test_cluster_name_from_config_wins(self, make_deployer):
        """A shared-environment cluster_name is used verbatim."""
        deployer = make_deployer(
            env_config=_env_config(infrastructure={"cluster_name": "shared-cluster"})
        )

        assert deployer.cluster_name == "shared-cluster"
        assert deployer.ctx.cluster_name == "shared-cluster"

    def test_cluster_name_falls_back_to_app_environment_cluster(self, make_deployer):
        """With no cluster_name configured the name is derived."""
        assert make_deployer().cluster_name == FALLBACK_CLUSTER

    def test_a_timer_is_registered_globally(self, make_deployer):
        """Sub-modules read the timer off the module global, so __init__ sets it."""
        run_timer = DeploymentTimer("run-1")

        make_deployer(timer=run_timer)

        assert get_timer() is run_timer

    def test_no_timer_leaves_the_global_alone(self, make_deployer):
        assert make_deployer(timer=None) is not None
        assert get_timer() is None

    def test_source_dir_is_resolved_relative_to_the_config_file(self, make_deployer, tmp_path):
        deployer = make_deployer()

        assert deployer.config_path == (tmp_path / "deploy.toml").resolve()
        assert deployer.source_dir == tmp_path.resolve()

    def test_infra_config_carries_defaults_for_absent_sections(self, make_deployer):
        """The deployment/scheduler sub-dicts are built with hard-coded defaults."""
        infra_config = make_deployer().infra_config

        assert infra_config.scheduler == {"enabled": False, "description": None}
        assert infra_config.deployment_config == {
            "minimum_healthy_percent": 100,
            "maximum_percent": 200,
            "circuit_breaker_enabled": False,
            "circuit_breaker_rollback": True,
        }
        assert infra_config.subnet_ids == ["subnet-1", "subnet-2"]
        assert infra_config.database_url is None

    def test_infra_config_reads_database_cache_and_storage_sections(self, make_deployer):
        deployer = make_deployer(
            env_config=_env_config(
                database={"host": "db.example.com", "port": 5432, "name": "app"},
                cache={"url": "redis://cache:6379/0"},
                storage={"media_bucket": "media-bucket"},
            )
        )

        assert deployer.infra_config.db_host == "db.example.com"
        assert deployer.infra_config.db_port == 5432
        assert deployer.infra_config.db_name == "app"
        assert deployer.infra_config.redis_url == "redis://cache:6379/0"
        assert deployer.infra_config.s3_media_bucket == "media-bucket"

    def test_context_is_built_from_the_resolved_attributes(self, make_deployer, aws):
        deployer = make_deployer(options=DeployOptions(dry_run=True))

        assert deployer.ctx.ecs_client is aws["ecs"]
        assert deployer.ctx.cluster_name == FALLBACK_CLUSTER
        assert deployer.ctx.app_name == APP_NAME
        assert deployer.ctx.environment == ENVIRONMENT
        assert deployer.ctx.region == REGION
        assert deployer.ctx.account_id == ACCOUNT_ID
        assert deployer.ctx.dry_run is True


class TestPrintServiceConfig:
    """Characterization pins for print_service_config()."""

    def test_defaults_are_shown_for_an_unsized_service(self, make_deployer, capsys):
        make_deployer().print_service_config()

        assert _lines(capsys.readouterr().out) == [
            "Service configuration:",
            "  web: cpu=256, memory=512, replicas=1 (no ALB)",
        ]

    def test_environment_sizing_overrides_and_load_balanced_is_labelled(
        self, make_deployer, capsys
    ):
        env_config = _env_config()
        env_config["services"]["config"] = {
            "web": {"cpu": 1024, "memory": 2048, "replicas": 3, "load_balanced": True}
        }

        make_deployer(env_config=env_config).print_service_config()

        assert _lines(capsys.readouterr().out) == [
            "Service configuration:",
            "  web: cpu=1024, memory=2048, replicas=3 (load_balanced)",
        ]

    def test_a_config_with_no_services_prints_only_the_heading(self, make_deployer, capsys):
        make_deployer(toml='[application]\nname = "testapp"\nsource = "."\n').print_service_config()

        assert _lines(capsys.readouterr().out) == ["Service configuration:"]

    def test_output_ends_with_a_blank_line(self, make_deployer, capsys):
        make_deployer().print_service_config()

        assert _plain(capsys.readouterr().out).endswith("\n\n")


class TestPrintEnvironmentConfig:
    """Characterization pins for print_environment_config()'s masking.

    Masking keys on a substring of the *name* is what the code does today.
    Two consequences are pinned below and are NOT endorsed: names that merely
    contain "url"/"key" are masked whether or not they are secret, and any
    name that does not match is printed in full whatever its value.
    """

    MASKING_TOML = """
[application]
name = "testapp"
source = "."

[environment]
DATABASE_URL = "postgres://user:pw@host/db"  # pragma: allowlist secret
SECRET_KEY = "s3cret"  # pragma: allowlist secret
API_TOKEN = "tok-123"
ADMIN_PASSWORD = "hunter2"  # pragma: allowlist secret
CONNECTION_STRING = "host=db"
BASE_URL = "https://example.com"
MONKEY_BUSINESS = "bananas"
LOG_LEVEL = "info"
PUBLIC_HOSTNAME = "example.com"
SSM_REF = "ssm:/testapp/staging/thing"
SM_REF = "secretsmanager:arn:aws:secretsmanager:x"
"""

    def _printed(self, capsys) -> dict[str, str]:
        pairs = [line.strip() for line in _lines(capsys.readouterr().out)[1:]]
        return dict(pair.split("=", 1) for pair in pairs)

    def test_names_matching_a_sensitive_substring_are_masked(self, make_deployer, capsys):
        make_deployer(toml=self.MASKING_TOML).print_environment_config()

        printed = self._printed(capsys)
        for masked in (
            "DATABASE_URL",
            "SECRET_KEY",
            "API_TOKEN",
            "ADMIN_PASSWORD",
            "CONNECTION_STRING",
        ):
            assert printed[masked] == "***"

    def test_innocuous_names_containing_url_or_key_are_masked_too(self, make_deployer, capsys):
        """Over-masking, pinned not endorsed: "url" and "key" are substrings."""
        make_deployer(toml=self.MASKING_TOML).print_environment_config()

        printed = self._printed(capsys)
        assert printed["BASE_URL"] == "***"
        assert printed["MONKEY_BUSINESS"] == "***"

    def test_non_matching_names_are_printed_in_full(self, make_deployer, capsys):
        """Pinned not endorsed: a secret under a non-matching name is exposed."""
        make_deployer(toml=self.MASKING_TOML).print_environment_config()

        printed = self._printed(capsys)
        assert printed["LOG_LEVEL"] == "info"
        assert printed["PUBLIC_HOSTNAME"] == "example.com"

    def test_ssm_and_secretsmanager_references_are_shown_not_masked(self, make_deployer, capsys):
        """The reference is shown so the operator can see which secret is wired."""
        make_deployer(toml=self.MASKING_TOML).print_environment_config()

        printed = self._printed(capsys)
        assert printed["SSM_REF"] == "ssm:/testapp/staging/thing"
        assert printed["SM_REF"] == "secretsmanager:arn:aws:secretsmanager:x"

    def test_keys_are_printed_in_sorted_order(self, make_deployer, capsys):
        make_deployer(toml=self.MASKING_TOML).print_environment_config()

        names = list(self._printed(capsys))
        assert names == sorted(names)

    def test_a_non_string_value_under_a_non_masked_name_raises(self, make_deployer):
        """LATENT BUG, pinned: value.startswith() assumes str, TOML gives int/bool.

        A masked name never reaches the .startswith() call, so this only bites
        variables whose names miss every sensitive substring.
        """
        toml = """
[application]
name = "testapp"
source = "."

[environment]
MAX_WORKERS = 4
"""
        deployer = make_deployer(toml=toml)

        with pytest.raises(AttributeError, match="startswith"):
            deployer.print_environment_config()

    def test_a_non_string_value_under_a_masked_name_is_fine(self, make_deployer, capsys):
        """The same int is harmless when the name short-circuits to "***"."""
        toml = """
[application]
name = "testapp"
source = "."

[environment]
SECRET_ROTATION_DAYS = 4
"""
        make_deployer(toml=toml).print_environment_config()

        assert "  SECRET_ROTATION_DAYS=***" in _lines(capsys.readouterr().out)

    def test_an_empty_environment_prints_only_the_heading(self, make_deployer, capsys):
        make_deployer().print_environment_config()

        assert _lines(capsys.readouterr().out) == ["Global environment variables:"]


class TestCheckInfrastructureStatus:
    """Characterization pins for all five returns and both except arms."""

    def _deployer(self, make_deployer, **infra):
        return make_deployer(env_config=_env_config(infrastructure=infra))

    def test_no_rds_instance_configured_is_a_clean_status(self, make_deployer, aws):
        status = self._deployer(make_deployer).check_infrastructure_status()

        assert status == InfraStatus(warnings=[], is_critical=False)
        assert aws["rds"].calls == []

    def test_a_missing_rds_instance_is_a_critical_warning(self, make_deployer, aws):
        aws["rds"].error = _DBInstanceNotFoundFault("nope")

        status = self._deployer(make_deployer, rds_instance_id=RDS_ID).check_infrastructure_status()

        assert status == InfraStatus(
            warnings=[f"RDS instance '{RDS_ID}' not found"], is_critical=True
        )
        assert aws["rds"].calls == [{"DBInstanceIdentifier": RDS_ID}]

    def test_any_other_describe_error_is_swallowed(self, make_deployer, aws):
        """Pinned not endorsed: a credentials or network failure reads as OK."""
        aws["rds"].error = RuntimeError("boom")

        status = self._deployer(make_deployer, rds_instance_id=RDS_ID).check_infrastructure_status()

        assert status == InfraStatus(warnings=[], is_critical=False)

    def test_an_empty_instance_list_is_a_clean_status(self, make_deployer, aws):
        aws["rds"].response = {"DBInstances": []}

        status = self._deployer(make_deployer, rds_instance_id=RDS_ID).check_infrastructure_status()

        assert status == InfraStatus(warnings=[], is_critical=False)

    def test_an_available_instance_is_a_clean_status(self, make_deployer, aws):
        aws["rds"].response = {"DBInstances": [{"DBInstanceStatus": "available"}]}

        status = self._deployer(make_deployer, rds_instance_id=RDS_ID).check_infrastructure_status()

        assert status == InfraStatus(warnings=[], is_critical=False)

    def test_any_other_instance_status_is_critical(self, make_deployer, aws):
        aws["rds"].response = {"DBInstances": [{"DBInstanceStatus": "stopped"}]}

        status = self._deployer(make_deployer, rds_instance_id=RDS_ID).check_infrastructure_status()

        assert status == InfraStatus(
            warnings=[f"RDS instance '{RDS_ID}' is stopped (not available)."], is_critical=True
        )

    def test_scheduler_hours_are_appended_when_both_keys_are_set(self, make_deployer, aws):
        aws["rds"].response = {"DBInstances": [{"DBInstanceStatus": "stopped"}]}
        env_config = _env_config(infrastructure={"rds_instance_id": RDS_ID})
        env_config["scheduler"] = {"enabled": True, "description": "Mon-Fri 08:00-18:00"}

        status = make_deployer(env_config=env_config).check_infrastructure_status()

        assert status.warnings == [
            f"RDS instance '{RDS_ID}' is stopped (not available)."
            "\n         Service hours: Mon-Fri 08:00-18:00"
        ]

    def test_scheduler_hours_need_both_enabled_and_a_description(self, make_deployer, aws):
        aws["rds"].response = {"DBInstances": [{"DBInstanceStatus": "stopped"}]}
        env_config = _env_config(infrastructure={"rds_instance_id": RDS_ID})
        env_config["scheduler"] = {"enabled": True}

        status = make_deployer(env_config=env_config).check_infrastructure_status()

        assert status.warnings == [f"RDS instance '{RDS_ID}' is stopped (not available)."]


def _critical_env_config():
    """An env config whose RDS instance the fake will report as stopped."""
    return _env_config(infrastructure={"rds_instance_id": RDS_ID})


def _stop_the_database(aws):
    aws["rds"].response = {"DBInstances": [{"DBInstanceStatus": "stopped"}]}


class TestDeploySteps:
    """Characterization pins for deploy()'s step order, args and kwargs.

    Every test runs on both timer arms via the `timer` fixture.
    """

    def test_the_seven_steps_run_in_order(self, make_deployer, steps, timer):
        make_deployer(timer=timer).deploy()

        assert steps.names == list(STEP_NAMES)

    def test_each_step_receives_exactly_these_arguments(self, make_deployer, steps, timer, aws):
        deployer = make_deployer(timer=timer)

        deployer.deploy()

        assert steps.calls == [
            ("ecr_login", (aws["ecr"], False), {}),
            (
                "build_and_push_images",
                (),
                {
                    "config": deployer.config,
                    "source_dir": deployer.source_dir,
                    "ecr_prefix": ECR_PREFIX,
                    "account_id": ACCOUNT_ID,
                    "region": REGION,
                    "environment": ENVIRONMENT,
                    "dry_run": False,
                    "ecr_client": aws["ecr"],
                    "force_build": False,
                },
            ),
            (
                "create_database_extensions",
                (),
                {
                    "config": deployer.config,
                    "env_config": deployer.env_config,
                    "region": REGION,
                    "dry_run": False,
                },
            ),
            (
                "start_migrations",
                (deployer.ctx, IMAGE_URIS),
                {"source_dir": deployer.source_dir},
            ),
            ("deploy_services", (deployer.ctx, IMAGE_URIS), {}),
            ("wait_for_migrations", (aws["ecs"], MIGRATION_TASK), {}),
            ("wait_for_stable", (deployer.ctx,), {}),
        ]

    def test_a_clean_run_returns_the_image_uris_and_no_failures(self, make_deployer, timer, capsys):
        result = make_deployer(timer=timer).deploy()

        assert result == (IMAGE_URIS, [])
        assert "Deployment complete!" in _plain(capsys.readouterr().out)

    def test_the_banner_names_account_region_and_cluster(self, make_deployer, timer, capsys):
        make_deployer(timer=timer).deploy()

        out = _lines(capsys.readouterr().out)
        assert out[:4] == [
            f"Deploying {APP_NAME} to {ENVIRONMENT}",
            f"  Account: {ACCOUNT_ID}",
            f"  Region:  {REGION}",
            f"  Cluster: {FALLBACK_CLUSTER}",
        ]

    def test_print_interleaving_around_the_steps(self, make_deployer, timer, capsys):
        """Pins where the blank lines fall relative to each step marker.

        Note there is no blank line after create_database_extensions, unlike
        every other step. Pinned as-is.
        """
        make_deployer(timer=timer).deploy()

        out = _all_lines(capsys.readouterr().out)
        assert out == [
            "",
            f"Deploying {APP_NAME} to {ENVIRONMENT}",
            f"  Account: {ACCOUNT_ID}",
            f"  Region:  {REGION}",
            f"  Cluster: {FALLBACK_CLUSTER}",
            "",
            "Service configuration:",
            "  web: cpu=256, memory=512, replicas=1 (no ALB)",
            "",
            "Global environment variables:",
            "",
            "[ecr_login]",
            "",
            "[build_and_push_images]",
            "",
            "[create_database_extensions]",
            "[start_migrations]",
            "",
            "[deploy_services]",
            "",
            "[wait_for_migrations]",
            "",
            "[wait_for_stable]",
            "",
            "Deployment complete!",
        ]

    def test_dry_run_labels_the_banner_and_threads_the_flag_through(
        self, make_deployer, steps, timer, aws, capsys
    ):
        """Dry run still calls all seven steps; each step handles its own no-op."""
        deployer = make_deployer(options=DeployOptions(dry_run=True), timer=timer)

        assert deployer.deploy() == (IMAGE_URIS, [])

        assert "  Mode:    DRY RUN" in _lines(capsys.readouterr().out)
        assert steps.names == list(STEP_NAMES)
        assert steps.calls[0][1] == (aws["ecr"], True)
        assert steps.calls[1][2]["dry_run"] is True
        assert steps.calls[2][2]["dry_run"] is True
        assert deployer.ctx.dry_run is True

    def test_force_build_reaches_only_the_image_step(self, make_deployer, steps, timer):
        make_deployer(options=DeployOptions(force_build=True), timer=timer).deploy()

        assert steps.calls[1][2]["force_build"] is True
        assert steps.calls[1][2]["dry_run"] is False


class TestDeployInfrastructureGuard:
    """Characterization pins for the critical-infrastructure branch."""

    def test_critical_infrastructure_aborts_before_any_step(
        self, make_deployer, steps, timer, aws, capsys
    ):
        _stop_the_database(aws)
        deployer = make_deployer(env_config=_critical_env_config(), timer=timer)

        with pytest.raises(RuntimeError, match="Infrastructure unavailable"):
            deployer.deploy()

        assert steps.calls == []
        out = _lines(capsys.readouterr().out)
        assert f"  ⚠ RDS instance '{RDS_ID}' is stopped (not available)." in out
        assert "  ✗ Cannot deploy: critical infrastructure is unavailable." in out
        assert "  The database must be running for migrations to succeed." in out
        assert "  Start the environment first:" in out
        assert f"    uv run python bin/environment.py {APP_NAME}-{ENVIRONMENT} start" in out
        assert "  Or use --force to deploy anyway (migrations will fail)." in out

    def test_force_continues_past_critical_infrastructure(
        self, make_deployer, steps, timer, aws, capsys
    ):
        _stop_the_database(aws)
        deployer = make_deployer(
            env_config=_critical_env_config(), options=DeployOptions(force=True), timer=timer
        )

        assert deployer.deploy() == (IMAGE_URIS, [])

        assert steps.names == list(STEP_NAMES)
        out = _lines(capsys.readouterr().out)
        assert "  ⚠ Continuing anyway due to --force flag. Migrations will likely fail." in out
        assert "Cannot deploy: critical infrastructure is unavailable." not in out

    def test_a_non_critical_warning_would_not_abort(self, make_deployer, steps, timer, capsys):
        """check_infrastructure_status never returns this shape today; the
        branch exists, so it is pinned by constructing the status directly."""
        deployer = make_deployer(timer=timer)
        deployer.check_infrastructure_status = lambda: InfraStatus(
            warnings=["disk is getting full"], is_critical=False
        )

        assert deployer.deploy() == (IMAGE_URIS, [])

        assert steps.names == list(STEP_NAMES)
        assert "  ⚠ disk is getting full" in _lines(capsys.readouterr().out)


class TestDeployMigrationFailure:
    """Characterization pins for the wait_for_migrations RuntimeError path."""

    def test_the_error_propagates_and_stops_the_pipeline(self, make_deployer, steps, timer):
        steps.raises["wait_for_migrations"] = RuntimeError("migration task exited 1")
        deployer = make_deployer(timer=timer)

        with pytest.raises(RuntimeError, match="migration task exited 1"):
            deployer.deploy()

        assert steps.names == list(STEP_NAMES[:6])
        assert "wait_for_stable" not in steps.names

    def test_earlier_infrastructure_warnings_are_re_displayed(
        self, make_deployer, steps, timer, aws, capsys
    ):
        _stop_the_database(aws)
        steps.raises["wait_for_migrations"] = RuntimeError("migration task exited 1")
        deployer = make_deployer(
            env_config=_critical_env_config(), options=DeployOptions(force=True), timer=timer
        )

        with pytest.raises(RuntimeError, match="migration task exited 1"):
            deployer.deploy()

        out = _lines(capsys.readouterr().out)
        reminder = out.index("  ⚠ Reminder: infrastructure issues were detected earlier:")
        assert out[reminder + 1] == f"  ⚠   RDS instance '{RDS_ID}' is stopped (not available)."

    def test_no_warnings_means_no_reminder(self, make_deployer, steps, timer, capsys):
        steps.raises["wait_for_migrations"] = RuntimeError("migration task exited 1")

        with pytest.raises(RuntimeError):
            make_deployer(timer=timer).deploy()

        assert "Reminder: infrastructure issues" not in _plain(capsys.readouterr().out)

    def test_a_timed_run_records_the_failed_step(self, make_deployer, steps):
        """The failing step is still recorded, marked unsuccessful, with the message."""
        run_timer = DeploymentTimer("run-1")
        steps.raises["wait_for_migrations"] = RuntimeError("migration task exited 1")

        with pytest.raises(RuntimeError):
            make_deployer(timer=run_timer).deploy()

        failed = run_timer.report.steps[-1]
        assert failed.name == "wait_for_migrations"
        assert failed.success is False
        assert failed.error == "migration task exited 1"
        assert run_timer.report.end_time == 0.0  # finish() is never reached


class TestDeployHealthChecks:
    """Characterization pins for the health-failure return path."""

    def test_failures_are_reported_and_returned(self, make_deployer, steps, timer, capsys):
        steps.returns["wait_for_stable"] = ["web", "worker"]

        result = make_deployer(timer=timer).deploy()

        assert result == (IMAGE_URIS, ["web", "worker"])
        out = _lines(capsys.readouterr().out)
        assert "Deployment completed with warnings:" in out
        assert "  The following services did not pass health checks: web, worker" in out
        assert "  Services may still become healthy - check the AWS console." in out
        assert "Deployment complete!" not in out

    def test_no_failures_reports_success(self, make_deployer, steps, timer, capsys):
        steps.returns["wait_for_stable"] = []

        result = make_deployer(timer=timer).deploy()

        assert result == (IMAGE_URIS, [])
        out = _lines(capsys.readouterr().out)
        assert "Deployment complete!" in out
        assert "Deployment completed with warnings:" not in out


class TestDeployTimerArmsAgree:
    """The pin that makes 53e-3b's nine-conditional collapse verifiable.

    Each scenario is run twice inside one test — once with a DeploymentTimer,
    once with timer=None — against the same fake AWS clients, and the two runs
    must produce identical step calls and identical output. If the collapse
    changes what either arm does, these fail.
    """

    def _run_both_arms(self, make_deployer, steps, capsys, **kwargs):
        run_timer = DeploymentTimer("run-1")
        make_deployer(timer=run_timer, **kwargs).deploy()
        timed = (list(steps.calls), _plain(capsys.readouterr().out))

        steps.calls.clear()
        set_timer(None)
        make_deployer(timer=None, **kwargs).deploy()
        untimed = (list(steps.calls), _plain(capsys.readouterr().out))

        return run_timer, timed, untimed

    def test_a_clean_run_is_identical_on_both_arms(self, make_deployer, steps, capsys):
        run_timer, timed, untimed = self._run_both_arms(make_deployer, steps, capsys)

        assert timed[0] == untimed[0]
        assert timed[1] == untimed[1]
        assert [step.name for step in run_timer.report.steps] == TIMER_STEP_NAMES

    def test_a_dry_run_is_identical_on_both_arms(self, make_deployer, steps, capsys):
        _, timed, untimed = self._run_both_arms(
            make_deployer, steps, capsys, options=DeployOptions(dry_run=True)
        )

        assert timed[0] == untimed[0]
        assert timed[1] == untimed[1]

    def test_a_forced_run_over_bad_infrastructure_is_identical_on_both_arms(
        self, make_deployer, steps, capsys, aws
    ):
        _stop_the_database(aws)

        _, timed, untimed = self._run_both_arms(
            make_deployer,
            steps,
            capsys,
            env_config=_critical_env_config(),
            options=DeployOptions(force=True),
        )

        assert timed[0] == untimed[0]
        assert timed[1] == untimed[1]

    def test_a_health_check_failure_is_identical_on_both_arms(self, make_deployer, steps, capsys):
        steps.returns["wait_for_stable"] = ["web"]

        _, timed, untimed = self._run_both_arms(make_deployer, steps, capsys)

        assert timed[0] == untimed[0]
        assert timed[1] == untimed[1]

    def test_a_migration_failure_is_identical_on_both_arms(self, make_deployer, steps, capsys):
        steps.raises["wait_for_migrations"] = RuntimeError("migration task exited 1")

        with pytest.raises(RuntimeError):
            make_deployer(timer=DeploymentTimer("run-1")).deploy()
        timed = (list(steps.calls), _plain(capsys.readouterr().out))

        steps.calls.clear()
        set_timer(None)
        with pytest.raises(RuntimeError):
            make_deployer(timer=None).deploy()
        untimed = (list(steps.calls), _plain(capsys.readouterr().out))

        assert timed[0] == untimed[0]
        assert timed[1] == untimed[1]

    def test_an_infrastructure_abort_is_identical_on_both_arms(
        self, make_deployer, steps, capsys, aws
    ):
        """The abort happens before the first timed step, so both arms match."""
        _stop_the_database(aws)
        run_timer = DeploymentTimer("run-1")
        with pytest.raises(RuntimeError, match="Infrastructure unavailable"):
            make_deployer(env_config=_critical_env_config(), timer=run_timer).deploy()
        timed = (list(steps.calls), _plain(capsys.readouterr().out))

        steps.calls.clear()
        set_timer(None)
        with pytest.raises(RuntimeError, match="Infrastructure unavailable"):
            make_deployer(env_config=_critical_env_config(), timer=None).deploy()
        untimed = (list(steps.calls), _plain(capsys.readouterr().out))

        assert timed[0] == untimed[0] == []
        assert timed[1] == untimed[1]
        assert run_timer.report.steps == []


class TestCommonDeployOptions:
    """Characterization pins for the decorator's six flags."""

    def _command(self):
        @click.command()
        @common_deploy_options
        @click.pass_context
        def cmd(ctx, **kwargs):
            print(sorted(f"{k}={v}" for k, v in kwargs.items()))

        return cmd

    def test_all_six_flags_default_to_false(self):
        result = CliRunner().invoke(self._command(), [])

        assert result.exit_code == 0
        assert result.output.strip() == str(
            [
                "dry_run=False",
                "force=False",
                "force_build=False",
                "skip_cluster_check=False",
                "skip_ecr_check=False",
                "skip_secrets_check=False",
            ]
        )

    def test_the_wrapper_forwards_every_flag(self):
        result = CliRunner().invoke(
            self._command(),
            [
                "--dry-run",
                "--force",
                "--force-build",
                "--skip-ecr-check",
                "--skip-secrets-check",
                "--skip-cluster-check",
            ],
        )

        assert result.exit_code == 0
        assert "dry_run=True" in result.output
        assert "skip_cluster_check=True" in result.output

    def test_the_help_text_lists_the_flags(self):
        result = CliRunner().invoke(self._command(), ["--help"])

        assert "Show what would be done without making changes" in result.output
        assert "Deploy even if infrastructure is unavailable" in result.output
