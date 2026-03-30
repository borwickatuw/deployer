# ------------------------------------------------------------------------------
# deployer-app-deploy Role
# Used by: deploy.py
# ------------------------------------------------------------------------------

data "aws_iam_policy_document" "app_deploy" {
  count = var.create_iam_roles ? 1 : 0

  # CloudWatch Logs - Read access to ECS logs
  statement {
    sid    = "CloudWatchLogsRead"
    effect = "Allow"
    actions = [
      "logs:DescribeLogStreams",
      "logs:GetLogEvents",
      "logs:FilterLogEvents"
    ]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${prefix}-*:*"
      ]
    ])
  }

  # ECS - Read access (global)
  statement {
    sid    = "ECSRead"
    effect = "Allow"
    actions = [
      "ecs:Describe*",
      "ecs:List*"
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

  # ECS - Service operations (scoped to projects)
  statement {
    sid    = "ECSService"
    effect = "Allow"
    actions = [
      "ecs:CreateService",
      "ecs:UpdateService",
      "ecs:RunTask"
    ]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:service/${prefix}-*/*",
        "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${prefix}-*:*"
      ]
    ])
  }

  # ECR - Authorization
  statement {
    sid       = "ECRAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # ECR - Image operations (scoped to projects)
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
      "ecr:UploadLayerPart"
    ]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${prefix}-*"
      ]
    ])
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

  # Lambda - Invoke db-users/db-on-shared-rds Lambda for creating extensions
  statement {
    sid     = "LambdaInvokeDbUsers"
    effect  = "Allow"
    actions = ["lambda:InvokeFunction"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:lambda:${var.region}:${data.aws_caller_identity.current.account_id}:function:${prefix}-*-create-db-users",
        "arn:aws:lambda:${var.region}:${data.aws_caller_identity.current.account_id}:function:${prefix}-*-setup-db-on-shared-rds",
      ]
    ])
  }

  # STS - Get caller identity
  statement {
    sid       = "STSIdentity"
    effect    = "Allow"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }

  # SSM - Read test passwords for Cognito
  statement {
    sid       = "SSMReadTestPasswords"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/deployer/*/cognito-test-password"]
  }

  # SSM - Describe parameters
  statement {
    sid       = "SSMDescribeSecrets"
    effect    = "Allow"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]
  }

  # SSM - Read/write migrations hash for skip detection
  statement {
    sid     = "SSMMigrationsHash"
    effect  = "Allow"
    actions = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${prefix}/*/last-migrations-hash"
      ]
    ])
  }

  # Cognito - Auth for staging access
  statement {
    sid       = "CognitoAuth"
    effect    = "Allow"
    actions   = ["cognito-idp:InitiateAuth"]
    resources = ["arn:aws:cognito-idp:${var.region}:${data.aws_caller_identity.current.account_id}:userpool/*"]
  }

  # IAM - Pass role to ECS
  statement {
    sid     = "PassRoleToECS"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = flatten([
      for prefix in var.project_prefixes : [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${prefix}-*-ecs-*"
      ]
    ])
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # S3 - Read terraform state
  statement {
    sid    = "S3TerraformStateRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket"
    ]
    resources = [
      "arn:aws:s3:::deployer-terraform-state-*",
      "arn:aws:s3:::deployer-terraform-state-*/*"
    ]
  }
}

resource "aws_iam_role" "app_deploy" {
  count = var.create_iam_roles ? 1 : 0

  name               = "deployer-app-deploy"
  assume_role_policy = data.aws_iam_policy_document.trust_policy[0].json
}

resource "aws_iam_role_policy" "app_deploy" {
  count = var.create_iam_roles ? 1 : 0

  name   = "permissions"
  role   = aws_iam_role.app_deploy[0].id
  policy = data.aws_iam_policy_document.app_deploy[0].json
}
