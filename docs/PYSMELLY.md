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
with the fix drafted, and **ratified 2026-09-21** (answer `c10`,
`LsSmallA` — "ratify all seven") — **re-evaluate-by: the second
comprehensive review following 2026-09-18**:

- single-call-site, `modules/staging-scheduler/lambda/handler.py:199`
  `stop_environment` — half a deliberate start/stop pair; inlining one half
  reads worse. *(Anchor re-measured at `f33c84c`; it was `:195` at
  `a510815`.)*

A second single-call-site finding, `is_likely_secret`, was not one of the
twenty units. It was adjudicated on its own merits on 2026-09-22 and does
**not** inherit `stop_environment`'s verdict. The verdict is a keep, and the
operator has not yet ratified it:

| Adj | Check            | Anchor, 2026-09-22                                                      | Why it stands                                                                                                                                                                                                                                                                                                                      | Ratified | Re-evaluate-by                                                                                  |
| --- | ---------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------- |
| A10 | single-call-site | `src/deployer/init/deploy_toml.py:93` `is_likely_secret` (call at :228) | It names the secret-detection policy. It is the one env-var rule that combines two tables with a precedence (an exact `NON_SECRET_ENV_VARS` entry beats a case-folded `SECRET_PATTERNS` substring). U13 made every other arm of `_build_environment_config`'s loop a one-table test, and this keeps the secret arm reading as one. | pending  | The rule collapses to a single table test, or the second comprehensive review after 2026-09-18. |

What the verdict rests on, measured rather than argued:

- **Not independently tested, so tests are not the reason.** No test names it.
  `tests/unit/test_init_deploy_toml_pins.py` deliberately drives every private
  helper through `generate_deploy_toml`, including the precedence and
  case-folding pins.
- **The inline was drafted and measured**, then reverted. It clears the finding
  (44 → 43), mints nothing, and the 158 tests in `test_init.py` and the pins
  file pass. So this is a readability verdict, not a "the fix does not work"
  one. The inline puts a three-line, two-table boolean at the head of a loop
  whose other arms are `var_name in TABLE`.
- **History.** The operator confirmed a leave-standing on this function on
  2026-08-25 (§53p, at `:77`) because "the named predicate heads a 4-way
  dispatch". U13 (`980b9be`) removed that `elif` dispatch, so the old reason no
  longer describes the code. That is why this was re-adjudicated rather than
  carried forward. The earlier note here, that nobody had ever adjudicated it,
  was wrong.
- **A trap for the next editor.** pysmelly's single-call-site check skips any
  function spanning 10 or more lines. A docstring two lines longer makes this
  finding vanish without anyone deciding anything. The docstring was kept to
  four lines so the finding stays live and this row stays attached to it. If
  the function grows past that, record here that the finding stopped firing
  by threshold, not by fix.

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

### Findings left standing — ratified 2026-09-21

The 2026-09-18 run was unattended, so it escalated these nine families rather
than adjudicating them. **The operator ratified eight of them on 2026-09-21**
(answer `b3`, `PysDeployer` — "leave all standing: ratify eight declines");
A08 had already been closed on the repo side by `ad50a02`, which is this file.

**A ratified row is a decision, not a backlog entry.** A scout running the next
review reads this table and does *not* re-mint a unit for a row here — it
re-measures the anchor, and only re-opens the question if the
`re-evaluate-by` trigger has fired or the finding's shape has changed. Every
anchor below was re-measured at `f33c84c` (total 44 findings); the counts
differ from the ones the run recorded at `f5d22e0`/`a510815` because eighteen
units moved the tree in between.

