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
}

# State migration: Route 53 record moved from inline resource to module
moved {
  from = aws_route53_record.main[0]
  to   = module.route53[0].aws_route53_record.alias["main"]
}

# VPC and networking
module "vpc" {
  source = "./modules/vpc"

  name_prefix        = local.name_prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
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
  default_health_check_path = var.default_health_check_path
  health_check_interval     = var.health_check_interval
  health_check_timeout      = var.health_check_timeout
  healthy_threshold         = var.healthy_threshold
  unhealthy_threshold       = var.unhealthy_threshold
  deregistration_delay      = var.deregistration_delay

  # Idle timeout - increase for large file uploads
  idle_timeout = var.alb_idle_timeout

  # Cognito authentication (optional)
  cognito_auth = var.cognito_auth_enabled ? {
    user_pool_arn       = module.cognito[0].user_pool_arn
    user_pool_client_id = module.cognito[0].client_id
    user_pool_domain    = module.cognito[0].domain
  } : null
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
module "cognito" {
  source = "./modules/cognito"
  count  = var.cognito_auth_enabled ? 1 : 0

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

# CloudFront CDN (optional, for S3 media delivery)
module "cloudfront" {
  source = "./modules/cloudfront"
  count  = var.cloudfront_enabled && var.cloudfront_s3_bucket != null ? 1 : 0

  name_prefix                    = local.name_prefix
  s3_bucket_id                   = module.s3[var.cloudfront_s3_bucket].bucket_id
  s3_bucket_arn                  = module.s3[var.cloudfront_s3_bucket].bucket_arn
  s3_bucket_regional_domain_name = module.s3[var.cloudfront_s3_bucket].bucket_regional_domain_name
  aliases                        = var.cloudfront_aliases
  certificate_arn                = var.cloudfront_certificate_arn
  price_class                    = var.cloudfront_price_class
  default_ttl                    = var.cloudfront_default_ttl
  max_ttl                        = var.cloudfront_max_ttl
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
    # CloudFront CDN subdomain (if enabled and aliases configured)
    var.cloudfront_enabled && length(var.cloudfront_aliases) > 0 ? {
      cdn = {
        type = "A"
        name = var.cloudfront_aliases[0]
        alias_target = {
          dns_name               = module.cloudfront[0].domain_name
          zone_id                = module.cloudfront[0].hosted_zone_id
          evaluate_target_health = false
        }
      }
    } : {}
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

# Allow ALB to reach ECS tasks on the application port
resource "aws_security_group_rule" "alb_to_ecs" {
  type                     = "ingress"
  from_port                = var.container_port
  to_port                  = var.container_port
  protocol                 = "tcp"
  source_security_group_id = module.alb.security_group_id
  security_group_id        = module.ecs_cluster.security_group_id
  description              = "Allow ALB to reach ECS tasks on port ${var.container_port}"
}

# WAF (optional)
module "waf" {
  source = "./modules/waf"
  count  = var.waf_enabled ? 1 : 0

  name_prefix = local.name_prefix
  alb_arn     = module.alb.arn

  # Protection rules
  ip_reputation_enabled    = var.waf_ip_reputation_enabled
  common_rules_enabled     = var.waf_common_rules_enabled
  common_rules_excluded    = var.waf_common_rules_excluded
  known_bad_inputs_enabled = var.waf_known_bad_inputs_enabled
  sqli_rules_enabled       = var.waf_sqli_rules_enabled

  # Rate limiting
  rate_limit_enabled  = var.waf_rate_limit_enabled
  rate_limit_requests = var.waf_rate_limit_requests

  # Bot control (paid tier)
  bot_control_level = var.waf_bot_control_level

  # Geographic and IP rules
  geo_block_countries = var.waf_geo_block_countries
  ip_allowlist        = var.waf_ip_allowlist

  # Deployment mode
  rule_action_override = var.waf_rule_action_override
}

# CloudFront in front of ALB (optional, for custom error pages)
module "cloudfront_alb" {
  source = "./modules/cloudfront-alb"
  count  = var.cloudfront_alb_enabled && var.domain_name != null && var.route53_zone_id != null ? 1 : 0

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }

  name_prefix           = local.name_prefix
  alb_dns_name          = module.alb.dns_name
  domain_name           = var.domain_name
  route53_zone_id       = var.route53_zone_id
  error_page_content    = var.cloudfront_alb_error_page_content
  error_caching_min_ttl = var.cloudfront_alb_error_caching_ttl
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
