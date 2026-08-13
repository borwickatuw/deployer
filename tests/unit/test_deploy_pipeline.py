"""Tests for deployer.deploy.pipeline — the shared deploy back half."""

from pathlib import Path

import pytest

from deployer.deploy import pipeline
from deployer.deploy.context import DeployOptions, EnvironmentTarget
from deployer.deploy.pipeline import run_deploy_pipeline
from deployer.deploy.preflight import PreflightError, PreflightOptions
from deployer.timing import DeploymentTimer

ENV_CONFIG = {"infrastructure": {"cluster_name": "myapp-staging-cluster"}}
TARGET = EnvironmentTarget("myapp-staging", "staging", ENV_CONFIG)


class StubDeployer:
    """Stand-in for Deployer that records its kwargs and replays a canned result."""

    last_kwargs: dict = {}
    result: tuple = ({}, [])
    error: Exception | None = None
    init_error: Exception | None = None

    def __init__(self, **kwargs):
        if type(self).init_error:
            raise type(self).init_error
        type(self).last_kwargs = kwargs

    def deploy(self):
        if type(self).error:
            raise type(self).error
        return type(self).result


@pytest.fixture
def stub_pipeline(monkeypatch, tmp_path):
    """Neutralise every collaborator so only pipeline control flow is under test."""
    StubDeployer.last_kwargs = {}
    StubDeployer.result = ({}, [])
    StubDeployer.error = None
    StubDeployer.init_error = None

    monkeypatch.setattr(pipeline, "parse_deploy_config", lambda path: {"parsed": str(path)})
    monkeypatch.setattr(pipeline, "run_preflight_checks", lambda **_kwargs: None)
    monkeypatch.setattr(pipeline, "Deployer", StubDeployer)

    deploy_toml = tmp_path / "deploy.toml"
    deploy_toml.write_text("")
    return deploy_toml


def _run(deploy_toml: Path, **overrides):
    kwargs = {"preflight": PreflightOptions(), "options": DeployOptions()}
    kwargs.update(overrides)
    return run_deploy_pipeline(deploy_toml, TARGET, **kwargs)


class TestSuccessPath:
    """Tests for the happy path and its exit codes."""

    def test_returns_0_with_no_health_failures(self, stub_pipeline):
        assert _run(stub_pipeline) == 0

    def test_health_failures_return_2(self, stub_pipeline):
        StubDeployer.result = ({"web": "arn"}, ["web"])
        assert _run(stub_pipeline) == pipeline.HEALTH_FAILURE_EXIT_CODE == 2

    def test_deploy_flags_are_forwarded(self, stub_pipeline):
        options = DeployOptions(dry_run=True, force=True, force_build=True)
        _run(stub_pipeline, options=options)
        assert StubDeployer.last_kwargs["options"] is options

    def test_target_is_unpacked_for_the_deployer(self, stub_pipeline):
        """Deployer takes the environment *type* and the resolved config."""
        _run(stub_pipeline)
        assert StubDeployer.last_kwargs["environment"] == "staging"
        assert StubDeployer.last_kwargs["env_config"] is ENV_CONFIG


class TestFailurePaths:
    """Tests for each guarded failure."""

    def test_unparseable_deploy_toml_returns_1(self, stub_pipeline, monkeypatch, capsys):
        def boom(path):
            raise ValueError("expected a table")

        monkeypatch.setattr(pipeline, "parse_deploy_config", boom)
        assert _run(stub_pipeline) == 1
        assert "Failed to parse deploy.toml" in capsys.readouterr().out

    def test_preflight_failure_returns_1(self, stub_pipeline, monkeypatch, capsys):
        def boom(**kwargs):
            raise PreflightError("ECR repository missing")

        monkeypatch.setattr(pipeline, "run_preflight_checks", boom)
        assert _run(stub_pipeline) == 1
        assert "ECR repository missing" in capsys.readouterr().out

    def test_deployer_construction_failure_returns_1(self, stub_pipeline, capsys):
        StubDeployer.init_error = ValueError("unknown service 'web'")
        assert _run(stub_pipeline) == 1
        assert "unknown service 'web'" in capsys.readouterr().out


class TestPushErrorHandling:
    """Tests for the handle_push_error branch."""

    def test_push_error_returns_1(self, stub_pipeline, capsys):
        StubDeployer.error = RuntimeError("Push failed for web")
        assert _run(stub_pipeline) == 1
        assert "Push failed for web" in capsys.readouterr().out

    def test_ecr_hint_is_only_shown_when_requested(self, stub_pipeline, capsys):
        StubDeployer.error = RuntimeError("Push failed for web")

        _run(stub_pipeline, ecr_hint=True)
        assert "verify ECR repository access" in capsys.readouterr().out

        _run(stub_pipeline, ecr_hint=False)
        assert "verify ECR repository access" not in capsys.readouterr().out

    def test_other_runtime_errors_propagate(self, stub_pipeline):
        StubDeployer.error = RuntimeError("migration failed")
        with pytest.raises(RuntimeError, match="migration failed"):
            _run(stub_pipeline)


class TestTimingReport:
    """Tests for the optional timing report."""

    def test_no_report_without_a_timer(self, stub_pipeline, capsys):
        _run(stub_pipeline)
        assert "Timing report" not in capsys.readouterr().out

    def test_report_printed_when_timer_passed(self, stub_pipeline, capsys):
        _run(stub_pipeline, timer=DeploymentTimer("deploy-test"))
        assert "Timing report" in capsys.readouterr().out

    def test_report_saved_when_output_path_given(self, stub_pipeline, tmp_path, capsys):
        output = tmp_path / "timing.json"
        _run(stub_pipeline, timer=DeploymentTimer("deploy-test"), timing_output=output)
        assert output.exists()
        assert "deploy-test" in output.read_text()
        assert "Timing saved to" in capsys.readouterr().out

    def test_output_path_ignored_without_a_timer(self, stub_pipeline, tmp_path):
        output = tmp_path / "timing.json"
        _run(stub_pipeline, timing_output=output)
        assert not output.exists()
