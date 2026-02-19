# Plan: CI/CD Deployment Support

## Problem

`deploy.py` currently requires **tofu state access at runtime** because the
environment's `config.toml` contains `${tofu:...}` placeholders that are
resolved by shelling out to `tofu output -json`. This means the deploy process
needs:

- OpenTofu installed
- The `deployer-environments` directory (with `.tf` files and state backend
  config)
- The **infra AWS profile** (which can read the S3 state backend containing
  *all* infrastructure state — passwords, ARNs, everything)

This is fine for local developer deployments but problematic for CI/CD:

1. **Excessive privilege** — CI/CD gets infra-level access when it only needs
   deploy-level access (ECR push, ECS update, SSM read)
2. **Unnecessary dependencies** — CI runner needs tofu installed and
   environments repo checked out
3. **Blast radius** — leaked CI credentials expose the entire tofu state
4. **The resolved values are stable** — they only change on `tofu apply`, not
   on every deploy

## Design Goals

1. **Keep the existing local workflow unchanged** — `deploy.py myapp-staging`
   continues to resolve `${tofu:...}` placeholders at deploy time
2. **A separate CI/CD deploy tool** that takes two explicit inputs and needs
   nothing else: the app's `deploy.toml` and a pre-resolved config JSON
3. **Provide a mechanism to keep the resolved config fresh** when
   infrastructure changes
4. **Support staleness detection** — CI/CD should know if it's deploying with
   an outdated config
5. **Minimal new infrastructure** — prefer AWS services already in use
6. **GitHub OIDC** — no long-lived AWS credentials stored as GitHub secrets

## Architecture

### Two Deployment Paths

The CI/CD tool doesn't know about "environments" in the deployer sense. It
doesn't have the deployer-environments repo, doesn't know about config.toml,
and doesn't call tofu. It just receives two files:

```
Local mode (existing deploy.py):
  deploy.toml + deployer-environments/myapp-staging/config.toml + tofu → Deployer
  (knows about environments, resolves tofu placeholders, selects AWS profile)

CI/CD mode (new bin/ci-deploy.py):
  deploy.toml + resolved-config.json → Deployer
  (no environment concept, no tofu, no deployer-environments, no profile magic)
```

`ci-deploy.py` is deliberately minimal. It doesn't need:

- Environment name resolution or linking
- `${tofu:...}` placeholder resolution
- AWS profile auto-selection (CI gets credentials via OIDC)
- The deployer-environments directory
- OpenTofu installed

It just needs deploy.toml (what to run) and resolved config (where to run it).

### How ci-deploy.py Gets Into CI

The deployer is already a pip-installable Python package (pyproject.toml with
`src/deployer`). Add a console_scripts entry point:

```toml
[project.scripts]
ci-deploy = "deployer.cli.ci_deploy:main"
```

Then in CI, install and run via uv:

```
uv tool install "deployer @ git+https://github.com/org/deployer.git"
ci-deploy deploy.toml resolved-config.json
```

Or as a one-liner without explicit install:

```
uvx --from "git+https://github.com/org/deployer.git" ci-deploy deploy.toml resolved-config.json
```

**Future optimization**: Build a Docker image with the deployer pre-installed
(`deployer-ci`), push to ECR, and use as the job container in GitHub Actions.
This avoids installing on every run and ensures exact version pinning. But
uv-from-git is fine to start.

### Resolved Config Format

The resolved config is a JSON file containing the fully-resolved environment
config dict plus metadata for staleness detection:

```json
{
  "_meta": {
    "environment": "myapp-staging",
    "environment_type": "staging",
    "resolved_at": "2026-02-17T12:00:00Z",
    "tofu_outputs_hash": "sha256:abc123...",
    "config_toml_hash": "sha256:def456..."
  },
  "infrastructure": { "cluster_name": "myapp-staging-cluster", ... },
  "services": { "config": { "web": { "cpu": 256, ... } }, ... },
  "database": { "host": "myapp-staging.abc.us-west-2.rds.amazonaws.com", ... },
  ...
}
```

The `_meta` block enables:

- **`environment`** — the deployer environment name, so ci-deploy can display
  it without needing to derive it
