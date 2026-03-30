# Shared Infrastructure Module
#
# Creates expensive shared resources for multiple apps:
# - VPC with NAT Gateway
# - ECS Cluster
# - ALB (without default target group - apps create their own)
# - Optional: Cognito for staging authentication
# - Optional: Shared ElastiCache
#
# Per-app resources (RDS, target groups, listener rules) are created
# by the app-in-shared-env module.

terraform {
  required_version = ">= 1.6.0"
}

# ------------------------------------------------------------------------------
# VPC
# ------------------------------------------------------------------------------

module "vpc" {
  source = "../vpc"

  name_prefix        = var.name_prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones

  # Flow logs
  flow_logs_enabled    = var.vpc_flow_logs_enabled
  permissions_boundary = var.permissions_boundary
}

# ------------------------------------------------------------------------------
# ECS Cluster
# ------------------------------------------------------------------------------

module "ecs_cluster" {
  source = "../ecs-cluster"

  name_prefix = var.name_prefix
  vpc_id      = module.vpc.vpc_id
}

# ------------------------------------------------------------------------------
# ACM Certificate (for HTTPS)
# ------------------------------------------------------------------------------

# Create certificate if Route53 zone provided (allows DNS validation)
resource "aws_acm_certificate" "main" {
  count = var.route53_zone_id != null ? 1 : 0

  domain_name               = var.domain_name
  subject_alternative_names = var.certificate_san
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.name_prefix}-cert"
  }
}

