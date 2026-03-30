# Shared Infrastructure Module - Outputs
#
# These outputs are consumed by the app-in-shared-env module via terraform_remote_state

# ------------------------------------------------------------------------------
# VPC Outputs
# ------------------------------------------------------------------------------

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = module.vpc.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = module.vpc.private_subnet_ids
}

# ------------------------------------------------------------------------------
# ECS Cluster Outputs
# ------------------------------------------------------------------------------

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = module.ecs_cluster.cluster_name
}

output "ecs_cluster_arn" {
  description = "ARN of the ECS cluster"
  value       = module.ecs_cluster.cluster_arn
}

output "ecs_security_group_id" {
  description = "ID of the ECS tasks security group"
  value       = module.ecs_cluster.security_group_id
}

# ------------------------------------------------------------------------------
# ALB Outputs
# ------------------------------------------------------------------------------

output "alb_arn" {
  description = "ARN of the ALB"
  value       = module.alb.arn
}

output "alb_dns_name" {
  description = "DNS name of the ALB"
  value       = module.alb.dns_name
}

output "alb_zone_id" {
  description = "Zone ID of the ALB (for Route 53 alias records)"
  value       = module.alb.zone_id
}

output "alb_security_group_id" {
  description = "Security group ID of the ALB"
  value       = module.alb.security_group_id
}

output "alb_http_listener_arn" {
  description = "ARN of the HTTP listener"
  value       = module.alb.http_listener_arn
}

output "alb_https_listener_arn" {
  description = "ARN of the HTTPS listener (null if HTTPS not enabled)"
  value       = module.alb.https_listener_arn
}

output "alb_default_target_group_arn" {
  description = "ARN of the ALB's default target group"
  value       = module.alb.default_target_group_arn
}

output "https_enabled" {
  description = "Whether HTTPS is enabled"
  value       = local.https_enabled
}

# ------------------------------------------------------------------------------
# Cognito Outputs
# ------------------------------------------------------------------------------

output "cognito_user_pool_id" {
  description = "Cognito user pool ID (empty if Cognito disabled)"
  value       = var.cognito_auth_enabled && length(module.cognito) > 0 ? module.cognito[0].user_pool_id : ""
}

output "cognito_user_pool_arn" {
  description = "Cognito user pool ARN (empty if Cognito disabled)"
  value       = var.cognito_auth_enabled && length(module.cognito) > 0 ? module.cognito[0].user_pool_arn : ""
}

output "cognito_user_pool_client_id" {
  description = "Cognito user pool client ID (empty if Cognito disabled)"
  value       = var.cognito_auth_enabled && length(module.cognito) > 0 ? module.cognito[0].client_id : ""
}

output "cognito_user_pool_client_secret" {
  description = "Cognito user pool client secret (empty if Cognito disabled)"
  value       = var.cognito_auth_enabled && length(module.cognito) > 0 ? module.cognito[0].client_secret : ""
  sensitive   = true
}

output "cognito_domain" {
  description = "Cognito domain (empty if Cognito disabled)"
  value       = var.cognito_auth_enabled && length(module.cognito) > 0 ? module.cognito[0].domain : ""
}

output "cognito_auth_enabled" {
  description = "Whether Cognito authentication is enabled"
  value       = var.cognito_auth_enabled
}

# ------------------------------------------------------------------------------
# Cache Outputs
# ------------------------------------------------------------------------------

output "redis_endpoint" {
  description = "Redis endpoint (empty if cache disabled)"
  value       = var.cache_enabled && length(module.elasticache) > 0 ? module.elasticache[0].endpoint : ""
}

output "redis_url" {
  description = "Redis URL for applications (empty if cache disabled)"
  value       = var.cache_enabled && length(module.elasticache) > 0 ? "redis://${module.elasticache[0].endpoint}:6379" : ""
}

# ------------------------------------------------------------------------------
# DNS/Certificate Outputs
# ------------------------------------------------------------------------------

output "domain_name" {
  description = "Primary domain name"
  value       = var.domain_name
}

output "certificate_arn" {
  description = "ACM certificate ARN"
  value       = local.certificate_arn
}

output "route53_zone_id" {
  description = "Route53 zone ID (if provided)"
  value       = var.route53_zone_id
}

# ------------------------------------------------------------------------------
# Naming Outputs
# ------------------------------------------------------------------------------

output "name_prefix" {
  description = "Name prefix used for resources"
  value       = var.name_prefix
}

# ------------------------------------------------------------------------------
# WAF Outputs
# ------------------------------------------------------------------------------

output "waf_enabled" {
  description = "Whether WAF is enabled"
  value       = local.waf_enabled
}

