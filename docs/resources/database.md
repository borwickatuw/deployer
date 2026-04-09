# Database Module

The database module uses a **two-account model** for security:

- **App user**: DML only (SELECT, INSERT, UPDATE, DELETE) - used by runtime services
- **Migrate user**: DDL + DML (CREATE, ALTER, DROP, etc.) - used by migrations

This reduces blast radius if the application is compromised - attackers cannot drop tables or alter schema.

## Application declares (`deploy.toml`)

```toml
[database]
type = "postgresql"
extensions = ["unaccent", "pg_bigm"]  # optional: PostgreSQL extensions to create
```

Extensions are created before migrations via the db-users Lambda (which connects as the RDS master user with `rds_superuser` privileges). This is necessary because some extensions (e.g., `pg_bigm`) can only be created by a superuser. Apps can keep `CREATE EXTENSION IF NOT EXISTS` in Django migrations as a safety net — they will harmlessly no-op when the extension already exists.

### Setting up extensions for an environment

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

## Environment provides (`config.toml`)

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

When running `ecs-run.py run <env> migrate`, the migrate task definition is automatically selected, which has the migrate credentials configured.

## db-users Terraform Module

The `modules/db-users/` Terraform module creates database users with appropriate privileges using a Lambda function.

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
