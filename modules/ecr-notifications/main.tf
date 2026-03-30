# ECR Vulnerability Notifications
#
# Sends SNS notifications when ECR image scans find CRITICAL or HIGH
# severity vulnerabilities.
#
# Usage:
#   module "ecr_notifications" {
#     source = "../../modules/ecr-notifications"
#
#     name_prefix   = local.name_prefix
#     sns_topic_arn = module.alarms.sns_topic_arn
#   }

resource "aws_cloudwatch_event_rule" "ecr_scan_findings" {
  name        = "${var.name_prefix}-ecr-scan-findings"
  description = "ECR image scan completed with critical or high vulnerabilities"

  event_pattern = jsonencode({
    source      = ["aws.ecr"]
    detail-type = ["ECR Image Scan"]
    detail = {
      scan-status = ["COMPLETE"]
      finding-severity-counts = {
        CRITICAL = [{ "numeric" : [">", 0] }]
      }
    }
  })

  tags = {
    Name = "${var.name_prefix}-ecr-scan-findings"
  }
}

resource "aws_cloudwatch_event_target" "ecr_scan_sns" {
  rule      = aws_cloudwatch_event_rule.ecr_scan_findings.name
  target_id = "${var.name_prefix}-ecr-scan-sns"
  arn       = var.sns_topic_arn

  input_transformer {
    input_paths = {
      repo   = "$.detail.repository-name"
      tag    = "$.detail.image-tags[0]"
      counts = "$.detail.finding-severity-counts"
    }
    input_template = "\"ECR vulnerability scan alert: <repo>:<tag> has critical findings. Severity counts: <counts>\""
  }
}

# Allow EventBridge to publish to the SNS topic
resource "aws_sns_topic_policy" "ecr_scan_publish" {
  arn = var.sns_topic_arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "${replace(var.name_prefix, "-", "")}EcrScanPublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = var.sns_topic_arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_cloudwatch_event_rule.ecr_scan_findings.arn
          }
        }
      }
    ]
  })
}
