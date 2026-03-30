# ------------------------------------------------------------------------------
# CI Role Module Outputs
# ------------------------------------------------------------------------------

output "role_arn" {
  description = "ARN of the CI deploy IAM role"
  value       = aws_iam_role.ci_deploy.arn
}

output "role_name" {
  description = "Name of the CI deploy IAM role"
  value       = aws_iam_role.ci_deploy.name
}
