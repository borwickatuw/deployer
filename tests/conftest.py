"""Shared test fixtures for deployer tests."""

import os
import tempfile
from pathlib import Path

import pytest
from moto import mock_aws

from deployer.aws import cli as aws_cli_module
from deployer.emergency import checkpoint as checkpoint_module


class FakeAwsCli:
    """Records the argv the `aws` CLI would have been run with.

    Replies with a queue of (success, output) results; the final result
    repeats, so a test that only cares about the argv can ignore the queue.
    """

    def __init__(self):
        self.calls: list[list[str]] = []
        self.results: list[tuple[bool, str]] = [(True, "{}")]

    def replies(self, *results: tuple[bool, str]) -> None:
        """Set the (success, output) results to return, in order."""
        self.results = list(results)

    def __call__(self, cmd: list[str], cwd: str | None = None) -> tuple[bool, str]:
        """Stand in for run_command."""
        self.calls.append(cmd)
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]

    @property
    def argv(self) -> list[str]:
        """The single argv emitted, asserting there was exactly one call."""
        assert len(self.calls) == 1, f"expected 1 call, got {len(self.calls)}"
        return self.calls[0]


@pytest.fixture
def aws_cli(monkeypatch) -> FakeAwsCli:
    """Stub the `aws` CLI at the deployer.aws.cli seam.

    cognito, rds and cloudwatch all reach the CLI through deployer.aws.cli, so
    patching run_command there covers every caller. No `aws` binary and no
    credentials are involved.
    """
    fake = FakeAwsCli()
    monkeypatch.setattr(aws_cli_module, "run_command", fake)
    return fake


@pytest.fixture
def fixtures_dir() -> Path:
    """Return path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_deploy_toml(fixtures_dir: Path) -> Path:
    """Return path to sample deploy.toml fixture."""
    return fixtures_dir / "sample_deploy.toml"


@pytest.fixture
def sample_docker_compose(fixtures_dir: Path) -> Path:
    """Return path to sample docker-compose.yml fixture."""
    return fixtures_dir / "sample_docker_compose.yml"


@pytest.fixture
def temp_project_dir(
    sample_deploy_toml: Path,
    sample_docker_compose: Path,
) -> Path:
    """Create a temporary project directory with deploy.toml and docker-compose.yml."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Copy fixtures
        (project_dir / "deploy.toml").write_text(sample_deploy_toml.read_text())
        (project_dir / "docker-compose.yml").write_text(sample_docker_compose.read_text())

        yield project_dir


@pytest.fixture
def mock_env_vars():
    """Fixture to temporarily set environment variables."""
    original_env = os.environ.copy()

    def _set_env(**kwargs):
        for key, value in kwargs.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    yield _set_env

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_aws_credentials(mock_env_vars):
    """Set up mock AWS credentials for boto3."""
    mock_env_vars(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_SECURITY_TOKEN="testing",
        AWS_SESSION_TOKEN="testing",
        AWS_DEFAULT_REGION="us-west-2",
        # A developer's real AWS_PROFILE must not leak into mocked calls:
        # boto3 resolves the profile before moto ever sees the request, so a
        # profile that doesn't exist locally fails the test for the wrong reason.
        AWS_PROFILE=None,
    )


@pytest.fixture
def mocked_aws(mock_aws_credentials):
    """Run the test against moto's in-memory AWS backends.

    Region is us-west-2, matching ``mock_aws_credentials``. Clients created
    inside the fixture's scope (including those built by the emergency
    modules' own ``_get_*_client`` helpers) are intercepted by moto.
    """
    with mock_aws():
        yield


@pytest.fixture
def checkpoint_dir(tmp_path, monkeypatch) -> Path:
    """Redirect the emergency checkpoint directory into ``tmp_path``.

    ``create_checkpoint``/``list_checkpoints`` take no directory argument by
    design -- their only production caller (``bin/emergency.py``) wants the
    single global location under the deployer root -- so ``get_deployer_root``
    is the seam tests use instead of widening the API for testability.

    Returns:
        Path to the redirected local/checkpoints/ directory (not created).
    """
    monkeypatch.setattr(checkpoint_module, "get_deployer_root", lambda: tmp_path)
    return tmp_path / "local" / "checkpoints"
