+++
title = "Developer Experience: Emergency Download Backup"
+++

**Current state**: `bin/emergency.py` supports RDS snapshot restore but not downloading database backups locally. (Note: Read-only monitoring is now in `bin/ops.py`, while `bin/emergency.py` handles write operations.)

**Enhancement**: Add `download-backup` command to emergency.py:

```bash
uv run python bin/emergency.py myapp-production download-backup --output backup.sql
```

**Implementation approach**:

1. Run a one-off ECS task with `pg_dump` command
1. Stream output to S3 (ECS tasks can't easily stream to local)
1. Download from S3 to local machine
1. Clean up S3 object

Alternatively:

- Use RDS native S3 export feature (requires setup)
- Run pg_dump through a bastion host

**Complexity**: High - requires ECS task orchestration, S3 bucket access, and handling large files. The RDS restore approach (`restore-db`) is usually more practical for recovery scenarios.

**Why deferred**: For most emergencies, restoring to a new RDS instance is faster and safer than downloading a backup. If you need a local copy for analysis, you can:

1. Create a restore instance: `emergency.py restore-db --snapshot <id>`
1. Connect to the restore instance and run `pg_dump` manually
1. Delete the restore instance when done
