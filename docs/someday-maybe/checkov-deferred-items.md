+++
title = "Checkov Deferred Items"
+++

These Checkov findings are valid but require infrastructure changes. Currently suppressed in `bin/checkov-scan.sh`. See [PLAN-ARCHIVE-2026-09.md](../plan-archive/PLAN-ARCHIVE-2026-09.md) for resolved items.

### RDS Enhancements (deferred)

| Check           | Description               | Complexity | Notes                                          |
| --------------- | ------------------------- | ---------- | ---------------------------------------------- |
| CKV_AWS_161     | IAM authentication        | Medium     | Requires app changes to use IAM auth           |

### Other

| Check           | Description                       | Complexity | Notes                                          |
| --------------- | --------------------------------- | ---------- | ---------------------------------------------- |
| CKV_AWS_51      | ECR immutable tags                | Medium     | Deploy workflow uses `latest` tag pattern      |
