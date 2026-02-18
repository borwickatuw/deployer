# ------------------------------------------------------------------------------
# Variables
# ------------------------------------------------------------------------------

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for the ALB"
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS. If provided, enables HTTPS and redirects HTTP to HTTPS."
  type        = string
  default     = null
}

variable "cognito_auth" {
  description = "Cognito authentication configuration. If provided, requires authentication before forwarding requests."
  type = object({
    user_pool_arn       = string
    user_pool_client_id = string
    user_pool_domain    = string
  })
  default = null
}

variable "default_health_check_path" {
  description = "Health check path for the default target group"
  type        = string
  default     = "/"
}

variable "health_check_interval" {
  description = "Interval between health checks in seconds"
  type        = number
  default     = 30 # Conservative default for production
}

variable "health_check_timeout" {
  description = "Health check timeout in seconds"
  type        = number
  default     = 10
}

variable "healthy_threshold" {
  description = "Number of consecutive successes to mark healthy"
  type        = number
  default     = 2
}

variable "unhealthy_threshold" {
  description = "Number of consecutive failures to mark unhealthy"
  type        = number
  default     = 5
}

variable "deregistration_delay" {
  description = "Time to wait for in-flight requests before deregistering (seconds)"
  type        = number
  default     = 120 # Conservative default for production
}

variable "idle_timeout" {
  description = "Idle timeout in seconds. Increase for large file uploads (default 60, max 4000)."
  type        = number
  default     = 60
}

variable "deletion_protection" {
  description = "Enable deletion protection (true for production, false for staging)"
  type        = bool
  default     = false
}

variable "access_logs_enabled" {
  description = "Enable ALB access logging to S3"
  type        = bool
  default     = false
}

variable "access_logs_bucket" {
  description = "S3 bucket name for ALB access logs (required if access_logs_enabled = true)"
  type        = string
  default     = ""
}

variable "access_logs_prefix" {
  description = "S3 key prefix for ALB access logs"
  type        = string
  default     = "alb-logs"
}

variable "additional_target_groups" {
  description = "Additional target groups with path-based routing rules"
  type = map(object({
    port                 = number
    path_pattern         = string
    health_check_path    = optional(string, "/")
    health_check_matcher = optional(string)
    priority             = number
  }))
  default = {}
}
