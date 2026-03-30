variable "name_prefix" {
  description = "Prefix for resource names (e.g., myapp-production)"
  type        = string
}

variable "budget_amount" {
  description = "Monthly budget threshold in USD"
  type        = number
}

variable "notification_email" {
  description = "Email address to receive budget alerts"
  type        = string
}
