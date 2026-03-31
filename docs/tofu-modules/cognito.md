# Cognito

Creates a Cognito user pool for ALB authentication on staging environments. Admin-only user creation (no self-signup). Includes a user pool client configured for ALB OAuth callbacks.

## Usage

```hcl
module "cognito" {
  source = "../../modules/cognito"

  name_prefix = "myapp-staging"
  domain_name = "myapp-staging.example.com"
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| name_prefix | string | Prefix for resource names |
| domain_name | string | Domain name for ALB OAuth callback URL |

## Outputs

| Output | Description |
| --- | --- |
| user_pool_arn | Cognito user pool ARN |
| user_pool_id | Cognito user pool ID (for managing users) |
| client_id | Client ID for ALB authentication |
| client_secret | Client secret for ALB authentication (sensitive) |
| domain | Cognito domain prefix |
| domain_url | Full Cognito domain URL |
