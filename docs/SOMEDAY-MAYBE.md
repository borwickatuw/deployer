# Someday/Maybe Ideas

Ideas for future improvements that aren't urgent.

---

## Notification Improvements

### PagerDuty Integration

**Current state**: CloudWatch alarms notify via SNS email subscriptions.

**Enhancement**: Integrate with PagerDuty for:
- Real-time alerting with escalation policies
- On-call schedules and rotations
- Incident management and tracking
- Mobile push notifications

**Implementation approach**:
```hcl
# Option 1: SNS → PagerDuty integration
resource "aws_sns_topic_subscription" "pagerduty" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "https"
  endpoint  = "https://events.pagerduty.com/integration/XXXXX/enqueue"
}

# Option 2: EventBridge → PagerDuty
resource "aws_cloudwatch_event_rule" "alarm_state_change" {
  name        = "${var.name_prefix}-alarm-to-pagerduty"
  description = "Route alarm state changes to PagerDuty"

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    detail = {
      state = { value = ["ALARM"] }
    }
  })
}
```

**Complexity**: Medium - requires PagerDuty account and API key management.

### Slack Integration

**Current state**: Email notifications only.

**Enhancement**: Send alerts to Slack channels for:
- Warning-level alerts (non-paging)
- Deployment notifications
- Daily summary reports

**Implementation approach**:
```hcl
# Lambda function to format and send to Slack
resource "aws_lambda_function" "slack_notifier" {
  function_name = "${var.name_prefix}-slack-notifier"
  # ... Lambda configuration
}

# SNS → Lambda → Slack
resource "aws_sns_topic_subscription" "slack" {
  topic_arn = aws_sns_topic.warnings.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.slack_notifier.arn
}
```

**Complexity**: Medium - requires Lambda function and Slack webhook management.

### OpsGenie Integration

**Alternative to PagerDuty** with similar features:
- Alerting and on-call management
- Often more cost-effective for smaller teams
- Native AWS integration available

---

## Infrastructure Enhancements

### Automated RDS Restore Testing

**Current state**: Manual monthly process documented in HOWTO-PRODUCTION.md.

**Enhancement**: Scheduled Lambda that:
1. Creates temporary RDS instance from latest snapshot
2. Runs basic connectivity/query test
3. Reports success/failure to SNS
4. Deletes temporary instance

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

### ECR Vulnerability Notifications

**Current state**: Manual console checks.

**Enhancement**: EventBridge rule to notify on vulnerability findings:

```hcl
resource "aws_cloudwatch_event_rule" "ecr_scan" {
  name = "${var.name_prefix}-ecr-vulnerability"

  event_pattern = jsonencode({
    source      = ["aws.ecr"]
    detail-type = ["ECR Image Scan State Change"]
    detail = {
      scan-status           = ["COMPLETE"]
      finding-severity-counts = {
        CRITICAL = [{ "numeric": [">", 0] }]
      }
    }
  })
}
```

**Complexity**: Low-Medium - straightforward EventBridge configuration.

### Cost Anomaly Detection

**Enhancement**: AWS Budgets with anomaly detection:

```hcl
resource "aws_budgets_budget" "anomaly" {
  name         = "${var.name_prefix}-cost-anomaly"
  budget_type  = "COST"
  limit_amount = "2000"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator = "GREATER_THAN"
    threshold           = 80
    threshold_type      = "PERCENTAGE"
    notification_type   = "FORECASTED"
    subscriber_email_addresses = [var.notification_email]
  }
}
```

**Complexity**: Low - native AWS service.

### Blue-Green Deployments

**Current state**: Rolling deployments via ECS.

**Enhancement**: CodeDeploy integration for blue-green:
- Instant rollback capability
- Traffic shifting (canary, linear, all-at-once)
- Pre/post deployment hooks

**Complexity**: High - significant architecture change.

### Multi-Region Disaster Recovery

**Enhancement**: Secondary region with:
- RDS read replica (can be promoted)
- S3 cross-region replication
- Route53 health checks and failover
- Infrastructure as code for standby region

