# Shared Environments Guide

This guide explains how to use shared infrastructure for running multiple simple Django applications, reducing costs while maintaining operational simplicity.

## Overview

Shared environments allow multiple small apps to share expensive AWS resources:

- Application Load Balancer
- ECS Cluster
- Optional: Cognito authentication
- Optional: **Shared RDS database**
- Optional: Shared ElastiCache
- VPC with NAT Gateway

Each app still gets its own:

- ALB target group and listener rule (independent routing)
- Database (either own RDS instance OR isolated database on shared RDS)
- ECR repository (separate images)
- IAM roles (security isolation)

Sharing the VPC, NAT Gateway, ALB, and optionally RDS/Redis significantly reduces per-app costs, especially for staging environments with many small apps.

## When to Use Shared Environments

Apps qualify for shared environments if they meet ALL of these criteria:

| Criterion                                      | Rationale                                    |
| ---------------------------------------------- | -------------------------------------------- |
| Minimal resource needs (≤512 CPU, ≤1GB memory) | Won't overwhelm shared resources             |
| No special networking                          | No VPC peering, no private link requirements |
| Same team ownership                            | Coordination for shared infra changes        |
| Single container/image                         | Simplifies routing, no worker coordination   |
| Single ECS service (web only)                  | No celery, no background workers             |
| Standard Django stack                          | PostgreSQL + optional Redis                  |

**Do NOT use shared environments for:**

- Apps needing custom VPC configurations
- Apps with strict isolation requirements
- High-traffic apps requiring dedicated ALB capacity
- Multi-container apps (web + celery + beat)

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
# Step 1: Create shared infrastructure
uv run python bin/init.py environment \
    --template shared-infra-staging \
    --domain staging.example.com

# Step 2: Create per-app environment
uv run python bin/init.py environment \
    --app-name myapp \
    --template shared-app-staging \
    --domain myapp.staging.example.com
```

**First time setup output:**

```
Created: environments/shared-infra-staging/main.tf
Created: environments/shared-infra-staging/terraform.tfvars

Next steps for shared infrastructure:
  1. Edit shared-infra-staging/terraform.tfvars (set domain, Route53 zone)
  2. Deploy: ./bin/tofu.sh rollout shared-infra-staging

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
    --template shared-app-staging \
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
./bin/tofu.sh rollout shared-infra-staging

# 2. Deploy per-app infrastructure
./bin/tofu.sh rollout myapp-staging

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

**Important:** Each app must have a unique `listener_rule_priority`. The init script auto-assigns priorities (100, 200, 300, ...) when using `--template shared-app-*`.

### Wildcard Certificate

The shared infrastructure should have a wildcard certificate covering all app subdomains:

```hcl
# In shared-infra-staging/terraform.tfvars
domain_name     = "staging.example.com"
certificate_san = ["*.staging.example.com"]
```

## Shared RDS Database

By default, each app on shared infrastructure gets its own RDS instance. For additional cost savings, you can enable a **shared RDS instance** where multiple apps share one database server, with each app getting its own isolated database.

### How It Works

```
Shared RDS Instance (e.g., db.t3.small)
├── alpha_db     ← alpha_staging_app / alpha_staging_migrate users
├── beta_db      ← beta_staging_app / beta_staging_migrate users
└── gamma_db     ← gamma_staging_app / gamma_staging_migrate users
```

PostgreSQL's permission model ensures complete isolation:

- Each app gets its own DATABASE (`CREATE DATABASE alpha_db`)
- Each app gets dedicated users (app + migrate) with passwords in Secrets Manager
- Users can only `CONNECT` to their specific database
- No `GRANT` on other databases = no access to other apps' data

### When to Use Shared RDS

**Good fit:**

- Apps owned by the same team
- Apps that don't need dedicated IOPS or specific instance sizing
- Multiple small apps with low database usage
- Staging environments where cost optimization matters

**Not recommended:**

- Apps from different teams needing separate billing
- Apps requiring dedicated database resources
- Different compliance or backup requirements per app
- Production apps with strict performance requirements

### Enabling Shared RDS

**Step 1: Enable in shared infrastructure**

```hcl
# shared-infra-staging/terraform.tfvars
shared_rds_enabled         = true
shared_rds_instance_class  = "db.t3.small"
shared_rds_allocated_storage = 20
shared_rds_master_username = "shared_admin"
shared_rds_master_password = "CHANGE-ME-generate-secure-password"  # openssl rand -base64 24

# For production, also set:
# shared_rds_backup_retention_period = 35
# shared_rds_skip_final_snapshot     = false
# shared_rds_deletion_protection     = true
# shared_rds_multi_az                = true
```

**Step 2: Configure each app to use shared RDS**

