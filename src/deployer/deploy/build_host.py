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

from ..utils import log, log_success

# The Docker API port the instance's loopback proxy listens on
DOCKER_API_PORT = 2375
TUNNEL_READY_TIMEOUT_SECONDS = 60


class BuildHostUnavailableError(RuntimeError):
    """The GPU build host is not running."""


def find_build_instance(asg_client, asg_name: str) -> str:
    """The InService, Healthy instance of the GPU Auto Scaling group.

    Args:
        asg_client: boto3 ``autoscaling`` (EC2 Auto Scaling) client.
        asg_name: The group's name (config.toml ``gpu_asg_name``).

    Returns:
        The instance id.

    Raises:
        BuildHostUnavailableError: No such instance — the environment is stopped
            (the instance is in the warm pool or gone), or the group does
            not exist. Never auto-starts anything.
    """
    response = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    groups = response.get("AutoScalingGroups", [])
    if not groups:
        raise BuildHostUnavailableError(
            f"Auto Scaling group '{asg_name}' not found: the environment's GPU "
            f"capacity is not applied (tofu) or config.toml's gpu_asg_name is wrong."
        )
    instances = [
        instance
        for instance in groups[0].get("Instances", [])
        if instance.get("LifecycleState") == "InService"
        and instance.get("HealthStatus") == "Healthy"
    ]
    if not instances:
        raise BuildHostUnavailableError(
            f"No running instance in Auto Scaling group '{asg_name}': the GPU build "
            f"host is stopped (off-schedule) or still launching. Start the "
            f"environment (bin/environment.py start <env>), wait for the instance "
            f"to be InService, and deploy again."
        )
    return instances[0]["InstanceId"]


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
