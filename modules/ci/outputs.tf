# ------------------------------------------------------------------------------
# CI Module Outputs (Shared Infrastructure)
# ------------------------------------------------------------------------------

output "resolved_configs_bucket" {
  description = "Name of the S3 bucket for resolved config storage"
  value       = aws_s3_bucket.resolved_configs.bucket
}

output "resolved_configs_bucket_arn" {
  description = "ARN of the S3 bucket (for granting access in ci-role and infra policies)"
  value       = aws_s3_bucket.resolved_configs.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC identity provider (passed to ci-role modules)"
  value       = aws_iam_openid_connect_provider.github.arn
}