output "waf_web_acl_arn" {
  description = "ARN of the WAF Web ACL (empty if WAF disabled)"
  value       = local.waf_enabled && length(module.waf) > 0 ? module.waf[0].web_acl_arn : ""
}

output "waf_log_group_name" {
  description = "CloudWatch log group name for WAF logs (empty if WAF disabled)"
  value       = local.waf_enabled && length(module.waf) > 0 ? module.waf[0].log_group_name : ""
}

# ------------------------------------------------------------------------------
# S3 Storage Outputs
# ------------------------------------------------------------------------------

output "s3_storage_enabled" {
  description = "Whether S3 storage is enabled"
  value       = var.s3_storage_enabled
}

output "s3_originals_bucket" {
  description = "S3 bucket name for original files (empty if S3 storage disabled)"
  value       = var.s3_storage_enabled && length(aws_s3_bucket.originals) > 0 ? aws_s3_bucket.originals[0].id : ""
}

output "s3_originals_bucket_arn" {
  description = "S3 bucket ARN for original files (empty if S3 storage disabled)"
  value       = var.s3_storage_enabled && length(aws_s3_bucket.originals) > 0 ? aws_s3_bucket.originals[0].arn : ""
}

output "s3_media_bucket" {
  description = "S3 bucket name for media derivatives (empty if S3 storage disabled)"
  value       = var.s3_storage_enabled && length(aws_s3_bucket.media) > 0 ? aws_s3_bucket.media[0].id : ""
}

output "s3_media_bucket_arn" {
  description = "S3 bucket ARN for media derivatives (empty if S3 storage disabled)"
  value       = var.s3_storage_enabled && length(aws_s3_bucket.media) > 0 ? aws_s3_bucket.media[0].arn : ""
}

# ------------------------------------------------------------------------------
# Service Discovery Outputs
# ------------------------------------------------------------------------------

output "service_discovery_enabled" {
  description = "Whether service discovery is enabled"
  value       = var.service_discovery_enabled
}

output "service_discovery_namespace_id" {
  description = "AWS Cloud Map namespace ID (empty if service discovery disabled)"
  value       = var.service_discovery_enabled && length(aws_service_discovery_private_dns_namespace.main) > 0 ? aws_service_discovery_private_dns_namespace.main[0].id : ""
}

output "service_discovery_namespace_arn" {
  description = "AWS Cloud Map namespace ARN (empty if service discovery disabled)"
  value       = var.service_discovery_enabled && length(aws_service_discovery_private_dns_namespace.main) > 0 ? aws_service_discovery_private_dns_namespace.main[0].arn : ""
}

output "service_discovery_namespace_name" {
  description = "AWS Cloud Map namespace name (e.g., 'myapp-staging.local')"
  value       = var.service_discovery_enabled && length(aws_service_discovery_private_dns_namespace.main) > 0 ? aws_service_discovery_private_dns_namespace.main[0].name : ""
}

# ------------------------------------------------------------------------------
# Shared RDS Outputs
# ------------------------------------------------------------------------------

output "shared_rds_enabled" {
  description = "Whether shared RDS is enabled"
  value       = var.shared_rds_enabled
}

output "shared_rds_endpoint" {
  description = "Shared RDS endpoint (empty if shared RDS disabled)"
  value       = var.shared_rds_enabled && length(module.shared_rds) > 0 ? module.shared_rds[0].endpoint : ""
}

output "shared_rds_address" {
  description = "Shared RDS hostname (empty if shared RDS disabled)"
  value       = var.shared_rds_enabled && length(module.shared_rds) > 0 ? module.shared_rds[0].address : ""
}

output "shared_rds_port" {
  description = "Shared RDS port (0 if shared RDS disabled)"
  value       = var.shared_rds_enabled && length(module.shared_rds) > 0 ? module.shared_rds[0].port : 0
}

output "shared_rds_security_group_id" {
  description = "Security group ID for the shared RDS instance (empty if shared RDS disabled)"
  value       = var.shared_rds_enabled && length(module.shared_rds) > 0 ? module.shared_rds[0].security_group_id : ""
}

output "shared_rds_master_secret_arn" {
  description = "ARN of the Secrets Manager secret containing shared RDS master credentials (empty if shared RDS disabled)"
  value       = var.shared_rds_enabled && length(module.shared_rds_secrets) > 0 ? module.shared_rds_secrets[0].master_secret_arn : ""
}

output "shared_rds_instance_id" {
  description = "Shared RDS instance identifier for AWS CLI commands (empty if shared RDS disabled)"
  value       = var.shared_rds_enabled && length(module.shared_rds) > 0 ? module.shared_rds[0].db_instance_id : ""
}
