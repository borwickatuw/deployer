<!-- pysmelly-guidance 0679ec1c1685 -->

# pysmelly — findings register and review conventions

Run `make pysmelly` (repo root — config lives in `[tool.pysmelly]` in
`pyproject.toml`). **`make pysmelly` truncates to the top ten findings and exits
non-zero through make**, so it never shows the per-category table; run
`uvx pysmelly . --more-please` to see all of them. Full guidance:
https://github.com/borwickatuw/pysmelly#readme or regenerate the generic guide
with `pysmelly init --short`.

This file is the **current-facing register** — the convention, the standing
suppressions, and what was decided about findings left standing. The
phase-by-phase reasoning behind each verdict (Phase 53's twenty subphases, and
the per-unit records of the 2026-09-18 run) is in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md), which is a history and is
read back, not maintained as current.

## Deployer's convention

**Derive the count; never read one here.** Every count in this register is
pinned to the commit it was measured at, and a pinned count is history: it
describes that tree, not the one you have checked out. Re-measure before acting
on any number, in this file or in the internal log.

**Near-zero suppression.** Findings are fixed, or left standing as an
operator-visible decision recorded here. Inline `# pysmelly: ignore` is reserved
for the cut-and-dry false positive, each with a rationale and a
`re-evaluate-by:` tag. A directive that suppresses nothing is deleted, not left
as scenery; the rationale prose above it may stay, demoted to a plain comment.

**Syntax: brackets, or it is a blanket ignore.** Write
`# pysmelly: ignore[<check-name>]`. The bracket-less spelling
`# pysmelly: ignore <check-name> — reason` parses as a **blanket** ignore of
every check on that line — the check name becomes prose, and the directive
silently hides findings nobody adjudicated. This repo carried the bracket-less
form in all of its directives until 2026-09-18 (see the audit below); one of
them was hiding two checks it did not name while the check it *did* name had
stopped firing years of commits earlier.

**Placement: the finding line, or the line immediately above it.** pysmelly
looks nowhere else. Put the directive on a full line of its own directly above
the finding, with the rationale and the `re-evaluate-by:` tag on the lines above
that — never as a trailing comment, which black re-wraps out of the window when
the line grows. Ruff reads `ignore[<name>]` as commented-out code, so a bare
directive line needs `# noqa: ERA001` alongside it; a directive followed by
prose does not.

**Whole-category `skip` is for the structurally inapplicable**, not the merely
inconvenient. Each entry in `[tool.pysmelly]` carries its rationale inline in
`pyproject.toml`, and each rationale is re-tested against live output every
review — `pysmelly . --check <name> --more-please` — because a skip whose FP
class the tool has since fixed hides real findings.

**Scan scope.** `make pysmelly` runs over the whole repo including `modules/`,
which is deliberate: the OpenTofu modules carry Python Lambda code. The
per-module `lambda/` directories also hold pip-vendored packages at apply time;
those are gitignored build artifacts absent from a clean tree. The tracked
shared Lambda code lives in `modules/lambda-shared/`. Because the exclude is
resolved against `.gitignore`, any scratch copy used for a strip-audit must
carry `.git` along or it will scan the vendored trees and produce phantom
findings.

## Standing inline suppressions

Enumerate them; do not count them from here:

```bash
grep -rn 'pysmelly: ignore' --include='*.py' . | grep -v .venv
```

| Location                                                             | Check                        | Why it stands                                                                 |
| -------------------------------------------------------------------- | ---------------------------- | ----------------------------------------------------------------------------- |
| `bin/cognito.py` `cli.command`                                       | shotgun-surgery              | Click decorator accessed in every `bin/` entry point — a facade reads worse.  |
| `bin/resolve-config.py` `build_meta`                                 | dict-as-dataclass            | The return is serialized to JSON for the resolver's consumers.                |
| `modules/db-on-shared-rds/lambda/index.py` `handle_setup_database`   | dict-as-dataclass            | A Lambda return must be a dict for JSON serialization.                        |
| `src/deployer/config/compose.py` `get_compose_services`              | isinstance-chain             | Parsing untyped YAML; the chain *is* the schema discrimination.               |
| `src/deployer/core/config.py` `get_commands_from_deploy_toml`        | isinstance-chain             | Same — untyped TOML input.                                                    |
| `src/deployer/core/config.py` `get_cognito_user_pool_id_from_config` | return-none-instead-of-raise | `None` is the absence sentinel; see DECISIONS.md "Error Contracts".           |
| `src/deployer/init/bootstrap.py` `bootstrap_dir_exists`              | return-none-instead-of-raise | Same contract.                                                                |
| `src/deployer/utils/datetime.py` `format_iso`                        | inconsistent-error-handling  | Formatting helper with no failure mode of its own; callers' contracts differ. |

