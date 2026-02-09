# App in Shared Environment Module - Variables

# ------------------------------------------------------------------------------
# App Identity
# ------------------------------------------------------------------------------

variable "app_name" {
  description = "Name of the application"
  type        = string
}

variable "environment" {
  description = "Environment name (e.g., 'staging', 'production')"
  type        = string
}

variable "domain_name" {
  description = "Domain name for this app (e.g., 'myapp.staging.example.com')"
  type        = string
}

# ------------------------------------------------------------------------------
# Shared Infrastructure Reference
# ------------------------------------------------------------------------------

variable "shared_state_backend" {
  description = "Terraform backend type for shared infrastructure state ('local' or 's3')"
  type        = string
  default     = "local"
}

variable "shared_state_path" {
  description = "Path to shared infrastructure state file (for local backend)"
  type        = string
  default     = null
}

variable "shared_state_config" {
  description = "Configuration for shared infrastructure state (for s3 backend)"
  type        = map(string)
  default     = {}
}

# ------------------------------------------------------------------------------
# Database Configuration
# ------------------------------------------------------------------------------

variable "use_shared_rds" {
  description = "Use shared RDS instance from shared infrastructure instead of creating a separate instance"
  type        = bool
  default     = false
}

variable "db_instance_class" {
  description = "RDS instance class (only used when use_shared_rds = false)"
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GB (only used when use_shared_rds = false)"
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Database name (defaults to app_name with dashes replaced)"
  type        = string
  default     = null
}

variable "db_username" {
  description = "Database master username (only used when use_shared_rds = false)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "db_password" {
  description = "Database master password (only used when use_shared_rds = false)"
  type        = string
  default     = ""
  sensitive   = true
}

# ------------------------------------------------------------------------------
# ECR Configuration
# ------------------------------------------------------------------------------

variable "ecr_repository_names" {
  description = "List of ECR repository names to create"
  type        = list(string)
  default     = ["web"]
}

# ------------------------------------------------------------------------------
# ALB Configuration
# ------------------------------------------------------------------------------

variable "listener_rule_priority" {
  description = "Priority for the ALB listener rule (must be unique per app, e.g., 100, 200, 300)"
  type        = number
}

variable "container_port" {
  description = "Port the container listens on"
  type        = number
  default     = 8000
}

variable "health_check_path" {
  description = "Health check path for the target group"
  type        = string
  default     = "/health/"
}

variable "health_check_interval" {
  description = "Interval between health checks in seconds"
  type        = number
  default     = 10
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
  default     = 15
}

# ------------------------------------------------------------------------------
# Route53 Configuration
# ------------------------------------------------------------------------------

variable "route53_zone_id" {
  description = "Route53 zone ID for creating DNS record (optional)"
  type        = string
  default     = null
}

# ------------------------------------------------------------------------------
# S3 Configuration
# ------------------------------------------------------------------------------

variable "s3_bucket_arns" {
  description = "ARNs of S3 buckets the app needs access to"
  type        = list(string)
  default     = []
}

# ------------------------------------------------------------------------------
# Service Configuration (for deploy.py)
# ------------------------------------------------------------------------------

variable "services" {
  description = "Service sizing configuration passed through for deploy.py"
  type = map(object({
    cpu               = number
    memory            = number
    replicas          = number
    load_balanced     = bool
    port              = optional(number)
    health_check_path = optional(string, "/")
  }))
  default = {
    web = {
      cpu           = 256
      memory        = 512
      replicas      = 1
      load_balanced = true
      port          = 8000
    }
  }
}

variable "scaling" {
  description = "Auto-scaling configuration passed through for deploy.py"
  type = map(object({
    min_replicas = number
    max_replicas = number
    cpu_target   = optional(number, 70)
  }))
  default = {}
}

variable "health_check" {
  description = "Health check configuration passed through for deploy.py"
  type = object({
    interval            = optional(number, 30)
    timeout             = optional(number, 10)
    healthy_threshold   = optional(number, 2)
    unhealthy_threshold = optional(number, 5)
    grace_period        = optional(number, 60) # ECS health check grace period in seconds
  })
  default = {}
}
