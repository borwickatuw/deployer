# Design

This document explains how deployer is designed and the reasoning behind its architecture.

## Overview

Deployer separates **infrastructure** (managed by OpenTofu) from **application deployment** (managed by a Python script). These are two distinct tools that work together but serve different purposes.

## Configuration Philosophy

**Clear separation of concerns:**

| Concern                                 | Location        | Owner                    |
| --------------------------------------- | --------------- | ------------------------ |
| **What to run** (app structure)         | `deploy.toml`   | App developers           |
| **How big to run it** (sizing/capacity) | OpenTofu tfvars | Infrastructure operators |

### What goes in `deploy.toml` (Application Repository)

`deploy.toml` holds **project-specific configuration** that defines the application's structure:

- Docker images and build contexts
- Service commands and entrypoints
- Environment variables and secrets
- Health check paths (what endpoint to check)
- Path patterns for routing (e.g., `/api/*`)
- Migration commands

App developers know their application best. They define *what* their app needs to run.

### What goes in OpenTofu tfvars (Infrastructure Repository)

OpenTofu holds **sizing and capacity** that varies by environment:

- CPU and memory allocation
- Replica counts
- Auto-scaling policies (min/max replicas, CPU targets)
- Health check timing (intervals, thresholds)
- Load balancer configuration
- Infrastructure sizing (RDS instance class, Redis node type)

Infrastructure operators control *how much* resources to allocate per environment.

### Benefits of This Separation

1. **No environment-specific values in app repos**: `deploy.toml` works for any environment
1. **Clear ownership**: App devs own app config, ops owns sizing
1. **Easy environment differences**: Staging can use minimal resources, production can scale
1. **No deployment mistakes**: Can't accidentally deploy production sizing to staging
1. **Independent changes**: Can adjust sizing without touching app code

### Example

**In `deploy.toml` (app repo):**

```toml
[services.web]
image = "web"
port = 8000
command = ["gunicorn", "app:application", "--bind", "0.0.0.0:8000"]
health_check_path = "/health/"
```

**In staging `terraform.tfvars`:**

```hcl
services = {
  web = {
    cpu           = 256
    memory        = 512
    replicas      = 1
    load_balanced = true
  }
}
```

**In production `terraform.tfvars`:**

```hcl
services = {
  web = {
    cpu           = 1024
    memory        = 2048
    replicas      = 2
    load_balanced = true
  }
}
scaling = {
  web = { min_replicas = 2, max_replicas = 10, cpu_target = 70 }
}
```

## Why Two Tools?

The separation exists because infrastructure and application code have different characteristics:

| Aspect             | Infrastructure                    | Application Code               |
| ------------------ | --------------------------------- | ------------------------------ |
| Change frequency   | Rarely (adding services, scaling) | Frequently (every deploy)      |
| Risk level         | High (can delete databases)       | Lower (rolling out containers) |
| Workflow           | Plan → Review → Apply             | Build → Push → Deploy          |
| Speed requirements | Acceptable to be slow             | Should be fast                 |
| State management   | Needs tracking (Terraform state)  | Stateless                      |

Running `tofu apply` for every code deployment would be:

- **Slow** - Terraform/OpenTofu refreshes all resource state
- **Risky** - Could accidentally modify infrastructure
- **Unnecessary** - Most deploys just update container images

## What OpenTofu Manages

OpenTofu creates and manages foundational AWS infrastructure - resources that change infrequently and require careful planning:

| Resource                   | Purpose                          | Change Frequency       |
| -------------------------- | -------------------------------- | ---------------------- |
| VPC, subnets, route tables | Network foundation               | Rarely                 |
| NAT Gateways               | Outbound internet access         | Rarely                 |
| RDS PostgreSQL             | Database                         | Rarely (maybe scaling) |
| ElastiCache Redis          | Cache/queue                      | Rarely                 |
| ECS Cluster                | Container orchestration platform | Rarely                 |
| Application Load Balancer  | Traffic routing                  | When adding services   |
| S3 Buckets                 | File storage                     | Rarely                 |
| IAM Roles                  | Permissions                      | When adding services   |
| Security Groups            | Network access control           | When adding services   |
| CloudWatch Log Groups      | Logging                          | When adding services   |
| ECR Repositories           | Container image storage          | When adding images     |

OpenTofu configuration lives in:

- `modules/` - Reusable infrastructure modules
- `environments/staging/` - Staging environment configuration
- `environments/production/` - Production environment configuration

## What deploy.py Manages

The Python deploy script (`bin/deploy.py`) handles frequent application deployments by talking directly to AWS APIs via boto3:

| Action              | AWS API                       | Purpose                            |
| ------------------- | ----------------------------- | ---------------------------------- |
| ECR login           | `ecr.get_authorization_token` | Authenticate Docker to push images |
| Build & push images | Docker CLI + ECR              | Update container images            |
| Run migrations      | `ecs.run_task`                | One-off task before deployment     |
| Deploy services     | `ecs.update_service`          | Tell ECS to use new images         |
| Wait for stability  | `ecs.describe_services`       | Confirm rollout succeeded          |

