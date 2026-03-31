# ElastiCache

Creates a single-node Redis 7 ElastiCache cluster with a security group allowing access from ECS tasks.

## Usage

```hcl
module "elasticache" {
  source = "../../modules/elasticache"

  name_prefix        = "myapp-staging"
  vpc_id             = module.vpc.vpc_id
  subnet_ids         = module.vpc.private_subnet_ids
  ecs_security_group = module.ecs_cluster.security_group_id
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| name_prefix | string | Prefix for resource names |
| vpc_id | string | VPC ID |
| subnet_ids | list(string) | Private subnet IDs |
| ecs_security_group | string | ECS security group ID (allowed to connect) |
| node_type | string | Node type (default: cache.t3.micro) |
| snapshot_retention_limit | number | Days to retain snapshots (default: 1) |

## Outputs

| Output | Description |
| --- | --- |
| endpoint | Redis endpoint hostname |
| port | Redis port |
| connection_url | Full Redis URL (redis://host:port) |
| security_group_id | ElastiCache security group ID |
