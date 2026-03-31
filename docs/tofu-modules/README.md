# OpenTofu Modules

Reusable infrastructure modules in `modules/`. These are referenced from environment `main.tf` files.

For the deploy.toml resource system (database, cache, storage, etc.), see [Resources](../resources/README.md).

## Core Infrastructure

| Module | Purpose |
| --- | --- |
| [vpc](vpc.md) | VPC with public/private subnets, NAT Gateway, and VPC Flow Logs |
| [ecs-cluster](ecs-cluster.md) | ECS Fargate cluster with Container Insights and capacity providers |
| [ecs-service](ecs-service.md) | ECS Fargate service with task definition, IAM roles, and CloudWatch logs |
| [alb](alb.md) | Application Load Balancer with HTTPS, Cognito auth, and path-based routing |
| [ecr](ecr.md) | ECR repositories with image scanning and lifecycle policies |

## Database

| Module | Purpose |
| --- | --- |
| [rds](rds.md) | PostgreSQL RDS instance with backups, monitoring, and encryption |
| [db-users](db-users.md) | Lambda that creates app (DML-only) and migrate (DDL+DML) database users |
| [db-secrets](db-secrets.md) | Stores RDS master credentials in AWS Secrets Manager |
| [db-on-shared-rds](db-on-shared-rds.md) | Creates isolated database and users on an existing shared RDS instance |

## Caching and Storage

| Module | Purpose |
| --- | --- |
| [elasticache](elasticache.md) | Redis ElastiCache cluster with security group for ECS access |
| [s3](s3.md) | S3 bucket with versioning, encryption, CORS, and CloudFront policies |

## DNS and TLS

| Module | Purpose |
| --- | --- |
| [acm](acm.md) | SSL/TLS certificate with Route 53 DNS validation |
| [route53](route53.md) | DNS records (A alias and CNAME) in Route 53 |
| [cloudfront-alb](cloudfront-alb.md) | CloudFront distribution in front of ALB with custom error pages |

## Authentication

| Module | Purpose |
| --- | --- |
| [cognito](cognito.md) | Cognito user pool for ALB authentication (per-environment) |
| [cognito-shared](cognito-shared.md) | Shared Cognito user pool with multiple app clients for SSO |

## Security

| Module | Purpose |
| --- | --- |
| [waf](waf.md) | AWS WAF with managed rules, rate limiting, geo-blocking, and IP allowlist |

## CI/CD

| Module | Purpose |
| --- | --- |
| [ci](ci.md) | Account-wide GitHub OIDC provider and S3 bucket for resolved configs |
| [ci-role](ci-role.md) | Per-project GitHub Actions IAM role with OIDC trust |

## Account Setup

| Module | Purpose |
| --- | --- |
| [bootstrap](bootstrap.md) | S3 state bucket, IAM roles, and permissions boundary |

## Composite Modules

| Module | Purpose |
| --- | --- |
| [shared-infrastructure](shared-infrastructure.md) | Complete shared environment: VPC, ECS, ALB, optional Cognito/WAF/ElastiCache/RDS |
| [app-in-shared-env](app-in-shared-env.md) | Per-app resources on shared infrastructure: database, ECR, ALB target group, IAM |

## Operations

| Module | Purpose |
| --- | --- |
| [staging-scheduler](staging-scheduler.md) | Lambda + EventBridge to auto start/stop ECS and RDS on a schedule |
| [cloudwatch-alarms](cloudwatch-alarms.md) | SNS-based CloudWatch alarms for ALB, RDS, ElastiCache, and ECS |
| [cost-budget](cost-budget.md) | AWS Budget with email alerts at 80% and 100% of monthly threshold |
| [ecr-notifications](ecr-notifications.md) | EventBridge rule to SNS for ECR vulnerability scan alerts |
