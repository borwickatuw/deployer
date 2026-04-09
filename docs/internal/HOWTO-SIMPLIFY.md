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

## Pysmelly Findings to Work Through

Findings from `uvx pysmelly .` run 2026-04-09. HIGH severity addressed; MEDIUM organized by category for follow-up.

### Convergence hotspots

Files flagged by 3+ checks — prioritize these for refactoring:
- `bin/emergency.py` (5 checks)
- `modules/db-on-shared-rds/lambda/index.py` (5 checks)
- `src/deployer/init/bootstrap.py` (4 checks)
- `src/deployer/aws/ecs.py` (4 checks)
- `src/deployer/core/config.py` (3 checks)

### By category

| Category | Count | Effort | Notes |
| --- | --- | --- | --- |
| duplicate-blocks | 24 | Large | Mostly in bin/init.py, emergency.py, lambda/index.py. Extract shared patterns. |
| inconsistent-error-handling | 15 | Medium | Needs error contract decisions for core functions (load_environment_config, get_environments_dir). |
| getattr-strings | 11 | Small | 9 are `hasattr(obj, 'isoformat')` — extract a datetime formatting helper. |
| pass-through-params | 9 | Medium | Structural — thin wrappers that only forward params. Evaluate if wrappers add value. |
| dict-as-dataclass | 9 | Medium | Good candidates: `_format_service()`, `get_status()`, `format_user()`, restore functions. |
| param-clumps | 9 | Medium | Repeated parameter groups (conn+username+password, env_config+deploy_config+environment). |
| vestigial-params | 7 | Small | 3 are AWS Lambda `context` (required by handler signature). 1 is `verbose` reserved for future. |
| duplicate-except-blocks | 6 | Small | Shared error handling patterns across bin/ scripts. |
| return-none-instead-of-raise | 4 | Small | `get_status()`, `get_cognito_user_pool_id_from_config()`, `bootstrap_dir_exists()`, `get_linked_deploy_toml()`. |
| foo-equals-foo | 4 | Small | Minor: inline single-use locals in function calls. |
| write-only-attributes | 2 | N/A | Suppressed — checkpoint fields read via JSON serialization. |
| feature-envy | 2 | Medium | DatabaseModule.validate() accesses 11 attrs of env_config. |
| isinstance-chain | 2 | Small | compose.py and config.py — consider dispatch tables. |
| inconsistent-returns | 2 | Small | emergency/rds.py restore functions return mixed types. |
| shotgun-surgery | 2 | N/A | click.command pattern — inherent to Click framework. |

### LOW severity (informational, not tracked)

17 long-function, 13 arrow-code, 4 law-of-demeter, 3 single-call-site, 2 temp-accumulators.

*Last updated: 2026-04-09*
