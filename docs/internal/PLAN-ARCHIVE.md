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
- **`docs/scenarios/ci-cd.md`** — Full setup guide with example GitHub Actions workflow.

### Key design decisions

- Two separate deployment paths (local vs CI) sharing the same `Deployer` class and preflight checks.
- Per-project IAM roles with resource-scoped permissions (ECR, ECS, SSM, S3 all scoped to `{project}-*`).
- GitHub OIDC for authentication — no stored AWS credentials.
- Resolved config stored in S3 with versioning, pushed automatically on `tofu apply`.

______________________________________________________________________

## Low-Complexity Improvements — Completed (Feb 2026)

8 items moved from SOMEDAY-MAYBE.md to PLAN.md, then implemented:

1. **Remove Django default commands fallback** — Removed `DJANGO_DEFAULT_COMMANDS` dict and `get_manage_command()` from `src/deployer/core/config.py`. `get_run_command()` and `command_requires_ddl()` now require deploy.toml and raise ValueError if missing. All commands must be explicitly defined in deploy.toml `[commands]`.

1. **RDS encryption in transit (CKV2_AWS_69)** — Added `rds.force_ssl = 1` parameter to RDS parameter group in `modules/rds/main.tf`. Removed CKV2_AWS_69 from Checkov skip list.

1. **ElastiCache automatic backups (CKV_AWS_134)** — Added `snapshot_retention_limit` variable (default 1) to `modules/elasticache/main.tf`. Removed CKV_AWS_134 from Checkov skip list. Note: snapshots require cache.t3.small or larger.

1. **Secrets audit alert** — Added `check_secrets_drift()` to `src/deployer/core/ssm_secrets.py` and integrated into preflight checks. Warns (non-fatal) when SSM has secrets not referenced in deploy.toml. Only works with module-style secrets.

1. **Service `interruptible` flag** — Added `interruptible` field to `ServiceConfig` in deploy_config.py. Deploy script uses `capacityProviderStrategy` (FARGATE_SPOT) for interruptible services. ECS module (`modules/ecs-service/`) supports `use_spot` variable with dynamic capacity provider strategy blocks.

1. **ECR vulnerability notifications** — New `modules/ecr-notifications/` module with EventBridge rule matching ECR scan critical findings, routed to SNS. Conditional in `deployer.tf` on `ecr_scan_sns_topic_arn`.

1. **Cost anomaly detection** — New `modules/cost-budget/` module with AWS Budgets (80% forecasted + 100% actual alerts). Conditional in `deployer.tf` on `budget_monthly_limit > 0`.

1. **Incident start/resolve commands** — Added `incident` subcommand to `bin/ops.py` with start/note/resolve/list actions. Stores timestamped markdown files in `local/incidents/`.
