+++
title = "Two production settings PRODUCTION.md promised are not settable from an environment"
+++

Captured 2026-09-22 from the RDS pass-through fix (deployer item 5,
sub-phase 5-1), which found two more entries in PRODUCTION.md's
production-override block that could never have worked and removed them.

**Current state.** `elasticache_multi_az`: `modules/elasticache` has no such
input at all, yet the pre-deploy checklist still says "Redis: ElastiCache
configured with Multi-AZ", which cannot be met. `lifecycle_policy_count`:
the root variable is `ecr_lifecycle_policy_count` and
`environments/deployer.tf` does not pass it through, so an environment
cannot set it. PRODUCTION.md now says both are not settable today.

**Proposed enhancement.** (1) Add a `multi_az_enabled` / automatic-failover
input to `modules/elasticache` (replication group with two nodes when set),
thread it through the root and deployer.tf, and put the production value in
`services.auto.tfvars.example`; or strike the checklist line with a
DECISIONS.md note if single-node cache is the accepted production posture.
(2) Pass `ecr_lifecycle_policy_count` through deployer.tf like the rds_*
variables.

**Complexity**: Low for (2); Medium for (1) — a replication-group change is
an apply with a cache replacement.
