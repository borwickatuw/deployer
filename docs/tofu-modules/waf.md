# AWS WAF Integration

The `modules/waf` module provides Web Application Firewall protection for your applications.

## Features

- **IP Reputation**: Blocks known malicious IP addresses (botnets, scanners, etc.)
- **OWASP Top 10**: Protection against common vulnerabilities (XSS, path traversal, etc.)
- **Known Bad Inputs**: Blocks requests with known exploit patterns
- **Rate Limiting**: Prevents abuse by limiting requests per IP
- **Bot Control**: Optional paid tier for advanced bot detection
- **Geographic Blocking**: Block traffic from specific countries
- **IP Allowlist**: Bypass rules for trusted IPs (offices, CI/CD, etc.)

## Pricing

See [AWS WAF Pricing](https://aws.amazon.com/waf/pricing/) for current rates. Costs are based on the number of Web ACLs, rules, and requests processed. Bot Control is an optional paid add-on.

## Usage

### Standalone Environment

Add to your environment's `main.tf`:

```hcl
module "waf" {
  source = "../../modules/waf"

  name_prefix = var.name_prefix
  alb_arn     = module.alb.arn

  # Basic protection (recommended defaults)
  ip_reputation_enabled    = true
  common_rules_enabled     = true
  known_bad_inputs_enabled = true
  rate_limit_enabled       = true
  rate_limit_requests      = 2000

  # Optional: Geographic blocking
  geo_block_countries = ["RU", "CN", "KP"]

  # Optional: Allow specific IPs to bypass rules
  ip_allowlist = ["203.0.113.0/24"]  # Office IP range
}
```

### Shared Infrastructure

Add to `modules/shared-infrastructure/main.tf`:

```hcl
module "waf" {
  source = "../waf"
  count  = var.waf_enabled ? 1 : 0

  name_prefix = var.name_prefix
  alb_arn     = module.alb.arn

  ip_reputation_enabled    = var.waf_ip_reputation_enabled
  common_rules_enabled     = var.waf_common_rules_enabled
  known_bad_inputs_enabled = var.waf_known_bad_inputs_enabled
  rate_limit_enabled       = var.waf_rate_limit_enabled
  rate_limit_requests      = var.waf_rate_limit_requests
  bot_control_level        = var.waf_bot_control_level
  geo_block_countries      = var.waf_geo_block_countries
  ip_allowlist             = var.waf_ip_allowlist
}
```

## Configuration Reference

### Protection Rules

| Variable                   | Default | Description                    |
| -------------------------- | ------- | ------------------------------ |
| `ip_reputation_enabled`    | `true`  | Block known malicious IPs      |
| `common_rules_enabled`     | `true`  | OWASP Top 10 protection        |
| `known_bad_inputs_enabled` | `true`  | Block exploit patterns         |
| `sqli_rules_enabled`       | `false` | Additional SQL injection rules |

### Rate Limiting

| Variable              | Default | Description                 |
| --------------------- | ------- | --------------------------- |
| `rate_limit_enabled`  | `true`  | Enable rate limiting        |
| `rate_limit_requests` | `2000`  | Max requests per IP         |
| `rate_limit_window`   | `300`   | Evaluation window (seconds) |

### Bot Control (Paid)

| Variable                  | Default  | Description                           |
| ------------------------- | -------- | ------------------------------------- |
| `bot_control_level`       | `"none"` | `"none"`, `"common"`, or `"targeted"` |
| `bot_control_scope_paths` | `[]`     | Limit bot control to specific paths   |

**Tip:** Use `bot_control_scope_paths` to reduce costs by only applying bot control to sensitive endpoints:

```hcl
bot_control_scope_paths = ["/login", "/api/", "/admin/"]
```

### Geographic and IP Rules

| Variable              | Default                   | Description                                   |
| --------------------- | ------------------------- | --------------------------------------------- |
| `geo_block_countries` | `[]`                      | Country codes to block (e.g., `["RU", "CN"]`) |
| `ip_allowlist`        | `[]`                      | CIDRs that bypass all rules                   |
| `health_check_paths`  | `["/health", "/health/"]` | Paths that bypass WAF                         |

### Deployment and Logging

| Variable               | Default  | Description                  |
| ---------------------- | -------- | ---------------------------- |
| `rule_action_override` | `"none"` | Set to `"count"` for testing |
| `logging_enabled`      | `true`   | Enable CloudWatch logging    |
| `log_retention_days`   | `30`     | Log retention period         |

## Deployment Strategy

### 1. Deploy in Count Mode First

When first enabling WAF, deploy with `rule_action_override = "count"` to monitor what would be blocked without actually blocking:

```hcl
module "waf" {
  # ...
  rule_action_override = "count"  # Log matches but don't block
}
```

### 2. Monitor CloudWatch Logs

Check the WAF logs in CloudWatch (log group: `aws-waf-logs-{name_prefix}`) for:

- False positives (legitimate traffic being matched)
- Expected blocks (malicious traffic)

### 3. Switch to Block Mode

After 1-2 weeks of monitoring, remove the override:

```hcl
module "waf" {
  # ...
  rule_action_override = "none"  # Enable blocking
}
```

## Viewing WAF Activity

### CloudWatch Metrics

WAF publishes metrics to CloudWatch under the `AWS/WAFV2` namespace:

- `AllowedRequests`
- `BlockedRequests`
- `CountedRequests`

### CloudWatch Logs

Query blocked requests:

```bash
aws logs filter-log-events \
  --log-group-name "aws-waf-logs-myapp-staging" \
  --filter-pattern '{ $.action = "BLOCK" }'
```

### AWS Console

1. Go to AWS WAF & Shield console
1. Select your Web ACL
1. View "Sampled requests" for recent matches
1. Check "CloudWatch metrics" for trends

## Common Issues

### Health Checks Failing

If ECS health checks fail after enabling WAF, ensure health check paths are configured:

```hcl
health_check_paths = ["/health", "/health/", "/api/health"]
```

### Legitimate Traffic Blocked

1. Check CloudWatch logs to identify which rule is triggering
1. Options:
   - Add source IP to `ip_allowlist`
   - Adjust `rate_limit_requests` if rate limiting
   - Disable specific rule set if causing issues

### High WAF Costs

Reduce costs by:

1. Using `bot_control_scope_paths` to limit expensive bot control inspection
1. Setting appropriate `rate_limit_requests` to block abuse early
1. Positioning cheaper rules (rate limit, geo block) before expensive ones

## See Also

- [AWS WAF Documentation](https://docs.aws.amazon.com/waf/latest/developerguide/)
- [AWS Managed Rules](https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-list.html)
- [WAF Pricing](https://aws.amazon.com/waf/pricing/)
