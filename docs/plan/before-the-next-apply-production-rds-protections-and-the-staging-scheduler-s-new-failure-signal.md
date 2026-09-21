+++
title = "Before the next apply: production RDS protections and the staging scheduler's new failure signal"
review = "2026-09-18"
number = 5
+++

Two infrastructure changes that reach real AWS and therefore wait for a
deliberate apply, filed so neither is discovered at the moment it bites.

The RDS half was raised by the deployer-environments lane against this repo
(evidence in claude-meta
`docs/investigations/2026-09-18-comprehensive-review-run-ledger.md`,
`## Unit records` → `### deployer-environments` → `#### Pending operator`,
the DATA-MANAGEMENT external-change bullet) and decided on 2026-09-21 in
answer `d4` (`RDSLock`, **"do both, ack the third"**), which assigns it here:
"deployer item: four RDS protections through `environments/deployer.tf`
(production-rollout trigger)". The other two members of `d4` belong to
storage-scripts (Object Lock from the console) and s3-slurm (already fixed in
`afbebe1`) and are not filed here.

The staging-scheduler half is a held-back behaviour-change from deployer's
own lane (same ledger, `### deployer` → `#### Pending operator`, the
`[behaviour-change, PYTHON]` staging-scheduler bullet). The code change has
landed and is tested; what is pending is the operator knowing two things
before the next `tofu apply`.

**Trigger: the first production environment, or the next `tofu apply` of the
staging-scheduler module — whichever comes first.** Per-repo item; no fleet
family.

### Sub-phases
- **5-1 — DATA-MANAGEMENT Practice #1 (external-change, answer d4) — "environments/deployer.tf must declare and pass the four production RDS protections before any production environment is created in deployer-environments; today setting them in services.auto.tfvars is silently ignored." Evidence: grep -n 'rds_' havoc-staging/deployer.tf returns only rds_instance_id; the root module declares all four (variables.tf:73/79/85/91, symlinked from deployer) and wires them to modules/rds at main.tf:163-166, but the intermediate environments/deployer.tf that every environment consumes passes none, so the root defaults (7 / true / false / false) always win — an environment that follows Practice #1 and sets rds_backup_retention_period = 35 gets an 'undeclared variable' warning and a database with staging protections under a production name. Latent today because no production environment exists, and precisely what bites when the first one does. Fix: in environments/deployer.tf declare rds_backup_retention_period, rds_skip_final_snapshot, rds_deletion_protection and rds_multi_az with the current staging defaults and pass them through to module 'infrastructure'. Then reconcile docs/operations/PRODUCTION.md:74, which documents the override using the module-level name backup_retention_period — that would not work from an environment tfvars either. Note the verifier refuted the original bullet's cited path (modules/rds/variables.tf does not exist; the module declares its variables in main.tf) and the fixup re-derived the finding; the escalation itself stands unchanged. (Ledger: deployer-environments → Pending operator, [external-change, DATA-MANAGEMENT → deployer].)**
- **5-2 — PYTHON §18/§19 (held-back behaviour-change, operator's apply decision) — "The staging-scheduler Lambda's production behaviour changed: it now returns 500 instead of 200 when any step fails, and an unreadable RDS status is an error rather than a silent skip." The code has landed with 29 new tests; the lead flagged rather than asked, and named two things to know before the next tofu apply. First: invocations that previously recorded a silent success will now show as Lambda errors — that is the point, but if a CloudWatch alarm watches the Errors metric it may fire on a pre-existing failure that was being hidden, which will look like a new break and is not one. Second, per PYTHON.md §18: fixing _get_rds_status exposes paths that could not previously run; the lead traced them as the ordinary error arms, all now covered, but the handler had 0% coverage before this round so nothing here had ever executed outside AWS. modules/staging-scheduler/main.tf:157-166 invokes it on an EventBridge schedule; archive_file rezips handler.py automatically and the tracked handler.zip is gitignored, so the deploy is just an apply. Related and already decided: answer b3 ratified A01 (env-fallbacks, LOG_LEVEL defaults in the four Lambda bundles) as a leave-standing, so do NOT thread LOG_LEVEL through the aws_lambda_function environment block as part of this — see docs/PYSMELLY.md. (Ledger: deployer → Pending operator, [behaviour-change, PYTHON] staging-scheduler, fix ref 07790ea; and [behaviour-change, LOGGING] ede4340.)**
