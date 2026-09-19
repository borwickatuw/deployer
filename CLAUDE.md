# Deployer - Claude Code Context

## Project Overview

Infrastructure and deployment tooling for containerized applications on AWS ECS Fargate. Supports any framework that runs in Docker (Django, Rails, Node.js, etc.).

## Related Projects

- **deployer-environments** - Per-environment tofu configurations that use this repo's modules
- **claude-meta** - Cross-repo standards and audit tooling

## Key Files

- `bin/deploy.py` - Main deployment script (auto-selects AWS profile from config.toml)
- `bin/init.py` - Initialize bootstrap infrastructure, new apps, and environment directories
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
- `modules/ci/` - GitHub OIDC provider and S3 bucket (shared CI infra, in bootstrap)
- `modules/ci-role/` - Per-project CI IAM role (instantiated per-environment)
- `templates/` - Environment templates (standalone, shared-app, shared-infra for staging/production)
- `DEPLOYER_ENVIRONMENTS_DIR` - Per-environment configurations (set in `.env`)

## Common Commands

```bash
# Infrastructure (use tofu.sh wrapper - auto-selects AWS profile)
./bin/tofu.sh plan myapp-staging
./bin/tofu.sh apply myapp-staging          # also auto-pushes resolved config to S3
./bin/tofu.sh rollout myapp-staging        # init + plan + apply in one command

# Local deployment (uses linked deploy.toml)
uv run python bin/deploy.py deploy myapp-staging

# CI/CD deployment (uses pre-resolved config)
ci-deploy deploy.toml resolved-config.json
ci-deploy deploy.toml s3://bucket/myapp-staging/config.json

# Resolve config for CI/CD
uv run python bin/resolve-config.py myapp-staging --push-s3

# Bootstrap infrastructure (one-time per AWS account)
uv run python bin/init.py bootstrap
uv run python bin/init.py bootstrap --migrate-state bootstrap-staging

# Initialize new environment from template
uv run python bin/init.py environment --list-templates
uv run python bin/init.py environment --app-name myapp --template standalone-staging
uv run python bin/init.py update-services myapp-staging --deploy-toml /path/to/deploy.toml

# Link environment to deploy.toml (one-time setup)
uv run python bin/link-environments.py myapp-staging /path/to/myapp/deploy.toml

# Run commands in containers (uses linked deploy.toml)
uv run python bin/ecs-run.py run myapp-staging migrate

# Production monitoring (read-only)
uv run python bin/ops.py audit myapp-production    # Run all health/security checks
uv run python bin/ops.py status myapp-production   # View current state

# Emergency operations (modify production)
uv run python bin/emergency.py rollback myapp-production --service web
```

## Key Documentation

