# App in Shared Environment

Creates per-app resources that use shared infrastructure: ALB target group and listener rule, ECR repositories, IAM roles, DNS record, and database (either a separate RDS instance or a database on the shared RDS instance).

## Usage

```hcl
module "myapp" {
  source = "../../modules/app-in-shared-env"

  app_name    = "myapp"
  environment = "staging"
  domain_name = "myapp.staging.example.com"

  # Reference shared infrastructure state
  shared_state_backend = "local"
  shared_state_path    = "../shared-staging/terraform.tfstate"

  # ALB routing
  listener_rule_priority = 100

  # Database: use shared RDS or separate instance
  use_shared_rds = true  # creates a database on the shared instance
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| app_name | string | Application name |
| environment | string | Environment name (e.g., "staging") |
| domain_name | string | Domain for this app |
| shared_state_backend | string | Backend type for shared state ("local" or "s3") |
| shared_state_path | string | Path to shared state file (local backend) |
| listener_rule_priority | number | ALB listener rule priority (unique per app) |
| use_shared_rds | bool | Use shared RDS instead of separate instance (default: false) |
| ecr_repository_names | list(string) | ECR repos to create (default: ["web"]) |
| container_port | number | Container port (default: 8000) |
| route53_zone_id | string | Route 53 zone ID for DNS (optional) |
| services | map(object) | Service sizing config for deploy.py |

## Outputs

| Output | Description |
| --- | --- |
| alb_target_group_arn | App's ALB target group ARN |
| ecs_execution_role_arn | ECS task execution role ARN |
| ecs_task_role_arn | ECS task role ARN |
| ecr_prefix | ECR repository URL prefix |
| ecr_repository_urls | Map of repo names to URLs |
| db_host | Database hostname |
| db_port | Database port |
| db_app_username_secret_arn | App DB username secret ARN (shared RDS mode) |
| db_app_password_secret_arn | App DB password secret ARN (shared RDS mode) |
| redis_url | Redis URL from shared infra (empty if disabled) |
