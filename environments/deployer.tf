# ==============================================================================
# Shared Environment Configuration
#
# This file is symlinked into each environment directory. OpenTofu merges all
# .tf files in a directory, so the per-env main.tf (backend-only stub) plus
# this file together form the complete configuration.
#
# All environment-specific values are set via tfvars. This file should NOT
# be edited per-environment.
#
# Setup (automatic):
#   uv run python bin/init.py environment --app-name myapp --env-type staging
#
# Setup (manual):
#   cd ~/deployer-environments/<env>
#   ln -s $DEPLOYER_DIR/environments/deployer.tf deployer.tf
# ==============================================================================

# ------------------------------------------------------------------------------
# Data Sources
# ------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

data "terraform_remote_state" "bootstrap" {
  count   = var.bootstrap_state_config != null ? 1 : 0
  backend = "s3"
  config  = var.bootstrap_state_config
}

# ------------------------------------------------------------------------------
# Environment Variables (set in services.auto.tfvars)
# ------------------------------------------------------------------------------

variable "env_type" {
  type        = string
  description = "Environment type: 'staging' or 'production'"

  validation {
    condition     = contains(["staging", "production"], var.env_type)
    error_message = "env_type must be 'staging' or 'production'."
  }
}

variable "aws_region" {
  type        = string
  default     = "us-west-2"
  description = "AWS region for all resources"
}

variable "availability_zones" {
  type        = list(string)
  default     = ["us-west-2a", "us-west-2b"]
  description = "Availability zones for the VPC"
}

variable "db_instance_class" {
  type        = string
  default     = "db.t3.micro"
  description = "RDS instance class"
}

variable "db_allocated_storage" {
  type        = number
  default     = 20
  description = "RDS allocated storage in GB"
}

variable "cache_node_type" {
  type        = string
  default     = "cache.t3.micro"
  description = "ElastiCache node type"
}

variable "bootstrap_state_config" {
  type = object({
    bucket = string
    key    = string
    region = string
  })
  default     = null
  description = "S3 backend config for bootstrap remote state. Set to null if not using shared bootstrap resources (Cognito, CI OIDC)."
}

variable "cognito_auth_enabled" {
  type        = bool
  default     = false
  description = "Enable shared Cognito authentication from bootstrap. Requires bootstrap_state_config."
}

# ------------------------------------------------------------------------------
# Variables
# ------------------------------------------------------------------------------

variable "project_name" {
  type        = string
  description = "Name of the project (used for resource naming)"
}

variable "db_username" {
  type      = string
  sensitive = true
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Database password (min 16 chars, no placeholder values)"

  validation {
    condition     = length(var.db_password) >= 16
    error_message = "Database password must be at least 16 characters."
  }

  validation {
    condition     = !can(regex("^(password|changeme|example|CHANGE)", lower(var.db_password)))
    error_message = "Database password cannot be a placeholder value."
  }
}

variable "domain_name" {
  type        = string
  description = "Domain name for the environment"
}

variable "route53_zone_id" {
  type        = string
  description = "Route 53 hosted zone ID for automatic certificate creation"
  default     = null
}

# Service configuration - sizing that varies by environment
variable "services" {
  type = map(object({
    cpu                  = number
    memory               = number
    replicas             = number
    load_balanced        = bool
    port                 = optional(number)
    health_check_path    = optional(string, "/")
    path_pattern         = optional(string)
    health_check_matcher = optional(string)
    service_discovery    = optional(bool, false)
    interruptible        = optional(bool, false)
  }))
  description = "Service sizing configuration (cpu, memory, replicas)"
}

# Auto-scaling configuration - typically only used in production
variable "scaling" {
  type = map(object({
    min_replicas = number
    max_replicas = number
    cpu_target   = optional(number, 70)
  }))
  default     = {}
  description = "Auto-scaling policies per service"
}

# Health check settings — staging-optimized defaults, override in tfvars for production
variable "health_check" {
  type = object({
    path                 = optional(string, "/health/")
    interval             = optional(number, 10)
    timeout              = optional(number, 5)
    healthy_threshold    = optional(number, 2)
    unhealthy_threshold  = optional(number, 3)
    deregistration_delay = optional(number, 15)
    idle_timeout         = optional(number, 60)
    grace_period         = optional(number, 60)
  })
  default     = {}
  description = "Health check settings. Defaults are staging-optimized. Override in tfvars for production."
}


# ECR repositories to create
variable "ecr_repository_names" {
  type        = list(string)
  default     = []
  description = "List of ECR repository names to create (e.g., ['web', 'worker'])"
}

# Cost budget
variable "budget_monthly_limit" {
  description = "Monthly cost budget in USD. Set to 0 to disable."
  type        = number
  default     = 0
}

variable "budget_notification_email" {
  description = "Email address for budget alerts"
  type        = string
  default     = ""
}

