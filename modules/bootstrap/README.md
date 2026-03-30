# Bootstrap Module

IAM roles and S3 state bucket for the deployer infrastructure.

## File Organization

| File                   | Purpose                                              |
| ---------------------- | ---------------------------------------------------- |
| `main.tf`              | Provider configuration and data sources              |
| `s3.tf`                | Terraform state S3 bucket                            |
| `iam-boundary.tf`      | ECS task role permissions boundary                   |
| `iam-trust.tf`         | Shared trust policy for deployer roles               |
| `iam-app-deploy.tf`    | `deployer-app-deploy` role for deploy.py             |
| `iam-infra-admin.tf`   | `deployer-infra-admin` role for tofu.sh              |
| `iam-cognito-admin.tf` | `deployer-cognito-admin` role for Cognito management |
| `variables.tf`         | Input variables                                      |
| `outputs.tf`           | Module outputs                                       |

## Usage

Instantiate this module in an account-specific directory (e.g., `bootstrap-staging/`):

```hcl
module "bootstrap" {
  source           = "../bootstrap"
  region           = "us-west-2"
  project_prefixes = ["myapp", "otherapp"]
  trusted_user_arns = ["arn:aws:iam::123456789:user/deployer"]
}
```

## Adding a New Project

1. Add the project name to `project_prefixes` in the instance's `terraform.tfvars`
1. Run `tofu apply`

The permissions boundary and IAM policies are automatically scoped to include the new project prefix.
