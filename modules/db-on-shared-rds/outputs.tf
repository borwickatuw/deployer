# Outputs for db-on-shared-rds module

output "db_name" {
  description = "Name of the database created on the shared RDS instance"
  value       = var.db_name
}

output "db_host" {
  description = "Host of the shared RDS instance"
  value       = var.db_host
}

output "db_port" {
  description = "Port of the shared RDS instance"
  value       = var.db_port
}

output "app_secret_arn" {
  description = "ARN of the Secrets Manager secret for app credentials"
  value       = aws_secretsmanager_secret.app_credentials.arn
}

output "app_username_arn" {
  description = "ARN for app username (for ECS secrets)"
  value       = "${aws_secretsmanager_secret.app_credentials.arn}:username::"
}

output "app_password_arn" {
  description = "ARN for app password (for ECS secrets)"
  value       = "${aws_secretsmanager_secret.app_credentials.arn}:password::"
}

output "migrate_secret_arn" {
  description = "ARN of the Secrets Manager secret for migrate credentials"
  value       = aws_secretsmanager_secret.migrate_credentials.arn
}

output "migrate_username_arn" {
  description = "ARN for migrate username (for ECS secrets)"
  value       = "${aws_secretsmanager_secret.migrate_credentials.arn}:username::"
}

output "migrate_password_arn" {
  description = "ARN for migrate password (for ECS secrets)"
  value       = "${aws_secretsmanager_secret.migrate_credentials.arn}:password::"
}

output "app_username" {
  description = "The app database username"
  value       = local.app_username
}

output "migrate_username" {
  description = "The migrate database username"
  value       = local.migrate_username
}

output "lambda_invocation_result" {
  description = "Result of the Lambda invocation"
  value       = jsondecode(aws_lambda_invocation.setup_db.result)
}

output "lambda_function_name" {
  description = "Name of the Lambda function for setting up database/extensions"
  value       = aws_lambda_function.setup_db.function_name
}

output "lambda_function_arn" {
  description = "ARN of the Lambda function for setting up database/extensions"
  value       = aws_lambda_function.setup_db.arn
}