**Complexity**: Very High - doubles infrastructure, requires careful planning.

---

## Monitoring Enhancements

### Synthetic Monitoring

**Enhancement**: CloudWatch Synthetics canaries:

```hcl
resource "aws_synthetics_canary" "health_check" {
  name                 = "${var.name_prefix}-health"
  artifact_s3_location = "s3://${var.artifacts_bucket}/canary/"
  execution_role_arn   = aws_iam_role.canary.arn
  handler              = "health_check.handler"
  runtime_version      = "syn-nodejs-puppeteer-6.2"

  schedule {
    expression = "rate(5 minutes)"
  }

  # Canary script checks:
  # - Homepage loads
  # - Login works
  # - Key API endpoints respond
}
```

**Complexity**: Medium - requires canary script development.

### Real User Monitoring (RUM)

**Enhancement**: CloudWatch RUM for frontend performance:
- Page load times by geography
- JavaScript error tracking
- User session analysis

**Complexity**: Medium - requires frontend code changes.

### Distributed Tracing (X-Ray)

**Enhancement**: AWS X-Ray integration for:
- Request tracing across services
- Performance bottleneck identification
- Service map visualization

**Complexity**: Medium - requires application instrumentation.

### Dashboards as Code

**Current state**: Dashboards created manually in AWS Console.

**Enhancement**: CloudWatch dashboards managed in Terraform via `aws_cloudwatch_dashboard` resource. A standard production dashboard would include:
- ECS: CPU/Memory per service, running task count
- ALB: Request count, latency percentiles, error rates
- RDS: CPU, connections, IOPS, free storage
- ElastiCache: Memory, CPU, connections

**Example**:
```hcl
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name_prefix}-overview"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ServiceName", "web", "ClusterName", var.cluster_name]
          ]
          title = "ECS CPU Utilization"
        }
      }
      # ... more widgets
    ]
  })
}
```

**Why someday**: Dashboards are easy to create manually in the console and don't drift much once created. The Terraform JSON is verbose (100+ lines for a basic dashboard) for relatively little ongoing benefit.

**Complexity**: Medium - verbose but straightforward.

---

## Developer Experience

### Deploy Preview Environments

**Enhancement**: Ephemeral environments for pull requests:
- Spin up environment on PR open
- Run integration tests
- Tear down on PR close/merge

**Complexity**: Very High - requires significant CI/CD work.

### Local Development Environment

**Enhancement**: Docker Compose configuration matching production:
- LocalStack for AWS services
- Same container images as production
- Seeded test data

**Complexity**: Medium - useful but requires maintenance.

### CLI Improvements

**Enhancement ideas**:
- `bin/status.py` - unified view of all environments
- `bin/logs.py` - simplified log tailing across services
- `bin/ssh.py` - ECS Exec wrapper with service selection

**Complexity**: Low-Medium per script.

### Emergency Download Backup

**Current state**: `bin/emergency.py` supports RDS snapshot restore but not downloading database backups locally. (Note: Read-only monitoring is now in `bin/ops.py`, while `bin/emergency.py` handles write operations.)

**Enhancement**: Add `download-backup` command to emergency.py:
```bash
uv run python bin/emergency.py myapp-production download-backup --output backup.sql
```

