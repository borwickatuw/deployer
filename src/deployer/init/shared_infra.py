"""Generate shared infrastructure environment directory structure.

Shared infrastructure includes VPC, NAT Gateway, ALB, and ECS cluster
that can be used by multiple applications.
"""

from deployer.utils import get_environments_dir
from .template import load_template, substitute


def generate_main_tf(env_type: str) -> str:
    """Generate main.tf for shared infrastructure.

    Args:
        env_type: Environment type ('staging' or 'production').

    Returns:
        main.tf content as string.
    """
    template = load_template("shared-infra", env_type, "main.tf.example")
    return substitute(template, env_type=env_type)


def generate_tfvars(env_type: str, domain_base: str | None = None) -> str:
    """Generate terraform.tfvars for shared infrastructure.

    Args:
        env_type: Environment type ('staging' or 'production').
        domain_base: Base domain for wildcard cert (e.g., 'staging.example.com').

    Returns:
        terraform.tfvars content as string.
    """
    domain = domain_base or f"{env_type}.example.com"

    template = load_template("shared-infra", env_type, "terraform.tfvars.example")
    return substitute(template, env_type=env_type, domain=domain)


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
