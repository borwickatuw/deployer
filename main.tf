# Main infrastructure configuration
#
# This creates the shared infrastructure for an environment.
# Individual applications are deployed using the deploy script
# which reads application-specific .toml config files.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "opentofu"
    }
  }
}

# Provider for us-east-1 (required for CloudFront ACM certificates)
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "opentofu"
    }
  }
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  # Determine certificate ARN: use provided ARN, or create via ACM module
  create_certificate = var.domain_name != null && var.route53_zone_id != null && var.certificate_arn == null
  certificate_arn    = var.certificate_arn != null ? var.certificate_arn : (local.create_certificate ? module.acm[0].certificate_arn : null)

  # Whether a CloudFront distribution actually fronts the ALB: the flag alone
  # is not enough, the distribution also needs a domain and a zone.
  cloudfront_alb_enabled = var.cloudfront_alb_enabled && var.domain_name != null && var.route53_zone_id != null

  # Cognito authentication configuration
  # Prefer external cognito_auth if provided, otherwise create local pool if enabled
  create_local_cognito = var.cognito_auth == null && var.cognito_auth_enabled
  cognito_auth_config = var.cognito_auth != null ? var.cognito_auth : (
    local.create_local_cognito ? {
      user_pool_arn       = module.cognito[0].user_pool_arn
      user_pool_client_id = module.cognito[0].client_id
      user_pool_domain    = module.cognito[0].domain
    } : null
  )

  # Services needing their own target group (have path_pattern + port)
  service_routes = {
    for name, svc in var.services : name => {
      port                 = svc.port
      path_pattern         = svc.path_pattern
      health_check_path    = svc.health_check_path
      health_check_matcher = svc.health_check_matcher
    } if svc.path_pattern != null && svc.port != null
  }

  # Auto-assign listener rule priorities: 10, 20, 30, ...
  service_routes_with_priority = {
    for idx, name in sort(keys(local.service_routes)) : name => merge(
      local.service_routes[name], { priority = (idx + 1) * 10 }
    )
  }

  # All unique ports from load-balanced services (for SG rules)
  alb_ingress_ports = length(var.services) > 0 ? toset([
    for name, svc in var.services : tostring(svc.port)
    if svc.port != null && svc.load_balanced
  ]) : toset([tostring(var.container_port)])

  # Services that need service discovery
  discovery_services = {
    for name, svc in var.services : name => true
    if svc.service_discovery && var.service_discovery_enabled
  }
}

# VPC and networking
module "vpc" {
  source = "./modules/vpc"

  name_prefix        = local.name_prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones

  # Flow logs
  flow_logs_enabled    = var.vpc_flow_logs_enabled
  permissions_boundary = var.iam_permissions_boundary
}

# ECS Cluster
module "ecs_cluster" {
  source = "./modules/ecs-cluster"

  name_prefix = local.name_prefix
  vpc_id      = module.vpc.vpc_id
}

# CloudWatch Log Group for ECS tasks
resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name_prefix}-ecs-logs"
  }
}

# Application Load Balancer
module "alb" {
  source = "./modules/alb"

  name_prefix       = local.name_prefix
  vpc_id            = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids

  # HTTPS configuration
  certificate_arn = local.certificate_arn

  # Health check configuration
  default_health_check_path = var.health_check.path
  health_check_interval     = var.health_check.interval
  health_check_timeout      = var.health_check.timeout
  healthy_threshold         = var.health_check.healthy_threshold
  unhealthy_threshold       = var.health_check.unhealthy_threshold
  deregistration_delay      = var.health_check.deregistration_delay

  # Idle timeout - increase for large file uploads
  idle_timeout = var.health_check.idle_timeout

  # Deletion protection
  deletion_protection = var.alb_deletion_protection

  # Accept traffic only from CloudFront (closes direct access to the ALB)
  restrict_ingress_to_cloudfront = var.alb_restrict_ingress_to_cloudfront

  # Cognito authentication (optional)
  # Prefer external cognito_auth if provided, otherwise use local pool if enabled
  cognito_auth = local.cognito_auth_config

