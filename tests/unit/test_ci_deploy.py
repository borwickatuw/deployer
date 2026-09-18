"""Tests for deployer.cli.ci_deploy module."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner

from deployer.cli import ci_deploy
from deployer.cli.ci_deploy import (
    load_resolved_config,
)


def make_resolved_config(**meta_overrides):
    """Create a minimal resolved config dict with _meta."""
    meta = {
        "environment": "myapp-staging",
        "environment_type": "staging",
        "resolved_at": "2026-02-17T12:00:00+00:00",
        "config_toml_hash": "sha256:abc123",
        "tofu_outputs_hash": "sha256:def456",
    }
    meta.update(meta_overrides)

    return {
        "_meta": meta,
        "infrastructure": {
            "cluster_name": "myapp-staging-cluster",
            "ecr_prefix": "myapp",
            "execution_role_arn": "arn:aws:iam::123:role/exec",
            "task_role_arn": "arn:aws:iam::123:role/task",
            "security_group_id": "sg-123",
            "private_subnet_ids": ["subnet-1"],
        },
    }


class TestLoadResolvedConfig:
    """Tests for load_resolved_config."""

    def test_loads_valid_config(self, tmp_path):
        """Should load and return env_config and meta separately."""
        config = make_resolved_config()
        config_file = tmp_path / "resolved.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        env_config, meta = load_resolved_config(str(config_file))

        assert meta["environment"] == "myapp-staging"
        assert meta["environment_type"] == "staging"
        assert "_meta" not in env_config
        assert env_config["infrastructure"]["cluster_name"] == "myapp-staging-cluster"

    def test_missing_file_raises(self, tmp_path):
        """Should raise FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_resolved_config(str(tmp_path / "missing.json"))

    def test_invalid_json_raises(self, tmp_path):
        """Should raise ValueError for invalid JSON."""
        config_file = tmp_path / "bad.json"
        config_file.write_text("not json", encoding="utf-8")

        with pytest.raises(ValueError, match="Invalid JSON"):
            load_resolved_config(str(config_file))

    def test_missing_meta_raises(self, tmp_path):
        """Should raise ValueError when _meta block is missing."""
        config_file = tmp_path / "no_meta.json"
        config_file.write_text(json.dumps({"infrastructure": {}}), encoding="utf-8")

        with pytest.raises(ValueError, match="missing _meta block"):
            load_resolved_config(str(config_file))

    def test_missing_required_meta_fields_raises(self, tmp_path):
        """Should raise ValueError when _meta is missing required fields."""
        config = {
            "_meta": {"environment": "test"},
            "infrastructure": {},
        }
        config_file = tmp_path / "partial_meta.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        with pytest.raises(ValueError, match="missing required fields"):
            load_resolved_config(str(config_file))

    def test_non_dict_raises(self, tmp_path):
        """Should raise ValueError for non-object JSON."""
        config_file = tmp_path / "array.json"
        config_file.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(ValueError, match="must be a JSON object"):
            load_resolved_config(str(config_file))

    def test_meta_is_stripped_from_env_config(self, tmp_path):
        """The _meta block should NOT appear in env_config."""
        config = make_resolved_config()
        config_file = tmp_path / "resolved.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        env_config, meta = load_resolved_config(str(config_file))

        # _meta should be stripped from env_config
        assert "_meta" not in env_config
        # meta should be a separate dict
        assert isinstance(meta, dict)
        assert "environment" in meta

    def test_preserves_all_config_sections(self, tmp_path):
        """Should preserve all config sections (not just infrastructure)."""
        config = make_resolved_config()
        config["database"] = {"host": "db.example.com", "port": 5432}
        config["cache"] = {"url": "redis://cache:6379"}

        config_file = tmp_path / "resolved.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")

        env_config, meta = load_resolved_config(str(config_file))

        assert env_config["database"]["host"] == "db.example.com"
        assert env_config["cache"]["url"] == "redis://cache:6379"


