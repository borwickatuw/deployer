# DB on Shared RDS

Creates a database and isolated users on an existing shared RDS instance via Lambda. Multiple apps can share one RDS instance with complete data isolation through PostgreSQL's permission model.

## Usage

```hcl
module "db_on_shared_rds" {
  source = "../../modules/db-on-shared-rds"

  name_prefix          = "myapp-staging"
  db_name              = "myapp"
  db_host              = data.terraform_remote_state.shared.outputs.shared_rds_address
  master_secret_arn    = data.terraform_remote_state.shared.outputs.shared_rds_master_secret_arn
  vpc_id               = data.terraform_remote_state.shared.outputs.vpc_id
  subnet_ids           = data.terraform_remote_state.shared.outputs.private_subnet_ids
  db_security_group_id = data.terraform_remote_state.shared.outputs.shared_rds_security_group_id
}
```

## Key Variables

| Variable             | Type         | Description                                    |
| -------------------- | ------------ | ---------------------------------------------- |
| name_prefix          | string       | Prefix for resource names                      |
| db_name              | string       | Database name to create                        |
| db_host              | string       | Shared RDS hostname                            |
| master_secret_arn    | string       | Secrets Manager ARN for RDS master credentials |
| vpc_id               | string       | VPC ID for Lambda                              |
| subnet_ids           | list(string) | Subnet IDs for Lambda                          |
| db_security_group_id | string       | Shared RDS security group ID                   |

## Outputs

| Output               | Description                                    |
| -------------------- | ---------------------------------------------- |
| app_secret_arn       | Secrets Manager ARN for app credentials        |
| app_username_arn     | ARN for app username (for ECS secrets)         |
| app_password_arn     | ARN for app password (for ECS secrets)         |
| migrate_secret_arn   | Secrets Manager ARN for migrate credentials    |
| migrate_username_arn | ARN for migrate username (for ECS secrets)     |
| migrate_password_arn | ARN for migrate password (for ECS secrets)     |
| lambda_function_name | Lambda function name (for creating extensions) |

## Lambda source

`lambda/index.py` holds this module's privilege policy: **shared instance** —
users get CONNECT on exactly one database at creation time, and schema/table
privileges are granted in a second pass from inside that database. This is
deliberately *not* how `db-users` composes the same grants; see that module's
Lambda source note.

The shared vocabulary it imports (`escape_literal`, `get_secret`, `connect`, the
individual GRANT helpers, `DbUser`, `DbCredentials`) lives in
`modules/lambda-shared/db_common.py`. That is the only tracked copy;
`null_resource.lambda_dependencies` copies it into `lambda/` at apply time
alongside the pip dependencies, and the copy is gitignored. **Edit
`modules/lambda-shared/db_common.py`, never `lambda/db_common.py`.**

Tests: `tests/unit/test_lambda_db_common.py`.