- **`environment_type`** — "staging" or "production", needed by deploy logic
  for environment-specific overrides from deploy.toml
- **`tofu_outputs_hash`** — SHA-256 of the `tofu output -json` blob. A later
  `resolve --verify` can compare hashes to detect drift.
- **`config_toml_hash`** — SHA-256 of the raw `config.toml`. Detects when
  someone edits config.toml but forgets to re-resolve.
- **`resolved_at`** — timestamp for staleness warnings.

Note: the `[aws]` section (profile names) is deliberately excluded from the
resolved config — CI/CD doesn't use named AWS profiles.

### Resolved Config Storage: S3

Store resolved configs in S3, mirroring how tofu stores state:

```
s3://deployer-resolved-configs-{account_id}/
  myapp-staging/config.json
  myapp-production/config.json
```

Why S3:

- Already in the AWS ecosystem — IAM, encryption, versioning are all built-in
- Works with any CI system (not coupled to GitHub)
- S3 versioning provides an automatic audit trail
- CI/CD IAM only needs `s3:GetObject` on this one bucket
- No size limits (unlike SSM's 8KB or GitHub's 48KB per secret)

The bucket is created by the bootstrap terraform module (same place as the
state bucket).

### Authentication: GitHub OIDC → AWS IAM

GitHub Actions uses OIDC federation to assume an AWS IAM role — **no AWS access
keys stored as GitHub secrets**.

**This is a completely separate role from the existing deployer-\* profiles.**
The `deployer-app-deploy`, `deployer-infra`, and `deployer-cognito` roles are
for human users assuming roles via named AWS profiles. The CI role is assumed
only by GitHub Actions via OIDC — no human ever uses it, no AWS profile
references it.

```
Existing roles (for humans via AWS profiles):
  deployer-app-deploy   ← deploy.py (local)
  deployer-infra        ← tofu.sh (local)
  deployer-cognito      ← cognito.py (local)

New role (for GitHub Actions via OIDC):
  deployer-ci-{project} ← ci-deploy (CI/CD only)
```

How it works:

1. Bootstrap terraform creates an **IAM OIDC identity provider** for
   `token.actions.githubusercontent.com` (account-wide, created once)
2. Bootstrap creates a **per-project `deployer-ci-{project}` IAM role**
   with a trust policy scoped to that project's GitHub repo
3. The GitHub Actions workflow uses `aws-actions/configure-aws-credentials`
   to assume the role via OIDC
4. Each role's policy grants deploy permissions scoped to that project

The CI role can do everything ci-deploy needs — including running database
migrations (ECS RunTask), pushing images (ECR), updating services (ECS),
and checking secrets (SSM). It just can't read tofu state, modify
infrastructure, or access other projects.

### Per-Project Isolation

Each project gets its own CI role. The myapp repo's CI role cannot see
anotherapp's resolved config (or ECS services, or ECR repos):

```
deployer-ci-myapp role:
  - Trust: only repo:myorg/myapp via OIDC
  - S3:    s3://bucket/myapp-*/config.json
  - ECS:   arn:...:service/myapp-*/*
  - ECR:   arn:...:repository/myapp-*
  - SSM:   arn:...:parameter/myapp/*

deployer-ci-anotherapp role:
  - Trust: only repo:myorg/anotherapp via OIDC
  - S3:    s3://bucket/anotherapp-*/config.json
  - ECS:   arn:...:service/anotherapp-*/*
  - ECR:   arn:...:repository/anotherapp-*
  - SSM:   arn:...:parameter/anotherapp/*
```

This follows the same project-prefix scoping pattern already used by the
`deployer-app-deploy` role in `iam-app-deploy.tf`.

The bootstrap variable maps project prefix → GitHub repo:

```hcl
github_ci_repos = {
  myapp      = "myorg/myapp"
  anotherapp = "myorg/anotherapp"
}
```

Bootstrap creates one role per entry via `for_each`.

### Staging vs Production Isolation via OIDC

The OIDC trust policy can scope which GitHub environment can assume the role.
Combined with GitHub environment protection rules, this controls who can
deploy to production:

```hcl
# Trust policy condition for deployer-ci-myapp:
condition {
  test     = "StringLike"
  variable = "token.actions.githubusercontent.com:sub"
  values   = [
    "repo:myorg/myapp:environment:staging",
    "repo:myorg/myapp:environment:production",
  ]
}
```

