"""Generate standalone environment directory structure.

Standalone environments include their own VPC, NAT Gateway, ALB, and ECS cluster.
"""

from deployer.utils import get_environments_dir
from .template import load_template, substitute

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


def generate_main_tf(app_name: str, env_type: str, domain: str | None = None) -> str:
    """Generate main.tf for a standalone environment.

    Args:
        app_name: Application name.
        env_type: Environment type ('staging' or 'production').
        domain: Optional domain name.

    Returns:
        main.tf content as string.
    """
    env_name = f"{app_name}-{env_type}"

    template = load_template("standalone", env_type, "main.tf.example")
    return substitute(
        template,
        app_name=app_name,
        env_type=env_type,
        env_name=env_name,
    )


def _format_services_block(services: dict) -> str:
    """Format the services block for terraform.tfvars.

    Args:
        services: Dictionary of service configurations.

    Returns:
        Formatted HCL services block.
    """
    lines = ["services = {"]
    for name, config in services.items():
        lines.append(f"  {name} = {{")
        lines.append(f"    cpu               = {config['cpu']}")
        lines.append(f"    memory            = {config['memory']}")
        lines.append(f"    replicas          = {config['replicas']}")
        lines.append(f"    load_balanced     = {'true' if config['load_balanced'] else 'false'}")
        if config.get("port"):
            lines.append(f"    port              = {config['port']}")
            lines.append(f'    health_check_path = "{config.get("health_check_path", "/health/")}"')
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines)


def _format_scaling_block(env_type: str) -> str:
    """Format the scaling block for terraform.tfvars.

    Args:
        env_type: Environment type ('staging' or 'production').

    Returns:
        Formatted HCL scaling block.
    """
    if env_type == "production":
        return """scaling = {
  web = {
    min_replicas = 2
    max_replicas = 10
    cpu_target   = 70
  }
}"""
    else:
        return "# Auto-scaling disabled in staging\nscaling = {}"


def generate_tfvars(
    app_name: str,
    env_type: str,
    deploy_config: dict | None = None,
    domain: str | None = None,
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
    env_name = f"{app_name}-{env_type}"

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

    services_block = _format_services_block(services)
    scaling_block = _format_scaling_block(env_type)
    domain_value = domain or f"{app_name}-{env_type}.example.com"

    template = load_template("standalone", env_type, "terraform.tfvars.example")
    return substitute(
        template,
        app_name=app_name,
        env_type=env_type,
        env_name=env_name,
        domain=domain_value,
        services_block=services_block,
        scaling_block=scaling_block,
    )


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
uv run python bin/environment.py status {env_name}
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

## Notes

- terraform.tfvars contains sensitive data - do not commit to git
- Use `./bin/tofu.sh` for auto-selecting AWS profile
'''
