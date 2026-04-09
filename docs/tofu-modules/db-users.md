# DB Users

Creates two PostgreSQL users with different privilege levels via a Lambda function: an app user (DML only) and a migrate user (DDL + DML). Credentials are stored in Secrets Manager.

## Usage

```hcl
module "db_users" {
  source = "../../modules/db-users"

  name_prefix          = "myapp-staging"
  db_host              = module.rds.address
  db_port              = module.rds.port
  db_name              = "myapp"
  master_secret_arn    = module.db_secrets.master_secret_arn
  vpc_id               = module.vpc.vpc_id
  subnet_ids           = module.vpc.private_subnet_ids
  db_security_group_id = module.rds.security_group_id
}
```

## Key Variables

| Variable             | Type         | Description                                |
| -------------------- | ------------ | ------------------------------------------ |
| name_prefix          | string       | Prefix for resource names                  |
| db_host              | string       | Database hostname                          |
| db_name              | string       | Database name                              |
| master_secret_arn    | string       | Secrets Manager ARN for master credentials |
| vpc_id               | string       | VPC ID for Lambda                          |
| subnet_ids           | list(string) | Subnet IDs for Lambda                      |
| db_security_group_id | string       | Database security group ID                 |
| permissions_boundary | string       | IAM permissions boundary ARN               |

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
