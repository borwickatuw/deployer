+++
title = "Characterization tests for the four largely unpinned bin/ CLIs"
review = "2026-09-18"
number = 7
+++

One PYTHON-TESTING carry-forward from the 2026-09-18 comprehensive review.
Evidence is in claude-meta
`docs/investigations/2026-09-18-comprehensive-review-run-ledger.md`,
`## Unit records` → `### deployer` → `#### Pending operator`,
`[carry-forward, PYTHON-TESTING]`:

> "Four `bin/` CLIs remain largely unpinned; the trimmed pyproject comment
> names only `bin/ops.py` as the largest gap." Measured at `c4d9648`:
> `bin/ops.py` 610 stmts 54%, `bin/environment.py` 130 stmts 22%,
> `bin/link-environments.py` 72 stmts 17%, `bin/resolve-config.py` 111 stmts
> 34%. "These four hold most of the repo's 1227 missed statements."

The lead was explicit that this is **not** a guide violation today — the
integration-heavy rationale is documented in `pyproject.toml` and the
measured floor is enforced (`fail_under` was raised 74 → 81 during the run,
`2c8d45e`, and now measures `bin/` and both Lambda bundles on every
invocation). So this is deliberate slack being paid down, not a red gate.

Method, from the lead: "a future session pins these the way phases 53d/53f
pinned the others (characterization tests on today's behaviour first, then
any refactor)". Every count above is pinned to `c4d9648` — re-measure with
`env -u VIRTUAL_ENV make test-cov` before taking a sub-phase, and raise
`fail_under` as each one lands rather than at the end.

Per-repo item; no fleet family.

### Sub-phases
- **7-1 — bin/ops.py — the largest gap, and the only one pyproject.toml's rationale comment names: 610 statements at 54% (c4d9648). Characterize today's behaviour first. Note its cmd_health/cmd_logs/cmd_maintenance/cmd_ecr are four of the six Click entry points behind the ratified internal-only skip (docs/PYSMELLY.md, A06) — tests here must not become an argument for renaming them private, which A06 declined.** **done** bin/ops.py 54% -> 99% via 84 new tests (botocore Stubber for the boto3 readers, CliRunner for every command); total 86.86%, fail_under 81 -> 86; only the two never-passed parameter branches left (808fea7, 8ed03f6)
- **7-2 — bin/environment.py — 130 statements at 22% (c4d9648). Its 'available' literal at :169 is one anchor of the ratified scattered-constants set (docs/PYSMELLY.md, A07); leave it alone while pinning.**
- **7-3 — bin/resolve-config.py — 111 statements at 34% (c4d9648). Its build_meta carries a standing dict-as-dataclass ignore (the return is serialized to JSON for the resolver's consumers) — see the standing-suppressions table in docs/PYSMELLY.md.**
- **7-4 — bin/link-environments.py — 72 statements at 17% (c4d9648), the smallest of the four. Sequence it with sub-phase 3-1 of the docs item: two of its ~/code/myapp example paths (:9 and the :48 Click docstring) are edited there, and a characterization test that asserts --help output would otherwise pin the wrong string.** **done** 21 tests, bin/link-environments.py 17% -> 97%, total 82.29 -> 83.59, fail_under 81 -> 83; only the unlink race branch left (d4743cb)
