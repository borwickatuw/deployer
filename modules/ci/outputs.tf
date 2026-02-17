# ------------------------------------------------------------------------------
# CI Module Outputs
# ------------------------------------------------------------------------------

output "ci_deploy_role_arns" {
  description = "Map of project prefix to CI deploy IAM role ARN"
  value       = { for k, v in aws_iam_role.ci_deploy : k => v.arn }
}

output "resolved_configs_bucket" {
  description = "Name of the S3 bucket for resolved config storage"
  value       = aws_s3_bucket.resolved_configs.bucket
}

output "resolved_configs_bucket_arn" {
  description = "ARN of the S3 bucket for resolved config storage (for granting write access to infra role)"
  value       = aws_s3_bucket.resolved_configs.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC identity provider"
  value       = aws_iam_openid_connect_provider.github.arn
}
