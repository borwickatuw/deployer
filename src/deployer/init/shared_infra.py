"""Generate shared infrastructure environment directory structure.

Shared infrastructure includes VPC, NAT Gateway, ALB, and ECS cluster
that can be used by multiple applications.
"""

from typing import Optional

from deployer.utils import get_environments_dir


def generate_main_tf(env_type: str) -> str:
    """Generate main.tf for shared infrastructure.

    Args:
        env_type: Environment type ('staging' or 'production').

    Returns:
        main.tf content as string.
    """
    env_name = f"shared-infra-{env_type}"
    title = f"Shared Infrastructure - {env_type.title()}"

    cognito_enabled = "true" if env_type == "staging" else "false"

    return f'''# {title}
#
# This creates shared infrastructure for multiple apps:
# - VPC with NAT Gateway
# - ECS Cluster
# - Application Load Balancer
# - Optional: Cognito authentication
# - Optional: Shared ElastiCache
#
# Per-app resources (RDS, target groups, etc.) are created separately.

terraform {{
  required_version = ">= 1.6.0"

  # Uncomment and configure for remote state
  # backend "s3" {{
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "{env_name}/terraform.tfstate"
  #   region         = "us-west-2"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }}
}}

# ------------------------------------------------------------------------------
# Variables
# ------------------------------------------------------------------------------

variable "name_prefix" {{
  type        = string
  description = "Prefix for resource names"
  default     = "{env_name}"
}}

variable "domain_name" {{
  type        = string
  description = "Primary domain name (e.g., staging.example.com)"
}}

variable "route53_zone_id" {{
  type        = string
  description = "Route 53 hosted zone ID for DNS and certificate validation"
  default     = null
}}

variable "certificate_san" {{
  type        = list(string)
  description = "Additional domain names for the certificate (e.g., ['*.staging.example.com'])"
  default     = []
}}

variable "cognito_auth_enabled" {{
  type        = bool
  description = "Enable Cognito authentication"
  default     = {cognito_enabled}
}}

variable "cache_enabled" {{
  type        = bool
  description = "Enable shared ElastiCache"
  default     = false
}}

# WAF Configuration
variable "waf_enabled" {{
  type        = bool
  description = "Enable AWS WAF for the ALB"
  default     = true
}}

variable "waf_rate_limit_requests" {{
  type        = number
  description = "Maximum requests per IP in a 5-minute window"
  default     = 2000
}}

variable "waf_geo_block_countries" {{
  type        = list(string)
  description = "Country codes to block (e.g., ['RU', 'CN'])"
  default     = []
}}

variable "waf_ip_allowlist" {{
  type        = list(string)
  description = "CIDR blocks that bypass WAF (e.g., office IPs)"
  default     = []
}}

variable "waf_rule_action_override" {{
  type        = string
  description = "Set to 'count' to test rules without blocking"
  default     = "none"
}}

# ------------------------------------------------------------------------------
# Shared Infrastructure Module
# ------------------------------------------------------------------------------

module "shared_infra" {{
  source = "../modules/shared-infrastructure"

  name_prefix          = var.name_prefix
  domain_name          = var.domain_name
  route53_zone_id      = var.route53_zone_id
  certificate_san      = var.certificate_san
  cognito_auth_enabled = var.cognito_auth_enabled
  cache_enabled        = var.cache_enabled

  # VPC configuration
  vpc_cidr           = "10.0.0.0/16"
  availability_zones = ["us-west-2a", "us-west-2b"]

  # Health check settings (staging-optimized)
  health_check_interval = 10
  health_check_timeout  = 5
  healthy_threshold     = 2
  unhealthy_threshold   = 3
  deregistration_delay  = 15

  # WAF configuration
  waf_enabled              = var.waf_enabled
  waf_rate_limit_requests  = var.waf_rate_limit_requests
  waf_geo_block_countries  = var.waf_geo_block_countries
  waf_ip_allowlist         = var.waf_ip_allowlist
  waf_rule_action_override = var.waf_rule_action_override
}}

# ------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------

# VPC
output "vpc_id" {{
  value = module.shared_infra.vpc_id
}}

output "public_subnet_ids" {{
  value = module.shared_infra.public_subnet_ids
}}

output "private_subnet_ids" {{
  value = module.shared_infra.private_subnet_ids
}}

# ECS
output "ecs_cluster_name" {{
  value = module.shared_infra.ecs_cluster_name
}}

output "ecs_cluster_arn" {{
  value = module.shared_infra.ecs_cluster_arn
}}

output "ecs_security_group_id" {{
  value = module.shared_infra.ecs_security_group_id
}}

# ALB
output "alb_arn" {{
  value = module.shared_infra.alb_arn
}}

output "alb_dns_name" {{
  value = module.shared_infra.alb_dns_name
}}

output "alb_zone_id" {{
  value = module.shared_infra.alb_zone_id
}}

output "alb_security_group_id" {{
  value = module.shared_infra.alb_security_group_id
}}

output "alb_http_listener_arn" {{
  value = module.shared_infra.alb_http_listener_arn
}}

output "alb_https_listener_arn" {{
  value = module.shared_infra.alb_https_listener_arn
}}

output "alb_default_target_group_arn" {{
  value = module.shared_infra.alb_default_target_group_arn
}}

output "https_enabled" {{
  value = module.shared_infra.https_enabled
}}

# Cognito
output "cognito_user_pool_id" {{
  value = module.shared_infra.cognito_user_pool_id
}}

output "cognito_user_pool_arn" {{
  value = module.shared_infra.cognito_user_pool_arn
}}

output "cognito_user_pool_client_id" {{
  value = module.shared_infra.cognito_user_pool_client_id
}}

output "cognito_user_pool_client_secret" {{
  value     = module.shared_infra.cognito_user_pool_client_secret
  sensitive = true
}}

output "cognito_domain" {{
  value = module.shared_infra.cognito_domain
}}

output "cognito_auth_enabled" {{
  value = module.shared_infra.cognito_auth_enabled
}}

# Cache
output "redis_endpoint" {{
  value = module.shared_infra.redis_endpoint
}}

output "redis_url" {{
  value = module.shared_infra.redis_url
}}

# DNS
output "domain_name" {{
  value = module.shared_infra.domain_name
}}

output "certificate_arn" {{
  value = module.shared_infra.certificate_arn
}}

output "route53_zone_id" {{
  value = module.shared_infra.route53_zone_id
}}

output "name_prefix" {{
  value = module.shared_infra.name_prefix
}}

# WAF
output "waf_enabled" {{
  value = module.shared_infra.waf_enabled
}}

output "waf_web_acl_arn" {{
  value = module.shared_infra.waf_web_acl_arn
}}

output "waf_log_group_name" {{
  value = module.shared_infra.waf_log_group_name
}}
'''


