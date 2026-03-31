# Bootstrap

IAM roles, S3 state bucket, and permissions boundary for the deployer infrastructure. Source code is in `modules/bootstrap/`. Instantiated once per AWS account.

For setup instructions, see [GETTING-STARTED.md](../GETTING-STARTED.md).

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
| `iam-user-policy.tf`   | Assume-role policy attached to trusted IAM users     |
| `variables.tf`         | Input variables                                      |
| `outputs.tf`           | Module outputs                                       |

## Usage

```hcl
module "bootstrap" {
  source           = "../bootstrap"
  region           = "us-west-2"
  project_prefixes = ["myapp", "otherapp"]
  trusted_user_arns = ["arn:aws:iam::123456789012:user/deployer"]
}
```

## Adding a New Application

Add the application name to `project_prefixes` in the instance's `terraform.tfvars` and run `tofu apply`. See [Adding New Applications](../GETTING-STARTED.md#adding-new-applications) for details.
