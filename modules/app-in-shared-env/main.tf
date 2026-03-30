# App in Shared Environment Module
#
# Creates per-app resources that use shared infrastructure:
# - Database (either separate RDS instance OR database on shared RDS)
# - ALB target group and listener rule
# - ECR repository
# - IAM roles (task execution and task)
#
# References shared infrastructure via terraform_remote_state
#
# Database modes:
# - use_shared_rds = false (default): Creates a separate RDS instance per app
# - use_shared_rds = true: Creates a database on the shared RDS instance
#   (requires shared_rds_enabled = true in shared infrastructure)

terraform {
  required_version = ">= 1.6.0"
}

# ------------------------------------------------------------------------------
# Data: Shared Infrastructure State
# ------------------------------------------------------------------------------

data "terraform_remote_state" "shared" {
  backend = var.shared_state_backend

  config = var.shared_state_backend == "local" ? {
    path = var.shared_state_path
  } : var.shared_state_config
}

locals {
  shared      = data.terraform_remote_state.shared.outputs
  name_prefix = "${var.app_name}-${var.environment}"
  db_name     = var.db_name != null ? var.db_name : replace(var.app_name, "-", "_")
}

# ------------------------------------------------------------------------------
# RDS Database - Separate Instance Mode (use_shared_rds = false)
# ------------------------------------------------------------------------------

module "rds" {
  source = "../rds"
  count  = var.use_shared_rds ? 0 : 1

  name_prefix        = local.name_prefix
  vpc_id             = local.shared.vpc_id
  subnet_ids         = local.shared.private_subnet_ids
  ecs_security_group = local.shared.ecs_security_group_id

  instance_class    = var.db_instance_class
  allocated_storage = var.db_allocated_storage
  database_name     = local.db_name
  master_username   = var.db_username
  master_password   = var.db_password
}

# ------------------------------------------------------------------------------
# Database on Shared RDS Mode (use_shared_rds = true)
#
# Creates a DATABASE on the shared RDS instance with isolated users.
# Each app gets its own database and credentials - PostgreSQL's permission
# model ensures complete data isolation.
# ------------------------------------------------------------------------------

module "db_on_shared_rds" {
  source = "../db-on-shared-rds"
  count  = var.use_shared_rds ? 1 : 0

  name_prefix          = local.name_prefix
  db_name              = local.db_name
  db_host              = local.shared.shared_rds_address
  db_port              = local.shared.shared_rds_port
  master_secret_arn    = local.shared.shared_rds_master_secret_arn
  vpc_id               = local.shared.vpc_id
  subnet_ids           = local.shared.private_subnet_ids
  db_security_group_id = local.shared.shared_rds_security_group_id

  tags = {
    App         = var.app_name
    Environment = var.environment
  }
}

# ------------------------------------------------------------------------------
# ECR Repository (per-app)
# ------------------------------------------------------------------------------

module "ecr" {
  source = "../ecr"

  name_prefix      = local.name_prefix
  repository_names = var.ecr_repository_names
}

# ------------------------------------------------------------------------------
# Route53 DNS Record (per-app subdomain)
# ------------------------------------------------------------------------------

resource "aws_route53_record" "app" {
  count = var.route53_zone_id != null ? 1 : 0

  zone_id = var.route53_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = local.shared.alb_dns_name
    zone_id                = local.shared.alb_zone_id
    evaluate_target_health = true
  }
}
