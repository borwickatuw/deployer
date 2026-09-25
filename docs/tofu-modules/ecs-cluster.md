# ECS Cluster

Creates an ECS Fargate cluster with Container Insights and a shared security group for tasks.

## Usage

```hcl
module "ecs_cluster" {
  source = "../../modules/ecs-cluster"

  name_prefix = "myapp-staging"
  vpc_id      = module.vpc.vpc_id
}
```

## Key Variables

| Variable    | Type   | Description                     |
| ----------- | ------ | ------------------------------- |
| name_prefix | string | Prefix for resource names       |
| vpc_id      | string | VPC ID where ECS tasks will run |

## Outputs

| Output            | Description                     |
| ----------------- | ------------------------------- |
| cluster_name      | ECS cluster name                |
| cluster_arn       | ECS cluster ARN                 |
| security_group_id | Security group ID for ECS tasks |
