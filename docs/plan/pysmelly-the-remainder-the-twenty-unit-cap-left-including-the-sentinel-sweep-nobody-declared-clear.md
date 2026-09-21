+++
title = "pysmelly: the remainder the twenty-unit cap left, including the sentinel sweep nobody declared clear"
review = "2026-09-18"
number = 6
+++

The 2026-09-18 pysmelly arc ran twenty units and landed eighteen. It left a
**named** remainder, which is the only part of the arc that is work: the
nine adjudicated families A01-A09 are not here, because the operator ratified
eight of them as leave-standings on 2026-09-21 (answer `b3`, `PysDeployer`,
"leave all standing — ratify eight declines") and A08 was closed on the repo
side by `ad50a02`. Those rows live in [docs/PYSMELLY.md](../PYSMELLY.md)
under "Findings left standing — ratified 2026-09-21", each with an anchor
re-measured at `f33c84c`, a reason and an event-shaped `re-evaluate-by`. **A
scout reading this item should not re-derive them.**

What is left, from claude-meta
`docs/investigations/2026-09-18-comprehensive-review-run-ledger.md`,
`## Unit records` → `### deployer` → `#### Pending operator`, the
`[carry-forward, PYSMELLY-REVIEW]` bullet on the 20-unit cap:

> "The 20-unit cap left a named remainder, including one class the scout
> explicitly refused to declare clear."

Counts below are re-measured at `f33c84c` (total 44 findings, 77 Python
files parsed), not inherited from the run's `f5d22e0` baseline — this
register's own convention is derive the count, never read one.

Answer `a6` (`PysGate`) keeps pysmelly non-gating and on-demand fleet-wide,
so none of this is a gate change. Per-repo item; no fleet family.

### Sub-phases
- **6-1 — The sentinel-returned-from-except sweep — the lead's own ranking: "Mint the sentinel sweep as its own unit-generating pass. It is the highest-severity item in this repo's design space and is invisible to the finding count by construction, because the except that hides the failure from the operator hides it from pysmelly too." PYSMELLY-REVIEW uses this repo's emergency/ family as its worked example of the contract bug — one value meaning both 'there is nothing there' and 'I could not look'. The guide's grep returns 34 hits across src/, bin/ and modules/, densest in emergency/ecs.py (:328, :449), emergency/rds.py (:119) and deploy/service.py (:199, :498, :590, :853, :1526, :1529). A hit proves only the shape; clearing one means reading the except body and every caller, which was not done for 34 sites in a scout pass — so this is NOT reported clear. Sequence: derive against docs/internal/DECISIONS.md '2026-08-18: Error Contracts' (read-only commands catch at the render boundary, acting commands abort at one boundary), which is the same standing decision 36ce6e8 and 1ecdfdc executed for the aws/ helpers. Note the guide's named instance, wait_for_deployment, does not exist in this repo at all — guide feedback for claude-meta, not work here. (Ledger: deployer → Pending operator, [carry-forward, PYSMELLY-REVIEW], the 20-unit-cap bullet.)**
- **6-2 — pass-through-params, the remainder U20 did not cover — 14 findings at f33c84c (the ledger named eleven at the run's baseline; re-measure before acting): aws/cloudwatch.py:68 x2, aws/cognito.py:46, aws/ssm.py:131, core/ssm_secrets.py:36/:49/:62/:267, deploy/preflight.py:46, deploy/service.py:596 x2, utils/cli.py:288 x2 and utils/cli.py:305. The two utils/cli.py anchors are the error-boundary adapters the register calls out by name, and U20's own central prediction — that re-pointing a forwarding call would clear such a finding — was falsified by its validator and the change kept anyway for maintainability, so do not assume a forwarding fix moves the count. Not ratified: this class carries no operator verdict, which is why it is work rather than a register row. (Ledger: deployer → Pending operator, [carry-forward, PYSMELLY-REVIEW]; and the Plan corrections entry "A unit's own central prediction was falsified by its validator".)**
- **6-3 — Two loose ends inside the ratified skips, neither of them a re-litigation. (a) The 50-literal cluster: A07 (scattered-constants skip stays) was ratified, but its named exception was not — the lead recorded the 50 appearing in bin/init.py:575, bin/ops.py:869 and src/deployer/deploy/service.py:976 as 'three unrelated limits ... unexamined by anyone'. Examine them once and either name them or record that they are genuinely unrelated; docs/PYSMELLY.md's A07 row already carries that as its re-evaluate-by trigger. While there, correct U10's rewritten skip rationale in pyproject.toml, which its own validator found still overstates: the surviving set is 8 findings at f33c84c and includes a numeric cluster the rewrite describes as string literals. (b) The new single-call-site finding src/deployer/init/deploy_toml.py:93 is_likely_secret (1 param, 1 call site at :225) is live at f33c84c, was not one of the twenty units, and carries no verdict — adjudicate it or fix it, but do not let it inherit the ratified stop_environment verdict next to it. (Ledger: deployer → Pending operator, [adjudication, PYSMELLY-REVIEW] A07 and the 20-unit-cap carry-forward; register: docs/PYSMELLY.md, 'Skipped and reverted findings' and the A07 row.)**