# DNS validation records
resource "aws_route53_record" "cert_validation" {
  for_each = var.route53_zone_id != null ? {
    for dvo in aws_acm_certificate.main[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id = var.route53_zone_id
  name    = each.value.name
  type    = each.value.type
  records = [each.value.record]
  ttl     = 60

  allow_overwrite = true
}

# Wait for certificate validation
resource "aws_acm_certificate_validation" "main" {
  count = var.route53_zone_id != null ? 1 : 0

  certificate_arn         = aws_acm_certificate.main[0].arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

locals {
  certificate_arn = var.certificate_arn != null ? var.certificate_arn : (
    var.route53_zone_id != null ? aws_acm_certificate_validation.main[0].certificate_arn : null
  )
  https_enabled = local.certificate_arn != null
}

# ------------------------------------------------------------------------------
# Cognito (optional, typically for staging)
# ------------------------------------------------------------------------------

module "cognito" {
  source = "../cognito"
  count  = var.cognito_auth_enabled ? 1 : 0

  name_prefix = var.name_prefix
  domain_name = var.domain_name
}

locals {
  cognito_auth = var.cognito_auth_enabled && length(module.cognito) > 0 ? {
    user_pool_arn       = module.cognito[0].user_pool_arn
    user_pool_client_id = module.cognito[0].client_id
    user_pool_domain    = module.cognito[0].domain
  } : null
}

# ------------------------------------------------------------------------------
# Application Load Balancer
# ------------------------------------------------------------------------------

module "alb" {
  source = "../alb"

  name_prefix       = var.name_prefix
  vpc_id            = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids
  certificate_arn   = local.certificate_arn
  cognito_auth      = local.cognito_auth

  # ALB settings
  idle_timeout = var.health_check.idle_timeout

  # Health check settings (staging-optimized by default)
  default_health_check_path = var.health_check.path
  health_check_interval     = var.health_check.interval
  health_check_timeout      = var.health_check.timeout
  healthy_threshold         = var.health_check.healthy_threshold
  unhealthy_threshold       = var.health_check.unhealthy_threshold
  deregistration_delay      = var.health_check.deregistration_delay

  # Deletion protection and access logging
  deletion_protection = var.alb_deletion_protection
  access_logs_enabled = var.alb_access_logs_enabled
  access_logs_bucket  = var.alb_access_logs_bucket
  access_logs_prefix  = var.alb_access_logs_prefix
}

# Allow ALB to reach ECS tasks
resource "aws_security_group_rule" "ecs_from_alb" {
  type                     = "ingress"
  from_port                = 0
  to_port                  = 65535
  protocol                 = "tcp"
  source_security_group_id = module.alb.security_group_id
  security_group_id        = module.ecs_cluster.security_group_id
  description              = "Allow inbound from ALB"
}

# ------------------------------------------------------------------------------
# Route53 DNS Record (optional)
# ------------------------------------------------------------------------------

module "route53" {
  source = "../route53"
  count  = var.route53_zone_id != null ? 1 : 0

  zone_id = var.route53_zone_id
  records = {
    main = {
      type = "A"
      name = var.domain_name
      alias_target = {
        dns_name = module.alb.dns_name
        zone_id  = module.alb.zone_id
      }
    }
  }
}

# ------------------------------------------------------------------------------
# Shared ElastiCache (optional)
# ------------------------------------------------------------------------------

module "elasticache" {
  source = "../elasticache"
  count  = var.cache_enabled ? 1 : 0

  name_prefix        = var.name_prefix
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  ecs_security_group = module.ecs_cluster.security_group_id
  node_type          = var.cache_node_type
}

# ------------------------------------------------------------------------------
# WAF (optional, controlled by waf_preset)
# ------------------------------------------------------------------------------

locals {
  waf_enabled   = var.waf_preset != "off"
  waf_is_strict = var.waf_preset == "strict"

  waf_config = {
    ip_reputation_enabled    = true
    common_rules_enabled     = true
    known_bad_inputs_enabled = true
    sqli_rules_enabled       = local.waf_is_strict
    rate_limit_enabled       = true
    rate_limit_requests      = local.waf_is_strict ? 1000 : 2000
    bot_control_level        = local.waf_is_strict ? "common" : "none"
    common_rules_excluded    = []
    rule_action_override     = "none"
  }

  waf = {
    for key, default_val in local.waf_config :
    key => lookup(var.waf_overrides, key, default_val)
  }
}

module "waf" {
  source = "../waf"
  count  = local.waf_enabled ? 1 : 0

  name_prefix = var.name_prefix
  alb_arn     = module.alb.arn

  # Protection rules (from preset + overrides)
  ip_reputation_enabled    = local.waf.ip_reputation_enabled
  common_rules_enabled     = local.waf.common_rules_enabled
  common_rules_excluded    = local.waf.common_rules_excluded
  known_bad_inputs_enabled = local.waf.known_bad_inputs_enabled
  sqli_rules_enabled       = local.waf.sqli_rules_enabled

  # Rate limiting
  rate_limit_enabled  = local.waf.rate_limit_enabled
  rate_limit_requests = local.waf.rate_limit_requests

  # Bot control (paid tier)
  bot_control_level = local.waf.bot_control_level

  # Geographic and IP rules
  geo_block_countries = var.waf_geo_block_countries
  ip_allowlist        = var.waf_ip_allowlist

  # Deployment mode
  rule_action_override = local.waf.rule_action_override
}

# ------------------------------------------------------------------------------
# Service Discovery (AWS Cloud Map)
#
# Creates a private DNS namespace for internal service-to-service communication.
# Services can communicate via DNS names like "web.{namespace}" without going
# through the ALB (avoiding Cognito auth for internal API calls).
# ------------------------------------------------------------------------------

resource "aws_service_discovery_private_dns_namespace" "main" {
  count = var.service_discovery_enabled ? 1 : 0

  name        = "${var.name_prefix}.local"
  description = "Private DNS namespace for ${var.name_prefix} service discovery"
  vpc         = module.vpc.vpc_id

  tags = {
    Name = "${var.name_prefix}-service-discovery"
  }
}

# ------------------------------------------------------------------------------
# Shared RDS Instance (optional)
#
# When enabled, creates a single RDS instance that multiple applications can
# share. Each app gets its own DATABASE on this instance via the
# db-on-shared-rds module, with complete data isolation through PostgreSQL's
# permission model.
#
# Cost savings: Instead of N separate db.t3.micro instances (~$15/month each),
# one larger shared instance can host multiple small apps more efficiently.
# ------------------------------------------------------------------------------

module "shared_rds" {
  source = "../rds"
  count  = var.shared_rds_enabled ? 1 : 0

  name_prefix        = var.name_prefix
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  ecs_security_group = module.ecs_cluster.security_group_id

  instance_class    = var.shared_rds_instance_class
  allocated_storage = var.shared_rds_allocated_storage

  # The master credentials are for admin access and creating per-app databases
  # Each app will have its own database and users created by db-on-shared-rds
  database_name   = "postgres" # Default admin database
  master_username = var.shared_rds_master_username
  master_password = var.shared_rds_master_password

  # Production settings
  backup_retention_period = var.shared_rds_backup_retention_period
  skip_final_snapshot     = var.shared_rds_skip_final_snapshot
  deletion_protection     = var.shared_rds_deletion_protection
  multi_az                = var.shared_rds_multi_az

  # Monitoring
  performance_insights_enabled = var.shared_rds_performance_insights
  monitoring_interval          = var.shared_rds_monitoring_interval
  permissions_boundary         = var.permissions_boundary

  # Encryption
  storage_encrypted = var.shared_rds_storage_encrypted
}

# Store master credentials in Secrets Manager for the db-on-shared-rds module
module "shared_rds_secrets" {
  source = "../db-secrets"
  count  = var.shared_rds_enabled ? 1 : 0

  name_prefix = var.name_prefix
  db_username = var.shared_rds_master_username
  db_password = var.shared_rds_master_password
  db_host     = module.shared_rds[0].address
  db_port     = module.shared_rds[0].port
  db_name     = "postgres"
}
