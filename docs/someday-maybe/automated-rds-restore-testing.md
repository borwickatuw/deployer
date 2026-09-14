+++
title = "Infrastructure Enhancements: Automated RDS Restore Testing"
+++

**Current state**: Manual monthly process documented in HOWTO-PRODUCTION.md.

**Enhancement**: Scheduled Lambda that:

1. Creates temporary RDS instance from latest snapshot
1. Runs basic connectivity/query test
1. Reports success/failure to SNS
1. Deletes temporary instance

**Implementation approach**:

```python
# Lambda function (pseudocode)
def handler(event, context):
    # Get latest snapshot
    snapshot = rds.describe_db_snapshots(...)[-1]

    # Restore to temp instance
    rds.restore_db_instance_from_db_snapshot(
        db_instance_identifier=f"{source}-test-restore-{date}",
        ...
    )

    # Wait and test
    wait_for_available()
    run_test_query()

    # Cleanup and report
    rds.delete_db_instance(...)
    sns.publish(success_message)
```

**Complexity**: High - requires Lambda, IAM roles, VPC configuration, error handling.
