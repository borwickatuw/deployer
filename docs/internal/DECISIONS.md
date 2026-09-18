# Architectural Decisions

This document records significant architectural decisions and the reasoning behind them.

## 2026-01-21: Separation of App Structure (deploy.toml) and Sizing (tfvars)

**Decision:** Split configuration between two locations:

- `deploy.toml` (in app repo): What to run - images, commands, env vars, health check paths
- `terraform.tfvars` (in deployer repo): How big - CPU, memory, replicas, scaling

**Alternatives considered:**

- All configuration in deploy.toml (including sizing per environment)
- All configuration in OpenTofu (including commands, env vars)
- Environment-specific deploy.toml files (deploy.staging.toml, deploy.production.toml)

**Reasoning:**

- **Clear ownership**: App developers control what their app needs; infrastructure operators control resource allocation.
- **No environment-specific values in app repos**: deploy.toml works identically for any environment.
- **Independent changes**: Can adjust staging/production sizing without touching application code.
- **No deployment mistakes**: Can't accidentally deploy production sizing to staging by using wrong config file.
- **Different change frequencies**: Sizing changes rarely (scaling events); app structure changes with each deployment.

**See also:** [DESIGN.md](DESIGN.md) for detailed explanation of this philosophy.

______________________________________________________________________

## 2026-01-21: Environment Variables Belong in deploy.toml, Not tfvars

**Decision:** Environment variables are configured in `deploy.toml` (the app repo), not in OpenTofu tfvars, even though they can differ by environment.

**Alternatives considered:**

- Move all env vars to OpenTofu tfvars (like sizing)
- Split env vars between both locations based on whether they differ by environment
- Use separate tfvars variables for environment-specific app config

**Reasoning:** Environment variables are **app behavior configuration**, not sizing/capacity. They control how the application behaves, not how many resources it uses. Key distinctions:

| Aspect           | Environment Variables                               | Sizing                            |
| ---------------- | --------------------------------------------------- | --------------------------------- |
| Who decides      | App developers                                      | Infrastructure operators          |
| Controls         | App behavior (debug mode, log level, feature flags) | Resource allocation (CPU, memory) |
| Change frequency | With app features                                   | Rarely (scaling events)           |
| Example          | `DEBUG=true`, `LOG_LEVEL=DEBUG`                     | `cpu=1024`, `replicas=2`          |

Developers should control their application's behavior. Moving env vars to tfvars would require infrastructure changes for every feature flag or config tweak.

______________________________________________________________________

## 2026-01-21: Environment-Specific Environment Variables with Base + Override Pattern

**Decision:** Support environment-specific environment variables using a base + override pattern in deploy.toml:

```toml
# Base - applies to all environments
[environment]
DJANGO_SETTINGS_MODULE = "myapp.settings"
DATABASE_URL = "${database_url}"

# Environment-specific overrides
[environment.staging]
DEBUG = "true"

[environment.production]
DEBUG = "false"
```

**Alternatives considered:**

- Conditional placeholders: `DEBUG = "${if environment == staging then true else false}"`
- Separate env var files per environment: `environment.staging.toml`
- All env vars in tfvars with full duplication

**Reasoning:**

- **DRY**: Base values defined once, only differences need overrides.
- **Clear and intuitive**: Anyone can understand what staging vs production gets.
- **Flexible**: Supports any environment name (dev, qa, staging, production).
- **Service-specific overrides**: Can also do `[services.celery.environment.staging]` for per-service, per-environment values.
- **No expression language**: Avoids complex conditional syntax that would be harder to read/maintain.

**Merge order** (later overrides earlier):

1. `[environment]` - base
1. `[environment.{env}]` - environment override
1. `[services.{name}.environment]` - service base
1. `[services.{name}.environment.{env}]` - service + environment

______________________________________________________________________

## 2026-01-21: Infrastructure References via Placeholders

**Decision:** Environment variables can reference infrastructure values using `${placeholder}` syntax, resolved at deploy time from the environment's `config.toml` file.

```toml
[environment]
DATABASE_URL = "${database_url}"
REDIS_URL = "${redis_url}"
AWS_REGION = "${aws_region}"
```

**Alternatives considered:**

- Direct OpenTofu variable interpolation in deploy.toml
- Separate "secrets" vs "config" distinction with different resolution
- Hardcoded values per environment (no placeholders)

**Reasoning:**

- **Clean interface**: deploy.toml declares what it needs; the deploy script resolves values from infrastructure outputs via config.toml.
- **Infrastructure as source of truth**: Database URLs, bucket names, etc. come from OpenTofu outputs (via `${tofu:...}` placeholders in config.toml), not duplicated in deploy.toml.
- **Environment-agnostic**: The same placeholder resolves differently per environment based on which config.toml is loaded.
- **Explicit**: Placeholders are clearly marked with `${}` syntax - no magic variable names.
- **No manual exports**: The config.toml approach eliminates the need to manually export environment variables before running deploy.py.

**Available placeholders** (resolved from config.toml):

- `${database_url}` - from `[database].url`
- `${redis_url}` - from `[cache].url`
- `${s3_media_bucket}` - from `[storage].media_bucket`
- `${aws_region}` - from AWS SDK
- `${environment}` - from environment argument

______________________________________________________________________

## 2026-01-21: Secrets via SSM/Secrets Manager References (Not in deploy.toml Values)

**Decision:** Secrets are stored as *references* to SSM Parameter Store or Secrets Manager paths, not as actual values. The deploy script never sees secret values.

```toml
[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"
DB_PASSWORD = "secretsmanager:myapp-db:password"
```

> **Syntax superseded 2026-08-17 (Phase 53h-2a).** The decision above still
> holds — deploy.toml carries references, never values. The *shape* of the
> reference does not: `[secrets]` now takes `names = [...]` only, and the SSM
> path comes from the environment's `config.toml` `path_prefix`. The explicit
> form shown here is rejected at preflight. See
> [removed-features/explicit-secret-paths.md](removed-features/explicit-secret-paths.md).

**Alternatives considered:**

- Encrypted secrets in deploy.toml
- Secrets in separate encrypted file
- All secrets in environment variables passed to deploy script

**Reasoning:**

- **Security**: Secret values never appear in deploy.toml, deploy script logs, or CI/CD output.
- **IAM-based access**: ECS tasks have IAM roles that grant access to specific secrets.
- **Environment substitution**: Using `${environment}` in paths means same config works for all environments.
- **Audit trail**: AWS CloudTrail logs secret access, not the deploy script.
- **Rotation**: Secrets can be rotated in AWS without redeploying.

______________________________________________________________________

## 2026-01-21: Docker Build Uses --no-cache to Avoid Stale Base Images

**Decision:** The deploy script uses `docker build --no-cache` to ensure fresh builds and avoid silently using stale cached base images.

