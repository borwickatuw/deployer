"""Generate standalone environment directory structure.

Standalone environments include their own VPC, NAT Gateway, ALB, and ECS cluster.
"""

from typing import Optional

from deployer.utils import get_environments_dir

# Default service sizing by environment type
STAGING_DEFAULTS = {
    "cpu": 256,
    "memory": 512,
    "replicas": 1,
}

PRODUCTION_DEFAULTS = {
    "cpu": 1024,
    "memory": 2048,
    "replicas": 2,
}


def generate_main_tf(app_name: str, env_type: str, domain: Optional[str] = None) -> str:
    """Generate main.tf for a standalone environment.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        domain: Optional domain name.

    Returns:
        main.tf content as string.
    """
    env_name = f"{app_name}-{env_type}"
    title = f"{app_name.title()} {env_type.title()} Environment"

    # Cognito is typically only for staging
    cognito_enabled = "true" if env_type == "staging" else "false"

    # Staging uses more aggressive health check settings
    health_check_section = ""
    if env_type == "staging":
        health_check_section = """
  # Staging-optimized ALB health check settings for faster deployments
  default_health_check_path = "/health/"
  health_check_interval     = 10  # 10s instead of default 30s
  health_check_timeout      = 5   # 5s instead of default 10s
  healthy_threshold         = 2   # Minimum for faster registration
  unhealthy_threshold       = 3   # 3 instead of default 5
  deregistration_delay      = 15  # 15s instead of default 120s"""

    scheduler_section = ""
    if env_type == "staging":
        scheduler_section = '''
# ------------------------------------------------------------------------------
# Automatic Scheduling (stop at night, start in morning)
# ------------------------------------------------------------------------------

module "scheduler" {
  source = "../modules/staging-scheduler"

  environment_name = "${var.project_name}-staging"
  ecs_cluster_name = module.infrastructure.ecs_cluster_name
  ecs_services = {
    for name, config in var.services : name => {
      replicas = config.replicas
    }
  }
  rds_instance_id = module.infrastructure.rds_instance_id

  # Schedule (all times in UTC)
  # Adjust these for your timezone
  stop_schedule  = "cron(0 2 ? * TUE-SAT *)"   # Stop at 6 PM Pacific Mon-Fri
  start_schedule = "cron(0 13 ? * MON-FRI *)"  # Start at 5 AM Pacific Mon-Fri

  # Set to false to disable automatic scheduling
  enabled = true
}

output "scheduler_enabled" {
  value       = module.scheduler.scheduling_enabled
  description = "Whether automatic scheduling is enabled"
}

output "scheduler_lambda" {
  value       = module.scheduler.lambda_function_name
  description = "Name of the scheduler Lambda function"
}
'''

    return f'''# {title}
#
# This instantiates the shared infrastructure for the {app_name} {env_type} environment.
# Application deployments are handled separately by the deploy.py script.

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

variable "project_name" {{
  type        = string
  description = "Name of the project (used for resource naming)"
}}

variable "db_username" {{
  type        = string
  description = "Database master username"
  sensitive   = true
}}

variable "db_password" {{
  type        = string
  description = "Database master password"
  sensitive   = true
}}

variable "domain_name" {{
  type        = string
  description = "Primary domain name for the application"
}}

variable "route53_zone_id" {{
  type        = string
  description = "Route53 zone ID for DNS records and certificate validation"
  default     = null
}}

# Service configuration
variable "services" {{
  type = map(object({{
    cpu               = number
    memory            = number
    replicas          = number
    load_balanced     = bool
    port              = optional(number)
    health_check_path = optional(string, "/")
  }}))
  description = "Service configuration for ECS services"
}}

variable "scaling" {{
  type = map(object({{
    min_replicas = number
    max_replicas = number
    cpu_target   = optional(number, 70)
  }}))
  default     = {{}}
  description = "Auto-scaling policies per service"
}}

# WAF configuration
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
# Infrastructure Module
# ------------------------------------------------------------------------------

module "infrastructure" {{
  source = "../modules/infrastructure"

  project_name    = var.project_name
  environment     = "{env_type}"
  db_username     = var.db_username
  db_password     = var.db_password
  domain_name     = var.domain_name
  route53_zone_id = var.route53_zone_id

  # Service configuration (for target group, IAM, etc.)
  services = var.services
  scaling  = var.scaling

  # Cognito for staging
  cognito_auth_enabled = {cognito_enabled}
{health_check_section}

  # WAF configuration
  waf_enabled              = var.waf_enabled
  waf_rate_limit_requests  = var.waf_rate_limit_requests
  waf_geo_block_countries  = var.waf_geo_block_countries
  waf_ip_allowlist         = var.waf_ip_allowlist
  waf_rule_action_override = var.waf_rule_action_override
}}

# ------------------------------------------------------------------------------
# Outputs for deploy script
# ------------------------------------------------------------------------------

output "ecs_cluster_name" {{
  value       = module.infrastructure.ecs_cluster_name
  description = "Name of the ECS cluster"
}}

output "ecs_security_group_id" {{
  value       = module.infrastructure.ecs_security_group_id
  description = "Security group for ECS tasks"
}}

output "private_subnet_ids" {{
  value       = module.infrastructure.private_subnet_ids
  description = "Private subnets for ECS tasks"
}}

output "ecs_execution_role_arn" {{
  value       = module.infrastructure.ecs_execution_role_arn
  description = "ECS task execution role ARN"
}}

output "ecs_task_role_arn" {{
  value       = module.infrastructure.ecs_task_role_arn
  description = "ECS task role ARN"
}}

output "alb_target_group_arn" {{
  value       = module.infrastructure.alb_target_group_arn
  description = "ALB target group ARN"
}}

output "alb_dns_name" {{
  value       = module.infrastructure.alb_dns_name
  description = "ALB DNS name"
}}

output "domain_name" {{
  value       = module.infrastructure.domain_name
  description = "Domain name for the application"
}}

output "database_url" {{
  value       = module.infrastructure.database_url
  description = "Database connection URL"
  sensitive   = true
}}

output "rds_instance_id" {{
  value       = module.infrastructure.rds_instance_id
  description = "RDS instance ID (for manage-environment.py)"
}}

output "redis_url" {{
  value       = module.infrastructure.redis_url
  description = "Redis connection URL"
}}

output "ecr_prefix" {{
  value       = module.infrastructure.ecr_prefix
  description = "ECR repository prefix"
}}

output "https_enabled" {{
  value       = module.infrastructure.https_enabled
  description = "Whether HTTPS is enabled"
}}

# Cognito outputs
output "cognito_user_pool_id" {{
  value       = module.infrastructure.cognito_user_pool_id
  description = "Cognito user pool ID"
}}

output "cognito_user_pool_client_id" {{
  value       = module.infrastructure.cognito_user_pool_client_id
  description = "Cognito user pool client ID"
}}

# Service configuration outputs (for deploy script)
output "service_config" {{
  value       = module.infrastructure.service_config
  description = "Service configuration (CPU, memory, replicas)"
}}

output "scaling_config" {{
  value       = module.infrastructure.scaling_config
  description = "Auto-scaling configuration"
}}

output "health_check_config" {{
  value       = module.infrastructure.health_check_config
  description = "Health check configuration"
}}

# WAF outputs
output "waf_enabled" {{
  value       = module.infrastructure.waf_enabled
  description = "Whether WAF is enabled"
}}

output "waf_web_acl_arn" {{
  value       = module.infrastructure.waf_web_acl_arn
  description = "WAF Web ACL ARN"
}}

output "waf_log_group_name" {{
  value       = module.infrastructure.waf_log_group_name
  description = "CloudWatch log group for WAF logs"
}}
{scheduler_section}'''


