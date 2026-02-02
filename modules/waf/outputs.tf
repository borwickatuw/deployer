# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------

output "web_acl_arn" {
  description = "ARN of the WAF Web ACL"
  value       = aws_wafv2_web_acl.main.arn
}

output "web_acl_id" {
  description = "ID of the WAF Web ACL"
  value       = aws_wafv2_web_acl.main.id
}

output "log_group_name" {
  description = "CloudWatch log group name for WAF logs"
  value       = var.logging_enabled ? aws_cloudwatch_log_group.waf[0].name : null
}
