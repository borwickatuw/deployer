"""Tests for deployer.init.setup_profiles — AWS CLI profile generation.

TestCmdSetupProfiles characterizes cmd_setup_profiles() end to end — return
codes, which stream each message goes to, whether ~/.aws/config is written and
with what separator, and the text and order of the next-steps run — so that
adopting advice_block() for that run can be shown to preserve it.

Nothing reaches the real home directory or the real AWS CLI: HOME is redirected
to tmp_path, prompt_account_id_and_region() is stubbed at the module boundary,
and click.prompt/click.confirm are fed through a queue the way
tests/unit/test_init_cli.py does.

Blank-line placement is deliberately not pinned: _lines() drops blank lines, so
a change of framing in the next-steps run is not a test failure. Step text and
order are pinned and must not change.
"""

from pathlib import Path

import pytest

from deployer.init import setup_profiles
from deployer.init.setup_profiles import (
    _find_existing_profiles,
    cmd_setup_profiles,
    generate_profile_config,
)

ACCOUNT_ID = "123456789012"
REGION = "us-west-2"


def _lines(captured):
    """Return a captured stream's non-blank lines."""
    return [line for line in captured.splitlines() if line.strip()]


@pytest.fixture
def aws_config(tmp_path, monkeypatch):
    """Redirect Path.home() at tmp_path and return the ~/.aws/config path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Path.home() == tmp_path
    return tmp_path / ".aws" / "config"


@pytest.fixture
def setup_stubs(monkeypatch):
    """Stub the account/region prompt and answer click's prompts from queues."""
    monkeypatch.setattr(
        setup_profiles, "prompt_account_id_and_region", lambda: (ACCOUNT_ID, REGION)
    )
    monkeypatch.setattr(setup_profiles.click, "prompt", lambda *_a, **_kw: "deployer")
    monkeypatch.setattr(setup_profiles.click, "confirm", lambda *_a, **_kw: True)


class TestGenerateProfileConfig:
    def test_generates_four_profiles(self):
        config = generate_profile_config("123456789012", "us-west-2", "deployer")
        assert "[profile deployer]" in config
        assert "[profile deployer-app]" in config
        assert "[profile deployer-infra]" in config
        assert "[profile deployer-cognito]" in config

    def test_includes_role_arns(self):
        config = generate_profile_config("123456789012", "us-west-2", "deployer")
        assert "arn:aws:iam::123456789012:role/deployer-app-deploy" in config
        assert "arn:aws:iam::123456789012:role/deployer-infra-admin" in config
        assert "arn:aws:iam::123456789012:role/deployer-cognito-admin" in config

    def test_uses_source_profile(self):
        config = generate_profile_config("123456789012", "us-west-2", "myprofile")
        assert "source_profile = myprofile" in config
        assert "[profile myprofile]" in config

    def test_uses_region(self):
        config = generate_profile_config("123456789012", "eu-west-1", "deployer")
        assert "region = eu-west-1" in config
        assert "region = us-west-2" not in config


class TestFindExistingProfiles:
    def test_no_config_file(self, tmp_path):
        config_path = tmp_path / "config"
        assert _find_existing_profiles(config_path) == []

    def test_empty_config(self, tmp_path):
        config_path = tmp_path / "config"
        config_path.write_text("")
        assert _find_existing_profiles(config_path) == []

    def test_finds_existing_profiles(self, tmp_path):
        config_path = tmp_path / "config"
        config_path.write_text(
            "[profile deployer-app]\n"
            "role_arn = arn:aws:iam::123:role/deployer-app-deploy\n"
            "\n"
            "[profile deployer-infra]\n"
            "role_arn = arn:aws:iam::123:role/deployer-infra-admin\n"
        )
        found = _find_existing_profiles(config_path)
        assert "deployer-app" in found
        assert "deployer-infra" in found
        assert "deployer-cognito" not in found

    def test_ignores_non_deployer_profiles(self, tmp_path):
        config_path = tmp_path / "config"
        config_path.write_text("[profile default]\nregion = us-west-2\n")
        assert _find_existing_profiles(config_path) == []


