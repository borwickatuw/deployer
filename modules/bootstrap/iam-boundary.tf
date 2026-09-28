# ------------------------------------------------------------------------------
# ECS Role Permissions Boundary
#
# This policy sets the MAXIMUM permissions any ECS task role can have.
# It's attached to all ECS task roles created by OpenTofu via the
# iam_permissions_boundary variable.
# ------------------------------------------------------------------------------

locals {
  # Every bucket kind an application may declare in deploy.toml's [storage]
  # buckets. Buckets are named {prefix}-{env}-{kind}-{account}; admitting
  # only these kinds keeps a project's other buckets (its tofu state, its
  # logs) out of a task role's reach. A kind missing here is denied to the
  # task role whatever the role's own policy grants.
  data_bucket_kinds = ["media", "originals", "cache", "evidence"]
}

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
            for kind in local.data_bucket_kinds : [
              "arn:aws:s3:::${prefix}-*-${kind}-*",
              "arn:aws:s3:::${prefix}-*-${kind}-*/*"
            ]
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
          "ecs:ListServices",
          # The GPU container instance's drain check before it is stopped
          "ecs:ListContainerInstances",
          "ecs:DescribeContainerInstances"
        ]
        Resource = flatten([
          for prefix in var.project_prefixes : [
            "arn:aws:ecs:us-west-2:${data.aws_caller_identity.current.account_id}:service/${prefix}-*/*",
            "arn:aws:ecs:us-west-2:${data.aws_caller_identity.current.account_id}:cluster/${prefix}-*",
            "arn:aws:ecs:us-west-2:${data.aws_caller_identity.current.account_id}:container-instance/${prefix}-*/*"
          ]
        ])
      },
      {
        # The GPU container instance (ecs-gpu-capacity) goes down and up
        # with the environment; only instances the module tagged
        Sid    = "AllowGPUInstanceStopStart"
        Effect = "Allow"
        Action = [
          "ec2:StartInstances",
          "ec2:StopInstances"
        ]
        Resource = "arn:aws:ec2:us-west-2:${data.aws_caller_identity.current.account_id}:instance/*"
        Condition = {
          StringLike = {
            "aws:ResourceTag/deployer-build-host" = [for prefix in var.project_prefixes : "${prefix}-*"]
          }
        }
      },
      {
        Sid      = "AllowGPUInstanceRead"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*"
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

# ------------------------------------------------------------------------------
# ECS Container Instance Role Permissions Boundary
#
# An EC2 container instance (the GPU capacity the ecs-gpu-capacity module
# provides) runs the ECS agent and the SSM agent, neither of which is a
# task: the task boundary above has no ecs:RegisterContainerInstance, no
# ssm:UpdateInstanceInformation, and would silently deny both, so the
# instance never joins the cluster and never answers a session. This
# boundary sets the MAXIMUM permissions an instance role can have — the
# two AWS-managed policies the module attaches (the ECS-for-EC2 role and
# SSM managed-instance core) are wider, and this is the ceiling on them.
# No task-runtime actions: a task's own role, not the instance's, is what
# a container acts as (ECS_AWSVPC_BLOCK_IMDS keeps it that way).
# ------------------------------------------------------------------------------

resource "aws_iam_policy" "ecs_instance_role_boundary" {
  name = "deployer-ecs-instance-role-boundary"
  # Note: description omitted to allow in-place updates (changing description forces replacement)

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowECSAgent"
        Effect = "Allow"
        Action = [
          "ecs:RegisterContainerInstance",
          "ecs:DeregisterContainerInstance",
          "ecs:DiscoverPollEndpoint",
          "ecs:Poll",
          "ecs:StartTelemetrySession",
          "ecs:Submit*",
          "ecs:UpdateContainerInstancesState",
          "ecs:TagResource",
          "ec2:DescribeTags"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowImagePullAndLogs"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Sid    = "AllowSSMAgent"
        Effect = "Allow"
        Action = [
          "ssm:UpdateInstanceInformation",
          "ssm:ListAssociations",
          "ssm:ListInstanceAssociations",
          "ssm:GetDocument",
          "ssm:DescribeDocument",
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel",
          "ec2messages:AcknowledgeMessage",
          "ec2messages:DeleteMessage",
          "ec2messages:FailMessage",
          "ec2messages:GetEndpoint",
          "ec2messages:GetMessages",
          "ec2messages:SendReply"
        ]
        Resource = "*"
      }
    ]
  })
}
