"""Generate environment directory structure for apps using shared infrastructure.

These lightweight environments reference shared VPC, NAT Gateway, ALB, and ECS cluster,
but create their own RDS, target groups, and other per-app resources.
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


def generate_main_tf(
    app_name: str,
    env_type: str,
    shared_infra_name: str,
    listener_priority: int,
) -> str:
    """Generate main.tf for an app using shared infrastructure.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        shared_infra_name: Name of the shared infrastructure environment.
        listener_priority: ALB listener rule priority (must be unique per app).

    Returns:
        main.tf content as string.
    """
    env_name = f"{app_name}-{env_type}"
    title = f"{app_name.title()} {env_type.title()} Environment (Shared)"
    shared_infra_dir = get_environments_dir() / shared_infra_name

    return f'''# {title}
#
# This app uses shared infrastructure from {shared_infra_name}.
# Only per-app resources are created here: RDS, target group, listener rule, ECR, IAM.

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

variable "app_name" {{
  type    = string
  default = "{app_name}"
}}

variable "environment" {{
  type    = string
  default = "{env_type}"
}}

variable "domain_name" {{
  type        = string
  description = "Domain name for this app (e.g., {app_name}.{env_type}.example.com)"
}}

variable "db_username" {{
  type      = string
  sensitive = true
}}

variable "db_password" {{
  type      = string
  sensitive = true
}}

variable "listener_rule_priority" {{
  type        = number
  description = "ALB listener rule priority (must be unique per app)"
  default     = {listener_priority}
}}

variable "route53_zone_id" {{
  type        = string
  description = "Route53 zone ID for creating DNS record (optional)"
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
  description = "Service sizing configuration"
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

variable "health_check" {{
  type = object({{
    interval            = optional(number, 30)
    timeout             = optional(number, 10)
    healthy_threshold   = optional(number, 2)
    unhealthy_threshold = optional(number, 5)
  }})
  default     = {{}}
  description = "Global health check defaults"
}}

# ------------------------------------------------------------------------------
# App Module (uses shared infrastructure)
# ------------------------------------------------------------------------------

module "app" {{
  source = "../modules/app-in-shared-env"

  app_name    = var.app_name
  environment = var.environment
  domain_name = var.domain_name

  # Reference shared infrastructure state
  shared_state_backend = "local"
  shared_state_path    = "{shared_infra_dir}/terraform.tfstate"

  # Database
  db_username = var.db_username
  db_password = var.db_password

  # ALB routing
  listener_rule_priority = var.listener_rule_priority
  route53_zone_id        = var.route53_zone_id

  # Service config (passed through for outputs)
  services     = var.services
  scaling      = var.scaling
  health_check = var.health_check
}}

# ------------------------------------------------------------------------------
# Outputs for deploy script
# ------------------------------------------------------------------------------

# Infrastructure (from shared)
output "vpc_id" {{
  value = module.app.vpc_id
}}

output "private_subnet_ids" {{
  value = module.app.private_subnet_ids
}}

output "ecs_cluster_name" {{
  value = module.app.ecs_cluster_name
}}

output "ecs_security_group_id" {{
  value = module.app.ecs_security_group_id
}}

output "alb_dns_name" {{
  value = module.app.alb_dns_name
}}

output "https_enabled" {{
  value = module.app.https_enabled
}}

# Per-app resources
output "alb_target_group_arn" {{
  value = module.app.alb_target_group_arn
}}

output "ecs_execution_role_arn" {{
  value = module.app.ecs_execution_role_arn
}}

output "ecs_task_role_arn" {{
  value = module.app.ecs_task_role_arn
}}

output "domain_name" {{
  value = module.app.domain_name
}}

# Database
output "database_url" {{
  value     = module.app.database_url
  sensitive = true
}}

output "rds_instance_id" {{
  value = module.app.rds_instance_id
}}

# ECR
output "ecr_prefix" {{
  value = module.app.ecr_prefix
}}

# Redis (from shared, if enabled)
output "redis_url" {{
  value = module.app.redis_url
}}

# Cognito (from shared, if enabled)
output "cognito_user_pool_id" {{
  value = module.app.cognito_user_pool_id
}}

output "cognito_user_pool_client_id" {{
  value = module.app.cognito_user_pool_client_id
}}

# Service config (for deploy.py)
output "service_config" {{
  value = module.app.service_config
}}

output "scaling_config" {{
  value = module.app.scaling_config
}}

output "health_check_config" {{
  value = module.app.health_check_config
}}
'''


def generate_tfvars(
    app_name: str,
    env_type: str,
    deploy_config: Optional[dict] = None,
    domain: Optional[str] = None,
    listener_priority: int = 100,
) -> str:
    """Generate terraform.tfvars for an app using shared infrastructure.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        deploy_config: Optional deploy.toml configuration dict.
        domain: Optional domain name.
        listener_priority: ALB listener rule priority (unique per app).

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

            if svc.get("port"):
                service_config["load_balanced"] = True
                service_config["port"] = svc["port"]
                service_config["health_check_path"] = svc.get("health_check_path", "/health/")
            else:
                service_config["load_balanced"] = False

            services[name] = service_config
    else:
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

    domain_value = domain or f"{app_name}.{env_type}.example.com"

    return f'''# {app_name.title()} {env_type.title()} Environment (Shared Infrastructure)
# DO NOT commit this file to version control (contains sensitive data)

# ------------------------------------------------------------------------------
# App Identity
# ------------------------------------------------------------------------------

app_name    = "{app_name}"
environment = "{env_type}"

# ------------------------------------------------------------------------------
# Database Credentials
# ------------------------------------------------------------------------------

db_username = "{app_name}_admin"
db_password = "CHANGE-ME-generate-a-secure-password"

# ------------------------------------------------------------------------------
# Domain Configuration
# ------------------------------------------------------------------------------

domain_name     = "{domain_value}"
route53_zone_id = null  # Set to your Route53 zone ID

# ------------------------------------------------------------------------------
# ALB Routing
# ------------------------------------------------------------------------------

listener_rule_priority = {listener_priority}

# ------------------------------------------------------------------------------
# Service Configuration
# ------------------------------------------------------------------------------

{services_block}

# ------------------------------------------------------------------------------
# Auto-Scaling
# ------------------------------------------------------------------------------

{scaling_block}
'''


