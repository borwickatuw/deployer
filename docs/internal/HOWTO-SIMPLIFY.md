# How to Simplify the Deployer Codebase

Technical debt and simplification candidates for deployer. For methodology (thresholds, extraction rules, quality safeguards), see the DOCS best-practice guide in claude-meta (Practice 1).

## Current Opportunities

### Code (threshold: 300 lines for scripts/modules, 400 for tests)

| File                                     | Lines | Notes                                                      |
| ---------------------------------------- | ----- | ---------------------------------------------------------- |
| `bin/ops.py`                             | 1076  | Largest bin/ script; read-only monitoring commands         |
| `src/deployer/deploy/service.py`         | 1003  | Core service deployment logic                              |
| `bin/emergency.py`                       | 893   | Emergency operations (rollback, scale, snapshot)           |
| `bin/init.py`                            | 769   | Init subcommands (bootstrap, environment, update-services) |
| `src/deployer/aws/ecs.py`                | 507   | AWS ECS operations                                         |
| `src/deployer/core/config.py`            | 485   | Configuration loading                                      |
| `src/deployer/core/audit.py`             | 315   | Audit checks + reporting; decomposed in 53e-2              |
| `src/deployer/init/deploy_toml.py`       | 473   | deploy.toml generation                                     |
| `src/deployer/deploy/task_definition.py` | 469   | ECS task definition builder                                |
| `bin/ecs-run.py`                         | 457   | ECS command execution                                      |
| `src/deployer/deploy/images.py`          | 455   | Docker image build/push                                    |
| `bin/ssm-secrets.py`                     | 451   | SSM Parameter Store management                             |
| `src/deployer/config/deploy_config.py`   | 445   | Deploy config parsing                                      |
| `bin/cognito.py`                         | 441   | Cognito user management                                    |
| `src/deployer/deploy/deployer.py`        | 424   | Deployment orchestrator                                    |
| `src/deployer/emergency/ecs.py`          | 375   | Emergency ECS operations                                   |
| `src/deployer/emergency/rds.py`          | 332   | Emergency RDS operations                                   |
| `src/deployer/utils/cli.py`              | 388   | Shared bin/ helper surface; grew through 53b–53d-2a        |
| `tests/unit/test_init_cli.py`            | 862   | Test file, above 400 threshold                             |
| `src/deployer/init/environment.py`       | 325   | Environment scaffolding                                    |
| `src/deployer/init/template.py`          | 320   | Template substitution                                      |
| `tests/unit/test_modules.py`             | 579   | Test file, above 400 threshold                             |
| `tests/unit/test_init.py`                | 573   | Test file, above 400 threshold                             |
| `tests/unit/test_audit.py`               | 927   | Test file, above 400 threshold; grew in 53e-2              |
| `tests/unit/test_ecs.py`                 | 510   | Test file, above 400 threshold                             |
| `tests/unit/test_core.py`                | 482   | Test file, above 400 threshold                             |
| `tests/unit/test_deploy.py`              | 470   | Test file, above 400 threshold                             |
| `tests/unit/test_config.py`              | 460   | Test file, above 400 threshold                             |
| `tests/unit/test_lambda_db_common.py`    | 443   | Test file, above 400 threshold                             |
| `tests/unit/test_utils_cli.py`           | 465   | Test file, above 400 threshold                             |
| `tests/unit/test_emergency_cli.py`       | 553   | Test file, above 400 threshold                             |
| `tests/unit/test_extensions.py`          | 428   | Test file, above 400 threshold; new in 53e-1               |

Re-measured at the Phase 53e-2 commit. Five files were already over threshold
and missing from this table before the 53d-1 re-measurement
(`emergency/ecs.py`, `emergency/rds.py`, `init/environment.py`,
`init/template.py`, `test_lambda_db_common.py`); they are listed now.

