# Compute Optimizer Terraform Module

## What it did

`modules/compute-optimizer/main.tf` (30 lines) enabled AWS Compute Optimizer at the account level via a single Terraform resource (`aws_computeoptimizer_enrollment_status`). Once enabled, Compute Optimizer analyzes ECS task utilization and provides right-sizing recommendations.

## Why it was removed

This is a one-time account-level setting that doesn't need to be managed as a Terraform module. It can be enabled with a single AWS CLI command:

```bash
aws compute-optimizer update-enrollment-status --status Active
```

Or via the AWS Console under Compute Optimizer.

## Removal commit

`bdb5891`

## How to restore

1. Recover files:
   ```bash
   git checkout bdb5891^ -- modules/compute-optimizer/
   ```
2. Reference the module from your environment configuration
