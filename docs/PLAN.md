# deployer Plan

Active work items. For completed phases, see
[PLAN-ARCHIVE.md](PLAN-ARCHIVE.md).

_No repo-local phases yet._ This file exists so cross-repo backlog has a
visible home in-repo (fleet convention).

Cross-repo backlog queued against this repo: claude-meta docs/PLAN.md
Phase 52 (container-level health checks for non-HTTP services) and Phase
53 (pysmelly subphase backlog 53a–53i).

Per-finding dispositions for the Phase 53 arc are in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md); size and simplification
candidates in [docs/internal/HOWTO-SIMPLIFY.md](internal/HOWTO-SIMPLIFY.md).

## Phase 53 status

**32 findings** at `4678c1e` (the 53i-2a closeout), from 97 at the start of
the arc. **53a through 53h, 53i-1 and 53i-2 are done.** Open: **53i-3**
(applying the policy), blocked on nothing but operator sign-off of the two
escalated items in the 53i-2c ADR.

**53i-2 moved the count by zero, by design** — 53g's precedent. 53i-2a moved
prose inside comments, 53i-2b wrote two claude-meta guides, and 53i-2c is an
ADR. The count was verified at 32 before and after 53i-2a, diffed as a finding
set rather than as a total.

Outcomes: every subphase now has an adjudication entry in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md), with 53a–53e also in
[PLAN-ARCHIVE.md](PLAN-ARCHIVE.md). **The 53f/53g register gap is closed**
(2026-08-18): both shipped in the 2026-08-13 unattended run without an
entry, 53h-1 and 53h-2 did not fill it, and it blocked 53i — which cannot
be scoped against a settled/open split that does not exist. §53f and §53g
are backfilled from their commit messages and the run ledger, and §53g's
skip list was re-verified at HEAD first, which found three stale verdicts
and one dead parameter.

**All 32 are attributed and nothing is unowned** — 20 adjudicated
leave-standings, **7 escalated by 53i-1** with measured diffs and awaiting
the operator, and 5 now **adjudicated by 53i-2c's ADR** and awaiting 53i-3. The 3 findings the closeout found
owned by no subphase were folded into 53i-1 by operator decision
(2026-08-18); one is cleared, two are among the seven. See the register's
"Remainder — the reconciled adjudication split".

`long-function` **9 → 0**, `dict-as-dataclass` **6 → 0**,
`write-only-attributes` **1 → 0**, `duplicate-except-blocks` and
`boolean-param-explosion` empty as categories, and the
convergence-hotspot list is empty. Coverage floor **53 → 74**.

**Carry into what is left**, the arc's most-repeated lesson: five times
running (53d-1, 53d-2a, 53e-2, 53h-1, 53h-2b) the real defect was
duplication pysmelly could not reach — copies that interleave with other
calls are not runs of consecutive statements, so `duplicate-blocks` never
sees them. Read for repetition before planning anything, and re-measure at
HEAD first; every count here is pinned to a SHA.

**And the lesson 53h-2 added**: three live bugs in one subphase, all the
same shape — a producer and a consumer that had never been run against
each other. pysmelly flags none of that class. What found all three was
writing characterization pins at the outermost boundary of an 8%-covered
module and then composing the readers end to end
(`tests/unit/test_init_deploy_round_trip.py`). **Coverage of the seam
between two components is worth more here than any single check.**

### 53h — `modules/` collect() interface — **done** (2026-08-17)

| Slice  | Scope                                                                  | Status |
| ------ | ---------------------------------------------------------------------- | ------ |
| 53h-1  | what `ModuleContext` carries — dead fields, the ARN, `credential_mode` | done   |
| 53h-2a | one `[secrets]` style, and the module boundary                         | done   |
| 53h-2b | the two signature adjudications                                        | done   |

Detail in [docs/internal/PYSMELLY.md](internal/PYSMELLY.md) §§ 53h-1,
53h-2a, 53h-2b. In short:

- 53h-2a was **not** an adjudication. The `_MODULE_SECTIONS` question was a
  live bug: explicit `[secrets]` plus any module section dropped every
  secret, with preflight and the audit both passing. One style now
  (`names`), one collection route, and a preflight rejection naming the
  migration. Pysmelly 38 → 38, as planned — correctness, not findings.
- 53h-2b cleared `database.validate`'s `feature-envy` (38 → 37, **partly via
  a mechanic** — `check_feature_envy` only walks `ClassDef` bodies) and
  replaced `DeployConfig`'s hardcoded module knowledge with
  `ResourceModule.injected_names`. The `ModuleInputs` bundle was drafted,
  measured at **37 → 43**, and rejected; the probe is unmerged at `235a215`
  on `probe/module-inputs`.

**Follow-on work this opened, not yet scheduled:**

1. **`ssm-secrets.py check` cannot classify anything on its own.** It never
   loads the environment's config.toml, so with the explicit `[secrets]`
   form gone every deploy.toml either declares nothing or gets the "run
   preflight instead" advice block. Already true for both fleet repos
   before 53h-2a. Teaching `cmd_check` to load config.toml would make the
   command useful again.
1. **`get_secrets_from_config`'s `environment` parameter is unread.**
   Removing it ripples through `check_secrets_exist`,
   `check_secrets_drift`, `get_secrets_from_deploy_toml`,
   `preflight.check_ssm_secrets` and `bin/ssm-secrets.py` — and would
   retire the `param-clumps` finding on `ssm_secrets.py:84`. Kept out of a
   correctness slice on purpose; it belongs with 53i.
