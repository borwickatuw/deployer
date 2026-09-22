"""Characterization pins for bin/resolve-config.py (resolve, --output, --push-s3, --verify).

These are characterization pins, not endorsements. They record what
``bin/resolve-config.py`` does **today**; where the behaviour looks surprising
it is pinned anyway and called out in the test's docstring. Nothing here is a
fix.

``bin/resolve-config.py`` was at **34%** before this file:
``test_bin_error_boundaries.py`` covers only the missing-directory raise and
the resolution-failure exit, with ``get_all_tofu_outputs`` stubbed out.

What is pinned:

* Resolve mode -- the resolved JSON (placeholders filled, ``[aws]`` stripped,
  ``_meta`` with both hashes), and where it goes: stdout, ``--output``,
  ``--push-s3``, or both of the latter. Every exit-1 arm of the resolve.
* ``--push-s3`` -- the bucket-by-convention, key and content type, and a
  refused upload turned into exit 1.
* ``--verify`` -- both ways of naming the file, fresh, stale by tofu outputs,
  stale by config.toml, and the two arms that do not exit cleanly.

Stubbing is at the outermost boundary, so the real config loader, placeholder
resolver and profile selection all run:

* ``tofu output -json`` is the one stub, replaced at ``run_command`` inside
  ``deployer.core.config`` -- the subprocess, not the loader.
* STS (profile validation, the bucket's account id) and S3 are moto.
* The filesystem is real: ``DEPLOYER_ENVIRONMENTS_DIR`` points at ``tmp_path``
  and ``AWS_CONFIG_FILE`` at a file defining the profiles the CLI selects.
"""

import hashlib
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import boto3
import pytest
from click.testing import CliRunner

from deployer.core import config as core_config

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("resolve_config_cli", bin_dir / "resolve-config.py")
resolve_config = module_from_spec(_spec)
_spec.loader.exec_module(resolve_config)

ENV = "myapp-staging"
PROFILE = "myapp-infra"
ACCOUNT = "123456789012"  # moto's default account
BUCKET = f"deployer-resolved-configs-{ACCOUNT}"
S3_URI = f"s3://{BUCKET}/{ENV}/config.json"

CONFIG_TOML = f"""\
[aws]
infra_profile = "{PROFILE}"

[environment]
type = "staging"

[infrastructure]
cluster_name = "${{tofu:cluster_name}}"
description = "Café ${{tofu:cluster_name}}"
"""

TOFU_OUTPUTS = {"cluster_name": "myapp-staging-cluster"}


def tofu_json(outputs: dict) -> str:
    """Render outputs the way `tofu output -json` prints them."""
    return json.dumps(
        {k: {"value": v, "type": "string", "sensitive": False} for k, v in outputs.items()}
    )


