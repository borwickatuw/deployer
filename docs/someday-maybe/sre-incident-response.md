+++
title = "SRE / Incident Response Improvements"
+++

Enhancements for Site Reliability Engineering practices and incident management.

### SLOs in config.toml

**Current state**: SLO targets are documented in prose (OPERATIONS.md, claude-meta best-practices/MONITORING.md).

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
1. Update cloudwatch-alarms module to use SLO values for thresholds
1. Add `ops.py slo` command to report current compliance
1. Consider error budget dashboard in CloudWatch

**Complexity**: Medium - requires config schema update, alarm module changes, new ops.py command.

### ~~Incident Response Tooling~~

Moved to [PLAN-ARCHIVE.md](../PLAN-ARCHIVE.md).

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

| Enhancement                         | Value  | Effort | Priority                                       |
| ----------------------------------- | ------ | ------ | ---------------------------------------------- |
| SLOs in config.toml                 | High   | Medium | 1                                              |
| ~~Incident start/resolve commands~~ |        |        | Moved to [PLAN-ARCHIVE.md](../PLAN-ARCHIVE.md) |
| Error budget dashboard              | Medium | Medium | 2                                              |
| Postmortem automation               | Low    | High   | 3                                              |
