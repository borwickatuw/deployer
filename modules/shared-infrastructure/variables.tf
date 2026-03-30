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
  description = "Advanced: override specific WAF settings from preset"
  type        = any
  default     = {}
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
