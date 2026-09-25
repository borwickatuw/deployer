"""Tests for deployer.deploy.build_host: finding the GPU build host and the
SSM tunnel to its Docker daemon.

Stubbing is at the outermost boundary: a fake boto3 EC2 client,
and ``subprocess.run``/``subprocess.Popen``/``shutil.which`` at the stdlib.
"""

import shutil
import subprocess

import pytest
from botocore.exceptions import ClientError

from deployer.deploy import build_host
from deployer.deploy.build_host import (
    BuildHostUnavailableError,
    find_build_instance,
    remote_docker,
)

INSTANCE = "i-0123456789abcdef0"


class _FakeEc2:
    """``describe_instances`` for one instance id, in a given state or absent."""

    def __init__(self, state: str | None):
        self._state = state
        self.calls = []

    def describe_instances(self, **kwargs):
        self.calls.append(kwargs)
        if self._state is None:
            raise ClientError(
                {"Error": {"Code": "InvalidInstanceID.NotFound", "Message": "gone"}},
                "DescribeInstances",
            )
        return {
            "Reservations": [
                {"Instances": [{"InstanceId": INSTANCE, "State": {"Name": self._state}}]}
            ]
        }


class TestFindBuildInstance:
    def test_a_running_instance_is_the_build_host(self):
        ec2 = _FakeEc2("running")
        assert find_build_instance(ec2, INSTANCE) == INSTANCE
        assert ec2.calls == [{"InstanceIds": [INSTANCE]}]

    def test_a_pending_instance_is_reported_as_starting(self):
        with pytest.raises(BuildHostUnavailableError, match="is starting.*deploy again"):
            find_build_instance(_FakeEc2("pending"), INSTANCE)

    @pytest.mark.parametrize("state", ["stopped", "stopping"])
    def test_a_stopped_box_is_reported_never_started(self, state):
        """The message names the start command rather than starting anything:
        the fake has no start_instances to call."""
        ec2 = _FakeEc2(state)
        with pytest.raises(
            BuildHostUnavailableError,
            match=rf"is {state} \(off-schedule\).*bin/environment.py start <env>",
        ):
            find_build_instance(ec2, INSTANCE)
        assert len(ec2.calls) == 1

    @pytest.mark.parametrize("state", [None, "terminated"])
    def test_a_missing_instance_names_the_config_key_and_tofu(self, state):
        with pytest.raises(BuildHostUnavailableError, match=r"(?s)gpu_instance_id.*tofu"):
            find_build_instance(_FakeEc2(state), INSTANCE)

    def test_other_client_errors_propagate(self):
        class _Denied:
            def describe_instances(self, **_kwargs):
                raise ClientError(
                    {"Error": {"Code": "UnauthorizedOperation", "Message": "no"}},
                    "DescribeInstances",
                )

        with pytest.raises(ClientError, match="UnauthorizedOperation"):
            find_build_instance(_Denied(), INSTANCE)


class _FakeSession:
    def __init__(self, recorder):
        self._recorder = recorder

    def terminate(self):
        self._recorder.terminated += 1

    def wait(self, timeout=None):
        self._recorder.waited += 1

    def kill(self):
        self._recorder.killed += 1


class _PopenRecorder:
    def __init__(self):
        self.calls = []
        self.terminated = 0
        self.waited = 0
        self.killed = 0

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        return _FakeSession(self)


class _RunRecorder:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=self.returncode, stdout="28.1.0\n", stderr="no daemon"
        )


@pytest.fixture
def plugin_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}")


class TestRemoteDocker:
    def test_refuses_without_the_session_manager_plugin(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        with (
            pytest.raises(RuntimeError, match="session-manager-plugin"),
            remote_docker("i-live", "us-west-2"),
        ):
            pass

    def test_opens_a_port_forward_and_yields_docker_host(self, monkeypatch, plugin_installed):
        popen = _PopenRecorder()
        run = _RunRecorder()
        monkeypatch.setattr(subprocess, "Popen", popen)
        monkeypatch.setattr(subprocess, "run", run)

        with remote_docker("i-live", "us-west-2") as env:
            assert env["DOCKER_HOST"].startswith("tcp://127.0.0.1:")
            port = env["DOCKER_HOST"].rsplit(":", 1)[1]

        [(argv, _kwargs)] = popen.calls
        assert argv[:3] == ["aws", "ssm", "start-session"]
        assert "--target" in argv and argv[argv.index("--target") + 1] == "i-live"
        assert argv[argv.index("--region") + 1] == "us-west-2"
        assert argv[argv.index("--document-name") + 1] == "AWS-StartPortForwardingSession"
        assert argv[-1] == f"portNumber=2375,localPortNumber={port}"
        # The readiness probe ran through the tunnel
        probe_cmd, probe_kwargs = run.calls[0]
        assert probe_cmd[:2] == ["docker", "version"]
        assert probe_kwargs["env"]["DOCKER_HOST"] == env["DOCKER_HOST"]
        # And the session was closed on exit
        assert popen.terminated == 1

    def test_a_tunnel_that_never_answers_is_an_error_and_still_closed(
        self, monkeypatch, plugin_installed
    ):
        popen = _PopenRecorder()
        monkeypatch.setattr(subprocess, "Popen", popen)
        monkeypatch.setattr(subprocess, "run", _RunRecorder(returncode=1))
        monkeypatch.setattr(build_host, "TUNNEL_READY_TIMEOUT_SECONDS", 0)
        monkeypatch.setattr(build_host.time, "sleep", lambda _s: None)

        with (
            pytest.raises(RuntimeError, match="did not come up"),
            remote_docker("i-live", "us-west-2"),
        ):
            pass
        assert popen.terminated == 1
