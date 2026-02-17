# Deployment Performance Testing

This document describes the deployment speed testing infrastructure, how to run tests, and findings from baseline measurements.

## Overview

The deployment speed test measures deployment time by instrumenting each step of `deploy.py`. It uses a SMED (Single-Minute Exchange of Dies) approach to identify bottlenecks and measure optimization impact.

### Key Metrics

- **Total Duration**: End-to-end deployment time from `deploy.py` start to "Deployment complete!"
- **Step Timing**: Duration of each deployment phase (ECR login, build, migrations, etc.)

### Deployment Pipeline Steps

| Step                | Function                  | Before All Optimizations | After ALB/ECS Tuning | After Parallel Optimizations  |
| ------------------- | ------------------------- | ------------------------ | -------------------- | ----------------------------- |
| ECR Login           | `ecr_login()`             | ~2s                      | ~2s                  | ~2s                           |
| Build & Push        | `build_and_push_images()` | 20-30s                   | 20-30s               | 20-30s                        |
| Start Migrations    | `start_migrations()`      | -                        | -                    | ~2s (non-blocking)            |
| Deploy Services     | `deploy_services()`       | ~2s                      | ~2s                  | ~2s                           |
| Wait for Migrations | `wait_for_migrations()`   | 60-70s                   | 60-70s               | ~40s (overlapped with deploy) |
| Wait for Stable     | `wait_for_stable()`       | **150-360s**             | **~140s**            | **~80s** (parallel)           |
| **Total**           |                           | **~450s**                | **~235s**            | **~145s**                     |

Note: With migrations skipped (no changes), total time is ~75s.

## Running a Speed Test

### Prerequisites

1. **Environment config.toml**: The environment must have a `config.toml` file with `${tofu:...}` placeholders configured. The speed test script automatically resolves these by running `tofu output` in the environment directory.