# ECR vulnerability notifications
variable "ecr_scan_sns_topic_arn" {
  description = "SNS topic ARN for ECR vulnerability scan notifications. Set to enable scan alerts."
  type        = string
  default     = ""
}

# WAF Configuration
variable "waf_preset" {
  description = "WAF protection level: 'off', 'standard' (recommended), or 'strict'"
  type        = string
  default     = "standard"

  validation {
    condition     = contains(["off", "standard", "strict"], var.waf_preset)
    error_message = "waf_preset must be 'off', 'standard', or 'strict'"
  }
}

variable "waf_ip_allowlist" {
  description = "CIDRs that bypass WAF (e.g., office IPs, CI/CD)"
  type        = list(string)
  default     = []
}

variable "waf_geo_block_countries" {
  description = "ISO country codes to block (e.g., ['RU', 'CN', 'KP'])"
  type        = list(string)
  default     = []
}

variable "waf_overrides" {
  description = "Advanced: override specific WAF settings from preset"
  type        = any
  default     = {}
}

# VPC configuration
variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the VPC"
}

# Storage
variable "s3_buckets" {
  type = map(object({
    versioning           = optional(bool, false)
    public               = optional(bool, false)
    cors_allowed_origins = optional(list(string), [])
    cors_allowed_methods = optional(list(string), ["GET", "HEAD"])
  }))
  default     = {}
  description = "Map of S3 bucket configurations"
}

# Service Discovery
variable "service_discovery_enabled" {
  type        = bool
  default     = false
  description = "Enable AWS Cloud Map service discovery"
}

# CloudFront ALB
variable "cloudfront_alb_enabled" {
  type        = bool
  default     = true
  description = "Enable CloudFront in front of ALB for custom error pages"
}

# Cache
variable "cache_enabled" {
  type        = bool
  default     = true
  description = "Enable ElastiCache Redis"
}

# Scheduler
variable "stop_schedule" {
  type        = string
  default     = "cron(0 3 ? * TUE-SAT *)"
  description = "Cron expression for stopping the environment (UTC)"
}

variable "start_schedule" {
  type        = string
  default     = "cron(0 15 ? * MON-FRI *)"
  description = "Cron expression for starting the environment (UTC)"
}

variable "scheduler_enabled" {
  type        = bool
  default     = true
  description = "Enable automatic start/stop scheduling"
}

# CI/CD
variable "github_repo" {
  description = "GitHub org/repo for CI/CD deployment (e.g., 'myorg/myapp')"
  type        = string
  default     = ""
}

# ------------------------------------------------------------------------------
# Infrastructure Module
# ------------------------------------------------------------------------------

module "infrastructure" {
  source = "../"

  project_name = var.project_name
  environment  = var.env_type
  aws_region   = var.aws_region

  # VPC
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones

  # Database
  db_instance_class    = var.db_instance_class
  db_allocated_storage = var.db_allocated_storage
  db_name              = var.project_name
  db_username          = var.db_username
  db_password          = var.db_password

  # Cache
  cache_enabled   = var.cache_enabled
  cache_node_type = var.cache_node_type

  # Storage
  s3_buckets = {
    for name, config in var.s3_buckets : name => merge(config, {
      cors_allowed_origins = length(config.cors_allowed_origins) > 0 ? config.cors_allowed_origins : ["https://${var.domain_name}"]
    })
  }

  # HTTPS and Authentication
  domain_name     = var.domain_name
  route53_zone_id = var.route53_zone_id

  # Cognito: use shared pool from bootstrap if available, otherwise disabled
  cognito_auth = var.bootstrap_state_config != null && var.cognito_auth_enabled ? {
    user_pool_arn       = data.terraform_remote_state.bootstrap[0].outputs.cognito_user_pool_arn
    user_pool_client_id = data.terraform_remote_state.bootstrap[0].outputs.cognito_app_clients[var.project_name].client_id
    user_pool_domain    = data.terraform_remote_state.bootstrap[0].outputs.cognito_domain
  } : null

  # Health check settings (override defaults in tfvars for production)
  health_check = var.health_check

  # ECR repositories
  ecr_repository_names = var.ecr_repository_names

  # WAF configuration
  waf_preset              = var.waf_preset
  waf_ip_allowlist        = var.waf_ip_allowlist
  waf_geo_block_countries = var.waf_geo_block_countries
  waf_overrides           = var.waf_overrides

  # Service definitions (target groups, SG rules, service discovery)
  services = var.services

  # Service Discovery (for internal service-to-service communication)
  service_discovery_enabled = var.service_discovery_enabled

  # CloudFront for custom error pages (shows friendly 503 when services are stopped)
  cloudfront_alb_enabled = var.cloudfront_alb_enabled

  # IAM permissions boundary (required for role creation)
  iam_permissions_boundary = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/deployer-ecs-role-boundary"
}

