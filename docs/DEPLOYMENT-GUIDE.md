# Deployment Guide

Complete guide for deploying applications to AWS ECS. Covers initial setup, ongoing deployments, and verification.

For first-time AWS account setup (IAM roles, bootstrap infrastructure), see [GETTING-STARTED.md](GETTING-STARTED.md).

## Quick Start Checklist

1. [Prerequisites](#prerequisites) — complete GETTING-STARTED.md, verify tools
2. [Initial Setup](#initial-setup) — create deploy.toml, environment directory, apply infrastructure
3. [Deploying](#deploying) — build images and deploy to ECS
4. [Verification](#verification) — confirm services are healthy

______________________________________________________________________

## Prerequisites

Complete [GETTING-STARTED.md](GETTING-STARTED.md) first (one-time AWS account setup). Then verify everything is working:

```bash
uv run python bin/init.py verify
```

______________________________________________________________________

## Initial Setup

These steps are done once per environment.

**First time deploying this application?** If the application name hasn't been registered in the deployer yet, you'll need to add it as a project prefix in bootstrap first. See [Adding New Applications](GETTING-STARTED.md#adding-new-applications) in the Getting Started guide.

### 1. Create deploy.toml (in your app repo)

Your application needs a `deploy.toml` file that describes what to deploy.

**Option A: Generate from docker-compose.yml (recommended)**

If you have a working `docker-compose.yml`, generate `deploy.toml` automatically:

```bash
cd /path/to/your/app
uv run python /path/to/deployer/bin/init.py deploy-toml --from-compose docker-compose.yml --dry-run

# Review the output, then generate the file:
uv run python /path/to/deployer/bin/init.py deploy-toml --from-compose docker-compose.yml
```

The generator will:

- Extract services with `build` configurations
- Filter out infrastructure services (postgres, redis, etc.)
- Detect your framework (Django, Rails, etc.)
- Set up appropriate migrations command
- Identify potential secrets from environment variables

**Option B: Generate from scratch**

```bash
uv run python bin/init.py deploy-toml --app-name myapp
# Edit deploy.toml to match your application
```

### Key deploy.toml Sections

```toml
[application]
name = "myapp"           # Used for ECS cluster naming
source = "."             # Path to source code

[images.web]
context = "."            # Build context relative to source
dockerfile = "Dockerfile"

[services.web]
image = "web"            # References [images.web]
port = 8000
command = ["gunicorn", "app:application", "--bind", "0.0.0.0:8000"]
health_check_path = "/health/"
# min_cpu = 512      # Optional: minimum CPU units required
# min_memory = 1024  # Optional: minimum memory MB required

[environment]
ALLOWED_HOSTS = "*"
DATABASE_URL = "${database_url}"   # Resolved from infrastructure
REDIS_URL = "${redis_url}"

[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"
DATABASE_URL = "ssm:/myapp/${environment}/database-url"

[migrations]
enabled = true
service = "web"
command = ["python", "manage.py", "migrate"]
```

See [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md) for complete documentation.

### Verify Docker Build Works

Before proceeding, verify your Docker image builds successfully:

```bash
cd /path/to/your/app
docker build -t myapp-test .
```

### 2. Create Environment Directory

Choose a template based on how your application's infrastructure is organized:

| Template | Use when |
| --- | --- |
| `standalone-staging` | App gets its own VPC, database, load balancer, etc. Most common for a first deployment. |
| `standalone-production` | Production version of the above. |
| `shared-infra-staging` | Creates shared infrastructure (VPC, ALB, database) that multiple apps will use. |
| `shared-infra-production` | Production version of the above. |
| `shared-app-staging` | App runs on existing shared infrastructure (requires `shared-infra` first). |
| `shared-app-production` | Production version of the above. |

To see all available templates: `uv run python bin/init.py environment --list-templates`

For shared infrastructure setups (multiple apps sharing a database and load balancer), see [SHARED-ENVIRONMENTS.md](operations/SHARED-ENVIRONMENTS.md).

**Option A: Use init script (recommended)**

```bash
cd /path/to/deployer
uv run python bin/init.py environment \
  --app-name myapp \
  --template standalone-staging \
  --deploy-toml /path/to/app/deploy.toml \
  --domain staging.myapp.com
```

This creates `environments/myapp-staging/` with:

```
environments/myapp-staging/
├── main.tf           # Infrastructure module reference
├── config.toml       # Deployment configuration with ${tofu:...} placeholders
├── terraform.tfvars  # Service sizing (cpu, memory, replicas)
└── README.md         # Environment-specific notes
```

**Environment Naming Convention:**

Environment directories follow the pattern `<app-name>-<env-type>` where:

- `<app-name>` can contain hyphens (e.g., `my-cool-app`)
- `<env-type>` must be `staging` or `production` and comes at the end

Valid examples: `myapp-staging`, `my-cool-app-production`, `api-v2-staging`

The deploy scripts parse the environment name by splitting on the *last* hyphen, so multi-hyphen app names work correctly.

**Option B: Manual setup**

```bash
mkdir -p environments/myapp-staging
cd environments/myapp-staging
```

### 3. Configure terraform.tfvars

Edit `environments/myapp-staging/terraform.tfvars`:

```hcl
project_name  = "myapp"

# Database
db_username       = "myapp"
db_password       = "use-a-strong-password"

# Domain (optional, for HTTPS)
domain_name     = "staging.myapp.com"
route53_zone_id = "Z1234567890ABC"

# Service sizing
services = {
  web = {
    cpu               = 256
    memory            = 512
    replicas          = 1
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
}
```

### 4. Apply Infrastructure

```bash
# Use the tofu wrapper (auto-selects correct AWS profile)
./bin/tofu.sh init myapp-staging
./bin/tofu.sh plan myapp-staging
./bin/tofu.sh apply myapp-staging

# Or use rollout to run init, plan, and apply in sequence
./bin/tofu.sh rollout myapp-staging
```

This creates VPC, RDS, ECS cluster, ALB, ECR repositories, CloudWatch log groups, and supporting resources.

**Note: Lambda timeout on first apply**

The infrastructure includes a Lambda function that creates database users. On the first `tofu apply`, this Lambda may timeout while AWS creates its VPC network interface (ENI). If you see a timeout error like:

```
module.infrastructure.module.db_users.aws_lambda_invocation.create_users: Still creating... [5m0s elapsed]
Error: invocation failed: timeout
```

Simply run `tofu apply` again. The second run typically succeeds in seconds because the ENI is already created. This is a one-time issue that only affects the initial infrastructure setup.

### 5. Create Secrets in SSM

```bash
# Django/application secret key
aws ssm put-parameter \
  --name "/myapp/staging/secret-key" \
  --type SecureString \
  --value "$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"

# Database URL (get host from tofu output)
aws ssm put-parameter \
  --name "/myapp/staging/database-url" \
  --type SecureString \
  --value "postgres://user:password@host/dbname"
```

______________________________________________________________________

## Deploying

### Dry Run First

```bash
uv run python bin/deploy.py myapp-staging --dry-run
```

This shows:

- Which images will be built
- What environment variables will be set
- Which services will be created/updated

### Deploy

```bash
uv run python bin/deploy.py myapp-staging
```

Note: Requires environment to be linked via `link-environments.py`, or use `--deploy-toml` flag.

The script will:

1. Log into ECR
1. Build and push Docker images
1. Run database migrations (if configured)
1. Create/update ECS services
1. Wait for services to stabilize

______________________________________________________________________

## Verification

### Check Service Status

```bash
# Get the ALB URL
./bin/tofu.sh -chdir=environments/myapp-staging output -raw alb_dns_name

# Check ECS service
aws ecs describe-services \
  --cluster myapp-staging-cluster \
  --services web \
  --query 'services[0].{desired:desiredCount,running:runningCount,status:status}'
```

### Test the Endpoint

```bash
curl https://staging.myapp.com/health/
```

For Cognito-protected staging environments, see [STAGING.md](operations/STAGING.md#test-account-for-automation) for authentication setup.

### View Logs

```bash
# Stream logs
aws logs tail /ecs/myapp-staging --follow

# Search for errors
aws logs filter-log-events \
  --log-group-name /ecs/myapp-staging \
  --filter-pattern "ERROR"
```

______________________________________________________________________

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
  → Edit services.auto.tfvars in deployer-environments/

Want to set MINIMUM resource requirements the app needs?
  → Edit deploy.toml (min_cpu, min_memory) in your app repo

Want to change INFRASTRUCTURE (DB, cache, networking)?
  → Edit main.tf, run tofu apply

Want to change DEPLOYMENT BEHAVIOR (speed, rollback)?
  → Edit config.toml in deployer/environments/
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

## Troubleshooting

### CSRF verification failed (Django)

Add to your Django settings:

```python
CSRF_TRUSTED_ORIGINS = [f"https://{os.environ.get('DOMAIN_NAME', 'localhost')}"]
```

### ALLOWED_HOSTS errors

Ensure `ALLOWED_HOSTS` includes your domain and that `local_settings.py` is excluded via `.dockerignore`.

### Static files 404

For Django, configure whitenoise:

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # ...
]
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
```

### Health check failures

1. Verify the health check endpoint returns 200 status
1. Check CloudWatch logs for application errors
1. Ensure the health check path in `services.auto.tfvars` matches your app

### Service stuck in "pending" state

Common causes:

- Missing SSM parameters (check CloudWatch logs)
- ECR image not found
- Security group or subnet misconfiguration

Check task stopped reason in ECS console or run:

```bash
aws ecs describe-tasks --cluster myapp-staging-cluster --tasks <task-arn>
```

### 302 redirects on health check

This usually indicates Cognito authentication is blocking the health check. Either:

1. Exclude the health check path from Cognito protection
1. Configure a test account for authenticated health checks (see STAGING-ENVIRONMENTS.md)

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for more issues.

______________________________________________________________________

## Checklists

### Before First Deployment

- [ ] AWS CLI configured and role assumption works
- [ ] `tofu init` run in environment directory
- [ ] `tofu apply` completed successfully (creates ECR repos, log groups, etc.)
- [ ] Secrets created in SSM Parameter Store
- [ ] Health check endpoint works locally
- [ ] Docker image builds locally

### Before Each Deployment

- [ ] Tests passing
- [ ] No sensitive data in `[environment]` (use `[secrets]`)
- [ ] Dry run successful

### After Deployment

- [ ] Health check returns 200
- [ ] Application loads in browser
- [ ] Key functionality works
- [ ] Logs show no errors
- [ ] ECS running count matches desired count

### Security

- [ ] Passwords and API keys in `[secrets]`, not `[environment]`
- [ ] SSM parameters use SecureString type
- [ ] ECS tasks have no public IPs
- [ ] Database only accessible from ECS security group

______________________________________________________________________

## Next Steps

- [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md) - All configuration options
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - More troubleshooting
- [SUPPORTED-ARCHITECTURES.md](background/SUPPORTED-ARCHITECTURES.md) - What's supported
- [DESIGN.md](background/DESIGN.md) - Architecture and design decisions
