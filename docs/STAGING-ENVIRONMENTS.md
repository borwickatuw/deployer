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
./bin/tofu.sh -chdir=environments/myapp-staging apply
```

### Managing Users

```bash
# List users
uv run python bin/manage-cognito-access.py list myapp-staging

# Create user (copies welcome message to clipboard)
uv run python bin/manage-cognito-access.py create myapp-staging \
  --username alice@example.com --clipboard

# Create with specific password
uv run python bin/manage-cognito-access.py create myapp-staging \
  --username alice@example.com -p "SecurePass123!"

# Disable/enable user
uv run python bin/manage-cognito-access.py disable myapp-staging --username alice@example.com
uv run python bin/manage-cognito-access.py enable myapp-staging --username alice@example.com

# Reset password
uv run python bin/manage-cognito-access.py reset-password myapp-staging --username alice@example.com

# Delete user
uv run python bin/manage-cognito-access.py delete myapp-staging --username alice@example.com
```

**Password requirements:** Minimum 12 characters, at least one uppercase, lowercase, and number.

### Test Account for Automation

For automated health checks of Cognito-protected environments, create a dedicated test account:

```bash
# Generate password
PASSWORD=$(python3 -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20)))")

# Create user
uv run python bin/manage-cognito-access.py create myapp-staging \
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
./bin/manage-environment.py status myapp-staging

# Stop (scales ECS to 0, stops RDS)
./bin/manage-environment.py stop myapp-staging

# Start (starts RDS, waits for it, scales ECS back up)
./bin/manage-environment.py start myapp-staging --wait
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
./bin/tofu.sh -chdir=environments/myapp-staging apply
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

## Troubleshooting

### Cognito Issues

**"Redirect URI mismatch"**
- Verify `domain_name` matches your actual domain
- Access via HTTPS, not HTTP

**User can't log in**
```bash
# Check user status
uv run python bin/manage-cognito-access.py list myapp-staging
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
1. Check if RDS is stopped: `./bin/manage-environment.py status myapp-staging`
2. Try manual start: `./bin/manage-environment.py start myapp-staging --wait`

**Health checks failing after start**
Normal - ECS services fail health checks while RDS is starting (5-10 minutes). Use `--wait` flag to start RDS before scaling ECS.

### Known Limitations

- **RDS auto-restart**: AWS automatically restarts stopped RDS after 7 days. The scheduler will stop it again on the next scheduled stop.
- **ElastiCache**: Cannot be stopped without deletion. Stays running.
- **Sessions**: Cognito sessions last 1 hour by default.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Staging Environment                      │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    ALB (always running)                     │ │
│  │  ┌──────────────────┐    ┌───────────────────────────────┐ │ │
│  │  │ Cognito Auth     │───►│ ECS Service (can be scaled    │ │ │
│  │  │ (login required) │    │ to 0 during off-hours)        │ │ │
│  │  └──────────────────┘    └───────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                │                                 │
│              ┌─────────────────┼─────────────────┐              │
│              │                 │                 │              │
│              ▼                 ▼                 ▼              │
│  ┌───────────────────┐ ┌─────────────┐ ┌─────────────────────┐ │
│  │ RDS (can be       │ │ ElastiCache │ │ Lambda Scheduler    │ │
│  │ stopped)          │ │ (always on) │ │ (EventBridge rules) │ │
│  └───────────────────┘ └─────────────┘ └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```
