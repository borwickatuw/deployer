# App in Shared Environment Module
#
# Creates per-app resources that use shared infrastructure:
# - RDS database (separate per app)
# - ALB target group and listener rule
# - ECR repository
# - IAM roles (task execution and task)
#
# References shared infrastructure via terraform_remote_state

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
}

# ------------------------------------------------------------------------------
# RDS Database (per-app)
# ------------------------------------------------------------------------------

module "rds" {
  source = "../rds"

  name_prefix        = local.name_prefix
  vpc_id             = local.shared.vpc_id
  subnet_ids         = local.shared.private_subnet_ids
  ecs_security_group = local.shared.ecs_security_group_id

  instance_class    = var.db_instance_class
  allocated_storage = var.db_allocated_storage
  database_name     = var.db_name != null ? var.db_name : replace(var.app_name, "-", "_")
  master_username   = var.db_username
  master_password   = var.db_password
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
