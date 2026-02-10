variable "environment_name" {
  type        = string
  description = "Name of the staging environment (e.g., myapp-staging)"
}

variable "ecs_cluster_name" {
  type        = string
  description = "Name of the ECS cluster"
}

variable "ecs_services" {
  type = map(object({
    replicas = number
  }))
  description = "Map of ECS service names to their configured replica counts"
}

variable "rds_instance_id" {
  type        = string
  description = "RDS instance identifier"
}

variable "stop_schedule" {
  type        = string
  description = "Cron expression for stopping the environment (UTC). Default: 7 PM Pacific Mon-Fri = 3 AM UTC Tue-Sat"
  default     = "cron(0 3 ? * TUE-SAT *)"
}

variable "start_schedule" {
  type        = string
  description = "Cron expression for starting the environment (UTC). Default: 7 AM Pacific Mon-Fri = 3 PM UTC Mon-Fri"
  default     = "cron(0 15 ? * MON-FRI *)"
}

variable "enabled" {
  type        = bool
  description = "Whether scheduling is enabled"
  default     = true
}

variable "permissions_boundary" {
  type        = string
  description = "ARN of the permissions boundary policy for the Lambda IAM role"
  default     = null
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 365
}
