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

## Pysmelly Findings — Worked Through

MEDIUM severity findings from 2026-04-09 run (147 total) have been worked through. Results:

- **Actual fix:** Removed unused `db_name` parameter, extracted `format_iso()` datetime helper, converted loop-and-append to comprehension
- **Suppressed with rationale:** Remaining findings evaluated as intentional patterns (CLI error boundaries, query functions returning None, security-critical privilege grants, Click framework patterns, etc.)

### Remaining (informational, not tracked)

19 duplicate-blocks (suppression comments in place but some have overlapping line ranges that pysmelly still reports), 16 long-function, 11 arrow-code, 9 inconsistent-error-handling (cascading from format_iso callers), 4 law-of-demeter, 3 single-call-site.

### Future candidate

`_format_service()` in `src/deployer/aws/ecs.py` is the strongest dict-to-dataclass candidate (7-key dict, 15+ call sites, pure Python). Deferred due to large caller update scope.

*Last updated: 2026-04-14*
