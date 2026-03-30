# Global configuration

variable "project_name" {
  description = "Name of the project (used for resource naming)"
  type        = string
}

variable "environment" {
  description = "Environment name (staging, production)"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-west-2"
}

# IAM configuration

variable "iam_permissions_boundary" {
  description = "ARN of the permissions boundary policy to attach to IAM roles created by this module"
  type        = string
  default     = null
}

# VPC configuration

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
  default     = ["us-west-2a", "us-west-2b"]
}

# Database configuration

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "Allocated storage for RDS in GB"
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Name of the database"
  type        = string
}

variable "db_username" {
  description = "Database master username"
  type        = string
  sensitive   = true
}

variable "db_password" {
  description = "Database master password"
  type        = string
  sensitive   = true
}

# RDS backup and protection settings (override for production)
variable "rds_backup_retention_period" {
  description = "Number of days to retain automated backups (7 for staging, 35 for production)"
  type        = number
  default     = 7
}

variable "rds_skip_final_snapshot" {
  description = "Skip final snapshot on deletion (true for staging, false for production)"
  type        = bool
  default     = true
}

variable "rds_deletion_protection" {
  description = "Prevent accidental deletion (false for staging, true for production)"
  type        = bool
  default     = false
}

variable "rds_multi_az" {
  description = "Enable Multi-AZ deployment for automatic failover (false for staging, true for production)"
  type        = bool
  default     = false
}

variable "rds_performance_insights" {
  description = "Enable RDS Performance Insights (free tier for 7 days retention on db.t3+)"
  type        = bool
  default     = true
}

variable "rds_monitoring_interval" {
  description = "RDS enhanced monitoring interval in seconds (0 = disabled, 60 = 1 min)"
  type        = number
  default     = 60
}

variable "rds_storage_encrypted" {
  description = "Enable RDS storage encryption at rest (cannot be changed in-place on existing instances)"
  type        = bool
  default     = true
}

# VPC flow logs

variable "vpc_flow_logs_enabled" {
  description = "Enable VPC flow logs to CloudWatch"
  type        = bool
  default     = true
}

# Cache configuration

variable "cache_enabled" {
  description = "Whether to create ElastiCache Redis"
  type        = bool
  default     = false
}

variable "cache_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.micro"
}

# S3 configuration

variable "s3_buckets" {
  description = "Map of S3 bucket configurations"
  type = map(object({
    versioning           = optional(bool, false)
    public               = optional(bool, false)
    cors_allowed_origins = optional(list(string), [])
    cors_allowed_methods = optional(list(string), ["GET", "HEAD"])
  }))
  default = {}
}

# Domain and HTTPS configuration

variable "domain_name" {
  description = "Domain name for the environment (e.g., staging.example.com). Required for HTTPS."
  type        = string
  default     = null
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID. If provided with domain_name, creates ACM certificate automatically."
  type        = string
  default     = null
}

variable "certificate_arn" {
  description = "Existing ACM certificate ARN. Use this instead of route53_zone_id for external DNS providers."
  type        = string
  default     = null
}

# Authentication configuration

variable "cognito_auth_enabled" {
  description = "Enable Cognito authentication for the ALB (creates per-environment pool). Requires HTTPS (domain_name must be set). Ignored if cognito_auth is provided."
  type        = bool
  default     = false
}

variable "cognito_auth" {
  description = "External Cognito authentication configuration (for shared pools). If provided, cognito_auth_enabled is ignored and no local pool is created."
  type = object({
    user_pool_arn       = string
    user_pool_client_id = string
    user_pool_domain    = string
  })
  default = null
}

# Load balancer configuration

variable "health_check" {
  description = "Health check and ALB settings (all have staging-optimized defaults)"
  type = object({
    path                 = optional(string, "/health/")
    interval             = optional(number, 10)
    timeout              = optional(number, 5)
    healthy_threshold    = optional(number, 2)
    unhealthy_threshold  = optional(number, 3)
    deregistration_delay = optional(number, 15)
    idle_timeout         = optional(number, 60)
  })
  default = {}
}

variable "alb_deletion_protection" {
  description = "Enable ALB deletion protection (true for production, false for staging)"
  type        = bool
  default     = false
}

# Container configuration

variable "container_port" {
  description = "Container port for ALB target group. Rails typically uses 3000, Django uses 8000."
  type        = number
  default     = 8000 # Backward compatible - Django default
}

# Logging configuration

variable "log_retention_days" {
  description = "CloudWatch log retention in days (0 = never expire)"
  type        = number
  default     = 365
}

# ECR configuration

variable "ecr_repository_names" {
  description = "List of ECR repository names to create (e.g., ['web', 'worker']). Repositories will be named {name_prefix}-{name}."
  type        = list(string)
  default     = []
}

variable "ecr_lifecycle_policy_count" {
  description = "Number of images to keep per ECR repository (0 to disable lifecycle policy)"
  type        = number
  default     = 10
}

variable "ecr_scan_on_push" {
  description = "Enable image scanning on push for ECR repositories"
  type        = bool
  default     = true
}

variable "ecr_image_tag_mutability" {
  description = "ECR image tag mutability (MUTABLE or IMMUTABLE)"
  type        = string
  default     = "MUTABLE"
}

variable "ecr_force_delete" {
  description = "Allow ECR repositories to be deleted even if they contain images"
  type        = bool
  default     = false
}

# CloudFront in front of ALB (for custom error pages)

variable "cloudfront_alb_enabled" {
  description = "Enable CloudFront in front of ALB for custom error pages"
  type        = bool
  default     = false
}

variable "cloudfront_alb_error_page_content" {
  description = "Custom HTML for 503 error page (uses default if not provided)"
  type        = string
  default     = null
}

variable "cloudfront_alb_error_caching_ttl" {
  description = "TTL for caching error responses (seconds)"
  type        = number
  default     = 60
}

# Additional DNS configuration

variable "additional_dns_records" {
  description = "Additional DNS records to create in Route 53"
  type = map(object({
    type = string
    name = string
    alias_target = optional(object({
      dns_name               = string
      zone_id                = string
      evaluate_target_health = optional(bool, true)
    }))
    cname_value = optional(string)
    ttl         = optional(number, 300)
  }))
  default = {}
}

# WAF configuration

variable "waf_preset" {
  description = "WAF protection level: 'off', 'standard' (recommended), or 'strict'"
  type        = string
  default     = "off"

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
  description = "Advanced: override specific WAF settings from preset (e.g., { rate_limit_requests = 5000, common_rules_excluded = [\"SizeRestrictions_BODY\"], rule_action_override = \"count\" })"
  type        = any
  default     = {}
}

# Service Discovery configuration

variable "service_discovery_enabled" {
  description = "Enable AWS Cloud Map service discovery for internal service-to-service communication"
  type        = bool
  default     = false
}

# Service definitions (for infrastructure provisioning)

variable "services" {
  description = "Service definitions for infrastructure provisioning (target groups, SG rules, service discovery)"
  type = map(object({
    cpu                  = optional(number)
    memory               = optional(number)
    replicas             = optional(number)
    load_balanced        = optional(bool, false)
    port                 = optional(number)
    health_check_path    = optional(string, "/")
    path_pattern         = optional(string)
    health_check_matcher = optional(string)
    service_discovery    = optional(bool, false)
    interruptible        = optional(bool, false)
  }))
  default = {}
}
