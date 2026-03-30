# ALB Access Logs Terraform Module

## What it did

`modules/alb-access-logs/main.tf` (121 lines) created an S3 bucket with the required bucket policy for ALB access logging. It handled the per-region ELB service account IDs needed for the bucket policy, versioning, encryption, lifecycle rules (365-day expiry), and public access blocking.

The module was used in root `main.tf` behind `alb_access_logs_enabled` (default: false).

## Why it was removed

The feature was never enabled in any environment (default false). The ALB module already accepts `access_logs_enabled`/`access_logs_bucket` variables directly — users who need ALB access logs can create an S3 bucket externally and pass its name. The per-region ELB account ID lookup was the main value-add, but this is well-documented in AWS docs.

## Removal commit

`bdb5891`

## How to restore

1. Recover files:
   ```bash
   git checkout bdb5891^ -- modules/alb-access-logs/
   ```
2. Re-add the module block and variables to `main.tf` and `variables.tf`:
   - `alb_access_logs_enabled` (bool, default false)
   - `alb_access_logs_prefix` (string, default "alb-logs")
3. Re-add the `access_logs_*` arguments to the ALB module block
