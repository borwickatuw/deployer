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

**53d is closed.**

Phase 53e was **split into five slices before it was run**, on the lesson
53d paid for by splitting twice mid-arc. Re-measured at `805d516`,
claude-meta's one-line 53e entry ("`service.py` ×4, `deployer.py` ×2,
`images.py`, `extensions.py`, `audit.py`") was six files and ~16
findings — three or four sessions. The slices are ordered by existing
coverage descending, so the characterization-test idiom is established on
small well-covered files before it reaches the untested heart:

| Slice | Scope                                                      | Coverage at split | Status |
| ----- | ---------------------------------------------------------- | ----------------- | ------ |
| 53e-1 | `extensions.py` + `setup_profiles.py`                      | 94% / 39%         | done   |
| 53e-2 | `core/audit.py` — `run_audit`                              | 66%               | done   |
| 53e-3 | `deployer.py` — `__init__`, `deploy`, 3 × `law-of-demeter` | 35%               | next   |
| 53e-4 | `images.py` — `build_and_push_images`, `temp-accumulators` | 16%               | open   |
| 53e-5 | `service.py` — 4 × `long-function` + `arrow-code`          | 11%               | open   |

`service.py` is 1003 lines at 11% coverage with four targets, and is the
only file left on the convergence-hotspot list — a session of
characterization tests before a line moves, so 53e-5 is deliberately last.
`service.py:196`'s `param-clumps` stays with 53g.

Phase 53e-1 (`extensions.py` + `setup_profiles.py`) **shipped
2026-08-13** — closing a thread open since 53b, which found five
`log_error → print advice → raise RuntimeError` blocks in
`create_database_extensions` and called them the file's real duplication;
53c landed the vocabulary and 53d-2b routed the two surviving
`duplicate-blocks` here, but nobody had done the adoption. The first
commit pinned all five advice blocks and `cmd_setup_profiles` end to end,
and every later commit left those tests passing unchanged. All three
targets cleared: pysmelly 60 → 57 (`long-function` 9 → 8,
`duplicate-blocks` 3 → 1), and `extensions.py` now has no findings of any
category. Coverage: `extensions.py` 94% → 100%, `setup_profiles.py`
39% → 100%; total 53.51% → 53.91%, floor stays 53. Nothing minted.
Adjudication record: [docs/internal/PYSMELLY.md](internal/PYSMELLY.md),
which also records the measurement that contradicted the plan — adopting
`print_with_advice` **grew** the function 114L → 118L rather than
shrinking it to ~102L, because black costs more in call framing than the
removed scaffolding saves. The decomposition into three helpers is what
took it to 21L. Pinned but not endorsed, routed to **53i**:
`extensions.py`'s bare `except Exception` reports any non-`ClientError`
failure as a credentials or network problem.

Phase 53e-2 (`core/audit.py`) **shipped 2026-08-13** — `run_audit`
cleared, pysmelly 57 → 56, `long-function` 8 → 7, and its
`# noqa: C901` came off (7 remain repo-wide). Nothing minted.

The length turned out to be **triplication pysmelly could not see**: 33
of the 114 lines were the same block written three times (heading, run a
check, warn each issue or print an all-clear, accumulate), but the copies
interleave with their own audit calls, so they are not the runs of
consecutive statements `duplicate-blocks` keys on. Third time in this
arc that a `long-function` finding was really a duplication finding out
of the checker's reach — after 53d-1's three-way deploy.toml-resolution
twin and 53d-2a's six no-op `format_timestamp` guards. The three blocks
became a three-entry table and a four-line loop; `run_audit` went
114L → 33L. Two things the length was hiding also went: `total_issues`,
which tracked exactly what `len(all_issues)` already knew, and three
single-use aliases for `deploy.services` / `.images` /
`.get_all_env_var_names()`.

**Latent bug fixed**, same family as 53d-2b's dead
`except RuntimeError: pass`: the Audit Configuration section is gated on
any of the four `[audit]` keys being set — including `ignore_images` —
but only three had a line inside it, so a deploy.toml configuring only
`ignore_images` printed an empty heading and nothing under it. Display
only; `audit_images` already honoured the setting. Fixed in its own
commit so the decomposition stayed behaviour-preserving.

One deliberate change, named: the three checks now all run before any
section prints. Output is byte-identical (the audit functions are pure
list-builders over parsed data), but a check that raised would no longer
show its heading first.

`core/audit.py` **66% → 100%** — its whole gap was `run_audit`'s
reporting half, since all four pre-existing tests passed `verbose=False`.
Coverage floor **53 → 54** (54.62% measured), 872 → 891 tests. Also
deleted an `assert issue_count >= 0` that held for every value the
function can return but the `-1` case, so it pinned nothing.
Adjudication record: [docs/internal/PYSMELLY.md](internal/PYSMELLY.md).

**Phase 53e-3 (`deploy/deployer.py`) is the next open subphase.**

Phase 54 (emergency-subsystem test coverage) **shipped 2026-08-12** in
`5f6b287` — checkpoint/ecs/rds 0% → 100%, coverage floor 25 → 32; record
in claude-meta docs/PLAN-ARCHIVE.md. Its tests pin today's
swallow-ClientError contracts on purpose, for 53i to decide.
