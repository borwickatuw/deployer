# Production Operations Guide

This guide covers deploying, operating, and maintaining production environments using the deployer framework.

## Table of Contents

1. [Initial Production Deployment](#initial-production-deployment)
1. [Maintenance Cadences](#maintenance-cadences)
1. [Alarms and Notifications](#alarms-and-notifications)
1. [Emergency Procedures](#emergency-procedures)
1. [Incident Response](#incident-response)
1. [Related Documentation](#related-documentation)

______________________________________________________________________

## Initial Production Deployment

### Recovery Targets

| Metric                         | Target               | How Achieved                                   |
| ------------------------------ | -------------------- | ---------------------------------------------- |
| RPO (Recovery Point Objective) | 5 minutes            | RDS continuous backup (point-in-time recovery) |
| RTO (Recovery Time Objective)  | 1 hour               | emergency.py restore procedures                |
| Retention                      | 35 days (production) | rds_backup_retention_period setting            |
| Multi-AZ Failover              | Automatic            | rds_multi_az = true                            |

These targets assume:

- Production RDS settings applied (35-day retention, deletion protection, multi-az)
- Monthly restore testing via `emergency.py restore-db`
- Emergency procedures documented and practiced

### What Recovery Does Not Cover

The targets above are about the database. Everything else in an environment
has its own posture, and several pieces have no backup at all:

| Data                        | Posture                                                                                                                                                                                      |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SSM Parameter Store secrets | **No backup.** `bin/ssm-secrets.py` writes them outside OpenTofu, so they are in no state file and no snapshot. Losing the account means re-entering every value.                            |
| S3 buckets (`modules/s3`)   | The `versioning` variable is opt-in per environment; with it unset, an overwritten or deleted object is gone.                                                                                |
| ElastiCache                 | Automatic snapshots follow `snapshot_retention_limit`, and cache contents are reconstructible anyway. Anything only in Redis — sessions, queued work — is lost when the cluster is replaced. |
| ECS task filesystems        | Ephemeral. Anything a container wrote to local disk is gone at the next deploy.                                                                                                              |
| ECR images                  | The lifecycle policy keeps the newest `lifecycle_policy_count` images, which is also how far back a rollback can reach.                                                                      |
| In-flight requests          | Lost during a Multi-AZ failover or a deployment's task replacement.                                                                                                                          |

OpenTofu state is the exception that *is* protected: the bootstrap module
enables versioning on the state bucket (`modules/bootstrap/s3.tf`), so a
corrupted or truncated state file can be restored from an earlier object
version.

### Pre-Deployment Checklist

Before deploying to production, verify:

- [ ] **Certificates**: ACM certificates issued and validated
- [ ] **Database**: RDS instance sized appropriately for production load
- [ ] **DNS**: Route53 hosted zone configured, domain verified
- [ ] **IAM**: Task roles and execution roles have correct permissions
- [ ] **Infrastructure**: All Terraform/OpenTofu resources planned and reviewed
- [ ] **Networking**: VPC, subnets, and security groups properly configured
- [ ] **Redis**: ElastiCache configured with Multi-AZ
- [ ] **Secrets**: All secrets stored in SSM Parameter Store (`bin/ssm-secrets.py`)

### Production-Specific terraform.tfvars Settings

These settings should always be enabled for production regardless of workload:

```hcl
# $DEPLOYER_ENVIRONMENTS_DIR/myapp-production/terraform.tfvars

# Database - reliability settings
rds_multi_az             = true      # Automatic failover
backup_retention_period  = 35        # 35 days vs 7 for staging
deletion_protection      = true      # Prevent accidental deletion
skip_final_snapshot      = false     # Always create final snapshot

# Redis - reliability settings
elasticache_multi_az     = true      # Automatic failover

# ECR - more rollback depth
lifecycle_policy_count   = 50        # Keep 50 images vs 10 for staging
```

### Sizing Guidelines

Sizing depends on your specific workload. Start conservatively and adjust based on monitoring data.

- `min_replicas >= 2` for high availability (survives single task failure)
- Use `capacity-report.py --days 7` to analyze actual utilization after a week of production traffic
- See [CONFIG-REFERENCE.md](../CONFIG-REFERENCE.md#cpumemory-combinations) for valid Fargate CPU/memory combinations

**Iteration process:**

1. Deploy with conservative estimates
1. Run `uv run python bin/capacity-report.py myapp-production --days 7` after a week
1. Adjust based on recommendations (watch for OOM kills especially)
1. Repeat monthly as part of maintenance cadence

### First Deployment Walkthrough

1. **Apply infrastructure**:

   ```bash
   bin/tofu.sh rollout myapp-production
   ```

1. **Verify infrastructure**:

   ```bash
   uv run python bin/ops.py status myapp-production
   ```

1. **Create secrets** (if not already present):

   ```bash
   uv run python bin/ssm-secrets.py set myapp-production SECRET_KEY "$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
   ```

1. **Link and deploy application**:

   ```bash
   uv run python bin/link-environments.py myapp-production ../myapp/deploy.toml
   uv run python bin/deploy.py deploy myapp-production
   ```

1. **Run migrations**:

   ```bash
   uv run python bin/ecs-run.py run myapp-production migrate
   ```

1. **Verify deployment**:

   ```bash
   uv run python bin/ops.py health myapp-production
   ```

______________________________________________________________________

## Maintenance Cadences

### Daily (~5-10 minutes)

| Task                | Command                                                          |
| ------------------- | ---------------------------------------------------------------- |
| Run full audit      | `uv run python bin/ops.py audit myapp-production`                |
| Check for OOM kills | `uv run python bin/capacity-report.py myapp-production --days 1` |

The `audit` command runs status, health, logs, maintenance, and ECR vulnerability checks in one command.

### Weekly

| Task                       | Command                                                          |
| -------------------------- | ---------------------------------------------------------------- |
| Run capacity report        | `uv run python bin/capacity-report.py myapp-production --days 7` |
| Check RDS/snapshots status | `uv run python bin/ops.py status myapp-production`               |

### Monthly

| Task                           | Command / Description                                                     |
| ------------------------------ | ------------------------------------------------------------------------- |
| Test RDS backup restore        | `emergency.py restore-db` — see [RDS Backup Testing](#rds-backup-testing) |
| Apply capacity recommendations | Review `capacity-report.py` output and update `terraform.tfvars`          |
| Check pending maintenance      | `uv run python bin/ops.py maintenance myapp-production`                   |

### Quarterly

| Task                        | Command / Description                                     |
| --------------------------- | --------------------------------------------------------- |
| Rotate database credentials | Update in RDS, then SSM Parameter Store                   |
| Review IAM policies         | Audit bootstrap/ policies for least privilege             |
| Test disaster recovery      | `emergency.py restore-db`                                 |
| Update OpenTofu providers   | `bin/tofu.sh init -upgrade myapp-production`              |
| Verify alarm notifications  | See [Alarms and Notifications](#alarms-and-notifications) |

______________________________________________________________________

## Alarms and Notifications

`ops.py` is pull-based — it answers questions when someone asks them.
Production also needs push: something that reaches you when nobody is looking.
That is the [cloudwatch-alarms module](../tofu-modules/cloudwatch-alarms.md),
which creates the ALB, RDS, ElastiCache and ECS alarms and the SNS topic they
publish to. Instantiate it in the production environment's `main.tf`; staging
does not need it.

**An email subscription is not live until it is confirmed.** AWS sends a
confirmation link when `tofu apply` first creates the subscription, and until
someone clicks it the subscription sits in `PendingConfirmation` and every
alarm fires into nothing. Check after the first apply, and again whenever
`notification_email` changes:

```bash
# Subscription state -- PendingConfirmation means alarms are invisible.
# The topic ARN is the module's sns_topic_arn output; re-export it from the
# environment's outputs.tf to read it with `bin/tofu.sh output <env> <name>`.
aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN"

# Alarm inventory and current states
aws cloudwatch describe-alarms --alarm-name-prefix myapp-production \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}'
```

An alarm parked in `INSUFFICIENT_DATA` is as silent as an unconfirmed
subscription. It usually means the alarm's dimensions no longer match a real
resource — a renamed ECS service, a replaced RDS instance — so treat it as a
finding, not as "quiet."

______________________________________________________________________

## Emergency Procedures

Production operations are split into two tools:

- **`bin/ops.py`** — Read-only monitoring commands (safe to run anytime)
- **`bin/emergency.py`** — Commands that modify production state (use with care)

All emergency actions are logged to `local/emergency.log`.

### Monitoring (read-only)

```bash
# Run full audit (status, health, logs, maintenance, ECR vulnerabilities)
uv run python bin/ops.py audit myapp-production

# View current state (services, task definitions, RDS, snapshots)
uv run python bin/ops.py status myapp-production

# Check ALB target health
uv run python bin/ops.py health myapp-production

# Scan recent logs for errors
uv run python bin/ops.py logs myapp-production --minutes 60

# Check pending maintenance (RDS, ElastiCache)
uv run python bin/ops.py maintenance myapp-production

# Check ECR vulnerability findings
uv run python bin/ops.py ecr myapp-production
```

### Rollback Deployment

If a deployment causes issues, roll back to a previous task definition:

```bash
# Interactive rollback (shows services, then revisions, prompts for selection)
uv run python bin/emergency.py rollback myapp-production

# Direct rollback to previous revision
uv run python bin/emergency.py rollback myapp-production --service web

# Rollback to specific revision
uv run python bin/emergency.py rollback myapp-production --service web --revision 42
```

The tool automatically creates a checkpoint before making changes, shows environment variable differences between revisions, and monitors deployment progress.

If the rollback was wrong, restore the previous state:

```bash
uv run python bin/emergency.py revert myapp-production --list
uv run python bin/emergency.py revert myapp-production --checkpoint emergency-2026-02-04-120000.json
```

### Database Recovery

Database restore operations create a **new** RDS instance with a `-restore` suffix. The original database is never modified, so you can compare data, go back to the original, or delete the restore instance if unneeded.

```bash
# Create an emergency snapshot first (recommended)
uv run python bin/emergency.py snapshot myapp-production

# Restore from a specific snapshot
uv run python bin/emergency.py restore-db myapp-production --snapshot <snapshot-id>

# Point-in-time recovery
uv run python bin/emergency.py restore-db myapp-production --time "2026-02-04T12:00:00Z"

# Interactive (lists available snapshots)
uv run python bin/emergency.py restore-db myapp-production
```

After restore completes (10-30 minutes):

1. The new instance will be at `myapp-production-db-restore`
1. Update your application's `DATABASE_URL` to point to the new instance
1. When done, delete the restore instance or the original as appropriate

### Scale Up Quickly

During traffic spikes:

```bash
# Scale specific service
uv run python bin/emergency.py scale myapp-production --service web --count 10

# Scale all services by multiplier
uv run python bin/emergency.py scale myapp-production --all --multiplier 2

# Reset to configured replicas (from terraform)
uv run python bin/emergency.py scale myapp-production --reset
```

### Force New Deployment

If containers are unhealthy but not being replaced:

```bash
# Force new deployment of a specific service
uv run python bin/emergency.py force-deploy myapp-production --service web

# Force new deployment of all services
uv run python bin/emergency.py force-deploy myapp-production --all
```

### RDS Backup Testing

Monthly procedure to verify backups are restorable:

```bash
# 1. View available snapshots
uv run python bin/ops.py status myapp-production

# 2. Restore from a snapshot (creates myapp-production-db-restore instance)
uv run python bin/emergency.py restore-db myapp-production

# 3. Verify connectivity (from bastion or ECS task)
psql "postgres://user:pass@myapp-production-db-restore.xxxxx.rds.amazonaws.com/myapp" \
  -c "SELECT COUNT(*) FROM users;"

# 4. Delete test instance
aws rds delete-db-instance \
  --db-instance-identifier myapp-production-db-restore \
  --skip-final-snapshot
```

______________________________________________________________________

## Incident Response

When something goes wrong in production, follow this structured approach.

### Severity Definitions

| Severity          | Definition                              | Response Time             |
| ----------------- | --------------------------------------- | ------------------------- |
| **P1 - Critical** | Total service outage or data loss risk  | Immediate (within 15 min) |
| **P2 - Major**    | Significant degradation, partial outage | Within 1 hour             |
| **P3 - Minor**    | Limited impact, workaround available    | Within 1 business day     |

### Immediate Response (First 15 Minutes)

1. **Assess the situation**

   ```bash
   uv run python bin/ops.py audit myapp-production
   ```

1. **Determine severity** using definitions above

1. **Stabilize if possible**

   ```bash
   # Rollback if recent deployment caused issue
   uv run python bin/emergency.py rollback myapp-production --service web

   # Scale up if capacity issue
   uv run python bin/emergency.py scale myapp-production --all --multiplier 2

   # Force redeploy if containers unhealthy
   uv run python bin/emergency.py force-deploy myapp-production --all
   ```

### During the Incident

- **Keep notes** — Document timeline, actions taken, findings
- **Focus on restoration** — Fix the symptom first, root cause later

### Resolution

1. **Verify service restored**

   ```bash
   uv run python bin/ops.py health myapp-production
   uv run python bin/ops.py logs myapp-production --minutes 10
   ```

1. **Document the incident** with a postmortem for P1 incidents and P2 incidents lasting > 1 hour

______________________________________________________________________

## Related Documentation

- [CONFIG-REFERENCE.md](../CONFIG-REFERENCE.md) - Configuration options
- [DEPLOYMENT-GUIDE.md](../DEPLOYMENT-GUIDE.md) - Deployment procedures
- [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) - Common issues
