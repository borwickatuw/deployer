# deployer Plan (Archive)

Completed phases. For active work, see [PLAN.md](PLAN.md).

Every phase here was queued from claude-meta `docs/PLAN.md`; deployer has
no repo-local phases yet. Per-finding dispositions for the whole Phase 53
arc live in [docs/internal/PYSMELLY.md](internal/PYSMELLY.md), which is
the register — these entries are the outcome summaries.

## Phase 53: pysmelly subphase backlog

Running total across the arc: **97 → 91 → 82 → 74 → 71 → 68 → 60 → 57 →
56**.

### 53a — db-\* Lambda twin consolidation

**Shipped 2026-08-12** in `bcd2218` — the two `index.py` twins now share
`modules/lambda-shared/db_common.py`, vendored into each bundle at apply
time; pysmelly 97 → 91. Two findings are left standing in the register
**pending operator confirmation**.

### 53b — CLI boilerplate dedup

**Shipped 2026-08-13** — the shared `bin/` helpers now live in
`src/deployer/utils/cli.py` and the `deploy.py` / `ci-deploy` clone in
`src/deployer/deploy/pipeline.py`; pysmelly 91 → 82,
`duplicate-except-blocks` empty for the first time, coverage floor
32 → 36. Eight `duplicate-blocks` findings (the `bin/init.py` print-runs)
were left standing **pending operator confirmation**, with the drafted fix
and the reason it was kept out, to be re-measured after 53d — which
53d-2b did.

### 53c — `src/deployer` dedup

**Shipped 2026-08-13** — an error-with-advice vocabulary in
`src/deployer/utils/logging.py` (`advice_block` / `print_with_advice`)
adopted at six sites, a shared `aws` CLI surface in
`src/deployer/aws/cli.py` that collapsed five twins pysmelly could not
see, and `EnvironmentTarget` / `DeployOptions` in
`src/deployer/deploy/context.py` for the two findings 53b routed here;
pysmelly 82 → 74, `duplicate-blocks` left with no open items, coverage
floor 36 → 37. The `aws/` package got its first tests ever (cognito
12% → 100%, rds 18% → 100%). Two findings were minted and left standing
**pending operator confirmation**; the register also lists five inline
suppressions carrying neither a rationale nor a `re-evaluate-by:` tag,
routed to 53i.

### 53d — `bin/` long-function decomposition

**53d was split twice, both times mid-arc**: first because re-measuring at
HEAD showed claude-meta's plan entry undercounted it (7 `bin/`
`long-function` findings, not 6), then because 53d-2 proved to be two
unrelated halves. That is the lesson 53e paid up front by splitting before
it ran.

#### 53d-1 — the `bin/` deploy.toml-resolution family

**Shipped 2026-08-13** — `resolve_deploy_toml_or_exit` in
`src/deployer/utils/cli.py` replaced three hand-rolled copies of the same
resolution in `deploy.py` / `ssm-secrets.py` / `ecs-run.py`, which was
most of three of the four decompositions; `capacity-report.py` split into
`_deployment_cutoff` plus two scan helpers. All four `long-function`
targets cleared and their four `# noqa: C901` lines removed; pysmelly
74 → 71, coverage floor 37 → 44. `ssm-secrets.py`, `ecs-run.py` and
`capacity-report.py` got their first tests ever (0% → 73/61/87%). One
`pass-through-params` was minted and left standing **pending operator
confirmation**; `capacity-report.check_environment`'s exit-code
conflation is recorded and routed to 53i.

#### 53d-2a — `emergency.py` + `ops.py`

**Shipped 2026-08-13** — the first commit pinned `cmd_rollback` /
`cmd_scale` / `cmd_force_deploy` / `cmd_status` at their current
behaviour, and every later commit had to leave those tests passing
unchanged. Six `if x and "T" in x:` guards around `format_timestamp`
turned out to be provable no-ops, and deleting them cleared
`cmd_status`'s arrow-code on its own; `select_index` in
`src/deployer/utils/cli.py` absorbed `cmd_rollback`'s two numbered-pick
twins; `_require_service` and `_checkpoint_and_log` unified twins pysmelly
could not see. Three of four targets cleared, nothing minted; pysmelly
71 → 68, coverage floor 44 → 49. `bin/emergency.py` got its first tests
ever (0% → 57%) and dropped off the convergence-hotspot list. The
`(environment, service, yes)` param-clump is left standing **pending
operator confirmation**, with the drafted `EmergencyTarget` dataclass and
the reason it reads worse than the three parameters.

#### 53d-2b — `bin/init.py` + the print-run re-measure

