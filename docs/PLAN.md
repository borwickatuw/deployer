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

**56 findings** at the 53e-2 commit (from 97 at the start of the arc).
53a, 53b, 53c, all of 53d, 53e-1 and 53e-2 are **done** — outcomes in
[PLAN-ARCHIVE.md](PLAN-ARCHIVE.md). Open: **53e-3 through 53e-5, then
53f–53i.**

### 53e — `deploy/` pipeline decomposition

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

**Carry into every remaining slice:** three times running (53d-1, 53d-2a,
53e-2) a `long-function` target turned out to be duplication pysmelly
could not reach — copies that interleave with other calls are not runs of
consecutive statements, so `duplicate-blocks` never sees them. Read for
repetition before planning a decomposition, and re-measure at HEAD first;
every count above is pinned to a SHA.

**Phase 53e-3 (`deploy/deployer.py`) is the next open subphase.**

### 53f–53i

Scoped in claude-meta `docs/PLAN.md`; counts re-verified at the 53e-2
commit and unchanged, since 53e-1 and 53e-2 minted nothing.

| Subphase | Scope                                                                                  | Findings                                                         |
| -------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| 53f      | dict-as-dataclass sweep (`emergency/rds.py` ×3, `emergency/ecs.py`, `core/cognito.py`) | 5                                                                |
| 53g      | parameter plumbing → context objects                                                   | 14 pass-through + 5 open param-clumps                            |
| 53h      | `modules/` `collect()` interface redesign                                              | ~6 (param-clump, feature-envy ×2, write-only)                    |
| 53i      | mechanical residue + the caller-contract policy call                                   | foo-equals-foo ×3, single-call-site ×3, error-handling contracts |

53i is the one that needs a written policy rather than code motion. Its
inputs have accumulated across the arc: Phase 54's pinned
swallow-`ClientError` tests, 53c's unsuppressed `run_aws_json` and five
untagged inline suppressions, 53d-1's `capacity-report` exit-code
conflation, 53d-2a's `emergency.py` decline-vs-failure exit codes, and
53d-2b + 53e-1's two bare `except Exception` handlers that misattribute an
internal failure to an operator-facing cause. Each is pinned by a test
naming 53i, so the tests are the checklist of call sites to change.
