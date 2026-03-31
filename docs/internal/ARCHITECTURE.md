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

## Cost Considerations

Costs depend heavily on your instance sizes, traffic, storage, and region. The primary cost drivers are:

- **NAT Gateway** — per-hour + per-GB charges; often the largest fixed cost in staging
- **RDS and ElastiCache** — instance size dominates; staging can use `t4g.micro`, production uses larger instances
- **ECS Fargate** — per-vCPU and per-GB-hour; scales with number of tasks
- **ALB** — base hourly charge plus per-LCU usage

Use the [AWS Pricing Calculator](https://calculator.aws/) to estimate costs for your specific configuration.

### Cost Optimization

1. **Reserved Instances**: RDS and ElastiCache reserved instances save 30-50%
1. **Fargate Spot**: Use for interruptible workloads (workers) for 70% savings
1. **S3 Lifecycle**: Move infrequently accessed files to Intelligent-Tiering
1. **CloudFront Caching**: Higher cache hit ratio reduces origin requests
1. **NAT Gateway**: Consider NAT instances for very low traffic
1. **Right-sizing**: Monitor actual usage and adjust instance sizes

## Security Considerations

### Network Security

- All ECS tasks run in private subnets (no public IPs)
- Internet access via NAT Gateway only
- S3 access via VPC Gateway Endpoint (never traverses public internet)
- ALB terminates TLS; internal traffic within VPC is unencrypted but isolated

### Data Security

- **S3**: Server-side encryption (SSE-S3) enabled by default
- **RDS**: Encryption at rest (AWS-managed key), encryption in transit
- **ElastiCache**: Encryption at rest and in transit
- **Secrets**: Stored in SSM Parameter Store as SecureString

### Access Control

- ECS tasks use IAM roles (no long-lived access keys)
- S3 bucket policy restricts access to VPC endpoint and CloudFront OAC
- RDS security group only allows connections from ECS tasks
- Principle of least privilege for all IAM roles

### Compliance Recommendations

- Enable **CloudTrail** for audit logging of AWS API calls
- Enable **VPC Flow Logs** for network monitoring
- Enable **GuardDuty** for threat detection
- Regular security patching via ECR image updates
- Enable **AWS Config** for compliance monitoring

### IMDS Protection (EC2 Instance Metadata Service)

**Current architecture:** Fargate-only (no IMDS exposure)

This infrastructure uses ECS Fargate exclusively, which does not have access to the EC2 Instance Metadata Service (IMDS). Therefore, no IMDS protection is required.

**If EC2 capacity providers are added in the future:**

EC2-backed ECS tasks can access the host's IMDS at `169.254.169.254`. This is a security risk because:

- SSRF vulnerabilities could leak IAM credentials
- Compromised containers could access instance metadata
- AWS credentials could be exfiltrated

To protect against this, configure the EC2 launch template with:

```hcl
resource "aws_launch_template" "ecs" {
  # ... other configuration ...

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"    # Enforce IMDSv2
    http_put_response_hop_limit = 1             # Block container access
  }
}
```

The `http_put_response_hop_limit = 1` setting is critical:

- IMDSv2 requires a token obtained via HTTP PUT
- Hop limit of 1 prevents the request from traversing the container network
- Containers cannot obtain a token, so IMDS is inaccessible

**Defense in depth:** Applications should also implement SSRF protection at the application layer (blocking private IP ranges like `169.254.0.0/16`). See individual application security documentation for details.

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

### Application-Specific Failure Modes

Each application should document its specific failure modes in `docs/OPERATIONS.md`. See:

- `~/code/claude-meta/best-practices/OPERATIONS.md` for template
- Application repos for specific documentation

## Scaling Strategies

### Auto-Scaling

Configure auto-scaling in your `deploy.toml`:

```toml
[scaling.web]
min_replicas = 2
max_replicas = 10
cpu_target = 70
```

| Trigger Type  | Description                              | Use Case              |
| ------------- | ---------------------------------------- | --------------------- |
| CPU Target    | Scale when average CPU exceeds target    | General workloads     |
| Memory Target | Scale when average memory exceeds target | Memory-intensive apps |
| Request Count | Scale based on requests per target       | Web services          |

### Database Scaling

- **Read Replicas**: Add RDS read replicas for read-heavy workloads
- **Connection Pooling**: Add PgBouncer if connection limits reached
- **Aurora**: Consider Aurora PostgreSQL Serverless v2 for auto-scaling

### CDN Scaling

CloudFront scales automatically. Consider:

- **Origin Shield**: Additional caching layer to reduce origin load
- **Lambda@Edge**: Custom logic at edge locations

## Monitoring and Alerting

### Recommended CloudWatch Alarms

| Alarm                   | Metric                        | Threshold           |
| ----------------------- | ----------------------------- | ------------------- |
| High ALB 5xx Rate       | HTTPCode_ELB_5XX_Count        | > 10 in 5 min       |
| High ALB Latency        | TargetResponseTime            | > 5s p95 for 5 min  |
| RDS High CPU            | CPUUtilization                | > 80% for 10 min    |
| RDS Low Storage         | FreeStorageSpace              | < 20GB              |
| ElastiCache High Memory | DatabaseMemoryUsagePercentage | > 80%               |
| ECS Task Failures       | RunningTaskCount              | < desired for 5 min |

### Log Groups

Services create log groups at `/ecs/{app}-{environment}/{service}`:

```
/ecs/myapp-staging/web
/ecs/myapp-staging/celery
/ecs/myapp-staging/worker
```

### Log Insights Queries

**Find errors:**

```
fields @timestamp, @message
| filter @message like /ERROR/
| sort @timestamp desc
| limit 100
```

**Slow requests:**

```
fields @timestamp, @message
| filter @message like /took.*ms/
| parse @message /took (?<duration>\d+)ms/
| filter duration > 1000
| sort duration desc
```

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
