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
2. **Deploy Script** - A Python script that reads TOML application configs and deploys to ECS
3. **Supporting Scripts** - Tools for Cognito user management, secrets, capacity reporting, and more

Environment configurations are stored in a separate directory (configured via `DEPLOYER_ENVIRONMENTS_DIR` in `.env`).

## Documentation

### Core Guides
- **[Deployment Guide](docs/DEPLOYMENT-GUIDE.md)** - First-time setup and deployment walkthrough
- **[Configuration Reference](docs/CONFIG-REFERENCE.md)** - Complete TOML configuration options
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** - Common issues and solutions

### Architecture & Design
- **[Design](docs/DESIGN.md)** - How deployer works and why it's structured this way
- **[Architecture](docs/ARCHITECTURE.md)** - AWS infrastructure details and cost estimates
- **[Decisions](docs/DECISIONS.md)** - Architecture decision records
- **[Supported Architectures](docs/SUPPORTED-ARCHITECTURES.md)** - What's supported and out of scope

### Topic Guides
- **[Staging Environments](docs/STAGING-ENVIRONMENTS.md)** - Cognito auth and cost-saving scheduling
- **[Shared Environments](docs/SHARED-ENVIRONMENTS.md)** - Multiple apps sharing infrastructure
- **[Multiple AWS Accounts](docs/MULTIPLE-AWS-ACCOUNTS.md)** - Staging/production account separation
- **[Capacity Monitoring](docs/CAPACITY-MONITORING.md)** - ECS right-sizing and cost optimization
- **[WAF](docs/WAF.md)** - Web Application Firewall integration

### Framework Guides
- **[Django](docs/frameworks/django.md)** - Python web framework
- **[Rails](docs/frameworks/rails.md)** - Ruby web framework
- **[Generic](docs/frameworks/generic.md)** - Any containerized application

## Repository Structure

```
deployer/
├── modules/                           # Reusable infrastructure modules
│   ├── vpc/                           # VPC, subnets, NAT gateway
│   ├── ecs-cluster/                   # ECS cluster and security groups
│   ├── ecs-service/                   # Individual ECS service definition
│   ├── alb/                           # Application Load Balancer
│   ├── rds/                           # PostgreSQL database
│   ├── elasticache/                   # Redis cache
│   ├── s3/                            # S3 buckets
│   ├── acm/                           # SSL/TLS certificates
│   ├── cognito/                       # User authentication for staging
│   ├── waf/                           # Web Application Firewall
│   ├── compute-optimizer/             # AWS Compute Optimizer integration
│   └── staging-scheduler/             # Automatic start/stop scheduling
├── bin/
│   ├── deploy.py                      # Application deployment
│   ├── tofu.sh                        # OpenTofu wrapper (auto-selects AWS profile)
│   ├── init.py                        # Initialize new apps and environments
│   ├── environment.py                 # Start/stop environments
│   ├── cognito.py                     # Cognito user management
│   ├── secrets.py                     # SSM Parameter Store secrets
│   └── capacity-report.py             # ECS right-sizing recommendations
├── example-deploy.toml                # Example application deploy.toml
├── example-deployer-environments/     # Example environments directory structure
└── docs/                              # Documentation
```

**Environment configurations** are stored separately (not in this repo):
```
~/code/deployer-environments/          # Set via DEPLOYER_ENVIRONMENTS_DIR
├── bootstrap/                         # IAM roles and shared resources
├── myapp-staging/                     # Per-environment config
│   ├── main.tf
│   ├── terraform.tfvars
│   └── config.toml
└── myapp-production/
```

## Quick Start

See [DEPLOYMENT-GUIDE.md](docs/DEPLOYMENT-GUIDE.md) for the complete walkthrough.

### Infrastructure

```bash
# Use the tofu wrapper (auto-selects correct AWS profile from config.toml)
./bin/tofu.sh init myapp-staging
./bin/tofu.sh plan myapp-staging
./bin/tofu.sh apply myapp-staging
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

| Tool | Version | Installation |
|------|---------|--------------|
| OpenTofu | >= 1.6.0 | `brew install opentofu` |
| AWS CLI | v2 | `brew install awscli` |
| Python | 3.11+ | `brew install python@3.11` |
| uv | Latest | `brew install uv` |
| Docker | Latest | `brew install docker` |

Dependencies are managed in `pyproject.toml` and installed automatically when you run `uv run`.

## Module Reference

| Module | Purpose |
|--------|---------|
| **vpc** | VPC with public/private subnets, NAT gateway, route tables |
| **ecs-cluster** | ECS cluster with Fargate capacity providers |
| **ecs-service** | ECS service with task definition, IAM roles, optional ALB |
| **alb** | Application Load Balancer with HTTP/HTTPS and optional Cognito |
| **rds** | PostgreSQL RDS instance in private subnets |
| **elasticache** | Redis ElastiCache cluster in private subnets |
| **s3** | S3 buckets with configurable versioning |
| **acm** | SSL/TLS certificates via ACM with Route 53 validation |
| **cognito** | Cognito User Pool for staging authentication |
| **waf** | Web Application Firewall with managed rules |
| **compute-optimizer** | AWS Compute Optimizer for right-sizing |
| **staging-scheduler** | Lambda/EventBridge for automatic start/stop |
