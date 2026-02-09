# Staging Environments

Staging environments have special features not used in production: Cognito authentication to restrict access, and automatic scheduling to reduce costs.

## Cognito Authentication

Staging environments can require login before accessing any content. Authentication happens at the ALB level - your application code doesn't need changes.

### Enabling Cognito

In your environment's `terraform.tfvars`:

```hcl
domain_name          = "staging.example.com"
route53_zone_id      = "Z1234567890ABC"
cognito_auth_enabled = true
```

Apply the changes:

```bash
./bin/tofu.sh apply myapp-staging

# Or use rollout to run init, plan, and apply in sequence
./bin/tofu.sh rollout myapp-staging
```

### Managing Users

```bash
# List users
uv run python bin/cognito.py list myapp-staging

# Create user (copies welcome message to clipboard)
uv run python bin/cognito.py create myapp-staging \
  --username alice@example.com --clipboard

# Create with specific password
uv run python bin/cognito.py create myapp-staging \
  --username alice@example.com -p "SecurePass123!"

# Disable/enable user
uv run python bin/cognito.py disable myapp-staging --username alice@example.com
uv run python bin/cognito.py enable myapp-staging --username alice@example.com

# Reset password
uv run python bin/cognito.py reset-password myapp-staging --username alice@example.com

# Delete user
uv run python bin/cognito.py delete myapp-staging --username alice@example.com
```

**Password requirements:** Minimum 12 characters, at least one uppercase, lowercase, and number.

### Test Account for Automation

For automated health checks of Cognito-protected environments, create a dedicated test account:

```bash
# Generate password
PASSWORD=$(python3 -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20)))")

# Create user
uv run python bin/cognito.py create myapp-staging \
  --username deployer@test.local -p "$PASSWORD"

# Store in SSM
aws ssm put-parameter \
  --name "/deployer/myapp-staging/cognito-test-password" \
  --type SecureString \
  --value "$PASSWORD"
```

Retrieve credentials later:

```bash
aws ssm get-parameter \
  --name "/deployer/myapp-staging/cognito-test-password" \
  --with-decryption --query 'Parameter.Value' --output text
```

---

## Cost Savings & Scheduling

Staging environments can be stopped during off-hours to reduce costs.

### What Gets Stopped

| Resource | When Stopped | Savings |
|----------|--------------|---------|
| ECS Fargate | Scaled to 0 | 100% |
| RDS PostgreSQL | Stopped | ~90% (storage charges continue) |
| ElastiCache | Keeps running | 0% (cannot be stopped) |
| ALB, NAT Gateway | Keep running | 0% |

**Estimated savings:** ~50-60% reduction in staging costs.

### Manual Control

```bash
# Check status
./bin/environment.py status myapp-staging

# Stop (scales ECS to 0, stops RDS)
./bin/environment.py stop myapp-staging

# Start (starts RDS, waits for it, scales ECS back up)
./bin/environment.py start myapp-staging
```

### Automatic Scheduling

Deploy the scheduler module for automatic start/stop:

```hcl
# In environments/myapp-staging/main.tf
module "scheduler" {
  source = "../../modules/staging-scheduler"

  environment_name = "myapp-staging"
  cluster_name     = module.infrastructure.ecs_cluster_name
  rds_instance_id  = module.infrastructure.rds_instance_id

  # Default: 7 AM start, 7 PM stop, Monday-Friday (Pacific)
  enabled = true
}
```

Apply:

```bash
./bin/tofu.sh apply myapp-staging
```

### Custom Schedule

Schedules use EventBridge cron format (UTC time):

```hcl
module "scheduler" {
  # ...
  stop_schedule  = "cron(0 3 ? * TUE-SAT *)"   # 7 PM Pacific (next day UTC)
  start_schedule = "cron(0 15 ? * MON-FRI *)"  # 7 AM Pacific
}
```

**Time zone note:** Pacific = UTC-8 (winter) or UTC-7 (summer).

### Testing the Scheduler

```bash
# Invoke Lambda manually
aws lambda invoke \
  --function-name myapp-staging-scheduler \
  --payload '{"action": "stop"}' \
  --cli-binary-format raw-in-base64-out \
  response.json

# View logs
aws logs tail /aws/lambda/myapp-staging-scheduler --follow
```

---

## Custom Error Pages