Each carries a `(re-evaluate-by: …)` tag at the directive. Re-verify the set
with the strip-audit method below, not by reading this table.

## `[tool.pysmelly]` config

`skip` and `exclude` live in `pyproject.toml` with their rationale inline —
read them there, not here. Two things a future audit should not re-derive:

- `exclude = ["tests/"]` **replaces** pysmelly's default exclude list, which
  drops `conftest.py`. Checked 2026-09-18: the repo's only `conftest.py` is
  `tests/conftest.py`, already inside the excluded prefix, and
  `find . -path ./.venv -prune -o -name tests -type d -print` returns only the
  top-level `./tests`, so the trailing-slash top-level-prefix limitation does
  not bite here. Costs nothing today; one directory move from a gap.
- `skip` rationales are re-tested each review with
  `pysmelly . --check <name> --more-please`. The 2026-09-18 pass found one skip
  inert and deleted it, and found the other two describing findings the tool no
  longer reports (see below).

## Adjudication record

### 2026-09-18 comprehensive review

**Baseline, re-measured at `f5d22e0`** (tree clean, pysmelly
3.4.1.dev2+g67d5d9772): **55** findings across 14 categories, "Parsed 77 Python
files". Two convergence hotspots — `src/deployer/deploy/service.py` (5 checks)
and `modules/db-on-shared-rds/lambda/index.py` (3, or 5 with its inline ignores
stripped). Strip-audit at the same commit: **67** with all directives removed,
i.e. 12 findings live-suppressed by 14 directives.

Twenty units were scoped; eighteen landed as commits (`f5d22e0..a510815`), each
independently validated against the diff rather than the remediator's report.

| Unit | Check                                 | Outcome | Commit    | What changed                                                                                                              |
| ---- | ------------------------------------- | ------- | --------- | ------------------------------------------------------------------------------------------------------------------------- |
| U01  | dead-code                             | applied | `ac48737` | Deleted the `service_exists` wrapper; retargeted its tests at `_get_live_service`.                                        |
| U02  | unused-defaults                       | applied | `b45b9d6` | `wait_for_stable` requires the updated-services map; the weaker None path is gone.                                        |
| U03  | unused-defaults                       | applied | `e228ce1` | `create_database_extensions` requires `app_name`/`environment`; the SSM skip path is unconditional.                       |
| U04  | suppression audit                     | applied | `2c00337` | Deleted three directives that suppressed nothing (2 dead, 1 redundant companion).                                         |
| U05  | suppression audit                     | applied | `d70b690` | Every directive rewritten to `ignore[<check>]`; two Lambda handlers renamed `_context`, retiring the findings outright.   |
| U06  | suppression placement                 | folded  | —         | The two trailing black-unstable directives were retired by U05 before this unit ran.                                      |
| U07  | suppression audit + dict-as-dataclass | applied | `e76b5ac` | `rds.get_status` answers an `RdsStatus` NamedTuple; the mislabelled blanket directive deleted.                            |
| U08  | skip-rot                              | applied | `c6895d8` | Removed the inert `shared-mutable-module-state` skip (the check reports nothing).                                         |
| U09  | internal-only                         | applied | `65ea718` | Renamed the file-private helpers `_`-prefixed; declared `db_common.__all__`.                                              |
| U10  | scattered-constants                   | applied | `a83ab2a` | Named the RDS wait budget, the poll cadence and the AWS error codes; fixed a third copy of the `timeout // 15` invariant. |
| U11  | duplicate-blocks                      | applied | `329a1e6` | `connect_as_master` shared by the db-\* twins; the second connection now names its endpoint.                              |
| U12  | register currency                     | applied | `d2e0a40` | The internal log's headline count became a derivation plus SHA-pinned history.                                            |
| U13  | arrow-code                            | applied | `980b9be` | The env-var rules are three module tables, not a depth-6 ladder.                                                          |
| U14  | temp-accumulators                     | applied | `8105a7c` | The cache-tag recipe is three named rules; the order contract is stated where it is enforced.                             |
| U15  | long-function                         | applied | `c31b824` | `_wait_for_service_stable` decomposed; the settle window is a named record.                                               |
| U16  | long-function                         | applied | `d76d0ec` | `Deployer.deploy()` is the step sequence; each step's timing key derives from its method name.                            |
| U17  | long-function                         | applied | `efd027e` | `_run_ecs_command` split into plan / launch / await; the migrate-family name is now tested.                               |
| U18  | single-call-site                      | applied | `2b4c999` | Inlined `run_pa11y`; added the first tests for `bin/a11y.py`.                                                             |
| U19  | single-call-site                      | skipped | —         | See below.                                                                                                                |
| U20  | pass-through-params                   | applied | `a510815` | One SSM path scheme instead of two; finding-neutral, taken for maintainability.                                           |

