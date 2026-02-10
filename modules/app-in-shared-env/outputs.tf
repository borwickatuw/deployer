# App in Shared Environment Module - Outputs
#
# These outputs match the structure expected by config.toml placeholders
# so the existing deploy.py workflow works unchanged.

# ------------------------------------------------------------------------------
# Infrastructure Outputs (from shared)
# ------------------------------------------------------------------------------

output "vpc_id" {
  description = "VPC ID (from shared infrastructure)"
  value       = local.shared.vpc_id
}

output "private_subnet_ids" {
  description = "Private subnet IDs (from shared infrastructure)"
  value       = local.shared.private_subnet_ids
}

output "ecs_cluster_name" {
  description = "ECS cluster name (from shared infrastructure)"
  value       = local.shared.ecs_cluster_name
}

output "ecs_cluster_arn" {
  description = "ECS cluster ARN (from shared infrastructure)"
  value       = local.shared.ecs_cluster_arn
}

output "ecs_security_group_id" {
  description = "ECS security group ID (from shared infrastructure)"
  value       = local.shared.ecs_security_group_id
}

output "alb_dns_name" {
  description = "ALB DNS name (from shared infrastructure)"
  value       = local.shared.alb_dns_name
}

output "https_enabled" {
  description = "Whether HTTPS is enabled (from shared infrastructure)"
  value       = local.shared.https_enabled
}

# ------------------------------------------------------------------------------
# Per-App Outputs
# ------------------------------------------------------------------------------

output "alb_target_group_arn" {
  description = "ARN of this app's ALB target group"
  value       = aws_lb_target_group.app.arn
}

output "ecs_execution_role_arn" {
  description = "ARN of the ECS task execution role"
  value       = aws_iam_role.ecs_task_execution.arn
}

output "ecs_task_role_arn" {
  description = "ARN of the ECS task role"
  value       = aws_iam_role.ecs_task.arn
}

output "domain_name" {
  description = "Domain name for this app"
  value       = var.domain_name
}

output "listener_rule_priority" {
  description = "ALB listener rule priority"
  value       = var.listener_rule_priority
}

# ------------------------------------------------------------------------------
# Database Outputs
# ------------------------------------------------------------------------------

output "db_host" {
  description = "Database hostname"
  value       = var.use_shared_rds ? module.db_on_shared_rds[0].db_host : module.rds[0].address
}

output "db_port" {
  description = "Database port"
  value       = var.use_shared_rds ? module.db_on_shared_rds[0].db_port : module.rds[0].port
}

output "db_name" {
  description = "Database name"
  value       = local.db_name
}

output "database_url" {
  description = "PostgreSQL connection URL (only available for separate RDS mode)"
  value       = var.use_shared_rds ? "" : module.rds[0].connection_url
  sensitive   = true
}

output "rds_instance_id" {
  description = "RDS instance identifier (empty when using shared RDS)"
  value       = var.use_shared_rds ? "" : module.rds[0].db_instance_id
}

output "rds_endpoint" {
  description = "RDS endpoint"
  value       = var.use_shared_rds ? "${module.db_on_shared_rds[0].db_host}:${module.db_on_shared_rds[0].db_port}" : module.rds[0].endpoint
}

# Database credentials (from db-on-shared-rds or db-users module)
output "db_app_username_secret_arn" {
  description = "ARN for app database username secret"
  value       = var.use_shared_rds ? module.db_on_shared_rds[0].app_username_arn : ""
}

output "db_app_password_secret_arn" {
  description = "ARN for app database password secret"
  value       = var.use_shared_rds ? module.db_on_shared_rds[0].app_password_arn : ""
}

output "db_migrate_username_secret_arn" {
  description = "ARN for migrate database username secret"
  value       = var.use_shared_rds ? module.db_on_shared_rds[0].migrate_username_arn : ""
}

output "db_migrate_password_secret_arn" {
  description = "ARN for migrate database password secret"
  value       = var.use_shared_rds ? module.db_on_shared_rds[0].migrate_password_arn : ""
}

output "use_shared_rds" {
  description = "Whether this app uses the shared RDS instance"
  value       = var.use_shared_rds
}

output "db_users_lambda_function_name" {
  description = "Name of the db-users Lambda function (for creating extensions at deploy time)"
  value       = var.use_shared_rds ? module.db_on_shared_rds[0].lambda_function_name : ""
}

# ------------------------------------------------------------------------------
# ECR Outputs
# ------------------------------------------------------------------------------

output "ecr_prefix" {
  description = "ECR repository URL prefix for building image URIs"
  value       = length(module.ecr.repository_urls) > 0 ? split("/", values(module.ecr.repository_urls)[0])[0] : ""
}

output "ecr_repository_urls" {
  description = "Map of ECR repository names to URLs"
  value       = module.ecr.repository_urls
}

# ------------------------------------------------------------------------------
# Redis Outputs (from shared, if enabled)
# ------------------------------------------------------------------------------

output "redis_url" {
  description = "Redis URL (from shared infrastructure, empty if disabled)"
  value       = local.shared.redis_url
}

output "redis_endpoint" {
  description = "Redis endpoint (from shared infrastructure, empty if disabled)"
  value       = local.shared.redis_endpoint
}

# ------------------------------------------------------------------------------
# Cognito Outputs (from shared, if enabled)
# ------------------------------------------------------------------------------

output "cognito_user_pool_id" {
  description = "Cognito user pool ID (from shared infrastructure, empty if disabled)"
  value       = local.shared.cognito_user_pool_id
}

output "cognito_user_pool_client_id" {
  description = "Cognito user pool client ID (from shared infrastructure, empty if disabled)"
  value       = local.shared.cognito_user_pool_client_id
}

output "cognito_user_pool_client_secret" {
  description = "Cognito user pool client secret (from shared infrastructure, empty if disabled)"
  value       = local.shared.cognito_user_pool_client_secret
  sensitive   = true
}

# ------------------------------------------------------------------------------
# Service Configuration Outputs (for deploy.py)
# ------------------------------------------------------------------------------

output "service_config" {
  description = "Service sizing configuration for deploy.py"
  value       = var.services
}

output "scaling_config" {
  description = "Auto-scaling configuration for deploy.py"
  value       = var.scaling
}

output "health_check_config" {
  description = "Health check configuration for deploy.py"
  value       = var.health_check
}
