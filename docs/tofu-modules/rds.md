# RDS

Creates a PostgreSQL 15 RDS instance with security group, parameter group, enhanced monitoring, and Performance Insights.

## Usage

```hcl
module "rds" {
  source = "../../modules/rds"

  name_prefix        = "myapp-staging"
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  ecs_security_group = module.ecs_cluster.security_group_id

  database_name   = "myapp"
  master_username = var.db_username
  master_password = var.db_password

  # Production overrides
  deletion_protection     = true
  multi_az                = true
  backup_retention_period = 35
  skip_final_snapshot     = false
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| name_prefix | string | Prefix for resource names |
| vpc_id | string | VPC ID |
| subnet_ids | list(string) | Private subnet IDs |
| ecs_security_group | string | ECS security group ID (allowed to connect) |
| database_name | string | Database name |
| master_username | string | Master username (sensitive) |
| master_password | string | Master password (sensitive) |
| instance_class | string | Instance class (default: db.t3.micro) |
| deletion_protection | bool | Prevent deletion (default: false) |
| multi_az | bool | Multi-AZ failover (default: false) |
| storage_encrypted | bool | Encrypt at rest (default: true) |

## Outputs

| Output | Description |
| --- | --- |
| endpoint | RDS endpoint (host:port) |
| address | RDS hostname |
| port | RDS port |
| connection_url | Full PostgreSQL connection URL (sensitive) |
| security_group_id | RDS security group ID |
| db_instance_id | RDS instance identifier |
