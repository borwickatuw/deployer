# ------------------------------------------------------------------------------
# IAM Roles for ECS Tasks
# ------------------------------------------------------------------------------

# Shared assume role policy for ECS tasks
data "aws_iam_policy_document" "ecs_task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ------------------------------------------------------------------------------
# Task Execution Role (used by ECS agent to pull images and write logs)
# ------------------------------------------------------------------------------

resource "aws_iam_role" "task_execution" {
  name               = "${var.name_prefix}-${var.service_name}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ------------------------------------------------------------------------------
# Task Role (used by the application to access AWS resources)
# ------------------------------------------------------------------------------

resource "aws_iam_role" "task" {
  name               = "${var.name_prefix}-${var.service_name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json
}

# S3 access policy for task role (optional)
data "aws_iam_policy_document" "s3_access" {
  count = local.has_s3_buckets ? 1 : 0

  # Originals bucket - read/write for Django uploads
  dynamic "statement" {
    for_each = var.s3_originals_bucket_arn != "" ? [1] : []
    content {
      sid = "OriginalsBucketAccess"
      actions = [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
      ]
      resources = [
        var.s3_originals_bucket_arn,
        "${var.s3_originals_bucket_arn}/*",
      ]
    }
  }

  # Media bucket - read/write for derivatives
  dynamic "statement" {
    for_each = var.s3_media_bucket_arn != "" ? [1] : []
    content {
      sid = "MediaBucketAccess"
      actions = [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
      ]
      resources = [
        var.s3_media_bucket_arn,
        "${var.s3_media_bucket_arn}/*",
      ]
    }
  }
}

resource "aws_iam_role_policy" "s3_access" {
  count = local.has_s3_buckets ? 1 : 0

  name   = "${var.name_prefix}-${var.service_name}-s3-access"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.s3_access[0].json
}
