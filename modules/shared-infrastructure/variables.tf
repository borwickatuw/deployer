# Shared Infrastructure Module - Variables

# ------------------------------------------------------------------------------
# Required Variables
# ------------------------------------------------------------------------------

variable "name_prefix" {
  description = "Prefix for resource names (e.g., 'shared-staging')"
  type        = string
}

variable "domain_name" {
  description = "Primary domain name for the shared infrastructure (e.g., 'staging.example.com')"
  type        = string
}

# ------------------------------------------------------------------------------
# VPC Configuration
# ------------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "List of availability zones to use"
  type        = list(string)
  default     = ["us-west-2a", "us-west-2b"]
}

# ------------------------------------------------------------------------------
# Certificate Configuration
# ------------------------------------------------------------------------------

variable "route53_zone_id" {
  description = "Route 53 hosted zone ID for automatic certificate creation and DNS"
  type        = string
  default     = null
}

variable "certificate_arn" {
  description = "Existing ACM certificate ARN (alternative to creating one)"
  type        = string
  default     = null
}

variable "certificate_san" {
  description = "Subject alternative names for the certificate (e.g., ['*.staging.example.com'])"
  type        = list(string)
  default     = []
}

# ------------------------------------------------------------------------------
# Cognito Configuration
# ------------------------------------------------------------------------------

variable "cognito_auth_enabled" {
  description = "Enable Cognito authentication (typically true for staging)"
  type        = bool
  default     = false
}

# ------------------------------------------------------------------------------
# Cache Configuration
# ------------------------------------------------------------------------------

variable "cache_enabled" {
  description = "Enable shared ElastiCache cluster"
  type        = bool
  default     = false
}

variable "cache_node_type" {
  description = "ElastiCache node type"
  type        = string
  default     = "cache.t3.micro"
}

# ------------------------------------------------------------------------------
# ALB Health Check Configuration
# ------------------------------------------------------------------------------

variable "default_health_check_path" {
  description = "Default health check path for target groups"
  type        = string
  default     = "/health/"
}

variable "health_check_interval" {
  description = "Interval between health checks in seconds"
  type        = number
  default     = 10 # Staging-optimized default
}

variable "health_check_timeout" {
  description = "Health check timeout in seconds"
  type        = number
  default     = 5
}

variable "healthy_threshold" {
  description = "Number of consecutive successes to mark healthy"
  type        = number
  default     = 2
}

variable "unhealthy_threshold" {
  description = "Number of consecutive failures to mark unhealthy"
  type        = number
  default     = 3
}

variable "deregistration_delay" {
  description = "Time to wait for in-flight requests before deregistering (seconds)"
  type        = number
  default     = 15 # Staging-optimized default
}

variable "alb_deletion_protection" {
  description = "Enable ALB deletion protection (true for production, false for staging)"
  type        = bool
  default     = false
}

variable "alb_access_logs_enabled" {
  description = "Enable ALB access logging to S3"
  type        = bool
  default     = false
}

variable "alb_access_logs_bucket" {
  description = "S3 bucket name for ALB access logs (required if alb_access_logs_enabled)"
  type        = string
  default     = ""
}

variable "alb_access_logs_prefix" {
  description = "S3 key prefix for ALB access logs"
  type        = string
  default     = "alb-logs"
}

# ------------------------------------------------------------------------------
# WAF Configuration
# ------------------------------------------------------------------------------

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

# ------------------------------------------------------------------------------
# S3 Storage Configuration
# ------------------------------------------------------------------------------

variable "s3_storage_enabled" {
  description = "Enable S3 storage for media files (creates originals and media buckets)"
  type        = bool
  default     = false
}

# ------------------------------------------------------------------------------
# CloudFront Configuration
# ------------------------------------------------------------------------------

variable "cloudfront_enabled" {
  description = "Enable CloudFront CDN for media delivery (requires s3_storage_enabled)"
  type        = bool
  default     = false
}

variable "cloudfront_domain" {
  description = "Custom domain for CloudFront distribution (e.g., 'media.example.com')"
  type        = string
  default     = ""
}

variable "cloudfront_certificate_arn" {
  description = "ACM certificate ARN for CloudFront custom domain (must be in us-east-1)"
  type        = string
  default     = ""
}

variable "cloudfront_public_key_pem" {
  description = "PEM-encoded public key for CloudFront signed URLs (RSA 2048-bit)"
  type        = string
  default     = ""
}

variable "cloudfront_price_class" {
  description = "CloudFront price class (PriceClass_100 = US/EU only, PriceClass_200 = +Asia, PriceClass_All = global)"
  type        = string
  default     = "PriceClass_100"

  validation {
    condition     = contains(["PriceClass_100", "PriceClass_200", "PriceClass_All"], var.cloudfront_price_class)
    error_message = "cloudfront_price_class must be 'PriceClass_100', 'PriceClass_200', or 'PriceClass_All'"
  }
}

# ------------------------------------------------------------------------------
# Service Discovery Configuration
# ------------------------------------------------------------------------------

variable "service_discovery_enabled" {
  description = "Enable AWS Cloud Map service discovery for internal service-to-service communication"
  type        = bool
  default     = false
}

# ------------------------------------------------------------------------------
# Shared RDS Configuration
# ------------------------------------------------------------------------------

variable "shared_rds_enabled" {
  description = "Enable a shared RDS instance for multiple applications. Each app gets its own database on this instance."
  type        = bool
  default     = false
}

variable "shared_rds_instance_class" {
  description = "RDS instance class for the shared database"
  type        = string
  default     = "db.t3.small"
}

variable "shared_rds_allocated_storage" {
  description = "Allocated storage in GB for the shared RDS instance"
  type        = number
  default     = 20
}

variable "shared_rds_master_username" {
  description = "Master username for the shared RDS instance"
  type        = string
  default     = "shared_admin"
  sensitive   = true
}

variable "shared_rds_master_password" {
  description = "Master password for the shared RDS instance"
  type        = string
  default     = ""
  sensitive   = true
}

variable "shared_rds_backup_retention_period" {
  description = "Number of days to retain automated backups (7 for staging, 35 for production)"
  type        = number
  default     = 7
}

variable "shared_rds_skip_final_snapshot" {
  description = "Skip final snapshot on deletion (true for staging, false for production)"
  type        = bool
  default     = true
}

variable "shared_rds_deletion_protection" {
  description = "Prevent accidental deletion (false for staging, true for production)"
  type        = bool
  default     = false
}

variable "shared_rds_multi_az" {
  description = "Enable Multi-AZ deployment for automatic failover"
  type        = bool
  default     = false
}

variable "shared_rds_performance_insights" {
  description = "Enable RDS Performance Insights (free tier for 7 days retention on db.t3+)"
  type        = bool
  default     = true
}

variable "shared_rds_monitoring_interval" {
  description = "RDS enhanced monitoring interval in seconds (0 = disabled, 60 = 1 min)"
  type        = number
  default     = 60
}

variable "shared_rds_storage_encrypted" {
  description = "Enable RDS storage encryption at rest (cannot be changed in-place on existing instances)"
  type        = bool
  default     = true
}

variable "vpc_flow_logs_enabled" {
  description = "Enable VPC flow logs to CloudWatch"
  type        = bool
  default     = true
}

variable "permissions_boundary" {
  description = "IAM permissions boundary ARN"
  type        = string
  default     = null
}
