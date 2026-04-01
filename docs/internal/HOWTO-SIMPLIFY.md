# How to Simplify the Deployer Codebase

Technical debt and simplification candidates for deployer. For methodology (thresholds, extraction rules, quality safeguards), see the DOCS best-practice guide in claude-meta (Practice 1).

## Current Opportunities

### Code

| File                       | Lines | Notes                                         |
| -------------------------- | ----- | --------------------------------------------- |
| `bin/deploy.py`            | 827   | Could extract Docker/ECR/ECS logic to library |
| `tests/unit/test_audit.py` | 452   | Large but may be appropriate for coverage     |
| `bin/cognito.py`           | 446   | Could share patterns with environment.py      |

### Terraform Modules

| Module                              | Lines | Notes                                          |
| ----------------------------------- | ----- | ---------------------------------------------- |
| `modules/alb/main.tf`               | 259   | Could split listeners, security, target groups |
| `modules/staging-scheduler/main.tf` | 229   | Lambda + EventBridge could be separated        |
| `modules/ecs-service/main.tf`       | 225   | Could extract IAM, security groups             |

### Documentation

| Document                   | Lines | Status                            |
| -------------------------- | ----- | --------------------------------- |
| `docs/CONFIG-REFERENCE.md` | 576   | Reference doc, may be appropriate |
| `docs/ARCHITECTURE.md`     | 339   | Moderate, acceptable              |
| `CLAUDE.md`                | 59    | Well under threshold              |