def sha256(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


class FakeTofu:
    """Stands in for run_command inside deployer.core.config."""

    def __init__(self):
        self.calls: list[tuple[list[str], str | None]] = []
        self.success = True
        self.outputs: dict = dict(TOFU_OUTPUTS)

    def __call__(self, cmd: list[str], cwd: str | None = None) -> tuple[bool, str]:
        self.calls.append((cmd, cwd))
        if not self.success:
            return False, "Error: Backend initialization required"
        return True, tofu_json(self.outputs)


@pytest.fixture
def tofu(monkeypatch) -> FakeTofu:
    fake = FakeTofu()
    monkeypatch.setattr(core_config, "run_command", fake)
    return fake


@pytest.fixture
def env_path(tmp_path, monkeypatch, mocked_aws, tofu) -> Path:
    """A deployed environment with a config.toml, under moto and fake tofu."""
    envs = tmp_path / "environments"
    path = envs / ENV
    path.mkdir(parents=True)
    (path / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")
    monkeypatch.setenv("DEPLOYER_ENVIRONMENTS_DIR", str(envs))

    # A session built with an explicit profile skips the environment
    # credentials, so each profile carries moto's dummy keys itself.
    profile_body = (
        "region = us-west-2\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    aws_config = tmp_path / "aws-config"
    aws_config.write_text(
        f"[profile {PROFILE}]\n{profile_body}[profile deployer-infra]\n{profile_body}",
        encoding="utf-8",
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(aws_config))
    return path


@pytest.fixture
def bucket(env_path) -> str:
    boto3.client("s3", region_name="us-west-2").create_bucket(
        Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-west-2"}
    )
    return BUCKET


def run(*args: str):
    return CliRunner().invoke(resolve_config.cli, [ENV, *args])


def json_from_stdout(output: str) -> dict:
    """The JSON document printed after the log lines."""
    return json.loads(output[output.index("\n{\n") + 1 :])


# =============================================================================
# Resolve mode
# =============================================================================


class TestResolve:
    def test_prints_the_resolved_config_after_the_log_lines(self, env_path, tofu):
        """stdout carries the profile and "Resolving" log lines ahead of the
        JSON, so ``resolve-config.py ENV > file.json`` is not valid JSON;
        --output is the way to get a clean file."""
        result = run()

        assert result.exit_code == 0, result.output
        assert result.output.index(f"Using AWS profile: {PROFILE}") < result.output.index("{")
        assert f"Resolving config for {ENV}..." in result.output

        resolved = json_from_stdout(result.output)
        assert "aws" not in resolved
        assert resolved["environment"] == {"type": "staging"}
        assert resolved["infrastructure"] == {
            "cluster_name": "myapp-staging-cluster",
            "description": "Café myapp-staging-cluster",
        }
        meta = resolved["_meta"]
        assert set(meta) == {
            "environment",
            "environment_type",
            "resolved_at",
            "config_toml_hash",
            "tofu_outputs_hash",
        }
        assert meta["environment"] == ENV
        assert meta["environment_type"] == "staging"
        assert meta["resolved_at"].endswith("+00:00")
        assert meta["config_toml_hash"] == sha256(CONFIG_TOML)
        assert meta["tofu_outputs_hash"] == sha256(json.dumps(TOFU_OUTPUTS, sort_keys=True))

    def test_tofu_runs_twice_in_the_environment_directory(self, env_path, tofu):
        """Once for the hash, once inside load_environment_config."""
        assert run().exit_code == 0

        assert tofu.calls == [(["tofu", "output", "-json"], str(env_path))] * 2

    def test_output_writes_the_file_and_prints_a_summary_not_the_json(
        self, env_path, tofu, tmp_path
    ):
        out_file = tmp_path / "resolved.json"

        result = run("--output", str(out_file))

        assert result.exit_code == 0, result.output
        text = out_file.read_text(encoding="utf-8")
        assert text.endswith("}\n")
        assert "Café" in text  # ensure_ascii=False: readable, not \u-escaped
        resolved = json.loads(text)
        assert f"Resolved config written to {out_file}" in result.output
        assert f"  Environment: {ENV}" in result.output
        assert "  Type:        staging" in result.output
        assert f"  Resolved at: {resolved['_meta']['resolved_at']}" in result.output
        assert '"_meta"' not in result.output

    def test_push_s3_uploads_to_the_account_bucket_and_prints_no_json(self, bucket):
        result = run("--push-s3")

        assert result.exit_code == 0, result.output
        assert f"Resolved config pushed to {S3_URI}" in result.output
        assert '"_meta"' not in result.output
        obj = boto3.client("s3", region_name="us-west-2").get_object(
            Bucket=BUCKET, Key=f"{ENV}/config.json"
        )
        assert obj["ContentType"] == "application/json"
        body = obj["Body"].read().decode("utf-8")
        assert json.loads(body)["_meta"]["environment"] == ENV

    def test_output_and_push_s3_carry_the_same_document(self, bucket, tmp_path):
        """The file gets a trailing newline the S3 object does not."""
        out_file = tmp_path / "resolved.json"

        result = run("--output", str(out_file), "--push-s3")

        assert result.exit_code == 0, result.output
        body = (
            boto3.client("s3", region_name="us-west-2")
            .get_object(Bucket=BUCKET, Key=f"{ENV}/config.json")["Body"]
            .read()
            .decode("utf-8")
        )
        assert out_file.read_text(encoding="utf-8") == body + "\n"

    def test_a_refused_upload_exits_1(self, env_path):
        """No bucket: the ClientError becomes one stderr line, not a traceback."""
        result = run("--push-s3")

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert f"Failed to push to {S3_URI}: NoSuchBucket - " in result.output

    def test_a_missing_config_toml_exits_1(self, env_path, tofu):
        (env_path / "config.toml").unlink()

        result = run()

        assert result.exit_code == 1
        assert f"Config file not found: {env_path / 'config.toml'}" in result.output
        assert tofu.calls == []

    def test_a_missing_environment_directory_exits_1(self, env_path, tofu):
        """The profile falls back to the default before the directory check."""
        result = CliRunner().invoke(resolve_config.cli, ["otherapp-staging"])

        assert result.exit_code == 1
        assert "Using AWS profile: deployer-infra (default)" in result.output
        assert "Environment directory not found" in result.output
        assert tofu.calls == []

    def test_a_tofu_failure_exits_1(self, env_path, tofu):
        tofu.success = False

        result = run()

        assert result.exit_code == 1
        assert "Failed to resolve config: Failed to fetch tofu outputs" in result.output

    def test_a_missing_environment_type_exits_1(self, env_path):
        (env_path / "config.toml").write_text(
            CONFIG_TOML.replace('[environment]\ntype = "staging"\n', ""), encoding="utf-8"
        )

        result = run()

        assert result.exit_code == 1
        assert "Missing [environment].type in config.toml." in result.output

    def test_an_undefined_profile_exits_1_before_tofu_runs(self, env_path, tofu):
        (env_path / "config.toml").write_text(
            CONFIG_TOML.replace(PROFILE, "undefined-profile"), encoding="utf-8"
        )

        result = run()

        assert result.exit_code == 1
        assert "AWS profile 'undefined-profile' not found." in result.output
        assert tofu.calls == []


# =============================================================================
# --verify
# =============================================================================


class TestVerify:
    @pytest.fixture
    def resolved_file(self, env_path, tmp_path) -> Path:
        out_file = tmp_path / "resolved.json"
        assert run("--output", str(out_file)).exit_code == 0
        return out_file

    def test_a_fresh_config_exits_0(self, resolved_file):
        resolved_at = json.loads(resolved_file.read_text(encoding="utf-8"))["_meta"]["resolved_at"]

        result = run("--verify", "--verify-file", str(resolved_file))

        assert result.exit_code == 0, result.output
        assert f"Verifying resolved config for {ENV}..." in result.output
        assert f"Config is fresh (resolved at {resolved_at})" in result.output

    def test_output_names_the_file_when_verify_file_is_absent(self, resolved_file):
        result = run("--verify", "--output", str(resolved_file))

        assert result.exit_code == 0, result.output
        assert "Config is fresh" in result.output

    def test_changed_tofu_outputs_are_stale(self, resolved_file, tofu):
        """The re-run hint is not an f-string: the braces print literally."""
        tofu.outputs["cluster_name"] = "myapp-staging-cluster-2"

        result = run("--verify", "--verify-file", str(resolved_file))

        assert result.exit_code == 1
        assert "Config is STALE" in result.output
        assert "Re-run: python resolve-config.py {environment} --output {file}" in result.output

    def test_a_changed_config_toml_is_stale(self, resolved_file, env_path, tofu):
        (env_path / "config.toml").write_text(CONFIG_TOML + "\n# edited\n", encoding="utf-8")

        result = run("--verify", "--verify-file", str(resolved_file))

        assert result.exit_code == 1
        assert "Config is STALE" in result.output
        assert len(tofu.calls) == 3  # two to resolve, one to verify

    def test_a_file_with_no_meta_verifies_as_fresh(self, env_path, tofu, tmp_path):
        """With neither hash present nothing is compared, so any JSON object
        -- even one that was never resolved -- passes."""
        bare = tmp_path / "bare.json"
        bare.write_text("{}", encoding="utf-8")

        result = run("--verify", "--verify-file", str(bare))

        assert result.exit_code == 0, result.output
        assert "Config is fresh (resolved at unknown)" in result.output
        assert tofu.calls == []

    def test_a_tofu_failure_during_verify_is_an_uncaught_traceback(self, resolved_file, tofu):
        """Unlike resolve mode, verify has no handler around the tofu call."""
        tofu.success = False

        result = run("--verify", "--verify-file", str(resolved_file))

        assert result.exit_code == 1
        assert isinstance(result.exception, RuntimeError)
        assert "Failed to fetch tofu outputs" in str(result.exception)

    def test_verify_needs_a_file(self, env_path):
        result = run("--verify")

        assert result.exit_code == 1
        assert "--verify requires --verify-file or --output" in result.output

    def test_a_missing_verify_file_exits_1(self, env_path, tmp_path):
        missing = tmp_path / "nope.json"

        result = run("--verify", "--verify-file", str(missing))

        assert result.exit_code == 1
        assert f"Resolved config not found: {missing}" in result.output
