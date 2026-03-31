# Troubleshooting

Common issues and solutions when deploying applications with deployer.

**Related docs:**
- [CONFIG-REFERENCE.md](CONFIG-REFERENCE.md) — Configuration options
- [DEPLOYMENT-GUIDE.md](DEPLOYMENT-GUIDE.md) — Deployment walkthrough
- [GETTING-STARTED.md](GETTING-STARTED.md) — Initial AWS account setup
- [Operations](operations/) — Day-to-day operational tasks

For framework-specific issues, see [Django](scenarios/django.md#common-issues), [Rails](scenarios/rails.md), or [Generic](scenarios/generic.md).

______________________________________________________________________

## Setup Issues

Most setup issues are caught by the verify command:

```bash
uv run python bin/init.py verify
```

This checks tool versions (Python, uv, OpenTofu, AWS CLI, Docker), confirms `DEPLOYER_ENVIRONMENTS_DIR` is set, verifies the bootstrap directory exists, and tests AWS profile access.

### Python Version

The deployer requires Python 3.12+. If you're managing Python with uv:

```bash
uv python install 3.12
```

### AWS Profiles

**Error:** `The config profile (deployer-app) could not be found`

Generate the required profiles automatically:

```bash
uv run python bin/init.py setup-profiles
```

Or check what profiles exist:

```bash
aws configure list-profiles
```

### Environment Directory Not Found

**Error:** `Environment directory not found: /path/to/environments/myapp-staging`

1. Verify `DEPLOYER_ENVIRONMENTS_DIR` points to the correct location
1. Check the environment exists: `ls $DEPLOYER_ENVIRONMENTS_DIR`
1. Create the environment if needed:
   ```bash
   uv run python bin/init.py environment --app-name myapp --template standalone-staging
   ```

______________________________________________________________________

## Container Issues

### Container Won't Start or Keeps Restarting

Check the current state and recent logs:

```bash
uv run python bin/ops.py myapp-staging status
uv run python bin/ops.py myapp-staging logs --minutes 30
```

Common causes:

- **Missing environment variables** — Check that all required env vars are set in deploy.toml
- **Database connection failed** — Verify security groups allow ECS to reach RDS
- **Application crash on startup** — Check logs for application errors
- **Missing secrets** — Verify SSM parameters exist:
  ```bash
  uv run python bin/ssm-secrets.py list myapp-staging
  ```

### Health Check Failures

1. Verify the health check works locally:

   ```bash
   curl http://localhost:8000/health/
   ```

1. Test from inside the container:

   ```bash
   uv run python bin/ecs-run.py exec myapp-staging curl http://localhost:8000/health/
   ```

1. Check that the health check path matches between deploy.toml and terraform.tfvars

1. Check ALB target health:

   ```bash
   uv run python bin/ops.py myapp-staging health
   ```

______________________________________________________________________

## Database Issues

### Connection Refused

1. Verify secrets are set correctly:

   ```bash
   uv run python bin/ssm-secrets.py list myapp-staging
   ```

1. Check security groups allow traffic from ECS to RDS on port 5432

1. Check the environment status:

   ```bash
   uv run python bin/ops.py myapp-staging status
   ```

### Permission Denied on DDL Operations

**Error:** `permission denied for schema public` or `must be owner of table`

The deployer uses a **two-account database model** — see [Resources: Database](resources/database.md) for details. Runtime services use DML-only credentials; migrations use DDL credentials.

If you see permission errors when running migrations:

1. **Verify the command is marked as DDL** in deploy.toml:

   ```toml
   [commands.migrate]
   command = ["python", "manage.py", "migrate"]
   ddl = true
   ```

1. **Verify you're running via ecs-run.py** (which selects the migrate task definition):

   ```bash
   uv run python bin/ecs-run.py run myapp-staging migrate
   ```

1. **Verify the migrate task definition exists**:

   ```bash
   aws ecs list-task-definitions --family-prefix myapp-staging-migrate
   ```

   If missing, run a deployment to register it:

   ```bash
   uv run python bin/deploy.py myapp-staging
   ```

### Permission Denied on DML Operations

**Error:** `permission denied for table` on SELECT, INSERT, UPDATE, or DELETE

This can happen if tables were created before the Lambda user-creation ran. Re-run the db-users Lambda:

```bash
cd $DEPLOYER_ENVIRONMENTS_DIR/myapp-staging
tofu taint 'module.db_users.aws_lambda_invocation.create_users'
tofu apply
```

______________________________________________________________________

## Image Build Failures

### Docker Build Fails

1. Verify Docker is running: `docker info`

1. Check the Dockerfile path in deploy.toml:

   ```toml
   [images.web]
   context = "."
   dockerfile = "Dockerfile"
   ```

1. Build manually to see detailed errors:

   ```bash
   cd /path/to/your-app
   docker build -t test --platform linux/amd64 .
   ```

### ECR Push Fails

Verify the ECR repository exists:

```bash
uv run python bin/ops.py myapp-staging ecr
```

______________________________________________________________________

## Environment Variable Issues

### Placeholder Not Resolved

If you see `${database_url}` in your container instead of the actual value:

1. Check that the environment's `config.toml` has the correct tofu output reference
1. Verify tofu outputs are available:
   ```bash
   bin/tofu.sh output myapp-staging
   ```

______________________________________________________________________

## Infrastructure Issues

### tofu apply Fails

1. Ensure you ran `tofu init` first (or use `bin/tofu.sh rollout` which does init automatically)
1. Check for missing required variables in terraform.tfvars
1. Verify AWS credentials: `uv run python bin/init.py verify`

### State Lock Issues

If you see "Error acquiring state lock":

1. Wait for another apply to finish
1. If no one else is running:
   ```bash
   tofu force-unlock LOCK_ID
   ```
