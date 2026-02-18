# CI/CD Deployment Guide

This guide covers deploying applications from CI/CD pipelines (GitHub Actions)
using the `ci-deploy` tool. Unlike local `deploy.py`, CI/CD deployments need
no OpenTofu, no deployer-environments directory, and no infra-level AWS access.

## How It Works

Local and CI/CD deployments share the same deploy logic but differ in how they
get configuration:

```
Local (deploy.py):
  deploy.toml + config.toml + tofu outputs → Deployer
  (resolves ${tofu:...} placeholders at runtime, needs infra AWS profile)

CI/CD (ci-deploy):
  deploy.toml + resolved-config.json → Deployer
  (pre-resolved JSON, no tofu, no deployer-environments, OIDC credentials)
```

The resolved config JSON is produced by `bin/resolve-config.py` and pushed to
S3. CI/CD fetches it at deploy time. Authentication uses GitHub OIDC — no
stored AWS credentials.

```
Developer laptop                    GitHub Actions
─────────────────                   ──────────────

tofu.sh apply myapp-staging         git push (app code)
  │                                   │
  ├─ tofu apply                       ├─ actions/checkout
  │    (creates/updates infra)        │
  │                                   ├─ aws-actions/configure-aws-credentials
  ├─ resolve-config.py                │    (OIDC → assume deployer-ci-{project} role)
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

## Step 1: Enable the CI Module in Bootstrap

The CI infrastructure is provided by `modules/ci` in the deployer repo.
Add it to your bootstrap configuration.

In your bootstrap instance's `main.tf`, add the module:

```hcl
module "ci" {
  source = "../../deployer/modules/ci"

  region          = var.region
  github_ci_repos = var.github_ci_repos
}
```

Add the variable (in `main.tf` or `variables.tf`):

```hcl
variable "github_ci_repos" {
  type    = map(string)
  default = {}
}
```

Add to your bootstrap `terraform.tfvars`:

```hcl
github_ci_repos = {
  myapp      = "myorg/myapp"
  anotherapp = "myorg/anotherapp"
}
```

Run `tofu apply` in your bootstrap directory. The module creates:

- A GitHub OIDC identity provider (account-wide, created once)
- A per-project `deployer-ci-{project}` IAM role for each entry
- An S3 bucket for resolved configs (versioned, encrypted)

Note the outputs:

```
ci_deploy_role_arns = {
  myapp      = "arn:aws:iam::123456789012:role/deployer-ci-myapp"
  anotherapp = "arn:aws:iam::123456789012:role/deployer-ci-anotherapp"
}
resolved_configs_bucket = "deployer-resolved-configs-123456789012"
```

Each role can only access its own project's resources — myapp's CI role
cannot see anotherapp's resolved configs, ECR repos, or ECS services.

## Step 2: Resolve and Push Config

After any `tofu apply` that changes infrastructure, the resolved config is
automatically pushed to S3 via the `tofu.sh` post-apply hook. No manual
action is needed.

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

## Step 3: Configure GitHub Repository

In your app repo's GitHub settings, create environments and add variables
(Settings > Environments > create "staging" and/or "production"):

| Variable                 | Value                                                                  | Secret? |
| ------------------------ | ---------------------------------------------------------------------- | ------- |
| `AWS_REGION`             | `us-west-2`                                                           | No      |
| `CI_DEPLOY_ROLE_ARN`     | `arn:aws:iam::123456789012:role/deployer-ci-myapp`                    | No      |
| `RESOLVED_CONFIG_S3_URI` | `s3://deployer-resolved-configs-123456789012/myapp-staging/config.json` | No      |

No AWS access keys needed — OIDC handles authentication.

## Step 4: Add GitHub Actions Workflow

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

## Step 5: Multi-Environment Setup

For deploying to both staging and production, use GitHub environments with
protection rules:

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

Each `deployer-ci-{project}` IAM role can:

- Push images to ECR (scoped to that project's repos only)
- Update ECS services and run tasks (scoped to that project's services only)
- Read SSM parameters (scoped to that project's path prefix)
- Read from the resolved-configs S3 bucket (scoped to that project's keys)
- Describe RDS instances (for health checks)
- Pass ECS task/execution roles (scoped to that project)

The role **cannot**:

- Read OpenTofu state
- Modify IAM roles or policies
- Access the terraform state S3 bucket
- Create or destroy infrastructure
- Access any other project's resources

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

**"Resolved config is X days old"** — Run `tofu.sh apply` (or
`resolve-config.py --push-s3`) to refresh. The config becomes stale when
infrastructure changes but nobody re-resolves.

**"Could not assume role"** — Check that your repo is listed in
`github_ci_repos` in bootstrap tfvars and that `tofu apply` has been run.

**"Access denied fetching S3"** — The deployer-ci role needs `s3:GetObject`
on the resolved-configs bucket. Check that the CI module is instantiated in
bootstrap.

**"Missing required field: infrastructure.*"** — The resolved config is
incomplete. Re-resolve: `uv run python bin/resolve-config.py <env> --push-s3`

**"Invalid JSON in resolved config"** — The config file is corrupted or
truncated. Re-resolve and push again.