def generate_readme(app_name: str, env_type: str, shared_infra_name: str) -> str:
    """Generate README.md for an app using shared infrastructure.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        shared_infra_name: Name of the shared infrastructure environment.

    Returns:
        README.md content as string.
    """
    env_name = f"{app_name}-{env_type}"
    env_dir = get_environments_dir() / env_name
    shared_infra_dir = get_environments_dir() / shared_infra_name

    return f'''# {app_name.title()} {env_type.title()} Environment

This environment uses shared infrastructure from `{shared_infra_name}`.

## What's Created Here (Per-App)

- RDS database
- ALB target group and listener rule
- ECR repository
- IAM roles (execution and task roles)
- Route53 DNS record (if configured)

## What's Shared (From {shared_infra_name})

- VPC with NAT Gateway
- ECS Cluster
- Application Load Balancer
- Cognito authentication (if enabled)
- ElastiCache (if enabled)

## Prerequisites

The shared infrastructure must be deployed first:
```bash
./bin/tofu.sh -chdir={shared_infra_dir} init
./bin/tofu.sh -chdir={shared_infra_dir} apply
```

## Quick Commands

```bash
# Deploy this app's infrastructure
./bin/tofu.sh -chdir={env_dir} init
./bin/tofu.sh -chdir={env_dir} plan
./bin/tofu.sh -chdir={env_dir} apply

# Deploy application
uv run python bin/deploy.py /path/to/{app_name}/deploy.toml {env_name}

# Check status
uv run python bin/manage-environment.py status {env_name}
```

## Configuration Files

- `main.tf` - Infrastructure module configuration (references shared state)
- `config.toml` - Deployment configuration (bridges tofu outputs to deploy script)
- `terraform.tfvars` - Service sizing and credentials (DO NOT commit)

## Before First Deployment

1. Edit `terraform.tfvars`:
   - Set database credentials
   - Configure domain
   - Verify listener_rule_priority is unique

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
- Listener rule priority must be unique across all apps in the shared environment
'''
