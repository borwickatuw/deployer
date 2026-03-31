# Configuration Reference

Complete reference for deployment configuration.

## Quick Reference

### Three Config Files

| File               | Location              | Purpose                                      | When to Edit                                     |
| ------------------ | --------------------- | -------------------------------------------- | ------------------------------------------------ |
| `deploy.toml`      | App repository        | What to run: images, commands, env vars      | Adding services, changing commands, new env vars |
| `terraform.tfvars` | Environment directory | How big: cpu, memory, replicas, scaling      | Resizing services, changing capacity             |
| `config.toml`      | Environment directory | Infrastructure glue: connects deploy to tofu | Rarely (auto-generated)                          |

### Common Commands

```bash
# Link environment to deploy.toml (one-time setup)
uv run python bin/link-environments.py myapp-staging ~/code/myapp/deploy.toml

# Deploy to staging (uses linked deploy.toml)
uv run python bin/deploy.py myapp-staging

# Dry-run (show what would happen)
uv run python bin/deploy.py myapp-staging --dry-run

# Infrastructure changes
./bin/tofu.sh plan myapp-staging
./bin/tofu.sh apply myapp-staging

# View logs
aws logs tail /ecs/myapp-staging --follow

# Run commands (uses linked deploy.toml)
uv run python bin/ecs-run.py run myapp-staging migrate

# List available commands
uv run python bin/ecs-run.py run myapp-staging --list-commands
```

### Required OpenTofu Outputs

These outputs must be defined in your environment's OpenTofu configuration for the deploy script to work:

| Output                   | Description               | Used For                |
| ------------------------ | ------------------------- | ----------------------- |
| `ecs_cluster_name`       | ECS cluster name          | Service deployment      |
| `ecs_execution_role_arn` | Task execution role       | Pulling images, secrets |
| `ecs_task_role_arn`      | Task role                 | Application AWS access  |
| `ecs_security_group_id`  | Security group ID         | Network configuration   |
| `private_subnet_ids`     | List of subnet IDs        | Task placement          |
| `ecr_prefix`             | ECR repository prefix     | Image naming            |
| `database_url`           | PostgreSQL connection URL | Application config      |

______________________________________________________________________

## Path Resolution

All paths in `deploy.toml` are resolved relative to the configuration file location:

- `source = "."` - The `source` path is relative to where deploy.toml is located
- `context = "."` - The build context is relative to the `source` directory
- `dockerfile = "subdir/Dockerfile"` - The Dockerfile path is relative to the build context

**Example directory structure:**

```
my-app/
├── deploy.toml           # Configuration file
├── Dockerfile            # Main Dockerfile
├── src/
│   └── ...
└── services/
    └── worker/
        └── Dockerfile    # Worker-specific Dockerfile
```

**Corresponding deploy.toml:**

```toml
[application]
source = "."              # Same directory as deploy.toml

[images.web]
context = "."             # Same as source (my-app/)
dockerfile = "Dockerfile" # my-app/Dockerfile

[images.worker]
context = "services/worker"    # my-app/services/worker/
dockerfile = "Dockerfile"      # my-app/services/worker/Dockerfile
```

**Common mistake:** If your app source is in a subdirectory, set `source` appropriately:

```toml
# If deploy.toml is at repo root but app code is in ./myapp/
[application]
source = "myapp"

[images.web]
context = "."  # Relative to source, so this is ./myapp/
```

______________________________________________________________________

## Overview

Configuration is split between three locations:

| Configuration         | Location                      | Purpose                                  |
| --------------------- | ----------------------------- | ---------------------------------------- |
| **App structure**     | `deploy.toml` (app repo)      | What to run: images, commands, env vars  |
| **Sizing & capacity** | `terraform.tfvars` (deployer) | How big: cpu, memory, replicas, scaling  |
| **Deployment glue**   | `config.toml` (deployer env)  | Infrastructure references for deployment |

The `config.toml` in each environment directory bridges the gap between OpenTofu outputs and the deploy script. It uses `${tofu:...}` placeholders that are resolved at deploy time.

See [DESIGN.md](background/DESIGN.md) for the philosophy behind this separation.

______________________________________________________________________

## deploy.toml Reference

The `deploy.toml` file lives in your application repository and defines the application's structure. It does **not** contain sizing (cpu, memory, replicas) - those are in OpenTofu tfvars.

### `[application]`

**Required.** Basic application metadata.

| Field         | Type   | Required | Description                                                                              |
| ------------- | ------ | -------- | ---------------------------------------------------------------------------------------- |
| `name`        | string | Yes      | Application name. Used for ECS cluster naming (`{name}-{environment}-cluster`).          |
| `description` | string | No       | Human-readable description.                                                              |
| `source`      | string | Yes      | Path to source code. Relative to config file or absolute. Use `.` for same directory.    |

