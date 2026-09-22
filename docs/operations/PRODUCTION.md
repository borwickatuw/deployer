# Production Operations Guide

This guide covers deploying, operating, and maintaining production environments using the deployer framework.

## Table of Contents

1. [Initial Production Deployment](#initial-production-deployment)
1. [WAF and CloudFront](#waf-and-cloudfront)
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

## WAF and CloudFront

With `cloudfront_alb_enabled` (the default in `deployer.tf`), viewers reach the
application through a CloudFront distribution whose origin is the ALB. The WAF
(`waf_preset`) is attached to the ALB, not to the distribution.

### Closing direct access to the ALB

By default the ALB also accepts HTTP and HTTPS from anywhere on its own DNS
name, the `alb_dns_name` output, so a client can skip CloudFront.
`alb_restrict_ingress_to_cloudfront = true` in `services.auto.tfvars` limits the
ALB security group to HTTPS from the AWS-managed prefix list
`com.amazonaws.global.cloudfront.origin-facing`. It takes effect on the next
`tofu apply`. The plan refuses it unless the distribution exists
(`cloudfront_alb_enabled` with `domain_name` and `route53_zone_id`).

What the operator loses once it is applied:

- **The ALB DNS name stops answering.** `https://<alb_dns_name>` from a
  workstation, CI job or external uptime monitor times out, because the security
  group drops the packets instead of refusing them. Point monitors at the domain.
- **No path around CloudFront for debugging.** When CloudFront serves its 503
  page, you cannot curl the ALB to see the origin's own response. Use
  `bin/ops.py health` (target health through the AWS API), the ALB's CloudWatch
  metrics, the application logs, or `bin/ecs-run.py exec <env> curl http://localhost:<port>/...`
  inside a task.
- **Port 80 closes.** CloudFront talks to the ALB over HTTPS only and redirects
  viewers to HTTPS at the edge, so no viewer ever used it.
- **Other DNS names aimed at the ALB break.** An `additional_dns_records` entry
  that aliases the ALB directly stops working. Alias the distribution instead.

What does not change: ALB target health checks, which run from the ALB to the
tasks; `bin/cognito.py`, which uses `domain_name` whenever one is set; and
deploys, which use the ECS and ELB APIs, not HTTP to the ALB.

The prefix list lets in **CloudFront as a service, not this distribution**.
Anyone can create their own CloudFront distribution with this ALB as its origin,
and it will pass the security group. The companion fix is a secret
origin-verify header: the distribution adds it as an origin custom header, and
the ALB or WAF rejects requests that lack it. Deployer does not implement it yet.

### What the WAF rate rule counts

The rate rule (2000 requests per 5 minutes on `standard`, 1000 on `strict`)
counts per client address. What "client address" means depends on the two
switches:

| CloudFront | `alb_restrict_ingress_to_cloudfront` | The rule counts per                                        |
| ---------- | ------------------------------------ | ---------------------------------------------------------- |
| off        | n/a                                  | Viewer IP (the TCP source)                                 |
| on         | `false`                              | CloudFront **edge** address (the TCP source the ALB sees)  |
| on         | `true`                               | Viewer IP, from the `x-viewer-ip` header CloudFront writes |

**Before** (restriction off): every viewer arriving through the same edge shares
one counter, so a busy edge can trip the limit for all of them. A single abusive
client spread across edges never trips it. `tofu plan` prints a check warning
for this combination.

**After** (restriction on): a viewer-request CloudFront Function sets
`x-viewer-ip` to the address CloudFront observed and overwrites any value the
client sent. The rule aggregates on that header (`FORWARDED_IP`), and a
malformed value counts against the limit. `X-Forwarded-For` was rejected
because CloudFront appends the viewer's address to whatever the client sent in
that header, and a WAF rate rule reads the first address, which the client
controls. The header is trustworthy only when nothing but CloudFront can reach
the ALB. For that reason the WAF module refuses `behind_cloudfront` unless the
ALB restriction is asserted, and the root module derives it from both switches.

The switch takes effect on the next `tofu apply` of an environment with the WAF
and CloudFront both enabled. That apply creates the function, attaches it to
the distribution, and changes the rule's aggregation. While the distribution
change propagates, requests that arrive without the header are not evaluated by
the rate rule at all, because WAF skips a forwarded-IP rule when its header is
absent. That window lasts minutes.

The residual gap above applies here too. Another account's CloudFront
distribution pointed at this ALB can send its own `x-viewer-ip`, or omit it, and
choose its rate key. The origin-verify header closes that as well.

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

### Before a Data-Altering Migration

Point-in-time recovery already covers the window, but "when exactly did the
migration start?" is a question nobody wants to answer from a deploy log
during an incident. Take the snapshot deliberately instead, before deploying a
migration that drops a column, backfills, or rewrites rows:

```bash
uv run python bin/emergency.py snapshot myapp-production
```

Deployments run the `[migrations]` command from the app's `deploy.toml` as a
one-off ECS task before services update (see
[CONFIG-REFERENCE.md](../CONFIG-REFERENCE.md#migrations)), so the snapshot has
to be taken before `deploy.py deploy`, not after.

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