**Context:** Users may have Dockerfiles that inherit from local images (e.g., `FROM myapp-base`). If the base image was built previously and cached locally, Docker will silently use the cached version even if the base Dockerfile has changed.

**Alternatives considered:**

- Require specific Dockerfile patterns (multi-stage builds, explicit tags)
- Add dependency tracking between images with explicit build ordering
- Dual-tag base images so children can find them
- Use BuildKit's `--build-context` to override FROM references

**Reasoning:**

- **Don't restrict Dockerfiles**: This project shouldn't dictate how users structure their Dockerfiles. People may have legitimate reasons for multi-image inheritance.
- **Fail loudly**: Better to get an error ("base image not found") than to silently use a stale cached image that doesn't reflect current code.
- **Simplicity**: `--no-cache` is a single flag that solves the problem without complex dependency tracking.
- **Correctness over speed**: Deploy builds should be reproducible and correct. The time cost of `--no-cache` is acceptable for production deployments.

**Trade-off:** Builds will be slower since layers aren't cached. This is acceptable because:

- Deployments aren't frequent enough for this to matter
- Correctness is more important than speed for production deployments
- CI/CD systems often don't have meaningful caches anyway

______________________________________________________________________

## 2026-01-23: Least-Privilege IAM Roles Instead of Administrator Access

**Decision:** Replace single AdministratorAccess with three purpose-specific IAM roles:

- `deployer-app-deploy` - Application deployments (deploy.py)
- `deployer-infra-admin` - Infrastructure changes (OpenTofu)
- `deployer-cognito-admin` - Cognito user management

**Alternatives considered:**

- Single scoped-down IAM user with all permissions
- Separate IAM users per workflow (instead of roles)
- AdministratorAccess with MFA requirement

**Reasoning:**