class TestCmdSetupProfiles:
    """Characterization tests for cmd_setup_profiles()."""

    def test_existing_profiles_warn_on_stderr_and_write_nothing(
        self, aws_config, setup_stubs, capsys
    ):
        """An existing deployer profile is a no-write warning, not an error."""
        aws_config.parent.mkdir(parents=True)
        aws_config.write_text("[profile deployer-app]\nregion = us-west-2\n")

        assert cmd_setup_profiles(dry_run=False) == 0

        captured = capsys.readouterr()
        assert _lines(captured.err) == [
            f"Warning: These profiles already exist in {aws_config}:",
            "  deployer-app",
        ]
        assert "Generated config (not written):" in captured.out
        assert "[profile deployer-cognito]" in captured.out
        assert aws_config.read_text() == "[profile deployer-app]\nregion = us-west-2\n"

    def test_dry_run_previews_without_writing(self, aws_config, setup_stubs, capsys):
        """Dry run names the destination and prints the config it would append."""
        assert cmd_setup_profiles(dry_run=True) == 0

        out = _lines(capsys.readouterr().out)
        assert out[0] == f"Would append to {aws_config}:"
        assert "[profile deployer-app]" in out
        assert not aws_config.exists()

    def test_declining_the_prompt_writes_nothing(
        self, aws_config, setup_stubs, monkeypatch, capsys
    ):
        """Answering no to the confirm leaves the file alone and says so."""
        monkeypatch.setattr(setup_profiles.click, "confirm", lambda *_a, **_kw: False)

        assert cmd_setup_profiles(dry_run=False) == 0

        out = _lines(capsys.readouterr().out)
        assert out[0] == f"Will append to {aws_config}:"
        assert out[-1] == "Not written. Copy the text above into your config manually."
        assert not aws_config.exists()

    def test_creates_the_aws_directory_and_writes(self, aws_config, setup_stubs, capsys):
        """A missing ~/.aws is created, and the config is written without a leading blank."""
        assert cmd_setup_profiles(dry_run=False) == 0

        written = aws_config.read_text()
        assert written.startswith("[profile deployer]\n")
        assert f"role_arn = arn:aws:iam::{ACCOUNT_ID}:role/deployer-app-deploy" in written
        assert f"Profiles written to {aws_config}" in capsys.readouterr().out

    def test_appends_a_separator_to_a_non_empty_file(self, aws_config, setup_stubs):
        """An existing unrelated config keeps its content and gains a blank separator."""
        aws_config.parent.mkdir(parents=True)
        aws_config.write_text("[profile default]\nregion = us-east-1\n")

        assert cmd_setup_profiles(dry_run=False) == 0

        assert aws_config.read_text().startswith(
            "[profile default]\nregion = us-east-1\n\n[profile deployer]\n"
        )

    def test_next_steps_run(self, aws_config, setup_stubs, capsys):
        """The credentials run is printed verbatim, in order, after the write."""
        assert cmd_setup_profiles(dry_run=False) == 0

        assert _lines(capsys.readouterr().out)[-5:] == [
            f"Profiles written to {aws_config}",
            "Next: add credentials to ~/.aws/credentials:",
            "  [deployer]",
            "  aws_access_key_id = YOUR_ACCESS_KEY",
            "  aws_secret_access_key = YOUR_SECRET_KEY",
        ]

    def test_next_steps_names_the_chosen_source_profile(
        self, aws_config, setup_stubs, monkeypatch, capsys
    ):
        """The credentials stanza echoes the source profile the operator picked."""
        monkeypatch.setattr(setup_profiles.click, "prompt", lambda *_a, **_kw: "  work  ")

        assert cmd_setup_profiles(dry_run=False) == 0

        out = _lines(capsys.readouterr().out)
        assert out[-3:] == [
            "  [work]",
            "  aws_access_key_id = YOUR_ACCESS_KEY",
            "  aws_secret_access_key = YOUR_SECRET_KEY",
        ]
        assert "[profile work]" in aws_config.read_text()
