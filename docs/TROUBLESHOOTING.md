# Troubleshooting

Common issues and solutions when deploying applications with deployer.

**Related docs:**
- [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md) — Configuration options
- [DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md) — Deployment walkthrough
- [GETTING-STARTED.md](GETTING-STARTED.md) — Initial AWS account setup
- [Operations](operations/) — Day-to-day operational tasks

______________________________________________________________________

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

1. Edit `.env` and set the path:

   ```bash
   DEPLOYER_ENVIRONMENTS_DIR=~/deployer-environments
   ```

1. Ensure the directory exists:

   ```bash
   mkdir -p ~/deployer-environments
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
1. Check the environment exists:
   ```bash
   ls $DEPLOYER_ENVIRONMENTS_DIR
   ```
1. Create the environment if needed:
   ```bash
   uv run python bin/init.py environment --app-name myapp --template standalone-staging
   ```

______________________________________________________________________

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

______________________________________________________________________

## Health Check Failures

### Health Check Endpoint Returns Non-200

1. Verify the health check works locally:

   ```bash
   curl http://localhost:8000/health/
   ```

1. Exec into the container to test:

   ```bash
   aws ecs execute-command \
     --cluster myapp-staging-cluster \
     --task $TASK_ID \
     --container web \
     --interactive \
     --command "curl http://localhost:8000/health/"
   ```

1. Check that the health check path matches your application:

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

______________________________________________________________________

## Database Connection Issues

### Connection Refused

1. Verify the DATABASE_URL is correct:

   ```bash
   aws ssm get-parameter \
     --name "/myapp/staging/database-url" \
     --with-decryption \
     --query 'Parameter.Value'
   ```

1. Check security groups allow traffic:

   ```bash
   # ECS tasks must be able to reach RDS on port 5432
   aws ec2 describe-security-groups \
     --group-ids $ECS_SG_ID \
     --query 'SecurityGroups[0].IpPermissionsEgress'
   ```

1. Verify the database exists and user has permissions:

   ```bash
   # Connect to RDS and check
   psql "$DATABASE_URL" -c "\l"
   ```

### Database Not Found

If migrations fail with "database does not exist":

1. Connect to the RDS instance as the admin user
1. Create the database:
   ```sql
   CREATE DATABASE myapp;
   ```

### Permission Denied on DDL Operations

**Error:** `permission denied for schema public` or `must be owner of table`

The deployer uses a **two-account database model** for security:

- **App user** (runtime): DML only (SELECT, INSERT, UPDATE, DELETE)
- **Migrate user** (migrations): DDL + DML (CREATE, ALTER, DROP)

If you see permission errors when running migrations:

1. **Verify you're using the migrate task definition**:

   Commands marked with `ddl = true` in deploy.toml automatically use migrate credentials:

   ```toml
   [commands]
   migrate = { command = ["python", "manage.py", "migrate"], ddl = true }
   ```

1. **Check the command is recognized**:

   ```bash
   # Should use migrate credentials (DDL)
   uv run python bin/ecs-run.py run myapp-staging migrate

   # Uses app credentials (DML only)
   uv run python bin/ecs-run.py run myapp-staging showmigrations
   ```

1. **Verify migrate task definition exists**:

   ```bash
   aws ecs list-task-definitions --family-prefix myapp-staging-migrate
   ```

   If missing, run a deployment to register it:

   ```bash
   uv run python bin/deploy.py myapp-staging
   ```

1. **Test credentials manually**:

   ```bash
   # Get migrate credentials from Secrets Manager
   aws secretsmanager get-secret-value \
     --secret-id myapp-staging/db-migrate-credentials \
     --query 'SecretString' --output text | jq .

   # Connect and test DDL
   psql "postgresql://migrate_user:password@host/dbname" \
     -c "CREATE TABLE test_ddl (id int); DROP TABLE test_ddl;"
   ```

### Permission Denied on DML Operations

**Error:** `permission denied for table` on SELECT, INSERT, UPDATE, or DELETE

This can happen if tables were created before the Lambda user-creation ran, or if the app user wasn't granted permissions on existing tables.

1. **Re-run the db-users Lambda** (requires OpenTofu):

   ```bash
   # Taint the Lambda invocation to force re-run
   cd $DEPLOYER_ENVIRONMENTS_DIR/myapp-staging
   tofu taint 'module.db_users.aws_lambda_invocation.create_users'
   tofu apply
   ```

1. **Manually grant permissions** (emergency fix):

   ```sql
   -- As master/admin user
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO myapp_staging_app;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO myapp_staging_app;
   ```

### Tables Created by Wrong User

If migrate user creates tables but app user can't access them:

The Lambda automatically sets `ALTER DEFAULT PRIVILEGES` so new tables get correct permissions. If tables were created manually or before this was configured:

```sql
-- As master/admin user, grant permissions on existing tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO myapp_staging_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO myapp_staging_app;

