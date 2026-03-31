# Shared Infrastructure

Creates expensive shared resources for multiple apps in a single environment: VPC, ECS cluster, ALB, and optionally Cognito auth, ElastiCache, WAF, service discovery, and shared RDS. Per-app resources are created by [app-in-shared-env](app-in-shared-env.md).

## Usage

```hcl
module "shared" {
  source = "../../modules/shared-infrastructure"

  name_prefix        = "shared-staging"
  domain_name        = "staging.example.com"
  availability_zones = ["us-west-2a", "us-west-2b"]
  route53_zone_id    = aws_route53_zone.main.zone_id

  # Optional features
  cognito_auth_enabled = true
  cache_enabled        = true
  waf_preset           = "standard"
  shared_rds_enabled   = true
  shared_rds_master_password = var.rds_password
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| name_prefix | string | Prefix for resource names |
| domain_name | string | Primary domain name |
| vpc_cidr | string | VPC CIDR block (default: 10.0.0.0/16) |
| availability_zones | list(string) | AZs to use |
| route53_zone_id | string | Route 53 zone ID (enables HTTPS + DNS) |
| cognito_auth_enabled | bool | Enable Cognito auth (default: false) |
| cache_enabled | bool | Enable shared ElastiCache (default: false) |
| waf_preset | string | WAF level: "off", "standard", "strict" |
| shared_rds_enabled | bool | Enable shared RDS instance (default: false) |
| s3_storage_enabled | bool | Enable S3 media buckets (default: false) |
| service_discovery_enabled | bool | Enable Cloud Map (default: false) |

## Outputs

Key outputs consumed by app-in-shared-env via `terraform_remote_state`:

| Output | Description |
| --- | --- |
| vpc_id | VPC ID |
| private_subnet_ids | Private subnet IDs |
| ecs_cluster_name | ECS cluster name |
| ecs_cluster_arn | ECS cluster ARN |
| ecs_security_group_id | ECS tasks security group ID |
| alb_dns_name | ALB DNS name |
| alb_https_listener_arn | HTTPS listener ARN |
| alb_default_target_group_arn | Default target group ARN |
| cognito_user_pool_id | Cognito pool ID (empty if disabled) |
| redis_url | Redis URL (empty if disabled) |
| shared_rds_address | Shared RDS hostname (empty if disabled) |
| shared_rds_master_secret_arn | Master credentials ARN (empty if disabled) |
