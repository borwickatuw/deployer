# AWS Architecture

This document describes the AWS infrastructure created by the deployer modules.

For information on how deployer is designed and why it separates infrastructure from deployment, see [DESIGN.md](DESIGN.md).

## Architecture Overview

```
                                    ┌─────────────────────────────────────────────────────────────┐
                                    │                        CloudFront                           │
                                    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
                                    │  │ Static/Web  │  │ API/Images  │  │   Video/Audio       │  │
                                    │  │ Distribution│  │ Distribution│  │   Distribution      │  │
                                    │  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
                                    └─────────┼────────────────┼────────────────────┼─────────────┘
                                              │                │                    │
                                              │                │                    │
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                        Public Subnets (2+ AZs)                                   │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                              Application Load Balancer                                      │ │
│  │                    ┌───────────┬───────────┬───────────────┐                                │ │
│  │                    │ /         │ /api/*    │ /svc/*        │                                │ │
│  │                    │ /admin/*  │           │               │                                │ │
│  │                    └─────┬─────┴─────┬─────┴───────────────┘                                │ │
│  └──────────────────────────┼───────────┼──────────────────────────────────────────────────────┘ │
│                             │           │                                                        │
│  ┌───────────┐              │           │                                                        │
│  │ NAT GW    │              │           │                                                        │
│  │ (per AZ)  │              │           │                                                        │
│  └───────────┘              │           │                                                        │
└─────────────────────────────┼───────────┼────────────────────────────────────────────────────────┘
                              │           │
┌─────────────────────────────┼───────────┼────────────────────────────────────────────────────────┐
│                             │    Private Subnets (2+ AZs)                                        │
│                             │           │                                                        │
│  ┌──────────────────────────┴───────────┴────────────────────────────────────────────────────┐   │
│  │                                ECS Fargate Cluster                                        │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │   │
│  │  │   Web       │  │   API       │  │   Worker    │  │   Celery    │  │   Other         │  │   │
│  │  │   Service   │  │   Service   │  │   Service   │  │   Service   │  │   Services      │  │   │
│  │  │ (2-10 tasks)│  │ (2-4 tasks) │  │ (2-8 tasks) │  │ (2-4 tasks) │  │                 │  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └───────┬─────────┘  │   │
│  └─────────┼────────────────┼────────────────┼────────────────┼─────────────────┼────────────┘   │
│            │                │                │                │                 │                │
│            ▼                ▼                ▼                ▼                 ▼                │
│  ┌───────────────────────────────────────────────────────────────────────────────────────────┐   │
│  │                                    S3 (via VPC Endpoint)                                  │   │
│  │                                    ┌──────────────────┐                                   │   │
│  │                                    │  Media Bucket    │                                   │   │
│  │                                    └──────────────────┘                                   │   │
│  └───────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                   │
│  ┌─────────────────────────┐       ┌─────────────────────────┐                                   │
│  │   RDS PostgreSQL        │       │   ElastiCache Redis     │                                   │
│  │   (Multi-AZ)            │       │   (Multi-AZ)            │                                   │
│  └─────────────────────────┘       └─────────────────────────┘                                   │
│                                                                                                   │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## AWS Services Used

### Compute

| Service                       | Purpose                                            | Sizing                            |
| ----------------------------- | -------------------------------------------------- | --------------------------------- |
| **ECS Fargate**               | Serverless containers for all application services | Variable per service              |
| **Application Load Balancer** | Request routing, SSL termination, health checks    | 1 ALB with multiple target groups |

### Storage

| Service                    | Purpose                                           | Sizing               |
| -------------------------- | ------------------------------------------------- | -------------------- |
| **S3 Standard**            | Object storage (uploads, assets, etc.)            | Variable             |
| **S3 Intelligent-Tiering** | Long-term storage for infrequently accessed files | Automatic transition |

### Database

| Service               | Purpose                              | Sizing                                          |
| --------------------- | ------------------------------------ | ----------------------------------------------- |
| **RDS PostgreSQL**    | Primary relational database          | Staging: db.t4g.micro, Prod: db.r6g.large       |
| **ElastiCache Redis** | Job queues, caching, session storage | Staging: cache.t4g.micro, Prod: cache.r6g.large |

### Networking & CDN

| Service           | Purpose                               | Notes                                |
| ----------------- | ------------------------------------- | ------------------------------------ |
| **CloudFront**    | CDN for static assets and media       | Optional, recommended for production |
| **VPC**           | Network isolation                     | /16 CIDR, 2+ AZs                     |
| **NAT Gateway**   | Outbound internet for private subnets | 1 per AZ                             |
| **VPC Endpoints** | Private S3 access                     | Gateway endpoint (free)              |

### Security & Management

| Service                 | Purpose                   | Notes                      |
| ----------------------- | ------------------------- | -------------------------- |
| **SSM Parameter Store** | Application secrets       | Secure string parameters   |
| **Secrets Manager**     | Database credentials      | Optional, for rotation     |
| **IAM**                 | Service roles, task roles | Least privilege            |
| **CloudWatch**          | Logs, metrics, alarms     | Container Insights enabled |
| **ACM**                 | SSL/TLS certificates      | For CloudFront and ALB     |
| **ECR**                 | Container image registry  | One repository per image   |

## Network Architecture

### VPC Design

```
VPC CIDR: 10.0.0.0/16

Public Subnets:
  - 10.0.1.0/24 (AZ-a) - ALB, NAT Gateway
  - 10.0.2.0/24 (AZ-b) - ALB, NAT Gateway

