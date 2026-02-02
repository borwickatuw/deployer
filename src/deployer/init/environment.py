"""Generate environment directory structure for deployer.

This module provides entry points for generating different types of environments:
- Standalone environments (own VPC, NAT, ALB, ECS cluster)
- Shared infrastructure (VPC, NAT, ALB, ECS cluster shared by multiple apps)
- Shared app environments (use shared infrastructure, own RDS and target group)
"""

import re
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from deployer.utils import get_environments_dir

# Import generators
from . import standalone
from . import shared_infra
from . import shared_app

# Re-export constants for backwards compatibility
STAGING_DEFAULTS = standalone.STAGING_DEFAULTS
PRODUCTION_DEFAULTS = standalone.PRODUCTION_DEFAULTS


def get_next_listener_priority(env_type: str) -> int:
    """Find next available listener priority for shared environment.

    Scans existing app environments using shared-infra-{env_type} and
    returns next available priority (100, 200, 300, ...).

    Args:
        env_type: Environment type ('staging' or 'production').

    Returns:
        Next available listener priority.
    """
    env_dir = get_environments_dir()
    shared_infra_name = f"shared-infra-{env_type}"

    # Find all environments that reference this shared infra
    existing_priorities = []
    for env_path in env_dir.iterdir():
        if env_path.is_dir() and env_path.name != shared_infra_name:
            tfvars_path = env_path / "terraform.tfvars"
            if tfvars_path.exists():
                try:
                    content = tfvars_path.read_text()
                    # Parse listener_rule_priority from tfvars
                    match = re.search(r'listener_rule_priority\s*=\s*(\d+)', content)
                    if match:
                        existing_priorities.append(int(match.group(1)))
                except Exception:
                    pass

    # Return next available (100, 200, 300, ...)
    if not existing_priorities:
        return 100
    return max(existing_priorities) + 100


def generate_environment(
    app_name: str,
    env_type: str,
    deploy_toml_path: Optional[Path] = None,
    domain: Optional[str] = None,
    shared: bool = False,
    listener_priority: Optional[int] = None,
) -> dict[str, str]:
    """Generate environment directory structure.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        deploy_toml_path: Optional path to deploy.toml for reading service info.
        domain: Optional domain name for the environment.
        shared: If True, generate lightweight environment using shared infrastructure.
        listener_priority: ALB listener rule priority (required if shared=True).

    Returns:
        Dictionary mapping file paths to their contents.
    """
    env_name = f"{app_name}-{env_type}"
    env_dir = get_environments_dir() / env_name

    # Load deploy.toml if provided
    deploy_config = None
    if deploy_toml_path and deploy_toml_path.exists():
        with open(deploy_toml_path, "rb") as f:
            deploy_config = tomllib.load(f)

    if shared:
        return generate_shared_app_environment(
            app_name=app_name,
            env_type=env_type,
            deploy_config=deploy_config,
            domain=domain,
            listener_priority=listener_priority or 100,
        )

    # Generate standalone environment
    files = {}

    # Generate main.tf
    files[str(env_dir / "main.tf")] = standalone.generate_main_tf(app_name, env_type, domain)

    # Generate config.toml
    files[str(env_dir / "config.toml")] = generate_config_toml(app_name, env_type, domain)

    # Generate terraform.tfvars
    files[str(env_dir / "terraform.tfvars")] = standalone.generate_tfvars(
        app_name, env_type, deploy_config, domain
    )

    # Generate README.md
    files[str(env_dir / "README.md")] = standalone.generate_readme(app_name, env_type)

    return files


def generate_shared_infrastructure(
    env_type: str,
    domain_base: Optional[str] = None,
) -> dict[str, str]:
    """Generate files for shared infrastructure environment.

    Args:
        env_type: 'staging' or 'production'
        domain_base: Base domain for wildcard cert (e.g., 'staging.example.com')

    Returns:
        Dict of {filepath: content} for main.tf, terraform.tfvars, README.md
    """
    env_name = f"shared-infra-{env_type}"
    env_dir = get_environments_dir() / env_name

    files = {}
    files[str(env_dir / "main.tf")] = shared_infra.generate_main_tf(env_type)
    files[str(env_dir / "terraform.tfvars")] = shared_infra.generate_tfvars(env_type, domain_base)
    files[str(env_dir / "README.md")] = shared_infra.generate_readme(env_type)
    return files


