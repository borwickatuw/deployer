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

---

## 2026-01-21: Environment Variables Belong in deploy.toml, Not tfvars

**Decision:** Environment variables are configured in `deploy.toml` (the app repo), not in OpenTofu tfvars, even though they can differ by environment.

**Alternatives considered:**
- Move all env vars to OpenTofu tfvars (like sizing)
- Split env vars between both locations based on whether they differ by environment
- Use separate tfvars variables for environment-specific app config

**Reasoning:** Environment variables are **app behavior configuration**, not sizing/capacity. They control how the application behaves, not how many resources it uses. Key distinctions:

| Aspect | Environment Variables | Sizing |
|--------|----------------------|--------|
| Who decides | App developers | Infrastructure operators |
| Controls | App behavior (debug mode, log level, feature flags) | Resource allocation (CPU, memory) |
| Change frequency | With app features | Rarely (scaling events) |
| Example | `DEBUG=true`, `LOG_LEVEL=DEBUG` | `cpu=1024`, `replicas=2` |

Developers should control their application's behavior. Moving env vars to tfvars would require infrastructure changes for every feature flag or config tweak.

---

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
2. `[environment.{env}]` - environment override
3. `[services.{name}.environment]` - service base
4. `[services.{name}.environment.{env}]` - service + environment

---

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
- `${redis_url}` - from `[redis].url`
- `${s3_media_bucket}` - from `[storage].media_bucket`
- `${aws_region}` - from AWS SDK
- `${environment}` - from environment argument
- `${iiif_server_url}` - from `[services].iiif_server_url`
- `${video_server_url}` - from `[services].video_server_url`

---

## 2026-01-21: Secrets via SSM/Secrets Manager References (Not in deploy.toml Values)

**Decision:** Secrets are stored as *references* to SSM Parameter Store or Secrets Manager paths, not as actual values. The deploy script never sees secret values.

```toml
[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"
DB_PASSWORD = "secretsmanager:myapp-db:password"
```

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

---

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

---

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

**See also:** [iam-policies/README.md](../iam-policies/README.md) for policy details

---

## 2026-01-24: ECS Fargate as Sole Deployment Target

**Decision:** Support only ECS Fargate as the deployment target. Do not add Docker Compose on EC2, Lambda, or Kubernetes support.

**Alternatives considered:**
- Docker Compose on EC2 (single-instance pattern, like Avalon's current terraform approach)
- Lambda as deployment target (serverless)
- Kubernetes/EKS (container orchestration)
- Pluggable deployment backends (abstract interface for multiple targets)

**Reasoning:**
- **ECS provides managed infrastructure**: Auto-scaling, health checks, blue-green deployments, and rolling updates without managing EC2 instances.
- **Single deployment model is maintainable**: Supporting multiple backends would multiply complexity without clear benefit for our use cases.
- **Lambda requires framework adapters**: Each framework needs special Lambda support (Django-Lambda, Rails-Lambda), different timeout constraints (15 min max), and special database connection handling (Lambda cold starts cause connection pool issues).
- **Docker Compose lacks production features**: No auto-scaling, no automatic health check recovery, single point of failure.
- **Kubernetes is overkill**: ECS provides sufficient orchestration for our use cases without Kubernetes complexity.

**Note:** Lambda is supported for *helper utilities* (e.g., `staging-scheduler` module for turning services on/off), just not as a primary deployment target.

**See also:** [SUPPORTED-ARCHITECTURES.md](SUPPORTED-ARCHITECTURES.md)

---

## 2026-01-24: Framework-Agnostic Commands via [commands] Section

**Decision:** Remove Django assumptions by adding a `[commands]` section to deploy.toml for framework-specific CLI commands.

**Alternatives considered:**
- Keep Django-specific `manage` command, add separate commands for other frameworks
- Auto-detect framework from Dockerfile or dependencies
- Require each framework to implement a standard interface (e.g., always use `./run.sh migrate`)

**Reasoning:**
- **Explicit over implicit**: Configuration-driven commands are clearer than auto-detection magic.
- **Framework flexibility**: Works for Django, Rails, Node.js, or any framework with CLI commands.
- **Backward compatible**: If no `[commands]` section exists, fall back to Django defaults.
- **Simple model**: Named commands map to arrays of command arguments - no complex DSL.

**Example usage:**

```toml
# Django
[commands]
migrate = ["python", "manage.py", "migrate"]
shell = ["python", "manage.py", "shell"]

# Rails
[commands]
migrate = ["bundle", "exec", "rake", "db:migrate"]
console = ["bundle", "exec", "rails", "console"]
```

---

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

---

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

| Value | Canonical Location | Rationale |
|-------|-------------------|-----------|
| ECR prefix | config.toml `[infrastructure]` | Created by Terraform |
| Service sizing (cpu, memory) | terraform.tfvars | Infrastructure capacity |
| Docker commands | deploy.toml `[services]` | App behavior |
| Environment variables | deploy.toml `[environment]` | App configuration |
| Secrets references | deploy.toml `[secrets]` | App needs (paths, not values) |

---

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

**See also:** [MODULES.md](MODULES.md), [DESIGN.md](DESIGN.md)

---

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

---

## Template for New Decisions

```markdown
## YYYY-MM-DD: Decision Title

**Decision:** What we decided.

**Alternatives considered:**
- Option A
- Option B

**Reasoning:** Why we chose this approach.
```