**No suppression was added by any unit.** The only suppression-shaped additions
in the whole run are four `# noqa: ERA001` required by the bracket syntax (each
proven necessary by re-running ruff with it removed) and three
`# pragma: allowlist secret` on fabricated test-fixture passwords.

### Skipped and reverted findings

Nothing was reverted. One finding was left standing, escalated to the operator
with the fix drafted — **re-evaluate-by: 2027-03 review** (the second
comprehensive review following this one; the cadence is ~3 months per
claude-meta's `docs/HOWTO-COMPREHENSIVE-REVIEW.md` closeout step):

- single-call-site, `modules/staging-scheduler/lambda/handler.py`
  `stop_environment` — half a deliberate start/stop pair; inlining one half
  reads worse.

### Standing-suppression audit (Practice #10)

**Method: strip-audit.** `git archive HEAD | tar -x` into a scratch directory
plus a copy of `.git` (so `.gitignore` is honoured and the vendored `lambda/`
trees stay out), pristine re-run first to prove determinism, then every
directive removed and the two `--more-please` listings diffed.

**At `f5d22e0`, before the run:** 14 directives suppressing 12 findings.
Classified into Practice #10's five classes — 11 live at the anchor, 2 dead, 1
redundant companion, 0 drifted-within-file, 0 ineffective non-anchor. Two
mechanical defects ran across the whole set: every directive used the
bracket-less form and was therefore a blanket ignore, and two sat as trailing
comments on 130-character lines, inside the black-rewrap failure mode that
killed 40 of havoc's 105 ignores.

**At `a510815`, after U04–U07 — re-measured, not inherited:** the pristine
scratch copy reproduced HEAD exactly (44 findings); stripped, 52. So **eight
directives suppress exactly eight findings, one each**, every one live, every
one scoped to the check it names, and every one sitting at `finding.line - 1`.
The blanket-ignore defect and the trailing-placement defect are both gone from
the repo. Re-run the method above rather than trusting this paragraph.

**Skip entries, re-tested at HEAD:** `shared-mutable-module-state` reported
nothing and was deleted (U08). `internal-only` and `scattered-constants` both
still hide real findings whose rationale the skip does not describe; U09 and U10
fixed what was fixable underneath them, and **whether either skip stays at all
is an open operator question**, not a settled one.

### Findings left standing at `a510815`

This was an unattended run, so no operator was in the loop and **none of the
remaining findings carry an operator verdict**. They are escalated, not
adjudicated; absence of a row here is not a decision. The families awaiting
adjudication, in the run's own numbering:

| Adjudication | Family                                                                                                                                                                  |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A01          | env-fallbacks — `LOG_LEVEL` defaults in the four Lambda bundles.                                                                                                        |
| A02          | inconsistent-error-handling — including `rds.get_status`, where the check does not credit the `exit_on` context manager and the "unhandled" callers do have boundaries. |
| A03          | dict-as-dataclass — `autoscaling._metric_alarm_common`.                                                                                                                 |
| A04          | law-of-demeter — `self.rds.exceptions.DBInstanceNotFoundFault`.                                                                                                         |
| A05          | param-clumps — scoped as a post-`service.py` subphase.                                                                                                                  |
| A06          | Whether the `internal-only` skip stays, and whether `db_common.__all__` is the right export surface.                                                                    |
| A07          | The `scattered-constants` remainder, including an unexamined `50` cluster.                                                                                              |
| A08          | Register location — resolved by this file; the guide's runnable check is `ls docs/PYSMELLY.md`.                                                                         |
| A09          | foo-equals-foo.                                                                                                                                                         |
| —            | pass-through-params not covered by U20, including the two `utils/cli.py` error-boundary adapters.                                                                       |

**One thing looked for and not cleared.** PYSMELLY-REVIEW uses this repo's
`emergency/` family as its worked example of the sentinel-returned-from-`except`
contract bug — one value meaning both "there is nothing there" and "I could not
look". The guide's grep returns many hits here, but a hit is evidence of the
*shape*, not the bug, and clearing one means reading the `except` body and every
caller. That was not done, so this is **not** reported clear. The shape is
densest in `emergency/ecs.py`, `emergency/rds.py` and `deploy/service.py`. It
is invisible to the finding count by construction: the `except` that hides the
failure from the operator hides it from pysmelly too.

### Earlier arcs

**Phase 53 (2026-08-12 … 2026-08-25), twenty subphases**, closed with every live
finding attributed and nothing escalated — the state of the tree at `187b2f9`,
not a standing property of the repo. Per-subphase reasoning, the latent-bug
ledger and the five-population pin ledger are in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md). Fleet-level session records
live in claude-meta's `audits/`.
