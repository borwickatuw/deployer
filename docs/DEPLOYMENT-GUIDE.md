# Deployment Guide

Complete guide for deploying applications to AWS ECS. Covers initial setup, ongoing deployments, and verification.

**Before you start:** Complete [GETTING-STARTED.md](GETTING-STARTED.md) first (one-time AWS account setup).

**Framework-specific guides:** [Django](scenarios/django.md), [Rails](scenarios/rails.md), [Generic](scenarios/generic.md) | [CI/CD](scenarios/ci-cd.md)

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

This creates `$DEPLOYER_ENVIRONMENTS_DIR/myapp-staging/` with:

```
$DEPLOYER_ENVIRONMENTS_DIR/myapp-staging/
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
mkdir -p $DEPLOYER_ENVIRONMENTS_DIR/myapp-staging
cd $DEPLOYER_ENVIRONMENTS_DIR/myapp-staging
```

### 3. Configure terraform.tfvars

Edit `$DEPLOYER_ENVIRONMENTS_DIR/myapp-staging/terraform.tfvars`:

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
bin/tofu.sh init myapp-staging
bin/tofu.sh plan myapp-staging
bin/tofu.sh apply myapp-staging

# Or use rollout to run init, plan, and apply in sequence
bin/tofu.sh rollout myapp-staging
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
bin/tofu.sh output myapp-staging alb_dns_name

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

- **[Operations](operations/)** — Post-deployment tasks, environment lifecycle, common patterns
- [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md) — All configuration options
- [Resources](resources/README.md) — Resource module system (database, cache, storage, CDN, secrets)
- [OpenTofu Modules](tofu-modules/README.md) — Infrastructure module reference
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Common issues and solutions
