+++
title = "Notification Improvements: PagerDuty Integration"
+++

**Current state**: CloudWatch alarms notify via SNS email subscriptions.

**Enhancement**: Integrate with PagerDuty for:

- Real-time alerting with escalation policies
- On-call schedules and rotations
- Incident management and tracking
- Mobile push notifications

**Implementation approach**:

```hcl
# Option 1: SNS → PagerDuty integration
resource "aws_sns_topic_subscription" "pagerduty" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "https"
  endpoint  = "https://events.pagerduty.com/integration/XXXXX/enqueue"
}

# Option 2: EventBridge → PagerDuty
resource "aws_cloudwatch_event_rule" "alarm_state_change" {
  name        = "${var.name_prefix}-alarm-to-pagerduty"
  description = "Route alarm state changes to PagerDuty"

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      state = { value = ["ALARM"] }
    }
  })
}
```

**Complexity**: Medium - requires PagerDuty account and API key management.
