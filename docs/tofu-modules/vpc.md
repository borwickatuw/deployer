# VPC

Creates a VPC with public and private subnets, NAT Gateway, and optional VPC flow logs.

## Usage

```hcl
module "vpc" {
  source = "../../modules/vpc"

  name_prefix        = "myapp-staging"
  vpc_cidr           = "10.0.0.0/16"
  availability_zones = ["us-west-2a", "us-west-2b"]
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| name_prefix | string | Prefix for resource names |
| vpc_cidr | string | CIDR block for the VPC |
| availability_zones | list(string) | Availability zones to use |
| flow_logs_enabled | bool | Enable VPC flow logs (default: true) |
| permissions_boundary | string | IAM permissions boundary ARN |

## Outputs

| Output | Description |
| --- | --- |
| vpc_id | VPC ID |
| public_subnet_ids | Public subnet IDs (for ALB) |
| private_subnet_ids | Private subnet IDs (for ECS, RDS, ElastiCache) |
