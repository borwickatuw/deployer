"""Tests for deployer.deploy.build_host: finding the GPU build host and the
SSM tunnel to its Docker daemon.

Stubbing is at the outermost boundary: a fake boto3 Auto Scaling client,
and ``subprocess.run``/``subprocess.Popen``/``shutil.which`` at the stdlib.
"""

import shutil
import subprocess

import pytest

from deployer.deploy import build_host
from deployer.deploy.build_host import (
    BuildHostUnavailableError,
    find_build_instance,
    remote_docker,
)


class _FakeAsg:
    def __init__(self, groups):
        self._groups = groups
        self.calls = []

    def describe_auto_scaling_groups(self, **kwargs):
        self.calls.append(kwargs)
        return {"AutoScalingGroups": self._groups}


def _group(*instances):
    return {"AutoScalingGroupName": "app-staging-gpu", "Instances": list(instances)}


def _instance(instance_id, state="InService", health="Healthy"):
    return {"InstanceId": instance_id, "LifecycleState": state, "HealthStatus": health}


class TestFindBuildInstance:
    def test_returns_the_in_service_healthy_instance(self):
        asg = _FakeAsg([_group(_instance("i-warm", state="Warmed:Stopped"), _instance("i-live"))])
        assert find_build_instance(asg, "app-staging-gpu") == "i-live"
        assert asg.calls == [{"AutoScalingGroupNames": ["app-staging-gpu"]}]

    def test_a_missing_group_is_reported(self):
        with pytest.raises(BuildHostUnavailableError, match="not found"):
            find_build_instance(_FakeAsg([]), "app-staging-gpu")

    def test_a_stopped_box_is_reported_never_started(self):
        """A warm-pooled (stopped) instance is not a build host; the message
        names the start command rather than starting anything."""
        asg = _FakeAsg([_group(_instance("i-warm", state="Warmed:Stopped"))])
        with pytest.raises(BuildHostUnavailableError, match="environment.py start"):
            find_build_instance(asg, "app-staging-gpu")
        assert len(asg.calls) == 1

    def test_an_unhealthy_instance_does_not_count(self):
        asg = _FakeAsg([_group(_instance("i-sick", health="Unhealthy"))])
        with pytest.raises(BuildHostUnavailableError):
            find_build_instance(asg, "app-staging-gpu")


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