When staging environments are stopped (ECS scaled to 0, RDS stopped), the ALB returns raw 502/503 errors. CloudFront sits in front of the ALB to intercept these errors and serve a friendly HTML page from S3 instead.

### How It Works

1. CloudFront distribution is created in front of the ALB
2. An S3 bucket stores the custom error page HTML
3. CloudFront intercepts 502, 503, and 504 responses from the ALB
4. Users see a branded "Service Temporarily Unavailable" page instead of a raw error

### Enabling CloudFront Error Pages

In your environment's `main.tf`, add to the `module "infrastructure"` block:

```hcl
# CloudFront for custom error pages (shows friendly 503 when services are stopped)
cloudfront_alb_enabled            = true
cloudfront_alb_error_page_content = file("${path.module}/error-503.html")
```

### Custom Error Page

Create an `error-503.html` file in your environment directory. This is a standalone HTML file that you can preview in a browser. The file is version-controlled and independently editable per environment.

If you omit `cloudfront_alb_error_page_content`, a generic default page is used (defined in the `cloudfront-alb` module).

### Required Outputs

Add these outputs to your environment's `main.tf`:

```hcl
# CloudFront ALB outputs
output "cloudfront_alb_distribution_id" {
  value       = module.infrastructure.cloudfront_alb_distribution_id
  description = "CloudFront distribution ID for custom error pages"
}

output "cloudfront_alb_domain_name" {
  value       = module.infrastructure.cloudfront_alb_domain_name
  description = "CloudFront distribution domain name"
}

output "cloudfront_alb_error_bucket" {
  value       = module.infrastructure.cloudfront_alb_error_bucket
  description = "S3 bucket for error pages"
}
```

---

## Troubleshooting

### Cognito Issues

**"Redirect URI mismatch"**
- Verify `domain_name` matches your actual domain
- Access via HTTPS, not HTTP

**User can't log in**
```bash
# Check user status
uv run python bin/cognito.py list myapp-staging
```
If status is `FORCE_CHANGE_PASSWORD`, user needs to complete first login or reset password with `--permanent`.

**Certificate not validating**
- Verify `route53_zone_id` is correct
- DNS propagation can take up to 30 minutes

### Scheduling Issues

**Environment won't stop**
```bash
aws logs tail /aws/lambda/myapp-staging-scheduler --since 1h
```
Common cause: RDS in transitional state.

**Environment won't start**
1. Check if RDS is stopped: `./bin/environment.py status myapp-staging`
2. Try manual start: `./bin/environment.py start myapp-staging`

**Health checks failing after start**
Normal - ECS services fail health checks while RDS is starting (5-10 minutes). The start command waits for RDS before scaling ECS, but health checks may still fail briefly during initialization.

### Known Limitations

- **RDS auto-restart**: AWS automatically restarts stopped RDS after 7 days. The scheduler will stop it again on the next scheduled stop.
- **ElastiCache**: Cannot be stopped without deletion. Stays running.
- **Sessions**: Cognito sessions last 1 hour by default.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Staging Environment                         │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │              CloudFront (intercepts 502/503/504)               │  │
│  │  ┌─────────────────────────┐                                  │  │
│  │  │ S3 Error Page (fallback │                                  │  │
│  │  │ when ALB returns 5xx)   │                                  │  │
│  │  └─────────────────────────┘                                  │  │
│  └────────────────────────────┬───────────────────────────────────┘  │
│                               ▼                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                     ALB (always running)                       │  │
│  │  ┌──────────────────┐    ┌───────────────────────────────┐    │  │
│  │  │ Cognito Auth     │───►│ ECS Service (can be scaled    │    │  │
│  │  │ (login required) │    │ to 0 during off-hours)        │    │  │
│  │  └──────────────────┘    └───────────────────────────────┘    │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                │                                     │
│              ┌─────────────────┼─────────────────┐                   │
│              │                 │                 │                   │
│              ▼                 ▼                 ▼                   │
│  ┌───────────────────┐ ┌─────────────┐ ┌─────────────────────┐      │
│  │ RDS (can be       │ │ ElastiCache │ │ Lambda Scheduler    │      │
│  │ stopped)          │ │ (always on) │ │ (EventBridge rules) │      │
│  └───────────────────┘ └─────────────┘ └─────────────────────┘      │
└──────────────────────────────────────────────────────────────────────┘
```