# ------------------------------------------------------------------------------
# CI/CD Role (optional — set github_repo in services.auto.tfvars to enable)
# Requires bootstrap_state_config for OIDC provider and S3 bucket.
# ------------------------------------------------------------------------------

module "ci_role" {
  source = "../modules/ci-role"
  count  = var.github_repo != "" && var.bootstrap_state_config != null ? 1 : 0

  project_prefix              = var.project_name
  github_repo                 = var.github_repo
  oidc_provider_arn           = data.terraform_remote_state.bootstrap[0].outputs.oidc_provider_arn
  resolved_configs_bucket_arn = data.terraform_remote_state.bootstrap[0].outputs.resolved_configs_bucket_arn
  region                      = var.aws_region
  permissions_boundary        = data.terraform_remote_state.bootstrap[0].outputs.ecs_role_boundary_arn
}

# ------------------------------------------------------------------------------
# Automatic Scheduling (stop at night, start in morning)
# ------------------------------------------------------------------------------

module "scheduler" {
  source = "../modules/staging-scheduler"

  environment_name = "${var.project_name}-${var.env_type}"
  ecs_cluster_name = module.infrastructure.ecs_cluster_name
  ecs_services = {
    for name, config in var.services : name => {
      replicas = config.replicas
    }
  }
  rds_instance_id = module.infrastructure.rds_instance_id

  # Schedule (all times in UTC)
  stop_schedule  = var.stop_schedule
  start_schedule = var.start_schedule

  # Set to false to disable automatic scheduling
  enabled = var.scheduler_enabled

  # Required permissions boundary for IAM role creation
  permissions_boundary = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/deployer-ecs-role-boundary"
}

# ------------------------------------------------------------------------------
# ECR Vulnerability Notifications (optional)
# ------------------------------------------------------------------------------

module "ecr_notifications" {
  source = "../modules/ecr-notifications"
  count  = var.ecr_scan_sns_topic_arn != "" ? 1 : 0

  name_prefix   = "${var.project_name}-${var.env_type}"
  sns_topic_arn = var.ecr_scan_sns_topic_arn
}

# ------------------------------------------------------------------------------
# Cost Budget (optional)
# ------------------------------------------------------------------------------

module "cost_budget" {
  source = "../modules/cost-budget"
  count  = var.budget_monthly_limit > 0 && var.budget_notification_email != "" ? 1 : 0

  name_prefix        = "${var.project_name}-${var.env_type}"
  budget_amount      = var.budget_monthly_limit
  notification_email = var.budget_notification_email
}

# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------

# Infrastructure
output "database_url" {
  value     = module.infrastructure.database_url
  sensitive = true
}

output "redis_url" {
  value = module.infrastructure.redis_endpoint != null ? "redis://${module.infrastructure.redis_endpoint}:6379" : ""
}

# Database credentials (two-tier: app + migrate)
output "db_app_username_secret_arn" {
  value       = module.infrastructure.db_app_username_secret_arn
  description = "ARN for app database username (DML only, for runtime services)"
}

output "db_app_password_secret_arn" {
  value       = module.infrastructure.db_app_password_secret_arn
  description = "ARN for app database password (DML only, for runtime services)"
}

output "db_migrate_username_secret_arn" {
  value       = module.infrastructure.db_migrate_username_secret_arn
  description = "ARN for migrate database username (DDL + DML, for migrations)"
}

output "db_migrate_password_secret_arn" {
  value       = module.infrastructure.db_migrate_password_secret_arn
  description = "ARN for migrate database password (DDL + DML, for migrations)"
}

output "db_users_lambda_function_name" {
  value       = module.infrastructure.db_users_lambda_function_name
  description = "Lambda function name for creating database extensions"
}

output "db_host" {
  value       = module.infrastructure.db_host
  description = "Database host endpoint"
}

output "db_port" {
  value       = module.infrastructure.db_port
  description = "Database port"
}

output "db_name" {
  value       = var.project_name
  description = "Database name"
}

# S3 buckets (conditional — not all envs have storage)
output "s3_bucket_names" {
  value       = module.infrastructure.s3_bucket_names
  description = "Map of S3 bucket names"
}

output "s3_media_bucket" {
  value       = try(module.infrastructure.s3_bucket_names["media"], null)
  description = "S3 bucket name for media/derivatives"
}

output "s3_originals_bucket" {
  value       = try(module.infrastructure.s3_bucket_names["originals"], null)
  description = "S3 bucket name for original uploads"
}

# ECS
output "ecs_cluster_name" {
  value = module.infrastructure.ecs_cluster_name
}

output "alb_dns_name" {
  value = module.infrastructure.alb_dns_name
}

output "domain_name" {
  value       = var.domain_name
  description = "Domain name for this environment"
}

