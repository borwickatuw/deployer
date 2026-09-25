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

| Variable         | Type        | Description                                          |
| ---------------- | ----------- | ---------------------------------------------------- |
| environment_name | string      | Staging environment name                             |
| ecs_cluster_name | string      | ECS cluster name                                     |
| ecs_services     | map(object) | Map of service names to replica counts               |
| rds_instance_id  | string      | RDS instance identifier                              |
| gpu_instance_id  | string      | GPU container instance to stop/start (default: none) |
| start_schedule   | string      | Cron for starting (default: 7 AM Pacific Mon-Fri)    |
| stop_schedule    | string      | Cron for stopping (default: 7 PM Pacific Mon-Fri)    |
| enabled          | bool        | Enable scheduling (default: true)                    |

## Outputs

| Output               | Description                    |
| -------------------- | ------------------------------ |
| lambda_function_name | Scheduler Lambda function name |
| stop_schedule        | Stop cron expression           |
| start_schedule       | Start cron expression          |
| scheduling_enabled   | Whether scheduling is active   |

## The GPU Container Instance

When the environment has one ([ecs-gpu-capacity](ecs-gpu-capacity.md); the root module passes its id as `gpu_instance_id`), the Lambda stops and starts it with the rest:

- **stop**: scale the services to 0, stop RDS, then wait until the cluster's container instance backed by that EC2 instance runs no tasks (the worker requeues its job on SIGTERM and exits) and stop it. The wait is capped at `GPU_DRAIN_TIMEOUT_SECONDS` (120 s, `handler.py`); a drain that outlasts it is named in the `gpu` outcome and the instance is stopped anyway, so a task that will not stop cannot keep the box billing all night. The Lambda's timeout covers the wait.
- **start**: start the instance first, so it boots and its ECS agent reconnects while RDS starts, then RDS, then the services.

The results dict gains a `gpu` entry with the same `error: ` convention, and a failed GPU step makes the invocation a 500 like any other.

## Failure Behavior

The handler (`lambda/handler.py`) fails loudly rather than silently:

- Every step is still attempted even after an earlier one fails — a
  half-scaled environment isn't abandoned mid-run — but the Lambda now
  answers `statusCode: 500`, and the invocation counts against the function's
  CloudWatch `Errors` metric, when any step reported an error. It previously
  answered `200` even on a partial failure, so a half-run recorded a silent
  success.
- An RDS status the handler cannot read (a failed `describe_db_instances`
  call) is now an error, not a skipped step. It previously treated an unread
  status the same as an unhandled one and skipped a running instance under a
  `200`.

**Before the next `tofu apply` of this module:** a CloudWatch alarm watching
this Lambda's `Errors` metric may fire on the first apply. That is not a new
break — it is a previously-hidden failure the old handler was reporting as
success. Confirm what actually happened with
`aws logs tail /aws/lambda/<environment>-scheduler --since 1h` before treating
an alarm as a regression.

**Deploying the fix needs no extra step.** `archive_file` rezips
`lambda/handler.py` into the deployment package on every `tofu apply`; the
built zip is gitignored, so the fix ships with the ordinary apply.

`LOG_LEVEL` is intentionally not threaded through the `aws_lambda_function`
resource's `environment` block as part of this — see `docs/PYSMELLY.md` (A01)
for why the in-code default is the accepted shape fleet-wide.