```hcl
# myapp-staging/terraform.tfvars
use_shared_rds = true
db_username    = ""  # Not used - credentials auto-generated
db_password    = ""  # Not used - credentials auto-generated
```

**Step 3: Deploy**

```bash
# Redeploy shared infrastructure (creates the RDS instance)
./bin/tofu.sh rollout shared-infra-staging

# Deploy app (creates database and users on shared RDS)
./bin/tofu.sh rollout myapp-staging
```

### Database Credentials

When using shared RDS, credentials are automatically:

1. Generated with secure random passwords
1. Stored in AWS Secrets Manager
1. Made available to ECS tasks via IAM permissions

The config.toml uses the same structure regardless of RDS mode:

```toml
[database]
host = "${tofu:db_host}"
port = "${tofu:db_port}"
name = "${tofu:db_name}"
credentials = "secretsmanager"
app_username_secret = "${tofu:db_app_username_secret_arn}"
app_password_secret = "${tofu:db_app_password_secret_arn}"
migrate_username_secret = "${tofu:db_migrate_username_secret_arn}"
migrate_password_secret = "${tofu:db_migrate_password_secret_arn}"
```

### Mixing Modes

You can mix apps using shared RDS with apps using separate RDS instances in the same shared infrastructure:

```
shared-infra-staging/
├── Shared RDS instance (if enabled)
│
├── app1-staging/  (use_shared_rds = true)  → Uses shared RDS
├── app2-staging/  (use_shared_rds = true)  → Uses shared RDS
└── app3-staging/  (use_shared_rds = false) → Has own RDS instance
```

This is useful when most apps can share but one needs dedicated resources.

## Standalone vs Shared Comparison

| Aspect           | Standalone           | Shared                        |
| ---------------- | -------------------- | ----------------------------- |
| ALB              | Own (~$20/mo)        | Shared (listener rule)        |
| `bin/init.py`    | `--template standalone-staging` | `--template shared-app-staging` |
| Cognito          | Own                  | Shared                        |
| ECS Cluster      | Own                  | Shared                        |
| Monthly overhead | ~$80-145             | ~$25-35                       |
| NAT Gateway      | Own (~$32/mo)        | Shared                        |
| RDS              | Own                  | Own                           |
| VPC              | Own                  | Shared                        |

## Migrating Existing Apps

To migrate an existing standalone environment to shared infrastructure:

1. **Create shared infrastructure** (if not exists)

   ```bash
   uv run python bin/init.py environment \
       --template shared-infra-staging \
       --domain staging.example.com
   ```

1. **Deploy shared infrastructure**

   ```bash
   ./bin/tofu.sh rollout shared-infra-staging
   ```

1. **Create new shared app environment**

   ```bash
   uv run python bin/init.py environment \
       --app-name existingapp \
       --template shared-app-staging \
       --domain existingapp.staging.example.com
   ```

1. **Configure and deploy**

   ```bash
   # Edit terraform.tfvars with DB credentials, etc.
   ./bin/tofu.sh rollout existingapp-staging
   ```

1. **Link and deploy app**

   ```bash
   # Link environment to deploy.toml
   uv run python bin/link-environments.py existingapp-staging /path/to/existingapp/deploy.toml

   # Update DNS to point to shared ALB and deploy
   uv run python bin/deploy.py existingapp-staging
   ```

1. **Decommission old infrastructure**

   ```bash
   # After verifying the new environment works
   ./bin/tofu.sh destroy existingapp-staging-old
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
1. The `shared_state_path` in `main.tf` points to the correct location:
   ```hcl
   shared_state_path = "../shared-infra-staging/terraform.tfstate"
   ```

### App not accessible at subdomain

1. Check DNS resolves to the shared ALB
1. Verify listener rule exists: `aws elbv2 describe-rules --listener-arn <listener_arn>`
1. Check target group health: `aws elbv2 describe-target-health --target-group-arn <tg_arn>`

### Cognito auth blocking health checks

The per-app listener rules include a higher-priority rule that bypasses Cognito for `/health` and `/health/` paths. If health checks still fail:

1. Verify the health check bypass rule exists (priority = app_priority - 1)
1. Check your app's health endpoint doesn't require authentication

## Cost Impact

The main savings come from sharing resources that have significant fixed costs regardless of traffic:

| Component   | Separate (per app) | Shared (once)           |
| ----------- | ------------------ | ----------------------- |
| ALB         | 1 per environment  | 1 shared                |
| ElastiCache | 1 per app          | 1 shared                |
| NAT Gateway | 1 per environment  | 1 shared                |
| RDS         | 1 per app          | 1 per app OR 1 shared   |

With 10 apps, sharing eliminates 9 NAT Gateways, 9 ALBs, and optionally 9 RDS/ElastiCache instances. Use the [AWS Pricing Calculator](https://calculator.aws/) to estimate savings for your region and instance sizes.