output "vpc_id" {
  value = module.infrastructure.vpc_id
}

output "private_subnet_ids" {
  value = module.infrastructure.private_subnet_ids
}

# Service configuration output - used by deploy.py
output "service_config" {
  value       = var.services
  description = "Service sizing configuration for deploy.py to merge with deploy.toml"
}

output "scaling_config" {
  value       = var.scaling
  description = "Auto-scaling configuration for deploy.py"
}

output "health_check_config" {
  value       = var.health_check
  description = "Health check defaults for deploy.py"
}

# Authentication (shared Cognito from bootstrap — conditional)
output "cognito_user_pool_id" {
  value       = var.bootstrap_state_config != null && var.cognito_auth_enabled ? data.terraform_remote_state.bootstrap[0].outputs.cognito_user_pool_id : null
  description = "Shared Cognito user pool ID - use this to create users via AWS CLI"
}

output "cognito_user_pool_client_id" {
  value       = var.bootstrap_state_config != null && var.cognito_auth_enabled ? data.terraform_remote_state.bootstrap[0].outputs.cognito_app_clients[var.project_name].client_id : null
  description = "Cognito user pool client ID - for authentication"
}

output "cognito_user_pool_client_secret" {
  value       = var.bootstrap_state_config != null && var.cognito_auth_enabled ? data.terraform_remote_state.bootstrap[0].outputs.cognito_app_clients[var.project_name].client_secret : null
  description = "Cognito user pool client secret - for authentication"
  sensitive   = true
}

output "https_enabled" {
  value       = module.infrastructure.https_enabled
  description = "Whether HTTPS is enabled"
}

output "rds_instance_id" {
  value       = module.infrastructure.rds_instance_id
  description = "RDS instance identifier for scheduling"
}

# ECS deployment outputs
output "ecs_security_group_id" {
  value       = module.infrastructure.ecs_security_group_id
  description = "Security group ID for ECS tasks"
}

output "alb_target_group_arn" {
  value       = module.infrastructure.alb_target_group_arn
  description = "ARN of the default ALB target group"
}

output "ecs_execution_role_arn" {
  value       = module.infrastructure.ecs_execution_role_arn
  description = "ARN of the ECS task execution role"
}

output "ecs_task_role_arn" {
  value       = module.infrastructure.ecs_task_role_arn
  description = "ARN of the ECS task role"
}

# Service target groups and discovery (from root module)
output "service_target_groups" {
  value       = module.infrastructure.service_target_groups
  description = "Map of service names to target group ARNs"
}

output "service_discovery_namespace_name" {
  value       = module.infrastructure.service_discovery_namespace_name
  description = "Service discovery namespace name"
}

output "service_discovery_registries" {
  value       = module.infrastructure.service_discovery_registries
  description = "Map of service names to service discovery registry ARNs"
}

# ECR outputs
output "ecr_repository_urls" {
  value       = module.infrastructure.ecr_repository_urls
  description = "Map of ECR repository names to their URLs"
}

output "ecr_prefix" {
  value       = "${var.project_name}-${var.env_type}"
  description = "ECR repository prefix (name_prefix)"
}

# WAF outputs
output "waf_enabled" {
  value       = module.infrastructure.waf_enabled
  description = "Whether WAF is enabled"
}

output "waf_web_acl_arn" {
  value       = module.infrastructure.waf_web_acl_arn
  description = "ARN of the WAF Web ACL"
}

output "waf_log_group_name" {
  value       = module.infrastructure.waf_log_group_name
  description = "CloudWatch log group for WAF logs"
}

# CloudFront ALB outputs
output "cloudfront_alb_distribution_id" {
  value       = module.infrastructure.cloudfront_alb_distribution_id
  description = "CloudFront distribution ID for custom error pages"
}

output "cloudfront_alb_domain_name" {
  value       = module.infrastructure.cloudfront_alb_domain_name
  description = "CloudFront distribution domain name"
}

output "cloudfront_alb_error_bucket" {
  value       = module.infrastructure.cloudfront_alb_error_bucket
  description = "S3 bucket for error pages"
}

# CI/CD
output "ci_role_arn" {
  value       = var.github_repo != "" && var.bootstrap_state_config != null ? module.ci_role[0].role_arn : null
  description = "CI deploy IAM role ARN for GitHub Actions"
}

# Scheduler
output "scheduler_enabled" {
  value       = module.scheduler.scheduling_enabled
  description = "Whether automatic scheduling is enabled"
}

output "scheduler_start_schedule" {
  value       = module.scheduler.start_schedule
  description = "Cron expression for when the environment starts"
}

output "scheduler_stop_schedule" {
  value       = module.scheduler.stop_schedule
  description = "Cron expression for when the environment stops"
}

output "scheduler_lambda" {
  value       = module.scheduler.lambda_function_name
  description = "Name of the scheduler Lambda function"
}
