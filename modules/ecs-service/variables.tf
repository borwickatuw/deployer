# ------------------------------------------------------------------------------
# Variables
# ------------------------------------------------------------------------------

variable "name_prefix" {
  type = string
}

variable "service_name" {
  type = string
}

variable "cluster_arn" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "image" {
  type = string
}

variable "command" {
  type    = list(string)
  default = null
}

variable "cpu" {
  type    = number
  default = 256
}

variable "memory" {
  type    = number
  default = 512
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "container_port" {
  type    = number
  default = null
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "secrets" {
  type    = map(string) # map of name -> SSM parameter ARN or Secrets Manager ARN
  default = {}
}

variable "alb_target_group_arn" {
  type    = string
  default = null
}

variable "health_check_path" {
  type    = string
  default = "/"
}

variable "log_group_name" {
  type = string
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention in days (0 = never expire)"
  type        = number
  default     = 365
}

# S3 bucket ARNs for task role permissions (optional)
variable "s3_originals_bucket_arn" {
  description = "ARN of the S3 originals bucket (for read/write access)"
  type        = string
  default     = ""
}

variable "s3_media_bucket_arn" {
  description = "ARN of the S3 media bucket (for read/write access)"
  type        = string
  default     = ""
}

# Fargate Spot (for interruptible workloads)
variable "use_spot" {
  description = "Use Fargate Spot for this service. Guarantees 1 on-demand task, rest use spot."
  type        = bool
  default     = false
}

# Service discovery (AWS Cloud Map) configuration
variable "service_discovery_registry_arn" {
  description = "ARN of the service discovery registry to register this service with"
  type        = string
  default     = null
}
