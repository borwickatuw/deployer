# CI/CD Deployment Guide

This guide covers deploying applications from CI/CD pipelines (GitHub Actions) using the `ci-deploy` tool. Unlike local `deploy.py`, CI/CD deployments need no OpenTofu, no deployer-environments directory, and no infra-level AWS access.

## How It Works

Local and CI/CD deployments share the same deploy logic but differ in how they get configuration:

```
Local (deploy.py):
  deploy.toml + config.toml + tofu outputs → Deployer
  (resolves ${tofu:...} placeholders at runtime, needs infra AWS profile)

CI/CD (ci-deploy):
  deploy.toml + resolved-config.json → Deployer
  (pre-resolved JSON, no tofu, no deployer-environments, OIDC credentials)
```

The resolved config JSON is produced by `bin/resolve-config.py` and pushed to S3. CI/CD fetches it at deploy time. Authentication uses GitHub OIDC — no stored AWS credentials.

```
Developer laptop                    GitHub Actions
─────────────────                   ──────────────

tofu.sh apply myapp-staging         git push (app code)
  │                                   │
  ├─ tofu apply                       ├─ actions/checkout
  │    (creates/updates infra)        │
  │                                   ├─ aws-actions/configure-aws-credentials
  ├─ resolve-config.py                │    (OIDC → assume {project}-ci-deploy role)
  │    (resolves ${tofu:...})         │
  │                                   ├─ aws s3 cp (fetch resolved-config.json)
  └─ push to S3 ─────────────────►   │
     resolved-config.json             ├─ ci-deploy deploy.toml resolved-config.json
                                      │    (build, push, deploy — no tofu needed)
                                      │
                                      └─ done
```

## Prerequisites

- An AWS account with deployer bootstrap applied
- A GitHub repository with a `deploy.toml` in the app root
- The deployer infrastructure already set up (`tofu apply` has been run)

## Step 1: Enable CI Shared Infrastructure in Bootstrap

The shared CI infrastructure (OIDC provider + S3 bucket) is provided by `modules/ci`. Add it to your bootstrap configuration.

In your bootstrap instance's `main.tf`:

```hcl
module "ci" {
  source = "../modules/ci"
}
```

Add outputs for environments to reference via remote state:

```hcl
output "oidc_provider_arn" {
  value = module.ci.oidc_provider_arn
}

output "resolved_configs_bucket" {
  value = module.ci.resolved_configs_bucket
}

output "resolved_configs_bucket_arn" {
  value = module.ci.resolved_configs_bucket_arn
}
```

Run `tofu apply` in your bootstrap directory. This creates:

- A GitHub OIDC identity provider (account-wide, created once)
- An S3 bucket for resolved configs (versioned, encrypted)

## Step 2: Add a CI Role to Your Environment

Per-project CI roles are created using `modules/ci-role`, instantiated in each environment's tofu config. This keeps the GitHub repo name defined alongside the environment it deploys to.

In your environment's `terraform.tfvars`, add:

```hcl
github_repo = "myorg/myapp"
```

In your environment's `main.tf`, add:

```hcl
variable "github_repo" {
  description = "GitHub org/repo for CI/CD deployment"
  type        = string
  default     = ""
}

module "ci_role" {
  source = "../modules/ci-role"
  count  = var.github_repo != "" ? 1 : 0

  project_prefix              = "myapp"
  github_repo                 = var.github_repo
  oidc_provider_arn           = data.terraform_remote_state.bootstrap.outputs.oidc_provider_arn
  resolved_configs_bucket_arn = data.terraform_remote_state.bootstrap.outputs.resolved_configs_bucket_arn
  region                      = var.region
  permissions_boundary        = data.terraform_remote_state.bootstrap.outputs.ecs_role_boundary_arn
}
```

Run `tofu apply`. The module creates a `myapp-ci-deploy` IAM role that only GitHub Actions from `myorg/myapp` can assume, with permissions scoped to `myapp-*` resources.

Note the output:

```
ci_role_arn = "arn:aws:iam::123456789012:role/myapp-ci-deploy"
```

## Step 3: Resolve and Push Config

After any `tofu apply` that changes infrastructure, the resolved config is automatically pushed to S3 via the `tofu.sh` post-apply hook. No manual action is needed.

