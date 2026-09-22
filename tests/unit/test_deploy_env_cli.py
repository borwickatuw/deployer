"""CLI tests for ``deploy.py env``, the machine-readable environment dump.

A real ``Deployer`` is built against faked boto3 clients, so the document is
produced by the same ``environment_variables`` route the deploy log's
environment block reads. The environments directory, config loading and
profile selection are stubbed at the deploy.py module bindings; the stubbed
profile step prints to stdout, as the real one's helpers do, so the tests can
see that nothing but the document reaches stdout.
"""

import json
import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from deployer.deploy import deployer as deployer_mod
from deployer.deploy.deployer import Deployer
from deployer.deploy.task_definition import get_secrets

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("deploy_env_cli", bin_dir / "deploy.py")
deploy = module_from_spec(_spec)
_spec.loader.exec_module(deploy)

ENV = "myapp-staging"
ACCOUNT_ID = "123456789012"
REGION = "us-west-2"

ENV_CONFIG = {
    "environment": {"type": "staging"},
    "infrastructure": {"ecr_prefix": "myapp-staging"},
    "services": {"config": {}, "scaling": {}, "health_check": {}},
    "secrets": {"provider": "ssm", "path_prefix": "/myapp/staging"},
}

DEPLOY_TOML = """
[application]
name = "myapp"
source = "."

[services.web]
port = 8000

[environment]
LOG_LEVEL = "info"
MAX_WORKERS = 4
DEBUG = false
EMPTY = ""
QUOTED = "it's \\"here\\""
REGION_REF = "${aws_region}"
ACCOUNT_REF = "${account_id}"

[environment.staging]
LOG_LEVEL = "debug"

[secrets]
names = ["SECRET_KEY", "SIGNING_TOKEN"]
"""

EXPECTED = {
    "ACCOUNT_REF": ACCOUNT_ID,
    "DEBUG": "False",
    "EMPTY": "",
    "LOG_LEVEL": "debug",
    "MAX_WORKERS": "4",
    "QUOTED": 'it\'s "here"',
    "REGION_REF": REGION,
}

EXPECTED_DOTENV = (
    f"ACCOUNT_REF='{ACCOUNT_ID}'\n"
    "DEBUG='False'\n"
    "EMPTY=''\n"
    "LOG_LEVEL='debug'\n"
    "MAX_WORKERS='4'\n"
    "QUOTED='it'\\''s \"here\"'\n"
    f"REGION_REF='{REGION}'\n"
)


@pytest.fixture
def deploy_toml(tmp_path, monkeypatch):
    """Stub everything env reaches outside the process; return a deploy.toml path."""
    (tmp_path / ENV).mkdir()
    monkeypatch.setattr(deploy, "get_environments_dir", lambda: tmp_path)
    monkeypatch.setattr(deploy, "load_environment_config", lambda _path: ENV_CONFIG)
    monkeypatch.setattr(
        deploy, "configure_profile_or_exit", lambda op, env: print(f"profile for {op} {env}")
    )

    sts = SimpleNamespace(get_caller_identity=lambda: {"Account": ACCOUNT_ID})
    monkeypatch.setattr(
        deployer_mod.boto3, "client", lambda name: sts if name == "sts" else SimpleNamespace()
    )
    monkeypatch.setattr(deployer_mod.boto3, "Session", lambda: SimpleNamespace(region_name=REGION))

    return _write_toml(tmp_path, DEPLOY_TOML)


def _write_toml(tmp_path, text: str) -> Path:
    path = tmp_path / "deploy.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _invoke(deploy_toml: Path, *args: str):
    return CliRunner().invoke(deploy.cli, ["env", ENV, "--deploy-toml", str(deploy_toml), *args])


class TestEnvCommand:
    def test_default_format_is_dotenv_and_stdout_is_exactly_the_document(self, deploy_toml):
        result = _invoke(deploy_toml)

        assert result.exit_code == 0, result.output
        assert result.stdout == EXPECTED_DOTENV

    def test_json_format_stdout_is_exactly_the_document(self, deploy_toml):
        result = _invoke(deploy_toml, "--format", "json")

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == EXPECTED
        assert result.stdout == json.dumps(EXPECTED, indent=2, sort_keys=True) + "\n"

    def test_progress_goes_to_stderr(self, deploy_toml):
        """The link tip, profile step and config loading are all still shown."""
        result = _invoke(deploy_toml)

        assert "Tip: Run" in result.stderr
        assert f"profile for deploy {ENV}" in result.stderr
        assert "Loading deployment config" in result.stderr

    @pytest.mark.parametrize("output_format", ["dotenv", "json"])
    def test_secrets_do_not_reach_the_document(self, deploy_toml, output_format):
        """[secrets] names travel the task definition's secrets block, not env.

        The first assertion proves the fixture's secrets are live -- the same
        deploy.toml does produce them on the secrets route -- so their absence
        from the document is the routes being disjoint, not the fixture being
        inert.
        """
        ctx = Deployer(str(deploy_toml), "staging", ENV_CONFIG).ctx
        assert sorted(s["name"] for s in get_secrets(ctx, None)) == ["SECRET_KEY", "SIGNING_TOKEN"]

        result = _invoke(deploy_toml, "--format", output_format)

        assert result.exit_code == 0, result.output
        for leaked in ("SECRET_KEY", "SIGNING_TOKEN", "/myapp/staging", "arn:aws:ssm"):
            assert leaked not in result.stdout

    def test_there_is_no_option_to_include_secrets(self, deploy_toml):
        result = _invoke(deploy_toml, "--include-secrets")

        assert result.exit_code == 2
        assert result.stdout == ""

    def test_the_dump_agrees_with_the_deploy_log_block(self, deploy_toml, capsys):
        """Same route, same values -- the log block only renders "" as (unset)."""
        dumped = json.loads(_invoke(deploy_toml, "--format", "json").stdout)

        Deployer(str(deploy_toml), "staging", ENV_CONFIG).print_environment_config()
        lines = re.sub(r"\033\[[0-9;]*m", "", capsys.readouterr().out).splitlines()
        narrated = dict(line.strip().split("=", 1) for line in lines[1:] if line.strip())

        assert narrated == {k: v or "(unset)" for k, v in dumped.items()}

    def test_a_name_a_shell_cannot_assign_fails_dotenv_with_nothing_on_stdout(
        self, deploy_toml, tmp_path
    ):
        toml = _write_toml(tmp_path, DEPLOY_TOML.replace("LOG_LEVEL = ", "LOG-LEVEL = ", 1))

        result = _invoke(toml)

        assert result.exit_code == 1
        assert result.stdout == ""
        assert "LOG-LEVEL" in result.stderr

    def test_the_same_name_is_fine_as_json(self, deploy_toml, tmp_path):
        toml = _write_toml(tmp_path, DEPLOY_TOML.replace("LOG_LEVEL = ", "LOG-LEVEL = ", 1))

        result = _invoke(toml, "--format", "json")

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["LOG-LEVEL"] == "info"

    def test_a_deployer_config_error_exits_1_with_nothing_on_stdout(self, deploy_toml, monkeypatch):
        monkeypatch.setattr(
            deploy, "load_environment_config", lambda _path: ENV_CONFIG | {"infrastructure": {}}
        )

        result = _invoke(deploy_toml)

        assert result.exit_code == 1
        assert result.stdout == ""
        assert "ecr_prefix" in result.stderr
