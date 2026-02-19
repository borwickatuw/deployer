# Plan Archive

Completed work, moved here from SOMEDAY-MAYBE.md and PLAN.md.

______________________________________________________________________

## Checkov Findings — Resolved

### Resolved (removed from skip list)

| Check       | Description                 | Resolution                                                       |
| ----------- | --------------------------- | ---------------------------------------------------------------- |
| CKV_AWS_118 | RDS enhanced monitoring     | Added `monitoring_interval` variable (default 60s) with IAM role |
| CKV_AWS_353 | RDS Performance Insights    | Added `performance_insights_enabled` variable (default true)     |
| CKV_AWS_338 | CloudWatch 1-year retention | Changed all `log_retention_days` defaults from 30 to 365         |

### Resolved (still in skip list — Checkov can't evaluate variables)

| Check       | Description             | Resolution                                                                                 |
| ----------- | ----------------------- | ------------------------------------------------------------------------------------------ |
| CKV_AWS_150 | ALB deletion protection | Added `deletion_protection` variable (default false for staging)                           |
| CKV_AWS_91  | ALB access logging      | Added `access_logs_enabled` variable with `alb-access-logs` module                         |
| CKV_AWS_16  | RDS storage encryption  | Added `storage_encrypted` variable (default true)                                          |
| CKV_AWS_129 | RDS logging             | Added parameter group with `log_connections`, `log_disconnections`, CloudWatch log exports |
| CKV2_AWS_30 | RDS query logging       | Added `log_statement=ddl` and `log_min_duration_statement=1000` to parameter group         |
| CKV2_AWS_11 | VPC flow logs           | Added flow logs resources to VPC module (CloudWatch destination, 365-day retention)        |

______________________________________________________________________

## CI/CD Deployment Support — Completed (Feb 2026)

Added CI/CD deployment support via GitHub Actions with OIDC authentication, eliminating the need for stored AWS credentials or OpenTofu access in CI.

### What was built

- **`ci-deploy` CLI** (`src/deployer/cli/ci_deploy.py`) — Console script entry point for CI/CD deployments. Takes `deploy.toml` + pre-resolved config JSON (local file or `s3://` URI). No tofu, no deployer-environments, no AWS profile auto-selection.
- **`bin/resolve-config.py`** — Resolves `${tofu:...}` placeholders into a standalone JSON file with `_meta` block (hashes, timestamps) for staleness detection. Supports `--push-s3`, `--verify`, `--output`.
- **`src/deployer/deploy/preflight.py`** — Extracted shared pre-flight checks from `deploy.py` so both local and CI entry points share the same validation logic.
- **`modules/ci`** — Terraform module creating GitHub OIDC provider + S3 bucket for resolved configs. Instantiated in bootstrap.
- **`modules/ci-role`** — Per-project CI IAM role scoped to one project prefix. Trusts a specific GitHub repo via OIDC. Instantiated per-environment.
- **`tofu.sh` post-apply hook** — Automatically pushes resolved config to S3 after successful `tofu apply`.
- **Staleness detection** — `ci-deploy` warns if resolved config is old; `--max-config-age` and `--strict` flags for enforcement.
- **`docs/CI-CD.md`** — Full setup guide.
- **`examples/github-actions/deploy.yml`** — Example GitHub Actions workflow.

### Key design decisions

- Two separate deployment paths (local vs CI) sharing the same `Deployer` class and preflight checks.
- Per-project IAM roles with resource-scoped permissions (ECR, ECS, SSM, S3 all scoped to `{project}-*`).
- GitHub OIDC for authentication — no stored AWS credentials.
- Resolved config stored in S3 with versioning, pushed automatically on `tofu apply`.
