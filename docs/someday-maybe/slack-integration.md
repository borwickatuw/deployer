+++
title = "Notification Improvements: Slack Integration"
+++

**Current state**: Email notifications only.

**Enhancement**: Send alerts to Slack channels for:

- Warning-level alerts (non-paging)
- Deployment notifications
- Daily summary reports

**Implementation approach**:

```hcl
# Lambda function to format and send to Slack
resource "aws_lambda_function" "slack_notifier" {
  function_name = "${var.name_prefix}-slack-notifier"
  # ... Lambda configuration
}

# SNS → Lambda → Slack
resource "aws_sns_topic_subscription" "slack" {
  topic_arn = aws_sns_topic.warnings.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.slack_notifier.arn
}
```

**Complexity**: Medium - requires Lambda function and Slack webhook management.