**Example:**

```toml
[application]
name = "myapp"
description = "My web application"
source = "."
```

### `[images.*]`

**Required.** Define Docker images to build and push.

Each image is defined as a subsection: `[images.web]`, `[images.worker]`, etc.

| Field        | Type    | Required | Default      | Description                                                        |
| ------------ | ------- | -------- | ------------ | ------------------------------------------------------------------ |
| `context`    | string  | Yes      | -            | Build context path relative to `source`.                           |
| `dockerfile` | string  | No       | `Dockerfile` | Dockerfile path relative to `context`.                             |
| `target`     | string  | No       | -            | Docker build target for multi-stage builds.                        |
| `depends_on` | array   | No       | `[]`         | List of image names that must be built before this one.            |
| `push`       | boolean | No       | `true`       | Whether to push to ECR. Set to `false` for local-only base images. |

**Example:**

```toml
[images.web]
context = "."
dockerfile = "Dockerfile"

[images.worker]
context = "worker"
dockerfile = "Dockerfile.worker"
```

The deploy script builds each image and pushes to ECR as:
`{account}.dkr.ecr.{region}.amazonaws.com/{ecr_prefix}-{image_name}:latest`

#### Image Dependencies

When your Dockerfiles use local base images (e.g., `FROM myapp-base`), use `depends_on` and `push` to control build order:

```toml
# Base image - built first, not pushed to ECR
[images.myapp-base]
context = "."
dockerfile = "docker/myapp-base"
push = false

# Main image - depends on base, pushed to ECR
[images.web]
context = "."
dockerfile = "docker/myapp"
depends_on = ["myapp-base"]
```

**How it works:**

1. Images are sorted topologically based on `depends_on`
1. Images with `push = false` are tagged locally as `{image_name}:latest` (e.g., `myapp-base:latest`)
1. Images with `push = true` (default) are tagged as `{ecr_prefix}-{image_name}:latest` and pushed to ECR

This allows Dockerfiles to use `FROM myapp-base` to inherit from local base images.

**Note:** The image key name (e.g., `myapp-base` in `[images.myapp-base]`) becomes the local tag name. Your Dockerfile's `FROM` statement must match this name.

#### Multi-Stage Build Targets

For Dockerfiles with multiple stages, use the `target` field to specify which stage to build.

**Same target for all environments:**

```toml
[images.web]
context = "."
dockerfile = "Dockerfile"
target = "prod"
```

**Different targets per environment:**

```toml
[images.web]
context = "."
dockerfile = "Dockerfile"

# Environment-specific targets
[images.web.target]
staging = "dev"
production = "prod"
```

**Example Dockerfile:**

```dockerfile
FROM python:3.12-slim AS base
# ... base setup ...

FROM base AS dev
RUN uv sync --frozen --group dev
# dev dependencies included (debug toolbar, etc.)

FROM base AS prod
RUN uv sync --frozen --no-dev
# production only
```

With the environment-specific configuration above:

- **Staging** builds the `dev` stage (includes dev dependencies)
- **Production** builds the `prod` stage (minimal production image)

The target affects the content hash, so changing targets triggers a rebuild.

### `[services.*]`

**Required.** Define ECS services to deploy.

Each service is defined as a subsection: `[services.web]`, `[services.celery]`, etc.

**Note:** Sizing fields (`cpu`, `memory`, `replicas`, `load_balanced`) are configured in OpenTofu tfvars, not here.

| Field               | Type    | Required | Description                                                          |
| ------------------- | ------- | -------- | -------------------------------------------------------------------- |
| `image`             | string  | Yes      | Image name (references `[images.*]`).                                |
| `command`           | array   | No       | Container command override.                                          |
| `port`              | integer | No       | Container port (for load-balanced services).                         |
| `health_check_path` | string  | No       | ALB health check endpoint.                                           |
| `path_pattern`      | string  | No       | ALB path-based routing pattern (e.g., `/api/*`).                     |
| `min_cpu`           | integer | No       | Minimum CPU units required. Deploy fails if environment sets less.   |
| `min_memory`        | integer | No       | Minimum memory (MB) required. Deploy fails if environment sets less. |
| `interruptible`     | boolean | No       | Service tolerates interruption. Enables Fargate Spot when infrastructure uses it. Default: `false`. |

**Examples:**