Then in GitHub:
- **staging environment**: no protection rules (deploys on push to main)
- **production environment**: requires manual approval from reviewers,
  restricted to main branch

This means the same `deployer-ci-myapp` IAM role is used for both staging
and production deploys, but **only the GitHub workflow running in the
"production" environment can trigger a production deploy**, and that
environment requires manual approval. The IAM role doesn't need to
distinguish — GitHub's environment protection is the gate.

### Freshness: Push on Apply

When `tofu apply` runs, the resolved config is automatically pushed:

```
tofu.sh apply myapp-staging
  → tofu apply succeeds
  → resolve config.toml using fresh tofu outputs
  → upload resolved-config.json to S3
  → log "Resolved config pushed to S3"
```

This is a post-apply hook in `tofu.sh`. Simple, synchronous, reliable. The
resolved config is always as fresh as the last `tofu apply`.

### Staleness Detection

Two levels:

1. **In CI/CD** — `ci-deploy` prints the `resolved_at` timestamp and warns if
   older than a configurable threshold. This is a soft warning, not a blocker.

2. **Local verification** — `resolve-config.py --verify` re-runs
   `tofu output -json`, hashes it, and compares to the stored
   `tofu_outputs_hash`. Answers: "has infrastructure changed since this config
   was resolved?"

## Implementation Steps

### Phase 1: Extract shared logic, then add resolve + ci-deploy

The goal is to keep both entry points thin — argument parsing and config
loading only — with all real work in shared modules.

#### Current state of deploy.py's main()

`deploy.py`'s `main()` currently does three things inline:

1. **Config loading** — resolve environment path, load config.toml, call
   `load_environment_config()`, derive environment_type
2. **Pre-flight checks** — validate config fields, audit deploy.toml,
   check ECR repos exist, check SSM secrets exist, check ECS cluster exists
3. **Deploy** — create `Deployer`, call `deployer.deploy()`

Steps 2 and 3 are the same regardless of where the config came from.
Step 1 is the only part that differs between local and CI.

#### Refactoring plan

Extract shared logic into `src/deployer/deploy/preflight.py`:

```python
# src/deployer/deploy/preflight.py

@dataclass
class PreflightOptions:
    skip_ecr_check: bool = False
    skip_secrets_check: bool = False
    skip_cluster_check: bool = False
    skip_audit: bool = False

def run_preflight_checks(
    deploy_config: DeployConfig,
    env_config: dict,
    environment: str,
    environment_type: str,
    project_dir: Path,
    options: PreflightOptions,
) -> None:
    """Run all pre-deployment validation checks.

    Raises SystemExit on failure.
    """
    # validate_environment_config, audit, ECR, secrets, cluster checks
    # (moved from deploy.py main())
```

After this refactor, both entry points become thin:

```python
# bin/deploy.py main() becomes:
def main():
    args = parse_args()
    config_path = resolve_config_path(args)       # local-specific
    env_config = load_environment_config(env_path) # local-specific (calls tofu)
    environment_type = derive_environment_from_env_name(environment)

    run_preflight_checks(...)  # shared
    deployer = Deployer(...)   # shared
    deployer.deploy()          # shared

# src/deployer/cli/ci_deploy.py main() becomes:
def main():
    args = parse_args()
    env_config, meta = load_resolved_config(args.resolved_config) # CI-specific
    environment_type = meta["environment_type"]                    # CI-specific

    run_preflight_checks(...)  # shared
    deployer = Deployer(...)   # shared
    deployer.deploy()          # shared
```

The `Deployer` class and `deployer.deploy` package are already shared and
need no changes — `env_config` is a plain dict in both cases.

#### pyproject.toml and package changes

To make `uvx --from "git+..." ci-deploy` work, we need a console_scripts
entry point. The changes are minimal:

1. Add `[project.scripts]` to `pyproject.toml`:

   ```toml
   [project.scripts]
   ci-deploy = "deployer.cli.ci_deploy:main"
   ```

2. Create the `cli` package (two new files):

   ```
   src/deployer/cli/__init__.py       # empty
   src/deployer/cli/ci_deploy.py      # main() entry point
   ```

