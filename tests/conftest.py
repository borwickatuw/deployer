"""Shared test fixtures for deployer tests."""

import os
import tempfile
from pathlib import Path

import pytest


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
    )
