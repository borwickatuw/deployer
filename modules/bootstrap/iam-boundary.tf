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
            "arn:aws:s3:::${prefix}-*-originals-*/*",
            "arn:aws:s3:::${prefix}-*-cache-*",
            "arn:aws:s3:::${prefix}-*-cache-*/*"
          ]
        ])
      },
      {
        Sid      = "AllowAutoscaleMetricPublish"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringLike = {
            "cloudwatch:namespace" = [
              for prefix in var.project_prefixes : "${prefix}-*"
            ]
          }
        }
      },
      {
        Sid    = "AllowTaskScaleInProtection"
        Effect = "Allow"
        Action = ["ecs:UpdateTaskProtection"]
        Resource = flatten([
          for prefix in var.project_prefixes : [
            "arn:aws:ecs:us-west-2:${data.aws_caller_identity.current.account_id}:task/${prefix}-*/*"
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

# ------------------------------------------------------------------------------
# Scheduler Role Permissions Boundary
#
# The staging start/stop scheduler is a control-plane Lambda, not an ECS
# task, so it cannot share the task boundary above: that boundary caps
# every task role at task-runtime actions and deliberately excludes
# service and RDS control-plane calls, which silently denied every
# start/stop the scheduler attempted (its own policy grants them, but the
# boundary is the ceiling). This boundary sets the MAXIMUM permissions the
# scheduler role can have. A boundary only caps; the scheduler module's own
# role policy still scopes each action to that environment's cluster and DB.
# ------------------------------------------------------------------------------

resource "aws_iam_policy" "scheduler_role_boundary" {
  name = "deployer-scheduler-role-boundary"
  # Note: description omitted to allow in-place updates (changing description forces replacement)

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSchedulerLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowServiceScaling"
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:DescribeServices",
          "ecs:ListServices"
        ]
        Resource = flatten([
          for prefix in var.project_prefixes : [
            "arn:aws:ecs:us-west-2:${data.aws_caller_identity.current.account_id}:service/${prefix}-*/*",
            "arn:aws:ecs:us-west-2:${data.aws_caller_identity.current.account_id}:cluster/${prefix}-*"
          ]
        ])
      },
      {
        Sid    = "AllowRDSStartStop"
        Effect = "Allow"
        Action = [
          "rds:StartDBInstance",
          "rds:StopDBInstance",
          "rds:DescribeDBInstances"
        ]
        Resource = flatten([
          for prefix in var.project_prefixes : [
            "arn:aws:rds:us-west-2:${data.aws_caller_identity.current.account_id}:db:${prefix}-*"
          ]
        ])
      }
    ]
  })
}