| Adj | Check                            | Anchor at `f33c84c`                                                                                                                                                                                                                                                            | Why it stands                                                                | Ratified   | Re-evaluate-by                                                |
| --- | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------- |
| A01 | env-fallbacks ×4                 | `modules/db-on-shared-rds/lambda/index.py:51`, `modules/db-users/lambda/index.py:44`, `modules/lambda-shared/db_common.py:54`, `modules/staging-scheduler/lambda/handler.py:44`                                                                                                | LOG_LEVEL is a fleet-wide optional default, not required config.             | 2026-09-21 | LOGGING.md stops treating LOG_LEVEL as optional-with-default. |
| A02 | inconsistent-error-handling ×10  | `src/deployer/utils/logging.py:47`/`:67`/`:72`/`:77`, `src/deployer/core/config.py:205`, `src/deployer/utils/environment.py:17`, `src/deployer/aws/rds.py:28`, `src/deployer/emergency/ecs.py:82`, `src/deployer/init/template.py:126`, `src/deployer/utils/aws_profile.py:85` | Callers legitimately differ; a caller-count check documentation cannot move. | 2026-09-21 | The second comprehensive review following 2026-09-18.         |
| A03 | dict-as-dataclass                | `src/deployer/deploy/autoscaling.py:140`                                                                                                                                                                                                                                       | A boto3 `**kwargs` bag spread at both call sites, not data.                  | 2026-09-21 | The boto3 keys stop being spread straight into the API call.  |
| A04 | law-of-demeter                   | `src/deployer/deploy/deployer.py:311`                                                                                                                                                                                                                                          | `client.exceptions.X` is the only supported botocore idiom.                  | 2026-09-21 | botocore offers another way to name service exceptions.       |
| A05 | param-clumps ×9                  | `bin/ecs-run.py:141` (strongest), plus eight listed by `--check param-clumps`                                                                                                                                                                                                  | Nine dataclass extractions are an interface programme, not a fix.            | 2026-09-21 | The second comprehensive review following 2026-09-18.         |
| A06 | `internal-only` skip stays       | skip entry in `pyproject.toml`; 6 findings behind it                                                                                                                                                                                                                           | The six survivors are Click `cmd_*` entry points, unrenameable.              | 2026-09-21 | pysmelly learns to recognize Click-dispatched entry points.   |
| A07 | `scattered-constants` skip stays | skip entry in `pyproject.toml`; 8 findings behind it                                                                                                                                                                                                                           | Naming `' - '`, `'PATH'` or `50` makes the code worse.                       | 2026-09-21 | Second comprehensive review after 2026-09-18 (`50`: below).   |
| A09 | foo-equals-foo ×3                | `bin/init.py:220`, `bin/init.py:563`, `src/deployer/config/deploy_config.py:498`                                                                                                                                                                                               | Locals computed in multi-line branches; inlining reads worse.                | 2026-09-21 | The second comprehensive review following 2026-09-18.         |
| A08 | register location                | this file                                                                                                                                                                                                                                                                      | Resolved on the repo side by `ad50a02`; the guide half is claude-meta's.     | —          | —                                                             |

A05's anchor moved during the run: U17's extraction added a seventh member to
the `(cluster_name, ecs_client, service_name)` clump and moved it from
`src/deployer/aws/ecs.py:91` into `bin/ecs-run.py:141`. A06's rationale
comment in `pyproject.toml` describes the skip, not the live findings. A07's
was rewritten at `6d36aa6` to list the eight findings the skip hides, one line
each, because the U10 rewrite described them as strings when two of the eight
are numeric clusters. Either comment is a snapshot: re-test with
`pysmelly . --check <name> --more-please` rather than reading it, as this
register's convention says.

**A07's `50` trigger, discharged 2026-09-22 at `6d36aa6`.** The literal `50`
appears at three sites, and each was read with its callee. They are three
unrelated limits, so none was named and the code is unchanged:

- `bin/init.py:575` — `max_lines=50` to `_print_dry_run_preview`: how many
  lines of each generated file `init.py environment --dry-run` prints before
  truncating. A display choice for rendered tofu/TOML templates.
- `bin/ops.py:869` — `limit=50` from `cmd_audit` to `cmd_logs`: the
  `filter_log_events` result cap per log group in the audit's error scan. The
  "(N errors)" count saturates at it; only ten are printed. The standalone
  `ops.py logs` defaults to 100, so this is the audit's own tighter budget, not
  a shared log-limit.