You can also resolve and push manually:

```bash
uv run python bin/resolve-config.py myapp-staging --push-s3
```

Or write to a local file and push:

```bash
uv run python bin/resolve-config.py myapp-staging --output resolved.json --push-s3
```

To verify the stored config is still fresh (hashes match current tofu state):

```bash
uv run python bin/resolve-config.py myapp-staging --verify --verify-file resolved.json
```

## Step 4: Configure GitHub Repository

In your app repo's GitHub settings, create environments and add variables (Settings > Environments > create "staging" and/or "production"):

| Variable                 | Value                                                                   | Secret? |
| ------------------------ | ----------------------------------------------------------------------- | ------- |
| `AWS_REGION`             | `us-west-2`                                                             | No      |
| `CI_DEPLOY_ROLE_ARN`     | `arn:aws:iam::123456789012:role/myapp-ci-deploy`                        | No      |
| `RESOLVED_CONFIG_S3_URI` | `s3://deployer-resolved-configs-123456789012/myapp-staging/config.json` | No      |

No AWS access keys needed — OIDC handles authentication.

## Step 5: Add GitHub Actions Workflow

Create `.github/workflows/deploy.yml` in your app repo:

```yaml
name: Deploy

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  id-token: write   # required for OIDC
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: staging

    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.CI_DEPLOY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Fetch resolved config from S3
        run: aws s3 cp "${{ vars.RESOLVED_CONFIG_S3_URI }}" resolved-config.json

      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2

      - name: Deploy
        run: uvx --from "git+https://github.com/myorg/deployer.git" ci-deploy deploy.toml resolved-config.json
```

## Step 6: Multi-Environment Setup

For deploying to both staging and production, use GitHub environments with protection rules:

```yaml
jobs:
  deploy-staging:
    runs-on: ubuntu-latest
    environment: staging
    steps: ...  # same as above, uses staging vars

  deploy-production:
    runs-on: ubuntu-latest
    needs: deploy-staging
    environment: production  # requires manual approval
    steps: ...  # same as above, uses production vars
```

GitHub environment protection rules can require:

- Manual approval from specific reviewers
- Wait timer (e.g., 10 minutes after staging)
- Branch restrictions (only main)

## CI Permissions

Each `{project}-ci-deploy` IAM role can:

- Describe RDS instances (for health checks)
- Pass ECS task/execution roles (scoped to that project)
- Push images to ECR (scoped to that project's repos only)
- Read from the resolved-configs S3 bucket (scoped to that project's keys)
- Read SSM parameters (scoped to that project's path prefix)
- Update ECS services and run tasks (scoped to that project's services only)

The role **cannot**:

- Access any other project's resources
- Access the terraform state S3 bucket
- Create or destroy infrastructure
- Modify IAM roles or policies
- Read OpenTofu state

## ci-deploy Reference

```
ci-deploy <deploy.toml> <resolved-config.json|s3://...> [options]

Options:
  --dry-run              Show what would be done without making changes
  --force                Deploy even if infrastructure is unavailable
  --force-build          Force rebuilding images even if unchanged
  --skip-ecr-check       Skip ECR repository existence check
  --skip-secrets-check   Skip SSM secrets existence check
  --skip-cluster-check   Skip ECS cluster existence check
  --max-config-age HOURS Warn if resolved config is older than this
  --strict               Treat staleness warnings as errors
```

## Troubleshooting

**"Resolved config is X days old"** — Run `tofu.sh apply` (or `resolve-config.py --push-s3`) to refresh. The config becomes stale when infrastructure changes but nobody re-resolves.

**"Could not assume role"** — Check that `modules/ci-role` is instantiated in the environment's tofu config with the correct `github_repo`, and that `tofu apply` has been run.

**"Access denied fetching S3"** — The ci-deploy role needs `s3:GetObject` on the resolved-configs bucket. Check that `modules/ci` is instantiated in bootstrap and `modules/ci-role` in the environment.

**"Missing required field: infrastructure.\*"** — The resolved config is incomplete. Re-resolve: `uv run python bin/resolve-config.py <env> --push-s3`

**"Invalid JSON in resolved config"** — The config file is corrupted or truncated. Re-resolve and push again.