`bin/emergency.py` and `bin/ops.py` grew in 53d-2a (828 → 893, 1056 → 1076),
and `bin/init.py` in 53d-2b (645 → 769): decomposition trades body lines for
helper signatures and docstrings. None is a candidate for a file split yet —
`emergency.py` splits along mutating-ECS vs RDS, which is the shape a later
subphase would own. `tests/unit/test_init_cli.py` (862) is new in 53d-2b and
the largest test file in the repo; it is one subject, so it is listed rather
than queued for a split.

`tests/unit/test_extensions.py` crossed the threshold in 53e-1 (154 → 428)
when the five advice blocks were pinned: exact-text assertions cost more lines
than the `pytest.raises`-only tests they joined. Same call as
`test_init_cli.py` — one subject, listed not split.
`src/deployer/deploy/extensions.py` grew 131 → 184 across the same subphase
(the `print_with_advice` call framing plus three helper signatures and their
docstrings) and stays well under threshold.

`tests/unit/test_audit.py` is now the second-largest test file (555 → 927 in
53e-2), because `run_audit`'s entire verbose output had to be pinned before it
could be decomposed. It covers two subjects — `deployer.config` parsing and
`deployer.core.audit` — so unlike `test_init_cli.py` it **is** a split
candidate, along `test_audit_config.py` / `test_audit.py` lines. Not done here:
53e-2's rule was that its own tests pass unchanged, and moving them is a
separate operator call. `src/deployer/core/audit.py` crossed the 300-line
threshold in the same subphase (293 → 315) — five helper signatures and their
docstrings against 81 lines removed from `run_audit`'s body.

### Terraform (threshold: 200 lines)

| File                                    | Lines | Notes                                 |
| --------------------------------------- | ----- | ------------------------------------- |
| `environments/deployer.tf`              | 681   | Shared environment config (symlinked) |
| `main.tf`                               | 545   | Root module orchestration             |
| `modules/bootstrap/iam-infra-admin.tf`  | 462   | IAM policies for infra admin role     |
| `modules/waf/main.tf`                   | 444   | WAF rules and associations            |
| `variables.tf`                          | 352   | Root module variables                 |
| `modules/shared-infrastructure/main.tf` | 328   | Shared infra module                   |
| `outputs.tf`                            | 299   | Root module outputs                   |
| `modules/db-on-shared-rds/main.tf`      | 295   | Shared RDS database module            |
| `modules/db-users/main.tf`              | 291   | Database user Lambda module           |
| `modules/cloudwatch-alarms/main.tf`     | 278   | CloudWatch alarm definitions          |

### Documentation (threshold: 600 for reference docs, 400 for guides)

| Document                         | Lines | Notes                                       |
| -------------------------------- | ----- | ------------------------------------------- |
| `docs/CONFIG-REFERENCE.md`       | 1073  | Reference doc; large but may be appropriate |
| `docs/internal/SOMEDAY-MAYBE.md` | 607   | Above threshold; review for completed items |
| `docs/internal/DECISIONS.md`     | 556   | Approaching ADR/ migration threshold        |
| `docs/internal/DESIGN.md`        | 410   | Design rationale; at guide threshold        |

## Pysmelly Status

**35 findings** at `722d50b` (from 147 original; 106 at the start of the
2026-08 comprehensive review; 97 before Phase 53a, 91 before 53b, 82 before
53c, 74 before 53d-1, 71 before 53d-2a, 68 before 53d-2b, 60 before 53e-1,
57 before 53e-2, 47 before 53f, 41 before 53h-1, 37 before the 53f/53g
closeout). The live per-category split is in "Remaining findings" below.
Suppressions, measured at the 53e-2 commit across tracked files:
**22 `# pysmelly: ignore` lines and 7 `# noqa: C901`**. 53d-1 removed four
`# noqa: C901`, 53d-2a a fifth (`cmd_rollback`), 53d-2b two more
(`cmd_bootstrap`, `cmd_environment`) and 53e-2 an eighth (`run_audit`);
53e-1 removed none, because the 114-line function it decomposed never carried
one. None added any, and none touched a `# pysmelly: ignore`. (This paragraph
previously claimed "18 suppression lines", which matched neither count at the
53d-1 commit — 22 and 11 — so it is replaced with both measurements rather
than decremented.)

