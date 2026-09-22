# deployer Plan (Archive)

The register of closed phases, maintained by fileplan (`plan.toml`
`[states.plan]`): every closed phase gets a `## N. Title` heading here,
minted by `archive` at its ordered place. Each entry carries a short
summary of the outcome and a pointer to the full record. The register
stays bounded by rotation (see
[PLAN-METHOD.md](../PLAN-METHOD.md#rotating-the-register)): the oldest
entries are cut into dated segment files and `first-number` is raised.

The floor is 1: before the 2026-09-18 review every phase this repo
worked was queued and numbered by claude-meta, and those records — Phase
53, Phase 54, and all the pre-numbering repo-local work — live in the
rotated segment [PLAN-ARCHIVE-2026-09.md](PLAN-ARCHIVE-2026-09.md). Their
headings are claude-meta numbers or unnumbered, so they were rotated out
whole rather than reheaded into this register; nothing here re-mints them.

Live items are one file each in [plan/](../plan/); what each state and
transition means is in [PLAN-METHOD.md](../PLAN-METHOD.md).

## 1. Operations and governance: an owner, the console-only checks, and the password flag

Closed 2026-09-22. Filed by the 2026-09-18 comprehensive review. An
Ownership block (owner and escalation contact) now heads
`docs/operations/README.md`; GOVERNANCE.md names the real `ecs-run.py exec`
subcommand; `bin/cognito.py create|reset-password` take `--password-stdin`
in place of `-p/--password` (17 characterization tests, residual aws-CLI
argv exposure recorded in GOVERNANCE R4 and filed as an idea); the four
AWS-side runnable checks were assigned to the app repos' own checkpoints.
Commits `695c046`, `13e498b`, `68ab618`, `8a1d4bc`. Full record in
`items/`.

## 5. Before the next apply: production RDS protections and the staging scheduler's new failure signal

Closed 2026-09-22. Filed by the 2026-09-18 comprehensive review.
`environments/deployer.tf` now declares and passes the four production RDS
protections (previously silently ignored from an environment's tfvars), the
production tfvars example carries the production values, and the docs name
the real variables (`fcb3c38`). The staging scheduler's 500-on-failure
behaviour and its alarm consequence are documented for the next apply
(`1be49e3`). Two follow-ups captured as ideas: the same gap in the shared-app
module, and two PRODUCTION.md settings that are not settable. Full record in
`items/`.