def generate_shared_app_environment(
    app_name: str,
    env_type: str,
    deploy_config: Optional[dict] = None,
    domain: Optional[str] = None,
    listener_priority: int = 100,
) -> dict[str, str]:
    """Generate lightweight environment that uses shared infrastructure.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        deploy_config: Optional deploy.toml configuration dict.
        domain: Optional domain name for the environment.
        listener_priority: ALB listener rule priority (unique per app).

    Returns:
        Dictionary mapping file paths to their contents.
    """
    env_name = f"{app_name}-{env_type}"
    env_dir = get_environments_dir() / env_name
    shared_infra_name = f"shared-infra-{env_type}"

    files = {}

    # Generate main.tf using app-in-shared-env module
    files[str(env_dir / "main.tf")] = shared_app.generate_main_tf(
        app_name, env_type, shared_infra_name, listener_priority
    )

    # Generate config.toml (same structure, just different cluster_name source)
    files[str(env_dir / "config.toml")] = generate_config_toml(app_name, env_type, domain)

    # Generate terraform.tfvars
    files[str(env_dir / "terraform.tfvars")] = shared_app.generate_tfvars(
        app_name, env_type, deploy_config, domain, listener_priority
    )

    # Generate README.md
    files[str(env_dir / "README.md")] = shared_app.generate_readme(app_name, env_type, shared_infra_name)

    return files


def generate_config_toml(app_name: str, env_type: str, domain: Optional[str] = None) -> str:
    """Generate config.toml for an environment.

    This is used by both standalone and shared app environments.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        domain: Optional domain name.

    Returns:
        config.toml content as string.
    """
    env_name = f"{app_name}-{env_type}"

    # Cognito section varies by environment type
    if env_type == "staging":
        cognito_section = f'''# Cognito authentication for staging
[cognito]
enabled = true
user_pool_id = "${{tofu:cognito_user_pool_id}}"
client_id = "${{tofu:cognito_user_pool_client_id}}"
test_username = "deployer@test.local"
test_password_ssm = "/deployer/{env_name}/cognito-test-password"'''
        deployment_section = '''# Staging deployment settings (faster, allows brief downtime)
[deployment]
minimum_healthy_percent = 0
maximum_percent = 100
circuit_breaker_enabled = true
circuit_breaker_rollback = true'''
    else:
        cognito_section = '''# Cognito disabled for production
[cognito]
enabled = false'''
        deployment_section = '''# Production deployment settings (maintain availability)
# These are defaults, so the section can be omitted
# [deployment]
# minimum_healthy_percent = 100
# maximum_percent = 200
# circuit_breaker_enabled = true
# circuit_breaker_rollback = true'''

    # Build rds_instance_id line separately to avoid f-string backslash issue
    if env_type == "staging":
        rds_line = 'rds_instance_id = "${tofu:rds_instance_id}"'
    else:
        rds_line = "# rds_instance_id omitted - production RDS should not be stopped"

    return f'''# {app_name.title()} {env_type.title()} Environment Configuration
#
# Values with ${{tofu:...}} placeholders are resolved at deploy time
# by running `tofu output` in this directory.

[environment]
type = "{env_type}"
domain_name = "${{tofu:domain_name}}"

[infrastructure]
cluster_name = "${{tofu:ecs_cluster_name}}"
security_group_id = "${{tofu:ecs_security_group_id}}"
private_subnet_ids = "${{tofu:private_subnet_ids}}"
execution_role_arn = "${{tofu:ecs_execution_role_arn}}"
task_role_arn = "${{tofu:ecs_task_role_arn}}"
target_group_arn = "${{tofu:alb_target_group_arn}}"
alb_dns_name = "${{tofu:alb_dns_name}}"
{rds_line}

[services]
config = "${{tofu:service_config}}"
scaling = "${{tofu:scaling_config}}"
health_check = "${{tofu:health_check_config}}"

[database]
url = "${{tofu:database_url}}"

[redis]
url = "${{tofu:redis_url}}"

# Uncomment if your app uses S3 for media storage
# [storage]
# media_bucket = "${{tofu:s3_media_bucket}}"

{cognito_section}

{deployment_section}
'''
