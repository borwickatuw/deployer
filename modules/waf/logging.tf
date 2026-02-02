# ------------------------------------------------------------------------------
# CloudWatch Logging
# ------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "waf" {
  count = var.logging_enabled ? 1 : 0

  # WAF logging requires log group name to start with "aws-waf-logs-"
  name              = "aws-waf-logs-${var.name_prefix}"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${var.name_prefix}-waf-logs"
  }
}

resource "aws_wafv2_web_acl_logging_configuration" "main" {
  count = var.logging_enabled ? 1 : 0

  log_destination_configs = [aws_cloudwatch_log_group.waf[0].arn]
  resource_arn            = aws_wafv2_web_acl.main.arn

  # Optionally filter what gets logged (reduce log volume)
  # By default, log all requests that match a rule
  logging_filter {
    default_behavior = "DROP"

    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ANY"

      condition {
        action_condition {
          action = "BLOCK"
        }
      }
      condition {
        action_condition {
          action = "COUNT"
        }
      }
    }
  }
}
