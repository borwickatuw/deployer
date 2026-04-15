# How to Simplify the Deployer Codebase

Technical debt and simplification candidates for deployer. For methodology (thresholds, extraction rules, quality safeguards), see the DOCS best-practice guide in claude-meta (Practice 1).

## Current Opportunities

### Code (threshold: 300 lines for scripts/modules, 400 for tests)

| File                                     | Lines | Notes                                                      |
| ---------------------------------------- | ----- | ---------------------------------------------------------- |
| `bin/ops.py`                             | 1066  | Largest bin/ script; read-only monitoring commands         |
| `src/deployer/deploy/service.py`         | 990   | Core service deployment logic                              |
| `bin/emergency.py`                       | 864   | Emergency operations (rollback, scale, snapshot)           |
| `bin/init.py`                            | 605   | Init subcommands (bootstrap, environment, update-services) |
| `tests/unit/test_modules.py`             | 579   | Test file, above 400 threshold                             |
| `tests/unit/test_init.py`                | 575   | Test file, above 400 threshold                             |
| `tests/unit/test_audit.py`               | 554   | Test file, above 400 threshold                             |
| `tests/unit/test_ecs.py`                 | 510   | Test file, above 400 threshold                             |
| `src/deployer/aws/ecs.py`                | 498   | AWS ECS operations                                         |
| `src/deployer/core/config.py`            | 484   | Configuration loading                                      |
| `tests/unit/test_core.py`                | 483   | Test file, above 400 threshold                             |
| `src/deployer/init/deploy_toml.py`       | 475   | deploy.toml generation                                     |
| `src/deployer/deploy/task_definition.py` | 470   | ECS task definition builder                                |
| `src/deployer/deploy/images.py`          | 460   | Docker image build/push                                    |
| `bin/cognito.py`                         | 452   | Cognito user management                                    |
| `bin/ssm-secrets.py`                     | 441   | SSM Parameter Store management                             |
| `src/deployer/config/deploy_config.py`   | 440   | Deploy config parsing                                      |
| `src/deployer/deploy/deployer.py`        | 416   | Deployment orchestrator                                    |
| `bin/ecs-run.py`                         | 411   | ECS command execution                                      |
| `tests/unit/test_config.py`              | 403   | Test file, at 400 threshold                                |

### Terraform (threshold: 200 lines)

| File                                    | Lines | Notes                                 |
| --------------------------------------- | ----- | ------------------------------------- |
| `environments/deployer.tf`              | 678   | Shared environment config (symlinked) |
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
| `docs/internal/SOMEDAY-MAYBE.md` | 600   | At threshold; review for completed items    |
| `docs/internal/DECISIONS.md`     | 557   | Approaching ADR/ migration threshold        |
| `docs/internal/DESIGN.md`        | 410   | Design rationale; at guide threshold        |

## Pysmelly Status

110 findings (from 147 original). 19 genuine false-positive suppressions remain (Lambda context params, JSON serialization constraints, query function semantics, Click patterns). All other suppress comments removed — findings left standing as design reminders.

### Code improvements made (Phases 42 + 42-2)

- Removed unused `db_name` param from `setup_schema_privileges()`
- Extracted `format_iso()` datetime helper (9 hasattr patterns → isinstance)
- Converted loop-and-append accumulator to comprehension
- Fixed silent failure in `_handle_restore_error()` (unknown ClientErrors now re-raise)
- Removed vestigial `verbose` param from `audit()` command
- Fixed `audit_images()` type contract (dict[str, Any] → dict[str, ImageConfig])
- Converted `_format_service()` return type to `ServiceInfo` dataclass, removed vestigial `arn` field
- Flattened arrow-code in 6 functions: `detect_framework()`, `get_next_listener_priority()`, `cmd_start()` (extracted `_ensure_rds_available()`), `list_repositories_for_environment()`, `cmd_put()` (extracted `_get_secret_value_interactively()`), `check_infrastructure_status()`

### Remaining findings (110)

| Category | Count | Notes |
| --- | --- | --- |
| duplicate-blocks | 19 | Mostly CLI boilerplate and security-distinct privilege grants |
| long-function | 16 | Orchestration functions (100-167 lines) |
| arrow-code | 7 | Remaining moderate nesting (depth 5) |
| inconsistent-error-handling | 9 | Logging leaf functions + CLI boundary patterns |
| law-of-demeter | 4 | Chain depth 4 on deploy config and path traversal |
| single-call-site | 3 | Named helpers that document intent |
| dict-as-dataclass | 2 | Restore function return dicts |
| inconsistent-returns | 2 | Restore functions (dict\|None) |
| vestigial-params | 2 | Module interface `context` (required by contract) |
| write-only-attributes | 1 | ServiceInfo fields used via iteration |
| unused-defaults | 1 | Semantically optional param (suppressed) |

*Last updated: 2026-04-15*
