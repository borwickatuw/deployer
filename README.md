# Deployer

Deploy any Docker app to AWS with complete infrastructure, from a single TOML file.

## What is Deployer?

Deployer creates production-ready AWS infrastructure for containerized applications and deploys them to ECS Fargate. You describe your app in a `deploy.toml` file — what images to build, what services to run, what environment variables they need — and deployer handles everything else: VPC, load balancer, database, cache, SSL, WAF, auto-scaling, and CI/CD. It works with Django, Rails, Node.js, or anything that runs in a Docker container.

Configuration is TOML, not YAML. App developers own `deploy.toml` (what to run), infrastructure operators own OpenTofu tfvars (how big to run it). These concerns stay cleanly separated so you can resize staging without touching app code, or add a new service without thinking about infrastructure.

## Example

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

- **VPC** with public/private subnets across multiple availability zones
- **ECS Fargate cluster** running your services in private subnets
- **Application Load Balancer** with path-based routing, HTTP-to-HTTPS redirect, and health checks
- **RDS PostgreSQL** in private subnets (Multi-AZ in production)
- **ElastiCache Redis** in private subnets
- **S3 buckets** for media and static files
- **ACM SSL certificates** with automatic DNS validation
- **WAF** with AWS managed rule sets
- **Auto-scaling** based on CPU/memory utilization
- **Cognito authentication** for staging environments (keep them private)
- **Staging scheduler** that automatically stops environments nights/weekends to save costs
- **CI/CD IAM roles** using GitHub OIDC — no stored AWS credentials

## Getting Started

**First-time users:** Follow this documentation sequence:

1. **[Getting Started](docs/GETTING-STARTED.md)** - One-time AWS account setup (IAM roles, bootstrap)
1. **[Deployment Guide](docs/DEPLOYMENT-GUIDE.md)** - Create environments and deploy your first app
1. **[Design](docs/background/DESIGN.md)** - Understand the three config files (deploy.toml, terraform.tfvars, config.toml)

**Quick reference:**

- [Configuration Reference](docs/CONFIG-REFERENCE.md) - All TOML options
- [Architecture](docs/background/ARCHITECTURE.md) - AWS infrastructure details
- [Troubleshooting](docs/TROUBLESHOOTING.md) - When things go wrong

## Documentation

### Core Guides

- **[Deployment Guide](docs/DEPLOYMENT-GUIDE.md)** - First-time setup and deployment walkthrough
- **[Configuration Reference](docs/CONFIG-REFERENCE.md)** - Complete TOML configuration options
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** - Common issues and solutions

### Background (Architecture & Design)

- **[Design](docs/background/DESIGN.md)** - How deployer works and why it's structured this way
- **[Architecture](docs/background/ARCHITECTURE.md)** - AWS infrastructure details
- **[Decisions](docs/background/DECISIONS.md)** - Architecture decision records
- **[Supported Architectures](docs/background/SUPPORTED-ARCHITECTURES.md)** - What's supported and out of scope
- **[Resources](docs/resources/README.md)** - Resource module system (deploy.toml)

### Operations (How-To Guides)

- **[Production](docs/operations/PRODUCTION.md)** - Production ops, maintenance, and capacity monitoring
- **[Staging](docs/operations/STAGING.md)** - Cognito auth and cost-saving scheduling
- **[Shared Environments](docs/operations/SHARED-ENVIRONMENTS.md)** - Multiple apps sharing infrastructure
- **[Multiple AWS Accounts](docs/operations/MULTIPLE-ACCOUNTS.md)** - Staging/production account separation
- **[WAF](docs/operations/WAF.md)** - Web Application Firewall integration

### Framework Guides

- **[Django](docs/frameworks/django.md)** - Python web framework
- **[Rails](docs/frameworks/rails.md)** - Ruby web framework
- **[Generic](docs/frameworks/generic.md)** - Any containerized application

## Tools

| Script | Purpose |
|--------|---------|
| `bin/init.py` | Bootstrap AWS accounts, generate environments and deploy.toml |
| `bin/deploy.py` | Build images, run migrations, deploy to ECS |
| `bin/tofu.sh` | OpenTofu wrapper (auto-selects AWS profile from config.toml) |
| `bin/ops.py` | Production monitoring: status, health, logs, audit |
| `bin/emergency.py` | Production modifications: rollback, scale, snapshot, restore |
| `bin/environment.py` | Start/stop staging environments |
| `bin/cognito.py` | Cognito user management |
| `bin/ecs-run.py` | Run commands in ECS containers (migrations, shell, etc.) |
| `bin/ssm-secrets.py` | SSM Parameter Store secrets management |
| `bin/link-environments.py` | Link environments to deploy.toml paths (one-time setup) |
| `bin/capacity-report.py` | ECS right-sizing recommendations |
| `bin/resolve-config.py` | Resolve config.toml into JSON for CI/CD |

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

