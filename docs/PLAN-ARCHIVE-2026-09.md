# deployer Plan (Archive)

Completed phases. For active work, see [plan/](plan/).

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

Slices 53e-3 through 53e-5 are still open; see `docs/PLAN.md` for the split
table and what is next.

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

______________________________________________________________________

## Phase 53 working detail (moved from docs/PLAN.md at the fileplan conversion)

The section below is `docs/PLAN.md`'s "Phase 53 status" as it stood on
2026-09-14, when deployer's plan lifecycle moved to fileplan and PLAN.md was
retired. Phase 53 is a claude-meta number and the arc closed 2026-08-25, so
the section is a closed record, not live work — it belongs in this rotated
segment rather than in an item. The prose below is unchanged.

## Phase 53 status — **closed 2026-08-25**

**36 findings** at `187b2f9` (the 53p closeout), from 97 at the start of the
arc. **All sixteen subphases 53a–53p are done, every live finding is an
adjudicated leave-standing, and nothing is escalated or open.** The operator
took the last ten verdicts on 2026-08-25: seven confirmed leave-standings,
four units of work applied by 53p.

**The count rose from 33 on purpose.** 53p-1 cleared one, and 53p-3 deleted
five `utils/logging.py` suppressions that carried a `re-evaluate-by:` tag and
no rationale — surfacing four findings whose adjudications now live in the
register instead of in comments nobody re-measures. One of the five had
suppressed nothing since `a9327ab`.

The sections below are the arc's working detail, left as written at the time.

**53i-3 moved the count up by one, and that was the right outcome.** It added
`emergency/ecs.py:81` — three callers of `get_all_services_state` handling it
three ways, which is correct for a read-only report, an incident recorder and
a destructive command — and removed none, because the two
`inconsistent-error-handling` rows it set out to fix are fixed in a form the
check cannot see: `exit_on` is a context manager and the check looks for
`try`/`except`. Both rows are adjudicated **fixed-but-unseen**. What the unit
moved instead is coverage, 74.94% → **78.73%**, and eleven places where a tool
told an operator something untrue.

**53i-2 moved the count by zero, by design** — 53g's precedent. 53i-2a moved
prose inside comments, 53i-2b wrote two claude-meta guides, and 53i-2c is an
ADR. The count was verified at 32 before and after 53i-2a, diffed as a finding
set rather than as a total.

Outcomes: every subphase now has an adjudication entry in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md), with 53a–53e also
recorded above in this segment. **The 53f/53g register gap is closed**
(2026-08-18): both shipped in the 2026-08-13 unattended run without an
entry, 53h-1 and 53h-2 did not fill it, and it blocked 53i — which cannot
be scoped against a settled/open split that does not exist. §53f and §53g
are backfilled from their commit messages and the run ledger, and §53g's
skip list was re-verified at HEAD first, which found three stale verdicts
and one dead parameter.

**All 33 are attributed and nothing is unowned** — 20 adjudicated
leave-standings, **7 escalated by 53i-1** with measured diffs and awaiting
the operator, 5 adjudicated by 53i-2c's ADR and applied by 53i-3, and **1 new
one escalated by 53i-3b** with its silencing fix drafted and rejected on
cost. The 3 findings the closeout found
owned by no subphase were folded into 53i-1 by operator decision
(2026-08-18); one is cleared, two are among the seven. See the register's
"Remainder — the reconciled adjudication split".

`long-function` **9 → 0**, `dict-as-dataclass` **6 → 0**,
`write-only-attributes` **1 → 0**, `duplicate-except-blocks` and
`boolean-param-explosion` empty as categories, and the
convergence-hotspot list is empty. Coverage floor **53 → 74**.

**Carry into what is left**, the arc's most-repeated lesson: five times
running (53d-1, 53d-2a, 53e-2, 53h-1, 53h-2b) the real defect was
duplication pysmelly could not reach — copies that interleave with other
calls are not runs of consecutive statements, so `duplicate-blocks` never
sees them. Read for repetition before planning anything, and re-measure at
HEAD first; every count here is pinned to a SHA.

**And the lesson 53h-2 added**: three live bugs in one subphase, all the
same shape — a producer and a consumer that had never been run against
each other. pysmelly flags none of that class. What found all three was
writing characterization pins at the outermost boundary of an 8%-covered
module and then composing the readers end to end
(`tests/unit/test_init_deploy_round_trip.py`). **Coverage of the seam
between two components is worth more here than any single check.**

### 53h — `modules/` collect() interface — **done** (2026-08-17)

| Slice  | Scope                                                                  | Status |
| ------ | ---------------------------------------------------------------------- | ------ |
| 53h-1  | what `ModuleContext` carries — dead fields, the ARN, `credential_mode` | done   |
| 53h-2a | one `[secrets]` style, and the module boundary                         | done   |
| 53h-2b | the two signature adjudications                                        | done   |