The `# pysmelly: ignore` lines cover Lambda context params, JSON serialization
constraints, query-function None contracts, Click patterns and leaf logging
utilities. Most are tagged `re-evaluate-by: 2026-11 review`, but **five carry
neither a rationale nor a tag**; 53c listed them in
`docs/internal/PYSMELLY.md` and routed them to 53i. The 2026-08 S2 review found
five Phase 42-2 suppressions had never taken effect due to comment placement —
four relocated, one (`generate_bootstrap` unused-default) fixed for real.

**The per-finding work is queued as Phase 53 (subphases 53a–53i) in
claude-meta `docs/PLAN.md`** — one finding-type × one subsystem per
operator-gated session, duplicate-block extraction before long-function
decomposition. 53a (db-\* Lambda twins), 53b (CLI boilerplate), 53c
(`src/deployer` dedup), 53d-1 (the `bin/` deploy.toml-resolution family) and
53d-2a (`emergency.py` + `ops.py`), 53d-2b (`init.py` + the print-run
re-measure), 53e-1 (`extensions.py` + `setup_profiles.py`) and 53e-2
(`core/audit.py`), the 53e-3/4/5 slices, 53f (`dict-as-dataclass`),
53g (parameter plumbing, zero code units) and 53h-1/2a/2b are done, as is the
2026-08-18 53f/53g closeout. **53i is the only open subphase.** Per-finding
dispositions are in `docs/internal/PYSMELLY.md`.

53d was split twice — first when re-measuring at HEAD showed the plan entry
undercounted it (7 `bin/` long-function findings, not 6), and again when 53d-2
proved to be two unrelated halves — and is now **closed**. 53d-2b cleared both
`init.py` decompositions and six of 53b's eight print-runs; the two that
survived had `setup_profiles.py` as their remaining leg, and **53e-1 cleared
both**.

**53e was split up front**, before it was run, on the lesson 53d paid for
twice: re-measured at `805d516` its plan entry was six files and ~16 findings,
three or four sessions of work. The slices are ordered by existing coverage
descending, so the characterization-test idiom lands on small well-covered
files before it reaches the untested heart:

| Slice | Scope                                                      | Status |
| ----- | ---------------------------------------------------------- | ------ |
| 53e-1 | `extensions.py` + `setup_profiles.py`                      | done   |
| 53e-2 | `core/audit.py` — `run_audit`                              | done   |
| 53e-3 | `deployer.py` — `__init__`, `deploy`, 3 × `law-of-demeter` | done   |
| 53e-4 | `images.py` — `build_and_push_images`, `temp-accumulators` | done   |
| 53e-5 | `service.py` — 4 × `long-function` + `arrow-code`          | done   |

**53e closed at `64e3e18`**: `long-function` went to 0, `deploy/service.py`
went 11% → 99% and left the convergence-hotspot list, which is now empty.

### Code improvements made (Phases 42 + 42-2)

- Removed unused `db_name` param from `setup_schema_privileges()`
- Extracted `format_iso()` datetime helper (9 hasattr patterns → isinstance)
- Converted loop-and-append accumulator to comprehension
- Fixed silent failure in `_handle_restore_error()` (unknown ClientErrors now re-raise)
- Removed vestigial `verbose` param from `audit()` command
- Fixed `audit_images()` type contract (dict[str, Any] → dict[str, ImageConfig])
- Converted `_format_service()` return type to `ServiceInfo` dataclass, removed vestigial `arn` field
- Flattened arrow-code in 6 functions: `detect_framework()`, `get_next_listener_priority()`, `cmd_start()` (extracted `_ensure_rds_available()`), `list_repositories_for_environment()`, `cmd_put()` (extracted `_get_secret_value_interactively()`), `check_infrastructure_status()`

### Remaining findings (35)

Live counts, re-measured at the 53f/53g closeout commit `722d50b` with
`uvx pysmelly . --more-please` (`make pysmelly` truncates to the top ten and
under-reports `inconsistent-error-handling`).