def generate_tfvars(
    app_name: str,
    env_type: str,
    deploy_config: Optional[dict] = None,
    domain: Optional[str] = None,
) -> str:
    """Generate terraform.tfvars for a standalone environment.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        deploy_config: Optional deploy.toml configuration dict.
        domain: Optional domain name.

    Returns:
        terraform.tfvars content as string.
    """
    defaults = STAGING_DEFAULTS if env_type == "staging" else PRODUCTION_DEFAULTS

    # Extract services from deploy.toml if provided
    services = {}
    if deploy_config and "services" in deploy_config:
        for name, svc in deploy_config["services"].items():
            service_config = {
                "cpu": defaults["cpu"],
                "memory": defaults["memory"],
                "replicas": defaults["replicas"],
            }

            # Determine if load balanced based on port presence
            if svc.get("port"):
                service_config["load_balanced"] = True
                service_config["port"] = svc["port"]
                service_config["health_check_path"] = svc.get("health_check_path", "/health/")
            else:
                service_config["load_balanced"] = False

            services[name] = service_config
    else:
        # Default to a single web service
        services["web"] = {
            "cpu": defaults["cpu"],
            "memory": defaults["memory"],
            "replicas": defaults["replicas"],
            "load_balanced": True,
            "port": 8000,
            "health_check_path": "/health/",
        }

    # Format services block
    services_lines = ["services = {"]
    for name, config in services.items():
        services_lines.append(f"  {name} = {{")
        services_lines.append(f"    cpu               = {config['cpu']}")
        services_lines.append(f"    memory            = {config['memory']}")
        services_lines.append(f"    replicas          = {config['replicas']}")
        services_lines.append(f"    load_balanced     = {'true' if config['load_balanced'] else 'false'}")
        if config.get("port"):
            services_lines.append(f"    port              = {config['port']}")
            services_lines.append(f'    health_check_path = "{config.get("health_check_path", "/health/")}"')
        services_lines.append("  }")
    services_lines.append("}")
    services_block = "\n".join(services_lines)

    # Scaling configuration
    if env_type == "production":
        scaling_block = '''# Auto-scaling for production
scaling = {
  web = {
    min_replicas = 2
    max_replicas = 10
    cpu_target   = 70
  }
}'''
    else:
        scaling_block = "# Auto-scaling disabled in staging\nscaling = {}"

    domain_value = domain or f"{app_name}-{env_type}.example.com"

    return f'''# {app_name.title()} {env_type.title()} Environment Configuration
# DO NOT commit this file to version control (contains sensitive data)

# ------------------------------------------------------------------------------
# Project Settings
# ------------------------------------------------------------------------------

project_name = "{app_name}"

# ------------------------------------------------------------------------------
# Database Credentials
# ------------------------------------------------------------------------------

db_username = "{app_name}_admin"
db_password = "CHANGE-ME-generate-a-secure-password"

# ------------------------------------------------------------------------------
# Domain Configuration
# ------------------------------------------------------------------------------

domain_name     = "{domain_value}"
route53_zone_id = null  # Set this to your Route53 zone ID, or use certificate_arn in main.tf

# ------------------------------------------------------------------------------
# Service Configuration
# ------------------------------------------------------------------------------

{services_block}

# ------------------------------------------------------------------------------
# Auto-Scaling
# ------------------------------------------------------------------------------

{scaling_block}

# ------------------------------------------------------------------------------
# WAF Configuration
# ------------------------------------------------------------------------------

waf_enabled              = true   # Enable AWS WAF for the ALB
waf_rate_limit_requests  = 2000   # Max requests per IP per 5 minutes
waf_geo_block_countries  = []     # e.g., ["RU", "CN", "KP"]
waf_ip_allowlist         = []     # CIDRs that bypass WAF (e.g., office IPs)
waf_rule_action_override = "none" # Set to "count" to test without blocking
'''


