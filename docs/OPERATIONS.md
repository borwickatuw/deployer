# Operations

Day-to-day operations for deployed applications. For initial deployment, see [DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md).

## Post-Deployment Tasks

### Link Environment to deploy.toml (One-Time Setup)

Link your environment to its deploy.toml so you don't have to specify `--deploy-toml` on every command:

```bash
# Link environment to deploy.toml (stored locally, gitignored)
uv run python bin/link-environments.py myapp-staging ~/code/myapp/deploy.toml

# List all links
uv run python bin/link-environments.py --list
```

### Run Commands in ECS

Use `ecs-run.py run` to execute commands defined in your deploy.toml's `[commands]` section:

```bash
# List available commands
uv run python bin/ecs-run.py run myapp-staging --list-commands

# Run migrations
uv run python bin/ecs-run.py run myapp-staging migrate

# Run Django deployment checks
uv run python bin/ecs-run.py run myapp-staging check
```

If the environment isn't linked, you can still specify `--deploy-toml` explicitly:

```bash
uv run python bin/ecs-run.py run myapp-staging migrate --deploy-toml ../app/deploy.toml
```

**Note:** Only non-interactive commands can be run via ecs-run.py. For interactive commands (shell, createsuperuser), use `ecs-run.py exec` with ECS Exec enabled:

```bash
# Run arbitrary command (requires ECS Exec)
uv run python bin/ecs-run.py exec myapp-staging python manage.py createsuperuser --email admin@example.com
```

### Create Cognito User (if using authentication)

```bash
uv run python bin/cognito.py create myapp-staging \
  --email user@example.com \
  --clipboard
```

See [STAGING.md](operations/STAGING.md) for full user management documentation.

______________________________________________________________________

## Managing Environment Lifecycle

Staging environments can be stopped during off-hours to reduce costs (~50-60% savings on compute).

```bash
# Check environment status
uv run python bin/environment.py status myapp-staging

# Stop environment (scales ECS to 0, stops RDS)
uv run python bin/environment.py stop myapp-staging

# Start environment (starts RDS, waits for it, restores ECS replicas)
uv run python bin/environment.py start myapp-staging
```

**Notes:**

- ElastiCache and ALB cannot be stopped (only deleted), so these continue to incur costs
- RDS auto-restarts after 7 days if stopped (AWS limitation)
- Start always waits for RDS to be available before scaling ECS back up

See [STAGING.md](operations/STAGING.md) for automated scheduling to stop/start environments on a schedule.

______________________________________________________________________

## Where to Make Changes

Quick reference for which file to edit based on what you want to change.

### Service Configuration

| If you want to...                 | Edit this file                               | Section/Key                                   |
| --------------------------------- | -------------------------------------------- | --------------------------------------------- |
| Change CPU or memory              | `services.auto.tfvars`                       | `services.*.cpu`, `services.*.memory`         |
| Change replica count              | `services.auto.tfvars`                       | `services.*.replicas`                         |
| Add auto-scaling                  | `services.auto.tfvars`                       | `scaling` block                               |
| Change the Docker command         | `deploy.toml` (app repo)                     | `services.*.command`                          |
| Change health check path          | `deploy.toml` (app repo)                     | `services.*.health_check_path`                |
| Change health check timing        | `services.auto.tfvars`                       | `health_check` block                          |
| Set minimum resource requirements | `deploy.toml` (app repo)                     | `services.*.min_cpu`, `services.*.min_memory` |
| Add a new service                 | Both: `deploy.toml` + `services.auto.tfvars` | Define service in both                        |

### Environment Variables & Secrets

| If you want to...                  | Edit this file                 | Section/Key                        |
| ---------------------------------- | ------------------------------ | ---------------------------------- |
| Add/change an environment variable | `deploy.toml` (app repo)       | `[environment]`                    |
| Add/change a secret                | `deploy.toml` (app repo) + SSM | `[secrets]` + create SSM parameter |
| Change database URL injection      | `deploy.toml` (app repo)       | `[environment]` or `[secrets]`     |

### Infrastructure

| If you want to...       | Edit this file         | Then run     |
| ----------------------- | ---------------------- | ------------ |
| Change domain name      | `services.auto.tfvars` | `tofu apply` |
| Change database size    | `main.tf`              | `tofu apply` |
| Change Redis/cache size | `main.tf`              | `tofu apply` |
| Add S3 bucket           | `main.tf`              | `tofu apply` |
| Change VPC/networking   | `main.tf`              | `tofu apply` |

### Deployment Behavior

| If you want to...               | Edit this file | Section/Key                          |
| ------------------------------- | -------------- | ------------------------------------ |
| Speed up staging deployments    | `config.toml`  | `[deployment]`                       |
| Enable/disable circuit breaker  | `config.toml`  | `deployment.circuit_breaker_enabled` |
| Change Cognito test credentials | `config.toml`  | `[cognito]`                          |

### Quick Decision Tree

```
Want to change HOW the app runs (command, env vars, Dockerfile)?
  → Edit deploy.toml in your app repo

Want to change HOW MUCH resources (CPU, memory, replicas)?
  → Edit services.auto.tfvars in $DEPLOYER_ENVIRONMENTS_DIR/

Want to set MINIMUM resource requirements the app needs?
  → Edit deploy.toml (min_cpu, min_memory) in your app repo

Want to change INFRASTRUCTURE (DB, cache, networking)?
  → Edit main.tf, run tofu apply

Want to change DEPLOYMENT BEHAVIOR (speed, rollback)?
  → Edit config.toml in $DEPLOYER_ENVIRONMENTS_DIR/
```

See [DESIGN.md](background/DESIGN.md#environment-directory-file-breakdown) for the full explanation of this separation.

______________________________________________________________________

## Common Patterns

### Django App with Celery

```toml
# deploy.toml
[application]
name = "myapp"
source = "."

[images.web]
context = "."
dockerfile = "Dockerfile"

[services.web]
image = "web"
port = 8000
command = ["gunicorn", "myapp.wsgi:application", "--bind", "0.0.0.0:8000"]
health_check_path = "/health/"

[services.celery]
image = "web"  # Reuse the same image
command = ["celery", "-A", "myapp", "worker", "-l", "INFO"]

[migrations]
enabled = true
service = "web"
command = ["python", "manage.py", "migrate"]
```

```hcl
# services.auto.tfvars
services = {
  web = {
    cpu               = 256
    memory            = 512
    replicas          = 1
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
  celery = {
    cpu           = 256
    memory        = 512
    replicas      = 1
    load_balanced = false
  }
}
```

### Multiple Services with Path Routing

```toml
[services.api]
image = "web"
port = 8000
command = ["gunicorn", "api:app"]
health_check_path = "/api/health"
path_pattern = "/api/*"

[services.admin]
image = "web"
port = 8000
command = ["gunicorn", "admin:app"]
health_check_path = "/admin/health"
path_pattern = "/admin/*"
```

______________________________________________________________________

## Rollback

If deployment fails:

1. **Check logs** - View ECS task logs and events
1. **Rollback to previous revision**:
   ```bash
   # Find previous revision
   aws ecs list-task-definitions --family-prefix myapp-staging-web

   # Update service to use previous revision
   aws ecs update-service \
     --cluster myapp-staging-cluster \
     --service web \
     --task-definition myapp-staging-web:PREVIOUS_REVISION
   ```
1. **Fix and redeploy** - Address the issue and deploy again

______________________________________________________________________

## Next Steps

- [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md) - All configuration options
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues and solutions
- [STAGING.md](operations/STAGING.md) - Staging environment management
- [PRODUCTION.md](operations/PRODUCTION.md) - Production operations
