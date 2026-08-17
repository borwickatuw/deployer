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

**38 findings** at `a8d7369` (53h-1), from 97 at the start of the arc.
**53a through 53g and 53h-1 are done.** Open: **53h-2 and 53i** — both
adjudication rather than code motion, which is why the 2026-08-13
unattended run stopped short of them.

Outcomes: 53a–53e in [PLAN-ARCHIVE.md](PLAN-ARCHIVE.md) and
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md); 53f and 53g in
claude-meta `docs/PLAN.md` Phase 53 (**they have no adjudication entry in
this repo's register** — a gap the 2026-08-13 run left, not something
53h-1 filled). 53h-1's entry is in the register.

`long-function` **9 → 0**, `dict-as-dataclass` **6 → 0**,
`write-only-attributes` **1 → 0**, `duplicate-except-blocks` and
`boolean-param-explosion` empty as categories, and the
convergence-hotspot list is empty. Coverage floor **53 → 70**.

**Carry into what is left**, the arc's most-repeated lesson: four times
running (53d-1, 53d-2a, 53e-2, 53h-1) the real defect was duplication
pysmelly could not reach — copies that interleave with other calls are
not runs of consecutive statements, so `duplicate-blocks` never sees
them. Read for repetition before planning anything, and re-measure at
HEAD first; every count here is pinned to a SHA.

### 53h — `modules/` collect() interface

Split in two when 53h-1 was planned, because reading the code found four
items pysmelly does not flag and one of them changes the subphase's
shape. Detail in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md) § 53h-1.

| Slice | Scope                                                                    | Status |
| ----- | ------------------------------------------------------------------------ | ------ |
| 53h-1 | what `ModuleContext` carries — dead fields, the ARN, `credential_mode`   | done   |
| 53h-2 | the `collect()`/`validate()` signature, and what a "module" is           | open   |

**53h-2 owns three questions, none of them now forced by a finding:**

1. Whether `(app_config, env_config, context)` becomes one `ModuleInputs`.
   53h-1's `@override` decorators cleared the `param-clump` as a side
   effect — pysmelly treats an interface-conformance signature as a
   contract rather than a chosen clump — so this is a merit decision, not
   a finding to close.
1. `database.validate`'s `feature-envy` (11 `env_config` reads against 1
   of `self`), which survives 53h-1 by design. A `DatabaseEnvConfig`
   dataclass would **not** clear it; only moving the logic onto the config
   type or extracting module-level helpers does.
1. **`_MODULE_SECTIONS` and the registry do not agree.**
   `task_definition.py:16` names `cdn` and `autoscale`, which no module
   implements, so a `[cdn]` section flips "the module system is in use" —
   changing which secrets path runs — while `validate_all` never validates
   it. Conversely `secrets` is a registered module missing from the tuple
   and special-cased instead, and the two readers disagree about it. All
   of it is pinned in `tests/unit/test_module_collect_pins.py`
   (`TestModuleSectionsRegistryGap`), pinned-not-endorsed.

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
