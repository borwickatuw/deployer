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
**Phase 53d-2 is the open half**: `emergency.py` `cmd_rollback` (155L) +
`cmd_scale` arrow-code, `ops.py` `cmd_status` arrow-code, `init.py`
`cmd_bootstrap` (131L) + `cmd_environment` (104L), the
`(environment, service, yes)` param-clump, and the re-measure of 53b's
eight `bin/init.py` print-run leave-standings that this entry's 53b
paragraph defers to "after 53d".

Phase 54 (emergency-subsystem test coverage) **shipped 2026-08-12** in
`5f6b287` — checkpoint/ecs/rds 0% → 100%, coverage floor 25 → 32; record
in claude-meta docs/PLAN-ARCHIVE.md. Its tests pin today's
swallow-ClientError contracts on purpose, for 53i to decide.
