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

variable "default_health_check_path" {
  description = "Health check path for the default ALB target group"
  type        = string
  default     = "/"
}

variable "health_check_interval" {
  description = "Interval between ALB health checks in seconds. Lower values detect failures faster but increase load."
  type        = number
  default     = 30 # Conservative default for production
}

variable "health_check_timeout" {
  description = "ALB health check timeout in seconds"
  type        = number
  default     = 10
}

variable "healthy_threshold" {
  description = "Number of consecutive health check successes to mark target healthy"
  type        = number
  default     = 2
}

variable "unhealthy_threshold" {
  description = "Number of consecutive health check failures to mark target unhealthy"
  type        = number
  default     = 5
}

variable "deregistration_delay" {
  description = "Time to wait for in-flight requests before deregistering targets (seconds). Lower values speed up deployments."
  type        = number
  default     = 120 # Conservative default for production
}

variable "alb_idle_timeout" {
  description = "ALB idle timeout in seconds. Increase for large file uploads (default 60, max 4000)."
  type        = number
  default     = 60
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
  default     = 30
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

# CloudFront configuration

variable "cloudfront_enabled" {
  description = "Enable CloudFront CDN for S3 media delivery"
  type        = bool
  default     = false
}

variable "cloudfront_s3_bucket" {
  description = "Name of the S3 bucket (from s3_buckets) to use as CloudFront origin"
  type        = string
  default     = null
}

variable "cloudfront_aliases" {
  description = "List of domain aliases for CloudFront (e.g., ['cdn.example.com'])"
  type        = list(string)
  default     = []
}

variable "cloudfront_certificate_arn" {
  description = "ACM certificate ARN for CloudFront (must be in us-east-1). Required if cloudfront_aliases is set."
  type        = string
  default     = null
}

variable "cloudfront_price_class" {
  description = "CloudFront price class (PriceClass_100 = US/Canada/Europe, PriceClass_200 = + Asia/Africa, PriceClass_All = all edge locations)"
  type        = string
  default     = "PriceClass_100"
}

variable "cloudfront_default_ttl" {
  description = "Default TTL for CloudFront cache in seconds"
  type        = number
  default     = 86400 # 1 day
}

variable "cloudfront_max_ttl" {
  description = "Maximum TTL for CloudFront cache in seconds"
  type        = number
  default     = 31536000 # 1 year
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

variable "waf_enabled" {
  description = "Enable AWS WAF for the ALB"
  type        = bool
  default     = false
}

variable "waf_ip_reputation_enabled" {
  description = "Enable AWS IP reputation list (blocks known malicious IPs)"
  type        = bool
  default     = true
}

variable "waf_common_rules_enabled" {
  description = "Enable AWS Common Rule Set (OWASP Top 10 protection)"
  type        = bool
  default     = true
}

variable "waf_known_bad_inputs_enabled" {
  description = "Enable Known Bad Inputs rule set (blocks exploit patterns)"
  type        = bool
  default     = true
}

variable "waf_sqli_rules_enabled" {
  description = "Enable SQL injection protection rules"
  type        = bool
  default     = false
}

variable "waf_rate_limit_enabled" {
  description = "Enable rate limiting per IP address"
  type        = bool
  default     = true
}

variable "waf_rate_limit_requests" {
  description = "Maximum requests per IP in a 5-minute window"
  type        = number
  default     = 2000
}

variable "waf_bot_control_level" {
  description = "Bot Control protection level: 'none', 'common', or 'targeted'"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "common", "targeted"], var.waf_bot_control_level)
    error_message = "waf_bot_control_level must be 'none', 'common', or 'targeted'"
  }
}

variable "waf_geo_block_countries" {
  description = "ISO 3166-1 alpha-2 country codes to block (e.g., ['RU', 'CN', 'KP'])"
  type        = list(string)
  default     = []
}

variable "waf_ip_allowlist" {
  description = "CIDR blocks that bypass all WAF rules (e.g., office IPs, CI/CD)"
  type        = list(string)
  default     = []
}

variable "waf_rule_action_override" {
  description = "Override all rule actions to 'count' for testing (set to 'none' for production)"
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "count"], var.waf_rule_action_override)
    error_message = "waf_rule_action_override must be 'none' or 'count'"
  }
}

variable "waf_common_rules_excluded" {
  description = "Rules to exclude from AWSManagedRulesCommonRuleSet (e.g., ['SizeRestrictions_BODY'] to allow file uploads)"
  type        = list(string)
  default     = []
}

# Service Discovery configuration

variable "service_discovery_enabled" {
  description = "Enable AWS Cloud Map service discovery for internal service-to-service communication"
  type        = bool
  default     = false
}
