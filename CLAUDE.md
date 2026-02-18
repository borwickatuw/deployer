# Deployer - Claude Code Context

## Project Overview

Infrastructure and deployment tooling for containerized applications on AWS ECS Fargate. Supports any framework that runs in Docker (Django, Rails, Node.js, etc.).

## Related Projects

- **deployer-environments** (`~/code/deployer-environments`) - Per-environment tofu configurations that use this repo's modules

## Key Files

- `bin/deploy.py` - Main deployment script (auto-selects AWS profile from config.toml)
- `bin/init.py` - Initialize new apps: generate deploy.toml and environment directories
- `bin/tofu.sh` - OpenTofu wrapper (auto-selects AWS profile from config.toml)
- `bin/ecs-run.py` - Run commands in ECS containers (uses [commands] from deploy.toml)
- `bin/link-environments.py` - Link environments to deploy.toml paths (local, gitignored)
- `bin/environment.py` - Start/stop staging environments
- `bin/ops.py` - Production monitoring (status, health, logs, maintenance, ecr, audit)
- `bin/emergency.py` - Emergency operations that modify production (rollback, scale, snapshot, restore)
- `bin/cognito.py` - Cognito user management (auto-selects AWS profile from config.toml)
- `bin/ssm-secrets.py` - SSM Parameter Store secrets management
- `bin/capacity-report.py` - ECS right-sizing recommendations
- `bin/resolve-config.py` - Resolve config.toml into standalone JSON for CI/CD
- `src/deployer/cli/ci_deploy.py` - CI/CD deployment entry point (`ci-deploy` console_scripts)
- `src/deployer/deploy/preflight.py` - Shared preflight checks (used by deploy.py and ci-deploy)
- `src/deployer/deploy/deployer.py` - Deployer class (shared between deploy.py and ci-deploy)
- `modules/` - Reusable Terraform/OpenTofu modules
- `modules/ci/` - GitHub OIDC, S3 bucket, per-project CI IAM roles
- `DEPLOYER_ENVIRONMENTS_DIR` - Per-environment configurations (set in `.env`)
- `example-deployer-environments/` - Example environments directory structure
- `example-deploy.toml` - Example application deploy.toml
- `examples/github-actions/deploy.yml` - Example CI/CD workflow

## Common Commands

```bash
# Infrastructure (use tofu.sh wrapper - auto-selects AWS profile)
./bin/tofu.sh plan myapp-staging
./bin/tofu.sh apply myapp-staging          # also auto-pushes resolved config to S3
./bin/tofu.sh rollout myapp-staging        # init + plan + apply in one command

# Local deployment (uses linked deploy.toml)
uv run python bin/deploy.py myapp-staging

# CI/CD deployment (uses pre-resolved config)
ci-deploy deploy.toml resolved-config.json
ci-deploy deploy.toml s3://bucket/myapp-staging/config.json

# Resolve config for CI/CD
uv run python bin/resolve-config.py myapp-staging --push-s3

# Link environment to deploy.toml (one-time setup)
uv run python bin/link-environments.py myapp-staging ~/code/myapp/deploy.toml

# Run commands in containers (uses linked deploy.toml)
uv run python bin/ecs-run.py run myapp-staging migrate

# Production monitoring (read-only)
uv run python bin/ops.py myapp-production audit    # Run all health/security checks
uv run python bin/ops.py myapp-production status   # View current state

# Emergency operations (modify production)
uv run python bin/emergency.py myapp-production rollback --service web
```

## Key Documentation

- [DEPLOYMENT-GUIDE.md](docs/DEPLOYMENT-GUIDE.md) - Complete deployment walkthrough
- [CONFIG-REFERENCE.md](docs/CONFIG-REFERENCE.md) - All configuration options
- [DESIGN.md](docs/background/DESIGN.md) - Architecture and three-layer config separation
- [PRODUCTION.md](docs/operations/PRODUCTION.md) - Production operations and maintenance
- [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - Common issues and solutions
- [SHARED-ENVIRONMENTS.md](docs/operations/SHARED-ENVIRONMENTS.md) - Multiple apps sharing infrastructure
- [HOWTO-PUBLISH.md](docs/internal/HOWTO-PUBLISH.md) - Publishing to the public repository
- [CI-CD.md](docs/CI-CD.md) - CI/CD deployment setup with GitHub Actions
- [SOMEDAY-MAYBE.md](docs/internal/SOMEDAY-MAYBE.md) - Future improvement ideas
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
1. `example-deployer-environments/app-on-shared-staging/config.toml.example`
1. `docs/CONFIG-REFERENCE.md` (Environment config.toml Reference section)
1. All existing `*/config.toml` files in the environments directory

## IAM Policies (Bootstrap Terraform)

IAM roles and policies are managed in `deployer-environments/bootstrap/`. Key guidelines:

- **Use service-level wildcards** (e.g., `ecs:*`, `rds:*`) rather than listing individual actions
- **Apply resource restrictions** where they matter: S3, SSM, ECR, IAM scoped to `project_prefixes`
- **Keep IAM role management granular** due to sensitivity

To add a new project:

1. Edit bootstrap's `terraform.tfvars`, add to `project_prefixes`
1. Run `AWS_PROFILE=admin tofu apply`

For multi-account setups, see [MULTIPLE-ACCOUNTS.md](docs/operations/MULTIPLE-ACCOUNTS.md).

## Security

This is infrastructure code. Security focus areas:

- **IAM policies**: Managed in `deployer-environments/bootstrap/`. Use service-level wildcards with resource restrictions.
- **Secrets**: Never hardcode. Use SSM Parameter Store (`bin/ssm-secrets.py`) or Secrets Manager.
- **AWS profiles**: Scripts auto-select profiles from config.toml. Never use `--profile admin` in deployed code.
- **IaC scanning**: `make security-checkov` scans OpenTofu modules with Checkov. Intentional suppressions are documented in the Makefile.
- **Code review**: Review changes manually, especially IAM policy modifications.

## Cross-Repository Ideas

When you discover a pattern or practice that would benefit other repositories, capture it:

```bash
claude-idea deployer "Description of the idea"
```

Ideas are collected in `~/code/claude-meta/docs/IDEAS.md` for cross-project review.