The deploy script does **not** use OpenTofu - it calls AWS APIs directly. This means:

- Deployments are fast (no Terraform state locking or planning)
- No risk of accidentally modifying infrastructure
- Can be run frequently without concern

Application configuration lives in:

- `deploy.toml` in each application repository (not in deployer)

## The Gray Area: ECS Services and Task Definitions

ECS services and task definitions sit between infrastructure and application:

- **Task definitions** describe *how* to run containers (image, CPU, memory, environment variables)
- **Services** describe *how many* containers to run and how to route traffic

Currently, OpenTofu creates the initial ECS services and task definitions. The deploy script then updates them by:

1. Pushing new images to ECR (same tag, e.g., `latest`)
1. Calling `update_service` with `forceNewDeployment=True`

ECS detects the new image digest and performs a rolling deployment.

For more complex scenarios (changing CPU/memory, adding environment variables), you would either:

- Update the OpenTofu configuration and run `tofu apply`
- Or extend deploy.py to register new task definition revisions

## Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     One-Time / Infrequent                           │
│                                                                     │
│   Developer ──► tofu plan ──► Review ──► tofu apply                 │
│                                                                     │
│   Creates: VPC, RDS, Redis, ECS Cluster, ALB, S3, IAM, etc.         │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Frequent Deployments                            │
│                                                                     │
│   Developer ──► uv run deploy.py ──► Build ──► Push ──► Deploy      │
│                                                                     │
│   Does: Build images, push to ECR, run migrations, update services  │
└─────────────────────────────────────────────────────────────────────┘
```

### Typical Infrastructure Change

When you need to modify infrastructure (e.g., increase database size):

```bash
cd deployer/environments/staging

# Edit configuration
vim terraform.tfvars

# Preview changes
tofu plan

# Apply after review
tofu apply
```

### Typical Application Deployment

When you need to deploy new code:

```bash
cd deployer

# Link environment to deploy.toml (one-time, stored in local/environments.toml)
uv run python bin/link-environments.py myapp-staging /path/to/app/deploy.toml

# Deploy (uses linked deploy.toml)
uv run python bin/deploy.py myapp-staging
```

The environment's `config.toml` contains `${tofu:...}` placeholders that are resolved at deploy time by fetching outputs from OpenTofu. This eliminates the need to manually export environment variables.

## Repository Structure

```
deployer/
├── modules/                    # OpenTofu modules (infrastructure)
│   ├── vpc/
│   ├── ecs-cluster/
│   ├── ecs-service/
│   ├── rds/
│   ├── elasticache/
│   ├── s3/
│   └── alb/
├── environments/               # Environment-specific infrastructure
│   ├── myapp-staging/
│   │   ├── main.tf
│   │   ├── config.toml        # Deployment config with ${tofu:...} placeholders
│   │   └── terraform.tfvars
│   └── myapp-production/
│       ├── main.tf
│       ├── config.toml
│       └── terraform.tfvars
├── bin/
│   └── deploy.py              # Application deployment script
├── docs/
│   ├── DESIGN.md              # This file
│   ├── ARCHITECTURE.md        # AWS infrastructure details
│   ├── CONFIG-REFERENCE.md    # Configuration reference
│   └── DEPLOYMENT-GUIDE.md    # Setup guide
├── modules/                        # Reusable OpenTofu modules
│   ├── bootstrap/                  # IAM roles, S3 state bucket, permissions boundary
│   ├── ecs-service/                # ECS service definition
│   └── ...                         # Other infrastructure modules
└── templates/                      # Environment templates for init.py
```

Application repositories contain their own `deploy.toml` that references this deployer.

## Design Decisions

### Why TOML for application config?

- Human-readable and easy to edit
- Supports comments (unlike JSON)
- Simpler than YAML (no anchors, aliases, or indentation issues)
- Native Python support in 3.11+ (`tomllib`)

### Why not put deploy.toml in deployer?

Applications own their deployment configuration because:

- Different apps have different services, commands, and environment variables
- App developers know their app's requirements
- Keeps deployer generic and reusable
- App config can be versioned with app code

### Why boto3 instead of AWS CLI in deploy.py?

- Better error handling and structured responses
- No shell escaping issues
- Easier to extend programmatically
- Type hints and IDE support

### Why config.toml in each environment?

Each environment directory contains a `config.toml` that references OpenTofu outputs via `${tofu:...}` placeholders. The deploy script resolves these at deploy time. This approach provides:

- **Single source of truth**: OpenTofu outputs are the authoritative source for infrastructure values
- **Environment isolation**: Each environment's config.toml contains only that environment's configuration
- **No manual exports**: No need to run `export SERVICE_CONFIG=$(tofu output ...)` before deploying
- **Clear separation**: Sizing (in tfvars/tofu) vs app structure (in deploy.toml) vs deployment glue (in config.toml)

### Environment Directory File Breakdown

Each environment directory (e.g., `environments/myapp-staging/`) contains three key files with distinct purposes:

| File                 | Purpose                                                                                                        | Consumed By     |
| -------------------- | -------------------------------------------------------------------------------------------------------------- | --------------- |
| **main.tf**          | Infrastructure *definition* - what AWS resources to create (VPC, ECS cluster, RDS, ALB, module calls, outputs) | OpenTofu        |
| **terraform.tfvars** | Infrastructure *inputs* - environment-specific values (credentials, service sizing, domain name)               | OpenTofu        |
| **config.toml**      | Deployment *bridge* - connects tofu outputs to deploy.py, plus deploy-time settings                            | `bin/deploy.py` |

**Why this separation?**

`config.toml` exists because **deploy.py shouldn't need to understand Terraform/OpenTofu**. The deploy script needs infrastructure values (cluster name, security groups, database URL) but shouldn't have to parse HCL or call `tofu output` directly. Instead, `config.toml` provides a clean interface with `${tofu:...}` placeholders resolved at deploy time.

**What goes where:**

- **main.tf**: *How* to build infrastructure - module instantiation, resource definitions, output declarations
- **terraform.tfvars**: *What* values to use - project name, DB credentials, service CPU/memory/replicas
- **config.toml**: *What* deploy.py needs - infrastructure references (`${tofu:...}`) plus deployment-specific settings

**config.toml can hold both tofu-derived and deploy-only values:**

```toml
# Resolved from tofu outputs at deploy time
[infrastructure]
cluster_name = "${tofu:ecs_cluster_name}"