class TestResolveConfig:
    """Tests for bin/resolve-config.py functions."""

    def test_compute_hash(self):
        """Hash should be deterministic and prefixed."""
        from importlib.util import module_from_spec, spec_from_file_location
        from pathlib import Path

        bin_dir = Path(__file__).parents[2] / "bin"
        spec = spec_from_file_location("resolve_config", bin_dir / "resolve-config.py")
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)

        h1 = mod._compute_hash("test data")
        h2 = mod._compute_hash("test data")
        h3 = mod._compute_hash("different data")

        assert h1 == h2
        assert h1 != h3
        assert h1.startswith("sha256:")

    def test_build_meta(self):
        """Meta block should contain all required fields."""
        from importlib.util import module_from_spec, spec_from_file_location
        from pathlib import Path

        bin_dir = Path(__file__).parents[2] / "bin"
        spec = spec_from_file_location("resolve_config", bin_dir / "resolve-config.py")
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)

        meta = mod.build_meta(
            environment="myapp-staging",
            environment_type="staging",
            config_toml_content="[infrastructure]\ncluster = test",
            tofu_outputs_json='{"cluster_name": "test"}',
        )

        assert meta["environment"] == "myapp-staging"
        assert meta["environment_type"] == "staging"
        assert "resolved_at" in meta
        assert meta["config_toml_hash"].startswith("sha256:")
        assert meta["tofu_outputs_hash"].startswith("sha256:")


class TestConfigStalenessGate:
    """Characterization pins for ``main()``'s --max-config-age / --strict gate.

    The staleness block is the only place ``--strict`` changes behaviour, and
    the difference is a warning versus ``sys.exit(1)`` before the pipeline
    runs. These pin that boundary at the seam -- CLI in, exit code and
    message out -- so the block can move without the contract moving.
    """

    @staticmethod
    def _write_inputs(tmp_path, age_hours=None, resolved_at=None):
        """Write a deploy.toml and a resolved config aged ``age_hours`` back."""
        if resolved_at is None:
            resolved_at = (datetime.now(UTC) - timedelta(hours=age_hours)).isoformat()

        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text('[application]\nname = "myapp"\n', encoding="utf-8")

        config_file = tmp_path / "resolved.json"
        config_file.write_text(
            json.dumps(make_resolved_config(resolved_at=resolved_at)), encoding="utf-8"
        )

        return str(deploy_toml), str(config_file)

    @staticmethod
    def _invoke(monkeypatch, argv):
        """Run ``main`` with the deploy pipeline stubbed out to a clean exit."""
        calls = []

        def fake_pipeline(*args, **kwargs):
            calls.append((args, kwargs))
            return 0

        monkeypatch.setattr(ci_deploy, "run_deploy_pipeline", fake_pipeline)
        result = CliRunner().invoke(ci_deploy.main, argv)
        return result, calls

    def test_fresh_config_passes_the_gate_silently(self, tmp_path, monkeypatch):
        """A config inside the limit says nothing and reaches the pipeline."""
        toml_path, config_path = self._write_inputs(tmp_path, age_hours=1)

        result, calls = self._invoke(
            monkeypatch, [toml_path, config_path, "--max-config-age", "48"]
        )

        assert result.exit_code == 0
        assert "limit:" not in result.output
        assert len(calls) == 1

    def test_stale_config_warns_and_still_deploys(self, tmp_path, monkeypatch):
        """Without --strict, staleness is a warning: the pipeline still runs."""
        toml_path, config_path = self._write_inputs(tmp_path, age_hours=100)

        result, calls = self._invoke(
            monkeypatch, [toml_path, config_path, "--max-config-age", "48"]
        )

        assert result.exit_code == 0
        assert "hours old" in result.output
        assert "limit: 48.0 hours" in result.output
        assert len(calls) == 1

    def test_strict_turns_staleness_into_exit_1_before_the_pipeline(self, tmp_path, monkeypatch):
        """--strict exits 1 and the pipeline is never reached."""
        toml_path, config_path = self._write_inputs(tmp_path, age_hours=100)

        result, calls = self._invoke(
            monkeypatch, [toml_path, config_path, "--max-config-age", "48", "--strict"]
        )

        assert result.exit_code == 1
        assert "hours old" in result.output
        assert calls == []

    def test_strict_alone_does_not_gate_anything(self, tmp_path, monkeypatch):
        """--strict without --max-config-age leaves an ancient config alone."""
        toml_path, config_path = self._write_inputs(tmp_path, age_hours=100)

        result, calls = self._invoke(monkeypatch, [toml_path, config_path, "--strict"])

        assert result.exit_code == 0
        assert "limit:" not in result.output
        assert len(calls) == 1

    def test_unparseable_timestamp_warns_rather_than_failing(self, tmp_path, monkeypatch):
        """A resolved_at that is not a timestamp warns, even under --strict."""
        toml_path, config_path = self._write_inputs(tmp_path, resolved_at="not-a-timestamp")

        result, calls = self._invoke(
            monkeypatch, [toml_path, config_path, "--max-config-age", "48", "--strict"]
        )

        assert result.exit_code == 0
        assert "Could not parse resolved_at timestamp" in result.output
        assert len(calls) == 1
