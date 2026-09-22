+++
title = "Developer Experience: Tofu Placeholder Map Indexing"
number = 8
+++

Promoted 2026-09-22 from someday-maybe (Developer Experience). Repo-local
phase; no fleet family, nothing blocked-on.

**Current state.** `src/deployer/core/config.py` resolves `${tofu:NAME}` by
`tofu_outputs.get(NAME)` — a whole-string placeholder returns the output's
value with its type preserved (dict and list included), an embedded one is
stringified (dicts and lists via `json.dumps`). There is no way to reach
*into* a map output, so a project with per-bucket S3 outputs needs a
hand-added `output` block per bucket in its root module, which is exactly
the per-environment edit the standardized template exists to remove.

**Scope.** Extend the placeholder grammar to `${tofu:NAME.KEY}` (and
`.KEY.KEY` for nested maps): resolve `NAME` as today, then walk the
dotted path through dict outputs. Fail fast, in the style of the existing
`RuntimeError`, when the base output is missing, when a segment is applied
to a non-dict, or when the key is absent — the error names the full
placeholder and the segment that failed. Whole-string and embedded forms
behave as they do today at the leaf (type-preserving vs stringified).
Output names containing a literal `.` are not a case this repo has; the
first segment is the output name and the grammar says so.

**Approach.** `TOFU_PLACEHOLDER_PATTERN` stays the outer match; a small
`_walk_tofu_output(name, tofu_outputs, env_path)` helper does the split and
walk and is shared by both branches of `_resolve_tofu_placeholders`.
Characterization tests for today's behaviour first (tests/unit, the
config module's existing suite), then the extension with its error cases.
Docs: `docs/CONFIG-REFERENCE.md`'s placeholder section gains the dotted
form and one example; the template `config.toml.example` files need no
change.

**Complexity**: Low-Medium.

### Sub-phases
- **8-1 — characterization tests for the resolver's whole-string and embedded forms as they behave today (type preservation, json.dumps, the two RuntimeErrors)**
- **8-2 — the dotted-path walk: _walk_tofu_output shared by both branches, fail-fast errors naming placeholder and segment, tests for each error arm**
- **8-3 — CONFIG-REFERENCE.md placeholder section documents the dotted form with one map-output example**
