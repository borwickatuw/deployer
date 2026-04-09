# CloudWatch Alarms

Creates standard production alarms with SNS email notifications for ALB, RDS, ElastiCache, and ECS services. Each alarm group can be independently enabled/disabled.

## Usage

```hcl
module "alarms" {
  source = "../../modules/cloudwatch-alarms"

  name_prefix        = "myapp-production"
  notification_email = "ops@example.com"

  alb_arn_suffix          = module.alb.arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  rds_instance_id         = module.rds.db_instance_id
  elasticache_cluster_id  = "myapp-production-cache"
  ecs_cluster_name        = module.ecs_cluster.cluster_name
  ecs_service_names       = ["web", "celery"]
}
```

## Key Variables

| Variable                  | Type         | Description                               |
| ------------------------- | ------------ | ----------------------------------------- |
| name_prefix               | string       | Prefix for alarm names                    |
| notification_email        | string       | Email for alarm notifications             |
| enable_alb_alarms         | bool         | Enable ALB alarms (default: true)         |
| enable_rds_alarms         | bool         | Enable RDS alarms (default: true)         |
| enable_elasticache_alarms | bool         | Enable ElastiCache alarms (default: true) |
| enable_ecs_alarms         | bool         | Enable ECS alarms (default: true)         |
| alb_arn_suffix            | string       | ALB ARN suffix                            |
| rds_instance_id           | string       | RDS instance identifier                   |
| ecs_cluster_name          | string       | ECS cluster name                          |
| ecs_service_names         | list(string) | ECS services to monitor                   |

Alarms created: ALB 5XX errors, ALB latency (p95), unhealthy hosts, RDS CPU/storage/connections, ElastiCache memory/CPU, ECS running task count.

## Outputs

| Output         | Description                     |
| -------------- | ------------------------------- |
| sns_topic_arn  | SNS topic ARN for notifications |
| all_alarm_arns | ARNs of all created alarms      |
