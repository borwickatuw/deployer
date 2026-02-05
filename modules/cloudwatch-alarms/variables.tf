# CloudWatch Alarms Module - Variables
#
# Creates standard production alarms with SNS email notifications.

variable "name_prefix" {
  description = "Prefix for alarm names (e.g., myapp-production)"
  type        = string
}

variable "notification_email" {
  description = "Email address for alarm notifications"
  type        = string
}

# Feature toggles
variable "enable_alb_alarms" {
  description = "Enable ALB alarms"
  type        = bool
  default     = true
}

variable "enable_rds_alarms" {
  description = "Enable RDS alarms"
  type        = bool
  default     = true
}

variable "enable_elasticache_alarms" {
  description = "Enable ElastiCache alarms"
  type        = bool
  default     = true
}

variable "enable_ecs_alarms" {
  description = "Enable ECS alarms"
  type        = bool
  default     = true
}

# Resource identifiers (required when corresponding alarms are enabled)
variable "alb_arn_suffix" {
  description = "ALB ARN suffix (from aws_lb.arn_suffix)"
  type        = string
  default     = ""
}

variable "target_group_arn_suffix" {
  description = "Target group ARN suffix (from aws_lb_target_group.arn_suffix)"
  type        = string
  default     = ""
}

variable "rds_instance_id" {
  description = "RDS instance identifier"
  type        = string
  default     = ""
}

variable "elasticache_cluster_id" {
  description = "ElastiCache cluster identifier"
  type        = string
  default     = ""
}

variable "ecs_cluster_name" {
  description = "ECS cluster name"
  type        = string
  default     = ""
}

variable "ecs_service_names" {
  description = "List of ECS service names to monitor"
  type        = list(string)
  default     = []
}

# Threshold customization
variable "alb_5xx_threshold" {
  description = "ALB 5XX error count threshold (5 min period)"
  type        = number
  default     = 10
}

variable "alb_latency_threshold" {
  description = "ALB target response time threshold in seconds (p95)"
  type        = number
  default     = 5
}

variable "rds_cpu_threshold" {
  description = "RDS CPU utilization threshold percentage"
  type        = number
  default     = 80
}

variable "rds_storage_threshold" {
  description = "RDS free storage threshold in bytes (default 10GB)"
  type        = number
  default     = 10737418240 # 10 GB in bytes
}

variable "rds_connections_threshold_percent" {
  description = "RDS connections threshold as percentage of max_connections"
  type        = number
  default     = 80
}

variable "elasticache_memory_threshold" {
  description = "ElastiCache memory usage threshold percentage"
  type        = number
  default     = 80
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