The existing `bin/` scripts stay as-is — they're standalone scripts for
local use (`uv run python bin/deploy.py`), not entry points.

Dependencies are already sufficient: `boto3` covers all AWS calls,
`tomllib` is stdlib in 3.12+ (guaranteed by `requires-python = ">=3.12"`).
The `requests` dependency in deploy.py is only used by speed-test/Cognito
code which ci-deploy doesn't touch.

#### Implementation steps

1. **Extract `run_preflight_checks()` from deploy.py**
   - Move ECR, secrets, cluster, and audit checks into
     `src/deployer/deploy/preflight.py`
   - deploy.py's `main()` calls the extracted function
   - Verify existing tests still pass

2. **Add `bin/resolve-config.py <environment>`**
   - Calls `load_environment_config()` (same as deploy.py does today)
   - Computes `_meta` block (hashes, timestamps, environment_type)
   - Outputs JSON to stdout (or `--output <file>`)
   - Requires tofu + infra profile (runs locally in the deployer repo)
   - Optional `--push-s3` to upload directly to S3 bucket

3. **Add `ci-deploy` console_scripts entry point**
   - Source: `src/deployer/cli/ci_deploy.py`
   - Takes two positional args: path to deploy.toml, path to resolved
     config JSON (or `s3://` URI)
   - Loads resolved config JSON, extracts `_meta`, passes rest to shared
     preflight + `Deployer`
   - Gets `environment_type` from `_meta`
   - Prints `_meta.environment` and `_meta.resolved_at` for visibility
   - No AWS profile auto-selection (CI sets credentials via OIDC)
   - No tofu, no deployer-environments, no environment linking
   - Flags: `--dry-run`, `--force`, `--force-build`, `--skip-*` checks
   - Skips docker-compose audit by default (not relevant in CI)

4. **Tests**
   - Round-trip test: resolve → ci-deploy with --dry-run → same behavior
   - Verify `_meta` is stripped before passing to Deployer
   - Verify ci-deploy fails clearly if resolved config is missing required
     fields

### Phase 2: `modules/ci` and S3 infrastructure

The CI infrastructure lives as a **reusable module in deployer** (not
hand-rolled in deployer-environments). This keeps the CI IAM policy in sync
with what ci-deploy actually needs — both evolve together in the same repo.

```
deployer/modules/ci/          ← new module (IAM roles, OIDC, S3 bucket)
deployer-environments/bootstrap/  ← instantiates the module
```

5. **Create `modules/ci` in deployer**

   The module creates all CI infrastructure:

   - **S3 bucket** for resolved configs — versioned, encrypted, public
     access blocked (same pattern as state bucket)
   - **GitHub OIDC provider** — `aws_iam_openid_connect_provider` for
     `token.actions.githubusercontent.com` (account-wide, created once)
   - **Per-project `deployer-ci-{project}` IAM roles** — `for_each` over a
     `github_ci_repos` map variable (project prefix → GitHub org/repo)

   Module variables:

   ```hcl
   variable "github_ci_repos" {
     description = "Map of project prefix to GitHub org/repo for CI roles"
     type        = map(string)
     default     = {}
     # Example: { myapp = "myorg/myapp", anotherapp = "myorg/anotherapp" }
   }

   variable "project_prefixes" {
     description = "Project prefixes for IAM resource scoping"
     type        = list(string)
   }

   variable "github_oidc_environments" {
     description = "GitHub environments to allow in OIDC trust (e.g., staging, production)"
     type        = list(string)
     default     = ["staging", "production"]
   }
   ```

   Module outputs:

   ```hcl
   output "ci_deploy_role_arns" {
     description = "Map of project prefix to CI deploy role ARN"
     value       = { for k, v in aws_iam_role.ci_deploy : k => v.arn }
   }

   output "resolved_configs_bucket" {
     value = aws_s3_bucket.resolved_configs.bucket
   }
   ```

   Each role's IAM policy is scoped to one project prefix:
   - `s3:GetObject` on `{project}-*/config.json` in the resolved-configs bucket
   - ECS/ECR/SSM/RDS permissions scoped to `{project}-*` (same pattern as
     `iam-app-deploy.tf` but per-project instead of all prefixes)
   - `iam:PassRole` on `{project}-*-ecs-*` roles
   - No terraform state access

