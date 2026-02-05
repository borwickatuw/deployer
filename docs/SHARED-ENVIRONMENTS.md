# Shared Environments Guide

This guide explains how to use shared infrastructure for running multiple simple Django applications, reducing costs while maintaining operational simplicity.

## Overview

Shared environments allow multiple small apps to share expensive AWS resources:
- VPC with NAT Gateway (~$32-50/month)
- Application Load Balancer (~$18-25/month)
- ECS Cluster (free)
- Optional: Cognito authentication (free)
- Optional: Shared ElastiCache (~$15-25/month)

Each app still gets its own:
- RDS database (data isolation)
- ALB target group and listener rule (independent routing)
- ECR repository (separate images)
- IAM roles (security isolation)

**Estimated savings: ~$65-85/month per app** (for apps that would otherwise have standalone infrastructure)

## When to Use Shared Environments

Apps qualify for shared environments if they meet ALL of these criteria:

| Criterion | Rationale |
|-----------|-----------|
| Single container/image | Simplifies routing, no worker coordination |
| Single ECS service (web only) | No celery, no background workers |
| Minimal resource needs (≤512 CPU, ≤1GB memory) | Won't overwhelm shared resources |
| No special networking | No VPC peering, no private link requirements |
| Standard Django stack | PostgreSQL + optional Redis |
| Same team ownership | Coordination for shared infra changes |

**Do NOT use shared environments for:**
- Multi-container apps (web + celery + beat)
- High-traffic apps requiring dedicated ALB capacity
- Apps with strict isolation requirements
- Apps needing custom VPC configurations

## Architecture

```
environments/
├── shared-infra-staging/        # Shared infrastructure
│   ├── main.tf
│   ├── terraform.tfvars
│   └── terraform.tfstate
│
├── app1-staging/                # Per-app environment
│   ├── main.tf                  # References shared-infra-staging
│   ├── config.toml
│   └── terraform.tfvars
│
├── app2-staging/
│   └── ...
│
└── shared-infra-production/     # Separate shared infra for production
    └── ...
```

## Quick Start

### Creating Your First Shared Environment

```bash
# Create an app with shared infrastructure
# This will prompt to create shared-infra-staging if it doesn't exist
uv run python bin/init.py environment \
    --app-name myapp \
    --env-type staging \
    --shared \
    --domain myapp.staging.example.com
```

**First time setup output:**
```
Shared infrastructure 'shared-infra-staging' doesn't exist. Create it? [y/N] y
Created: environments/shared-infra-staging/main.tf
Created: environments/shared-infra-staging/terraform.tfvars

Next steps for shared infrastructure:
  1. Edit shared-infra-staging/terraform.tfvars (set domain, Route53 zone)
  2. Deploy: ./bin/tofu.sh -chdir=shared-infra-staging init && apply

Created: environments/myapp-staging/main.tf
Created: environments/myapp-staging/config.toml
Created: environments/myapp-staging/terraform.tfvars
...
```

### Adding Another App to Existing Shared Infrastructure

```bash
# Shared infra already exists, so just creates per-app environment
uv run python bin/init.py environment \
    --app-name otherapp \
    --env-type staging \
    --shared \
    --domain otherapp.staging.example.com
```

**Output:**
```
Using existing shared infrastructure: shared-infra-staging
Created: environments/otherapp-staging/main.tf
...
```

### Deploying

```bash
# 1. Deploy shared infrastructure (first time only)
./bin/tofu.sh -chdir=environments/shared-infra-staging init
./bin/tofu.sh -chdir=environments/shared-infra-staging apply

# 2. Deploy per-app infrastructure
./bin/tofu.sh -chdir=environments/myapp-staging init
./bin/tofu.sh -chdir=environments/myapp-staging apply

# 3. Link environment to deploy.toml (one-time)
uv run python bin/link-environments.py myapp-staging /path/to/myapp/deploy.toml

# 4. Deploy application
uv run python bin/deploy.py myapp-staging
```

## Configuration

### Shared Infrastructure (`shared-infra-staging/terraform.tfvars`)

```hcl
name_prefix = "shared-infra-staging"

# Domain and certificate
domain_name     = "staging.example.com"
route53_zone_id = "Z1234567890ABC"
certificate_san = ["*.staging.example.com"]  # Wildcard for all apps

# Features
cognito_auth_enabled = true   # Enable for staging
cache_enabled        = false  # Set true for shared Redis
```

### Per-App Environment (`myapp-staging/terraform.tfvars`)

```hcl
app_name    = "myapp"
environment = "staging"

# Database (separate per app)
db_username = "myapp_admin"
db_password = "secure-password-here"

# Domain (subdomain of shared infra)
domain_name     = "myapp.staging.example.com"
route53_zone_id = "Z1234567890ABC"

# ALB routing (must be unique per app!)
listener_rule_priority = 100  # 100, 200, 300, etc.

# Service sizing
services = {
  web = {
    cpu               = 256
    memory            = 512
    replicas          = 1
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
}
```

