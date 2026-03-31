# Staging Scheduler

Creates a Lambda function and EventBridge rules to automatically start and stop a staging environment on a schedule, reducing costs during off-hours.

## Usage

```hcl
module "scheduler" {
  source = "../../modules/staging-scheduler"

  environment_name = "myapp-staging"
  ecs_cluster_name = module.ecs_cluster.cluster_name
  rds_instance_id  = module.rds.db_instance_id

  ecs_services = {
    web    = { replicas = 1 }
    celery = { replicas = 1 }
  }

  # Default: start 7 AM Pacific Mon-Fri, stop 7 PM Pacific Mon-Fri
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| environment_name | string | Staging environment name |
| ecs_cluster_name | string | ECS cluster name |
| ecs_services | map(object) | Map of service names to replica counts |
| rds_instance_id | string | RDS instance identifier |
| start_schedule | string | Cron for starting (default: 7 AM Pacific Mon-Fri) |
| stop_schedule | string | Cron for stopping (default: 7 PM Pacific Mon-Fri) |
| enabled | bool | Enable scheduling (default: true) |

## Outputs

| Output | Description |
| --- | --- |
| lambda_function_name | Scheduler Lambda function name |
| stop_schedule | Stop cron expression |
| start_schedule | Start cron expression |
| scheduling_enabled | Whether scheduling is active |
