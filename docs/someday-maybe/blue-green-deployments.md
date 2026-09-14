+++
title = "Infrastructure Enhancements: Blue-Green Deployments"
+++

**Current state**: Rolling deployments via ECS.

**Enhancement**: CodeDeploy integration for blue-green:

- Instant rollback capability
- Traffic shifting (canary, linear, all-at-once)
- Pre/post deployment hooks

**Complexity**: High - significant architecture change.