def generate_readme(app_name: str, env_type: str) -> str:
    """Generate README.md for a standalone environment.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').

    Returns:
        README.md content as string.
    """
    env_name = f"{app_name}-{env_type}"
    env_dir = get_environments_dir() / env_name

    return f'''# {app_name.title()} {env_type.title()} Environment

## Quick Commands

```bash
# Deploy infrastructure
./bin/tofu.sh -chdir={env_dir} init
./bin/tofu.sh -chdir={env_dir} plan
./bin/tofu.sh -chdir={env_dir} apply

# Deploy application
uv run python bin/deploy.py /path/to/{app_name}/deploy.toml {env_name}

# Check status
uv run python bin/manage-environment.py status {env_name}
```

## Configuration Files

- `main.tf` - Infrastructure module configuration
- `config.toml` - Deployment configuration (bridges tofu outputs to deploy script)
- `terraform.tfvars` - Service sizing and credentials (DO NOT commit)

## Before First Deployment

1. Edit `terraform.tfvars`:
   - Set database credentials
   - Configure domain and Route53 zone ID
   - Adjust service sizing if needed

2. Create SSM parameters for secrets:
   ```bash
   aws ssm put-parameter --name "/{app_name}/{env_type}/secret-key" --value "..." --type SecureString
   ```

3. Create CloudWatch log group:
   ```bash
   aws logs create-log-group --log-group-name /ecs/{app_name}
   ```

## Notes

- terraform.tfvars contains sensitive data - do not commit to git
- Use `./bin/tofu.sh` for auto-selecting AWS profile
'''
