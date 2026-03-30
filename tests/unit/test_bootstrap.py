"""Tests for bootstrap initialization."""

import os
from unittest.mock import patch

import pytest

from deployer.init.bootstrap import (
    bootstrap_dir_exists,
    detect_aws_account_id,
    format_hcl_list,
    format_hcl_map,
    generate_bootstrap,
    uncomment_backend_block,
)


class TestDetectAwsAccountId:
    """Tests for detect_aws_account_id."""

    def test_successful_detection(self):
        with patch("deployer.init.bootstrap.run_command") as mock:
            mock.return_value = (True, '{"Account": "123456789012"}')
            assert detect_aws_account_id() == "123456789012"

    def test_failure_returns_none(self):
        with patch("deployer.init.bootstrap.run_command") as mock:
            mock.return_value = (False, "error")
            assert detect_aws_account_id() is None

    def test_invalid_json_returns_none(self):
        with patch("deployer.init.bootstrap.run_command") as mock:
            mock.return_value = (True, "not json")
            assert detect_aws_account_id() is None


class TestFormatHclList:
    """Tests for format_hcl_list."""

    def test_single_item(self):
        assert format_hcl_list(["myapp"]) == '["myapp"]'

    def test_multiple_items(self):
        assert format_hcl_list(["a", "b"]) == '["a", "b"]'

    def test_empty_list(self):
        assert format_hcl_list([]) == "[]"


class TestFormatHclMap:
    """Tests for format_hcl_map."""

    def test_single_entry(self):
        result = format_hcl_map({"myapp": "myapp.example.com"})
        assert 'myapp = "myapp.example.com"' in result
        assert result.startswith("{")
        assert result.endswith("}")

    def test_multiple_entries(self):
        result = format_hcl_map({"a": "a.com", "b": "b.com"})
        assert 'a = "a.com"' in result
        assert 'b = "b.com"' in result

    def test_empty_map(self):
        assert format_hcl_map({}) == "{}"


class TestUncommentBackendBlock:
    """Tests for uncomment_backend_block."""

    def test_uncomments_backend(self):
        content = """\
terraform {
  required_version = ">= 1.6.0"

  # BOOTSTRAP-BACKEND-START
  # backend "s3" {
  #   bucket  = "my-bucket"
  #   key     = "bootstrap/terraform.tfstate"
  #   region  = "us-west-2"
  #   encrypt = true
  # }
  # BOOTSTRAP-BACKEND-END
}
"""
        result = uncomment_backend_block(content)
        assert 'backend "s3" {' in result
        assert 'bucket  = "my-bucket"' in result
        assert "BOOTSTRAP-BACKEND-START" not in result
        assert "BOOTSTRAP-BACKEND-END" not in result
        # Non-backend content preserved
        assert "required_version" in result

    def test_raises_on_missing_markers(self):
        with pytest.raises(ValueError, match="Could not find"):
            uncomment_backend_block("terraform { }")

    def test_raises_on_partial_markers(self):
        content = "# BOOTSTRAP-BACKEND-START\nsome content"
        with pytest.raises(ValueError, match="Could not find"):
            uncomment_backend_block(content)


class TestGenerateBootstrap:
    """Tests for generate_bootstrap."""

    def test_basic_generation(self):
        files = generate_bootstrap(
            account_id="123456789012",
            region="us-west-2",
            env_label="staging",
            project_prefixes=["myapp"],
            trusted_user_arns=["arn:aws:iam::123456789012:user/deployer"],
        )
        assert "main.tf" in files
        assert "terraform.tfvars" in files
        assert "import-existing.sh" in files
        # Cognito files should not be present
        assert "cognito.auto.tfvars" not in files
        assert "main-cognito.tf" not in files

    def test_placeholder_substitution(self):
        files = generate_bootstrap(
            account_id="123456789012",
            region="us-east-1",
            env_label="production",
            project_prefixes=["myapp", "otherapp"],
            trusted_user_arns=["arn:aws:iam::123456789012:user/deployer"],
        )
        # Check main.tf
        assert "123456789012" in files["main.tf"]
        assert "us-east-1" in files["main.tf"]
        assert "bootstrap-production" in files["main.tf"]
        # Check tfvars
        assert '"myapp"' in files["terraform.tfvars"]
        assert '"otherapp"' in files["terraform.tfvars"]

    def test_backend_is_commented_out(self):
        files = generate_bootstrap(
            account_id="123456789012",
            region="us-west-2",
            env_label="staging",
            project_prefixes=["myapp"],
            trusted_user_arns=["arn:aws:iam::123456789012:user/deployer"],
        )
        main_tf = files["main.tf"]
        assert "BOOTSTRAP-BACKEND-START" in main_tf
        # The backend line should be commented
        assert '# backend "s3"' in main_tf

    def test_with_cognito(self):
        files = generate_bootstrap(
            account_id="123456789012",
            region="us-west-2",
            env_label="staging",
            project_prefixes=["myapp"],
            trusted_user_arns=["arn:aws:iam::123456789012:user/deployer"],
            include_cognito=True,
            cognito_app_domains={"myapp": "myapp-staging.example.com"},
        )
        # Cognito module should be in main.tf
        assert "cognito_shared" in files["main.tf"]
        assert "cognito_user_pool_id" in files["main.tf"]
        # Cognito tfvars should exist
        assert "cognito.auto.tfvars" in files
        assert "myapp-staging.example.com" in files["cognito.auto.tfvars"]

    def test_without_cognito_no_cognito_in_main(self):
        files = generate_bootstrap(
            account_id="123456789012",
            region="us-west-2",
            env_label="staging",
            project_prefixes=["myapp"],
            trusted_user_arns=["arn:aws:iam::123456789012:user/deployer"],
            include_cognito=False,
        )
        assert "cognito_shared" not in files["main.tf"]


class TestBootstrapDirExists:
    """Tests for bootstrap_dir_exists."""

    def test_returns_name_when_exists(self, tmp_path):
        (tmp_path / "bootstrap-staging").mkdir()
        with patch.dict(os.environ, {"DEPLOYER_ENVIRONMENTS_DIR": str(tmp_path)}):
            result = bootstrap_dir_exists()
            assert result == "bootstrap-staging"

    def test_returns_none_when_missing(self, tmp_path):
        (tmp_path / "myapp-staging").mkdir()
        with patch.dict(os.environ, {"DEPLOYER_ENVIRONMENTS_DIR": str(tmp_path)}):
            result = bootstrap_dir_exists()
            assert result is None

    def test_returns_none_when_dir_missing(self, tmp_path):
        with patch.dict(os.environ, {"DEPLOYER_ENVIRONMENTS_DIR": str(tmp_path / "nonexistent")}):
            result = bootstrap_dir_exists()
            assert result is None

    def test_returns_none_when_env_var_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove DEPLOYER_ENVIRONMENTS_DIR if set
            os.environ.pop("DEPLOYER_ENVIRONMENTS_DIR", None)
            result = bootstrap_dir_exists()
            assert result is None
