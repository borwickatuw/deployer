"""
Reach the GPU build host's Docker daemon over an SSM port-forward.

An image FROM a base too large to pull to a laptop (deploy.toml
``build_on_gpu_host = true``) is built on the daemon of the environment's
GPU container instance — the same box that runs it, so the base layers
are already on its disk. The instance exposes its Docker API on loopback
only (modules/ecs-gpu-capacity); the operator's ``ssm:StartSession`` is the
only door, and the bootstrap scopes it to instances tagged
``deployer-build-host``.

Nothing here starts the instance. The environment's schedule does
(``bin/environment.py start``); a stopped box is reported, not woken.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator

from botocore.exceptions import ClientError

from ..utils import log, log_success

# The Docker API port the instance's loopback proxy listens on
DOCKER_API_PORT = 2375
TUNNEL_READY_TIMEOUT_SECONDS = 60


class BuildHostUnavailableError(RuntimeError):
    """The GPU build host is not running."""


def find_build_instance(ec2_client, instance_id: str) -> str:
    """Check that the environment's GPU instance is running.

    Args:
        ec2_client: boto3 ``ec2`` client.
        instance_id: The instance (config.toml ``gpu_instance_id``).

    Returns:
        The instance id, once its state is ``running``.

    Raises:
        BuildHostUnavailableError: The instance is starting, stopped
            (off-schedule), or does not exist. Never starts anything.
    """
    try:
        response = ec2_client.describe_instances(InstanceIds=[instance_id])
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "InvalidInstanceID.NotFound":
            raise
        response = {}
    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
    ]
    state = instances[0]["State"]["Name"] if instances else None
    if state == "running":
        return instance_id
    if state == "pending":
        raise BuildHostUnavailableError(
            f"The GPU build host {instance_id} is starting: wait for it to reach "
            f"running and deploy again."
        )
    if state in ("stopped", "stopping"):
        raise BuildHostUnavailableError(
            f"The GPU build host {instance_id} is {state} (off-schedule): start the "
            f"environment (bin/environment.py start <env>) and deploy again."
        )
    raise BuildHostUnavailableError(
        f"The GPU build host {instance_id} "
        f"{'is ' + state if state else 'does not exist'}: config.toml's "
        f"gpu_instance_id does not name the environment's GPU instance — re-apply "
        f"the environment with tofu and check that gpu_instance_id is "
        f'"${{tofu:gpu_instance_id}}".'
    )


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_docker(env: dict[str, str], timeout: int) -> None:
    """Poll ``docker version`` through the tunnel until the daemon answers."""
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if result.returncode == 0:
            log_success(f"GPU build host Docker {result.stdout.strip()} reachable")
            return
        last_error = (result.stderr or "").strip()
        time.sleep(2)
    raise RuntimeError(
        f"The SSM tunnel to the GPU build host's Docker daemon did not come up "
        f"within {timeout}s: {last_error or 'docker printed nothing'}"
    )


@contextlib.contextmanager
def remote_docker(instance_id: str, region: str) -> Iterator[dict[str, str]]:
    """An environment whose ``DOCKER_HOST`` is the build host's daemon.

    Opens ``aws ssm start-session`` with the port-forwarding document to the
    instance's loopback Docker API on a free local port, waits for the
    daemon to answer, yields the environment for ``docker`` commands, and
    terminates the session on exit. The AWS CLI's ``session-manager-plugin``
    must be installed (``brew install --cask session-manager-plugin``).

    Args:
        instance_id: The build host (``find_build_instance``).
        region: The instance's region.

    Yields:
        A copy of ``os.environ`` with ``DOCKER_HOST`` set.
    """
    if shutil.which("session-manager-plugin") is None:
        raise RuntimeError(
            "session-manager-plugin is not installed; the GPU build host is reached "
            "over an SSM port-forward. Install it: brew install --cask "
            "session-manager-plugin (see the AWS Session Manager plugin docs)."
        )
    local_port = _free_local_port()
    log(f"Opening SSM tunnel to {instance_id} (localhost:{local_port} -> docker)...")
    session = subprocess.Popen(
        [
            "aws",
            "ssm",
            "start-session",
            "--region",
            region,
            "--target",
            instance_id,
            "--document-name",
            "AWS-StartPortForwardingSession",
            "--parameters",
            f"portNumber={DOCKER_API_PORT},localPortNumber={local_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = {**os.environ, "DOCKER_HOST": f"tcp://127.0.0.1:{local_port}"}
    try:
        _wait_for_docker(env, TUNNEL_READY_TIMEOUT_SECONDS)
        yield env
    finally:
        session.terminate()
        try:
            session.wait(timeout=10)
        except subprocess.TimeoutExpired:
            session.kill()
