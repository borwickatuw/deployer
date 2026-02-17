# Resource Module System

The deployer uses a module system to separate **what an application needs** from **how an environment provides it**.

## Overview

Applications declare their needs declaratively in `deploy.toml`:

```toml
[database]
type = "postgresql"

[cache]
type = "redis"

[secrets]
names = ["SECRET_KEY", "SIGNED_URL_SECRET"]
```

Environments provide implementation details in `config.toml`:

```toml
[database]
host = "${tofu:db_host}"
port = "${tofu:db_port}"
name = "${tofu:db_name}"
credentials = "secretsmanager"
username_secret = "${tofu:db_username_secret_arn}"
password_secret = "${tofu:db_password_secret_arn}"

[secrets]
provider = "ssm"
path_prefix = "/myapp/staging"
```

## Benefits

- **Applications are infrastructure-agnostic**: Apps don't know if credentials come from SSM, Secrets Manager, or environment files.
- **Environment controls implementation**: Staging might use one secret store, production another - the app doesn't care.
- **Clear validation**: Modules validate that config.toml provides what deploy.toml declares.
- **Extensible pattern**: Adding new resource types follows a consistent pattern.

## Built-in Modules

### Database Module

The database module uses a **two-account model** for security:

- **App user**: DML only (SELECT, INSERT, UPDATE, DELETE) - used by runtime services
- **Migrate user**: DDL + DML (CREATE, ALTER, DROP, etc.) - used by migrations

This reduces blast radius if the application is compromised - attackers cannot drop tables or alter schema.

**Application declares** (`deploy.toml`):

```toml
[database]
type = "postgresql"
extensions = ["unaccent", "pg_bigm"]  # optional: PostgreSQL extensions to create
```

Extensions are created before migrations via the db-users Lambda (which connects
as the RDS master user with `rds_superuser` privileges). This is necessary because
some extensions (e.g., `pg_bigm`) can only be created by a superuser. Apps can
keep `CREATE EXTENSION IF NOT EXISTS` in Django migrations as a safety net — they
will harmlessly no-op when the extension already exists.

**Setting up extensions for an environment:**

1. Add the `extensions` list to your app's `deploy.toml`:

   ```toml
   [database]
   type = "postgresql"
   extensions = ["unaccent", "pg_bigm"]
   ```

1. Add the Lambda function name output to your environment's `main.tf`:

   ```hcl
   output "db_users_lambda_function_name" {
     value = module.infrastructure.db_users_lambda_function_name
   }
   ```

1. Add `extensions_lambda` to your environment's `config.toml`:

   ```toml
   [database]
   # ... existing fields ...
   extensions_lambda = "${tofu:db_users_lambda_function_name}"
   ```

1. Run `tofu apply` in your environment directory to create the output.

1. Deploy — `deploy.py` will invoke the Lambda to create extensions before running migrations.

**Environment provides** (`config.toml`):

```toml
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
```

**Injects**: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME` (secret), `DB_PASSWORD` (secret)

**Credential Selection:**

- **Runtime services** use app credentials (DML only) via `credential_mode="app"`
- **Migrations** use migrate credentials (DDL+DML) via `credential_mode="migrate"`

When running `ecs-run.py run <env> migrate`, the migrate task definition is automatically selected, which has the migrate credentials configured

### Cache Module

**Application declares** (`deploy.toml`):

```toml
[cache]
type = "redis"
```

**Environment provides** (`config.toml`):

```toml
[cache]
url = "${tofu:redis_url}"
```

**Injects**: `REDIS_URL`

### Storage Module

**Application declares** (`deploy.toml`):

```toml
[storage]
type = "s3"
buckets = ["media"]  # or ["originals", "media"]
```

**Environment provides** (`config.toml`):

```toml
[storage]
media_bucket = "${tofu:s3_media_bucket}"
media_bucket_region = "us-west-2"  # optional
originals_bucket = "${tofu:s3_originals_bucket}"  # if declared
```

**Injects**: `S3_MEDIA_BUCKET`, `S3_MEDIA_BUCKET_REGION` (if specified), `S3_ORIGINALS_BUCKET` (if declared)

### CDN Module

**Application declares** (`deploy.toml`):

```toml
[cdn]
type = "cloudfront"
```

**Environment provides** (`config.toml`):

```toml
[cdn]
domain = "${tofu:cloudfront_domain}"
key_id = "${tofu:cloudfront_key_id}"
private_key_param = "/myapp/staging/cloudfront-private-key"
```

**Injects**: `CLOUDFRONT_DOMAIN`, `CLOUDFRONT_KEY_ID`, `CLOUDFRONT_PRIVATE_KEY` (secret)

### Secrets Module

**Application declares** (`deploy.toml`):

```toml
[secrets]
names = ["SECRET_KEY", "SIGNED_URL_SECRET", "DATACITE_PASSWORD"]
```

**Environment provides** (`config.toml`):

```toml
[secrets]
provider = "ssm"
path_prefix = "/myapp/staging"
```

**Name normalization**: `SECRET_KEY` → `secret-key`, `SIGNED_URL_SECRET` → `signed-url-secret`

**Injects**: Each named secret from SSM Parameter Store

## Service URL References

For referencing other services' URLs in environment variables:

```toml
[environment]
API_BASE_URL = "${services.api.url}"
```

The deployer calculates this from:

- Domain from config.toml's `[environment].domain_name`
- Path from the service's `path_pattern` in deploy.toml

For `api` with `path_pattern = "/api/*"` → `https://example.com/api`

## Validation

When deploying, the module system validates that:

1. If an app declares `[database]`, config.toml must have a `[database]` section
1. Required fields are present in the config
1. Credential types are valid (`secretsmanager` or `ssm`)

Validation errors are shown before deployment starts, helping catch configuration mismatches early.

## Summary Table

| Module   | App Declares                                | Environment Provides                                  | Injects                                                      |
| -------- | ------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------ |
| database | `type = "postgresql"`, `extensions = [...]` | host, port, name, credentials (app + migrate), lambda | DB_HOST, DB_PORT, DB_NAME, DB_USERNAME, DB_PASSWORD          |
| cache    | `type = "redis"`                            | url                                                   | REDIS_URL                                                    |
| storage  | `type = "s3"`, `buckets = [...]`            | bucket names per declared bucket                      | S3\_{NAME}\_BUCKET                                           |
| cdn      | `type = "cloudfront"`                       | domain, key_id, private_key_param                     | CLOUDFRONT_DOMAIN, CLOUDFRONT_KEY_ID, CLOUDFRONT_PRIVATE_KEY |
| secrets  | `names = [...]`                             | provider, path_prefix                                 | Each named secret                                            |

## Terraform Modules

### db-users Module

Creates database users with appropriate privileges using a Lambda function.

**Usage:**

```hcl
module "db_users" {
  source = "../modules/db-users"

  name_prefix          = local.name_prefix
  db_host              = module.rds.address
  db_port              = module.rds.port
  db_name              = var.database_name
  master_secret_arn    = module.db_secrets.master_secret_arn
  vpc_id               = var.vpc_id
  subnet_ids           = var.private_subnet_ids
  db_security_group_id = module.rds.security_group_id
}
```

**Outputs:**

- `app_username_arn` / `app_password_arn` - For runtime services (DML only)
- `migrate_username_arn` / `migrate_password_arn` - For migrations (DDL + DML)