  # Paths that bypass Cognito (each enforces its own access in the app)
  unauthenticated_path_patterns = var.unauthenticated_path_patterns

  # Additional target groups for path-based routing (derived from services variable)
  additional_target_groups = local.service_routes_with_priority
}

# RDS PostgreSQL
module "rds" {
  source = "./modules/rds"

  name_prefix        = local.name_prefix
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  instance_class     = var.db_instance_class
  allocated_storage  = var.db_allocated_storage
  database_name      = var.db_name
  master_username    = var.db_username
  master_password    = var.db_password
  ecs_security_group = module.ecs_cluster.security_group_id

  # Backup and protection (override for production environments)
  backup_retention_period = var.rds_backup_retention_period
  skip_final_snapshot     = var.rds_skip_final_snapshot
  deletion_protection     = var.rds_deletion_protection
  multi_az                = var.rds_multi_az

  # Monitoring
  performance_insights_enabled = var.rds_performance_insights
  monitoring_interval          = var.rds_monitoring_interval
  permissions_boundary         = var.iam_permissions_boundary

  # Encryption
  storage_encrypted = var.rds_storage_encrypted
}

# Database credentials in Secrets Manager (for ECS secrets injection)
module "db_secrets" {
  source = "./modules/db-secrets"

  name_prefix = local.name_prefix
  db_username = var.db_username
  db_password = var.db_password
  db_host     = module.rds.address
  db_port     = module.rds.port
  db_name     = var.db_name
}

# Database Users (app with DML-only, migrate with DDL+DML)
#
# Creates two database users with different privilege levels:
# - App user: DML only (SELECT, INSERT, UPDATE, DELETE) - for runtime services
# - Migrate user: DDL + DML (CREATE, ALTER, DROP) - for migrations only
#
# This reduces blast radius if the application is compromised.
module "db_users" {
  source = "./modules/db-users"

  name_prefix          = local.name_prefix
  db_host              = module.rds.address
  db_port              = module.rds.port
  db_name              = var.db_name
  master_secret_arn    = module.db_secrets.master_secret_arn
  vpc_id               = module.vpc.vpc_id
  subnet_ids           = module.vpc.private_subnet_ids
  db_security_group_id = module.rds.security_group_id
  permissions_boundary = var.iam_permissions_boundary

  depends_on = [module.rds, module.db_secrets]
}

# ElastiCache Redis (optional)
module "elasticache" {
  source = "./modules/elasticache"
  count  = var.cache_enabled ? 1 : 0

  name_prefix        = local.name_prefix
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  node_type          = var.cache_node_type
  ecs_security_group = module.ecs_cluster.security_group_id
}

# S3 Buckets
module "s3" {
  source   = "./modules/s3"
  for_each = var.s3_buckets

  name_prefix          = local.name_prefix
  bucket_name          = each.key
  versioning           = each.value.versioning
  public               = each.value.public
  cors_allowed_origins = each.value.cors_allowed_origins
  cors_allowed_methods = each.value.cors_allowed_methods
}

# ACM Certificate (when using Route 53)
module "acm" {
  source = "./modules/acm"
  count  = local.create_certificate ? 1 : 0

  domain_name     = var.domain_name
  route53_zone_id = var.route53_zone_id
}

# Cognito User Pool (for staging authentication)
# Only created if cognito_auth_enabled and no external cognito_auth is provided
module "cognito" {
  source = "./modules/cognito"
  count  = local.create_local_cognito ? 1 : 0

  name_prefix = local.name_prefix
  domain_name = var.domain_name
}

# ECR Repositories (optional)
module "ecr" {
  source = "./modules/ecr"
  count  = length(var.ecr_repository_names) > 0 ? 1 : 0

  name_prefix            = local.name_prefix
  repository_names       = var.ecr_repository_names
  lifecycle_policy_count = var.ecr_lifecycle_policy_count
  scan_on_push           = var.ecr_scan_on_push
  image_tag_mutability   = var.ecr_image_tag_mutability
  force_delete           = var.ecr_force_delete
}