- [DEPLOYMENT-GUIDE.md](docs/DEPLOYMENT-GUIDE.md) - Complete deployment walkthrough
- [CONFIG-REFERENCE.md](docs/CONFIG-REFERENCE.md) - All configuration options
- [DESIGN.md](docs/internal/DESIGN.md) - Architecture and three-layer config separation
- [PRODUCTION.md](docs/operations/PRODUCTION.md) - Production operations and maintenance
- [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - Common issues and solutions
- [GOVERNANCE.md](docs/GOVERNANCE.md) - Access, risk, continuity and privacy posture
- [SHARED-ENVIRONMENTS.md](docs/operations/SHARED-ENVIRONMENTS.md) - Multiple apps sharing infrastructure
- [HOWTO-PUBLISH.md](docs/internal/HOWTO-PUBLISH.md) - Publishing to the public repository
- [someday-maybe/](docs/someday-maybe/) - Future improvement ideas, one file per idea
- [PLAN-METHOD.md](docs/PLAN-METHOD.md) - The plan workflow: what each state, key and transition means
- Scenario guides: [Django](docs/scenarios/django.md), [Rails](docs/scenarios/rails.md), [Generic](docs/scenarios/generic.md), [CI/CD](docs/scenarios/ci-cd.md), [Passive Deployer](docs/scenarios/passive-deployer.md)

## Generic Codebase

This repository is published as a generic, reusable tool. **Never use real project names, org names, account IDs, or other internal identifiers in code, docs, examples, or comments.** Use generic placeholders:

- Project names: `myapp`, `otherapp`, `anotherapp`
- Organizations: `myorg`
- Account IDs: `123456789012`
- Domains: `example.com`, `myapp.example.com`
- Users: `deployer`, `admin`

The `local/` directory is gitignored and may contain real project names — that's fine. Everything else must be generic.

## Design Principles

One canonical location per config value, and fail fast with a clear error — the
general statements live in `~/.claude/CLAUDE.md`. What they mean here:

Example: `ecr_prefix` belongs in the environment's config.toml (infrastructure), not deploy.toml (app structure). The deploy script requires it from config.toml and fails with a clear error if missing.

**Tests reflect actual usage, not speculative generality**: Tests should not exercise code paths that no production code uses. If a function parameter is always passed the same value in production, don't keep the parameter general just because tests pass different values — simplify both the code and the tests. Tests should not introduce branches that production doesn't need.

## Environments Directory

Environment configs are stored separately, configured via `DEPLOYER_ENVIRONMENTS_DIR` in `.env`:

```
~/deployer-environments/
├── bootstrap/                # IAM roles and shared resources
├── myapp-staging/
│   ├── main.tf
│   ├── terraform.tfvars
│   └── config.toml
└── myapp-production/
```

## Maintainer Notes

When changing the environment config.toml structure, update:

1. `templates/standalone-staging/config.toml.example` (and production)
1. `templates/shared-app-staging/config.toml.example` (and production)
1. `docs/CONFIG-REFERENCE.md` (Environment config.toml Reference section)
1. All existing `*/config.toml` files in the environments directory

## IAM Policies (Bootstrap Terraform)

IAM roles and policies are managed in `modules/bootstrap/`. Key guidelines:

- **Use service-level wildcards** (e.g., `ecs:*`, `rds:*`) rather than listing individual actions
- **Apply resource restrictions** where they matter: S3, SSM, ECR, IAM scoped to `project_prefixes`
- **Keep IAM role management granular** due to sensitivity

To add a new project:

1. Edit bootstrap's `terraform.tfvars`, add to `project_prefixes`
1. Run `AWS_PROFILE=admin tofu apply`

For multi-account setups, see [MULTIPLE-ACCOUNTS.md](docs/operations/MULTIPLE-ACCOUNTS.md).

## Security

This is infrastructure code. Security focus areas:

- **IAM policies**: Managed in `modules/bootstrap/`. Use service-level wildcards with resource restrictions.
- **Secrets**: Never hardcode. Use SSM Parameter Store (`bin/ssm-secrets.py`) or Secrets Manager.
- **AWS profiles**: Scripts auto-select profiles from config.toml. Never use `--profile admin` in deployed code.
- **IaC scanning**: `make security-checkov` scans OpenTofu modules with Checkov (via `uvx`). Intentional suppressions are documented in the Makefile.
- **Dependency CVEs**: `make security-deps` runs `uv audit`. `make security-updates` adds the outdated-package report (quarterly review).
- **Secrets scanning**: `make security-secrets` runs detect-secrets against `.secrets.baseline`.
- **Code review**: Review changes manually, especially IAM policy modifications.

Run `make security` (bandit + uv audit + detect-secrets + checkov) before committing.

## Posture: accessibility and i18n

Deployer is a CLI plus OpenTofu modules — no web app, no GUI, no templating
framework. The entire HTML surface is the CloudFront 503 page built in
`modules/cloudfront-alb/locals.tf` and uploaded to S3, and it targets **WCAG
2.1 Level AA**. `make a11y` renders both environment variants and runs pa11y
against them.

An environment that supplies its own `error_page_content` to the
`cloudfront-alb` module replaces that page wholesale; its accessibility is the
supplying environment's responsibility, not this repo's. See
[docs/internal/DECISIONS.md](docs/internal/DECISIONS.md) "The accessibility
surface is one static error page".

**UI translation: N/A** — there is no UI to translate, and the CLI output is
read by the operator running the deploy. **Character-set support: implemented**
— every text read and write passes `encoding="utf-8"` explicitly rather than
inheriting the host locale, which ruff's `PLW1514` enforces. The artifacts an
operator reads back (`emergency` checkpoints, the timing report, the resolved
config JSON) use `ensure_ascii=False`, so non-ASCII values in a `deploy.toml`
or a tofu output arrive readable instead of `\uXXXX`-escaped. JSON that is
hashed or handed to an AWS API keeps the default escaping: those strings are
compared byte-for-byte, not read.

## pysmelly

Read [docs/PYSMELLY.md](docs/PYSMELLY.md) — the findings register and review
conventions — before running pysmelly code smell analysis on this project. The
phase-by-phase reasoning behind the standing verdicts is in
[docs/internal/PYSMELLY.md](docs/internal/PYSMELLY.md).

## Cross-Repository Ideas

```
claude-idea deployer "Description of the pattern or improvement"
```

## Plan Lifecycle

Managed by [fileplan](https://pypi.org/project/fileplan/) via `plan.toml`
at the repo root; [docs/PLAN-METHOD.md](docs/PLAN-METHOD.md) defines every
state, key and transition.

```
uv run --group dev fileplan                    # the workflow: states + transitions
uv run --group dev fileplan list               # someday-maybe/plan items + register state
```

`--group dev` is required: this project sets `default-groups = []`, so a bare
`uv run fileplan` gets no dev dependencies and silently falls back to whatever
`fileplan` is on `PATH` instead of the pinned version.

| Location                                                             | Holds                                                |
| -------------------------------------------------------------------- | ---------------------------------------------------- |
| [docs/someday-maybe/](docs/someday-maybe/)                           | Ideas kept, nothing committed to                     |
| [docs/plan/](docs/plan/)                                             | Active phases, one file each, numbered from 1        |
| [docs/plan-archive/](docs/plan-archive/)                             | Recent full records of closed phases                 |
| [PLAN-ARCHIVE.md](docs/plan-archive/PLAN-ARCHIVE.md)                 | The register: `## N. Title` summaries of closed work |
| [PLAN-ARCHIVE-2026-09.md](docs/plan-archive/PLAN-ARCHIVE-2026-09.md) | Rotated segment: everything closed pre-conversion    |

Cross-repo arcs queued against deployer keep **claude-meta's** phase
numbers, cited `fileplan-claude-meta:<number>` so one grep finds every
citation; the pointers live in docs/PLAN-METHOD.md.
