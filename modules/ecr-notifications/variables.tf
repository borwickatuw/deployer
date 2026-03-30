variable "name_prefix" {
  description = "Prefix for resource names (e.g., myapp-production)"
  type        = string
}

variable "sns_topic_arn" {
  description = "ARN of the SNS topic to send vulnerability notifications to"
  type        = string
}