1. **`_build_images_config` has the same `.get(key, default)` bug shape** as
   the crash fixed at `c31f0f3` — `get_compose_services` sets the key to
   `None`, so the default never fires. There it writes an absent
   `dockerfile` key rather than crashing, and deployer's own default takes
   over, so it was pinned as-is rather than changed.

### 53i — split into three (2026-08-18)

Reading 53i's contents found **two unrelated kinds of work**, so it split
at planning time — the arc's fourth planning-time split, after 53d (twice),
53e (up front) and 53h (twice).

| Unit      | Scope                                                  | Status              |
| --------- | ------------------------------------------------------ | ------------------- |
| **53i-1** | 10 mechanical findings — code motion and adjudication  | **done** (35 → 32)  |
| **53i-2** | The raise-vs-return policy — split again into 2a/2b/2c | **done** (32 → 32)  |
| 53i-3     | Apply that policy across the call sites 53i-2c names   | blocked on sign-off |

#### 53i-1 — mechanical residue — **done** (2026-08-18)

Three findings cleared in three commits, each measured against the previous
unit rather than the run total:

| Commit    | Change                                                      | Count   |
| --------- | ----------------------------------------------------------- | ------- |
| `d0330c2` | Pin-first: 5 pins on `--max-config-age`/`--strict`, no code | —       |
| `af23287` | One SSM leaf-name transform, not three                      | 35 → 34 |
| `78c3a6c` | `template.py` asks for the deployer root, not `.parent` ×3  | 34 → 33 |
| `a9327ab` | Lift ci_deploy's staleness gate out of `main()`             | 33 → 32 |

Seven findings were drafted, measured and **escalated to the operator**
rather than recorded as self-authored leave-standings. Two of the drafts
carry evidence that changes the question: `bin/init.py:557`'s inlining
silently widens a `try` to swallow a `FileNotFoundError` from the listener
-priority scan, and `bin/init.py:220`'s **does not clear the finding and
breaks 10 tests**, because inlining `click.prompt` results into a
constructor call reorders the prompts. Diffs and costs are in the
register's §53i-1.

Two findings were not what their category said, both found by reading:
`modules/secrets.py:86` was 1 of 3 copies of one transform (a sixth
producer-and-consumer-never-run-together instance), and
`init/deploy_toml.py:195`'s depth-6 `arrow-code` is one flat five-arm
`elif` chain the check counts as nesting — the fourth "the check's
mechanic, not the code" find in this arc, filed as a pysmelly feature
request in claude-meta `docs/GUIDE-BACKLOG.md`.

Coverage 74.23% → **74.94%** against a floor of 74; the pin-first commit
bought 0.68 points before any production code moved.

#### 53i-2 — the raise-vs-return policy — **done** (2026-08-18)

Split again at planning time into **53i-2a** (reattach six detached suppression
rationales — `4678c1e`, 32 → 32), **53i-2b** (the policy written **fleet-wide**
in claude-meta's `best-practices/PYTHON.md` #19 and `PYSMELLY-REVIEW.md`, by
operator decision, from a 13-repo measurement in which storage-scripts and
claude-meta each carry more of this corpus than deployer), and **53i-2c** (the
ADR below). Two of the corpus items dissolved on re-measurement: the "5 inline
suppressions carrying neither a rationale nor a tag" were **six**, carrying
**both**, detached from their directive by `df01cdb` — a documentation defect
with zero effect on the count, repaired as 53i-2a.

**The ADR is [DECISIONS.md](internal/DECISIONS.md) "2026-08-18: Error Contracts",
and it is 53i-3's checklist** — every call site named, across three layers:
`emergency/`'s eleven sentinel-from-`except` functions plus `run_aws_json`
(producer); the four `inconsistent-error-handling` contracts, classified
one false-positive / one document-only / two real-caller-bugs with the exact
unhandled sites listed (consumer); and the emergency CLI exit codes, where a
**third** swallow (`cmd_revert`) turned up that none of the eleven pins covers
(boundary). **Two items are escalated, not decided** — `emergency/` queries
raising, and exit code `2` for "declined" — and 53i-3 must not apply either
until the operator confirms.

Its corpus was
**31 "pinned, not endorsed" markers across 6 test files** (re-measured at
`a9327ab`; the 53i-1 plan entry said 50, but its own per-file list sums to 31
and the list is what verifies)
(`test_emergency_ecs.py` 11, `test_emergency_cli.py` 7,
`test_emergency_rds.py` 5, `test_extensions.py` 3, `test_init_cli.py` 3,
`test_emergency_cli_restore.py` 2), plus the 4
`inconsistent-error-handling` findings, `aws/cli.run_aws_json`, and the 5
inline suppressions carrying neither a rationale nor a `re-evaluate-by:`
tag. Its inputs have accumulated across the arc: Phase 54's pinned
swallow-`ClientError` tests, 53c's unsuppressed `run_aws_json` and those
five suppressions, 53d-1's `capacity-report` exit-code conflation, 53d-2a's
`emergency.py` decline-vs-failure exit codes, and 53d-2b + 53e-1's two bare
`except Exception` handlers that misattribute an internal failure to an
operator-facing cause. Each is pinned by a test naming 53i, so the tests
are the checklist of call sites 53i-3 changes.