6. **Instantiate `modules/ci` in bootstrap**

   In `deployer-environments/bootstrap/main.tf`:

   ```hcl
   module "ci" {
     source = "../modules/ci"

     github_ci_repos  = var.github_ci_repos
     project_prefixes = var.project_prefixes
   }
   ```

   This is opt-in: if `github_ci_repos` is empty (the default), the module
   creates nothing.

7. **Add S3 read to ci-deploy**
   - Allow resolved-config arg to be an `s3://` URI
   - `ci-deploy deploy.toml s3://bucket/myapp-staging/config.json`
   - Fetches JSON via boto3 `s3.get_object`

### Phase 3: Automatic resolve on apply

9. **Add post-apply hook to `tofu.sh`**
   - After successful `tofu apply` or `tofu rollout`, run resolve-config.py
   - Auto-detect whether the resolved-configs bucket exists; if so, push
   - Print confirmation: "Resolved config pushed to S3"
   - Failure to push is a warning, not a fatal error

10. **Add staleness warning to ci-deploy**
    - Print age of resolved config on every run
    - Warn if older than threshold (default: 7 days)
    - `--max-config-age <hours>` flag
    - `--strict` makes staleness a fatal error

### Phase 4: Documentation

11. **Create `docs/CI-CD.md`** (see sketch below)

12. **Create example GitHub Actions workflow** in
    `examples/github-actions/deploy.yml`

13. **Update existing docs**
    - DESIGN.md: add CI/CD architecture diagram
    - CONFIG-REFERENCE.md: resolved config format
    - CLAUDE.md: add ci-deploy to key files

## docs/CI-CD.md Sketch

Below is a sketch of what the CI/CD setup guide would cover. This becomes
the actual doc in Phase 4.

----------------------------------------------------------------------

### Prerequisites

- An AWS account with deployer bootstrap applied
- A GitHub repository with a `deploy.toml` in the app root
- The deployer infrastructure already set up (`tofu apply` has been run)

### Step 1: Enable the CI Module in Bootstrap

The CI infrastructure is provided by `modules/ci` in the deployer repo.
Add it to your bootstrap configuration.

In `bootstrap/main.tf` (or your bootstrap instance), add the module:

```hcl
module "ci" {
  source = "../modules/ci"

  github_ci_repos  = var.github_ci_repos
  project_prefixes = var.project_prefixes
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

- An OIDC identity provider for GitHub Actions (account-wide, created once)
- A **per-project** `deployer-ci-{project}` IAM role for each entry
- An S3 bucket for resolved configs

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

### Step 2: Resolve and Push Config

After any `tofu apply` that changes infrastructure, the resolved config is
automatically pushed to S3 (via the tofu.sh post-apply hook).

You can also resolve manually:

```bash
uv run python bin/resolve-config.py myapp-staging --push-s3
```

Or verify the stored config is still fresh:

```bash
uv run python bin/resolve-config.py myapp-staging --verify
```

### Step 3: Configure GitHub Repository

In your app repo's GitHub settings, add these **environment variables**
(Settings → Environments → create "staging" and/or "production"):

| Variable                        | Value                                                  | Secret? |
| ------------------------------- | ------------------------------------------------------ | ------- |
| `AWS_REGION`                    | `us-west-2`                                            | No      |
| `CI_DEPLOY_ROLE_ARN`            | `arn:aws:iam::123456789012:role/deployer-ci-myapp`     | No      |
| `RESOLVED_CONFIG_S3_URI`        | `s3://deployer-resolved-configs-123456789012/myapp-staging/config.json` | No      |

No AWS access keys needed — OIDC handles authentication.

### Step 4: Add GitHub Actions Workflow

Create `.github/workflows/deploy.yml` in your app repo:

