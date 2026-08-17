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

**37 findings** at `aacee1b` (53h-2b), from 97 at the start of the arc.
**53a through 53h are done.** Open: **53i** — adjudication rather than
code motion, which is why the 2026-08-13 unattended run stopped short of
it.

Outcomes: 53a–53e in [PLAN-ARCHIVE.md](PLAN-ARCHIVE.md) and
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md); 53f and 53g in
claude-meta `docs/PLAN.md` Phase 53 (**they have no adjudication entry in
this repo's register** — a gap the 2026-08-13 run left, and one that 53h-1
and 53h-2 did not fill either; it is still open). 53h-1, 53h-2a and 53h-2b
have entries in the register.

`long-function` **9 → 0**, `dict-as-dataclass` **6 → 0**,
`write-only-attributes` **1 → 0**, `duplicate-except-blocks` and
`boolean-param-explosion` empty as categories, and the
convergence-hotspot list is empty. Coverage floor **53 → 70**.

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

### 53i — mechanical residue + the caller-contract policy call

`foo-equals-foo` ×3, `single-call-site` ×3, and the
`inconsistent-error-handling` contracts. The one that needs a written
policy rather than code motion. Its inputs have accumulated across the
arc: Phase 54's pinned swallow-`ClientError` tests, 53c's unsuppressed
`run_aws_json` and five untagged inline suppressions, 53d-1's
`capacity-report` exit-code conflation, 53d-2a's `emergency.py`
decline-vs-failure exit codes, and 53d-2b + 53e-1's two bare
`except Exception` handlers that misattribute an internal failure to an
operator-facing cause. Each is pinned by a test naming 53i, so the tests
are the checklist of call sites to change.