Private Subnets (Application):
  - 10.0.10.0/24 (AZ-a) - ECS tasks
  - 10.0.11.0/24 (AZ-b) - ECS tasks

Private Subnets (Data):
  - 10.0.20.0/24 (AZ-a) - RDS, ElastiCache
  - 10.0.21.0/24 (AZ-b) - RDS, ElastiCache
```

### Security Groups

| Security Group   | Inbound Rules             | Outbound Rules                                 |
| ---------------- | ------------------------- | ---------------------------------------------- |
| `{app}-alb-sg`   | 80, 443 from 0.0.0.0/0    | All to `{app}-ecs-sg`                          |
| `{app}-ecs-sg`   | Ports from `{app}-alb-sg` | 5432 to RDS, 6379 to Redis, 443 to S3 endpoint |
| `{app}-rds-sg`   | 5432 from `{app}-ecs-sg`  | None                                           |
| `{app}-redis-sg` | 6379 from `{app}-ecs-sg`  | None                                           |

### VPC Endpoints

| Endpoint        | Type      | Purpose                                          |
| --------------- | --------- | ------------------------------------------------ |
| S3              | Gateway   | Free, private S3 access without NAT              |
| ECR (dkr, api)  | Interface | Private ECR access (optional, reduces NAT costs) |
| CloudWatch Logs | Interface | Private logging (optional)                       |

## Storage Architecture

### S3 Bucket Structure

Buckets are named `{app}-{bucket}-{environment}` (e.g., `myapp-assets-staging`). Internal structure is up to your application.

### S3 Bucket Configuration

**Lifecycle Rules:**

| Rule                     | Transition                    |
| ------------------------ | ----------------------------- |
| Intelligent-Tiering      | 30 days → INTELLIGENT_TIERING |
| Abort incomplete uploads | 7 days                        |

**CORS Configuration:**

```json
[
  {
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedOrigins": ["https://myapp.example.com", "https://*.cloudfront.net"],
    "ExposeHeaders": ["Content-Length", "Content-Range", "ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

## Failure Modes by Component

Understanding what happens when each component fails helps prioritize resilience investments and incident response.

| Component               | If Unavailable                                          | User Impact                         | Detection                               | Recovery                               |
| ----------------------- | ------------------------------------------------------- | ----------------------------------- | --------------------------------------- | -------------------------------------- |
| **RDS PostgreSQL**      | App returns 503, all DB operations fail                 | Total outage                        | ALB unhealthy hosts, health check fails | Multi-AZ automatic failover (< 2 min)  |
| **ElastiCache Redis**   | Cache misses, sessions lost, Celery tasks fail to queue | Degraded (slower), users logged out | Application errors in logs              | Automatic reconnect when available     |
| **S3**                  | File uploads/downloads fail                             | Partial outage (file features)      | Application errors                      | S3 is highly available (99.99%); retry |
| **ALB**                 | No traffic reaches application                          | Total outage                        | External monitoring                     | ALB is highly available; check config  |
| **NAT Gateway**         | Outbound internet fails (webhooks, external APIs)       | Partial outage                      | Outbound connection errors              | NAT Gateway per AZ; check AZ health    |
| **ECS Tasks**           | Reduced capacity                                        | Degraded (slow or errors)           | ALB unhealthy hosts                     | Auto-scaling replaces tasks            |
| **CloudWatch Logs**     | Logs not visible (app continues)                        | No user impact                      | Missing recent logs                     | Automatic; logs buffered briefly       |
| **SSM Parameter Store** | New containers can't start                              | Delayed deployments                 | ECS task start failures                 | SSM is highly available                |

### Graceful Degradation Patterns

**What typically degrades gracefully:**

- **Redis unavailable**: Most apps fall back to database queries (slower but functional)
- **Celery workers down**: Tasks queue in Redis, processed when workers return
- **CloudFront unavailable**: Requests route directly to ALB (higher latency)

**What doesn't degrade:**

- **Database unavailable**: No database = no service (except static pages)
- **S3 unavailable**: File-based features fail completely
- **All ECS tasks down**: Complete outage

### Multi-AZ Resilience

With Multi-AZ enabled (production recommendation):

| Component   | AZ Failure Behavior                     |
| ----------- | --------------------------------------- |
| RDS         | Automatic failover to standby (< 2 min) |
| ElastiCache | Automatic failover to replica           |
| ECS         | Tasks redistributed to healthy AZs      |
| NAT Gateway | Other AZ's gateway handles traffic      |
| ALB         | Automatically routes to healthy AZs     |

**Single points of failure to be aware of:**

- Secrets in SSM (regional service, but highly available)
- ECR (regional, but highly available)
- Route53 (global, highly available)

## Staging vs Production Differences

| Aspect       | Staging                   | Production                       |
| ------------ | ------------------------- | -------------------------------- |
| Availability | Single-AZ                 | Multi-AZ                         |
| RDS          | db.t4g.micro, no Multi-AZ | db.r6g.large, Multi-AZ           |
| Redis        | cache.t4g.micro           | cache.r6g.large, Multi-AZ        |
| ECS Tasks    | 1 per service             | 2+ per service with auto-scaling |
| NAT Gateway  | 1                         | 1 per AZ                         |
| CloudFront   | Optional                  | Recommended                      |
| Auto-scaling | Disabled                  | Enabled                          |
| Cost         | Lower (smaller instances) | Higher (larger instances, Multi-AZ) |
