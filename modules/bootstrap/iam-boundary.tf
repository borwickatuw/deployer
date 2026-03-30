# ------------------------------------------------------------------------------
# ECS Role Permissions Boundary
#
# This policy sets the MAXIMUM permissions any ECS task role can have.
# It's attached to all ECS task roles created by OpenTofu via the
# iam_permissions_boundary variable.
# ------------------------------------------------------------------------------

resource "aws_iam_policy" "ecs_role_boundary" {
  name = "deployer-ecs-role-boundary"
  # Note: description omitted to allow in-place updates (changing description forces replacement)

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSTaskCommonActions"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:PutRetentionPolicy",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowSSMParameterAccess"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath"
        ]
        Resource = flatten([
          for prefix in var.project_prefixes : [
            "arn:aws:ssm:us-west-2:${data.aws_caller_identity.current.account_id}:parameter/${prefix}/*"
          ]
        ])
      },
      {
        Sid    = "AllowSecretsManagerAccess"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = flatten([
          for prefix in var.project_prefixes : [
            "arn:aws:secretsmanager:us-west-2:${data.aws_caller_identity.current.account_id}:secret:${prefix}-*"
          ]
        ])
      },
      {
        Sid    = "AllowS3BucketAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:HeadObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = flatten([
          for prefix in var.project_prefixes : [
            "arn:aws:s3:::${prefix}-*-media-*",
            "arn:aws:s3:::${prefix}-*-media-*/*",
            "arn:aws:s3:::${prefix}-*-originals-*",
            "arn:aws:s3:::${prefix}-*-originals-*/*"
          ]
        ])
      },
      {
        Sid    = "AllowSESForEmail"
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowECSExecForDebugging"
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowLambdaVPCAccess"
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:AssignPrivateIpAddresses",
          "ec2:UnassignPrivateIpAddresses"
        ]
        Resource = "*"
      }
    ]
  })
}
