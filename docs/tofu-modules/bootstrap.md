# Bootstrap

IAM roles, S3 state bucket, and permissions boundary for the deployer infrastructure. Source code is in `modules/bootstrap/`. Instantiated once per AWS account.

For setup instructions, see [GETTING-STARTED.md](../GETTING-STARTED.md).

## File Organization

| File                   | Purpose                                                                               |
| ---------------------- | ------------------------------------------------------------------------------------- |
| `main.tf`              | Provider configuration and data sources                                               |
| `s3.tf`                | Terraform state S3 bucket                                                             |
| `iam-boundary.tf`      | Permissions boundaries: ECS task roles, the scheduler Lambda, EC2 container instances |
| `iam-trust.tf`         | Shared trust policy for deployer roles                                                |
| `iam-app-deploy.tf`    | `deployer-app-deploy` role for deploy.py                                              |
| `iam-infra-admin.tf`   | `deployer-infra-admin` role for tofu.sh                                               |
| `iam-cognito-admin.tf` | `deployer-cognito-admin` role for Cognito management                                  |
| `iam-user-policy.tf`   | Assume-role policy attached to trusted IAM users                                      |
| `variables.tf`         | Input variables                                                                       |
| `outputs.tf`           | Module outputs                                                                        |

## Usage

```hcl
module "bootstrap" {
  source           = "../bootstrap"
  region           = "us-west-2"
  project_prefixes = ["myapp", "otherapp"]
  trusted_user_arns = ["arn:aws:iam::123456789012:user/deployer"]
}
```

## Container instances and the GPU build host

`iam-boundary.tf` also holds `deployer-ecs-instance-role-boundary`, the ceiling for an EC2 container instance role ([ecs-gpu-capacity](ecs-gpu-capacity.md)): the ECS agent and SSM agent actions the task boundary deliberately lacks, and nothing a task does. The scheduler boundary (`deployer-scheduler-role-boundary`) admits what the [staging scheduler](staging-scheduler.md) needs to stop and start that instance: the container-instance reads for the drain check, and `ec2:StartInstances`/`StopInstances` on instances tagged `deployer-build-host = <project>-*`. `iam-infra-admin.tf`'s fifth policy, `deployer-infra-admin-capacity`, lets tofu run the instance, manage its lifecycle (start, stop, terminate, reconfigure — only instances tagged `deployer-build-host = <project>-*`), read the ECS-optimized AMI parameters, and manage project-prefixed instance profiles. `iam-app-deploy.tf` lets the deploy role read the GPU build host's state (`ec2:DescribeInstances`), stop and start it with the environment (`bin/environment.py`; the same tag condition), and open an SSM port-forwarding session to it — only to instances tagged `deployer-build-host = <project>-*`, only the port-forwarding document — and, when `ecr_pull_repositories` names them, pull unprefixed repositories in this account that hold another project's published base image.

```hcl
module "bootstrap" {
  # ...
  ecr_pull_repositories = ["blocker"]
}
```

## Adding a New Application

Add the application name to `project_prefixes` in the instance's `terraform.tfvars` and run `tofu apply`. See [Adding New Applications](../GETTING-STARTED.md#adding-new-applications) for details.

## IAM Policy Guidelines

The roles and policies this module creates follow three rules:

- **Use service-level wildcards** (e.g., `ecs:*`, `rds:*`) rather than listing
  individual actions.
- **Apply resource restrictions where they matter**: S3, SSM, ECR and IAM are
  scoped to `project_prefixes`.
- **Keep IAM role management granular**, because it is the sensitive part.

Review IAM policy changes by hand. For multi-account setups, see
[MULTIPLE-ACCOUNTS.md](../operations/MULTIPLE-ACCOUNTS.md).