1. **Cognito Test Account**: For Cognito-protected environments, the deployer test account must exist with password stored in SSM at `/deployer/<environment>/cognito-test-password`. See [STAGING.md](../operations/STAGING.md#test-account-for-automation) for setup.

### Running the Test

```bash
# Test myapp-staging (requires deploy.toml path as first argument)
uv run python bin/deploy.py speed-test ~/code/myapp/deploy.toml myapp-staging \
    --output local/results/test-XXX.json
```

### Arguments

**Positional (required):**

- `config`: Path to the application's deploy.toml file
- `environment`: Full environment name (e.g., `myapp-staging`, `myapp-production`)

**Optional flags:**

- `--output`: Path for JSON results file
- `--dry-run`: Skip actual deployment, test infrastructure only
- `--skip-auth`: Skip Cognito authentication (for debugging)
- `--poll-interval`: Seconds between health check polls (default: 2.0)

## Results

Results are stored in `local/results/` directory (gitignored):

- **JSON files**: Detailed per-run data with step timing
- **summary.csv**: Aggregated data for charting

### Results Directory

```
local/results/
├── baseline-001.json          # First baseline measurement
├── optimized-001.json         # After optimizations
└── summary.csv                # CSV summary of all runs
```

### JSON Format

```json
{
  "run_id": "speedtest-ecd15330",
  "total_duration_seconds": 234.26,
  "steps": [
    {
      "name": "ecr_login",
      "duration_seconds": 2.13,
      "success": true
    },
    {
      "name": "build_and_push_images",
      "duration_seconds": 15.29,
      "success": true,
      "sub_steps": [
        {"name": "web_build", "duration_seconds": 1.14, "success": true},
        {"name": "web_push", "duration_seconds": 2.03, "success": true}
      ]
    },
    {"name": "run_migrations", "duration_seconds": 73.46, "success": true},
    {"name": "deploy_services", "duration_seconds": 1.82, "success": true},
    {"name": "wait_for_stable", "duration_seconds": 141.06, "success": true}
  ]
}
```

## Pre-Optimization Baseline Findings

These measurements were taken before the ALB and ECS deployment optimizations were applied.

### Best Baseline: baseline-002.json (248s total)

| Step            | Duration | % of Total |
| --------------- | -------- | ---------- |
| ECR Login       | 2.08s    | 0.8%       |
| Build & Push    | 26.31s   | 10.6%      |
| Migrations      | 61.88s   | 25.0%      |
| Deploy Services | 1.74s    | 0.7%       |
| Wait for Stable | 155.74s  | **62.9%**  |

### Latest Full Test: baseline-004.json (448s total)

| Step            | Duration | % of Total |
| --------------- | -------- | ---------- |
| ECR Login       | 2.18s    | 0.5%       |
| Build & Push    | 22.82s   | 5.1%       |
| Migrations      | 63.09s   | 14.1%      |
| Deploy Services | 1.65s    | 0.4%       |
| Wait for Stable | 358.27s  | **79.9%**  |

### Key Observations

1. **`wait_for_stable` was the bottleneck** at 60-80% of total time. Root causes:

   - ALB health check intervals (was 60s, now 10s for staging)
   - Old task draining / deregistration delay (was 300s, now 15s for staging)
   - ECS deployment config waiting for overlap (now 0% minimum healthy for staging)
   - **Status: RESOLVED** - See "Implemented Optimizations" section

1. **Migrations are now the largest remaining target** at ~29% of optimized time (~68s). Could save ~60s by skipping when no changes.

1. **Build/push is well optimized** at 20-30s total, thanks to Docker layer caching and good `.dockerignore`.

1. **Variance is high**: Total time ranged from 248s to 624s in pre-optimization tests. Factors include:

   - Whether RDS/services were already warm
   - Network conditions for image push/pull
   - ECS scheduling delays

## Implemented Optimizations

The following optimizations have been implemented to reduce deployment time for staging environments:

### 1. ALB Health Check Settings (Experiment 1)

**Location:** `modules/alb/main.tf`, passed through `main.tf` → `environments/*/main.tf`

| Setting                 | Default (Prod) | Staging Optimized | Impact                   |
| ----------------------- | -------------- | ----------------- | ------------------------ |
| `health_check_interval` | 30s            | 10s               | Faster health checks     |
| `health_check_timeout`  | 10s            | 5s                | Quicker timeout          |
| `healthy_threshold`     | 2              | 2                 | Min 20s to healthy       |
| `unhealthy_threshold`   | 5              | 3                 | Faster failure detection |
| `deregistration_delay`  | 120s           | 15s               | Faster task draining     |

**Expected savings:** ~100s+ (health check) + faster draining

### 2. ECS Deployment Configuration (Experiment 2)

**Location:** `config.toml` → `[deployment]` section, used by `deploy.py`

| Setting                    | Default (Prod) | Staging Optimized | Impact                              |
| -------------------------- | -------------- | ----------------- | ----------------------------------- |
| `minimum_healthy_percent`  | 100%           | 0%                | No wait for new before stopping old |
| `maximum_percent`          | 200%           | 100%              | No extra capacity                   |
| `circuit_breaker_enabled`  | false          | true              | Faster failure detection            |
| `circuit_breaker_rollback` | true           | true              | Auto-rollback on failure            |

**Expected savings:** ~30-60s (no overlap period)

### Configuration Example

**Staging ALB settings** (in `environments/myapp-staging/main.tf`):

```hcl
module "infrastructure" {
  # ... other config ...
  health_check_interval  = 10
  health_check_timeout   = 5
  healthy_threshold      = 2
  unhealthy_threshold    = 3
  deregistration_delay   = 15
}
```

**Staging deployment settings** (in `environments/myapp-staging/config.toml`):

```toml
[deployment]
minimum_healthy_percent = 0
maximum_percent = 100
circuit_breaker_enabled = true
circuit_breaker_rollback = true
```

### Measured Results (2026-01-23)

| Metric              | Before (baseline-004) | After (experiment-combined) | Improvement    |
| ------------------- | --------------------- | --------------------------- | -------------- |
| **wait_for_stable** | 358.27s               | 140.23s                     | **218s (61%)** |
| **Total Duration**  | 448.02s               | 235.34s                     | **213s (47%)** |

The combined optimizations achieved a **47% reduction** in total deployment time.

______________________________________________________________________

## Implemented: Skip Migrations When Unchanged

**Status: IMPLEMENTED (2026-01-30)**

When no migration files have changed since the last successful deployment, the migration step is skipped entirely. This saves ~60-70s on most deployments.

### How It Works

1. Before running migrations, compute a hash of all `*/migrations/*.py` files using `git ls-files` and `git hash-object`
1. Compare with the stored hash in SSM (`/<app>/<env>/last-migrations-hash`)
1. If hashes match, skip migrations
1. After successful migration, store the new hash

### Output Examples

**Migrations skipped:**

```
Migrations unchanged (hash: 464f3623962b7cdb), skipping ✓
```

**Migrations run (first deploy or files changed):**

```
No stored migrations hash found, will run migrations
Running migrations...
  Migrations complete ✓
```

______________________________________________________________________

## Implemented: Parallel Service Waiting

**Status: IMPLEMENTED (2026-02-02)**

When waiting for services to stabilize, all services are now waited on in parallel using a thread pool instead of sequentially.

### How It Works

1. After deploying services, `wait_for_stable()` creates a thread pool with one thread per service
1. Each thread independently polls ECS for service status and ALB target health
1. Results are collected as threads complete using `as_completed()`
1. First fatal error fails the deployment immediately

### Expected Savings

For deployments with multiple services (e.g., web + celery):

- **Before**: web stable (60s) → web ALB (20s) → celery stable (60s) = ~140s sequential
- **After**: max(web, celery) = ~80s parallel

______________________________________________________________________

## Implemented: Parallel Migrations

**Status: IMPLEMENTED (2026-02-02)**

Migrations now start before ECS services are deployed, allowing them to run in parallel with ECS pulling images and starting containers.

### How It Works

1. `start_migrations()` launches the migration task and returns immediately
1. `deploy_services()` triggers ECS deployment (image pulls start)
1. `wait_for_migrations()` waits for migrations to complete
1. `wait_for_stable()` waits for services (which may already be partially started)

### New Deployment Flow

```
start_migrations (2s) ─┬─ migrations running (~70s) ──────────────┬─ done
                       │                                          │
deploy_services (2s) ──┴─ ECS pulling/starting (~30s) → stabilize ┴─ parallel wait (~80s)
```

### Expected Savings

- Migrations (~70s) now overlap with ECS startup (~30s)
- Total time reduced by overlap: ~30s savings
- Combined with parallel wait_for_stable: ~60s total savings

______________________________________________________________________

## Future Optimization Targets

Additional optimizations not yet implemented:

### Medium Impact

1. **Smaller container images** (~10-20s savings)
   - Reduce image pull time
   - Multi-stage builds, smaller base images

### Low Impact (already optimized)

2. **ECR login** (~2s, minimal)
1. **Deploy API calls** (~2s, minimal)

## Troubleshooting

### Service not stabilizing

Check ECS service events for errors:

```bash
aws ecs describe-services \
    --cluster myapp-staging-cluster \
    --services web \
    --query 'services[0].events[:5]'
```

Common issues:

- Missing SSM parameters
- Health check failing
- Insufficient resources

### Authentication errors

Verify Cognito test account:

```bash
aws ssm get-parameter \
    --name /deployer/myapp-staging/cognito-test-password \
    --with-decryption --query 'Parameter.Value' --output text
```