- `src/deployer/deploy/service.py:976` — `_display_migration_logs`'s
  `limit: int = 50`: how many of the most recent `get_log_events` lines from
  one failed migration task's stream are shown to explain the failure.

The second and third are both CloudWatch caps, but on different APIs (a
filtered search across a group versus the tail of one stream) for different
purposes (counting errors versus explaining one failure). A reason to change
one gives no reason to change the other, so sharing a name would couple them
for no gain.

**Not ratified, and therefore still open:** the pass-through-params remainder
(14 findings at `f33c84c`, including the two `utils/cli.py` error-boundary
adapters). That is scoped as plan work, not as a standing row. The
sentinel-returned-from-`except` sweep that used to be listed here was worked
on 2026-09-22; see below.

### Non-pysmelly verdicts ratified in the same review

Recorded here so the one register a scout reads carries every 2026-09-18
verdict, not only the pysmelly ones:

| Finding                                                               | Guide | Why it stands                                                     | Ratified   | Re-evaluate-by                                            |
| --------------------------------------------------------------------- | ----- | ----------------------------------------------------------------- | ---------- | --------------------------------------------------------- |
| `docs/internal/SIMILAR-TOOLS.md` lives outside `docs/investigations/` | DOCS  | A positioning survey the investigations rule was not written for. | 2026-09-21 | The next time the tool landscape is actually re-surveyed. |

Branch protection on the public release repo's `main` was ratified as an
accepted risk in the same round (answer `a1`); its home is risk **R3** in
[docs/GOVERNANCE.md](GOVERNANCE.md), not here.

### 2026-09-22 sentinel-returned-from-`except` sweep

The 2026-09-18 review looked for this and did not clear it. PYSMELLY-REVIEW uses
this repo's `emergency/` family as its worked example of the contract bug: one
value meaning both "there is nothing there" and "I could not look". A grep hit
proves only the *shape*; clearing one means reading the `except` body and every
caller. That reading has now been done, hit by hit. It is invisible to the
finding count by construction, so the per-hit table in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md) § "2026-09-22 — the
sentinel-returned-from-`except` sweep" is the record. This row summarises it:

| Sweep                                     | Measured at | Hits                                                           | Verdicts (a fine / b fixed / c narrowed)                                                                                                                                                                                                    | Commits                                                     | pysmelly                                                                                                                                                                                                                                                                                                                       | Ratified |
| ----------------------------------------- | ----------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| returned sentinel, plus `pass`/`continue` | `0b60994`   | 40 returned-sentinel + 23 `pass`/`continue`, 2 of them repeats | **40 hits: 28 / 9 / 3.** **23 hits: 13 / 9 / 0**, plus 1 repeat of a (c). **5 more (b)** found by reading callers and neighbours. `emergency/` itself: **all (a)**; its 53i-3 fixes hold, and its mutators' `False` is ratified contract 3. | `1866153` `4a33224` `f16f840` `4d9fae4` `0283520` `8af8113` | 44 → 44, set moved. Retired: `logging.py log_error` (its one specific caller's `except` was removed by `1866153`). Minted: **`aws/ecs.py get_services`** inconsistent-error-handling. All 5 callers handle the raise, but the check does not credit `cmd_stop`'s `with exit_on(...)`. Pending adjudication, same class as A02. | pending  |

The six notes in that section are things the sweep saw and deliberately left:
the emergency mutators drop the AWS reason text; a `WaiterError` from
`create_emergency_snapshot` escapes `emergency.py`'s boundary as a traceback;
`ci-deploy --strict` passes when it cannot parse `resolved_at`; and three
smaller ones. None is a sentinel collapse. Each needs its own decision.

### Earlier arcs

**Phase 53 (2026-08-12 … 2026-08-25), twenty subphases**, closed with every live
finding attributed and nothing escalated — the state of the tree at `187b2f9`,
not a standing property of the repo. Per-subphase reasoning, the latent-bug
ledger and the five-population pin ledger are in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md). Fleet-level session records
live in claude-meta's `audits/`.
