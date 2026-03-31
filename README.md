# Deployer

Deploy any Docker app to AWS with complete infrastructure, from a single TOML file.

## What is Deployer?

Deployer creates production-ready AWS infrastructure for containerized applications and deploys them to ECS Fargate. You describe your app in a `deploy.toml` file — what images to build, what services to run, what environment variables they need — and deployer handles everything else: VPC, load balancer, database, cache, SSL, WAF, auto-scaling, and CI/CD. It works with Django, Rails, Node.js, or anything that runs in a Docker container.

Configuration is TOML, not YAML. App developers own `deploy.toml` (what to run), infrastructure operators own OpenTofu tfvars (how big to run it). These concerns stay cleanly separated so you can resize staging without touching app code, or add a new service without thinking about infrastructure.

## Example

This assumes you've completed the one-time [AWS account setup](docs/GETTING-STARTED.md).

**1. Define your app** in `deploy.toml` (lives in your app repo):

```toml
[application]
name = "myapp"

[images.web]
context = "."
dockerfile = "Dockerfile"

[services.web]
image = "web"
port = 8000
command = ["gunicorn", "app:application", "--bind", "0.0.0.0:8000"]
health_check_path = "/health/"

[environment]
DATABASE_URL = "${database_url}"
REDIS_URL = "${redis_url}"

[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"
```

**2. Generate the environment** from a template:

```bash
uv run python bin/init.py environment --app-name myapp --template standalone-staging
```

**3. Create the infrastructure** (VPC, ECS, RDS, Redis, ALB, SSL — all of it):

```bash
./bin/tofu.sh rollout myapp-staging
```

**4. Deploy:**

```bash
uv run python bin/deploy.py myapp-staging
```

Deployer builds your Docker image, pushes it to ECR, runs migrations, and updates ECS services with zero-downtime rolling deployment.

## What You Get

A single `tofu apply` creates all of this:

- **ACM SSL certificates** with automatic DNS validation
- **Application Load Balancer** with path-based routing, HTTP-to-HTTPS redirect, and health checks
- **Auto-scaling** based on CPU/memory utilization
- **CI/CD IAM roles** using GitHub OIDC — no stored AWS credentials
- **Cognito authentication** for staging environments (keep them private)
- **ECS Fargate cluster** running your services in private subnets
- **ElastiCache Redis** in private subnets
- **RDS PostgreSQL** in private subnets (Multi-AZ in production)
- **S3 buckets** for media and static files
- **Staging scheduler** that automatically stops environments nights/weekends to save costs
- **VPC** with public/private subnets across multiple availability zones
- **WAF** with AWS managed rule sets

## Getting Started

**First-time users:** Follow this documentation sequence:

1. **[Getting Started](docs/GETTING-STARTED.md)** - One-time AWS account setup (IAM roles, bootstrap)
1. **[Deployment Guide](docs/DEPLOYMENT-GUIDE.md)** - Create environments and deploy your first app
1. **[Design](docs/internal/DESIGN.md)** - Understand the three config files (deploy.toml, terraform.tfvars, config.toml)

**Quick reference:**

- [Architecture](docs/internal/ARCHITECTURE.md) - AWS infrastructure details
- [Configuration Reference](docs/CONFIG-REFERENCE.md) - All TOML options
- [Troubleshooting](docs/TROUBLESHOOTING.md) - When things go wrong

## Documentation

### Core Guides

- **[Configuration Reference](docs/CONFIG-REFERENCE.md)** - Complete TOML configuration options
- **[Deployment Guide](docs/DEPLOYMENT-GUIDE.md)** - First-time setup and deployment walkthrough
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** - Common issues and solutions

### Background (Architecture & Design)

- **[Architecture](docs/internal/ARCHITECTURE.md)** - AWS infrastructure details
- **[Decisions](docs/internal/DECISIONS.md)** - Architecture decision records
- **[Design](docs/internal/DESIGN.md)** - How deployer works and why it's structured this way
- **[Resources](docs/resources/README.md)** - Resource module system (deploy.toml)
- **[Supported Architectures](docs/internal/SUPPORTED-ARCHITECTURES.md)** - What's supported and out of scope

### Operations (How-To Guides)

- **[Operations](docs/operations/)** — Post-deployment tasks, "where to make changes" reference
- **[Multiple AWS Accounts](docs/operations/MULTIPLE-ACCOUNTS.md)** — Staging/production account separation
- **[Production](docs/operations/PRODUCTION.md)** — Monitoring, maintenance, emergency procedures
- **[Shared Environments](docs/operations/SHARED-ENVIRONMENTS.md)** — Multiple apps sharing infrastructure
- **[Staging](docs/operations/STAGING.md)** — Cognito auth and cost-saving scheduling

### Scenario Guides

- **[CI/CD](docs/scenarios/ci-cd.md)** - GitHub Actions deployment pipeline
- **[Django](docs/scenarios/django.md)** - Python web framework
- **[Generic](docs/scenarios/generic.md)** - Any containerized application
- **[Passive Deployer](docs/scenarios/passive-deployer.md)** - Using deployer tools with external infrastructure
- **[Rails](docs/scenarios/rails.md)** - Ruby web framework

## Tools

| Script | Purpose |
|--------|---------|
| `bin/capacity-report.py` | ECS right-sizing recommendations |
| `bin/cognito.py` | Cognito user management |
| `bin/deploy.py` | Build images, run migrations, deploy to ECS |
| `bin/ecs-run.py` | Run commands in ECS containers (migrations, shell, etc.) |
| `bin/emergency.py` | Production modifications: rollback, scale, snapshot, restore |
| `bin/environment.py` | Start/stop staging environments |
| `bin/init.py` | Bootstrap AWS accounts, generate environments and deploy.toml |
| `bin/link-environments.py` | Link environments to deploy.toml paths (one-time setup) |
| `bin/ops.py` | Production monitoring: status, health, logs, audit |
| `bin/resolve-config.py` | Resolve config.toml into JSON for CI/CD |
| `bin/ssm-secrets.py` | SSM Parameter Store secrets management |
| `bin/tofu.sh` | OpenTofu wrapper (auto-selects AWS profile from config.toml) |

## Repository Structure

```
deployer/
├── bin/                               # CLI tools (see Tools section above)
├── src/deployer/                      # Python library
├── modules/                           # Reusable OpenTofu modules (see Module Reference below)
├── templates/                         # Environment templates for init.py
└── docs/                              # Documentation
```

**Environment configurations** are stored separately (not in this repo):

```
~/deployer-environments/          # Set via DEPLOYER_ENVIRONMENTS_DIR
├── bootstrap-staging/                 # IAM roles and shared resources (per account)
├── myapp-staging/                     # Per-environment config
│   ├── main.tf
│   ├── terraform.tfvars
│   └── config.toml
└── myapp-production/
```

## Requirements

| Tool     | Version  | Installation               |
| -------- | -------- | -------------------------- |
| AWS CLI  | v2       | `brew install awscli`      |
| Docker   | Latest   | `brew install docker`      |
| OpenTofu | >= 1.6.0 | `brew install opentofu`    |
| Python   | 3.11+    | `brew install python@3.11` |
| uv       | Latest   | `brew install uv`          |

Dependencies are managed in `pyproject.toml` and installed automatically when you run `uv run`.

## Module Reference

See **[OpenTofu Modules](docs/tofu-modules/README.md)** for the complete reference (25 modules covering core infrastructure, database, networking, auth, CI/CD, and monitoring).

See **[Resources](docs/resources/README.md)** for the deploy.toml resource module system (database, cache, storage, CDN, secrets).
