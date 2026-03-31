# Using Deployer Tools with External Infrastructure

Deployer's deployment and operational tools (deploy.py, ecs-run.py, ops.py,
emergency.py, environment.py) can work with infrastructure managed outside of
deployer's own OpenTofu modules. This means you can manage your infrastructure
however you like -- your own tofu modules, CDK, CloudFormation, or even manual
setup -- and still use deployer for deployments, running commands, monitoring,
and emergency operations.

## How It Works

Deployer's tools read a `config.toml` file from an environment directory in
`deployer-environments/`. Normally this file contains `${tofu:...}` placeholders
that resolve by running `tofu output` in the same directory. With external
infrastructure, you use the `[tofu].dir` setting to point at your own tofu
directory, or simply hardcode values directly.

## Setup

### 1. Create the Environment Directory

Create a directory in `deployer-environments/` with just a `config.toml`:

```
deployer-environments/
└── myapp-staging/
    └── config.toml       # Only file needed
```

No `main.tf`, no `terraform.tfvars`, no tofu state -- just the config file.

### 2. Configure `[tofu].dir` (Optional)

If your infrastructure is managed by tofu and you want to resolve values
dynamically, point `[tofu].dir` at your tofu directory:

```toml
[tofu]
dir = "~/code/myapp/infra/staging"
```

This supports absolute paths, relative paths (resolved against the environment
directory), and `~` expansion. When set, all `${tofu:...}` placeholders resolve
by running `tofu output -json` in that directory instead of the environment
directory.

If your infrastructure isn't managed by tofu, or you prefer to hardcode
everything, omit `[tofu]` entirely and use literal values throughout.

### 3. Write config.toml

You can mix `${tofu:...}` placeholders (for values your tofu exposes) with
hardcoded values (for everything else).

## Required Config Fields

### Minimum for Any Tool

Every deployer tool loads config.toml. These fields are always needed:

```toml
[aws]
deploy_profile = "your-aws-profile"    # Profile with ECS/ECR permissions

[environment]
type = "staging"                       # or "production"
```

### By Tool

**ecs-run.py** (run commands in containers):

```toml
[infrastructure]
cluster_name = "myapp-staging"
```

Also requires a `deploy.toml` linked via `bin/link-environments.py` (for the
`run` subcommand to find named commands).

**environment.py** (start/stop staging environments):

```toml
[infrastructure]
cluster_name = "myapp-staging"
rds_instance_id = "myapp-staging"      # Optional, for stopping/starting RDS

[services]
config = { web = { replicas = 1 }, celery = { replicas = 1 } }
```

**ops.py** (monitoring and health checks):

```toml
[infrastructure]
cluster_name = "myapp-staging"
rds_instance_id = "myapp-staging"
target_group_arn = "arn:aws:elasticloadbalancing:..."

[services]
config = { web = { ... }, celery = { ... } }

[cache]
url = "redis://..."                    # Optional, for cache status checks
```

**emergency.py** (rollback, scale, snapshot, restore):

```toml
[infrastructure]
cluster_name = "myapp-staging"
rds_instance_id = "myapp-staging"
```

**deploy.py** (full deployments) -- needs the most config:

```toml
[infrastructure]
cluster_name = "myapp-staging"
ecr_prefix = "myapp-staging"
execution_role_arn = "arn:aws:iam::123456789012:role/myapp-staging-ecs-execution"
task_role_arn = "arn:aws:iam::123456789012:role/myapp-staging-ecs-task"
security_group_id = "sg-..."
private_subnet_ids = ["subnet-...", "subnet-..."]
target_group_arn = "arn:aws:elasticloadbalancing:..."
rds_instance_id = "myapp-staging"

[services]
config = { web = { cpu = 256, memory = 512, replicas = 1, load_balanced = true, port = 8000 } }

[database]
host = "myapp-staging.xxx.us-west-2.rds.amazonaws.com"
port = 5432
name = "myapp"

[cache]
url = "redis://myapp-staging.xxx.0001.usw2.cache.amazonaws.com:6379"

[deployment]
minimum_healthy_percent = 0
maximum_percent = 100
circuit_breaker_enabled = true
circuit_breaker_rollback = true
```

Also requires a linked `deploy.toml`.

## Tofu Outputs for Full Compatibility

If you want your tofu to expose outputs that work as `${tofu:...}` placeholders,
here are the output names deployer expects:

| Output Name | Type | Used By |
|---|---|---|
| `alb_dns_name` | string | ops.py |
| `alb_target_group_arn` | string | deploy.py, ops.py |
| `db_host` | string | deploy.py |
| `db_name` | string | deploy.py |
| `db_port` | number | deploy.py |
| `domain_name` | string | ops.py |
| `ecr_prefix` | string | deploy.py |
| `ecs_cluster_name` | string | All tools |
| `ecs_execution_role_arn` | string | deploy.py |
| `ecs_security_group_id` | string | deploy.py |
| `ecs_task_role_arn` | string | deploy.py |
| `health_check_config` | object | deploy.py |
| `private_subnet_ids` | list(string) | deploy.py |
| `rds_instance_id` | string | ops.py, emergency.py, environment.py |
| `redis_url` | string | deploy.py |
| `scaling_config` | map(object) | deploy.py |
| `service_config` | map(object) | deploy.py |
| `service_discovery_registries` | map(string) | deploy.py (if using service discovery) |
| `service_target_groups` | map(string) | deploy.py (multi-service) |

You don't need all of these. Only expose what you want to resolve dynamically;
hardcode the rest in config.toml.

## Example: Mixed Config

This config.toml resolves three values from tofu and hardcodes the rest:

```toml
[tofu]
dir = "~/code/myapp/infra/staging"

[aws]
deploy_profile = "deployer-app"
infra_profile = "deployer-infra"

[environment]
type = "staging"
domain_name = "myapp-staging.example.com"

[infrastructure]
cluster_name = "${tofu:ecs_cluster_name}"
security_group_id = "${tofu:ecs_security_group_id}"
private_subnet_ids = "${tofu:private_subnet_ids}"
execution_role_arn = "arn:aws:iam::123456789012:role/myapp-staging-ecs-execution"
task_role_arn = "arn:aws:iam::123456789012:role/myapp-staging-ecs-task"
target_group_arn = "arn:aws:elasticloadbalancing:..."
rds_instance_id = "myapp-staging"
ecr_prefix = "myapp-staging"

[database]
host = "myapp-staging.xxx.us-west-2.rds.amazonaws.com"
port = 5432
name = "myapp"

[cache]
url = "redis://myapp-staging.xxx.0001.usw2.cache.amazonaws.com:6379"
```

## Limitations

- **Cognito**: If using deployer's shared Cognito pool from bootstrap, add your
  app to `bootstrap-staging/cognito.auto.tfvars`. The Cognito pool itself is
  independent of your infrastructure.
- **resolve-config.py**: Works with `[tofu].dir`, so CI/CD config resolution is
  supported.
- **SSM secrets**: `bin/ssm-secrets.py` works independently -- it only needs the
  `[secrets].path_prefix` from config.toml.
- **tofu.sh wrapper**: The `bin/tofu.sh` wrapper is designed for deployer-managed
  environments. Use your own tofu workflow for infrastructure changes.
