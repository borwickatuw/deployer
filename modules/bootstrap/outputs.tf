# ------------------------------------------------------------------------------
# Bootstrap Outputs
# ------------------------------------------------------------------------------

# State bucket
output "state_bucket_name" {
  value       = aws_s3_bucket.terraform_state.bucket
  description = "Name of the S3 bucket for terraform state storage"
}

# ECS permissions boundary
output "ecs_role_boundary_arn" {
  value       = aws_iam_policy.ecs_role_boundary.arn
  description = "ARN of the ECS role permissions boundary policy"
}

# Account info
output "account_id" {
  value       = data.aws_caller_identity.current.account_id
  description = "AWS account ID"
}

output "region" {
  value       = data.aws_region.current.id
  description = "AWS region"
}

# IAM Role ARNs (only when create_iam_roles = true)
output "app_deploy_role_arn" {
  value       = var.create_iam_roles ? aws_iam_role.app_deploy[0].arn : null
  description = "ARN of the deployer-app-deploy IAM role"
}

output "infra_admin_role_arn" {
  value       = var.create_iam_roles ? aws_iam_role.infra_admin[0].arn : null
  description = "ARN of the deployer-infra-admin IAM role"
}

output "cognito_admin_role_arn" {
  value       = var.create_iam_roles ? aws_iam_role.cognito_admin[0].arn : null
  description = "ARN of the deployer-cognito-admin IAM role"
}
