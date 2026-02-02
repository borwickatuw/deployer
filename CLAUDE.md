# Deployer - Claude Code Context

## Project Overview
Infrastructure and deployment tooling for containerized applications on AWS ECS Fargate. Supports any framework that runs in Docker (Django, Rails, Node.js, etc.).

## Key Files
- `bin/deploy.py` - Main deployment script (auto-selects AWS profile from config.toml)
- `bin/init.py` - Initialize new apps: generate deploy.toml and environment directories
- `bin/tofu.sh` - OpenTofu wrapper (auto-selects AWS profile from config.toml)
- `bin/manage-cognito-access.py` - Cognito user management (auto-selects AWS profile from config.toml)
- `bin/manage-secrets.py` - SSM Parameter Store secrets management (auto-selects AWS profile from config.toml)
- `modules/` - Reusable Terraform/OpenTofu modules
- `DEPLOYER_ENVIRONMENTS_DIR` - Per-environment configurations (set in `.env`, each env has a `config.toml`)
- `example-deployer-environments/` - Example environments directory structure
- `example-deploy.toml` - Example application deploy.toml
- `.env` - Configuration (environments directory path)

## Getting Started with a New App

See [docs/DEPLOYMENT-GUIDE.md](docs/DEPLOYMENT-GUIDE.md) for the complete deployment guide.

Quick start:
```bash
# Generate deploy.toml from docker-compose.yml
cd /path/to/your/app
uv run python /path/to/deployer/bin/init.py deploy-toml --from-compose docker-compose.yml

# Create environment directory (standalone)
cd /path/to/deployer
uv run python bin/init.py environment --app-name myapp --env-type staging --domain myapp.example.com

# Or use shared infrastructure (for simple single-container apps)
uv run python bin/init.py environment --app-name myapp --env-type staging --shared --domain myapp.staging.example.com
```

## Shared vs Standalone Environments

**Standalone environments** (default): Each app gets its own VPC, NAT Gateway, ALB, and ECS cluster. Best for:
- Production environments
- Apps with celery workers or multiple services
- High-traffic apps
- Apps needing custom networking

**Shared environments** (`--shared` flag): Multiple simple apps share VPC, NAT, ALB, and ECS cluster. Each app still gets its own RDS database. Best for:
- Staging environments
- Simple single-container Django apps
- Cost-conscious deployments (~$65-85/month savings per app)

See [docs/SHARED-ENVIRONMENTS.md](docs/SHARED-ENVIRONMENTS.md) for detailed guide.

## Maintainer Notes
When changing the environment config.toml structure, update:
1. `example-deployer-environments/myapp-staging/config.toml.example` (the documented template)
2. `example-deployer-environments/app-on-shared-staging/config.toml.example` (for shared environments)
3. `docs/CONFIG-REFERENCE.md` (Environment config.toml Reference section)
4. All existing `*/config.toml` files in the environments directory

## Design Principles

**One canonical location for each config value**: Every configuration value should have exactly one correct place to live. Don't add fallback logic that checks multiple locations - this creates ambiguity about where values come from and masks configuration errors.

**Fail fast with clear errors**: If required configuration is missing, fail immediately with a helpful error message rather than silently falling back to defaults or alternative sources. This makes problems obvious during development rather than causing subtle bugs in production.

Example: `ecr_prefix` belongs in the environment's config.toml (infrastructure), not deploy.toml (app structure). The deploy script requires it from config.toml and fails with a clear error if missing, rather than falling back to deploy.toml or the app name.

## AWS Profiles

AWS profiles are configured per-environment in each `config.toml`. See [docs/CONFIG-REFERENCE.md](docs/CONFIG-REFERENCE.md#aws) for full documentation.

```toml
[aws]
deploy_profile = "deployer-app"      # for deploy.py
infra_profile = "deployer-infra"     # for tofu.sh
cognito_profile = "deployer-cognito" # for manage-cognito-access.py
```

The scripts automatically read the appropriate profile from the environment's config.toml:

```bash
# Deployments - reads [aws].deploy_profile from config.toml
uv run python bin/deploy.py ../app/deploy.toml myapp-staging

# Infrastructure - reads [aws].infra_profile from config.toml
./bin/tofu.sh myapp-staging plan

# Cognito - reads [aws].cognito_profile from config.toml
uv run python bin/manage-cognito-access.py list myapp-staging
```

You can override with `AWS_PROFILE=... ` if needed.

## Environments Directory

Environment configs are stored in a separate directory configured via `DEPLOYER_ENVIRONMENTS_DIR` in `.env`. This is required - the scripts will fail with a clear error if not set.

```bash
DEPLOYER_ENVIRONMENTS_DIR=~/code/deployer-environments
```

**Current configuration**: Environments are stored in `~/code/deployer-environments/`.

The directory should contain environment directories directly at its root:
```
~/code/deployer-environments/
├── myapp-production/
└── myapp-staging/
```

## Deployment Workflow

1. Run `bin/deploy.py` with positional arguments:
   ```bash
   uv run python bin/deploy.py ../app/deploy.toml myapp-staging
   ```
2. Verify services are healthy via ECS and target group health checks

The environment's `config.toml` contains `${tofu:...}` placeholders that are resolved at deploy time by fetching outputs from tofu.

## Testing Cognito-Protected Environments

For Cognito test account setup and credential retrieval, see [docs/STAGING-ENVIRONMENTS.md](docs/STAGING-ENVIRONMENTS.md#test-account-for-automation).

Staging environments will likely need Cognito access to do health checks. 302 redirects are a sign that Cognito access may be needed.

## Framework Configuration

Framework-specific configuration guides:
- [Django](docs/frameworks/django.md) - Python web framework
- [Rails](docs/frameworks/rails.md) - Ruby web framework
- [Generic](docs/frameworks/generic.md) - Any containerized application

## Common Issues

- **CSRF verification failed**: Add `CSRF_TRUSTED_ORIGINS` to Django settings (see docs/frameworks/django.md)
- **ALLOWED_HOSTS errors**: Check that local_settings.py is excluded via .dockerignore
- **Static files 404**: Ensure whitenoise is configured in Django settings
- **CloudWatch log group missing**: Usually created by tofu; see TROUBLESHOOTING.md if this occurs

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for full troubleshooting guide.

## IAM Policies (Bootstrap Terraform)

IAM roles and policies are managed by terraform in `deployer-environments/bootstrap/`. When modifying IAM policies:

- **Use service-level wildcards** (e.g., `ecs:*`, `rds:*`) rather than listing individual actions. This reduces maintenance overhead.
- **Apply resource restrictions** where they matter: S3 buckets, SSM parameters, ECR repos, IAM roles should be scoped to project prefixes via the `project_prefixes` variable.
- **Keep IAM role management granular** - the `infra_admin_iam` policy document should remain specific due to the sensitivity of IAM operations.
- Don't enumerate every possible action - AWS adds new actions frequently and enumerating them all creates busywork.

To add a new project:
1. Edit your bootstrap instance's `terraform.tfvars` (e.g., `bootstrap-staging/terraform.tfvars`)
2. Add the project name to `project_prefixes`
3. Run `AWS_PROFILE=admin tofu apply`

For multi-account setups (staging/production in different accounts), create separate bootstrap instances and configure per-environment profiles in each `config.toml`:
```toml
[aws]
deploy_profile = "deployer-app-production"
infra_profile = "deployer-infra-production"
cognito_profile = "deployer-cognito-production"
```