Detail in [docs/internal/PYSMELLY.md](internal/PYSMELLY.md) §§ 53h-1,
53h-2a, 53h-2b. In short:

- 53h-2a was **not** an adjudication. The `_MODULE_SECTIONS` question was a
  live bug: explicit `[secrets]` plus any module section dropped every
  secret, with preflight and the audit both passing. One style now
  (`names`), one collection route, and a preflight rejection naming the
  migration. Pysmelly 38 → 38, as planned — correctness, not findings.
- 53h-2b cleared `database.validate`'s `feature-envy` (38 → 37, **partly via
  a mechanic** — `check_feature_envy` only walks `ClassDef` bodies) and
  replaced `DeployConfig`'s hardcoded module knowledge with
  `ResourceModule.injected_names`. The `ModuleInputs` bundle was drafted,
  measured at **37 → 43**, and rejected; the probe is unmerged at `235a215`
  on `probe/module-inputs`.

**Follow-on work this opened, not yet scheduled:**

1. **`ssm-secrets.py check` cannot classify anything on its own.** It never
   loads the environment's config.toml, so with the explicit `[secrets]`
   form gone every deploy.toml either declares nothing or gets the "run
   preflight instead" advice block. Already true for both fleet repos
   before 53h-2a. Teaching `cmd_check` to load config.toml would make the
   command useful again.
1. **`get_secrets_from_config`'s `environment` parameter is unread.**
   Removing it ripples through `check_secrets_exist`,
   `check_secrets_drift`, `get_secrets_from_deploy_toml`,
   `preflight.check_ssm_secrets` and `bin/ssm-secrets.py` — and would
   retire the `param-clumps` finding on `ssm_secrets.py:84`. Kept out of a
   correctness slice on purpose; it belongs with 53i.
1. **`_build_images_config` has the same `.get(key, default)` bug shape** as
   the crash fixed at `c31f0f3` — `get_compose_services` sets the key to
   `None`, so the default never fires. There it writes an absent
   `dockerfile` key rather than crashing, and deployer's own default takes
   over, so it was pinned as-is rather than changed.

### 53i — split into three (2026-08-18)

Reading 53i's contents found **two unrelated kinds of work**, so it split
at planning time — the arc's fourth planning-time split, after 53d (twice),
53e (up front) and 53h (twice).

| Unit      | Scope                                                  | Status             |
| --------- | ------------------------------------------------------ | ------------------ |
| **53i-1** | 10 mechanical findings — code motion and adjudication  | **done** (35 → 32) |
| **53i-2** | The raise-vs-return policy — split again into 2a/2b/2c | **done** (32 → 32) |
| **53i-3** | Apply that policy — split at planning into 3a/3b/3c/3d | **done** (32 → 33) |

#### 53i-1 — mechanical residue — **done** (2026-08-18)

Three findings cleared in three commits, each measured against the previous
unit rather than the run total:

| Commit    | Change                                                      | Count   |
| --------- | ----------------------------------------------------------- | ------- |
| `d0330c2` | Pin-first: 5 pins on `--max-config-age`/`--strict`, no code | —       |
| `af23287` | One SSM leaf-name transform, not three                      | 35 → 34 |
| `78c3a6c` | `template.py` asks for the deployer root, not `.parent` ×3  | 34 → 33 |
| `a9327ab` | Lift ci_deploy's staleness gate out of `main()`             | 33 → 32 |

Seven findings were drafted, measured and **escalated to the operator**
rather than recorded as self-authored leave-standings. Two of the drafts
carry evidence that changes the question: `bin/init.py:557`'s inlining
silently widens a `try` to swallow a `FileNotFoundError` from the listener
-priority scan, and `bin/init.py:220`'s **does not clear the finding and
breaks 10 tests**, because inlining `click.prompt` results into a
constructor call reorders the prompts. Diffs and costs are in the
register's §53i-1.

Two findings were not what their category said, both found by reading:
`modules/secrets.py:86` was 1 of 3 copies of one transform (a sixth
producer-and-consumer-never-run-together instance), and
`init/deploy_toml.py:195`'s depth-6 `arrow-code` is one flat five-arm
`elif` chain the check counts as nesting — the fourth "the check's
mechanic, not the code" find in this arc, filed as a pysmelly feature
request in claude-meta `docs/GUIDE-BACKLOG.md`.

Coverage 74.23% → **74.94%** against a floor of 74; the pin-first commit
bought 0.68 points before any production code moved.

#### 53i-2 — the raise-vs-return policy — **done** (2026-08-18)

Split again at planning time into **53i-2a** (reattach six detached suppression
rationales — `4678c1e`, 32 → 32), **53i-2b** (the policy written **fleet-wide**
in claude-meta's `best-practices/PYTHON.md` #19 and `PYSMELLY-REVIEW.md`, by
operator decision, from a 13-repo measurement in which storage-scripts and
claude-meta each carry more of this corpus than deployer), and **53i-2c** (the
ADR below). Two of the corpus items dissolved on re-measurement: the "5 inline
suppressions carrying neither a rationale nor a tag" were **six**, carrying
**both**, detached from their directive by `df01cdb` — a documentation defect
with zero effect on the count, repaired as 53i-2a.

