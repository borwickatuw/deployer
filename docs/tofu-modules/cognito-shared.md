# Cognito Shared

Creates a single Cognito user pool with multiple app clients (one per domain), allowing users to have one set of credentials across all staging environments.

## Usage

```hcl
module "cognito_shared" {
  source = "../../modules/cognito-shared"

  name_prefix = "staging-shared"
  app_domains = {
    myapp    = "myapp-staging.example.com"
    otherapp = "otherapp-staging.example.com"
  }
}
```

## Key Variables

| Variable    | Type        | Description                                   |
| ----------- | ----------- | --------------------------------------------- |
| name_prefix | string      | Prefix for resource names                     |
| app_domains | map(string) | Map of app name to domain for OAuth callbacks |

## Outputs

| Output        | Description                                               |
| ------------- | --------------------------------------------------------- |
| user_pool_id  | Shared user pool ID                                       |
| user_pool_arn | Shared user pool ARN                                      |
| domain        | Cognito domain prefix                                     |
| domain_url    | Full Cognito domain URL                                   |
| app_clients   | Map of app name to {client_id, client_secret} (sensitive) |
