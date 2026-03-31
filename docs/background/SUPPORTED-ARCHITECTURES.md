# Supported Architectures

This document describes what the deployer supports and doesn't support.

## Supported Deployment Patterns

### ECS Fargate with Single Service

The simplest deployment: one web service receiving HTTP traffic.

```
ALB → ECS Service (web) → RDS/ElastiCache
```

**Configuration:**

```toml
# deploy.toml
[services.web]
image = "web"
port = 8000
command = ["gunicorn", "app:application", "--bind", "0.0.0.0:8000"]
```

### ECS Fargate with Multiple Services

Web service plus background workers (e.g., Celery, Sidekiq).

```
ALB → ECS Service (web)     ─┬→ RDS/ElastiCache
      ECS Service (worker)  ─┘
```

**Configuration:**

```toml
# deploy.toml
[services.web]
image = "web"
port = 8000
command = ["gunicorn", "app:application", "--bind", "0.0.0.0:8000"]

[services.worker]
image = "web"  # Same image, different command
command = ["celery", "-A", "app", "worker"]
```

Workers don't receive HTTP traffic, so they have no port or health_check_path.

## Supported Frameworks

The deployer is framework-agnostic. Any application that:

1. Runs in a Docker container
1. Listens on a configurable port (for web services)
1. Has a health check endpoint
1. Reads configuration from environment variables

Can be deployed with this tool.

### Tested Frameworks

| Framework       | Port | Documentation                                     |
| --------------- | ---- | ------------------------------------------------- |
| Django (Python) | 8000 | [Django guide](../scenarios/django.md) |
| Rails (Ruby)    | 3000 | [Rails guide](../scenarios/rails.md)   |

### Untested but Supported

These should work with the [generic framework guide](../scenarios/generic.md):

| Framework        | Typical Port |
| ---------------- | ------------ |
| Node.js/Express  | 3000         |
| Go               | 8080         |
| Java/Spring      | 8080         |
| FastAPI (Python) | 8000         |
| Flask (Python)   | 5000         |

## Supported AWS Services

### Core Infrastructure

| Service     | Purpose                         | Module                                       | Required |
| ----------- | ------------------------------- | -------------------------------------------- | -------- |
| ECS Fargate | Container orchestration         | `modules/ecs-cluster`, `modules/ecs-service` | Yes      |
| ALB         | Load balancing, SSL termination | `modules/alb`                                | Yes      |
| VPC         | Networking                      | `modules/vpc`                                | Yes      |
| ECR         | Container registry              | `modules/ecr`                                | Yes      |

### Data Stores

| Service           | Purpose                          | Module                | Required |
| ----------------- | -------------------------------- | --------------------- | -------- |
| RDS PostgreSQL    | Relational database              | `modules/rds`         | Yes      |
| ElastiCache Redis | Caching, sessions, Celery broker | `modules/elasticache` | Optional |
| S3                | Object storage (media files)     | `modules/s3`          | Optional |

### CDN & Static Content

| Service    | Purpose                   | Module               | Required |
| ---------- | ------------------------- | -------------------- | -------- |
| CloudFront | CDN for S3 media delivery | `modules/cloudfront` | Optional |

### Security & Auth

| Service             | Purpose                  | Module                             | Required           |
| ------------------- | ------------------------ | ---------------------------------- | ------------------ |
| ACM                 | SSL certificates         | `modules/acm`                      | Required for HTTPS |
| Cognito             | Staging environment auth | `modules/cognito`                  | Optional           |
| SSM Parameter Store | Secrets storage          | (managed via `bin/ssm-secrets.py`) | Yes                |
| IAM                 | Access control           | (inline in `main.tf`)              | Yes                |

### Monitoring

| Service            | Purpose                      | Module                      | Required  |
| ------------------ | ---------------------------- | --------------------------- | --------- |
| CloudWatch Logs    | Container logs               | (ECS integration)           | Yes       |
| CloudWatch Metrics | Container metrics            | (automatic)                 | Automatic |
| Compute Optimizer  | Right-sizing recommendations | `modules/compute-optimizer` | Optional  |