def generate_tfvars(env_type: str, domain_base: Optional[str] = None) -> str:
    """Generate terraform.tfvars for shared infrastructure.

    Args:
        env_type: Environment type ('staging' or 'production').
        domain_base: Base domain for wildcard cert (e.g., 'staging.example.com').

    Returns:
        terraform.tfvars content as string.
    """
    domain = domain_base or f"{env_type}.example.com"
    cognito = "true" if env_type == "staging" else "false"

    return f'''# Shared Infrastructure - {env_type.title()}
# DO NOT commit this file to version control (may contain sensitive data)

# ------------------------------------------------------------------------------
# Naming
# ------------------------------------------------------------------------------

name_prefix = "shared-infra-{env_type}"

# ------------------------------------------------------------------------------
# Domain Configuration
# ------------------------------------------------------------------------------

domain_name     = "{domain}"
route53_zone_id = null  # Set to your Route53 zone ID

# Wildcard certificate for all apps
certificate_san = ["*.{domain}"]

# ------------------------------------------------------------------------------
# Features
# ------------------------------------------------------------------------------

cognito_auth_enabled = {cognito}
cache_enabled        = false  # Set to true to enable shared Redis

# ------------------------------------------------------------------------------
# WAF Configuration
# ------------------------------------------------------------------------------

waf_enabled              = true   # Enable AWS WAF for the ALB
waf_rate_limit_requests  = 2000   # Max requests per IP per 5 minutes
waf_geo_block_countries  = []     # e.g., ["RU", "CN", "KP"]
waf_ip_allowlist         = []     # CIDRs that bypass WAF (e.g., office IPs)
waf_rule_action_override = "none" # Set to "count" to test without blocking
'''


def generate_readme(env_type: str) -> str:
    """Generate README.md for shared infrastructure.

    Args:
        env_type: Environment type ('staging' or 'production').

    Returns:
        README.md content as string.
    """
    env_name = f"shared-infra-{env_type}"
    env_dir = get_environments_dir() / env_name

    return f'''# Shared Infrastructure - {env_type.title()}

This environment contains shared infrastructure for multiple {env_type} apps.

## What's Included

- VPC with NAT Gateway
- ECS Cluster
- Application Load Balancer
- Optional: Cognito authentication (for staging)
- Optional: Shared ElastiCache

## What's NOT Included (Per-App)

Each app creates its own:
- RDS database
- ALB target group and listener rule
- ECR repository
- IAM roles

## Quick Commands

```bash
# Deploy shared infrastructure
./bin/tofu.sh -chdir={env_dir} init
./bin/tofu.sh -chdir={env_dir} plan
./bin/tofu.sh -chdir={env_dir} apply
```

## Adding a New App

```bash
uv run python bin/init.py environment \\
    --app-name myapp \\
    --env-type {env_type} \\
    --shared \\
    --domain myapp.{env_type}.example.com
```

## Notes

- terraform.tfvars may contain sensitive data - do not commit to git
- Apps reference this infrastructure via terraform_remote_state
- Listener rule priorities must be unique per app (auto-assigned)
'''
