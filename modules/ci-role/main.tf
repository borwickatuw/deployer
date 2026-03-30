# ------------------------------------------------------------------------------
# CI Role Module
#
# Creates a {project}-ci-deploy IAM role for GitHub Actions CI/CD.
# The role is scoped to one project prefix and trusts a specific GitHub repo
# via OIDC federation.
#
# Prerequisites:
#   - modules/ci must be instantiated in bootstrap (creates OIDC provider + S3 bucket)
#
# Usage (in an environment's main.tf):
#   module "ci_role" {
#     source = "../modules/ci-role"
#
#     project_prefix              = "myapp"
#     github_repo                 = "myorg/myapp"
#     oidc_provider_arn           = data.terraform_remote_state.bootstrap.outputs.oidc_provider_arn
#     resolved_configs_bucket_arn = data.terraform_remote_state.bootstrap.outputs.resolved_configs_bucket_arn
#     region                      = var.region
#     permissions_boundary        = data.terraform_remote_state.bootstrap.outputs.ecs_role_boundary_arn
#   }
# ------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# ------------------------------------------------------------------------------
# OIDC Trust Policy
#
# Allows GitHub Actions from the specified repo + environments to assume
# this role via OIDC. No stored AWS credentials needed.
# ------------------------------------------------------------------------------

data "aws_iam_policy_document" "trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
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
        "repo:${var.github_repo}:environment:${env}"
      ]
    }
  }
}

resource "aws_iam_role" "ci_deploy" {
  name                 = "${var.project_prefix}-ci-deploy"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  permissions_boundary = var.permissions_boundary
}

# ------------------------------------------------------------------------------
# Permissions Policy
#
# Scoped to one project prefix. Mirrors the deploy permissions from
# iam-app-deploy.tf but without terraform state access.
# ------------------------------------------------------------------------------

data "aws_iam_policy_document" "permissions" {
  # S3 - Read resolved configs (scoped to this project's keys)
  statement {
    sid       = "S3ResolvedConfigs"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.resolved_configs_bucket_arn}/${var.project_prefix}-*/config.json"]
  }

  statement {
    sid       = "S3ListBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.resolved_configs_bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.project_prefix}-*/*"]
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
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:service/${var.project_prefix}-*/*",
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${var.project_prefix}-*:*",
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
      "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${var.project_prefix}-*",
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
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.project_prefix}-*:*",
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
      "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.project_prefix}/*/last-migrations-hash",
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
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_prefix}-*-ecs-*",
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ci_deploy" {
  name   = "permissions"
  role   = aws_iam_role.ci_deploy.id
  policy = data.aws_iam_policy_document.permissions.json
}