```yaml
name: Deploy

on:
  push:
    branches: [main]
  workflow_dispatch:  # manual trigger

permissions:
  id-token: write   # required for OIDC
  contents: read

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: staging  # uses the GitHub environment configured above

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

### Step 5: Multi-Environment Setup

For deploying to both staging and production, use GitHub environments with
protection rules:

```yaml
jobs:
  deploy-staging:
    environment: staging
    steps: ...  # same as above, uses staging vars

  deploy-production:
    needs: deploy-staging
    environment: production  # requires manual approval
    steps: ...  # same as above, uses production vars
```

GitHub environment protection rules can require:

- Manual approval from specific reviewers
- Wait timer (e.g., 10 minutes after staging)
- Branch restrictions (only main)

### How the Pieces Fit Together

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

### What Permissions Does CI Get?

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

### Troubleshooting

**"Resolved config is X days old"** — Run `tofu.sh apply` (or
`resolve-config.py --push-s3`) to refresh. The config becomes stale when
infrastructure changes but nobody re-resolves.

**"Could not assume role"** — Check that your repo is listed in
`github_ci_repos` in bootstrap tfvars and the OIDC provider is set up.

**"Access denied fetching S3"** — The deployer-ci role needs `s3:GetObject`
on the resolved-configs bucket. Check bootstrap IAM policy.

----------------------------------------------------------------------

## Interface Summary

```bash
# --- Existing (unchanged) ---

# Local deploy (resolves tofu at runtime)
uv run python bin/deploy.py myapp-staging

# --- New ---

# Resolve config and write to file
uv run python bin/resolve-config.py myapp-staging --output resolved.json

# Resolve config and push to S3
uv run python bin/resolve-config.py myapp-staging --push-s3

# Verify stored config is still fresh
uv run python bin/resolve-config.py myapp-staging --verify

# CI/CD deploy with local file
ci-deploy deploy.toml resolved-config.json

# CI/CD deploy with S3 URI
ci-deploy deploy.toml s3://bucket/myapp-staging/config.json

# CI/CD deploy with staleness enforcement
ci-deploy deploy.toml resolved-config.json --max-config-age 48 --strict
```

## Open Questions

1. **Bucket naming** — `deployer-resolved-configs-{account_id}` or something
   shorter? Should be configurable in bootstrap tfvars?

2. **Multi-account** — Per-account bucket (matching state bucket pattern) or
   one central bucket? Probably per-account.

3. **Deployer installation in CI** — `uv tool install` from git is simplest
   to start. A pre-built Docker image (`deployer-ci`) pushed to ECR would be
   faster and more reproducible. Add as a future optimization?

4. **Pre-flight checks in CI** — ci-deploy should probably run the same checks
   (ECR repos, secrets, cluster). Some may not apply — e.g., the
   docker-compose audit is a local dev concern. Start with same checks minus
   audit, adjust based on experience.

5. **Naming** — `ci-deploy` as the CLI command? Or `deployer-ci`?
   `deploy-ci`? Should signal "CI/CD deployment tool."

6. **GitHub environment per deployer environment?** — The sketch above uses
   GitHub environments ("staging", "production") for variable scoping and
   approval gates. This maps cleanly to deployer environments but means each
   app repo needs environment setup. Worth it for the approval controls.

## What This Does NOT Change

- Local developer workflow (`deploy.py myapp-staging`) is unchanged
- config.toml format and `${tofu:...}` placeholders are unchanged
- tofu.sh behavior is unchanged (Phase 3 adds a post-apply step)
- deploy.toml (app-side config) is unchanged
- The `Deployer` class and `deployer.deploy` internals are unchanged
- No new external dependencies (S3 and IAM are already in use)

## Risks

- **Stale config causes deploy failure** — Mitigated by automatic resolve on
  `tofu apply` and staleness warnings. The window is small.
- **S3 bucket adds infrastructure** — Minimal: one bucket per account.
  Created in bootstrap alongside the state bucket.
- **Resolved config contains infrastructure details** — ARNs and subnet IDs
  aren't secrets, but are sensitive-adjacent. S3 bucket has encryption at
  rest and restricted access (same treatment as state bucket).
- **Two deploy entry points to maintain** — ci-deploy is deliberately thin
  (arg parsing + JSON loading). The real logic stays in `Deployer` and the
  `deployer.deploy` package, shared by both.
- **Git-based uv install is slow in CI** — Acceptable to start (uv is fast).
  Docker image optimization can come later if CI time matters.
