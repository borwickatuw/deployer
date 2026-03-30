output "event_rule_arn" {
  description = "ARN of the EventBridge rule for ECR scan findings"
  value       = aws_cloudwatch_event_rule.ecr_scan_findings.arn
}
