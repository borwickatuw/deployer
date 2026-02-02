# Troubleshooting

Common issues and solutions when deploying applications with deployer.

---

## Setup Issues

Problems that occur before you can run deployments.

### Python Version Errors

**Error:** `ModuleNotFoundError: No module named 'tomllib'`

Python 3.11+ is required. Check your version:

```bash
python3 --version
```

If you have an older version:

```bash
brew install python@3.11
# Then ensure you're using the right Python
python3.11 --version
```

### DEPLOYER_ENVIRONMENTS_DIR Not Set

**Error:** `RuntimeError: DEPLOYER_ENVIRONMENTS_DIR environment variable is not set`

The deployer needs to know where your environment configurations are stored:

1. Copy the example `.env` file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set the path:
   ```bash
   DEPLOYER_ENVIRONMENTS_DIR=~/code/deployer-environments
   ```

3. Ensure the directory exists:
   ```bash
   mkdir -p ~/code/deployer-environments
   ```

### OpenTofu Not Found

**Error:** `tofu: command not found`

Install OpenTofu:

```bash
brew install opentofu
```

Verify installation:

```bash
tofu --version
```

### AWS Profile Not Found

**Error:** `The config profile (deployer-app) could not be found`

AWS profiles are configured in `~/.aws/config`. See [GETTING-STARTED.md](GETTING-STARTED.md#6-configure-aws-cli-profiles) for profile setup instructions.

Check what profiles exist:

```bash
aws configure list-profiles
```

Test the profile:

```bash
AWS_PROFILE=deployer-app aws sts get-caller-identity
```

### Environment Directory Not Found

**Error:** `Environment directory not found: /path/to/environments/myapp-staging`

1. Verify `DEPLOYER_ENVIRONMENTS_DIR` points to the correct location
2. Check the environment exists:
   ```bash
   ls $DEPLOYER_ENVIRONMENTS_DIR
   ```
3. Create the environment if needed:
   ```bash
   uv run python bin/init.py environment --app-name myapp --env-type staging
   ```

---

## Container Startup Issues

### Container Won't Start

Check CloudWatch logs for startup errors:

```bash
aws logs tail /ecs/myapp-staging --since 30m
```

Common causes:
- **Missing environment variables** — Check that all required env vars are set in deploy.toml
- **Database connection failed** — Verify security groups allow ECS to reach RDS
- **Application crash on startup** — Check logs for Python/Django errors
- **Missing secrets** — Ensure all SSM parameters exist

### Task Keeps Restarting

View ECS events for error details:

```bash
aws ecs describe-services \
  --cluster myapp-staging-cluster \
  --services web \
  --query 'services[0].events[:5]'
```

Check the stopped task reason:

```bash
aws ecs describe-tasks \
  --cluster myapp-staging-cluster \
  --tasks $(aws ecs list-tasks --cluster myapp-staging-cluster --service-name web --desired-status STOPPED --query 'taskArns[0]' --output text) \
  --query 'tasks[0].stoppedReason'
```

---

## Health Check Failures

### Health Check Endpoint Returns Non-200

1. Verify the health check works locally:
   ```bash
   curl http://localhost:8000/health/
   ```

2. Exec into the container to test:
   ```bash
   aws ecs execute-command \
     --cluster myapp-staging-cluster \
     --task $TASK_ID \
     --container web \
     --interactive \
     --command "curl http://localhost:8000/health/"
   ```

3. Check that the health check path matches your application:
   - In deploy.toml: `health_check_path = "/health/"`
   - In terraform.tfvars: `health_check_path = "/health/"`

### Container Not Listening on Expected Port

Verify your application binds to the correct port:

```bash
# In container
netstat -tlnp
# Or
ss -tlnp
```

Ensure your command uses the right port (typically 8000 for Django/Gunicorn).

### Slow Startup Causing Health Check Timeout

Increase the health check `startPeriod` in your infrastructure configuration. Default is usually too short for Django applications that need to compile templates or run startup tasks.

---

## Database Connection Issues

### Connection Refused

1. Verify the DATABASE_URL is correct:
   ```bash
   aws ssm get-parameter \
     --name "/myapp/staging/database-url" \
     --with-decryption \
     --query 'Parameter.Value'
   ```

2. Check security groups allow traffic:
   ```bash
   # ECS tasks must be able to reach RDS on port 5432
   aws ec2 describe-security-groups \
     --group-ids $ECS_SG_ID \
     --query 'SecurityGroups[0].IpPermissionsEgress'
   ```

3. Verify the database exists and user has permissions:
   ```bash
   # Connect to RDS and check
   psql "$DATABASE_URL" -c "\l"
   ```

### Database Not Found

If migrations fail with "database does not exist":

1. Connect to the RDS instance as the admin user
2. Create the database:
   ```sql
   CREATE DATABASE myapp;
   ```

---

## Image Build Failures

### Docker Build Fails

1. Verify Docker is running:
   ```bash
   docker info
   ```

2. Check the Dockerfile path in deploy.toml:
   ```toml
   [images.web]
   context = "."
   dockerfile = "Dockerfile"  # Path relative to context
   ```

3. Build manually to see detailed errors:
   ```bash
   cd /path/to/your-app
   docker build -t test --platform linux/amd64 .
   ```

### ECR Push Fails

1. Ensure ECR repository exists:
   ```bash
   aws ecr describe-repositories --repository-names myapp-web
   ```

2. Verify ECR login is current:
   ```bash
   aws ecr get-login-password --region us-west-2 | \
     docker login --username AWS --password-stdin \
     123456789.dkr.ecr.us-west-2.amazonaws.com
   ```

---

## Static Files Issues

### Static Files Return 404

1. Ensure WhiteNoise is configured in Django settings:
   ```python
   MIDDLEWARE = [
       'django.middleware.security.SecurityMiddleware',
       'whitenoise.middleware.WhiteNoiseMiddleware',  # After SecurityMiddleware
       # ...
   ]
   ```

2. Run collectstatic in your Dockerfile:
   ```dockerfile
   RUN python manage.py collectstatic --noinput
   ```

3. Verify STATIC_ROOT is set and WhiteNoise can find files.

### ALLOWED_HOSTS Error

Check that `local_settings.py` is excluded via `.dockerignore`:

```
# .dockerignore
local_settings.py
**/local_settings.py
```

Ensure ALLOWED_HOSTS includes your domain or ALB DNS name in production settings.

---

## Cognito Authentication Issues

For Cognito setup and user management, see [STAGING-ENVIRONMENTS.md](STAGING-ENVIRONMENTS.md#cognito-authentication).

### "Redirect URI Mismatch" Error

The callback URL configured in Cognito must exactly match your domain:
- Check `domain_name` variable matches your actual domain
- Access the site via HTTPS, not HTTP
- Verify no trailing slash mismatch

### User Can't Log In

1. Verify user exists:
   ```bash
   aws cognito-idp admin-get-user \
     --user-pool-id $USER_POOL_ID \
     --username user@example.com
   ```

2. Check user status (should be `CONFIRMED`):
   ```bash
   aws cognito-idp list-users \
     --user-pool-id $USER_POOL_ID \
     --filter "username = \"user@example.com\""
   ```

3. If status is `FORCE_CHANGE_PASSWORD`, user needs to complete first login or set a permanent password:
   ```bash
   uv run python bin/manage-cognito-access.py reset-password \
     myapp-staging --username user@example.com --permanent
   ```

---

## CloudWatch Logs Issues

### Log Group Missing

OpenTofu creates log groups automatically, but if you see this error, create the log group manually:

```bash
aws logs create-log-group --log-group-name /ecs/myapp-staging
```

This can happen if deployment was attempted before `tofu apply` completed, or if the environment was created with an older version of the deployer.

### No Logs Appearing

1. Verify the log group name matches what's in the task definition
2. Check the ECS task IAM role has CloudWatch Logs permissions
3. Ensure the container is actually starting (check ECS events)

---

## Environment Variable Issues

### Placeholder Not Resolved

If you see `${database_url}` in your container instead of the actual value:

1. Check that the environment's `config.toml` has the correct tofu output reference
2. Verify `tofu output database_url` returns a valid value in the environment directory
3. Check the placeholder name matches exactly (case-sensitive)

### Config Loading Fails

If deploy.py fails to load configuration:

1. Verify `config.toml` exists in the environment directory:
   ```bash
   ls environments/myapp-staging/config.toml
   ```

2. Check that `tofu init` has been run in the environment directory

3. Verify tofu outputs are available:
   ```bash
   cd environments/myapp-staging
   tofu output
   ```

---

## Infrastructure Issues

### tofu apply Fails

1. Ensure you ran `tofu init` first:
   ```bash
   tofu init
   ```

2. Check for missing required variables in terraform.tfvars

3. Verify AWS credentials have sufficient permissions

### Resource Already Exists

If a resource already exists from a previous failed apply:

1. Try importing it:
   ```bash
   tofu import aws_ecr_repository.web myapp-web
   ```

2. Or delete it manually and re-run apply

### State Lock Issues

If you see "Error acquiring state lock":

1. Wait for another apply to finish
2. If no one else is running:
   ```bash
   tofu force-unlock LOCK_ID
   ```

---

## Quick Diagnostics

### Full Service Health Check

```bash
# Service status
aws ecs describe-services \
  --cluster myapp-staging-cluster \
  --services web \
  --query 'services[0].{status:status,running:runningCount,desired:desiredCount,pending:pendingCount}'

# Recent events
aws ecs describe-services \
  --cluster myapp-staging-cluster \
  --services web \
  --query 'services[0].events[:3]'

# Recent logs
aws logs tail /ecs/myapp-staging --since 10m

# Target group health
aws elbv2 describe-target-health \
  --target-group-arn $ALB_TARGET_GROUP_ARN
```

### Rollback to Previous Version

If a deployment breaks the application:

```bash
# Find previous task definition revision
aws ecs list-task-definitions \
  --family-prefix myapp-staging-web \
  --sort DESC \
  --query 'taskDefinitionArns[:5]'

# Update service to use previous revision
aws ecs update-service \
  --cluster myapp-staging-cluster \
  --service web \
  --task-definition myapp-staging-web:PREVIOUS_REVISION
```
