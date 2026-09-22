+++
title = "Shared-app production RDS gets staging protections"
+++

Captured 2026-09-22 from the RDS pass-through fix (deployer item 5,
sub-phase 5-1), which closed the gap for standalone environments only.

**Current state.** `modules/app-in-shared-env/main.tf` instantiates
`module "rds"` in its separate-instance mode and passes none of
`rds_backup_retention_period`, `rds_skip_final_snapshot`,
`rds_deletion_protection`, `rds_multi_az`. The shared-app templates cannot
set them, so a shared-app production app always gets the staging defaults
(7 / true / false / false). Latent until the first shared-app production
environment exists — the same shape 5-1 fixed for standalone.

**Proposed enhancement.** Declare the four in `modules/app-in-shared-env`
with the same defaults, pass them to its `module "rds"`, thread them
through the shared-app templates' `services.auto.tfvars.example` for
production with 35 / true / false / true, and extend the CONFIG-REFERENCE
table's scope line.

**Complexity**: Low. Same edit as 5-1 in a second module. Promote before the
first shared-app production rollout.