**The ADR is [DECISIONS.md](internal/DECISIONS.md) "2026-08-18: Error Contracts",
and it is 53i-3's checklist** — every call site named, across three layers:
`emergency/`'s eleven sentinel-from-`except` functions plus `run_aws_json`
(producer); the four `inconsistent-error-handling` contracts, classified
one false-positive / one document-only / two real-caller-bugs with the exact
unhandled sites listed (consumer); and the emergency CLI exit codes, where a
**third** swallow (`cmd_revert`) turned up that none of the eleven pins covers
(boundary). **Two items are escalated, not decided** — `emergency/` queries
raising, and exit code `2` for "declined" — and 53i-3 must not apply either
until the operator confirms.

#### 53i-3 — applying the policy — **done** (2026-08-19)

Both escalated items were confirmed, **one with a correction**, and a fourth
layer was added. Split at planning time into four units — the arc's fifth
planning-time split:

| Unit   | Scope                                                  | Count   |
| ------ | ------------------------------------------------------ | ------- |
| **3a** | Pin every consumer 3b/3c/3d change. Tests only.        | 32 → 32 |
| **3b** | The producers raise; consumers catch where they render | 32 → 33 |
| **3c** | The boundary rule and the exit-code ladder             | 33 → 33 |
| **3d** | The `except Exception` misattribution family           | 33 → 33 |

**The ADR proposed an exit code that collided with Click.** `bin/emergency.py`
is a Click CLI, and `click.UsageError.exit_code` is **2**, so the proposed "2
means declined" would have made `emergency rollback --bogus-flag` and a
declined confirmation indistinguishable to any wrapper — the exact defect
PYTHON.md #19 exists to prevent, reintroduced by the fix for it. Corrected to
**3**, verified by hand against `havoc-staging`: `0` / `1` / `2` (Click) / `3`.

**Reading the corpus changed the unit's shape again, for the sixth time in
this arc.** Three things the ADR did not contain:

1. A **twelfth** sentinel-from-`except` instance, `compare_task_definitions`,
   which has no `except` of its own and renders an unreadable task definition
   as "this rollback changes nothing" — immediately before the operator
   confirms a production rollback.
1. The render-boundary pattern the operator chose **already existed** in
   `bin/ops.py:422 _print_rds_status`, pinned since 53d-2a. Its neighbour two
   functions down deleted the whole "Recent Snapshots" section on a
   `ClientError`. Applying an existing pattern, not inventing one.
1. One of the ADR's eleven "real bug" sites was **not a defect**:
   `bin/resolve-config.py:109`'s `cli()` already caught the documented triple.
   Measured by running it.

**53i-3a is why the rest was verifiable.** Every `bin/` call site the later
units changed was unexecuted by any test — `cmd_revert` at 0%, and so were
`cmd_health`, `cmd_maintenance`, `cmd_ecr` and `cmd_incident_start`. The pins
drive the *real* producers against a client that refuses every call, so they
would fail if a producer went back to swallowing. `bin/ops.py` 24% → **54%**,
`bin/emergency.py` 76% → **90%**.

**pysmelly earned its keep mid-unit, twice.** The first version of the
three-command exit-status fix introduced three `failed = []` accumulators and
53i-3d's a fourth; the check caught all four, and the
predicate-plus-comprehension answer that replaced them turned
`deploy_services`'s 45-line loop into a 5-line one.

Full per-unit detail is in the register's §53i-3a through §53i-3d.

Its corpus was
**31 "pinned, not endorsed" markers across 6 test files** (re-measured at
`a9327ab`; the 53i-1 plan entry said 50, but its own per-file list sums to 31
and the list is what verifies)
(`test_emergency_ecs.py` 11, `test_emergency_cli.py` 7,
`test_emergency_rds.py` 5, `test_extensions.py` 3, `test_init_cli.py` 3,
`test_emergency_cli_restore.py` 2), plus the 4
`inconsistent-error-handling` findings, `aws/cli.run_aws_json`, and the 5
inline suppressions carrying neither a rationale nor a `re-evaluate-by:`
tag. Its inputs have accumulated across the arc: Phase 54's pinned
swallow-`ClientError` tests, 53c's unsuppressed `run_aws_json` and those
five suppressions, 53d-1's `capacity-report` exit-code conflation, 53d-2a's
`emergency.py` decline-vs-failure exit codes, and 53d-2b + 53e-1's two bare
`except Exception` handlers that misattribute an internal failure to an
operator-facing cause. Each is pinned by a test naming 53i, so the tests
are the checklist of call sites 53i-3 changes.
