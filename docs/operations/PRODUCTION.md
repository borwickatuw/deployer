# Production Operations Guide

This guide covers deploying, operating, and maintaining production environments using the deployer framework.

## Table of Contents

1. [Initial Production Deployment](#initial-production-deployment)
1. [Maintenance Cadences](#maintenance-cadences)
1. [Component-Specific Maintenance](#component-specific-maintenance)
1. [Monitoring and Alerting](#monitoring-and-alerting)
1. [Emergency Procedures](#emergency-procedures)
1. [Incident Response](#incident-response)
1. [Tooling Gaps and Workarounds](#tooling-gaps-and-workarounds)
1. [Compliance Considerations](#compliance-considerations)
1. [Appendices](#appendices)

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

### Pre-Deployment Checklist

Before deploying to production, verify:

- [ ] **Infrastructure**: All Terraform/OpenTofu resources planned and reviewed
- [ ] **Secrets**: All secrets stored in SSM Parameter Store (`bin/ssm-secrets.py`)
- [ ] **DNS**: Route53 hosted zone configured, domain verified
- [ ] **Certificates**: ACM certificates issued and validated
- [ ] **Database**: RDS instance sized appropriately for production load
- [ ] **Redis**: ElastiCache configured with Multi-AZ
- [ ] **Networking**: VPC, subnets, and security groups properly configured
- [ ] **IAM**: Task roles and execution roles have correct permissions

### Production-Specific terraform.tfvars Settings

Production environments require different settings than staging. Focus on these areas:

#### Required Production Settings (Non-Sizing)

These settings should always be enabled for production regardless of workload:

```hcl
# environments/myapp-production/terraform.tfvars

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

#### Sizing Guidelines

Sizing depends on your specific workload. Start conservatively and adjust based on monitoring data.

**RDS Instance Class:**

- Start with the smallest instance that meets your baseline needs
- `db.t4g.*` (burstable): Good for variable workloads with idle periods
- `db.r6g.*` (memory-optimized): Better for steady, memory-intensive workloads
- Monitor `CPUUtilization` and `FreeableMemory` after launch
- Use `capacity-report.py` patterns to right-size over time

**RDS Storage:**

- Estimate based on current data + 6-12 months growth
- GP3 storage can be resized without downtime (increases only)
- Monitor `FreeStorageSpace` and set alarms

**ElastiCache Node Type:**

- Size based on working set size (how much data needs to be in memory)
- `cache.t4g.*` (burstable): Good for caching with variable load
- `cache.r6g.*` (memory-optimized): Better for session storage or large datasets
- Monitor `DatabaseMemoryUsagePercentage`

**ECS CPU/Memory:**

- Profile your application locally to understand baseline needs
- Web services: Often memory-bound (Django/Rails); start with higher memory ratio
- Workers: Often CPU-bound during processing; may need higher CPU
- Use `capacity-report.py --days 7` to analyze actual utilization
- See [CONFIG-REFERENCE.md](../CONFIG-REFERENCE.md#cpumemory-combinations) for valid Fargate combinations

**Replicas and Auto-Scaling:**

- `min_replicas >= 2` for high availability (survives single task failure)
- Set `max_replicas` based on budget and expected peak load
- `cpu_target = 70` is a reasonable starting point; lower means more headroom

Example structure (fill in values based on your workload):

```hcl
services = {
  web = {
    cpu               = ???   # Start with 256-512, increase if CPU-bound
    memory            = ???   # Start with 512-1024, increase if OOM errors
    replicas          = 2     # Minimum 2 for HA
    load_balanced     = true
    port              = 8000
    health_check_path = "/health/"
  }
  celery = {
    cpu           = ???       # Often needs more CPU for task processing
    memory        = ???       # Depends on task memory requirements
    replicas      = 2
    load_balanced = false
  }
}

scaling = {
  web = {
    min_replicas = 2
    max_replicas = ???        # Based on budget and peak load estimates
    cpu_target   = 70
  }
}
```

**Iteration Process:**

1. Deploy with conservative estimates
1. Run `capacity-report.py --days 7` after a week of production traffic
1. Adjust based on recommendations (watch for OOM kills especially)
1. Repeat monthly as part of maintenance cadence

#### Capacity Monitoring Tools

Two tools help with ongoing right-sizing: AWS Compute Optimizer and `capacity-report.py`.

Both require Container Insights to be enabled on the ECS cluster (already configured in the `ecs-cluster` module; costs ~$0.30/task/month).

**AWS Compute Optimizer** is a free AWS service that uses machine learning to analyze utilization patterns:

```bash
# Enable (one-time)
aws compute-optimizer update-enrollment-status --status Active

# Or via OpenTofu - add to your environment's main.tf:
# module "compute_optimizer" { source = "../../modules/compute-optimizer" }

# View recommendations (wait 14 days after enabling)
aws compute-optimizer get-ecs-service-recommendations
aws compute-optimizer get-ecs-service-recommendations \
  --filters name=Finding,values=OVER_PROVISIONED
```

**capacity-report.py** provides on-demand capacity analysis integrated with the deployer workflow:

```bash
# Basic report (last 7 days)
uv run bin/capacity-report.py myapp-production

# Extended period
uv run bin/capacity-report.py myapp-production --days 14

# Compare against tfvars and generate suggested updates (RECOMMENDED)
uv run bin/capacity-report.py myapp-production \
  --tfvars environments/myapp-production/terraform.tfvars

# JSON output for automation
uv run bin/capacity-report.py myapp-production --format json
```

Classification logic:

| Status            | Criteria                                                  |
| ----------------- | --------------------------------------------------------- |
| OVER_PROVISIONED  | avg < 30% AND p95 < 50% for both CPU and memory           |
| UNDER_PROVISIONED | avg > 70% OR p95 > 90% for either CPU or memory           |
| BURSTY            | avg < 30% BUT p95 > 70% (workload is spiky, don't reduce) |
| OK                | Everything else                                           |

#### Right-Sizing Feedback Loop

The capacity report creates a tight feedback loop between CloudWatch metrics and your tfvars:

```
CloudWatch Metrics → capacity-report.py → terraform.tfvars
                                                ↓
ECS Services (right-sized) ← tofu apply ← Review & Commit
```

**Regular right-sizing review:**

1. Run capacity report with tfvars comparison:

   ```bash
   uv run bin/capacity-report.py myapp-production \
     --days 30 \
     --tfvars environments/myapp-production/terraform.tfvars
   ```

1. Review the output: check utilization percentages (avg and p95), review the tfvars comparison, copy suggested tfvars if recommendations look reasonable.

1. Update tfvars, run `tofu apply`, then deploy to pick up new task definitions.

1. Wait a few days and re-run the capacity report to verify the changes had the expected effect.

**Detecting tfvars drift:** If you see `cpu: tfvars=256 → running=512` in the comparison output, either update tfvars to match (if the running value is correct) or run `tofu apply` to sync (if tfvars is correct).

### First Deployment Walkthrough

1. **Apply infrastructure**:

   ```bash
   ./bin/tofu.sh plan myapp-production
   ./bin/tofu.sh apply myapp-production

   # Or use rollout to run init, plan, and apply in sequence
   ./bin/tofu.sh rollout myapp-production
   ```

1. **Verify infrastructure**:

   ```bash
   # Check environment status (ECS services, RDS)
   uv run python bin/environment.py status myapp-production

   # Or use ops.py for more detail (task definitions, snapshots)
   uv run python bin/ops.py myapp-production status

   # Check ElastiCache
   aws elasticache describe-cache-clusters --cache-cluster-id myapp-production-cache \
     --query 'CacheClusters[0].CacheClusterStatus'
   ```

1. **Create secrets** (if not already present):

   ```bash
   uv run python bin/ssm-secrets.py set myapp-production SECRET_KEY "$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
   ```

1. **Link and deploy application**:

   ```bash
   uv run python bin/link-environments.py myapp-production ../myapp/deploy.toml
   uv run python bin/deploy.py myapp-production
   ```

1. **Run migrations** (assumes environment is linked via `link-environments.py`):

   ```bash
   uv run python bin/ecs-run.py run myapp-production migrate
   ```

1. **Verify deployment**:

   ```bash
   # Check ECS services, task definitions, and RDS status
   uv run python bin/ops.py myapp-production status

   # Check ALB target health
   uv run python bin/ops.py myapp-production health
   ```

______________________________________________________________________

## Maintenance Cadences

### Daily (~5-10 minutes)

| Task                     | Command/Action                                                   |
| ------------------------ | ---------------------------------------------------------------- |
| Run full audit           | `uv run python bin/ops.py myapp-production audit`                |
| Review CloudWatch alarms | AWS Console > CloudWatch > Alarms > In alarm                     |
| Check for OOM kills      | `uv run python bin/capacity-report.py myapp-production --days 1` |

The `audit` command runs status, health, logs, maintenance, and ECR vulnerability checks in one command.

### Weekly

| Task                        | Description                                                                                     |
| --------------------------- | ----------------------------------------------------------------------------------------------- |
| Run capacity report         | `uv run python bin/capacity-report.py myapp-production --days 7`                                |
| Check RDS/snapshots status  | `uv run python bin/ops.py myapp-production status` (shows recent snapshots)                     |
| Review WAF blocked requests | AWS Console > WAF > Web ACLs > Sampled requests                                                 |
| Check ECR image count       | `aws ecr describe-images --repository-name myapp-production-web --query 'length(imageDetails)'` |

### Monthly

| Task                           | Description                                                               |
| ------------------------------ | ------------------------------------------------------------------------- |
| Test RDS backup restore        | `emergency.py restore-db` - see [RDS Backup Testing](#rds-backup-testing) |
| Review CloudWatch Logs costs   | AWS Console > Cost Explorer > Filter by CloudWatch                        |
| Apply capacity recommendations | Review `capacity-report.py` output and update `terraform.tfvars`          |
| Check pending maintenance      | `uv run python bin/ops.py myapp-production maintenance`                   |

### Quarterly

| Task                        | Description                                                                     |
| --------------------------- | ------------------------------------------------------------------------------- |
| Rotate database credentials | Update in RDS, then SSM Parameter Store                                         |
| Security audit              | Review CloudTrail, GuardDuty findings                                           |
| Review IAM policies         | Audit bootstrap/ policies for least privilege                                   |
| Test disaster recovery      | `emergency.py restore-db` - see [RDS Backup Testing](#rds-backup-testing)       |
| Update OpenTofu providers   | `./bin/tofu.sh init -upgrade myapp-production`                                  |
| Load testing baseline       | Run load test, compare to previous baseline - see [Load Testing](#load-testing) |

### Load Testing

Establish baseline performance to detect regressions and understand capacity limits.

**When to run:**

- Quarterly (at minimum)
- Before major releases
- After significant infrastructure changes

**Recommended tools:**

- [k6](https://k6.io/) - Scriptable, good for CI integration
- [locust](https://locust.io/) - Python-based, easy to customize
- [Apache Bench](https://httpd.apache.org/docs/2.4/programs/ab.html) - Simple, quick tests

**Basic test with k6:**

```javascript
// load-test.js
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '2m', target: 10 },   // Ramp up to 10 users
    { duration: '5m', target: 10 },   // Hold at 10 users
    { duration: '2m', target: 50 },   // Ramp up to 50 users
    { duration: '5m', target: 50 },   // Hold at 50 users
    { duration: '2m', target: 0 },    // Ramp down
  ],
};

export default function () {
  const res = http.get('https://myapp.example.com/health/');
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response time < 500ms': (r) => r.timings.duration < 500,
  });
  sleep(1);
}
```

**Run test:**

```bash
k6 run load-test.js
```

**Document results:**
Store baseline results in the application repo's `docs/performance/` directory:

```markdown
# Load Test Baseline - 2026-02-08

**Environment:** myapp-production
**Tool:** k6 v0.48

## Results

| Metric | 10 users | 50 users |
|--------|----------|----------|
| p95 latency | 120ms | 350ms |
| p99 latency | 250ms | 800ms |
| Error rate | 0% | 0.1% |
| RPS | 45 | 180 |

## Observations

- Performance degrades gracefully under load
- No errors until 50+ concurrent users
- Database connection pooling effective

## Capacity Estimate

Based on current sizing, production can handle ~100 concurrent users
before p95 latency exceeds SLO target (500ms).
```

**Example:** See your application's `performance/` directory for load testing scripts.

______________________________________________________________________

## Component-Specific Maintenance

### ECR (Elastic Container Registry)

**Current defaults** (modules/ecr/main.tf:33-37):

- `lifecycle_policy_count = 10` - keeps last 10 images

**Production recommendations**:

- Increase to 30-50 images for deeper rollback capability
- Override in `terraform.tfvars`: `lifecycle_policy_count = 50`
- Review vulnerability scan findings regularly

**Maintenance commands**:

```bash
# Check vulnerability findings (CRITICAL/HIGH) for all repositories
uv run python bin/ops.py myapp-production ecr

# Detailed findings with CVE names
uv run python bin/ops.py myapp-production ecr --verbose
```

<details>
<summary>Manual AWS CLI commands</summary>

```bash
# List images with scan status
aws ecr describe-images --repository-name myapp-production-web \
  --query 'imageDetails[*].[imageTags[0],imageScanStatus.status]' \
  --output table

# Get vulnerability findings for latest image
aws ecr describe-image-scan-findings \
  --repository-name myapp-production-web \
  --image-id imageTag=latest \
  --query 'imageScanFindings.findings[?severity==`CRITICAL` || severity==`HIGH`]'
```

</details>

**References**: [AWS ECR Lifecycle Policies](https://docs.aws.amazon.com/AmazonECR/latest/userguide/LifecyclePolicies.html)

### RDS PostgreSQL

**Current defaults** (modules/rds/main.tf:88-92):

- `backup_retention_period = 7` days
- `skip_final_snapshot = true`
- `backup_window = "03:00-04:00"` UTC

**Production recommendations**:

- Increase backup retention: `backup_retention_period = 35`
- Enable deletion protection: `deletion_protection = true`
- Disable skip final snapshot: `skip_final_snapshot = false`
- Enable enhanced monitoring: `monitoring_interval = 60`
- Set application DNS TTL < 30s for faster failover

**Key metrics to monitor**:

| Metric                   | Warning   | Critical  |
| ------------------------ | --------- | --------- |
| CPUUtilization           | > 70%     | > 90%     |
| FreeStorageSpace         | < 20GB    | < 10GB    |
| DatabaseConnections      | > 70% max | > 90% max |
| ReadLatency/WriteLatency | > 20ms    | > 100ms   |

**Maintenance commands**:

```bash
# Check backup status
aws rds describe-db-instance-automated-backups \
  --db-instance-identifier myapp-production-db

# Check pending maintenance
aws rds describe-pending-maintenance-actions \
  --resource-identifier arn:aws:rds:us-east-1:ACCOUNT:db:myapp-production-db
```

**References**: [AWS RDS Best Practices](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_BestPractices.html)

### ElastiCache Redis

**Current defaults** (modules/elasticache/main.tf):

- Single node (`num_cache_nodes = 1`)
- No Multi-AZ

**Production recommendations**:

- Use Multi-AZ with automatic failover
- Enable at-rest and in-transit encryption
- Consider reserved nodes (30-50% savings)

**Key metrics to monitor**:

| Metric                        | Warning   | Critical  |
| ----------------------------- | --------- | --------- |
| DatabaseMemoryUsagePercentage | > 70%     | > 85%     |
| EngineCPUUtilization          | > 80%     | > 90%     |
| ReplicationLag                | > 5s      | > 10s     |
| CurrConnections               | > 80% max | > 95% max |

**Maintenance commands**:

```bash
# Check cluster status
aws elasticache describe-cache-clusters \
  --cache-cluster-id myapp-production-cache \
  --show-cache-node-info

# Check replication group (if Multi-AZ)
aws elasticache describe-replication-groups \
  --replication-group-id myapp-production-cache
```

**References**: [AWS ElastiCache Redis Best Practices](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/BestPractices.html)

### ALB (Application Load Balancer)

**Key metrics to monitor**:

| Metric                 | Warning               | Critical |
| ---------------------- | --------------------- | -------- |
| UnHealthyHostCount     | > 0                   | > 1      |
| HTTPCode_ELB_5XX_Count | > 5/min               | > 20/min |
| TargetResponseTime     | > 2s p95              | > 5s p95 |
| RequestCount           | Monitor for anomalies | -        |

**Recommendations**:

- Enable access logs to S3 for debugging
- Enable deletion protection
- Configure appropriate idle timeout (default 60s)

**Maintenance commands**:

```bash
# Check target health
uv run python bin/ops.py myapp-production health
```

<details>
<summary>Manual AWS CLI commands</summary>

```bash
# Check target health
aws elbv2 describe-target-health \
  --target-group-arn $(./bin/tofu.sh output myapp-production -raw alb_target_group_arn)

# Get recent 5xx errors
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApplicationELB \
  --metric-name HTTPCode_ELB_5XX_Count \
  --dimensions Name=LoadBalancer,Value=app/myapp-production-alb/XXXXXXXXX \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Sum
```

</details>

**References**: [AWS ALB Monitoring](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-cloudwatch-metrics.html)

### CloudWatch Logs

The `ecs-service` module creates log groups with retention policies automatically. The `log_retention_days` variable controls retention (default: 30 days).

**Recommended retention periods**:

| Environment | Retention   | Setting                             |
| ----------- | ----------- | ----------------------------------- |
| Staging     | 7 days      | `log_retention_days = 7`            |
| Production  | 30 days     | `log_retention_days = 30` (default) |
| Audit logs  | 90-365 days | `log_retention_days = 90`           |

### ECS Fargate

**Using capacity-report.py**:

```bash
# Weekly right-sizing analysis
uv run python bin/capacity-report.py myapp-production --days 7

# Check for OOM kills
uv run python bin/capacity-report.py myapp-production --days 1
```

**Recommendations**:

- Enable Container Insights (already configured in ecs-cluster module)
- Configure auto-scaling based on CPU/memory metrics
- Use `min_replicas >= 2` for production services
- Monitor for OOM kills and adjust memory accordingly

**References**: [ECS Auto Scaling Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/capacity.html)

### S3

**Recommendations**:

- Configure lifecycle rules (Intelligent-Tiering after 30 days)
- Enable versioning for critical buckets
- Enable access logging to a separate bucket
- Review storage growth monthly

**Maintenance commands**:

```bash
# Check bucket size
aws s3 ls s3://myapp-production-media --summarize --recursive \
  | tail -2

# List lifecycle rules
aws s3api get-bucket-lifecycle-configuration \
  --bucket myapp-production-media
```

______________________________________________________________________

## Monitoring and Alerting

### Recommended CloudWatch Alarms

Use the `modules/cloudwatch-alarms/` module to create standard production alarms:

```hcl
module "alarms" {
  source = "../../modules/cloudwatch-alarms"

  name_prefix        = "myapp-production"
  notification_email = "ops@example.com"

  alb_arn_suffix         = module.alb.arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  rds_instance_id        = module.rds.db_instance_id
  elasticache_cluster_id = module.elasticache.cluster_id
  ecs_cluster_name       = module.ecs_cluster.name
  ecs_service_names      = ["web", "celery"]
}
```

**Alarm thresholds**:

| Alarm                | Metric                        | Threshold     | Severity |
| -------------------- | ----------------------------- | ------------- | -------- |
| ALB-5XX-High         | HTTPCode_ELB_5XX_Count        | > 10/5min     | Critical |
| ALB-Latency-High     | TargetResponseTime            | > 5s p95/5min | Warning  |
| RDS-CPU-High         | CPUUtilization                | > 80%/10min   | Warning  |
| RDS-Storage-Low      | FreeStorageSpace              | < 10GB        | Critical |
| RDS-Connections-High | DatabaseConnections           | > 80% max     | Warning  |
| ElastiCache-Memory   | DatabaseMemoryUsagePercentage | > 80%         | Warning  |
| ECS-Tasks-Failing    | RunningTaskCount < Desired    | 5min          | Critical |

### SNS Notification Setup

The cloudwatch-alarms module creates an SNS topic and email subscription:

1. Apply the module - creates SNS topic `myapp-production-alarms`
1. Check email for subscription confirmation
1. Click confirmation link in email

For additional notification channels (Slack, PagerDuty), see [SOMEDAY-MAYBE.md](../internal/SOMEDAY-MAYBE.md).

### Recommended Dashboard

Create a CloudWatch dashboard with these widgets:

**ECS**:

- CPU/Memory utilization per service
- Running task count vs desired
- Task launch/stop events

**ALB**:

- Request count (stacked by target group)
- Latency percentiles (p50, p95, p99)
- HTTP error rates (4xx, 5xx)

**RDS**:

- CPU utilization
- Database connections
- Read/Write IOPS
- Free storage space

**ElastiCache**:

- Memory usage percentage
- CPU utilization
- Current connections
- Cache hit ratio

### Log Insights Queries

**Find errors**:

```
fields @timestamp, @message
| filter @message like /ERROR|Exception|Traceback/
| sort @timestamp desc
| limit 100
```

**Slow requests**:

```
fields @timestamp, @message
| filter @message like /took.*ms/
| parse @message /took (?<duration>\d+)ms/
| filter duration > 1000
| sort duration desc
| limit 50
```

**Request volume by endpoint**:

```
fields @timestamp, @message
| filter @message like /GET|POST|PUT|DELETE/
| parse @message /"(?<method>GET|POST|PUT|DELETE) (?<path>[^ ]+)/
| stats count() as requests by path
| sort requests desc
| limit 20
```

______________________________________________________________________

## Emergency Procedures

Production operations are split into two tools:

- **`bin/ops.py`** - Read-only monitoring commands (safe to run anytime)
- **`bin/emergency.py`** - Commands that modify production state (use with care)

All emergency actions are logged to `local/emergency.log`.

### Quick Reference

**Monitoring (read-only - ops.py):**

```bash
# Run full audit (status, health, logs, maintenance, ECR vulnerabilities)
uv run python bin/ops.py myapp-production audit

# View current state (services, task definitions, RDS, snapshots)
uv run python bin/ops.py myapp-production status

# Check ALB target health
uv run python bin/ops.py myapp-production health

# Scan recent logs for errors
uv run python bin/ops.py myapp-production logs --minutes 60

# Check pending maintenance (RDS, ElastiCache)
uv run python bin/ops.py myapp-production maintenance

# Check ECR vulnerability findings
uv run python bin/ops.py myapp-production ecr
```

**Emergency operations (modifies production - emergency.py):**

```bash
# Roll back to previous task definition (creates checkpoint first)
uv run python bin/emergency.py myapp-production rollback --service web

# Scale services quickly
uv run python bin/emergency.py myapp-production scale --service web --count 10
uv run python bin/emergency.py myapp-production scale --all --multiplier 2

# Force new deployment (replace unhealthy containers)
uv run python bin/emergency.py myapp-production force-deploy --service web

# Create emergency snapshot before making changes
uv run python bin/emergency.py myapp-production snapshot

# Restore database (creates NEW instance, doesn't modify original)
uv run python bin/emergency.py myapp-production restore-db --snapshot <snapshot-id>
uv run python bin/emergency.py myapp-production restore-db --time "2026-02-04T12:00:00Z"

# View and restore from checkpoints
uv run python bin/emergency.py myapp-production revert --list
uv run python bin/emergency.py myapp-production revert --checkpoint emergency-2026-02-04-120000.json
```

### Rollback Deployment

If a deployment causes issues, roll back to a previous task definition:

```bash
# View current state and recent revisions
uv run python bin/ops.py myapp-production status

# Interactive rollback (shows services, then revisions, prompts for selection)
uv run python bin/emergency.py myapp-production rollback

# Direct rollback to previous revision
uv run python bin/emergency.py myapp-production rollback --service web

# Rollback to specific revision
uv run python bin/emergency.py myapp-production rollback --service web --revision 42
```

The tool automatically:

- Creates a checkpoint before making changes
- Shows environment variable differences between revisions
- Monitors deployment progress
- Logs all actions to `local/emergency.log`

If the rollback was wrong, restore the previous state:

```bash
uv run python bin/emergency.py myapp-production revert --list
uv run python bin/emergency.py myapp-production revert --checkpoint emergency-2026-02-04-120000.json
```

### Database Recovery

Database restore operations create a **new** RDS instance with a `-restore` suffix. This is intentional—the original database is never modified, so you can:

- Compare data between original and restored instances
- Go back to the original by not updating the app config
- Delete the `-restore` instance if the restore wasn't needed

```bash
# Create an emergency snapshot first (recommended)
uv run python bin/emergency.py myapp-production snapshot

# Restore from a specific snapshot (interactive if snapshot not specified)
uv run python bin/emergency.py myapp-production restore-db --snapshot myapp-production-db-emergency-2026-02-04-120000

# Point-in-time recovery
uv run python bin/emergency.py myapp-production restore-db --time "2026-02-04T12:00:00Z"
```

After restore completes (10-30 minutes):

1. The new instance will be at `myapp-production-db-restore`
1. Update your application's `DATABASE_URL` to point to the new instance
1. When done, delete the restore instance or the original as appropriate

### Scale Up Quickly

During traffic spikes:

```bash
# Scale specific service
uv run python bin/emergency.py myapp-production scale --service web --count 10

# Scale all services by multiplier
uv run python bin/emergency.py myapp-production scale --all --multiplier 2

# Reset to configured replicas (from terraform)
uv run python bin/emergency.py myapp-production scale --reset
```

### Force New Deployment

If containers are unhealthy but not being replaced:

```bash
# Force new deployment of a specific service
uv run python bin/emergency.py myapp-production force-deploy --service web

# Force new deployment of all services
uv run python bin/emergency.py myapp-production force-deploy --all
```

<details>
<summary>Manual CLI commands (if emergency.py unavailable)</summary>

### Rollback (Manual)

```bash
# List recent task definitions
aws ecs list-task-definitions \
  --family-prefix myapp-production-web \
  --sort DESC \
  --max-items 5

# Rollback to previous revision
aws ecs update-service \
  --cluster myapp-production-cluster \
  --service web \
  --task-definition myapp-production-web:PREVIOUS_REVISION

# Monitor rollback
aws ecs describe-services \
  --cluster myapp-production-cluster \
  --services web \
  --query 'services[0].deployments'
```

### Database Recovery (Manual)

```bash
# Restore to a specific point in time
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier myapp-production-db \
  --target-db-instance-identifier myapp-production-db-restore \
  --restore-time 2026-02-04T12:00:00Z \
  --db-instance-class db.r6g.large \
  --vpc-security-group-ids sg-XXXXXXXX \
  --db-subnet-group-name myapp-production-db-subnet

# Wait for restore to complete
aws rds wait db-instance-available \
  --db-instance-identifier myapp-production-db-restore
```

### Scale (Manual)

```bash
# Temporarily increase desired count
aws ecs update-service \
  --cluster myapp-production-cluster \
  --service web \
  --desired-count 10

# Or update auto-scaling min/max
aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id service/myapp-production-cluster/web \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 5 \
  --max-capacity 20
```

</details>

______________________________________________________________________

## Incident Response

When something goes wrong in production, follow this structured approach.

### Severity Definitions

| Severity          | Definition                              | Examples                                  | Response Time             |
| ----------------- | --------------------------------------- | ----------------------------------------- | ------------------------- |
| **P1 - Critical** | Total service outage or data loss risk  | All users affected, no workaround         | Immediate (within 15 min) |
| **P2 - Major**    | Significant degradation, partial outage | Key feature unavailable, slow performance | Within 1 hour             |
| **P3 - Minor**    | Limited impact, workaround available    | Cosmetic issue, single user affected      | Within 1 business day     |

### Immediate Response (First 15 Minutes)

1. **Assess the situation**

   ```bash
   # Quick health check
   uv run python bin/ops.py myapp-production audit
   ```

1. **Determine severity** using definitions above

1. **Communicate** (for P1/P2)

   - Notify stakeholders: "We're aware of [issue] and investigating"
   - Post to team channel with initial assessment

1. **Stabilize if possible**

   ```bash
   # Rollback if recent deployment caused issue
   uv run python bin/emergency.py myapp-production rollback --service web

   # Scale up if capacity issue
   uv run python bin/emergency.py myapp-production scale --all --multiplier 2

   # Force redeploy if containers unhealthy
   uv run python bin/emergency.py myapp-production force-deploy --all
   ```

### During the Incident

- **Keep notes** - Document timeline, actions taken, findings
- **Communicate updates** - Every 30 min for P1, every hour for P2
- **Focus on restoration** - Fix the symptom first, root cause later

### Resolution

1. **Verify service restored**

   ```bash
   uv run python bin/ops.py myapp-production health
   uv run python bin/ops.py myapp-production logs --minutes 10
   ```

1. **Communicate resolution**

   - "Service restored at [time]. We'll follow up with details."

1. **Document the incident** (see Postmortem section)

### Postmortem Process

**When required:** All P1 incidents, P2 incidents lasting > 1 hour

**Timeline:** Complete within 5 business days of incident

**Template:**

```markdown
# Incident: [Brief Title]

**Date:** YYYY-MM-DD
**Duration:** X hours Y minutes
**Severity:** P1/P2
**Author:** [name]

## Summary
One paragraph describing what happened and impact.

## Timeline
- HH:MM - Issue reported/detected
- HH:MM - Investigation started
- HH:MM - Root cause identified
- HH:MM - Fix deployed
- HH:MM - Service restored

## Root Cause
What actually broke and why.

## Impact
- Users affected: X
- Duration: Y minutes
- Data loss: None / [describe]

## What Went Well
- [Things that helped]

## What Went Wrong
- [Things that hurt]

## Action Items
| Action | Owner | Due Date | Status |
|--------|-------|----------|--------|
| [Fix] | [name] | YYYY-MM-DD | Open |

## Lessons Learned
What we'll do differently next time.
```

**Store postmortems:** In the application repo's `docs/postmortems/` directory.

### Communication Templates

**Initial notification (P1/P2):**

> We're aware of an issue affecting [service]. Users may experience [symptom]. We're actively investigating and will provide updates.

**Update during incident:**

> Update on [service] issue: We've identified [cause/area]. We're [action being taken]. Next update in [30 min/1 hour].

**Resolution:**

> The issue affecting [service] has been resolved as of [time]. [Brief description of fix]. We'll share a postmortem with more details.

______________________________________________________________________

## Tooling Gaps and Workarounds

Some production operations require manual steps or CLI commands:

| Gap                           | Workaround                                                |
| ----------------------------- | --------------------------------------------------------- |
| RDS backup retention > 7 days | Override `backup_retention_period` in terraform.tfvars    |
| RDS deletion protection       | Override `deletion_protection = true` in terraform.tfvars |
| CloudWatch alarms             | Use `modules/cloudwatch-alarms/` module                   |

### RDS Backup Testing

Monthly procedure to verify backups are restorable:

```bash
# 1. View available snapshots
uv run python bin/ops.py myapp-production status
# Look at the "Recent Snapshots" section

# 2. Restore from a snapshot (creates myapp-production-db-restore instance)
uv run python bin/emergency.py myapp-production restore-db --snapshot <snapshot-id>
# Or run interactively to select from a list:
uv run python bin/emergency.py myapp-production restore-db

# 3. Wait for restore (10-30 minutes) - check status with:
aws rds describe-db-instances --db-instance-identifier myapp-production-db-restore \
  --query 'DBInstances[0].DBInstanceStatus'

# 4. Verify connectivity (from bastion or ECS task)
psql "postgres://user:pass@myapp-production-db-restore.xxxxx.rds.amazonaws.com/myapp" \
  -c "SELECT COUNT(*) FROM users;"

# 5. Delete test instance
aws rds delete-db-instance \
  --db-instance-identifier myapp-production-db-restore \
  --skip-final-snapshot
```

Note: The `restore-db` command creates a new instance with `-restore` suffix and never modifies the original database.

______________________________________________________________________

## Compliance Considerations

### Audit Logging

- **CloudTrail**: Enable in all regions, log to S3 with lifecycle policies
  ```bash
  aws cloudtrail describe-trails --query 'trailList[*].[Name,IsMultiRegionTrail,S3BucketName]'
  ```
- Enable CloudTrail log file validation for integrity verification
- Retain logs per compliance requirements (typically 1-7 years)

### Access Control

- Use IAM roles, never long-lived access keys
- Enable MFA for all console users
- Review IAM policies quarterly
- Use AWS Organizations SCPs for guardrails

### Data Protection

- **Encryption at rest**: S3 (SSE-S3), RDS, ElastiCache - all enabled by default
- **Encryption in transit**: TLS everywhere (ALB terminates, internal VPC traffic)
- **Secrets**: SSM Parameter Store SecureString or Secrets Manager

### Network Security

- VPC Flow Logs for network monitoring
- Security groups follow least privilege
- No public IPs on ECS tasks (NAT Gateway for outbound)
- WAF for web application protection

### Detection and Response

- **GuardDuty**: Enable for threat detection
- **AWS Config**: Enable for compliance rules
- **CloudWatch**: Regular log review for anomalies

______________________________________________________________________

## Appendices

### AWS CLI Quick Reference

```bash
# ECS
aws ecs list-services --cluster myapp-production-cluster
aws ecs describe-services --cluster myapp-production-cluster --services web
aws ecs update-service --cluster myapp-production-cluster --service web --desired-count 3

# RDS
aws rds describe-db-instances --db-instance-identifier myapp-production-db
aws rds reboot-db-instance --db-instance-identifier myapp-production-db
aws rds describe-db-snapshots --db-instance-identifier myapp-production-db

# ElastiCache
aws elasticache describe-cache-clusters --cache-cluster-id myapp-production-cache
aws elasticache reboot-cache-cluster --cache-cluster-id myapp-production-cache --cache-node-ids-to-reboot 0001

# CloudWatch
aws logs tail /ecs/myapp-production --follow
aws cloudwatch describe-alarms --alarm-name-prefix myapp-production

# SSM (secrets)
aws ssm get-parameter --name /myapp/production/secret-key --with-decryption
```

### Cost Optimization Checklist

- [ ] Reserved Instances for RDS (1 or 3 year)
- [ ] Reserved Nodes for ElastiCache (1 or 3 year)
- [ ] Fargate Spot for interruptible workloads
- [ ] S3 Intelligent-Tiering lifecycle rules
- [ ] CloudWatch Logs retention policies
- [ ] Right-sizing via `capacity-report.py`
- [ ] NAT Gateway data transfer review
- [ ] CloudFront caching optimization
- [ ] Stop non-production environments during off-hours (see below)

### Environment Start/Stop

Use `bin/environment.py` to stop staging environments during off-hours to save costs:

```bash
# Show status of all environments
uv run python bin/environment.py status

# Stop staging environment (scales ECS to 0, stops RDS)
uv run python bin/environment.py stop myapp-staging

# Start staging environment (waits for RDS, then scales ECS)
uv run python bin/environment.py start myapp-staging
```

Notes:

- ElastiCache and ALB cannot be stopped (only deleted)
- RDS auto-restarts after 7 days if stopped (AWS limitation)
- Data is preserved when stopped

### Security Checklist

- [ ] CloudTrail enabled in all regions
- [ ] GuardDuty enabled
- [ ] AWS Config enabled with required rules
- [ ] VPC Flow Logs enabled
- [ ] WAF enabled for ALB
- [ ] RDS encryption enabled
- [ ] S3 bucket policies reviewed
- [ ] IAM policies follow least privilege
- [ ] MFA enabled for all users
- [ ] Secrets rotated quarterly

______________________________________________________________________

## Related Documentation

- [ARCHITECTURE.md](../background/ARCHITECTURE.md) - Infrastructure architecture and costs
- [DEPLOYMENT-GUIDE.md](../DEPLOYMENT-GUIDE.md) - Deployment procedures
- [CONFIG-REFERENCE.md](../CONFIG-REFERENCE.md) - Configuration options
- [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) - Common issues
- [SOMEDAY-MAYBE.md](../internal/SOMEDAY-MAYBE.md) - Future improvements
