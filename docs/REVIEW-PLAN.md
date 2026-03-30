# Code Review and Reduction Plan

**Status: COMPLETE.** All 5 phases finished (2026-03-19).

The custom `bin/lint-review.py` used during this plan has been replaced
by [pysmelly](https://github.com/borwickatuw/pysmelly), a standalone
tool with the same checks. Run: `uvx pysmelly`

## Background

A previous session (2026-03-18) reduced src/+bin/ from 18,289 to 18,005
lines (-284) and built a custom lint script with 12 checks. This plan
worked through the remaining findings plus manual review of the largest
files.

## Phase 1: High-signal automated findings

Fix findings from the "act on these" tier. These are mechanical —
each finding is either dead code or an unnecessary Optional. Low risk.

### 1a: Dead code removal (6 findings)

Delete functions with zero callers:

- `src/deployer/aws/ecs.py`: `get_service_network_config()`,
  `get_service_task_definition()`, `get_task_logs_location()` — all
  superseded by `get_service_info()` and `get_logs_location_from_containers()`
- `src/deployer/emergency/ecs.py`: `scale_all_services()`
- `src/deployer/emergency/rds.py`: `wait_for_rds_available()`
- `src/deployer/init/framework.py`: `detect_framework_from_dockerfile()`

Also remove any imports/exports of these functions.

**Verify**: grep each name before deleting to confirm zero callers.
**Test**: `make test` after each file.

### 1b: Unused Optional defaults (40 findings)

For each function where every caller always passes a value, make the
parameter required (remove `= None`) and delete any fallback branch
inside the function that handles the None case.

Group by file for efficient editing:

- `src/deployer/deploy/service.py` (5 params across 3 functions)
- `src/deployer/deploy/task_definition.py` (6 params across 3 functions)
- `src/deployer/core/ssm_secrets.py` (3 params across 3 functions)
- `src/deployer/emergency/` (5 params across 4 functions)
- `src/deployer/aws/ecs.py` (4 params across 3 functions) — NOTE: the
  `ecs_client` params are Optional by design (auto-create when None).
  These are intentional and should be skipped.
- Remaining files (17 params across ~15 functions)

**Important**: Some Optional params are intentionally Optional even
though current callers always pass them (e.g., `ecs_client` params
that auto-create a client). Review each before changing — the linter
flags them but human judgment is needed.

**Test**: `make test` after each file group.

### 1c: Lazy imports (4 fixable findings)

Move to module level:
- `bin/ecs-run.py`: `import boto3`
- `bin/ops.py`: `import re`
- `bin/resolve-config.py`: `import boto3`, `from botocore.exceptions`

Skip `src/deployer/utils/cli.py` (2 findings) — genuine circular dep.

### 1d: Suspicious fallbacks (2 findings)

Review and fix:
- `src/deployer/init/framework.py`: `DEFAULT_PORTS.get()` with fallback
- `src/deployer/utils/aws_profile.py`: `PROFILE_CONFIG_KEYS.get()` with fallback

## Phase 2: service.py deep review

`src/deployer/deploy/service.py` is the largest file (1,141 lines)
and has the most lint findings (5 unused-defaults, 3 too-many-params
at 12-13 params each, duplicate blocks, foo=foo patterns). It's also
the riskiest file (production deployments), so changes must be careful.

### 2a: Introduce DeploymentContext dataclass

Many service.py functions share the same parameters: `cluster_name`,
`ecs_client`, `app_name`, `environment`, `region`, `infra_config`,
`env_config`, `dry_run`. A `DeploymentContext` dataclass would:

- Reduce `register_task_definition` from 13 params
- Reduce `deploy_services` from 12 params
- Reduce `start_migrations` / `run_migrations` from 13 params
- Eliminate foo=foo at call sites

**Approach**: Read each function, identify the common params, define
the dataclass, update function signatures one at a time. Run tests
after each function change.

### 2b: Manual review per protocol

Apply the full PYTHON-REVIEW.md checklist to service.py. The
`duplicate-blocks` check flagged several blocks — review each.

## Phase 3: Review remaining large files

Apply lint findings + manual PYTHON-REVIEW.md checklist to each file.
One commit per file.

Priority order (by lint finding density + file size):

1. `src/deployer/deploy/task_definition.py` (527 lines, 6 unused-defaults,
   2 too-many-params, 1 foo=foo)
2. `bin/capacity-report.py` (700 lines, 2 unused-defaults,
   3 too-many-params, 1 foo=foo, temp accumulators)
3. `src/deployer/deploy/images.py` (460 lines, 1 unused-default,
   1 too-many-params)
4. `bin/ops.py` (801 lines, duplicate blocks in incident commands)
5. `bin/cognito.py` (503 lines, duplicate blocks across cmd_ functions)
6. `src/deployer/emergency/` directory (multiple files, scattered findings)

For each file:
1. Run `uv run python bin/lint-review.py` filtered to that file's findings
2. Walk through each function per the manual checklist
3. Fix findings, run tests after each change
4. Commit

## Phase 4: Cross-cutting patterns

After per-file review, address patterns that span multiple files:

### 4a: Duplicate blocks across bin/ scripts

The `duplicate-blocks` check found shared patterns across emergency.py,
ops.py, environment.py, cognito.py — mostly config loading boilerplate.
Evaluate whether `require_environment()` from `utils/cli.py` can replace
more of these.

### 4b: Duplicate module validation (cache/database/storage)

`cache.py:validate()`, `database.py:validate()`, `storage.py:validate()`
have 7 duplicate statements. Consider extracting a base validation
method in `modules/base.py`.

## Phase 5: Final measurement and linter update

1. Run `wc -l` and compare to baseline (18,005 lines)
2. Run `uv run python bin/lint-review.py` and compare to baseline (205 findings)
3. Update `docs/PYTHON-REVIEW.md` files-reviewed table
4. Note any new check ideas discovered during review

## Results

All phases complete. Final measurements (2026-03-19):

| Metric | Baseline (pre-Phase 1) | Post-Phase 1 | Final (post-Phase 4) | Total delta |
|--------|------------------------|--------------|----------------------|-------------|
| Deployer lines (\*) | 17,743 | 17,594 | 17,495 | -248 |
| Lint findings | 205 | 135 | 113 | -92 (45%) |
| Tests | 376 | 376 | 376 | unchanged |

(\*) src/+bin/ .py files excluding `bin/lint-review.py` (dev tooling, 933 lines).

### Findings breakdown (113 remaining)

| Check | Count | Notes |
|-------|-------|-------|
| single-call-site | 68 | Informational; most are dispatch functions or clear abstractions |
| duplicate-blocks | 16 | Remaining are cross-file or semantic differences |
| foo-equals-foo | 11 | Natural function-call/constructor patterns |
| too-many-params | 10 | bin/ scripts (intentionally skipped) + abstraction boundaries |
| unused-defaults | 3 | Intentionally Optional (tests call without, or public API) |
| temp-accumulators | 3 | Clear, readable patterns not worth restructuring |
| lazy-imports | 2 | Genuine circular dependency in utils/cli.py |

### Phase-by-phase actuals

| Phase | Lines saved | Findings eliminated | Risk |
|-------|-------------|---------------------|------|
| 1: Automated findings | ~170 | 70 | Low |
| 2: service.py (DeploymentContext) | ~60 | 10 | Medium |
| 3: Per-file reviews | ~40 | 8 | Low-Medium |
| 4: Cross-cutting | ~20 | 9 | Low |
| **Total** | **~290** | **92** | |

### New check ideas discovered during review

- **raise-instead-of-return-None**: Functions that return `None` on error
  where all callers check `if not result: return 1` — these should raise
  exceptions instead (applied manually to cognito.py `resolve_environment`
  and emergency.py config loading).
- **duplicate-boilerplate-prefix**: Detect identical N-line prefixes across
  functions in the same file (config loading, validation + error handling).
  Current `duplicate-blocks` catches this but doesn't distinguish
  "extract a helper" vs "natural similarity."

## Phase 6: Click migration + structural cleanup (2026-03-19)

Separate session focused on reducing boilerplate and unnecessary abstraction.

### 6a: Dacite migration completion

Renamed underscore-prefixed fields on `ImageConfig` (`_target`, `_build_args`)
and `ServiceConfig` (`_environment`) to public names. Deleted manual `from_dict()`
classmethods, replaced with `dacite.from_dict()` at the call site.

### 6b: Click migration (12 scripts)

Migrated all 11 bin/ scripts and `ci_deploy.py` from argparse to Click.
Eliminated `dispatch_subcommand()` utility and `add_common_deploy_arguments()`
helper, replaced with Click groups/decorators. Added `click>=8.1` dependency.

Argument order changed for ops.py and emergency.py: `<cmd> <env>` (was `<env> <cmd>`).
deploy.py became `deploy.py deploy <env>` (was `deploy.py <env>`).

### 6c: Dead code removal

Deleted `rollback_service()` (36 lines, zero callers) and
`get_unhealthy_targets()` (11 lines, zero callers) from `emergency/`.

### 6d: `__init__.py` re-export cleanup

Gutted `core/__init__.py` (91 → 1 line) and `deploy/__init__.py` (78 → 1 line)
by converting all callers to use submodule imports directly. `utils/__init__.py`
kept as-is (22 callers justify the re-export layer).

### 6e: Inline single-use emergency/ modules

Moved 5 modules used by only one bin/ script into that script:
- `alb.py`, `logs.py`, `maintenance.py`, `ecr.py` → `bin/ops.py`
- `logging.py` (EmergencyLogger) → `bin/emergency.py`

Deleted the 5 source files and gutted `emergency/__init__.py` (100 → 1 line).
Remaining shared modules (`ecs.py`, `rds.py`, `checkpoint.py`) stay as library code.

### 6f: Slim capacity-report to OOM detection only

Removed the utilization analysis feature (CloudWatch Container Insights metrics,
classification engine, right-sizing recommendations, tfvars comparison, savings
estimates, JSON report format). These are unreliable for services not running
24x7 and duplicated by AWS Compute Optimizer.

Kept the OOM kill detection (ECS stopped tasks + CloudWatch Logs search) which
answers the concrete question "did my service crash since the last deploy?"

Deleted:
- `src/deployer/core/capacity.py` (284 lines) — classification, recommendations
- `src/deployer/config/tfvars.py` (85 lines) — only used by capacity report
- `tests/unit/test_capacity.py` (322 lines)
- `get_container_insights_metrics()` from `aws/cloudwatch.py`
- `get_task_definition_resources()` from `aws/ecs.py`
- Related tests and fixture file

`capacity-report.py` rewritten from 655 to 186 lines.

### Phase 6 results

| Metric | Pre-Phase 6 | Post-Phase 6 | Delta |
|--------|-------------|--------------|-------|
| Deployer lines (src/+bin/) | 17,495 | 15,142 | **-2,353** |
| Tests | 376 | 329 | -47 (capacity/tfvars tests removed) |
| emergency/ files | 9 | 4 | -5 deleted |

| Step | Lines saved |
|------|-------------|
| 6a: Dacite migration | -63 |
| 6b: Click migration | -890 |
| 6c: Dead code | -50 |
| 6d: `__init__.py` cleanup | -116 |
| 6e: Inline emergency/ | -376 |
| 6f: Slim capacity-report | -863 |
| **Total** | **-2,353** (rounding: 5 from miscellaneous) |

### Cumulative totals

| Metric | Original (2026-03-18) | Current (post-Phase 6) | Total delta |
|--------|----------------------|------------------------|-------------|
| Deployer lines (src/+bin/) | 18,289 | 15,142 | **-3,147 (17.2%)** |