## ALB Routing

Apps are routed based on host headers (subdomains):

```
app1.staging.example.com → app1 target group (priority 100)
app2.staging.example.com → app2 target group (priority 200)
app3.staging.example.com → app3 target group (priority 300)
```

**Important:** Each app must have a unique `listener_rule_priority`. The init script auto-assigns priorities (100, 200, 300, ...) when using `--shared`.

### Wildcard Certificate

The shared infrastructure should have a wildcard certificate covering all app subdomains:

```hcl
# In shared-infra-staging/terraform.tfvars
domain_name     = "staging.example.com"
certificate_san = ["*.staging.example.com"]
```

## Standalone vs Shared Comparison

| Aspect | Standalone | Shared |
|--------|------------|--------|
| `bin/init.py` | `--env-type staging` | `--env-type staging --shared` |
| VPC | Own | Shared |
| NAT Gateway | Own (~$32/mo) | Shared |
| ALB | Own (~$20/mo) | Shared (listener rule) |
| ECS Cluster | Own | Shared |
| RDS | Own | Own |
| Cognito | Own | Shared |
| Monthly overhead | ~$80-145 | ~$25-35 |

## Migrating Existing Apps

To migrate an existing standalone environment to shared infrastructure:

1. **Create shared infrastructure** (if not exists)
   ```bash
   uv run python bin/init.py environment \
       --app-name placeholder \
       --env-type staging \
       --shared
   # Say 'y' to create shared infra, then cancel
   ```

2. **Deploy shared infrastructure**
   ```bash
   ./bin/tofu.sh -chdir=environments/shared-infra-staging init
   ./bin/tofu.sh -chdir=environments/shared-infra-staging apply
   ```

3. **Create new shared app environment**
   ```bash
   uv run python bin/init.py environment \
       --app-name existingapp \
       --env-type staging \
       --shared \
       --domain existingapp.staging.example.com
   ```

4. **Configure and deploy**
   ```bash
   # Edit terraform.tfvars with DB credentials, etc.
   ./bin/tofu.sh -chdir=environments/existingapp-staging init
   ./bin/tofu.sh -chdir=environments/existingapp-staging apply
   ```

5. **Link and deploy app**
   ```bash
   # Link environment to deploy.toml
   uv run python bin/link-environments.py existingapp-staging /path/to/existingapp/deploy.toml

   # Update DNS to point to shared ALB and deploy
   uv run python bin/deploy.py existingapp-staging
   ```

6. **Decommission old infrastructure**
   ```bash
   # After verifying the new environment works
   ./bin/tofu.sh -chdir=environments/existingapp-staging-old destroy
   ```

## Troubleshooting

### "Listener rule priority already exists"

Each app needs a unique listener rule priority. Check existing priorities:

```bash
grep -r "listener_rule_priority" environments/*/terraform.tfvars
```

Assign the next available priority (100, 200, 300, ...).

### "Cannot resolve shared infrastructure state"

The per-app environment references the shared infrastructure state file. Ensure:

1. Shared infrastructure is deployed first
2. The `shared_state_path` in `main.tf` points to the correct location:
   ```hcl
   shared_state_path = "../shared-infra-staging/terraform.tfstate"
   ```

### App not accessible at subdomain

1. Check DNS resolves to the shared ALB
2. Verify listener rule exists: `aws elbv2 describe-rules --listener-arn <listener_arn>`
3. Check target group health: `aws elbv2 describe-target-health --target-group-arn <tg_arn>`

### Cognito auth blocking health checks

The per-app listener rules include a higher-priority rule that bypasses Cognito for `/health` and `/health/` paths. If health checks still fail:

1. Verify the health check bypass rule exists (priority = app_priority - 1)
2. Check your app's health endpoint doesn't require authentication

## Cost Breakdown

### 10 Separate Staging Environments

| Component | Per Env | x10 Total |
|-----------|---------|-----------|
| NAT Gateway | $32 | $320 |
| ALB | $20 | $200 |
| RDS (db.t3.micro) | $25 | $250 |
| ElastiCache | $20 | $200 |
| Misc | $5 | $50 |
| **Total** | **$102** | **$1,020** |

### 10 Apps on Shared Staging

| Component | Shared | Per-App (x10) | Total |
|-----------|--------|---------------|-------|
| NAT Gateway | $32 | - | $32 |
| ALB | $30 | - | $30 |
| RDS | - | $25 x 10 | $250 |
| Redis (optional) | $25 | - | $25 |
| Misc | $10 | - | $10 |
| **Total** | | | **$347** |

**Savings: ~$673/month (66%)** for staging alone.