```toml
# Web service
[services.web]
image = "web"
port = 8000
command = ["gunicorn", "app:application", "--bind", "0.0.0.0:8000"]
health_check_path = "/health/"

# Background worker (no ALB)
[services.celery]
image = "web"
command = ["celery", "-A", "app", "worker", "--loglevel=info"]

# API server with path-based routing
[services.api]
image = "api"
port = 8080
health_check_path = "/health"
path_pattern = "/api/*"

# Resource-intensive service with minimum requirements
[services.transcoder]
image = "transcoder"
min_cpu = 512      # Deployment fails if environment sets cpu < 512
min_memory = 1024  # Deployment fails if environment sets memory < 1024
```

### `[environment]`

**Optional.** Environment variables passed to all services.

Values can be:

- **Static strings**: `DEBUG = "false"`
- **Placeholders**: `DATABASE_URL = "${database_url}"` (resolved at deploy time)

#### Environment-Specific Overrides

Use `[environment.staging]` and `[environment.production]` sections to override base values per environment:

```toml
# Base - applies to all environments
[environment]
DJANGO_SETTINGS_MODULE = "myapp.settings"
ALLOWED_HOSTS = "*"
DATABASE_URL = "${database_url}"

# Staging overrides
[environment.staging]
DEBUG = "true"
LOG_LEVEL = "DEBUG"

# Production overrides
[environment.production]
DEBUG = "false"
LOG_LEVEL = "INFO"
```

**Merge order** (later values override earlier):

1. `[environment]` - base values
1. `[environment.{env}]` - environment-specific overrides

The environment name comes from the first argument passed to the deploy script (e.g., `uv run python bin/deploy.py myapp-staging`).

#### Service-Specific Environment Variables

Services can have their own environment variables that override the global ones:

```toml
[environment]
LOG_LEVEL = "INFO"

# Celery workers need different concurrency
[services.celery.environment]
CELERY_CONCURRENCY = "4"

# Staging celery uses fewer workers
[services.celery.environment.staging]
CELERY_CONCURRENCY = "2"
```

**Full merge order** (for a service in a specific environment):

1. `[environment]` - global base
1. `[environment.{env}]` - global environment override
1. `[services.{name}.environment]` - service-specific base
1. `[services.{name}.environment.{env}]` - service + environment override

#### Available Placeholders

These placeholders are resolved at deploy time from the environment's `config.toml`:

| Placeholder          | Source (config.toml)     | Description                                 |
| -------------------- | ------------------------ | ------------------------------------------- |
| `${database_url}`    | `[database].url`         | PostgreSQL connection URL                   |
| `${redis_url}`       | `[cache].url`            | Redis connection URL                        |
| `${s3_media_bucket}` | `[storage].media_bucket` | S3 bucket name                              |
| `${aws_region}`      | AWS SDK                  | Current AWS region                          |
| `${environment}`     | `environment` argument   | Deployment environment (staging/production) |

