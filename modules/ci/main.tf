# ------------------------------------------------------------------------------
# CI Module (Shared Infrastructure)
#
# Creates account-wide CI/CD infrastructure for GitHub Actions deployments:
# - GitHub OIDC identity provider (created once per account)
# - S3 bucket for resolved config storage (versioned, encrypted)
#
# Per-project IAM roles are created separately using modules/ci-role,
# instantiated in each environment's tofu config.
#
# Usage:
#   module "ci" {
#     source = "../../deployer/modules/ci"
#   }
# ------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# ------------------------------------------------------------------------------
# GitHub OIDC Identity Provider
#
# Account-wide resource — allows GitHub Actions to assume IAM roles via OIDC.
# The thumbprint is GitHub's OIDC certificate thumbprint. AWS verifies it
# automatically for actions.githubusercontent.com since July 2023, but the
# field is still required by the API.
# ------------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  # AWS verifies GitHub's OIDC thumbprint automatically, but the field is
  # required. This is the well-known thumbprint for GitHub Actions.
  thumbprint_list = ["ffffffffffffffffffffffffffffffffffffffff"]
}

# ------------------------------------------------------------------------------
# Resolved Configs S3 Bucket
#
# Stores pre-resolved config JSON files for CI/CD deployments.
# Structure: {environment}/config.json (e.g., myapp-staging/config.json)
# ------------------------------------------------------------------------------

resource "aws_s3_bucket" "resolved_configs" {
  bucket = "deployer-resolved-configs-${data.aws_caller_identity.current.account_id}"
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_versioning" "resolved_configs" {
  bucket = aws_s3_bucket.resolved_configs.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "resolved_configs" {
  bucket = aws_s3_bucket.resolved_configs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "resolved_configs" {
  bucket                  = aws_s3_bucket.resolved_configs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