### DNS

| Service  | Purpose        | Module            | Required |
| -------- | -------------- | ----------------- | -------- |
| Route 53 | DNS management | `modules/route53` | Optional |

## What's NOT Supported

### Alternative Deployment Targets

| Pattern                  | Why Not Supported                                                     |
| ------------------------ | --------------------------------------------------------------------- |
| Docker Compose on EC2    | No auto-scaling, no health check recovery, single point of failure    |
| Lambda as primary target | Requires framework adapters, different timeout/connection constraints |
| Kubernetes/EKS           | Overkill for our use cases; ECS provides sufficient orchestration     |
| Non-AWS providers        | Out of scope                                                          |

**Note:** Lambda IS supported for helper utilities (e.g., the `staging-scheduler` module for turning services on/off), just not as a primary deployment target for application code.

### Specialized AWS Services

These require custom Terraform modules beyond what's provided:

| Service      | Use Case                            |
| ------------ | ----------------------------------- |
| MediaConvert | Video transcoding                   |
| OpenSearch   | Full-text search                    |
| SES          | Email sending                       |
| SQS          | Message queuing (use Redis instead) |

### Database Engines (Future)

Currently only PostgreSQL is supported. Future versions may add:

- MySQL
- MariaDB

### Complex Architectures

| Pattern                          | Why Not Supported                   |
| -------------------------------- | ----------------------------------- |
| Multiple ALBs                    | Not currently implemented           |
| Multiple target groups per ALB   | Not currently implemented           |
| Cross-region deployments         | Not currently implemented           |
| Blue-green via multiple clusters | Use ECS rolling deployments instead |

## Recommended Architecture

For most web applications, we recommend:

```
┌─────────────────────────────────────────────────────────┐
│                        VPC                               │
│  ┌────────────────────────────────────────────────────┐ │
│  │              Public Subnets                         │ │
│  │  ┌─────────────────────────────────────────────┐   │ │
│  │  │                   ALB                        │   │ │
│  │  │         (HTTPS, Cognito auth)                │   │ │
│  │  └─────────────────────────────────────────────┘   │ │
│  └────────────────────────────────────────────────────┘ │
│                          │                               │
│  ┌────────────────────────────────────────────────────┐ │
│  │              Private Subnets                        │ │
│  │  ┌───────────────┐  ┌───────────────┐              │ │
│  │  │  ECS Service  │  │  ECS Service  │              │ │
│  │  │    (web)      │  │   (worker)    │              │ │
│  │  └───────────────┘  └───────────────┘              │ │
│  │          │                  │                       │ │
│  │          ▼                  ▼                       │ │
│  │  ┌───────────────┐  ┌───────────────┐              │ │
│  │  │      RDS      │  │  ElastiCache  │              │ │
│  │  │  (PostgreSQL) │  │    (Redis)    │              │ │
│  │  └───────────────┘  └───────────────┘              │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
                    ┌───────────┐
                    │    S3     │
                    │  (media)  │
                    └───────────┘
```

### Staging vs Production

| Aspect        | Staging                        | Production                   |
| ------------- | ------------------------------ | ---------------------------- |
| Replicas      | 1                              | 2+                           |
| Instance size | Minimal (256 CPU, 512 MB)      | Larger (1024+ CPU, 2048+ MB) |
| Auto-scaling  | Disabled                       | Enabled                      |
| Cognito auth  | Enabled (protect from public)  | Disabled                     |
| RDS           | Can be stopped when not in use | Always running               |

## Related Documentation

- [DESIGN.md](DESIGN.md) - Philosophy behind configuration separation
- [CONFIG-REFERENCE.md](../CONFIG-REFERENCE.md) - Complete configuration reference
- [DECISIONS.md](DECISIONS.md) - Architectural decision records
