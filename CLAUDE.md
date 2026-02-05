# Deployer - Claude Code Context

## Project Overview
Infrastructure and deployment tooling for containerized applications on AWS ECS Fargate. Supports any framework that runs in Docker (Django, Rails, Node.js, etc.).

## Key Files
- `bin/deploy.py` - Main deployment script (auto-selects AWS profile from config.toml)
- `bin/init.py` - Initialize new apps: generate deploy.toml and environment directories
- `bin/tofu.sh` - OpenTofu wrapper (auto-selects AWS profile from config.toml)
- `bin/ecs-run.py` - Run commands in ECS containers (migrations, shell, etc.)
- `bin/environment.py` - Start/stop staging environments
- `bin/ops.py` - Production monitoring (status, health, logs, maintenance, ecr, audit)
- `bin/emergency.py` - Emergency operations that modify production (rollback, scale, snapshot, restore)
- `bin/cognito.py` - Cognito user management (auto-selects AWS profile from config.toml)
- `bin/secrets.py` - SSM Parameter Store secrets management
- `bin/capacity-report.py` - ECS right-sizing recommendations
- `modules/` - Reusable Terraform/OpenTofu modules
- `DEPLOYER_ENVIRONMENTS_DIR` - Per-environment configurations (set in `.env`)
- `example-deployer-environments/` - Example environments directory structure
- `example-deploy.toml` - Example application deploy.toml

## Common Commands

```bash
# Infrastructure (use tofu.sh wrapper - auto-selects AWS profile)
./bin/tofu.sh plan myapp-staging
./bin/tofu.sh apply myapp-staging

# Deployment
uv run python bin/deploy.py ../app/deploy.toml myapp-staging

# Run commands in containers
uv run python bin/ecs-run.py manage myapp-staging migrate

# Production monitoring (read-only)
uv run python bin/ops.py myapp-production audit    # Run all health/security checks
uv run python bin/ops.py myapp-production status   # View current state

# Emergency operations (modify production)
uv run python bin/emergency.py myapp-production rollback --service web
```

## Key Documentation
- [DEPLOYMENT-GUIDE.md](docs/DEPLOYMENT-GUIDE.md) - Complete deployment walkthrough
- [CONFIG-REFERENCE.md](docs/CONFIG-REFERENCE.md) - All configuration options
- [DESIGN.md](docs/DESIGN.md) - Architecture and three-layer config separation
- [HOWTO-PRODUCTION.md](docs/HOWTO-PRODUCTION.md) - Production operations and maintenance
- [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - Common issues and solutions
- [SHARED-ENVIRONMENTS.md](docs/SHARED-ENVIRONMENTS.md) - Multiple apps sharing infrastructure
- [SOMEDAY-MAYBE.md](docs/SOMEDAY-MAYBE.md) - Future improvement ideas
- Framework guides: [Django](docs/frameworks/django.md), [Rails](docs/frameworks/rails.md), [Generic](docs/frameworks/generic.md)

## Design Principles

**One canonical location for each config value**: Every configuration value should have exactly one correct place to live. Don't add fallback logic that checks multiple locations - this creates ambiguity about where values come from and masks configuration errors.

**Fail fast with clear errors**: If required configuration is missing, fail immediately with a helpful error message rather than silently falling back to defaults or alternative sources.

Example: `ecr_prefix` belongs in the environment's config.toml (infrastructure), not deploy.toml (app structure). The deploy script requires it from config.toml and fails with a clear error if missing.

## Environments Directory

Environment configs are stored separately, configured via `DEPLOYER_ENVIRONMENTS_DIR` in `.env`:

```
~/code/deployer-environments/
├── bootstrap/                # IAM roles and shared resources
├── myapp-staging/
│   ├── main.tf
│   ├── terraform.tfvars
│   └── config.toml
└── myapp-production/
```

## Maintainer Notes

When changing the environment config.toml structure, update:
1. `example-deployer-environments/myapp-staging/config.toml.example`
2. `example-deployer-environments/app-on-shared-staging/config.toml.example`
3. `docs/CONFIG-REFERENCE.md` (Environment config.toml Reference section)
4. All existing `*/config.toml` files in the environments directory

## IAM Policies (Bootstrap Terraform)

IAM roles and policies are managed in `deployer-environments/bootstrap/`. Key guidelines:
- **Use service-level wildcards** (e.g., `ecs:*`, `rds:*`) rather than listing individual actions
- **Apply resource restrictions** where they matter: S3, SSM, ECR, IAM scoped to `project_prefixes`
- **Keep IAM role management granular** due to sensitivity

To add a new project:
1. Edit bootstrap's `terraform.tfvars`, add to `project_prefixes`
2. Run `AWS_PROFILE=admin tofu apply`

For multi-account setups, see [MULTIPLE-AWS-ACCOUNTS.md](docs/MULTIPLE-AWS-ACCOUNTS.md).
