# Database User Creation Module
#
# Creates two database users with different privilege levels:
# - App user: DML only (SELECT, INSERT, UPDATE, DELETE)
# - Migrate user: DDL + DML (CREATE, ALTER, DROP, etc.)
#
# Uses a Lambda function to connect to PostgreSQL and create the users.
# Credentials are stored in Secrets Manager.

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names (e.g., myapp-staging)"
}

variable "db_host" {
  type        = string
  description = "Database hostname"
}

variable "db_port" {
  type        = number
  description = "Database port"
  default     = 5432
}

variable "db_name" {
  type        = string
  description = "Database name"
}

variable "master_secret_arn" {
  type        = string
  description = "ARN of the Secrets Manager secret containing master credentials"
}

variable "vpc_id" {
  type        = string
  description = "VPC ID where Lambda will run"
}

variable "subnet_ids" {
  type        = list(string)
  description = "Subnet IDs for Lambda VPC configuration"
}

variable "db_security_group_id" {
  type        = string
  description = "Security group ID for the database (Lambda will be allowed to connect)"
}

variable "tags" {
  type        = map(string)
  description = "Tags to apply to resources"
  default     = {}
}

# Generate random passwords for app and migrate users
resource "random_password" "app_password" {
  length  = 32
  special = false # Avoid URL encoding issues in connection strings
}

resource "random_password" "migrate_password" {
  length  = 32
  special = false
}

# Derive usernames from name_prefix (replace hyphens with underscores for PostgreSQL)
locals {
  app_username     = "${replace(var.name_prefix, "-", "_")}_app"
  migrate_username = "${replace(var.name_prefix, "-", "_")}_migrate"
}

# Store app credentials in Secrets Manager
resource "aws_secretsmanager_secret" "app_credentials" {
  name                    = "${var.name_prefix}/db-app-credentials"
  recovery_window_in_days = 7
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "app_credentials" {
  secret_id = aws_secretsmanager_secret.app_credentials.id
  secret_string = jsonencode({
    username = local.app_username
    password = random_password.app_password.result
    host     = var.db_host
    port     = var.db_port
    dbname   = var.db_name
  })

  lifecycle {
    ignore_changes = [secret_string] # Don't revert after manual rotation
  }
}

# Store migrate credentials in Secrets Manager
resource "aws_secretsmanager_secret" "migrate_credentials" {
  name                    = "${var.name_prefix}/db-migrate-credentials"
  recovery_window_in_days = 7
  tags                    = var.tags
}

resource "aws_secretsmanager_secret_version" "migrate_credentials" {
  secret_id = aws_secretsmanager_secret.migrate_credentials.id
  secret_string = jsonencode({
    username = local.migrate_username
    password = random_password.migrate_password.result
    host     = var.db_host
    port     = var.db_port
    dbname   = var.db_name
  })

  lifecycle {
    ignore_changes = [secret_string] # Don't revert after manual rotation
  }
}

# Security group for Lambda function
resource "aws_security_group" "lambda" {
  name        = "${var.name_prefix}-db-users-lambda"
  description = "Security group for db-users Lambda function"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-db-users-lambda"
  })
}

# Allow Lambda to connect to the database
resource "aws_security_group_rule" "lambda_to_db" {
  type                     = "ingress"
  from_port                = var.db_port
  to_port                  = var.db_port
  protocol                 = "tcp"
  security_group_id        = var.db_security_group_id
  source_security_group_id = aws_security_group.lambda.id
  description              = "Allow db-users Lambda to connect to PostgreSQL"
}

# IAM role for Lambda
resource "aws_iam_role" "lambda" {
  name = "${var.name_prefix}-db-users-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

# IAM policy for Lambda to access secrets and logs
resource "aws_iam_role_policy" "lambda" {
  name = "${var.name_prefix}-db-users-lambda"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          var.master_secret_arn,
          aws_secretsmanager_secret.app_credentials.arn,
          aws_secretsmanager_secret.migrate_credentials.arn,
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

# Attach VPC access policy for Lambda
resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Package the Lambda function code
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/lambda.zip"
}

# Lambda function to create database users
resource "aws_lambda_function" "create_db_users" {
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  function_name    = "${var.name_prefix}-create-db-users"
  role             = aws_iam_role.lambda.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      MASTER_SECRET_ARN  = var.master_secret_arn
      APP_SECRET_ARN     = aws_secretsmanager_secret.app_credentials.arn
      MIGRATE_SECRET_ARN = aws_secretsmanager_secret.migrate_credentials.arn
      DB_NAME            = var.db_name
    }
  }

  # Use Lambda layer for psycopg2
  layers = [
    "arn:aws:lambda:${data.aws_region.current.name}:898466741470:layer:psycopg2-py312:1"
  ]

  tags = var.tags

  depends_on = [
    aws_iam_role_policy.lambda,
    aws_iam_role_policy_attachment.lambda_vpc,
  ]
}

data "aws_region" "current" {}

# Trigger Lambda after secrets are created
resource "aws_lambda_invocation" "create_users" {
  function_name = aws_lambda_function.create_db_users.function_name

  input = jsonencode({
    action = "create_users"
  })

  depends_on = [
    aws_secretsmanager_secret_version.app_credentials,
    aws_secretsmanager_secret_version.migrate_credentials,
    aws_lambda_function.create_db_users,
    aws_security_group_rule.lambda_to_db,
  ]

  lifecycle {
    # Re-run if credentials change
    replace_triggered_by = [
      aws_secretsmanager_secret_version.app_credentials,
      aws_secretsmanager_secret_version.migrate_credentials,
    ]
  }
}
