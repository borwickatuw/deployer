+++
title = "Checkov Deferred Items"
+++

These Checkov findings are valid but require infrastructure changes. Currently suppressed via `--skip-check` in the Makefile. See [PLAN-ARCHIVE.md](../PLAN-ARCHIVE.md) for resolved items.

### RDS Enhancements (deferred)

| Check           | Description               | Complexity | Notes                                          |
| --------------- | ------------------------- | ---------- | ---------------------------------------------- |
| CKV_AWS_161     | IAM authentication        | Medium     | Requires app changes to use IAM auth           |
| ~~CKV2_AWS_69~~ | ~~Encryption in transit~~ | ~~Low~~    | Moved to [PLAN-ARCHIVE.md](../PLAN-ARCHIVE.md) |

### Other

| Check           | Description                       | Complexity | Notes                                          |
| --------------- | --------------------------------- | ---------- | ---------------------------------------------- |
| ~~CKV_AWS_134~~ | ~~ElastiCache automatic backups~~ | ~~Low~~    | Moved to [PLAN-ARCHIVE.md](../PLAN-ARCHIVE.md) |
| CKV_AWS_51      | ECR immutable tags                | Medium     | Deploy workflow uses `latest` tag pattern      |
