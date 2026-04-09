# CI Role

Creates a per-project IAM role for GitHub Actions CI/CD deployments. The role trusts a specific GitHub repo via OIDC federation and is scoped to one project prefix for ECS, ECR, S3, and CloudWatch operations.

## Usage

```hcl
module "ci_role" {
  source = "../../modules/ci-role"

  project_prefix              = "myapp"
  github_repo                 = "myorg/myapp"
  oidc_provider_arn           = data.terraform_remote_state.bootstrap.outputs.oidc_provider_arn
  resolved_configs_bucket_arn = data.terraform_remote_state.bootstrap.outputs.resolved_configs_bucket_arn
  region                      = var.region
  permissions_boundary        = data.terraform_remote_state.bootstrap.outputs.ecs_role_boundary_arn
}
```

## Key Variables

| Variable                    | Type         | Description                                                       |
| --------------------------- | ------------ | ----------------------------------------------------------------- |
| project_prefix              | string       | Project prefix for IAM scoping                                    |
| github_repo                 | string       | GitHub org/repo (e.g., "myorg/myapp")                             |
| oidc_provider_arn           | string       | GitHub OIDC provider ARN (from ci module)                         |
| resolved_configs_bucket_arn | string       | S3 bucket ARN (from ci module)                                    |
| region                      | string       | AWS region for resource ARNs                                      |
| permissions_boundary        | string       | IAM permissions boundary ARN                                      |
| github_oidc_environments    | list(string) | GitHub environments to trust (default: ["staging", "production"]) |

## Outputs

| Output    | Description             |
| --------- | ----------------------- |
| role_arn  | CI deploy IAM role ARN  |
| role_name | CI deploy IAM role name |
