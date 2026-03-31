# CI

Creates account-wide CI/CD infrastructure for GitHub Actions: a GitHub OIDC identity provider and a versioned S3 bucket for resolved config storage. Instantiated once per AWS account in bootstrap.

Per-project IAM roles are created separately using [ci-role](ci-role.md).

## Usage

```hcl
module "ci" {
  source = "../../modules/ci"

  create_oidc_provider = true  # false if another stack manages it
}
```

## Key Variables

| Variable | Type | Description |
| --- | --- | --- |
| create_oidc_provider | bool | Create the GitHub OIDC provider (default: false) |

## Outputs

| Output | Description |
| --- | --- |
| resolved_configs_bucket | S3 bucket name for resolved configs |
| resolved_configs_bucket_arn | S3 bucket ARN |
| oidc_provider_arn | GitHub OIDC identity provider ARN |
