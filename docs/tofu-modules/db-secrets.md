# DB Secrets

Stores RDS master credentials in AWS Secrets Manager as a JSON object. Used for emergency admin access and as input to the db-users module.

## Usage

```hcl
module "db_secrets" {
  source = "../../modules/db-secrets"

  name_prefix = "myapp-staging"
  db_username = var.db_username
  db_password = var.db_password
  db_host     = module.rds.address
  db_port     = module.rds.port
  db_name     = "myapp"
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| name_prefix | string | Prefix for resource names |
| db_username | string | Master username (sensitive) |
| db_password | string | Master password (sensitive) |
| db_host | string | Database hostname |
| db_name | string | Database name |

## Outputs

| Output | Description |
| --- | --- |
| secret_arn | Secrets Manager secret ARN |
| master_secret_arn | Same as secret_arn (for db-users module input) |
| master_password_arn | ARN for password field (for ECS secrets) |
| master_username_arn | ARN for username field (for ECS secrets) |
| host_arn | ARN for host field (for ECS secrets) |