For service URL references like `${services.api.url}`, see [Modules](modules/README.md#service-url-references).

**Example:**

```toml
[environment]
ALLOWED_HOSTS = "*"
DATABASE_URL = "${database_url}"
REDIS_URL = "${redis_url}"
AWS_STORAGE_BUCKET_NAME = "${s3_media_bucket}"
AWS_REGION = "${aws_region}"

[environment.staging]
DEBUG = "true"

[environment.production]
DEBUG = "false"
```

### `[secrets]`

**Optional.** References to secrets in SSM Parameter Store or Secrets Manager.

Secrets are injected into containers at runtime. The deploy script never sees secret values.

#### SSM Parameter Store

Format: `ssm:/path/to/parameter`

```toml
[secrets]
SECRET_KEY = "ssm:/myapp/staging/secret-key"
API_KEY = "ssm:/myapp/staging/external-api-key"
```

#### Secrets Manager

Format: `secretsmanager:secret-name:json-key`

```toml
[secrets]
DB_PASSWORD = "secretsmanager:myapp-staging-db:password"
DB_USERNAME = "secretsmanager:myapp-staging-db:username"
```

#### Environment Substitution

Use `${environment}` in paths to share config across environments:

```toml
[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"
# Resolves to:
#   staging:    ssm:/myapp/staging/secret-key
#   production: ssm:/myapp/production/secret-key
```

### `[commands]`

**Required for ecs-run.py.** Framework-agnostic command definitions for use with `ecs-run.py run`.

This section defines named commands that can be run in ECS containers, making the deployer framework-agnostic.

Commands come in two forms:

| Form                  | Type  | Description                                                  |
| --------------------- | ----- | ------------------------------------------------------------ |
| `<name>` (simple)    | array | Command and arguments as a list of strings                   |
| `[commands.<name>]`  | table | Command with metadata: `command` (array) and `ddl` (boolean) |

Commands with `ddl = true` require extra confirmation in ecs-run.py because they modify database schema.

**Important:** Only include non-interactive commands. Interactive commands (shell, dbshell, createsuperuser) cannot run via ecs-run.py since there's no TTY attached.

**Example (Django):**

```toml
[commands]
showmigrations = ["python", "manage.py", "showmigrations"]
collectstatic = ["python", "manage.py", "collectstatic", "--noinput"]
check = ["python", "manage.py", "check", "--deploy"]

[commands.migrate]
command = ["python", "manage.py", "migrate"]
ddl = true

[commands.makemigrations]
command = ["python", "manage.py", "makemigrations"]
ddl = true
```

**Example (Rails):**

```toml
[commands]
migrate = ["bundle", "exec", "rake", "db:migrate"]
assets = ["bundle", "exec", "rake", "assets:precompile"]
```

**Example (Node.js):**

```toml
[commands]
migrate = ["npm", "run", "migrate"]
seed = ["npm", "run", "seed"]
```

**Usage:**

```bash
# Link environment to deploy.toml (one-time, stored in local/environments.toml)
python bin/link-environments.py myapp-staging ~/code/myapp/deploy.toml

# List available commands (uses linked deploy.toml)
python bin/ecs-run.py run myapp-staging --list-commands

# Run a named command
python bin/ecs-run.py run myapp-staging migrate
```

### `[database]`

**Optional.** Declares database requirements. The environment's `config.toml` provides the actual connection details.

| Field        | Type   | Required | Description                                                                                                                       |
| ------------ | ------ | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `type`       | string | Yes      | Database type: `"postgresql"`.                                                                                                    |
| `extensions` | array  | No       | PostgreSQL extensions to create before migrations (e.g., `["unaccent", "pg_bigm"]`). Requires `extensions_lambda` in config.toml. |

Extensions listed here are created via a Lambda function that connects as the RDS master user (which has `rds_superuser` privileges). This is necessary because the migrate user cannot create extensions like `pg_bigm` that require superuser.

**Example:**

```toml
[database]
type = "postgresql"
extensions = ["unaccent", "pg_bigm"]
```

The deploy script invokes the Lambda **before** running migrations, so extensions are available for any migration that depends on them.

### `[migrations]`

**Optional.** Database migration configuration.

Migrations run as a one-off ECS task before updating services.

| Field     | Type    | Required         | Default | Description                                  |
| --------- | ------- | ---------------- | ------- | -------------------------------------------- |
| `enabled` | boolean | No               | false   | Whether to run migrations.                   |
| `service` | string  | No               | `web`   | Which service's image to use for migrations. |
| `command` | array   | Yes (if enabled) | -       | Migration command.                           |

**Example:**

```toml
[migrations]
enabled = true
service = "web"
command = ["python", "manage.py", "migrate"]
```

For Django with uv:

```toml
[migrations]
enabled = true
service = "web"
command = ["uv", "run", "python", "manage.py", "migrate"]
```

### `[audit]`

**Optional.** Configuration for `bin/ops.py audit`, which compares your `docker-compose.yml` (local dev) against `deploy.toml` (production) to catch drift.

| Field             | Type   | Description                                                        |
| ----------------- | ------ | ------------------------------------------------------------------ |
| `service_mapping` | table  | Maps docker-compose service names to deploy.toml names             |
| `ignore_services` | array  | Services to skip entirely (dev-only infrastructure like MinIO)     |
| `ignore_env_vars` | array  | Env vars expected to differ between dev and prod                   |
| `ignore_images`   | set    | Images to skip in build context comparison                         |

**Auto-ignored env vars:** The audit automatically ignores variables that are either injected by resource modules (`DB_HOST`, `REDIS_URL`, `S3_*_BUCKET`, secret names) or are common dev-only vars:

- `DEBUG`, `PYTHONUNBUFFERED`, `UV_LINK_MODE`, `JAVA_OPTS`
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — ECS Fargate provides credentials via the task role
- `AWS_S3_ENDPOINT_URL` — only needed for local S3-compatible services (MinIO, LocalStack)

**Auto-ignored services:** Infrastructure services (`postgres`, `redis`, `mysql`, `mongo`, `memcached`) are always ignored.

**Example:**

```toml
[audit]
service_mapping = { "myapp" = "web", "celery-worker" = "celery" }
ignore_services = ["minio", "minio-init"]
ignore_env_vars = [
    "SOME_DEV_ONLY_VAR",   # Only used in local development
]
```

______________________________________________________________________

## Environment config.toml Reference

Each environment directory contains a `config.toml` that provides deployment configuration. Values can use `${tofu:output_name}` placeholders that are resolved at deploy time by fetching OpenTofu outputs.

**Example files:**

- Standalone environments: [templates/standalone-staging/config.toml.example](../templates/standalone-staging/config.toml.example)
- Shared environments: [templates/shared-app-staging/config.toml.example](../templates/shared-app-staging/config.toml.example)

> **Maintainer note:** When updating the config.toml structure, update both this documentation and the template files in `templates/`.

### Location

```
environments/
├── myapp-staging/
│   ├── config.toml        # Deployment configuration
│   ├── main.tf
│   └── terraform.tfvars
└── myapp-production/
    ├── config.toml
    └── ...
```

### Structure

```toml
# environments/myapp-staging/config.toml

[environment]
type = "staging"  # or "production"
domain_name = "${tofu:domain_name}"  # For constructing URLs

[aws]
deploy_profile = "deployer-app"      # for deploy.py
infra_profile = "deployer-infra"     # for tofu.sh
cognito_profile = "deployer-cognito" # for cognito.py

[infrastructure]
cluster_name = "${tofu:ecs_cluster_name}"
security_group_id = "${tofu:ecs_security_group_id}"
private_subnet_ids = "${tofu:private_subnet_ids}"
execution_role_arn = "${tofu:ecs_execution_role_arn}"
task_role_arn = "${tofu:ecs_task_role_arn}"
target_group_arn = "${tofu:alb_target_group_arn}"
service_target_groups = "${tofu:service_target_groups}"
service_discovery_registries = "${tofu:service_discovery_registries}"
service_discovery_namespace = "${tofu:service_discovery_namespace_name}"
alb_dns_name = "${tofu:alb_dns_name}"  # Fallback URL
rds_instance_id = "${tofu:rds_instance_id}"  # Staging only - for start/stop
ecr_prefix = "${tofu:ecr_prefix}"

[services]
config = "${tofu:service_config}"
scaling = "${tofu:scaling_config}"
health_check = "${tofu:health_check_config}"

[database]
host = "${tofu:db_host}"
port = "${tofu:db_port}"
name = "${tofu:db_name}"
credentials = "secretsmanager"
# App credentials (DML only - for runtime services)
app_username_secret = "${tofu:db_app_username_secret_arn}"
app_password_secret = "${tofu:db_app_password_secret_arn}"
# Migrate credentials (DDL + DML - for migrations only)
migrate_username_secret = "${tofu:db_migrate_username_secret_arn}"
migrate_password_secret = "${tofu:db_migrate_password_secret_arn}"

[cache]
url = "${tofu:redis_url}"

# [storage]  # Uncomment if app uses S3
# media_bucket = "${tofu:s3_media_bucket}"

[cognito]
enabled = true  # or false for production
user_pool_id = "${tofu:cognito_user_pool_id}"
client_id = "${tofu:cognito_user_pool_client_id}"
# Test account for automated access (staging only)
test_username = "deployer@test.local"
test_password_ssm = "/deployer/myapp-staging/cognito-test-password"
```

### Sections

#### `[environment]`

| Field         | Type   | Description                                                  |
| ------------- | ------ | ------------------------------------------------------------ |
| `type`        | string | Environment type: `"staging"` or `"production"`              |
| `domain_name` | string | Domain name for this environment (e.g., `staging.myapp.com`) |

#### `[aws]`

AWS profile configuration. Each script reads the appropriate profile for its operation.

| Field             | Used By      | Description                                           |
| ----------------- | ------------ | ----------------------------------------------------- |
| `deploy_profile`  | `deploy.py`  | AWS profile for deployment operations (ECS, ECR, SSM) |
| `infra_profile`   | `tofu.sh`    | AWS profile for infrastructure operations (OpenTofu)  |
| `cognito_profile` | `cognito.py` | AWS profile for Cognito user management               |

**Example:**

```toml
[aws]
deploy_profile = "deployer-app"      # for deploy.py
infra_profile = "deployer-infra"     # for tofu.sh
cognito_profile = "deployer-cognito" # for cognito.py
```

Scripts automatically read the appropriate profile. You can override with `AWS_PROFILE=...` if needed.

For multi-account setups (e.g., staging and production in different AWS accounts), use different profiles per environment. See [MULTIPLE-ACCOUNTS.md](operations/MULTIPLE-ACCOUNTS.md) for detailed setup instructions.

#### `[infrastructure]`

Core ECS infrastructure references.

| Field                            | Tofu Output                        | Description                                                 |
| -------------------------------- | ---------------------------------- | ----------------------------------------------------------- |
| `cluster_name`                   | `ecs_cluster_name`                 | ECS cluster name                                            |
| `security_group_id`              | `ecs_security_group_id`            | Security group for ECS tasks                                |
| `private_subnet_ids`             | `private_subnet_ids`               | List of private subnet IDs                                  |
| `execution_role_arn`             | `ecs_execution_role_arn`           | ECS task execution role ARN                                 |
| `task_role_arn`                  | `ecs_task_role_arn`                | ECS task role ARN                                           |
| `target_group_arn`               | `alb_target_group_arn`             | ALB target group ARN (default)                              |
| `service_target_groups`          | `service_target_groups`            | Map of service name to target group ARN (for path routing)  |
| `service_discovery_registries`   | `service_discovery_registries`     | Map of service name to discovery registry ARN (optional)    |
| `service_discovery_namespace`    | `service_discovery_namespace_name` | Service discovery namespace name (optional)                 |
| `alb_dns_name`                   | `alb_dns_name`                     | ALB DNS name (fallback URL)                                 |
| `rds_instance_id`                | `rds_instance_id`                  | RDS instance ID for start/stop (staging only)               |
| `ecr_prefix`                     | `ecr_prefix`                       | ECR repository prefix for image naming                      |

#### `[services]`

Service configuration from OpenTofu.

| Field          | Tofu Output           | Description                                        |
| -------------- | --------------------- | -------------------------------------------------- |
| `config`       | `service_config`      | JSON map of service sizing (cpu, memory, replicas) |
| `scaling`      | `scaling_config`      | JSON map of auto-scaling config                    |
| `health_check` | `health_check_config` | JSON health check defaults                         |

#### `[database]`

The database module uses a **two-account model** for security:

- **App credentials**: Used by runtime services. The app user has DML privileges only (SELECT, INSERT, UPDATE, DELETE).
- **Migrate credentials**: Used by migrations. The migrate user has DDL privileges (CREATE, ALTER, DROP tables).

This reduces blast radius if the application is compromised - attackers cannot drop tables or alter schema.

| Field                     | Tofu Output                      | Description                                                                                             |
| ------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `host`                    | `db_host`                        | Database hostname                                                                                       |
| `port`                    | `db_port`                        | Database port (default: 5432)                                                                           |
| `name`                    | `db_name`                        | Database name                                                                                           |
| `credentials`             | -                                | Credential source: `secretsmanager` or `ssm`                                                            |
| `app_username_secret`     | `db_app_username_secret_arn`     | App user username ARN (DML only)                                                                        |
| `app_password_secret`     | `db_app_password_secret_arn`     | App user password ARN                                                                                   |
| `migrate_username_secret` | `db_migrate_username_secret_arn` | Migrate user username ARN (DDL + DML)                                                                   |
| `migrate_password_secret` | `db_migrate_password_secret_arn` | Migrate user password ARN                                                                               |
| `extensions_lambda`       | `db_users_lambda_function_name`  | Lambda function name for creating PostgreSQL extensions. Required if deploy.toml declares `extensions`. |

When running `ecs-run.py run <env> migrate`, the migrate task definition is used automatically, which has the migrate credentials.

#### `[cache]`

| Field | Tofu Output | Description          |
| ----- | ----------- | -------------------- |
| `url` | `redis_url` | Redis connection URL |

#### `[storage]`

Optional storage configuration.

| Field          | Tofu Output       | Description                    |
| -------------- | ----------------- | ------------------------------ |
| `media_bucket` | `s3_media_bucket` | S3 bucket name for media files |

#### `[cognito]`

Cognito authentication configuration. Required for staging environments with Cognito protection.

| Field               | Type    | Description                         |
| ------------------- | ------- | ----------------------------------- |
| `enabled`           | boolean | Whether Cognito auth is enabled     |
| `user_pool_id`      | string  | Cognito user pool ID (from tofu)    |
| `client_id`         | string  | Cognito client ID (from tofu)       |
| `test_username`     | string  | Username for automated test account |
| `test_password_ssm` | string  | SSM path to test account password   |

#### `[deployment]`

ECS deployment configuration. Optional - controls how ECS deploys new task revisions.

**Important:** These settings significantly impact deployment behavior. Staging environments can use aggressive settings for faster deployments. Production environments should use conservative defaults (or omit this section entirely).

| Field                      | Type    | Default | Description                                                                                                                |
| -------------------------- | ------- | ------- | -------------------------------------------------------------------------------------------------------------------------- |
| `minimum_healthy_percent`  | number  | 100     | Minimum percentage of healthy tasks to maintain during deployment. Use 0 for staging (faster), 100 for production (safer). |
| `maximum_percent`          | number  | 200     | Maximum percentage of tasks during deployment. Use 100 for staging (no extra capacity), 200 for production (rolling).      |
| `circuit_breaker_enabled`  | boolean | false   | Enable deployment circuit breaker for faster failure detection.                                                            |
| `circuit_breaker_rollback` | boolean | true    | Automatically rollback on deployment failure (requires circuit breaker).                                                   |

**Staging example (faster deployments):**

```toml
[deployment]
minimum_healthy_percent = 0     # Allow 0 running tasks during deployment
maximum_percent = 100           # Don't run extra capacity
circuit_breaker_enabled = true  # Fast-fail on errors
circuit_breaker_rollback = true # Auto-rollback on failure
```

**Production example (safe deployments):**

```toml
# Omit [deployment] section entirely to use safe defaults:
# minimum_healthy_percent = 100 (always maintain availability)
# maximum_percent = 200 (allow rolling deployments)
# circuit_breaker_enabled = false
```

### Placeholder Resolution

The `${tofu:output_name}` syntax tells the deploy script to run `tofu output -json output_name` (or `-raw` for simple values) in the environment directory and substitute the result.

**Complex types** (lists, maps) are preserved as Python objects when the entire value is a placeholder:

```toml
private_subnet_ids = "${tofu:private_subnet_ids}"  # Returns a list
```

**Embedded placeholders** are converted to strings:

```toml
connection = "host=${tofu:db_host} port=5432"  # String interpolation
```

______________________________________________________________________

## Resolved Config JSON Reference

The resolved config JSON is a pre-resolved version of `config.toml` used by `ci-deploy` for CI/CD pipelines. It is produced by `bin/resolve-config.py` and contains all `${tofu:...}` placeholders already resolved to their values.

### Format

```json
{
  "_meta": {
    "environment": "myapp-staging",
    "environment_type": "staging",
    "resolved_at": "2026-02-17T12:00:00+00:00",
    "config_toml_hash": "sha256:abc123...",
    "tofu_outputs_hash": "sha256:def456..."
  },
  "infrastructure": {
    "cluster_name": "myapp-staging-cluster",
    "ecr_prefix": "myapp",
    "execution_role_arn": "arn:aws:iam::123:role/myapp-staging-ecs-execution",
    "task_role_arn": "arn:aws:iam::123:role/myapp-staging-ecs-task",
    "security_group_id": "sg-abc123",
    "private_subnet_ids": ["subnet-1", "subnet-2"]
  }
}
```

### `_meta` Block

| Field               | Description                                       |
| ------------------- | ------------------------------------------------- |
| `environment`       | Environment name (e.g., "myapp-staging")          |
| `environment_type`  | "staging" or "production"                         |
| `resolved_at`       | ISO 8601 timestamp when the config was resolved   |
| `config_toml_hash`  | SHA-256 hash of the raw config.toml content       |
| `tofu_outputs_hash` | SHA-256 hash of the tofu output JSON              |

The `_meta` block is stripped before passing the config to the Deployer. It is used only for staleness detection and display.

### Generation

```bash
# Resolve to stdout
uv run python bin/resolve-config.py myapp-staging

# Resolve to file
uv run python bin/resolve-config.py myapp-staging --output resolved.json

# Resolve and push to S3
uv run python bin/resolve-config.py myapp-staging --push-s3

# Verify freshness
uv run python bin/resolve-config.py myapp-staging --verify --verify-file resolved.json
```

The resolved config is also automatically pushed to S3 after every successful `tofu.sh apply`.

______________________________________________________________________

## OpenTofu tfvars Reference

Service sizing and scaling are configured in `terraform.tfvars` files in the deployer repository, one per environment.

### `services` Variable

Map of service configurations. Each service needs sizing information.

| Field                  | Type   | Required | Description                                                              |
| ---------------------- | ------ | -------- | ------------------------------------------------------------------------ |
| `cpu`                  | number | Yes      | CPU units (256 = 0.25 vCPU, 1024 = 1 vCPU).                              |
| `memory`               | number | Yes      | Memory in MB. Must be compatible with CPU.                               |
| `replicas`             | number | Yes      | Desired task count.                                                      |
| `load_balanced`        | bool   | Yes      | Whether to receive traffic from ALB.                                     |
| `port`                 | number | No       | Container port. Required if `load_balanced = true`.                      |
| `health_check_path`    | string | No       | ALB health check path. Default: `/`.                                     |
| `path_pattern`         | string | No       | ALB path-based routing pattern (auto-creates target group + rule).       |
| `health_check_matcher` | string | No       | HTTP status codes for healthy response (e.g., `"200-499"`). Default: 200 |
| `service_discovery`    | bool   | No       | Register with Cloud Map for service-to-service communication.            |

#### CPU/Memory Combinations

Fargate requires specific CPU/memory combinations:

| CPU (units) | Memory (MB) options                                                                 |
| ----------- | ----------------------------------------------------------------------------------- |
| 256         | 512, 1024, 2048                                                                     |
| 512         | 1024, 2048, 3072, 4096                                                              |
| 1024        | 2048, 3072, 4096, 5120, 6144, 7168, 8192                                            |
| 2048        | 4096, 5120, 6144, 7168, 8192, 9216, 10240, 11264, 12288, 13312, 14336, 15360, 16384 |
| 4096        | 8192 - 30720 (in 1024 increments)                                                   |

**Example (staging - minimal):**

```hcl
services = {
  web = {
    cpu               = 256
    memory            = 512
    replicas          = 1
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
  celery = {
    cpu           = 256
    memory        = 512
    replicas      = 1
    load_balanced = false
  }
}
```

**Example (production - larger):**

```hcl
services = {
  web = {
    cpu               = 1024
    memory            = 2048
    replicas          = 2
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
  celery = {
    cpu           = 512
    memory        = 1024
    replicas      = 2
    load_balanced = false
  }
}
```

### `scaling` Variable

Map of auto-scaling configurations. Only define for services that should auto-scale.

| Field          | Type   | Required | Description                                     |
| -------------- | ------ | -------- | ----------------------------------------------- |
| `min_replicas` | number | Yes      | Minimum task count.                             |
| `max_replicas` | number | Yes      | Maximum task count.                             |
| `cpu_target`   | number | No       | Target CPU utilization percentage. Default: 70. |

**Example:**

```hcl
# Staging - no auto-scaling
scaling = {}

# Production - auto-scale web and api
scaling = {
  web = {
    min_replicas = 2
    max_replicas = 10
    cpu_target   = 70
  }
  api = {
    min_replicas = 2
    max_replicas = 8
    cpu_target   = 80
  }
}
```

### `health_check` Variable

Global health check defaults. Optional - uses sensible defaults if not specified.

| Field                 | Type   | Default | Description                                 |
| --------------------- | ------ | ------- | ------------------------------------------- |
| `interval`            | number | 30      | Seconds between health checks.              |
| `timeout`             | number | 10      | Seconds to wait for a response.             |
| `healthy_threshold`   | number | 2       | Consecutive successes to consider healthy.  |
| `unhealthy_threshold` | number | 5       | Consecutive failures to consider unhealthy. |

**Example:**

```hcl
health_check = {
  interval            = 30
  timeout             = 10
  healthy_threshold   = 2
  unhealthy_threshold = 5
}
```

______________________________________________________________________

## Complete Examples

### deploy.toml (in app repo)

```toml
[application]
name = "myapp"
description = "My web application"
source = "."

[images.web]
context = "."
dockerfile = "Dockerfile"

[services.web]
image = "web"
port = 8000
command = ["gunicorn", "app:application", "--bind", "0.0.0.0:8000"]
health_check_path = "/health/"

[services.celery]
image = "web"
command = ["celery", "-A", "app", "worker", "--loglevel=info"]

# Base environment variables (all environments)
[environment]
DJANGO_SETTINGS_MODULE = "myapp.settings"
ALLOWED_HOSTS = "*"
DATABASE_URL = "${database_url}"
REDIS_URL = "${redis_url}"

# Staging-specific overrides
[environment.staging]
DEBUG = "true"

# Production-specific overrides
[environment.production]
DEBUG = "false"

[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"

[migrations]
enabled = true
service = "web"
command = ["python", "manage.py", "migrate"]
```

### terraform.tfvars (staging)

```hcl
project_name = "myapp"
db_username  = "myapp_admin"
db_password  = "CHANGE_THIS"

services = {
  web = {
    cpu               = 256
    memory            = 512
    replicas          = 1
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
  celery = {
    cpu           = 256
    memory        = 512
    replicas      = 1
    load_balanced = false
  }
}

scaling = {}
```

### terraform.tfvars (production)

```hcl
project_name = "myapp"
db_username  = "myapp_admin"
db_password  = "CHANGE_THIS"

services = {
  web = {
    cpu               = 1024
    memory            = 2048
    replicas          = 2
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
  celery = {
    cpu           = 512
    memory        = 1024
    replicas      = 2
    load_balanced = false
  }
}

scaling = {
  web = {
    min_replicas = 2
    max_replicas = 10
    cpu_target   = 70
  }
}
```
