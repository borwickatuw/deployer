variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "allocated_storage" {
  type    = number
  default = 20
}

variable "database_name" {
  type = string
}

variable "master_username" {
  type      = string
  sensitive = true
}

variable "master_password" {
  type      = string
  sensitive = true
}

variable "ecs_security_group" {
  description = "Security group ID for ECS tasks (allowed to connect)"
  type        = string
}

# Production settings - override these for production environments
variable "backup_retention_period" {
  description = "Number of days to retain automated backups (7 for staging, 35 for production)"
  type        = number
  default     = 7
}

variable "skip_final_snapshot" {
  description = "Skip final snapshot on deletion (true for staging, false for production)"
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "Prevent accidental deletion (false for staging, true for production)"
  type        = bool
  default     = false
}

variable "multi_az" {
  description = "Enable Multi-AZ deployment for automatic failover (false for staging, true for production)"
  type        = bool
  default     = false
}

variable "performance_insights_enabled" {
  description = "Enable RDS Performance Insights (free tier for 7 days retention on db.t3+)"
  type        = bool
  default     = true
}

variable "monitoring_interval" {
  description = "Enhanced monitoring interval in seconds (0 = disabled, 1/5/10/15/30/60)"
  type        = number
  default     = 60
}

variable "permissions_boundary" {
  description = "IAM permissions boundary ARN (for monitoring IAM role)"
  type        = string
  default     = null
}

variable "storage_encrypted" {
  description = "Enable storage encryption at rest (cannot be changed in-place on existing instances)"
  type        = bool
  default     = true
}


# Subnet group
resource "aws_db_subnet_group" "main" {
  name       = "${var.name_prefix}-db-subnet"
  subnet_ids = var.subnet_ids

  tags = {
    Name = "${var.name_prefix}-db-subnet"
  }
}

# Security group
resource "aws_security_group" "rds" {
  name        = "${var.name_prefix}-rds"
  description = "Security group for RDS"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-rds-sg"
  }
}

# Use standalone rules to avoid conflicts with other modules adding rules
resource "aws_security_group_rule" "ecs_to_rds" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds.id
  source_security_group_id = var.ecs_security_group
  description              = "PostgreSQL access from ECS tasks"
}

# IAM Role for RDS Enhanced Monitoring
resource "aws_iam_role" "rds_monitoring" {
  count = var.monitoring_interval > 0 ? 1 : 0
  name  = "${var.name_prefix}-rds-monitoring"

  permissions_boundary = var.permissions_boundary

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
    }]
  })

  tags = {
    Name = "${var.name_prefix}-rds-monitoring"
  }
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  count      = var.monitoring_interval > 0 ? 1 : 0
  role       = aws_iam_role.rds_monitoring[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

resource "aws_db_parameter_group" "main" {
  name   = "${var.name_prefix}-postgres15"
  family = "postgres15"

  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  tags = {
    Name = "${var.name_prefix}-postgres15"
  }
}

resource "aws_db_instance" "main" {
  identifier = "${var.name_prefix}-db"

  engine         = "postgres"
  engine_version = "15"

  instance_class    = var.instance_class
  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = var.storage_encrypted

  db_name  = var.database_name
  username = var.master_username
  password = var.master_password

  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.rds.id]
  parameter_group_name            = aws_db_parameter_group.main.name
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  publicly_accessible = false

  # Backup and protection settings (override for production)
  backup_retention_period = var.backup_retention_period
  skip_final_snapshot     = var.skip_final_snapshot
  deletion_protection     = var.deletion_protection
  multi_az                = var.multi_az

  # Performance Insights and Enhanced Monitoring
  performance_insights_enabled = var.performance_insights_enabled
  monitoring_interval          = var.monitoring_interval
  monitoring_role_arn          = var.monitoring_interval > 0 ? aws_iam_role.rds_monitoring[0].arn : null

  auto_minor_version_upgrade = true
  copy_tags_to_snapshot      = true

  backup_window      = "03:00-04:00"
  maintenance_window = "Mon:04:00-Mon:05:00"

  tags = {
    Name = "${var.name_prefix}-db"
  }
}

# Outputs
output "endpoint" {
  value = aws_db_instance.main.endpoint
}

output "address" {
  value = aws_db_instance.main.address
}

output "port" {
  value = aws_db_instance.main.port
}

output "connection_url" {
  value     = "postgres://${var.master_username}:${var.master_password}@${aws_db_instance.main.endpoint}/${var.database_name}"
  sensitive = true
}

output "security_group_id" {
  value = aws_security_group.rds.id
}

output "db_instance_id" {
  description = "RDS instance identifier (for AWS CLI commands)"
  value       = aws_db_instance.main.identifier
}