-- Also grant to migrate user (needs DML too)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO myapp_staging_migrate;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO myapp_staging_migrate;
```

______________________________________________________________________

## Image Build Failures

### Docker Build Fails

1. Verify Docker is running:

   ```bash
   docker info
   ```

1. Check the Dockerfile path in deploy.toml:

   ```toml
   [images.web]
   context = "."
   dockerfile = "Dockerfile"  # Path relative to context
   ```

1. Build manually to see detailed errors:

   ```bash
   cd /path/to/your-app
   docker build -t test --platform linux/amd64 .
   ```

### ECR Push Fails

1. Ensure ECR repository exists:

   ```bash
   aws ecr describe-repositories --repository-names myapp-web
   ```

1. Verify ECR login is current:

   ```bash
   aws ecr get-login-password --region us-west-2 | \
     docker login --username AWS --password-stdin \
     123456789.dkr.ecr.us-west-2.amazonaws.com
   ```

______________________________________________________________________

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

1. Run collectstatic in your Dockerfile:

   ```dockerfile
   RUN python manage.py collectstatic --noinput
   ```

1. Verify STATIC_ROOT is set and WhiteNoise can find files.

### ALLOWED_HOSTS Error

Check that `local_settings.py` is excluded via `.dockerignore`:

```
# .dockerignore
local_settings.py
**/local_settings.py
```

Ensure ALLOWED_HOSTS includes your domain or ALB DNS name in production settings.

______________________________________________________________________

## Cognito Authentication Issues

For Cognito setup and user management, see [STAGING.md](operations/STAGING.md#cognito-authentication).

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

1. Check user status (should be `CONFIRMED`):

   ```bash
   aws cognito-idp list-users \
     --user-pool-id $USER_POOL_ID \
     --filter "username = \"user@example.com\""
   ```

1. If status is `FORCE_CHANGE_PASSWORD`, user needs to complete first login or set a permanent password:

   ```bash
   uv run python bin/cognito.py reset-password \
     myapp-staging --email user@example.com --permanent
   ```

______________________________________________________________________

## CloudWatch Logs Issues

### Log Group Missing

OpenTofu creates log groups automatically, but if you see this error, create the log group manually:

```bash
aws logs create-log-group --log-group-name /ecs/myapp-staging
```

This can happen if deployment was attempted before `tofu apply` completed, or if the environment was created with an older version of the deployer.

### No Logs Appearing

1. Verify the log group name matches what's in the task definition
1. Check the ECS task IAM role has CloudWatch Logs permissions
1. Ensure the container is actually starting (check ECS events)

______________________________________________________________________

## Environment Variable Issues

### Placeholder Not Resolved

If you see `${database_url}` in your container instead of the actual value:

1. Check that the environment's `config.toml` has the correct tofu output reference
1. Verify `tofu output database_url` returns a valid value in the environment directory
1. Check the placeholder name matches exactly (case-sensitive)

### Config Loading Fails

If deploy.py fails to load configuration:

1. Verify `config.toml` exists in the environment directory:

   ```bash
   ls environments/myapp-staging/config.toml
   ```

1. Check that `tofu init` has been run in the environment directory

1. Verify tofu outputs are available:

   ```bash
   cd environments/myapp-staging
   tofu output
   ```

______________________________________________________________________

## Infrastructure Issues

### tofu apply Fails

1. Ensure you ran `tofu init` first:

   ```bash
   tofu init
   ```

1. Check for missing required variables in terraform.tfvars

1. Verify AWS credentials have sufficient permissions

### Resource Already Exists

If a resource already exists from a previous failed apply:

1. Try importing it:

   ```bash
   tofu import aws_ecr_repository.web myapp-web
   ```

1. Or delete it manually and re-run apply

### State Lock Issues

If you see "Error acquiring state lock":

1. Wait for another apply to finish
1. If no one else is running:
   ```bash
   tofu force-unlock LOCK_ID
   ```

______________________________________________________________________

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

For production rollback with checkpoints and monitoring, see [PRODUCTION.md](operations/PRODUCTION.md#rollback-deployment).
