# Plan Archive

Completed work, moved here from SOMEDAY-MAYBE.md and PLAN.md.

______________________________________________________________________

## Checkov Findings — Resolved

### Resolved (removed from skip list)

| Check       | Description                 | Resolution                                                       |
| ----------- | --------------------------- | ---------------------------------------------------------------- |
| CKV_AWS_118 | RDS enhanced monitoring     | Added `monitoring_interval` variable (default 60s) with IAM role |
| CKV_AWS_353 | RDS Performance Insights    | Added `performance_insights_enabled` variable (default true)     |
| CKV_AWS_338 | CloudWatch 1-year retention | Changed all `log_retention_days` defaults from 30 to 365         |

### Resolved (still in skip list — Checkov can't evaluate variables)

| Check       | Description             | Resolution                                                                                 |
| ----------- | ----------------------- | ------------------------------------------------------------------------------------------ |
| CKV_AWS_150 | ALB deletion protection | Added `deletion_protection` variable (default false for staging)                           |
| CKV_AWS_91  | ALB access logging      | Added `access_logs_enabled` variable with `alb-access-logs` module                         |
| CKV_AWS_16  | RDS storage encryption  | Added `storage_encrypted` variable (default true)                                          |
| CKV_AWS_129 | RDS logging             | Added parameter group with `log_connections`, `log_disconnections`, CloudWatch log exports |
| CKV2_AWS_30 | RDS query logging       | Added `log_statement=ddl` and `log_min_duration_statement=1000` to parameter group         |
| CKV2_AWS_11 | VPC flow logs           | Added flow logs resources to VPC module (CloudWatch destination, 365-day retention)        |
