# Database Master Credentials stored in AWS Secrets Manager
#
# Stores the RDS master credentials as a JSON object with fields:
# - username, password, host, port, dbname
#
# These are the master/admin credentials for emergency access and for the
# db-users module to create app and migrate users.
#
# For runtime services, use the db-users module which creates separate
# app (DML-only) and migrate (DDL+DML) users with limited privileges.

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
  name                    = "${var.name_prefix}/db-master-credentials"
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
  description = "ARN of the Secrets Manager secret containing master credentials"
  value       = aws_secretsmanager_secret.db_credentials.arn
}

output "master_secret_arn" {
  description = "ARN of the master credentials secret (for db-users module)"
  value       = aws_secretsmanager_secret.db_credentials.arn
}

output "master_password_arn" {
  description = "ARN for master database password (for emergency access)"
  value       = "${aws_secretsmanager_secret.db_credentials.arn}:password::"
}

output "master_username_arn" {
  description = "ARN for master database username (for emergency access)"
  value       = "${aws_secretsmanager_secret.db_credentials.arn}:username::"
}

# Legacy outputs for backward compatibility during migration
# These will be removed after all environments migrate to db-users module
output "password_arn" {
  description = "DEPRECATED: Use master_password_arn instead"
  value       = "${aws_secretsmanager_secret.db_credentials.arn}:password::"
}

output "username_arn" {
  description = "DEPRECATED: Use master_username_arn instead"
  value       = "${aws_secretsmanager_secret.db_credentials.arn}:username::"
}

output "host_arn" {
  description = "ARN for database host (for ECS secrets)"
  value       = "${aws_secretsmanager_secret.db_credentials.arn}:host::"
}
