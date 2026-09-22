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

| Variable                       | Type         | Description                                                            |
| ------------------------------ | ------------ | ---------------------------------------------------------------------- |
| name_prefix                    | string       | Prefix for resource names                                              |
| vpc_id                         | string       | VPC ID                                                                 |
| public_subnet_ids              | list(string) | Public subnet IDs for the ALB                                          |
| certificate_arn                | string       | ACM certificate ARN (enables HTTPS)                                    |
| cognito_auth                   | object       | Cognito auth config (optional)                                         |
| unauthenticated_path_patterns  | list(string) | Paths forwarded without Cognito auth (app must enforce its own access) |
| additional_target_groups       | map(object)  | Path-based routing target groups                                       |
| deletion_protection            | bool         | Enable deletion protection (default: false)                            |
| idle_timeout                   | number       | Idle timeout in seconds (default: 60)                                  |
| restrict_ingress_to_cloudfront | bool         | Accept traffic only from CloudFront, HTTPS only (default: false)       |

## Restricting Ingress to CloudFront

By default the security group accepts HTTP and HTTPS from `0.0.0.0/0`. With
`restrict_ingress_to_cloudfront = true` it accepts HTTPS only, and only from the
AWS-managed prefix list `com.amazonaws.global.cloudfront.origin-facing`. Port 80
closes: [cloudfront-alb](cloudfront-alb.md) reaches the ALB over HTTPS only, and
the prefix list weighs 55 of the default 60 inbound rules per security group, so
it is referenced once rather than once per port. The module refuses the setting
without `certificate_arn`.

Enable it only when a CloudFront distribution fronts the ALB. Anything that
reached the ALB's own DNS name directly stops working; target health checks are
unaffected, because they run from the ALB to the tasks.

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
