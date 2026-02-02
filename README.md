# Deployer

OpenTofu infrastructure and deployment tooling for AWS ECS applications.

## Getting Started

**First-time users:** Follow this documentation sequence:

1. **[Getting Started](docs/GETTING-STARTED.md)** - One-time AWS account setup (IAM roles, bootstrap)
2. **[Deployment Guide](docs/DEPLOYMENT-GUIDE.md)** - Create environments and deploy your first app
3. **[Design](docs/DESIGN.md)** - Understand the three config files (deploy.toml, terraform.tfvars, config.toml)

**Quick reference:**
- [Configuration Reference](docs/CONFIG-REFERENCE.md) - All TOML options
- [Architecture](docs/ARCHITECTURE.md) - AWS infrastructure and **cost estimates** (~$150-250/mo staging, ~$1,600-2,100/mo production)
- [Troubleshooting](docs/TROUBLESHOOTING.md) - When things go wrong

## Overview

This repository provides:

1. **Shared Infrastructure Modules** - Reusable OpenTofu modules for VPC, ECS, RDS, ElastiCache, S3, and ALB
2. **Environment Configurations** - Per-environment (staging, production) infrastructure instantiation
3. **Deploy Script** - A Python script that reads TOML application configs and deploys to ECS

## Documentation

- **[Deployment Guide](docs/DEPLOYMENT-GUIDE.md)** - First-time setup and deployment walkthrough
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** - Common issues and solutions
- **[Design](docs/DESIGN.md)** - How deployer works and why it's structured this way
- **[Configuration Reference](docs/CONFIG-REFERENCE.md)** - Complete TOML configuration options
- **[Architecture](docs/ARCHITECTURE.md)** - AWS infrastructure details and cost estimates
- **[Capacity Monitoring](docs/CAPACITY-MONITORING.md)** - ECS right-sizing and cost optimization
- **[Staging Environments](docs/STAGING-ENVIRONMENTS.md)** - Cognito auth and cost-saving scheduling
- **[Decisions](docs/DECISIONS.md)** - Architecture decision records
- **[Supported Architectures](docs/SUPPORTED-ARCHITECTURES.md)** - What's supported and out of scope

## Architecture

```
deployer/
├── modules/                    # Reusable infrastructure modules
│   ├── vpc/                    # VPC, subnets, NAT gateway
│   ├── ecs-cluster/            # ECS cluster and security groups
│   ├── ecs-service/            # Individual ECS service definition
│   ├── alb/                    # Application Load Balancer
│   ├── rds/                    # PostgreSQL database
│   ├── elasticache/            # Redis cache
│   ├── s3/                     # S3 buckets
│   ├── acm/                    # SSL/TLS certificates
│   ├── cognito/                # User authentication for staging
│   ├── compute-optimizer/      # AWS Compute Optimizer integration
│   └── staging-scheduler/      # Automatic start/stop scheduling
├── environments/               # Environment-specific configurations
│   ├── myapp-staging/
│   └── myapp-production/
├── example-deploy.toml         # Example application deploy.toml
├── example-deployer-environments/  # Example environments directory structure
├── docs/                       # Documentation
└── bin/
    ├── deploy.py               # Application deployment
    ├── manage-environment.py   # Start/stop environments
    ├── manage-cognito-access.py # Cognito user management
    └── capacity-report.py      # ECS right-sizing recommendations
```

## Usage

### 1. Set Up Infrastructure

```bash
cd environments/myapp-staging

# Copy and edit variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# Initialize and apply
tofu init
tofu plan
tofu apply
```

### 2. Deploy an Application

Create a TOML config for your application (see `example-deploy.toml`), then:

```bash
# Set environment variables from Terraform outputs
export DATABASE_URL=$(tofu -chdir=environments/myapp-staging output -raw database_url)
export REDIS_URL=$(tofu -chdir=environments/myapp-staging output -raw redis_url)
export S3_MEDIA_BUCKET=$(tofu -chdir=environments/myapp-staging output -raw s3_media_bucket)

# Deploy
uv run bin/deploy.py /path/to/your-app.toml myapp-staging

# Or dry-run first
uv run bin/deploy.py /path/to/your-app.toml myapp-staging --dry-run
```

## Application Configuration Format

Applications are configured using TOML files. See `example-deploy.toml` for a complete example.

### Sections

#### `[application]`
Basic application metadata and source location.

```toml
[application]
name = "myapp"
source = "/path/to/source"
ecr_prefix = "myapp"
```

#### `[images.*]`
Docker images to build and push.

```toml
[images.web]
context = "."
dockerfile = "Dockerfile"
```

For multi-image builds with dependencies (e.g., base images):

```toml
[images.myapp-base]
context = "."
dockerfile = "docker/myapp-base"
push = false                       # Local-only, not pushed to ECR

[images.web]
context = "."
dockerfile = "docker/myapp"
depends_on = ["myapp-base"]        # Built after myapp-base
```

#### `[services.*]`
ECS services to deploy.

```toml
[services.web]
image = "web"              # References [images.web]
port = 8000
command = ["gunicorn", "app:application"]
cpu = 256
memory = 512
replicas = 1
load_balanced = true
health_check_path = "/health/"
```

#### `[environment]`
Environment variables passed to all services.

```toml
[environment]
DJANGO_SETTINGS_MODULE = "myapp.settings"
DATABASE_URL = "${database_url}"    # Resolved at deploy time
```

#### `[secrets]`
References to SSM Parameter Store or Secrets Manager.

```toml
[secrets]
SECRET_KEY = "ssm:/myapp/staging/secret-key"
```

#### `[migrations]`
Database migration configuration.

```toml
[migrations]
enabled = true
service = "web"
command = ["python", "manage.py", "migrate"]
```

## Requirements

### For Infrastructure (OpenTofu)

- OpenTofu >= 1.6.0 (or Terraform >= 1.6.0)
- AWS CLI configured with appropriate credentials

### For Deploy Script

- [uv](https://docs.astral.sh/uv/) - Python package manager
- Python 3.11+
- Docker

Install uv if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or: brew install uv
```

Dependencies are managed in `pyproject.toml` and installed automatically when you run `uv run`.

## Adding a New Application

1. Create a TOML config file for your application (copy from `example-deploy.toml`)
2. Ensure your application has Dockerfiles in the expected locations
3. Create ECR repositories for your images:
   ```bash
   aws ecr create-repository --repository-name myapp-web
   ```
4. Run the deploy script

## Module Reference

### vpc

Creates a VPC with public and private subnets, NAT gateway, and route tables.

### ecs-cluster

Creates an ECS cluster with Fargate capacity providers and a shared security group for tasks.

### ecs-service

Creates an individual ECS service with task definition, IAM roles, and optional ALB integration.

### alb

Creates an Application Load Balancer with HTTP/HTTPS listeners and optional Cognito authentication.

### rds

Creates a PostgreSQL RDS instance in private subnets.

### elasticache

Creates a Redis ElastiCache cluster in private subnets.

### s3

Creates S3 buckets with configurable versioning and public access settings.

### acm

Creates and validates SSL/TLS certificates via AWS Certificate Manager with Route 53 DNS validation.

### cognito

Creates a Cognito User Pool for staging environment authentication.

### compute-optimizer

Enables AWS Compute Optimizer for ECS right-sizing recommendations.

### staging-scheduler

Creates Lambda and EventBridge resources to automatically start/stop staging environments on a schedule.
