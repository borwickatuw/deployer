# How to Simplify the Deployer Codebase

Technical debt and simplification candidates for deployer. For methodology (thresholds, extraction rules, quality safeguards), see the DOCS best-practice guide in claude-meta (Practice 1).

## Current Opportunities

### Code (threshold: 300 lines for scripts/modules, 400 for tests)

| File                                     | Lines | Notes                                                      |
| ---------------------------------------- | ----- | ---------------------------------------------------------- |
| `bin/ops.py`                             | 1058  | Largest bin/ script; read-only monitoring commands         |
| `src/deployer/deploy/service.py`         | 1003  | Core service deployment logic                              |
| `bin/emergency.py`                       | 867   | Emergency operations (rollback, scale, snapshot)           |
| `bin/init.py`                            | 643   | Init subcommands (bootstrap, environment, update-services) |
| `src/deployer/aws/ecs.py`                | 507   | AWS ECS operations                                         |
| `src/deployer/core/config.py`            | 485   | Configuration loading                                      |
| `src/deployer/init/deploy_toml.py`       | 473   | deploy.toml generation                                     |
| `src/deployer/deploy/task_definition.py` | 469   | ECS task definition builder                                |
| `bin/ecs-run.py`                         | 467   | ECS command execution                                      |
| `bin/ssm-secrets.py`                     | 460   | SSM Parameter Store management                             |
| `src/deployer/deploy/images.py`          | 455   | Docker image build/push                                    |
| `src/deployer/config/deploy_config.py`   | 445   | Deploy config parsing                                      |
| `bin/cognito.py`                         | 441   | Cognito user management                                    |
| `src/deployer/deploy/deployer.py`        | 424   | Deployment orchestrator                                    |
| `tests/unit/test_modules.py`             | 579   | Test file, above 400 threshold                             |
| `tests/unit/test_init.py`                | 573   | Test file, above 400 threshold                             |
| `tests/unit/test_audit.py`               | 555   | Test file, above 400 threshold                             |
| `tests/unit/test_ecs.py`                 | 510   | Test file, above 400 threshold                             |
| `tests/unit/test_core.py`                | 482   | Test file, above 400 threshold                             |
| `tests/unit/test_config.py`              | 460   | Test file, above 400 threshold                             |

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

74 findings (from 147 original; 106 at the start of the 2026-08 comprehensive
review; 97 before Phase 53a, 91 before 53b, 82 before 53c). 22 suppression lines
stand — unchanged by 53c, which added none — covering Lambda context params,
JSON serialization constraints, query-function None contracts, Click patterns
and leaf logging utilities. Most are tagged `re-evaluate-by: 2026-11 review`,
but **five carry neither a rationale nor a tag**; 53c listed them in
`docs/internal/PYSMELLY.md` and routed them to 53i. The 2026-08 S2 review found
five Phase 42-2 suppressions had never taken effect due to comment placement —
four relocated, one (`generate_bootstrap` unused-default) fixed for real.

**The per-finding work is queued as Phase 53 (subphases 53a–53i) in
claude-meta `docs/PLAN.md`** — one finding-type × one subsystem per
operator-gated session, duplicate-block extraction before long-function
decomposition. 53a (db-\* Lambda twins), 53b (CLI boilerplate) and 53c
(`src/deployer` dedup) are done; per-finding dispositions are in
`docs/internal/PYSMELLY.md`.

### Code improvements made (Phases 42 + 42-2)

- Removed unused `db_name` param from `setup_schema_privileges()`
- Extracted `format_iso()` datetime helper (9 hasattr patterns → isinstance)
- Converted loop-and-append accumulator to comprehension
- Fixed silent failure in `_handle_restore_error()` (unknown ClientErrors now re-raise)
- Removed vestigial `verbose` param from `audit()` command
- Fixed `audit_images()` type contract (dict[str, Any] → dict[str, ImageConfig])
- Converted `_format_service()` return type to `ServiceInfo` dataclass, removed vestigial `arn` field
- Flattened arrow-code in 6 functions: `detect_framework()`, `get_next_listener_priority()`, `cmd_start()` (extracted `_ensure_rds_available()`), `list_repositories_for_environment()`, `cmd_put()` (extracted `_get_secret_value_interactively()`), `check_infrastructure_status()`

### Remaining findings (74)

Live counts, re-measured after Phase 53c. 15 of the 74 are adjudicated
leave-standings (53a 2, 53b 11, 53c 2) rather than open work, so **59 are open**.

| Category                     | Count | Open | Notes                                                                 |
| ---------------------------- | ----- | ---- | --------------------------------------------------------------------- |
| long-function                | 16    | 16   | Orchestration functions (100–166 lines) (53d–e)                       |
| pass-through-params          | 13    | 9    | ssm_secrets/preflight/aws plumbing; 4 adjudicated (53b ×3, 53c) (53g) |
| duplicate-blocks             | 9     | 0    | 8 init.py print-runs + 1 Lambda, all adjudicated                      |
| param-clumps                 | 7     | 6    | Context-object candidates; 1 adjudicated (53a) (53g–h)                |
| arrow-code                   | 5     | 5    | Depth-5/6 nesting (53d–e)                                             |
| dict-as-dataclass            | 5     | 5    | emergency/rds, ecs, cognito returns (53f)                             |
| inconsistent-error-handling  | 4     | 4    | Caller-contract policy needed (53i)                                   |
| law-of-demeter               | 4     | 4    | Chain depth 4 (53e, 53i)                                              |
| foo-equals-foo               | 3     | 3    | Single-use locals to inline (53i)                                     |
| single-call-site             | 3     | 3    | Named helpers that document intent (53i)                              |
| feature-envy                 | 2     | 2    | DatabaseModule methods (53h)                                          |
| return-none-instead-of-raise | 1     | 0    | aws/cli.run_aws_json — left unsuppressed for 53i to decide            |
| write-only-attributes        | 1     | 1    | ModuleContext.domain_name (53h)                                       |
| temp-accumulators            | 1     | 1    | images.py hash_modifiers (53e)                                        |

`duplicate-except-blocks` (6 at the 2026-08-07 measurement, 5 at `a8800cd`) is
empty for the first time — cleared by 53a and 53b. `duplicate-blocks` is not
empty but has **no open items**: 53c cleared its last five, and every remaining
finding is an adjudicated leave-standing. `boolean-param-explosion` is empty,
cleared by 53c's `DeployOptions`.

*Last updated: 2026-08-13 (Phase 53c)*