- **Least privilege**: Each role has only the permissions needed for its specific task
- **Roles over users**: No long-lived credentials; temporary credentials via STS
- **Auditability**: CloudTrail shows which role was used for each action
- **Explicit project scoping**: ARN patterns restrict access to known projects via prefixes
- **Learned from past issues**: Uses resource ARN patterns instead of condition keys (ecs:cluster condition doesn't work for UpdateService)

**Trade-offs:**

- Requires updating policies when adding new projects
- Initial setup requires Administrator access (bootstrap problem)
- Developers must use correct AWS profile for each operation

**See also:** [iam-policies/README.md](../../iam-policies/README.md) for policy details

______________________________________________________________________

## 2026-01-24: ECS Fargate as Sole Deployment Target

**Decision:** Support only ECS Fargate as the deployment target. Do not add Docker Compose on EC2, Lambda, or Kubernetes support.

**Alternatives considered:**

- Docker Compose on EC2 (single-instance pattern)
- Lambda as deployment target (serverless)
- Kubernetes/EKS (container orchestration)
- Pluggable deployment backends (abstract interface for multiple targets)

**Reasoning:**

- **ECS provides managed infrastructure**: Auto-scaling, health checks, blue-green deployments, and rolling updates without managing EC2 instances.
- **Single deployment model is maintainable**: Supporting multiple backends would multiply complexity without clear benefit.
- **Lambda requires framework adapters**: Each framework needs special Lambda support (Django-Lambda, Rails-Lambda), different timeout constraints (15 min max), and special database connection handling (Lambda cold starts cause connection pool issues).
- **Docker Compose lacks production features**: No auto-scaling, no automatic health check recovery, single point of failure.
- **Kubernetes is overkill**: ECS provides sufficient orchestration without Kubernetes complexity.

**Note:** Lambda is supported for *helper utilities* (e.g., `staging-scheduler` module for turning services on/off), just not as a primary deployment target.

**See also:** [SUPPORTED-ARCHITECTURES.md](SUPPORTED-ARCHITECTURES.md)

______________________________________________________________________

## 2026-01-24: Framework-Agnostic Commands via [commands] Section

**Decision:** Remove Django assumptions by requiring a `[commands]` section in deploy.toml for framework-specific CLI commands.

**Alternatives considered:**

- Keep Django-specific `manage` command, add separate commands for other frameworks
- Auto-detect framework from Dockerfile or dependencies
- Require each framework to implement a standard interface (e.g., always use `./run.sh migrate`)

**Reasoning:**

- **Explicit over implicit**: Configuration-driven commands are clearer than auto-detection magic.
- **Framework flexibility**: Works for Django, Rails, Node.js, or any framework with CLI commands.
- **No magic defaults**: Each app explicitly declares its commands rather than relying on Django defaults.
- **Simple model**: Named commands map to arrays of command arguments - no complex DSL.
- **Non-interactive only**: Commands run without a TTY, so only non-interactive commands are supported.

**Example usage:**

```toml
# Django (non-interactive commands only)
[commands]
migrate = ["python", "manage.py", "migrate"]
collectstatic = ["python", "manage.py", "collectstatic", "--noinput"]
check = ["python", "manage.py", "check", "--deploy"]

# Rails
[commands]
migrate = ["bundle", "exec", "rake", "db:migrate"]
assets = ["bundle", "exec", "rake", "assets:precompile"]
```

**ecs-run.py usage:**

```bash
# List available commands
python bin/ecs-run.py run --list-commands --deploy-toml ../app/deploy.toml

# Run a command
python bin/ecs-run.py run myapp-staging migrate --deploy-toml ../app/deploy.toml
```

______________________________________________________________________

## 2026-01-24: Configurable Container Port (Not Hardcoded 8000)

**Decision:** Make the container port configurable via OpenTofu variable with 8000 as the default.

**Alternatives considered:**

- Keep 8000 hardcoded (Django default)
- Auto-detect from Dockerfile EXPOSE directive
- Require explicit port in deploy.toml

**Reasoning:**

- **Rails uses 3000**: Hardcoding 8000 assumes Django. Other frameworks have different conventions (Rails: 3000, Node: 3000, Go: 8080).
- **Backward compatible**: Default of 8000 means existing Django deployments work unchanged.
- **Infrastructure concern**: Port is configured in tfvars (infrastructure) not deploy.toml (app), following our separation of concerns.
- **Simple implementation**: Single variable in main.tf, used in security group rule.

**Usage:**

```hcl
# terraform.tfvars (for Rails app)
container_port = 3000
```

______________________________________________________________________

## 2026-01-24: One Canonical Location Per Config Value (No Fallbacks)

**Decision:** Every configuration value has exactly one correct location. Scripts fail with clear errors when required values are missing, rather than falling back to alternative sources.

**Context:** When adding ECR repository support, the initial implementation checked multiple locations for `ecr_prefix`: environment config.toml, then deploy.toml, then app name. This created ambiguity about which value would be used.

**Alternatives considered:**

- Fallback chain: config.toml → deploy.toml → default value
- Allow same value in multiple places (last one wins)
- Document "preferred" location but accept alternatives

**Reasoning:**

- **Eliminates ambiguity**: When debugging, you know exactly where to look for each value.
- **Prevents silent misconfiguration**: A typo in config.toml doesn't silently fall back to an outdated value in deploy.toml.
- **Enforces correct architecture**: ECR prefix is infrastructure (created by Terraform), so it must come from config.toml. Fallbacks would allow it in deploy.toml, blurring the infrastructure/app boundary.
- **Faster debugging**: Clear error messages ("ecr_prefix not found in environment config") immediately point to the fix, rather than mysterious behavior from unexpected fallback values.
- **Simpler code**: No cascading if/else chains to check multiple sources.

**Application:**

| Value                        | Canonical Location             | Rationale                     |
| ---------------------------- | ------------------------------ | ----------------------------- |
| ECR prefix                   | config.toml `[infrastructure]` | Created by Terraform          |
| Service sizing (cpu, memory) | terraform.tfvars               | Infrastructure capacity       |
| Docker commands              | deploy.toml `[services]`       | App behavior                  |
| Environment variables        | deploy.toml `[environment]`    | App configuration             |
| Secrets references           | deploy.toml `[secrets]`        | App needs (paths, not values) |

______________________________________________________________________

## 2026-01-29: Resource Module System for Infrastructure Abstraction

**Decision:** Implement a module system that separates application resource declarations (in deploy.toml) from environment implementation details (in config.toml).

**Alternatives considered:**

- Keep current placeholder system (`${db_host}`, `secretsmanager:${arn}`) with better documentation
- Use environment variables for everything (no structured resource declarations)
- Framework-specific adapters (Django adapter, Rails adapter) that know infrastructure

**Reasoning:**

- **Applications should be infrastructure-agnostic**: An app shouldn't know if credentials come from SSM, Secrets Manager, or environment files.
- **Environment controls implementation**: Staging might use SSM, production might use Secrets Manager with rotation - the app shouldn't care.
- **Clear validation**: Modules can validate that config.toml provides what deploy.toml declares.
- **Extensible pattern**: Adding new resource types (queues, search, etc.) follows a consistent pattern.
- **Clean break is acceptable**: All apps and environments are under our control, so we can update everything at once.

**Trade-offs:**

- Requires updating all deploy.toml and config.toml files simultaneously
- More indirection (modules instead of direct placeholders)
- App developers must learn the declarative syntax

**See also:** [Resources](../resources/README.md), [DESIGN.md](DESIGN.md)

______________________________________________________________________

## 2026-01-30: Migrate IAM Policies to Terraform-Managed Bootstrap

**Decision:** Move IAM policies from static JSON files (`iam-policies/`) to terraform resources in `deployer-environments/bootstrap/`, enabling multi-account support with per-environment AWS profile configuration.

**Alternatives considered:**

- Keep static JSON files with manual setup.sh script
- Use AWS CloudFormation instead of Terraform
- Use a single bootstrap with conditional logic for multi-account

**Reasoning:**

- **Infrastructure as Code**: IAM policies are infrastructure and should be versioned/managed with terraform like everything else.
- **Multi-account support**: Different AWS accounts (staging vs production) need different IAM resources. The bootstrap module pattern allows per-account instantiation with account-specific `trusted_user_arns`.
- **Dynamic values**: Terraform allows using `${data.aws_caller_identity.current.account_id}` and `${var.region}` instead of hardcoded values, making policies portable across accounts.
- **Easier project additions**: Adding a new project just requires updating `project_prefixes` in terraform.tfvars and running apply, rather than editing multiple JSON files and running setup.sh.
- **Per-environment profiles**: The `[aws].profile` in config.toml allows each environment to specify its AWS profile, enabling multi-account deployments from a single workstation.

**Trade-offs:**

- Initial migration requires importing existing IAM resources
- Bootstrap terraform state is stored locally (can't use S3 before the bucket exists)
- Slightly more complex setup for new accounts

**Structure after migration:**

```
deployer-environments/
├── bootstrap/                    # Shared terraform module
│   ├── main.tf                   # IAM + S3 + boundary resources
│   ├── variables.tf              # region, project_prefixes, trusted_user_arns
│   └── outputs.tf                # role ARNs, state bucket, account_id
├── bootstrap-staging/            # Instance for staging account
│   ├── main.tf                   # module "bootstrap" { source = "../bootstrap" }
│   └── terraform.tfvars          # Account-specific config
└── myapp-staging/
    └── config.toml               # Can include [aws].profile for this env
```

______________________________________________________________________

## 2026-02-05: Two-Account Database Model for Django Deployments

**Decision:** All Django deployments use two database users:

- **App user**: DML only (SELECT, INSERT, UPDATE, DELETE) - used by runtime services
- **Migrate user**: DDL + DML (CREATE, ALTER, DROP, etc.) - used by migrations

This is always enabled - no opt-in flag.

**Alternatives considered:**

- Single user for all operations (previous approach)
- Opt-in dual-user mode with backward compatibility
- Manual user creation documented but not automated

**Reasoning:**

- **Reduced blast radius**: If the application is compromised, attackers cannot drop tables or alter schema - only corrupt data.
- **Principle of least privilege**: Runtime services don't need DDL access, so they shouldn't have it.
- **Always enabled**: No configuration complexity. Every Django deployment follows the same security pattern.
- **Automated setup**: Lambda function creates users at RDS provisioning time - no manual SQL to run.

**Implementation:**

- New `modules/db-users/` terraform module with Lambda that creates PostgreSQL users
- Separate ECS task definition for migrations (`{app}-{env}-migrate`)
- `ecs-run.py` automatically uses migrate credentials for `migrate` command
- Service task definitions use app credentials (DML only)

**AWS ECS Constraint:** AWS ECS does not support overriding secrets in `containerOverrides` when calling `run-task`. This means we need a **separate task definition** with migrate credentials for migrations, rather than just overriding credentials at runtime.

______________________________________________________________________

## 2026-02-05: pg8000 for Lambda PostgreSQL Driver (Bundled Dependencies)

**Decision:** Use pg8000 (pure Python PostgreSQL driver) bundled directly in the Lambda zip, rather than psycopg2 with external Lambda layers.

**Alternatives considered:**

- psycopg2 with public Lambda Layer (e.g., Klayers project)
- psycopg2 with self-managed Lambda Layer
- psycopg2-binary compiled for Amazon Linux

**Reasoning:**

- **No external dependencies**: Public Lambda layers can disappear, change versions, or have access issues. We encountered permission errors trying to use a third-party psycopg2 layer.
- **Pure Python**: pg8000 has no C extensions, so it works on any platform without compilation. No need to build for Amazon Linux specifically.
- **Self-contained**: Dependencies are installed via `pip install -r requirements.txt -t .` at terraform apply time and bundled in the zip.
- **Small footprint**: pg8000 with dependencies is ~200KB, well under Lambda's 50MB limit.
- **Reliability**: No cross-account layer access, no version drift, no external service dependencies.

**Trade-offs:**

- pg8000 is slightly slower than psycopg2 (pure Python vs C extension)
- Less common than psycopg2 in tutorials/documentation

For a Lambda that runs once at infrastructure provisioning time, the performance difference is irrelevant.

______________________________________________________________________

## 2026-02-09: Database Extensions via deploy.toml and Lambda

**Decision:** Applications declare required PostgreSQL extensions in `deploy.toml`. At deploy time, `deploy.py` invokes the db-users Lambda (which connects as the RDS master user) to create them before running migrations.

```toml
# deploy.toml
[database]
type = "postgresql"
extensions = ["unaccent", "pg_bigm"]
```

**Alternatives considered:**

- Create extensions in Django migrations (current approach that broke)
- Create extensions purely in tofu (db-users Lambda hardcodes extensions)
- Run a pre-migration ECS task with master credentials
- Grant `rds_superuser` to the migrate user

**Reasoning:**

- **Some extensions require `rds_superuser`**: On AWS RDS, extensions like `pg_bigm` can only be created by the master user. The migrate user (DDL+DML) is insufficient even with `CREATE` privilege on the database. Trusted extensions like `unaccent` work with lesser privileges, but untrusted ones do not.
- **App declares, infrastructure provides**: This follows the existing module pattern — the app says what it needs, the deployer figures out how to provide it. Extensions are database infrastructure needs, so they belong in `deploy.toml`'s `[database]` section.
- **Lambda already has the right access**: The db-users Lambda connects as the RDS master user, which has `rds_superuser`. Adding extension creation to its capabilities is a natural fit.
- **deploy.py invokes the Lambda**: Rather than only running at `tofu apply` time, the Lambda can be invoked by `deploy.py` before migrations. This means new extensions take effect at deploy time without requiring a separate `tofu apply`.
- **Django migrations become no-ops**: Apps can keep `CREATE EXTENSION IF NOT EXISTS` in migrations as a safety net. When the extension already exists (created by the Lambda), the migration harmlessly no-ops.

**Implementation outline:**

1. `deploy.toml` declares `extensions` in `[database]` section
1. `config.toml` provides the Lambda function name (from tofu output)
1. `deploy.py` reads extensions from deploy.toml, invokes the Lambda with `{"action": "create_extensions", "extensions": ["unaccent", "pg_bigm"]}` before starting migrations
1. The Lambda creates each extension as the master user (`CREATE EXTENSION IF NOT EXISTS`)
1. The Lambda function name is a new tofu output from the db-users module
1. `deploy.py` needs `lambda:InvokeFunction` permission on the db-users Lambda (added to the `deployer-app-deploy` IAM role)

**Trade-offs:**

- `deploy.py` gains a new AWS API call (Lambda invoke), adding to the `deployer-app-deploy` IAM role
- The db-users Lambda gains a second action (`create_extensions` in addition to `create_users`)
- Extensions are created on every deploy (idempotent, but adds a few seconds)

______________________________________________________________________

## 2026-02-19: deploy.toml as Cloud-Agnostic Protocol, This Repo Owns the Spec

**Decision:** The deploy.toml format is a cloud-agnostic protocol describing what to deploy. This project (`deployer`, effectively `deployer-aws`) owns the spec. A hypothetical `deployer-azure` would follow it.

**Context:** Analysis of Azure feasibility (see [WHATIF-AZURE.md](abandoned/WHATIF-AZURE.md)) showed that deploy.toml is almost entirely cloud-agnostic -- it describes images, services, commands, env vars, and migrations with no cloud coupling. The only cloud-specific element is the `[secrets]` URI format. This makes deploy.toml a natural shared contract between cloud implementations.

**Alternatives considered:**

- Shared spec repository or Python package (`deployer-config`) that both implementations depend on
- Shared spec doc with independent parsers in each project
- This repo owns the spec, other implementations follow it

**Reasoning:**

- **One owner avoids governance overhead**: A shared spec repo or package requires coordinating releases, versioning, and compatibility across projects. For two implementations (one hypothetical), this is over-engineering.
- **The spec is the code**: The parser in `deploy_config.py` is the authoritative definition of what's valid. Duplicating that in a separate package adds indirection without benefit.
- **Extract later if needed**: If a second implementation actually materializes, extracting the parsing code into a shared package is straightforward -- `deploy_config.py` is already a clean, self-contained module.
- **Validation is per-implementation**: Each deployer validates what it supports and fails fast on anything it doesn't. An Azure deployer seeing `ssm:/path` in secrets should fail with a clear error, not silently ignore it. No shared "lowest common denominator" validator needed.
- **Cloud-specific concerns stay in config.toml**: deploy.toml stays cloud-agnostic because cloud-specific config (registry prefixes, infrastructure references, credential sources) lives in the environment's config.toml, which is already per-cloud by design.

**What this means in practice:**

- deploy.toml changes are made here and documented in [CONFIG-REFERENCE.md](../CONFIG-REFERENCE.md)
- A `deployer-azure` project would implement its own parser following this spec
- The `[secrets]` section is the main extension point -- each cloud defines its own URI scheme
- No cross-project validation; each implementation validates independently

______________________________________________________________________

## 2026-08-18: Error Contracts — Raise, Absence Sentinel, or Failure Sentinel (Phase 53i-2c)

**Status: decided in full (operator, 2026-08-19); applied by Phase 53i-3.**
Both escalated items were confirmed, one of them with a correction:

- **(a)** the `emergency/` queries **raise**, and consumers **catch at the
  render boundary** rather than letting one failed section abort a whole
  read-only command. `bin/ops.py` reports each failure in place; the
  destructive `bin/emergency.py` aborts, which is the right answer there.
- **(b)** "operator declined" exits **`3`, not `2`** — see the correction
  below. This entry proposed `2`, which would have collided with Click.
- **A fourth layer** was added: the `except Exception` misattribution family
  this entry did not decide.

#### Correction (2026-08-19): the proposed exit code collided with Click

`bin/emergency.py` is a Click CLI, and **Click already returns `2` for usage
errors** (`click.UsageError.exit_code == 2`, verified at HEAD). Under the
original proposal `emergency rollback --bogus-flag` and `emergency rollback`
answered "n" would have been indistinguishable to any wrapper script — the
exact defect PYTHON.md #19 exists to prevent, reintroduced by the fix for it.
The corrected ladder, which deployer's exit codes now follow:

| Code | Meaning                                      |
| ---- | -------------------------------------------- |
| `0`  | succeeded (warnings may have printed)        |
| `1`  | failed                                       |
| `2`  | usage error — **Click owns this, untouched** |
| `3`  | declined by the operator; nothing attempted  |

deployer used no exit code above `1` before this, so `3` was free.

**Fleet note, out of scope here:** storage-scripts uses `2` for FATAL across 11
sites, and its `docs/DECISIONS.md` records an EventBridge rule and a Step
Functions `Choice` keying on `ExitCode == 1` vs anything else — so that repo's
`2` already collides with argparse's usage-error `2`. Filed as a
claude-meta GUIDE-BACKLOG item.

**Decision:** Every function in this repo has exactly one of three error
contracts, stated in its docstring, and **one sentinel never means both
"nothing is there" and "I could not look"**:

| Contract                       | Shape                | Used for                                                    |
| ------------------------------ | -------------------- | ----------------------------------------------------------- |
| **Raises**                     | value, or exception  | The caller cannot proceed without the value                 |
| **Sentinel meaning _absence_** | `None` / `[]` / `{}` | "Not found" / "not applicable" is a normal, expected answer |
| **Sentinel meaning _failure_** | documented as such   | The caller must distinguish outcomes and keep going         |

The fleet-wide form of this rule is claude-meta `best-practices/PYTHON.md`
Practice 19, written in the same session (53i-2b) from a 13-repo measurement.
This entry decides the three questions that guide **cannot** answer for us,
because only this repo has the shape: what `emergency/` does, what the four
`inconsistent-error-handling` contracts are, and what the emergency CLI's exit
codes mean.

**Alternatives considered:**

- **Leave it undecided and suppress the findings.** Rejected: four
  `return-none-instead-of-raise` suppressions already stand, and 53c
  deliberately left `run_aws_json` unsuppressed so the decision would land in
  front of the operator rather than be hidden by a fifth.
- **Make everything raise.** Rejected: it churns every call site that guards a
  legitimately-absent answer. Measured fleet-wide, every real call site already
  guards — the `None`-means-absence contract works where it is used honestly.
- **Write the policy in deployer only.** Rejected by operator decision
  (2026-08-18): storage-scripts (5+7) and claude-meta (4+3) each carry more of
  this corpus than deployer (1+4), so a deployer-only guide would have been
  drawn from the third-largest sample.

**Reasoning:** `return-none-instead-of-raise` and `inconsistent-error-handling`
are the only two checks in this arc with no mechanical fix, because both are
symptoms of the same omission — a function that never said what it does when
things go wrong, so each caller guessed. The count is not the deliverable; a
decidable contract is.

### Layer 1 — Producer: `emergency/` reports failure as absence

**This is the decision that matters.** Eleven functions return a sentinel from
inside `except ClientError`, so an AWS permissions failure or a network timeout
is reported to the operator, **during an incident**, as "nothing is there."

| Function                                 | Returns from `except ClientError` | Reads to the operator as  |
| ---------------------------------------- | --------------------------------- | ------------------------- |
| `ecs.get_service_state:43`               | `None` (line 70)                  | no such service           |
| `ecs.get_all_services_state:73`          | `pass` (line 105)                 | service silently omitted  |
| `ecs.list_task_definition_revisions:110` | `pass` (line 165)                 | no revisions to roll back |
| `ecs.get_task_definition_details:170`    | `None` (line 205)                 | no such task definition   |
| `rds.get_rds_snapshots:115`              | `[]` (line 190)                   | no snapshots exist        |
| `rds.get_rds_instance_details:193`       | `None` (line 227)                 | no such instance          |
| `ecs.update_service_task_definition:261` | `False` (line 285)                | the update failed         |
| `ecs.scale_service:341`                  | `False` (line 365)                | the scale failed          |
| `ecs.force_new_deployment:368`           | `False` (line 393)                | the deploy failed         |
| `rds.create_emergency_snapshot:76`       | `None` (line 112)                 | no snapshot was made      |
| `ecs.wait_for_deployment:288`            | `pass` (line 334)                 | **keeps polling**         |

**A twelfth instance was found when 53i-3 read the corpus.**
`compare_task_definitions` (`emergency/ecs.py:208`) has the same shape without
an `except` of its own:

```python
details1 = get_task_definition_details(arn1)
details2 = get_task_definition_details(arn2)
if not details1 or not details2:
    return {}          # "no differences" — or "I could not read one of them"
```

`bin/emergency.py:322` renders that `{}` as the environment-variable diff shown
to the operator **before confirming a rollback**, so a failed read displays "no
changes". It inherits the defect rather than adding one, so it was fixed for
free once `get_task_definition_details` raised — and once it raised, that
function had no `None` left to give, so its return type stopped being optional
and the dead guard went with it.

**The six queries and `wait_for_deployment` are the dangerous half.** The four
mutators returning `False` are at least honest — `False` already means
"failed", and the caller acts on it. A query returning `[]` is not: "you may not
read this" and "this isn't there" become the same answer, and the rollback
command then reports that there is nothing to roll back.

`wait_for_deployment` is the worst of the eleven: its `except ClientError: pass`
sits inside the poll loop, so a call that fails every time polls a service it
cannot see for the **entire** timeout and then returns `False` — indistinguishable
from a deployment that genuinely did not stabilise.

**Decision (a) — confirmed 2026-08-19:** the six queries and
`wait_for_deployment` adopt contract 1 — **raise**. `ClientError` is a failure, not an absence; genuine absence
(`describe-services` succeeding with an empty list) keeps the existing `None`/`[]`
and its docstring says so. The four mutators keep `False` with `Raises:`
documented, since their callers already branch on it. `bin/emergency.py` and
`bin/ops.py` are the **only** two consumers, both CLI entry points, so the blast
radius is bounded.

**Where each consumer catches (operator decision, 2026-08-19).** The two
answers differ because the commands differ, not by accident:

| Consumer           | Answer                                                            | Why                                                                                                                                         |
| ------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `bin/ops.py`       | catch at each **render boundary** and report the failure in place | Read-only. A CloudWatch permissions gap must not stop the ECS section printing, and a missing section reads as "there is nothing there".    |
| `bin/emergency.py` | let it propagate to one boundary `exit_on(RuntimeError)`          | Destructive. Aborting with a named reason is correct where rendering partial output is not: nobody should confirm a change nobody can read. |

The render-boundary pattern was **not new** — `bin/ops.py:422 _print_rds_status` already printed `"  Unable to retrieve status"` in place,
and `test_ops.py` already pinned it. Its neighbour two functions down showed
the gap: `_print_recent_snapshots` did `if not snapshots: return`, so a
`ClientError` silently deleted the whole "Recent Snapshots" section from the
status report — the answer that matters least when it is wrong. 53i-3b applied
the existing pattern consistently rather than inventing one.

**One `except Exception` is deliberately left alone.**
`bin/ops.py:880 cmd_incident_start` writes `"(Could not capture state: {e})"`
into the incident file. That is honest degradation, not misattribution: the
file is still written and records *why* the state is missing. Starting an
incident must never fail because the environment it is about is unreachable —
that is the case it exists for.

Also at this layer: **`aws/cli.run_aws_json:48`** — the arc's one live
`return-none-instead-of-raise`, left unsuppressed by 53c on purpose. Its `None`
means failure only (a successful `describe-*` always parses), which is contract
3 — but `aws/rds.get_status:12` then translates that failure-`None` into its own
documented "or None if not found". **The two meanings collapse one layer down.**
That translation is the defect, not the check.

### Layer 2 — Consumer: the four `inconsistent-error-handling` contracts

Each call site classified by whether it sits inside a `try` (measured at
`4678c1e`, so the counts reconcile to pysmelly's):

| Contract                                                                    | Verdict                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `utils/aws_profile.py:85 configure_aws_profile_for_environment` (3 callers) | **False positive — no change.** It raises `RuntimeError` only inside `if validate:` (line 141). `utils/cli.py:219` passes `validate=True` and catches; `:233` and `:254` pass the default `False` and *cannot* raise it. The check does not model the flag.                                                                                                                                                                                                                                                 |
| `init/template.py:126 substitute` (4 callers)                               | **Callers legitimately differ — document only.** `init/environment.py:163`'s `except KeyError` is not error handling but a **fallback dispatch** to `substitute_optional`. `init/bootstrap.py:170/175/179` want the `KeyError`: a missing bootstrap placeholder must not write a half-rendered `main.tf`. (Using an exception as a mode selector is a separate smell; noted, not scheduled.)                                                                                                                |
| `core/config.py:205 load_environment_config` (14 callers)                   | **A caller has a real bug — fix.** Nine `bin/` sites wrap it in `except (FileNotFoundError, RuntimeError)`; **four do not** — `bin/ops.py:504`, `:615`, `:686` and `bin/resolve-config.py:109` — so a missing `config.toml` is a traceback in three `ops` subcommands and in the CI config resolver. Two more catch **broad `Exception`** (`bin/deploy.py:74`, `bin/ops.py:871`), swallowing errors the contract never promised.                                                                            |
| `utils/environment.py:17 get_environments_dir` (17 callers)                 | **A caller has a real bug — fix.** It raises `RuntimeError` carrying actionable text ("Set it in your .env file"). **Six `bin/` entry points let it become a traceback**: `capacity-report.py:224`, `cognito.py:72`, `deploy.py:67`, `environment.py:67`, `init.py:501`, `init.py:520` — while `init.py:239/347/457` already catch it. The four library-level sites (`init/environment.py:42/129/254`, `init/verify.py:141`) and the two internal ones (`utils/environment.py:47/113`) correctly propagate. |

**The fix at both "real bug" rows is the boundary rule, not a per-caller
`try`**: wrap the single failing call in `utils/cli.py:127 exit_on`, which
already exists for exactly this and whose docstring already says "wrap the
single call that can fail — not a whole command body."

### Layer 3 — Boundary: the emergency CLI's exit codes

Eleven assertions in `tests/unit/test_emergency_cli.py` and
`test_emergency_cli_restore.py` pin today's behaviour as "pinned, not endorsed."
What they pin:

| Behaviour                                                       | Today | Decision                                             |
| --------------------------------------------------------------- | ----- | ---------------------------------------------------- |
| Operator declines the confirmation (`cmd_rollback`)             | `1`   | **`3`** — declined is not a failure (see correction) |
| The update fails (`cmd_rollback`)                               | `1`   | `1` — unchanged                                      |
| A service fails to scale (`cmd_scale`, line 538)                | `0`   | **`1`** — a reported failure must not exit 0         |
| A service fails to force-deploy (`cmd_force_deploy`, line 800)  | `0`   | **`1`** — same swallow                               |
| A service fails to restore (`cmd_revert`, lines 742/747)        | `0`   | **`1`** — **not pinned by any test; found here**     |
| The deployment times out (`cmd_rollback` → `_await_deployment`) | `0`   | `0` with a **warning** exit — see below              |

A failure printed to the terminal but exiting `0` is invisible to CI and to any
wrapper script; the terminal is not the interface a non-interactive caller
reads. `cmd_scale` and `cmd_force_deploy` both log `Failed to scale…` /
`Failed to force deploy…` and then `return 0` — the two clearest instances.

**`cmd_revert` is a third instance the eleven pins missed**, and the worst of
the three: a failed `update_service_task_definition` or a failed `scale_service`
logs, `continue`s to the next service, and the command still prints
`Revert completed` and returns `0`. That is the checkpoint-restore path — the
one an operator reaches for when a rollback has already gone wrong. 53i-3 needs
a characterization test here **before** changing it, since unlike the other two
there is nothing pinning today's behaviour.

A deployment timeout is genuinely a third thing: the rollback *was* applied and
the wait was inconclusive. It gets the **succeeded-with-warnings** status rather
than being folded into either neighbour. The three-way split is:

- **`0`** — succeeded (warnings printed, work completed)
- **`1`** — failed
- **`3`** — declined by the operator; nothing was attempted (`2` is Click's)

**53i-3 flips the eleven pins from "pinned, not endorsed" to asserted
behaviour** — that is what they were written for.

### Layer 4 — the `except Exception` misattribution family

**Added by operator decision (2026-08-19); this entry as first written had no
answer for it.** Layers 1-3 are all "an error is reported as absence". This
layer is the adjacent shape: an error is reported as *a different error*, so
the operator is sent to fix something that is not broken.

| Site                         | What it reports                                                                                                                                            |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `deploy/deployer.py:223`     | **A _clean_ `InfraStatus()`** — a credentials failure is indistinguishable from "RDS is healthy", and the deploy proceeds. The one with real blast radius. |
| `deploy/extensions.py:148`   | Every non-`ClientError` as "check your AWS credentials and network connectivity" — so a bug inside boto3 is blamed on the operator's network.              |
| `bin/init.py:412`            | Any non-`ValueError` from `generate_deploy_toml` as "Error parsing docker-compose.yml" — a generator bug blamed on the operator's input.                   |
| `deploy/images.py ecr_login` | A `docker login` failure as a bare `RuntimeError("ECR login failed")` with both streams sent to `DEVNULL`, so there are no diagnostics at all.             |
| `deploy/service.py:431`      | A per-service `ClientError` logged, the loop continued, and **the run still ends as a success** — a partial deploy that looks complete.                    |

The fix is one shape in every case: **catch what you can actually attribute,
and re-raise or report the rest as itself.**

### Scope note

This entry is the **checklist 53i-3 executes**; it changes no code itself.
The units that executed it are recorded in [PYSMELLY.md](PYSMELLY.md) §53i-3a
through §53i-3d.

**Named so it is not lost:** the other eleven of 53e-5a's fourteen pinned
latent bugs are **not** in 53i-3's scope, including
`deploy/service.py:171/193 service_exists` swallowing `ClusterNotFoundException`
so a mistyped cluster takes the CREATE branch. Same `except` shape, higher
severity; it belongs to 53e-5's pin list, and folding it in would have made
53i-3 unscopeable. **That one was fixed by 53j-1** (`752e146`) using this same
entry's discriminator — does the API distinguish absence from failure? — see
[PYSMELLY.md](PYSMELLY.md) §53j-1.
53i-2a (`4678c1e`) repaired six detached suppression rationales and moved the
count by zero, and 53i-2b wrote the fleet guide. The register entry is
deployer `docs/internal/PYSMELLY.md` §53i-2a; the phase record is claude-meta
`docs/PLAN.md` Phase 53.

______________________________________________________________________

## 2026-08-20: `build_args.<environment>` Must Be a Table on Both Config Arms

**Decision:** A scalar under `build_args.<environment>` in `deploy.toml` is a
**config error**, and it is rejected identically whichever config shape the
caller holds. Both arms route through `config.merge_build_args`, which raises:

```
Image 'web': build_args.staging must be a table of build args, got str.
Did you mean [images.web.build_args.staging]?
```

**This changes what a `deploy.toml` may contain**, which is why it is recorded
here and not only in the pysmelly register. The accepted cost: a build arg
**named after an environment** — `build_args.staging = "x"` meaning "pass
`--build-arg staging=x`" — can no longer be expressed. Nothing in the fleet does
this, and the name collision with the per-environment block is exactly the typo
the message exists to catch.

**The two arms disagreed before this.** `ImageConfig.get_build_args` guarded
with `isinstance(env_override, dict)` and passed the scalar straight through as
a literal `--build-arg staging=5`. The inline copy in `deploy/images.py` did
not guard, so `dict.update(<scalar>)` raised — `TypeError` for an int,
`ValueError: dictionary update sequence…` for a string — **naming neither the
image nor the key**. Same file, two outcomes, decided by which object the caller
happened to be holding. Production takes the dict arm (`deployer.py` passes
`get_raw_dict()`), so the live behaviour was the opaque crash.

**Alternatives considered:**

- **Both arms pass the scalar through** (the `ImageConfig` behaviour). Rejected:
  `--build-arg staging=5` is not what `build_args.staging = 5` reads as to
  anyone, and the deploy would silently build the wrong image.
- **Both arms ignore it silently.** Rejected: a typo in `deploy.toml` that
  changes nothing and says nothing is the worst of the three.
- **Keep the divergence, document it.** Rejected: it was already documented, in
  a docstring that said the arms "deliberately stay separate". Two answers to
  one question is not a contract.
- **Warn rather than raise.** Rejected: this is a fail-fast repo, and a warning
  in a deploy log is a warning nobody reads.

**Reasoning:** One canonical implementation is what makes the arms unable to
drift again — the shared function is the decision, the message is the surface.
`build_args` sub-tables are the only place `deploy.toml` overloads a key's type
by name, so it is the only place this shape can arise.

**See also:** [PYSMELLY.md](PYSMELLY.md) §53j-2 for the unit that applied it;
the fifth of five items in the 53e latent-bug ledger's 53j scope.

______________________________________________________________________

## 2026-08-20: The Cache-Tag Recipe — What Invalidates a Built Image

**Decision:** `compute_context_hash` hashes the **selected Dockerfile under its
own `Dockerfile:` prefix**, then the path and content of every context file
that survives `.dockerignore` — **excluding two files the walk skips whether or
not `.dockerignore` names them**: the selected Dockerfile (already counted by
the prefix) and `.dockerignore` itself.

**This is an operational promise**, not a refactor: it changes what does and
does not invalidate a built image, and therefore when a deploy rebuilds and
pushes. It is recorded here for that reason.

**The accepted cost, and it is the surprising half:** a `.dockerignore` edit
that only affects files which **do not exist yet** no longer changes the tag.
Add `*.tmp` to a context that has no `.tmp` files and the tag is unchanged.
That is correct — the context docker would send is byte-for-byte identical — but
it is the half that surprises, because "I edited a config file and nothing
rebuilt" reads like a bug. A pattern change that *does* hide or reveal a real
file still busts the tag, through the file set it selects, and that direction is
pinned from both sides.

**`.dockerignore` is build metadata, not build input.** The raw file was hashed
as an ordinary context file, so a comment-only edit rebuilt every image in the
app. The information that matters — which files docker receives — already
reaches the digest through the file set.

**The `Dockerfile:` prefix stays.** It is what records *which* Dockerfile was
selected; without it `compute_context_hash(ctx, "Dockerfile")` and
`(ctx, "Dockerfile.dev")` would collide whenever the two files have identical
content. What was wrong was hashing the same file *again* in the walk.

**Alternatives considered:**

- **Hash a digest of the parsed patterns** in place of the raw file (what the
  written plan proposed). **Rejected on measurement.** Prototyped read-only
  against havoc's three real contexts, it moves **all three** tags where
  exclude-from-walk moves **one** — necessarily, since it feeds every context a
  new hash input it did not have before, `.dockerignore` or not. (The exact
  digests it lands on depend on the framing bytes chosen for the pattern list,
  which is an arbitrary choice; the 3-vs-1 churn is not.) It buys nothing: every
  context that has a `.dockerignore` already self-ignores it, and a pattern
  change that matters already reaches the digest through the file set. It would
  also hash `[".git"]` for an empty context, so an empty context would stop
  hashing the empty digest.
- **Add `Dockerfile` and `.dockerignore` to `parse_dockerignore`'s pattern
  list.** Rejected: those are hash-recipe decisions, not the user's ignore
  rules, and `parse_dockerignore`'s output is read as the latter. Conflating
  them would also make `should_ignore` claim docker excludes files it does not.
- **Leave both defects.** Rejected: the doubled Dockerfile makes the hash inputs
  read wrong to anyone auditing them, and the `.dockerignore` hash rebuilt every
  image in an app for a comment.

**Blast radius, measured before choosing:** `havoc` is the only live app with an
`[images]` section. Of its three contexts only `cantaloupe` moves —
`5ec6bfaec77d` → `5c3ff5cc515e` — because it is the only one with no
`.dockerignore`, so the only one whose Dockerfile was reaching the walk. One
rebuild-and-push on the next havoc deploy. `docker build` still layer-caches
locally, and a changed tag is a cache **miss**, never a failure.

**Reasoning:** The tag answers one question — "would docker build the same
image from the same inputs?" — so the hash should cover exactly the inputs and
nothing else. Both defects were the same mistake in opposite directions: one
input counted twice, one non-input counted at all.

**See also:** [PYSMELLY.md](PYSMELLY.md) §53j-3 for the unit that applied it and
the full pin list; the 2026-01-21 decision on `--no-cache` for the separate
question of base-image staleness *within* a build.

______________________________________________________________________

## 2026-08-21: The Deploy Log Prints Every Environment Variable in Full (Phase 53j-4)

**Decision:** `Deployer.print_environment_config` masks nothing. Every variable
in the environment map prints its value; an empty value prints `(unset)`. The
disjointness that makes this safe is enforced at preflight by
`check_environment_secrets_overlap` rather than assumed.

**What the masking did, measured.** Until this decision the display hid any
variable whose *name* contained `secret`, `password`, `key`, `token`, `url`,
`database` or `connection`. Measured against `havoc-staging`, the only live app,
on a real `deploy --dry-run`: **40 variables, 9 masked, and all 9 held nothing
secret.**

| Masked                   | Actual value                               | What it was       |
| ------------------------ | ------------------------------------------ | ----------------- |
| `BASE_URL`               | `https://havoc-staging.uw-lib-pilot.click` | public            |
| `HEALTH_CHECK_URL`       | `/health/`                                 | a path            |
| `IIIF_SERVER_URL`        | an internal service URL                    | not secret        |
| `SAML_METADATA_URL`      | UW's public IdP metadata URL               | public            |
| `SIGNED_URL_EXPIRY`      | `3600`                                     | a number          |
| `REDIS_URL`              | `redis://<endpoint>:6379`                  | no auth token     |
| `CLOUDFRONT_KEY_ID`      | `""`                                       | empty             |
| `CLOUDFRONT_PRIVATE_KEY` | `""`                                       | empty             |
| `SAML_SP_KEY_PATH`       | `""`                                       | empty, and a path |

`SIGNED_URL_EXPIRY` was masked because its name contains `url`. Three masked an
**empty string**, printing `***` where the truth was "unset". And
`CSRF_TRUSTED_ORIGINS` printed in full holding the same value `BASE_URL` was
masked for — the contradiction was visible on one screen, in every deploy log,
for months.

**Under-masking is structurally impossible, not merely unobserved.**
`get_environment_variables` and `get_secrets` are disjoint routes off
`_collect_modules` — the `.environment` half and the `.secrets` half
(`task_definition.py`). A `[secrets] names` entry reaches the container as the
task definition's `secrets` block, resolved from SSM, and never enters the
environment map. That is this document's own 2026-01-21 decision: *"Secret
values never appear in deploy.toml, deploy script logs, or CI/CD output."* The
masking was defending against a shape the repo had already banned by ADR, and
charging nine wrong answers out of forty for the defence.

**Alternatives considered:**

- **Keep masking, fix the substring list.** Rejected: the list was 0-for-9 on
  live data while looking obviously right in review. A better list is the same
  bet — a guess about a *name* standing in for a fact about a *value* — and its
  failures are just as invisible.
- **Also abort on credential-*shaped* values at preflight.** Rejected: a
  heuristic with production stakes, where a false positive blocks a deploy, made
  of the same guesswork this decision deleted. The check that shipped is exact:
  a name is in both lists or it is not.
- **A `$NONE` sentinel for empty values instead of `(unset)`.** Rejected: a
  dotenv parser would take it literally, a shell would expand it, `set -u` would
  error on it, and it collides with a real value. All four are only problems if
  the block is machine-read, which is the point — it is not.
- **Leave the defect standing as a display-only nuisance.** Rejected: the whole
  point of printing the environment is to debug a deploy, and nine of forty
  answers were wrong in the direction that hides the truth.

**`(unset)` is a marker, not an encoding.** This block is deploy narration for a
human. Nothing in the fleet parses it — the only references to the heading
`"Global environment variables:"` anywhere are `deployer.py` and two of its own
tests. A value literally equal to `(unset)` is therefore indistinguishable from
an empty one, which is pinned as an accepted ambiguity rather than worked
around. If a pipeable dump is ever wanted it gets its own subcommand with real
quoting.

`str(raw_value)` runs before the `or`, which is what makes the marker safe: only
`""` is falsy afterwards, so `0` prints `0` and `false` prints `False`.

**The accepted cost:** values that are **sensitive but not secret** now print in
full to anyone reading a deploy log — internal hostnames, the RDS endpoint, the
Redis endpoint, bucket names. They already appeared in task definitions,
CloudWatch and the AWS console; the masking never protected them anywhere but
this one screen, and it protected them there at the price of hiding a health
check path. If any of these should be secret, the answer is to move them into
`[secrets]`, where the preflight check now guarantees they cannot also be
sitting in `[environment]`.

**The invariant is now enforced.** `check_environment_secrets_overlap` runs
unconditionally in `run_preflight_checks`, beside `check_secrets_style`, and
raises when a name appears in both `[secrets] names` and any `[environment]`
table — base, per-environment, `[services.X.environment]`, or its sub-table. A
collision is an error on its own terms whatever ECS makes of it: the deployer
emits the colliding name twice on the same container definition, once per block,
and reconciles them nowhere. The check fires before the task definition is
built, so it rests on no assumption about precedence. Blast radius measured
before shipping: havoc's three declared secrets intersect its environment tables
in nothing, so the check passed for the live fleet on day one.

**See also:** the 2026-01-21 decision on secrets via SSM references, whose
security claim this makes checkable; [PYSMELLY.md](PYSMELLY.md) §53j-4 for the
unit that applied it and the closing of the 53j arc.

______________________________________________________________________

## 2026-09-18: The accessibility surface is one static error page

**Decision:** This repo's WCAG responsibility is the CloudFront 503 page built
in `modules/cloudfront-alb/locals.tf`, nothing more. It targets WCAG 2.1 Level
AA and is checked by `make a11y` (pa11y, both environment variants). When an
environment passes `error_page_content` to the module, that page replaces the
default wholesale and its accessibility belongs to the supplying environment.

**Alternatives considered:**

- **No accessibility scope at all.** Deployer has no app UI, so the ACCESSIBILITY
  guide reads N/A at a glance. But the module really does serve HTML to end
  users of every deployed app, at exactly the moment the app is down — an
  unusually bad moment to be unreadable.
- **The full guide harness** — Docker Compose pa11y service against a running
  server, keyboard/screen-reader checklists, a testing guide. There is no server
  to run and no interactive element on the page: no links, no buttons, no form
  controls, so tab order, focus indicators and keyboard traps have no subject.

**Reasoning:** The page is static, non-interactive, and rendered from an
OpenTofu heredoc rather than served by an app, so the checkable part is markup
semantics and contrast. `bin/a11y.py` renders the heredoc the way OpenTofu does
and hands the result to pa11y, which keeps the check honest without inventing a
web-app testing apparatus around a single file. The renderer models exactly the
two template constructs the heredoc uses and fails loudly on anything else, so
the page growing a feature surfaces as a broken check rather than a silently
wrong one.

**See also:** claude-meta `best-practices/ACCESSIBILITY.md` Practice #8, which
draws the same fragment-versus-host boundary for storage-scripts' HTML emitters.

______________________________________________________________________

## Template for New Decisions

```markdown
## YYYY-MM-DD: Decision Title

**Decision:** What we decided.

**Alternatives considered:**
- Option A
- Option B

**Reasoning:** Why we chose this approach.
```
