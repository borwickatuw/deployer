# Database credentials stored in AWS Secrets Manager
#
# Stores database credentials as a JSON object with fields:
# - username, password, host, port, dbname
#
# ECS tasks can inject individual fields using the "secretsmanager:" ARN format.

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names (e.g., myapp-staging)"
}

variable "db_username" {
  type      = string
  sensitive = true
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "db_host" {
  type        = string
  description = "Database hostname"
}

variable "db_port" {
  type    = number
  default = 5432
}

variable "db_name" {
  type        = string
  description = "Database name"
}

resource "aws_secretsmanager_secret" "db_credentials" {
  name                    = "${var.name_prefix}/db-credentials"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id = aws_secretsmanager_secret.db_credentials.id
  secret_string = jsonencode({
    username = var.db_username
    password = var.db_password
    host     = var.db_host
    port     = var.db_port
    dbname   = var.db_name
  })

  lifecycle {
    ignore_changes = [secret_string] # Don't revert after manual rotation
  }
}

output "secret_arn" {
  description = "ARN of the Secrets Manager secret"
  value       = aws_secretsmanager_secret.db_credentials.arn
}

output "password_arn" {
  description = "ARN for database password (for ECS secrets)"
  value       = "${aws_secretsmanager_secret.db_credentials.arn}:password::"
}

output "username_arn" {
  description = "ARN for database username (for ECS secrets)"
  value       = "${aws_secretsmanager_secret.db_credentials.arn}:username::"
}

output "host_arn" {
  description = "ARN for database host (for ECS secrets)"
  value       = "${aws_secretsmanager_secret.db_credentials.arn}:host::"
}
