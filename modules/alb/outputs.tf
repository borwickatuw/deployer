# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------

output "arn" {
  description = "ARN of the load balancer"
  value       = aws_lb.main.arn
}

output "dns_name" {
  description = "DNS name of the load balancer"
  value       = aws_lb.main.dns_name
}

output "zone_id" {
  description = "Zone ID of the load balancer (for Route 53 alias records)"
  value       = aws_lb.main.zone_id
}

output "security_group_id" {
  description = "Security group ID of the load balancer"
  value       = aws_security_group.alb.id
}

output "http_listener_arn" {
  description = "ARN of the HTTP listener"
  value       = aws_lb_listener.http.arn
}

output "https_listener_arn" {
  description = "ARN of the HTTPS listener (null if HTTPS not enabled)"
  value       = local.https_enabled ? (local.auth_enabled ? aws_lb_listener.https_with_auth[0].arn : aws_lb_listener.https[0].arn) : null
}

output "default_target_group_arn" {
  description = "ARN of the default target group"
  value       = aws_lb_target_group.default.arn
}

output "https_enabled" {
  description = "Whether HTTPS is enabled"
  value       = local.https_enabled
}

output "auth_enabled" {
  description = "Whether Cognito authentication is enabled"
  value       = local.auth_enabled
}

output "arn_suffix" {
  description = "ALB ARN suffix (for CloudWatch metrics dimensions)"
  value       = aws_lb.main.arn_suffix
}

output "target_group_arn_suffix" {
  description = "Default target group ARN suffix (for CloudWatch metrics)"
  value       = aws_lb_target_group.default.arn_suffix
}

output "service_target_group_arns" {
  description = "Map of service names to their target group ARNs (for path-based routing)"
  value       = { for k, v in aws_lb_target_group.service : k => v.arn }
}
