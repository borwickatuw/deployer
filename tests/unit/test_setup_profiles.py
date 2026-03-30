"""Tests for deployer.init.setup_profiles — AWS CLI profile generation."""

from pathlib import Path

from deployer.init.setup_profiles import (
    _find_existing_profiles,
    generate_profile_config,
)


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
        config_path.write_text(
            "[profile default]\nregion = us-west-2\n"
        )
        assert _find_existing_profiles(config_path) == []
