# deployer Plan

Active work items. Completed work will move to a PLAN-ARCHIVE.md when
the first phase ships.

_No repo-local phases yet._ This file exists so cross-repo backlog has a
visible home in-repo (fleet convention).

Cross-repo backlog queued against this repo: claude-meta docs/PLAN.md
Phase 52 (container-level health checks for non-HTTP services) and Phase
53 (pysmelly subphase backlog 53a–53i).

Phase 53a (db-\* Lambda twin consolidation) **shipped 2026-08-12** in
`bcd2218` — the two `index.py` twins now share
`modules/lambda-shared/db_common.py`, vendored into each bundle at apply
time; pysmelly 97 → 91. Adjudication record:
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md). Two findings are left
standing there **pending operator confirmation**.

Phase 53b (CLI boilerplate dedup) **shipped 2026-08-13** — the shared
`bin/` helpers now live in `src/deployer/utils/cli.py` and the
`deploy.py` / `ci-deploy` clone in `src/deployer/deploy/pipeline.py`;
pysmelly 91 → 82, `duplicate-except-blocks` empty for the first time,
coverage floor 32 → 36. Adjudication record:
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md). Eight `duplicate-blocks`
findings (the `bin/init.py` print-runs) are left standing there **pending
operator confirmation**, with the drafted fix and the reason it was kept
out; re-measure them after 53d.

Phase 53c (`src/deployer` dedup) **shipped 2026-08-13** — an
error-with-advice vocabulary in `src/deployer/utils/logging.py`
(`advice_block` / `print_with_advice`) adopted at six sites, a shared
`aws` CLI surface in `src/deployer/aws/cli.py` that collapsed five twins
pysmelly could not see, and `EnvironmentTarget` / `DeployOptions` in
`src/deployer/deploy/context.py` for the two findings 53b routed here;
pysmelly 82 → 74, `duplicate-blocks` left with no open items, coverage
floor 36 → 37. The `aws/` package got its first tests ever (cognito 12% →
100%, rds 18% → 100%). Adjudication record:
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md). Two findings were
minted and are left standing there **pending operator confirmation**; it
also lists five inline suppressions carrying neither a rationale nor a
`re-evaluate-by:` tag, routed to 53i.

Phase 53d-1 (the `bin/` deploy.toml-resolution family) **shipped
2026-08-13** — `resolve_deploy_toml_or_exit` in
`src/deployer/utils/cli.py` replaced three hand-rolled copies of the same
resolution in `deploy.py` / `ssm-secrets.py` / `ecs-run.py`, which was
most of three of the four decompositions; `capacity-report.py` split into
`_deployment_cutoff` plus two scan helpers. All four `long-function`
targets cleared and their four `# noqa: C901` lines removed; pysmelly
74 → 71, coverage floor 37 → 44. `ssm-secrets.py`, `ecs-run.py` and
`capacity-report.py` got their first tests ever (0% → 73/61/87%).
Adjudication record: [docs/internal/PYSMELLY.md](internal/PYSMELLY.md).
One `pass-through-params` was minted and is left standing there **pending
operator confirmation**; `capacity-report.check_environment`'s
exit-code conflation is recorded and routed to 53i.

53d was split because re-measuring at HEAD showed claude-meta's plan
entry undercounted it — 7 `bin/` `long-function` findings, not 6.

Phase 53d-2a (`emergency.py` + `ops.py`) **shipped 2026-08-13** — the
first commit pinned `cmd_rollback` / `cmd_scale` / `cmd_force_deploy` /
`cmd_status` at their current behaviour, and every later commit had to
leave those tests passing unchanged. Six `if x and "T" in x:` guards
around `format_timestamp` turned out to be provable no-ops, and deleting
them cleared `cmd_status`'s arrow-code on its own; `select_index` in
`src/deployer/utils/cli.py` absorbed `cmd_rollback`'s two numbered-pick
twins; `_require_service` and `_checkpoint_and_log` unified twins
pysmelly could not see. Three of four targets cleared, nothing minted;
pysmelly 71 → 68, coverage floor 44 → 49. `bin/emergency.py` got its
first tests ever (0% → 57%) and dropped off the convergence-hotspot list.
Adjudication record: [docs/internal/PYSMELLY.md](internal/PYSMELLY.md).
The `(environment, service, yes)` param-clump is left standing there
**pending operator confirmation**, with the drafted `EmergencyTarget`
dataclass and the reason it reads worse than the three parameters.

Phase 53d-2b (`bin/init.py` + the print-run re-measure) **shipped
2026-08-13** — closing 53d. The first commit pinned `cmd_bootstrap` /
`cmd_bootstrap_migrate` / `cmd_deploy_toml` / `cmd_environment` /
`_print_next_steps` at their current behaviour, and every later commit
left those tests passing unchanged. Reading the cross-file legs settled
the design call 53b could not: the eight print-runs were two families
sharing an AST shape, and four of the five next-steps runs live in
`bin/init.py` — so `_numbered_steps` is module-local, and the
`extensions.py` legs stay with 53e's `print_with_advice` adoption. The
helper owning the counter let `_print_next_steps` drop the dynamic
`step` variable 53b cited as the evidence against generalizing. Both
`long-function` targets cleared and both `# noqa: C901` lines came off;
six of the eight print-runs cleared (`duplicate-blocks` 9 → 3). pysmelly
68 → 60, the arc's largest single-subphase drop; coverage floor 49 → 53.
`bin/init.py` got its first tests ever (0% → 92%) and dropped off the
convergence-hotspot list, leaving only `deploy/service.py`; the repo has
no 0%-coverage file left. Adjudication record:
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md). It also fixed a
latent bug: an unset `DEPLOYER_ENVIRONMENTS_DIR` was reported as a
missing bootstrap directory, sending the operator to a command that
fails the same way. The two surviving print-runs are recorded as
belonging to the `setup_profiles.py` neighbourhood (**53e**), not
re-deferred as generic leave-standings.

**53d is closed.** Phase 53e (`extensions.py` / `setup_profiles.py`) is
the next open subphase.

Phase 54 (emergency-subsystem test coverage) **shipped 2026-08-12** in
`5f6b287` — checkpoint/ecs/rds 0% → 100%, coverage floor 25 → 32; record
in claude-meta docs/PLAN-ARCHIVE.md. Its tests pin today's
swallow-ClientError contracts on purpose, for 53i to decide.
