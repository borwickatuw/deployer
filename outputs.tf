output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = module.ecs_cluster.cluster_name
}

output "ecs_cluster_arn" {
  description = "ARN of the ECS cluster"
  value       = module.ecs_cluster.cluster_arn
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = module.alb.dns_name
}

output "alb_zone_id" {
  description = "Zone ID of the Application Load Balancer"
  value       = module.alb.zone_id
}

output "database_endpoint" {
  description = "RDS database endpoint"
  value       = module.rds.endpoint
}

output "database_url" {
  description = "Database connection URL"
  value       = module.rds.connection_url
  sensitive   = true
}

output "rds_instance_id" {
  description = "RDS instance identifier"
  value       = module.rds.db_instance_id
}

# Database credentials in Secrets Manager (for ECS secrets injection)
output "db_secret_arn" {
  description = "ARN of the database credentials secret"
  value       = module.db_secrets.secret_arn
  sensitive   = true
}

output "db_password_secret_arn" {
  description = "ARN for database password (for ECS secrets)"
  value       = module.db_secrets.password_arn
}

output "db_username_secret_arn" {
  description = "ARN for database username (for ECS secrets)"
  value       = module.db_secrets.username_arn
}

output "db_host" {
  description = "Database host endpoint"
  value       = module.rds.address
}

output "db_port" {
  description = "Database port"
  value       = module.rds.port
}

output "redis_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = var.cache_enabled ? module.elasticache[0].endpoint : null
}

output "s3_bucket_arns" {
  description = "ARNs of created S3 buckets"
  value       = { for k, v in module.s3 : k => v.bucket_arn }
}

output "s3_bucket_names" {
  description = "Names of created S3 buckets"
  value       = { for k, v in module.s3 : k => v.bucket_name }
}

output "private_subnet_ids" {
  description = "IDs of private subnets (for ECS services)"
  value       = module.vpc.private_subnet_ids
}

output "public_subnet_ids" {
  description = "IDs of public subnets (for ALB)"
  value       = module.vpc.public_subnet_ids
}

# HTTPS/Certificate outputs

output "certificate_arn" {
  description = "ARN of the ACM certificate (if created)"
  value       = local.certificate_arn
}

output "https_enabled" {
  description = "Whether HTTPS is enabled for the ALB"
  value       = module.alb.https_enabled
}

# Cognito outputs (for managing users)

output "cognito_user_pool_id" {
  description = "Cognito user pool ID (for managing users via AWS CLI)"
  value       = var.cognito_auth_enabled ? module.cognito[0].user_pool_id : null
}

output "cognito_user_pool_client_id" {
  description = "Cognito user pool client ID (for authentication)"
  value       = var.cognito_auth_enabled ? module.cognito[0].client_id : null
}

output "cognito_user_pool_client_secret" {
  description = "Cognito user pool client secret (for authentication)"
  value       = var.cognito_auth_enabled ? module.cognito[0].client_secret : null
  sensitive   = true
}

output "cognito_auth_enabled" {
  description = "Whether Cognito authentication is enabled"
  value       = module.alb.auth_enabled
}

# ECS outputs for deploy script

output "ecs_security_group_id" {
  description = "Security group ID for ECS tasks"
  value       = module.ecs_cluster.security_group_id
}

output "alb_target_group_arn" {
  description = "ARN of the default ALB target group"
  value       = module.alb.default_target_group_arn
}

output "alb_https_listener_arn" {
  description = "ARN of the HTTPS listener (null if HTTPS not enabled)"
  value       = module.alb.https_listener_arn
}

output "alb_security_group_id" {
  description = "Security group ID for the ALB"
  value       = module.alb.security_group_id
}

output "ecs_execution_role_arn" {
  description = "ARN of the ECS task execution role"
  value       = aws_iam_role.ecs_task_execution.arn
}

output "ecs_task_role_arn" {
  description = "ARN of the ECS task role"
  value       = aws_iam_role.ecs_task.arn
}

output "ecs_log_group_name" {
  description = "CloudWatch log group name for ECS tasks"
  value       = aws_cloudwatch_log_group.ecs.name
}

# ECR outputs

output "ecr_repository_urls" {
  description = "Map of ECR repository names to their URLs"
  value       = length(var.ecr_repository_names) > 0 ? module.ecr[0].repository_urls : {}
}

output "ecr_repository_arns" {
  description = "Map of ECR repository names to their ARNs"
  value       = length(var.ecr_repository_names) > 0 ? module.ecr[0].repository_arns : {}
}

# CloudFront outputs

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID"
  value       = var.cloudfront_enabled && var.cloudfront_s3_bucket != null ? module.cloudfront[0].distribution_id : null
}

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain name"
  value       = var.cloudfront_enabled && var.cloudfront_s3_bucket != null ? module.cloudfront[0].domain_name : null
}

# Route 53 outputs

output "dns_record_fqdns" {
  description = "FQDNs of created DNS records"
  value       = var.domain_name != null && var.route53_zone_id != null ? module.route53[0].record_fqdns : {}
}

# WAF outputs

output "waf_enabled" {
  description = "Whether WAF is enabled"
  value       = var.waf_enabled
}

output "waf_web_acl_arn" {
  description = "ARN of the WAF Web ACL (null if WAF disabled)"
  value       = var.waf_enabled ? module.waf[0].web_acl_arn : null
}

output "waf_log_group_name" {
  description = "CloudWatch log group name for WAF logs (null if WAF disabled)"
  value       = var.waf_enabled ? module.waf[0].log_group_name : null
}

# CloudFront ALB outputs (for custom error pages)

output "cloudfront_alb_distribution_id" {
  description = "CloudFront distribution ID (null if CloudFront ALB disabled)"
  value       = var.cloudfront_alb_enabled ? module.cloudfront_alb[0].distribution_id : null
}

output "cloudfront_alb_domain_name" {
  description = "CloudFront distribution domain name (null if CloudFront ALB disabled)"
  value       = var.cloudfront_alb_enabled ? module.cloudfront_alb[0].distribution_domain_name : null
}

output "cloudfront_alb_error_bucket" {
  description = "S3 bucket name for error pages (null if CloudFront ALB disabled)"
  value       = var.cloudfront_alb_enabled ? module.cloudfront_alb[0].error_bucket_name : null
}

# Service Discovery outputs

output "service_discovery_enabled" {
  description = "Whether service discovery is enabled"
  value       = var.service_discovery_enabled
}

output "service_discovery_namespace_id" {
  description = "AWS Cloud Map namespace ID (null if service discovery disabled)"
  value       = var.service_discovery_enabled ? aws_service_discovery_private_dns_namespace.main[0].id : null
}

output "service_discovery_namespace_arn" {
  description = "AWS Cloud Map namespace ARN (null if service discovery disabled)"
  value       = var.service_discovery_enabled ? aws_service_discovery_private_dns_namespace.main[0].arn : null
}

output "service_discovery_namespace_name" {
  description = "AWS Cloud Map namespace name, e.g., 'myapp-staging.local'"
  value       = var.service_discovery_enabled ? aws_service_discovery_private_dns_namespace.main[0].name : null
}