**Shipped 2026-08-13**, closing 53d. The first commit pinned
`cmd_bootstrap` / `cmd_bootstrap_migrate` / `cmd_deploy_toml` /
`cmd_environment` / `_print_next_steps`, and every later commit left those
tests passing unchanged. Reading the cross-file legs settled the design
call 53b could not: the eight print-runs were two families sharing an AST
shape, and four of the five next-steps runs live in `bin/init.py` — so
`_numbered_steps` is module-local, and the `extensions.py` legs stayed
with 53e's `print_with_advice` adoption. The helper owning the counter let
`_print_next_steps` drop the dynamic `step` variable 53b cited as the
evidence against generalizing. Both `long-function` targets cleared and
both `# noqa: C901` lines came off; six of the eight print-runs cleared
(`duplicate-blocks` 9 → 3). pysmelly 68 → 60, the arc's largest
single-subphase drop; coverage floor 49 → 53. `bin/init.py` got its first
tests ever (0% → 92%) and dropped off the convergence-hotspot list,
leaving only `deploy/service.py`; the repo has no 0%-coverage file left.

It also fixed a latent bug: an unset `DEPLOYER_ENVIRONMENTS_DIR` was
reported as a missing bootstrap directory, sending the operator to a
command that fails the same way. The two surviving print-runs were
recorded as belonging to the `setup_profiles.py` neighbourhood (**53e**),
not re-deferred as generic leave-standings — and 53e-1 cleared both.

### 53e — `deploy/` pipeline decomposition (slices 1–2 of 5)

Slices 53e-3 through 53e-5 are still open; see [PLAN.md](PLAN.md) for the
split table and what is next.

#### 53e-1 — `extensions.py` + `setup_profiles.py`

**Shipped 2026-08-13** — closing a thread open since 53b, which found five
`log_error → print advice → raise RuntimeError` blocks in
`create_database_extensions` and called them the file's real duplication;
53c landed the vocabulary and 53d-2b routed the two surviving
`duplicate-blocks` here, but nobody had done the adoption. The first
commit pinned all five advice blocks and `cmd_setup_profiles` end to end,
and every later commit left those tests passing unchanged. All three
targets cleared: pysmelly 60 → 57 (`long-function` 9 → 8,
`duplicate-blocks` 3 → 1), and `extensions.py` now has no findings of any
category. Coverage: `extensions.py` 94% → 100%, `setup_profiles.py`
39% → 100%; total 53.51% → 53.91%, floor stays 53. Nothing minted.

The register also records the measurement that contradicted the plan:
adopting `print_with_advice` **grew** the function 114L → 118L rather than
shrinking it to ~102L, because black costs more in call framing than the
removed scaffolding saves. The decomposition into three helpers is what
took it to 21L. Pinned but not endorsed, routed to **53i**:
`extensions.py`'s bare `except Exception` reports any non-`ClientError`
failure as a credentials or network problem.

#### 53e-2 — `core/audit.py`

**Shipped 2026-08-13** — `run_audit` cleared, pysmelly 57 → 56,
`long-function` 8 → 7, and its `# noqa: C901` came off (7 remain
repo-wide). Nothing minted.

The length turned out to be **triplication pysmelly could not see**: 33 of
the 114 lines were the same block written three times (heading, run a
check, warn each issue or print an all-clear, accumulate), but the copies
interleave with their own audit calls, so they are not the runs of
consecutive statements `duplicate-blocks` keys on. Third time in this arc
that a `long-function` finding was really a duplication finding out of the
checker's reach — after 53d-1's three-way deploy.toml-resolution twin and
53d-2a's six no-op `format_timestamp` guards. The three blocks became a
three-entry table and a four-line loop; `run_audit` went 114L → 33L. Two
things the length was hiding also went: `total_issues`, which tracked
exactly what `len(all_issues)` already knew, and three single-use aliases
for `deploy.services` / `.images` / `.get_all_env_var_names()`.

**Latent bug fixed**, same family as 53d-2b's dead
`except RuntimeError: pass`: the Audit Configuration section is gated on
any of the four `[audit]` keys being set — including `ignore_images` — but
only three had a line inside it, so a deploy.toml configuring only
`ignore_images` printed an empty heading and nothing under it. Display
only; `audit_images` already honoured the setting. Fixed in its own commit
so the decomposition stayed behaviour-preserving.

One deliberate change, named: the three checks now all run before any
section prints. Output is byte-identical (the audit functions are pure
list-builders over parsed data), but a check that raised would no longer
show its heading first.

`core/audit.py` **66% → 100%** — its whole gap was `run_audit`'s reporting
half, since all four pre-existing tests passed `verbose=False`. Coverage
floor **53 → 54** (54.62% measured), 872 → 891 tests. Also deleted an
`assert issue_count >= 0` that held for every value the function can
return but the `-1` case, so it pinned nothing.

## Phase 54: emergency-subsystem test coverage

**Shipped 2026-08-12** in `5f6b287` — checkpoint/ecs/rds 0% → 100%,
coverage floor 25 → 32; record in claude-meta `docs/PLAN-ARCHIVE.md`. Its
tests pin today's swallow-`ClientError` contracts on purpose, for 53i to
decide.

## Repo-local work (pre-Wave-0)

The entries below moved here from `docs/internal/PLAN-ARCHIVE.md` when
deployer consolidated onto the fleet's one-PLAN-one-archive shape (every
other repo keeps the pair at `docs/`, and deployer was carrying two).
They predate the cross-repo phase numbering above and were tracked through
`docs/internal/SOMEDAY-MAYBE.md`.

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
