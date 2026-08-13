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

Phase 54 (emergency-subsystem test coverage) **shipped 2026-08-12** in
`5f6b287` — checkpoint/ecs/rds 0% → 100%, coverage floor 25 → 32; record
in claude-meta docs/PLAN-ARCHIVE.md. Its tests pin today's
swallow-ClientError contracts on purpose, for 53i to decide.
