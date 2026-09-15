# deployer Plan (Archive)

The register of closed phases, maintained by fileplan (`plan.toml`
`[states.plan]`): every closed phase gets a `## N. Title` heading here,
minted by `archive` at its ordered place. Each entry carries a short
summary of the outcome and a pointer to the full record. The register
stays bounded by rotation (see
[PLAN-METHOD.md](../PLAN-METHOD.md#rotating-the-register)): the oldest
entries are cut into dated segment files and `first-number` is raised.

The register is empty and the floor is 1, because deployer has never had
a repo-local phase. Every phase this repo has worked was queued and
numbered by claude-meta, and those records — Phase 53, Phase 54, and all
the pre-numbering repo-local work — live in the rotated segment
[PLAN-ARCHIVE-2026-09.md](PLAN-ARCHIVE-2026-09.md). Their headings are
claude-meta numbers or unnumbered, so they were rotated out whole rather
than reheaded into this register; nothing here re-mints them. The first
heading minted below will be `## 1.`.

Live items are one file each in [plan/](../plan/); what each state and
transition means is in [PLAN-METHOD.md](../PLAN-METHOD.md).
