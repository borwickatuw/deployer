# Staging Environment Scheduler
#
# Creates a Lambda function and EventBridge rules to automatically
# start/stop the staging environment on a schedule.

locals {
  function_name = "${var.environment_name}-scheduler"
  ecs_services_json = jsonencode({
    for name, config in var.ecs_services : name => {
      replicas = config.replicas
    }
  })
}

# -----------------------------------------------------------------------------
# IAM Role for Lambda
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name                 = "${local.function_name}-role"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume_role.json
  permissions_boundary = var.permissions_boundary

  tags = {
    Name = "${local.function_name}-role"
  }
}

data "aws_iam_policy_document" "scheduler_policy" {
  # ECS permissions - use resource ARN pattern instead of condition
  # The ecs:cluster condition key doesn't work correctly for UpdateService
  statement {
    actions = [
      "ecs:UpdateService",
      "ecs:DescribeServices",
    ]
    resources = ["arn:aws:ecs:*:*:service/${var.ecs_cluster_name}/*"]
  }

  # ListServices needs cluster resource, not service resource
  statement {
    actions = [
      "ecs:ListServices",
    ]
    resources = ["arn:aws:ecs:*:*:cluster/${var.ecs_cluster_name}"]
  }

  # RDS permissions
  statement {
    actions = [
      "rds:StartDBInstance",
      "rds:StopDBInstance",
      "rds:DescribeDBInstances",
    ]
    resources = ["arn:aws:rds:*:*:db:${var.rds_instance_id}"]
  }

  # CloudWatch Logs
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "${local.function_name}-policy"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_policy.json
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------

data "archive_file" "lambda" {
  type        = "zip"
  source_file = "${path.module}/lambda/handler.py"
  output_path = "${path.module}/lambda/handler.zip"
}

resource "aws_lambda_function" "scheduler" {
  filename         = data.archive_file.lambda.output_path
  function_name    = local.function_name
  role             = aws_iam_role.scheduler.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      ECS_CLUSTER_NAME = var.ecs_cluster_name
      ECS_SERVICES     = local.ecs_services_json
      RDS_INSTANCE_ID  = var.rds_instance_id
    }
  }

  tags = {
    Name        = local.function_name
    Environment = var.environment_name
  }
}

# CloudWatch Log Group for Lambda
resource "aws_cloudwatch_log_group" "scheduler" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.function_name}-logs"
  }
}

# -----------------------------------------------------------------------------
# EventBridge Rules (using for_each to reduce duplication)
# -----------------------------------------------------------------------------

locals {
  schedules = var.enabled ? {
    stop  = { schedule = var.stop_schedule, action = "stop" }
    start = { schedule = var.start_schedule, action = "start" }
  } : {}
}

resource "aws_cloudwatch_event_rule" "schedule" {
  for_each = local.schedules

  name                = "${var.environment_name}-${each.key}"
  description         = "${title(each.key)} ${var.environment_name} staging environment"
  schedule_expression = each.value.schedule

  tags = {
    Name        = "${var.environment_name}-${each.key}"
    Environment = var.environment_name
  }
}

resource "aws_cloudwatch_event_target" "schedule" {
  for_each = local.schedules

  rule      = aws_cloudwatch_event_rule.schedule[each.key].name
  target_id = "${title(each.key)}StagingEnvironment"
  arn       = aws_lambda_function.scheduler.arn
  input     = jsonencode({ action = each.value.action })
}

resource "aws_lambda_permission" "schedule" {
  for_each = local.schedules

  statement_id  = "AllowEventBridge${title(each.key)}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule[each.key].arn
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "lambda_function_name" {
  description = "Name of the scheduler Lambda function"
  value       = aws_lambda_function.scheduler.function_name
}

output "lambda_function_arn" {
  description = "ARN of the scheduler Lambda function"
  value       = aws_lambda_function.scheduler.arn
}

output "stop_schedule" {
  description = "Cron expression for stop schedule"
  value       = var.stop_schedule
}

output "start_schedule" {
  description = "Cron expression for start schedule"
  value       = var.start_schedule
}

output "scheduling_enabled" {
  description = "Whether automatic scheduling is enabled"
  value       = var.enabled
}
