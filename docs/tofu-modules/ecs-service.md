# ECS Service

Creates an ECS Fargate service with task definition, IAM roles, CloudWatch log group, and optional ALB and service discovery integration.

## Usage

```hcl
module "web" {
  source = "../../modules/ecs-service"

  name_prefix        = "myapp-staging"
  service_name       = "web"
  cluster_arn        = module.ecs_cluster.cluster_arn
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  security_group_ids = [module.ecs_cluster.security_group_id]
  image              = "123456789012.dkr.ecr.us-west-2.amazonaws.com/myapp-staging-web:latest"
  container_port     = 8000
  log_group_name     = "/ecs/myapp-staging/web"

  alb_target_group_arn = module.alb.default_target_group_arn
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| name_prefix | string | Prefix for resource names |
| service_name | string | Name of the service (e.g., "web", "celery") |
| cluster_arn | string | ECS cluster ARN |
| vpc_id | string | VPC ID |
| subnet_ids | list(string) | Private subnet IDs |
| security_group_ids | list(string) | Security group IDs |
| image | string | Docker image URI |
| container_port | number | Container port (null for non-HTTP services) |
| log_group_name | string | CloudWatch log group name |
| cpu | number | CPU units (default: 256) |
| memory | number | Memory in MB (default: 512) |
| desired_count | number | Number of tasks (default: 1) |
| alb_target_group_arn | string | ALB target group ARN (optional) |
| environment_variables | map(string) | Environment variables |
| secrets | map(string) | SSM/Secrets Manager ARN map |
| use_spot | bool | Use Fargate Spot (default: false) |
| service_discovery_registry_arn | string | Cloud Map registry ARN (optional) |

## Outputs

| Output | Description |
| --- | --- |
| service_name | ECS service name |
| service_arn | ECS service ARN |
| task_definition_arn | Task definition ARN |
| task_role_arn | Task IAM role ARN |