# Route 53 DNS records
module "route53" {
  source = "./modules/route53"
  count  = var.domain_name != null && var.route53_zone_id != null ? 1 : 0

  zone_id = var.route53_zone_id
  records = merge(
    # Main domain pointing to CloudFront (if enabled) or ALB
    {
      main = {
        type = "A"
        name = var.domain_name
        alias_target = var.cloudfront_alb_enabled ? {
          dns_name               = module.cloudfront_alb[0].distribution_domain_name
          zone_id                = module.cloudfront_alb[0].distribution_hosted_zone_id
          evaluate_target_health = false
          } : {
          dns_name               = module.alb.dns_name
          zone_id                = module.alb.zone_id
          evaluate_target_health = true
        }
      }
    },
    # Additional DNS records from variable
    var.additional_dns_records,
  )
}

# ECS Task Execution Role (for pulling images from ECR and writing logs)
resource "aws_iam_role" "ecs_task_execution" {
  name                 = "${local.name_prefix}-ecs-execution"
  permissions_boundary = var.iam_permissions_boundary

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })

  tags = {
    Name = "${local.name_prefix}-ecs-execution"
  }
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow reading secrets from SSM Parameter Store, Secrets Manager, and creating logs
resource "aws_iam_role_policy" "ecs_ssm_access" {
  name = "${local.name_prefix}-ecs-ssm-access"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameters",
          "ssm:GetParameter"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.project_name}/${var.environment}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:*:secret:${local.name_prefix}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:*:log-group:/ecs/${local.name_prefix}/*"
      }
    ]
  })
}

# ECS Task Role (for application-level permissions)
resource "aws_iam_role" "ecs_task" {
  name                 = "${local.name_prefix}-ecs-task"
  permissions_boundary = var.iam_permissions_boundary

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })

  tags = {
    Name = "${local.name_prefix}-ecs-task"
  }
}

# Allow task to access S3 buckets (if any are configured)
resource "aws_iam_role_policy" "ecs_task_s3" {
  count = length(var.s3_buckets) > 0 ? 1 : 0
  name  = "${local.name_prefix}-ecs-s3-access"
  role  = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:HeadObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
      ]
      Resource = concat(
        [for k, v in module.s3 : v.bucket_arn],
        [for k, v in module.s3 : "${v.bucket_arn}/*"]
      )
    }]
  })
}

# Queue-depth autoscaling signals: the app publishes its queue depth and
# protects busy workers from scale-in. It can signal, never scale — the
# scaling decisions live in Application Auto Scaling policies applied by
# deploy.py, bounded by the environment's tfvars.
resource "aws_iam_role_policy" "ecs_task_autoscale_signals" {
  name = "${local.name_prefix}-ecs-autoscale-signals"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "cloudwatch:namespace" = local.name_prefix
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:UpdateTaskProtection"]
        Resource = "arn:aws:ecs:${var.aws_region}:*:task/${local.name_prefix}-cluster/*"
      }
    ]
  })
}

# Allow ALB to reach ECS tasks on application ports
resource "aws_security_group_rule" "alb_to_ecs" {
  for_each                 = local.alb_ingress_ports
  type                     = "ingress"
  from_port                = tonumber(each.value)
  to_port                  = tonumber(each.value)
  protocol                 = "tcp"
  source_security_group_id = module.alb.security_group_id
  security_group_id        = module.ecs_cluster.security_group_id
  description              = "Allow ALB to reach ECS on port ${each.value}"
}

# WAF (optional, controlled by waf_preset)
#
# Presets:
#   off      - No WAF
#   standard - IP reputation + common rules + known bad inputs + rate limit (2000/5min)
#   strict   - Standard + SQLi rules + bot control (common) + lower rate limit (1000/5min)

locals {
  waf_enabled   = var.waf_preset != "off"
  waf_is_strict = var.waf_preset == "strict"

  # Preset defaults (overridable via waf_overrides)
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

  # Merge overrides on top of preset defaults
  waf = {
    for key, default_val in local.waf_config :
    key => lookup(var.waf_overrides, key, default_val)
  }

  # Behind CloudFront the WAF's rate rule counts per viewer IP, read from a
  # header CloudFront's viewer-IP function writes. That header is only
  # trustworthy when the ALB accepts traffic from CloudFront alone, so both
  # switches must be on; otherwise the rule keeps counting per TCP source.
  waf_behind_cloudfront = local.cloudfront_alb_enabled && var.alb_restrict_ingress_to_cloudfront
  viewer_ip_header      = "x-viewer-ip"
}

check "waf_rate_rule_behind_cloudfront" {
  assert {
    condition     = !(local.waf_enabled && local.waf.rate_limit_enabled && local.cloudfront_alb_enabled) || var.alb_restrict_ingress_to_cloudfront
    error_message = "The WAF rate rule is counting per CloudFront edge address, not per viewer: every viewer behind one edge shares a counter. Set alb_restrict_ingress_to_cloudfront = true to count per viewer IP (this closes direct access to the ALB's DNS name; see docs/operations/PRODUCTION.md)."
  }
}

module "waf" {
  source = "./modules/waf"
  count  = local.waf_enabled ? 1 : 0

  name_prefix = local.name_prefix
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

  # Count per viewer IP behind CloudFront (see local.waf_behind_cloudfront)
  behind_cloudfront               = local.waf_behind_cloudfront
  origin_restricted_to_cloudfront = var.alb_restrict_ingress_to_cloudfront
  viewer_ip_header                = local.viewer_ip_header

  # Bot control (paid tier)
  bot_control_level = local.waf.bot_control_level

  # Geographic and IP rules
  geo_block_countries = var.waf_geo_block_countries
  ip_allowlist        = var.waf_ip_allowlist

  # Deployment mode
  rule_action_override = local.waf.rule_action_override
}

# CloudFront in front of ALB (optional, for custom error pages)
module "cloudfront_alb" {
  source = "./modules/cloudfront-alb"
  count  = local.cloudfront_alb_enabled ? 1 : 0

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  name_prefix           = local.name_prefix
  environment           = var.environment
  alb_dns_name          = module.alb.dns_name
  domain_name           = var.domain_name
  route53_zone_id       = var.route53_zone_id
  error_page_content    = var.cloudfront_alb_error_page_content
  error_caching_min_ttl = var.cloudfront_alb_error_caching_ttl

  # Stamp the viewer IP for the WAF rate rule only when the WAF reads it
  viewer_ip_header_enabled = local.waf_enabled && local.waf.rate_limit_enabled && local.waf_behind_cloudfront
  viewer_ip_header         = local.viewer_ip_header
}

# Service Discovery (AWS Cloud Map)
#
# Creates a private DNS namespace for internal service-to-service communication.
# Services can communicate via DNS names like "web.{namespace}" without going
# through the ALB (avoiding Cognito auth for internal API calls).
resource "aws_service_discovery_private_dns_namespace" "main" {
  count = var.service_discovery_enabled ? 1 : 0

  name        = "${local.name_prefix}.local"
  description = "Private DNS namespace for ${local.name_prefix} service discovery"
  vpc         = module.vpc.vpc_id

  tags = {
    Name = "${local.name_prefix}-service-discovery"
  }
}

# Service Discovery services (auto-created from services variable)
resource "aws_service_discovery_service" "services" {
  for_each = local.discovery_services

  name = each.key

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main[0].id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {}

  tags = {
    Name = "${local.name_prefix}-${each.key}-discovery"
  }

  lifecycle {
    ignore_changes = [health_check_custom_config]
  }

  provisioner "local-exec" {
    when    = destroy
    command = <<-EOT
      for instance_id in $(aws servicediscovery list-instances --service-id ${self.id} --query 'Instances[].Id' --output text 2>/dev/null); do
        aws servicediscovery deregister-instance --service-id ${self.id} --instance-id $instance_id || true
      done
      sleep 2
    EOT
  }
}
