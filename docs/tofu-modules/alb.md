# ALB

Creates an Application Load Balancer with optional HTTPS, Cognito authentication, and path-based routing via additional target groups.

## Usage

```hcl
module "alb" {
  source = "../../modules/alb"

  name_prefix       = "myapp-staging"
  vpc_id            = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids
  certificate_arn   = module.acm.certificate_arn  # enables HTTPS

  # Optional: Cognito auth (requires HTTPS)
  cognito_auth = {
    user_pool_arn       = module.cognito.user_pool_arn
    user_pool_client_id = module.cognito.client_id
    user_pool_domain    = module.cognito.domain
  }
}
```

## Key Variables

| Variable                 | Type         | Description                                 |
| ------------------------ | ------------ | ------------------------------------------- |
| name_prefix              | string       | Prefix for resource names                   |
| vpc_id                   | string       | VPC ID                                      |
| public_subnet_ids        | list(string) | Public subnet IDs for the ALB               |
| certificate_arn          | string       | ACM certificate ARN (enables HTTPS)         |
| cognito_auth             | object       | Cognito auth config (optional)              |
| additional_target_groups | map(object)  | Path-based routing target groups            |
| deletion_protection      | bool         | Enable deletion protection (default: false) |
| idle_timeout             | number       | Idle timeout in seconds (default: 60)       |

## Outputs

| Output                    | Description                              |
| ------------------------- | ---------------------------------------- |
| arn                       | ALB ARN                                  |
| dns_name                  | ALB DNS name                             |
| zone_id                   | ALB zone ID (for Route 53 alias records) |
| security_group_id         | ALB security group ID                    |
| default_target_group_arn  | Default target group ARN                 |
| https_listener_arn        | HTTPS listener ARN (null if not enabled) |
| service_target_group_arns | Map of service name to target group ARN  |
| arn_suffix                | ALB ARN suffix (for CloudWatch metrics)  |