## Quick Start

See [GETTING-STARTED.md](docs/GETTING-STARTED.md) for first-time AWS account setup,
then [DEPLOYMENT-GUIDE.md](docs/DEPLOYMENT-GUIDE.md) for the complete deployment walkthrough.

### Bootstrap (one-time per AWS account)

```bash
uv run python bin/init.py bootstrap
uv run python bin/init.py bootstrap --migrate-state bootstrap-staging
```

### Infrastructure

```bash
# Use the tofu wrapper (auto-selects correct AWS profile from config.toml)
./bin/tofu.sh init myapp-staging
./bin/tofu.sh plan myapp-staging
./bin/tofu.sh apply myapp-staging

# Or use rollout to run init, plan, and apply in sequence
./bin/tofu.sh rollout myapp-staging
```

### Deployment

```bash
# Link environment to deploy.toml (one-time setup)
uv run python bin/link-environments.py myapp-staging /path/to/app/deploy.toml

# Deploy (uses linked deploy.toml)
uv run python bin/deploy.py myapp-staging

# Dry-run first
uv run python bin/deploy.py myapp-staging --dry-run
```

## Requirements

| Tool     | Version  | Installation               |
| -------- | -------- | -------------------------- |
| OpenTofu | >= 1.6.0 | `brew install opentofu`    |
| AWS CLI  | v2       | `brew install awscli`      |
| Python   | 3.11+    | `brew install python@3.11` |
| uv       | Latest   | `brew install uv`          |
| Docker   | Latest   | `brew install docker`      |

Dependencies are managed in `pyproject.toml` and installed automatically when you run `uv run`.

## Module Reference

### Core Infrastructure

| Module                    | Purpose                                                        |
| ------------------------- | -------------------------------------------------------------- |
| **bootstrap**             | IAM roles, S3 state bucket, ECS permissions boundary           |
| **vpc**                   | VPC with public/private subnets, NAT gateway, route tables     |
| **ecs-cluster**           | ECS cluster with Fargate capacity providers                    |
| **ecs-service**           | ECS service with task definition, IAM roles, optional ALB      |
| **alb**                   | Application Load Balancer with HTTP/HTTPS and optional Cognito |
| **route53**               | DNS records (A alias and CNAME) in Route 53                    |
| **acm**                   | SSL/TLS certificates via ACM with Route 53 validation          |

### Data

| Module                    | Purpose                                                        |
| ------------------------- | -------------------------------------------------------------- |
| **rds**                   | PostgreSQL RDS instance in private subnets                     |
| **elasticache**           | Redis ElastiCache cluster in private subnets                   |
| **s3**                    | S3 buckets with configurable versioning                        |
| **ecr**                   | ECR repositories with lifecycle policies                       |
| **db-secrets**            | RDS master credentials in AWS Secrets Manager                  |
| **db-users**              | PostgreSQL app/migrate users with least-privilege grants        |
| **db-on-shared-rds**      | Per-app database on a shared RDS instance                      |

### Security and Auth

| Module                    | Purpose                                                        |
| ------------------------- | -------------------------------------------------------------- |
| **cognito**               | Cognito User Pool for per-environment authentication           |
| **cognito-shared**        | Shared Cognito User Pool across multiple environments          |
| **waf**                   | Web Application Firewall with managed rules                    |
| **ci** / **ci-role**      | GitHub OIDC provider and per-project CI IAM roles              |

### Monitoring and Cost

| Module                    | Purpose                                                        |
| ------------------------- | -------------------------------------------------------------- |
| **cloudwatch-alarms**     | Standard production alarms with SNS email notifications        |
| **ecr-notifications**     | SNS alerts for critical/high ECR vulnerability scan findings   |
| **cost-budget**           | AWS Budget with email alerts at 80% and 100% threshold         |
| **staging-scheduler**     | Lambda/EventBridge for automatic start/stop scheduling         |

### Shared and Multi-App

| Module                    | Purpose                                                        |
| ------------------------- | -------------------------------------------------------------- |
| **shared-infrastructure** | Shared VPC, ECS cluster, ALB, and RDS for multiple apps        |
| **app-in-shared-env**     | Per-app resources (target group, listener rule, DB) on shared infra |
| **cloudfront-alb**        | CloudFront distribution in front of ALB for custom error pages |