**The Open column is back.** It was dropped at 38 because 53f and 53g had
shipped with no adjudication entry in [PYSMELLY.md](PYSMELLY.md), so which
findings were settled was only partly recorded. **That gap was filled on
2026-08-18** — every live finding is now attributed to an adjudicated
leave-standing or an open owner, and the derivation is in that file under
"Remainder — the reconciled adjudication split". Open = live − settled.

| Category                     | Live | Settled | Open | Notes                                                                             |
| ---------------------------- | ---- | ------- | ---- | --------------------------------------------------------------------------------- |
| pass-through-params          | 13   | 13      | 0    | All re-verified 2026-08-18; 53g's skip list plus 53b/53c/53d-1 leave-standings    |
| param-clumps                 | 5    | 5       | 0    | 53a, 53d-2a and 53g; `modules/` cleared by 53h-1, `ssm_secrets` by the closeout   |
| inconsistent-error-handling  | 4    | 0       | 4    | Caller-contract policy needed (53i)                                               |
| foo-equals-foo               | 3    | 0       | 3    | 53i; `init.py`'s two measured in 53d-2b, `deploy_config.py:379` is the third      |
| single-call-site             | 3    | 0       | 3    | Named helpers that document intent (53i)                                          |
| arrow-code                   | 2    | 0       | 2    | **Owned by no subphase** — `ci_deploy.py:181`, `init/deploy_toml.py:199`          |
| law-of-demeter               | 2    | 1       | 1    | 53e-3 adjudicated `deployer.py:219`; `template.py:26` to 53i                      |
| duplicate-blocks             | 1    | 1       | 0    | The db-\* Lambda pair, adjudicated in 53a                                         |
| return-none-instead-of-raise | 1    | 0       | 1    | `aws/cli.run_aws_json` — left unsuppressed for 53i to decide                      |
| temp-accumulators            | 1    | 0       | 1    | **Owned by no subphase** — `images.py:301`, relocated into `_cache_tag` by 53e-4b |
| **Total**                    | 35   | **20**  | 15   | 53i owns 12; **3 need a home**                                                    |

**Three findings are owned by no subphase** — surfaced by the 2026-08-18
reconciliation, not by a re-measure. They appear in no skip list and in no
subphase's scope, and need either 53i or an explicit leave-standing. That is a
scoping input for 53i, which this closeout unblocks.

Categories now empty: `long-function` (9 → 0 across 53d–53e),
`dict-as-dataclass` (6 → 0 in 53f), `write-only-attributes` (1 → 0 in 53h-1),
`duplicate-except-blocks` and `boolean-param-explosion`.

`long-function` dropped 16 → 12 → 11 → 9 → 8: 53d-1 cleared four `bin/`
orchestrators, 53d-2a cleared `emergency.py cmd_rollback`, 53d-2b cleared
`init.py`'s `cmd_bootstrap` and `cmd_environment`, and 53e-1 cleared
`extensions.py create_database_extensions`. All 8 remaining sit in
`src/deployer/`, four of them in `deploy/service.py`; `bin/` has none.
`arrow-code` dropped 5 → 3 in 53d-2a: `ops.py cmd_status` cleared when a
redundant `"T" in x` guard — its depth-5 node — was deleted, and
`emergency.py cmd_scale` cleared by extracting its `--all` branch.
`pass-through-params` held at 14 across 53d-2a and 53e-1, finding for finding:
extracting ten helpers between them minted none.

`duplicate-except-blocks` (6 at the 2026-08-07 measurement, 5 at `a8800cd`) is
empty for the first time — cleared by 53a and 53b. `duplicate-blocks` fell
9 → 3 in 53d-2b, when `_numbered_steps` collapsed six of the eight `init.py`
print-runs at once, then **3 → 1** in 53e-1, when adopting `print_with_advice`
in `extensions.py` and `advice_block` in `setup_profiles.py` turned the last two
runs of `print()` statements into arguments. The category has **no open items**:
the single remaining finding is 53a's adjudicated Lambda pair.
`boolean-param-explosion` is empty, cleared by 53c's `DeployOptions`.

*Last updated: 2026-08-18 (Phase 53f/53g closeout)*