**Implementation approach**:
1. Run a one-off ECS task with `pg_dump` command
2. Stream output to S3 (ECS tasks can't easily stream to local)
3. Download from S3 to local machine
4. Clean up S3 object

Alternatively:
- Use RDS native S3 export feature (requires setup)
- Run pg_dump through a bastion host

**Complexity**: High - requires ECS task orchestration, S3 bucket access, and handling large files. The RDS restore approach (`restore-db`) is usually more practical for recovery scenarios.

**Why deferred**: For most emergencies, restoring to a new RDS instance is faster and safer than downloading a backup. If you need a local copy for analysis, you can:
1. Create a restore instance: `emergency.py restore-db --snapshot <id>`
2. Connect to the restore instance and run `pg_dump` manually
3. Delete the restore instance when done

---

## Checkov Deferred Items

These Checkov findings are valid but require infrastructure changes. Currently suppressed via `--skip-check` in the Makefile.

### RDS Enhancements

| Check | Description | Complexity | Notes |
|-------|-------------|------------|-------|
| CKV_AWS_16 | Storage encryption at rest | High | Requires snapshot-restore for existing instances |
| CKV_AWS_161 | IAM authentication | Medium | Requires app changes to use IAM auth |
| CKV_AWS_118 | Enhanced monitoring | Low | Needs IAM role for monitoring |
| CKV_AWS_129 | Database logging | Medium | Needs parameter group changes |
| CKV_AWS_353 | Performance Insights | Low | Check instance class support |
| CKV2_AWS_30 | Query logging | Medium | Needs parameter group with `log_statement` |

### Infrastructure Logging

| Check | Description | Complexity | Notes |
|-------|-------------|------------|-------|
| CKV2_AWS_11 | VPC flow logs | Medium | Needs CloudWatch log group or S3 destination, adds cost |
| CKV_AWS_91 | ALB access logging | Low | Needs S3 bucket for logs |
| CKV_AWS_338 | CloudWatch 1-year retention | Low | Increase retention_in_days |

### Other

| Check | Description | Complexity | Notes |
|-------|-------------|------------|-------|
| CKV_AWS_134 | ElastiCache automatic backups | Low | Single-node cache, can rebuild from scratch |
| CKV_AWS_150 | ALB deletion protection | Low | Already configurable but defaults to false |
| CKV_AWS_51 | ECR immutable tags | Medium | Deploy workflow uses `latest` tag pattern |

---

## Security Enhancements

### Secrets Rotation

**Enhancement**: Automated secrets rotation:
- Database credentials via Secrets Manager rotation
- API keys with scheduled Lambda
- Notification on rotation

**Complexity**: Medium - requires careful coordination with applications.

### Security Hub Integration

**Enhancement**: AWS Security Hub for:
- Aggregated security findings
- Compliance standards (CIS, PCI DSS)
- Automated remediation

**Complexity**: Medium - native AWS service but requires tuning.

### Container Image Signing

**Enhancement**: Sign container images with cosign/Sigstore:
- Verify image provenance
- Prevent unauthorized images
- Supply chain security

**Complexity**: High - requires CI/CD changes and verification policies.

---

## Advanced WAF Features

The `modules/waf` module provides baseline protection. Future enhancements could include:

### Account Fraud Protection (ATP/ACFP)
AWS offers paid rule groups for login and signup endpoints:
- **Account Takeover Prevention (ATP)**: Protects login endpoints from credential stuffing
- **Account Creation Fraud Prevention (ACFP)**: Blocks fraudulent account creation

These require app-specific configuration (login/signup endpoint paths) and cost ~$10/month + per-request fees.

### Client-Side SDK Integration
AWS WAF's advanced bot protection works best with JavaScript/mobile SDKs that provide challenge tokens:
- Enables CAPTCHA and silent challenges
- Improves bot detection accuracy
- Requires app code changes to integrate the SDK

### CloudFront WAF Integration
Currently the WAF module only supports ALB attachment. For apps using CloudFront:
- WAF can attach to CloudFront distributions for edge-level protection
- Requires scope="CLOUDFRONT" and us-east-1 region
- Blocks bad traffic before it reaches the origin

### Centralized WAF Management
For organizations with many environments:
- **AWS Firewall Manager**: Enforce WAF policies across multiple accounts/regions
- Requires AWS Organizations setup
- Provides compliance reporting and automatic remediation

### Advanced Rate Limiting
The current module uses simple IP-based rate limiting. Advanced options:
- Rate limit by URI path (different limits for /api vs /static)
- Rate limit by custom header (e.g., API key)
- Rate limit by authenticated user (requires forwarded headers)
- Aggregate by IP + URI for more precise control

### Shield Advanced
For high-value applications needing DDoS protection:
- $3,000/month subscription
- Automatic application layer DDoS mitigation
- 24/7 access to AWS DDoS Response Team
- Cost protection (credits for scaling during attacks)

## Direct-to-S3 Uploads for Large Files

The current large file upload approach requires increasing timeouts at multiple layers (gunicorn, ALB, WAF). A better pattern for UI uploads is **direct-to-S3 with presigned URLs**:

### How It Works
1. User selects file in browser
2. JavaScript requests a presigned upload URL from Django
3. Django generates S3 presigned POST/PUT URL (with size limits, content-type restrictions)
4. Browser uploads directly to S3 (bypasses Django, ALB, gunicorn entirely)
5. S3 notifies Django via Lambda/SQS, or browser calls Django to confirm upload
6. Django creates the File record and queues transcoding

### Benefits
- **No timeout concerns**: S3 handles the upload, not Django
- **Resumable**: Can use S3 multipart uploads for pause/resume support
- **Progress tracking**: Browser can show real upload progress
- **Scalable**: Django workers aren't blocked during uploads
- **Cost effective**: Less ALB/ECS compute time

### Implementation Notes
- Use `boto3.client('s3').generate_presigned_post()` for browser uploads
- Set conditions: content-length-range, content-type, key prefix
- Consider S3 event notifications (Lambda or SQS) for upload completion
- For multipart (resumable), look at libraries like Uppy, Evaporate.js, or AWS Amplify
- CORS configuration needed on S3 bucket

### When to Use
- Files > 100 MB
- When upload reliability matters (resumable uploads)
- High-traffic sites where Django worker availability is precious

### Current Approach (for reference)
The timeout-based approach works for occasional large uploads:
- `DATA_UPLOAD_MAX_MEMORY_SIZE` (Django): 1 GB
- `--timeout 1800` (gunicorn): 30 minutes
- `alb_idle_timeout`: 1800 seconds
- WAF `SizeRestrictions_BODY`: excluded

---

## Prioritization Notes

When considering implementation:

1. **High value, low effort**: ECR vulnerability notifications, cost anomaly detection
2. **High value, medium effort**: Slack integration, synthetic monitoring
3. **High value, high effort**: PagerDuty integration, automated restore testing
4. **Lower priority**: Blue-green deployments, multi-region DR (only if specifically needed)

---

## Contributing Ideas

To add ideas to this list:

1. Describe the current state/gap
2. Explain the proposed enhancement
3. Outline implementation approach
4. Estimate complexity (Low/Medium/High/Very High)
5. Note any dependencies or prerequisites

---

We have a secrets audit already but I always forget to use it. Maybe deploy.py should alert
when there are unused secrets

---

## Remove Django Default Commands Fallback

**Current state**: `src/deployer/core/config.py` has two hardcoded fallbacks for backward compatibility:

1. `DJANGO_DEFAULT_COMMANDS` dict (lines 636-644) - provides default commands for `migrate`, `shell`, `dbshell`, `createsuperuser`, `collectstatic`, `check`, `showmigrations` when not defined in deploy.toml

2. Hardcoded DDL check (lines 619, 632) - assumes `migrate` and `makemigrations` need DDL credentials even if deploy.toml doesn't specify `ddl = true`

**Problem**: These fallbacks:
- Create "magic" behavior that's not explicit in deploy.toml
- Mix available commands from two sources (deploy.toml + hardcoded defaults)
- Could mask missing command definitions

**Enhancement**: Remove fallbacks and require explicit command definitions:
- All commands must be defined in deploy.toml's `[commands]` section
- DDL requirements must be explicit via `ddl = true`
- Fail fast with clear error if command not found

**Migration path**:
1. Audit all deploy.toml files to ensure they define all needed commands
2. Add `shell`, `dbshell`, `createsuperuser` to deploy.toml files that use them
3. Remove `DJANGO_DEFAULT_COMMANDS` and hardcoded DDL checks
4. Update error messages to suggest adding missing commands to deploy.toml

**Complexity**: Low - straightforward code removal and config updates.

---

## SRE / Incident Response Improvements

Enhancements for Site Reliability Engineering practices and incident management.

### SLOs in config.toml

**Current state**: SLO targets are documented in prose (OPERATIONS.md, MONITORING-BEST-PRACTICE.md).

**Enhancement**: Define SLOs programmatically in environment config.toml:

```toml
[slo]
availability_target = 99.9        # percent
latency_p95_ms = 500              # milliseconds
latency_p99_ms = 1000             # milliseconds
error_rate_percent = 0.1          # percent

[slo.alerting]
# When to alert vs when to page
availability_warning = 99.5       # Warning at 99.5%
availability_critical = 99.0      # Page at 99.0%
```

**Benefits:**
- CloudWatch alarms module can read targets and set thresholds automatically
- `ops.py status` can report SLO compliance
- Drift between documentation and reality becomes impossible
- Error budget calculations become possible

**Implementation approach:**
1. Add `[slo]` section to config.toml schema
2. Update cloudwatch-alarms module to use SLO values for thresholds
3. Add `ops.py slo` command to report current compliance
4. Consider error budget dashboard in CloudWatch

**Complexity**: Medium - requires config schema update, alarm module changes, new ops.py command.

### Incident Response Tooling

**Current state**: `emergency.py` handles operational actions; incident tracking is manual.

**Enhancement**: Add incident management commands to ops.py:

```bash
# Start tracking an incident
uv run python bin/ops.py myapp-production incident start "Database slow queries"
# Creates timestamped log at local/incidents/2026-02-08-1430-database-slow.md
# Captures current state (status, health, recent logs)

# Add notes during incident
uv run python bin/ops.py myapp-production incident note "Identified slow query in users table"

# Resolve and generate postmortem template
uv run python bin/ops.py myapp-production incident resolve
# Prompts for resolution summary
# Generates postmortem template with timeline
```

**Features:**
- Automatic timeline from command history during incident
- Pre-populated postmortem with CloudWatch metrics from incident window
- Links to relevant log queries
- Integration with emergency.py actions (auto-logged to incident)

**Complexity**: Medium - mostly CLI wrapper and file management.

### Postmortem Automation

**Current state**: Postmortems are written manually.

**Enhancement**: Auto-generate postmortem template with data:

```bash
uv run python bin/ops.py myapp-production postmortem generate --start "2026-02-08T14:30:00Z" --end "2026-02-08T15:45:00Z"
```

**Generated content:**
- Timeline of emergency.py commands run during window
- CloudWatch metrics graphs (exported as images or links)
- Error counts from logs
- Deployment events in the timeframe
- Pre-filled template sections

**Complexity**: Medium-High - requires CloudWatch API integration, metric extraction.

### Error Budget Dashboard

**Current state**: No error budget tracking.

**Enhancement**: Calculate and display remaining error budget:

```bash
uv run python bin/ops.py myapp-production slo
# Output:
# SLO Status for myapp-production (rolling 30 days)
#
# Availability: 99.94% (target: 99.9%) ✓
#   Error budget: 43 min allowed, 26 min consumed, 17 min remaining
#
# Latency p95: 320ms (target: 500ms) ✓
#   All requests within budget
#
# Error rate: 0.08% (target: 0.1%) ✓
#   Error budget: 0.1% allowed, 0.08% current
```

**Implementation:**
- Query CloudWatch metrics for rolling window
- Calculate against SLO targets from config.toml
- Display remaining budget

**Complexity**: Medium - CloudWatch queries, calculations, formatting.

### Prioritization

| Enhancement | Value | Effort | Priority |
|-------------|-------|--------|----------|
| SLOs in config.toml | High | Medium | 1 |
| Incident start/resolve commands | Medium | Low | 2 |
| Error budget dashboard | Medium | Medium | 3 |
| Postmortem automation | Low | High | 4 |
