# ------------------------------------------------------------------------------
# CI Module
#
# Creates CI/CD infrastructure for GitHub Actions deployments:
# - GitHub OIDC identity provider (account-wide, created once)
# - S3 bucket for resolved config storage (versioned, encrypted)
# - Per-project deployer-ci-{project} IAM roles with OIDC trust
#
# Usage:
#   module "ci" {
#     source = "../../deployer/modules/ci"
#
#     region          = var.region
#     github_ci_repos = var.github_ci_repos
#   }
#
# The module is opt-in: if github_ci_repos is empty, only the OIDC provider
# and S3 bucket are created (no IAM roles).
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

# ------------------------------------------------------------------------------
# Per-Project CI Deploy IAM Roles
#
# Each project gets a deployer-ci-{project} role that can only access
# that project's resources. The trust policy allows GitHub Actions from
# the specified repo to assume the role via OIDC.
# ------------------------------------------------------------------------------

# Trust policy: allow GitHub Actions from the specified repo + environments
data "aws_iam_policy_document" "ci_trust" {
  for_each = var.github_ci_repos

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        for env in var.github_oidc_environments :
        "repo:${each.value}:environment:${env}"
      ]
    }
  }
}

resource "aws_iam_role" "ci_deploy" {
  for_each = var.github_ci_repos

  name               = "deployer-ci-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.ci_trust[each.key].json
}

# Permissions policy: scoped to one project prefix
data "aws_iam_policy_document" "ci_deploy" {
  for_each = var.github_ci_repos

  # S3 - Read resolved configs (scoped to this project's keys)
  statement {
    sid    = "S3ResolvedConfigs"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = [
      "${aws_s3_bucket.resolved_configs.arn}/${each.key}-*/config.json",
    ]
  }

  statement {
    sid       = "S3ListBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.resolved_configs.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${each.key}-*/*"]
    }
  }

  # ECS - Read access (global, needed for describe/list operations)
  statement {
    sid    = "ECSRead"
    effect = "Allow"
    actions = [
      "ecs:Describe*",
      "ecs:List*",
    ]
    resources = ["*"]
  }

  # ECS - Register task definitions
  statement {
    sid       = "ECSTaskDefinition"
    effect    = "Allow"
    actions   = ["ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  # ECS - Service operations (scoped to this project)
  statement {
    sid    = "ECSService"
    effect = "Allow"
    actions = [
      "ecs:CreateService",
      "ecs:UpdateService",
      "ecs:RunTask",
    ]
    resources = [
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:service/${each.key}-*/*",
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${each.key}-*:*",
    ]
  }

  # ECR - Authorization
  statement {
    sid       = "ECRAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # ECR - Image operations (scoped to this project)
  statement {
    sid    = "ECRImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [
      "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${each.key}-*",
    ]
  }

  # CloudWatch Logs - Read ECS logs (scoped to this project)
  statement {
    sid    = "CloudWatchLogsRead"
    effect = "Allow"
    actions = [
      "logs:DescribeLogStreams",
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${each.key}-*:*",
    ]
  }

  # ELB - Read access for health checks
  statement {
    sid       = "ELBHealthCheck"
    effect    = "Allow"
    actions   = ["elasticloadbalancing:Describe*"]
    resources = ["*"]
  }

  # RDS - Read access for status checks
  statement {
    sid       = "RDSStatus"
    effect    = "Allow"
    actions   = ["rds:DescribeDBInstances"]
    resources = ["*"]
  }

  # SSM - Describe parameters (needed for secrets existence check)
  statement {
    sid       = "SSMDescribe"
    effect    = "Allow"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }

  # SSM - Read/write migrations hash (scoped to this project)
  statement {
    sid     = "SSMMigrationsHash"
    effect  = "Allow"
    actions = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = [
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${each.key}/*/last-migrations-hash",
    ]
  }

  # STS - Get caller identity
  statement {
    sid       = "STSIdentity"
    effect    = "Allow"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }

  # IAM - Pass role to ECS (scoped to this project's ECS roles)
  statement {
    sid     = "PassRoleToECS"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${each.key}-*-ecs-*",
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ci_deploy" {
  for_each = var.github_ci_repos

  name   = "permissions"
  role   = aws_iam_role.ci_deploy[each.key].id
  policy = data.aws_iam_policy_document.ci_deploy[each.key].json
}