# Pure deploy-time config (not from tofu)
[deployment]
minimum_healthy_percent = 0
circuit_breaker_enabled = true
```

The `[deployment]` section demonstrates that config.toml isn't just a tofu output mirror—it can hold deployment strategy settings that don't come from infrastructure at all.

## CI/CD Deployment

For CI/CD pipelines, a separate `ci-deploy` tool provides a minimal deployment path that doesn't need OpenTofu, the deployer-environments directory, or infra-level AWS access:

```
Local (deploy.py):       deploy.toml + config.toml + tofu → Deployer
CI/CD (ci-deploy):       deploy.toml + resolved-config.json → Deployer
```

The resolved config JSON is produced by `bin/resolve-config.py` (which resolves all `${tofu:...}` placeholders) and pushed to S3. CI/CD fetches it at deploy time and passes it to the same `Deployer` class used by `deploy.py`.

Authentication uses GitHub OIDC federation — no stored AWS credentials. Each project gets a scoped `deployer-ci-{project}` IAM role that can only access that project's resources (ECR, ECS, SSM, S3 configs).

See [CI-CD.md](../CI-CD.md) for the complete setup guide.

## Resource Module System

The deployer uses a module system to separate **what an application needs** from **how an environment provides it**.

### The Problem

Without modules, applications needed AWS-specific knowledge in their deploy.toml:

```toml
# deploy.toml - knows too much about infrastructure
[environment]
DB_HOST = "${db_host}"
DB_PASSWORD = "secretsmanager:${db_password_secret_arn}"
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"
```

This couples the application to:

- Placeholder naming conventions (`${db_host}`)
- Secret storage mechanisms (SSM vs Secrets Manager)
- AWS ARN formats

### The Solution

Applications declare their needs declaratively:

```toml
# deploy.toml - infrastructure-agnostic
[database]
type = "postgresql"

[secrets]
names = ["SECRET_KEY"]
```

Environments provide implementation details:

```toml
# config.toml - environment provides how
[database]
host = "${tofu:db_host}"
credentials = "secretsmanager"
username_secret = "${tofu:db_username_secret_arn}"

[secrets]
provider = "ssm"
path_prefix = "/myapp/staging"
```

### Built-in Modules

| Module   | App Declares                     | Environment Provides              | Injects                                                      |
| -------- | -------------------------------- | --------------------------------- | ------------------------------------------------------------ |
| database | `type = "postgresql"`            | host, port, credentials           | DB_HOST, DB_PORT, DB_NAME, DB_USERNAME, DB_PASSWORD          |
| cache    | `type = "redis"`                 | url                               | REDIS_URL                                                    |
| storage  | `type = "s3"`, `buckets = [...]` | bucket names per declared bucket  | S3\_{NAME}\_BUCKET                                           |
| cdn      | `type = "cloudfront"`            | domain, key_id, private_key_param | CLOUDFRONT_DOMAIN, CLOUDFRONT_KEY_ID, CLOUDFRONT_PRIVATE_KEY |
| secrets  | `names = [...]`                  | provider, path_prefix             | Each named secret                                            |

See [Resources](../resources/README.md) for complete module reference.
