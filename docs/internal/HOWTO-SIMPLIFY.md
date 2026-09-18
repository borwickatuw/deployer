# How to Simplify the Deployer Codebase

Technical debt tracker for deployer: what is still big, why it is still big,
and what was already ruled out. For the methodology — thresholds, when to
extract, the quality safeguards — see the DOCS best-practice guide in
claude-meta (Practice 1). No counts live in this file; every one of them is
a command away and wrong the day after it is written.

## Listing the current candidates

Size candidates, thresholds per the DOCS guide's table:

```bash
# Python (scripts/modules; test files get the larger threshold)
git ls-files -- '*.py' | xargs wc -l | sort -rn | awk '$2 != "total" && $1 > 300'

# OpenTofu / Terraform
git ls-files -- '*.tf' | xargs wc -l | sort -rn | awk '$2 != "total" && $1 > 200'

# Documentation (guides; reference docs get the larger threshold)
git ls-files -- '*.md' | xargs wc -l | sort -rn | awk '$2 != "total" && $1 > 400'
```

Code-smell candidates come from pysmelly. Read
[PYSMELLY.md](PYSMELLY.md) first — it is the findings register and carries
every per-finding disposition — then:

```bash
uvx pysmelly . --more-please   # make pysmelly truncates to the top ten
```

## Context and decisions

### Large Python files

`bin/emergency.py` splits along mutating-ECS versus RDS if it is ever split;
that shape is a later subphase's to own, not a mechanical extraction.

`tests/unit/test_init_cli.py` and `tests/unit/test_extensions.py` are each one
subject, so they are listed by the command above rather than queued for a
split. `test_extensions.py` crossed threshold when the advice blocks were
pinned with exact-text assertions, which cost more lines than the
`pytest.raises`-only tests they joined.

`tests/unit/test_audit.py` **is** a split candidate, along
`test_audit_config.py` / `test_audit.py` lines, because it covers two subjects
— `deployer.config` parsing and `deployer.core.audit`. Phase 53e-2's rule was
that its own tests pass unchanged, so moving them stayed a separate operator
call and has not been made.

Decomposition trades body lines for helper signatures and docstrings, so the
`bin/` scripts and `core/audit.py` grew across Phase 53 even as their long
functions cleared. That is expected, not regression.

### Large Terraform files

`environments/deployer.tf` is symlinked shared environment config, not a file
this repo decomposes on its own.

### Large documents

`docs/CONFIG-REFERENCE.md` is a reference doc; large is likely appropriate for
it. `docs/internal/DECISIONS.md` is the one to watch — past the size in the
DOCS guide's decision-format table it becomes an `ADR/` directory rather than
a single file.

## Pysmelly arc

The per-finding work ran as **Phase 53** (subphases 53a–53i) in claude-meta's
plan tree, one finding-type × one subsystem per operator-gated session, with
duplicate-block extraction before long-function decomposition. **The arc has
no open subphase.** Live counts, the per-category live/settled/escalated
split, and every adjudication are in [PYSMELLY.md](PYSMELLY.md) — that file
owns them; do not restate them here.

Two lessons the arc paid for, which outlive the counts:

- **Re-measure at HEAD before scoping a subphase.** 53d was split twice
  because its plan entry had undercounted it; 53e was split up front, ordered
  by existing coverage descending, so the characterization-test idiom landed
  on small well-covered files before it reached the untested heart.
- **A suppression can be silently inert.** The 2026-08 S2 review found Phase
  42-2 suppressions that had never taken effect, because
  `# pysmelly: ignore` only counts on the finding line or one or two lines
  above. Four were relocated; one was a real bug and was fixed.

Some `# pysmelly: ignore` lines carry neither a rationale nor a
`re-evaluate-by` tag. They are listed and routed in
[PYSMELLY.md](PYSMELLY.md).

## Code improvements made (Phases 42 + 42-2)

- Removed unused `db_name` param from `setup_schema_privileges()`
- Extracted `format_iso()` datetime helper (hasattr patterns → isinstance)
- Converted loop-and-append accumulator to comprehension
- Fixed silent failure in `_handle_restore_error()` (unknown ClientErrors now re-raise)
- Removed vestigial `verbose` param from `audit()` command
- Fixed `audit_images()` type contract (dict[str, Any] → dict[str, ImageConfig])
- Converted `_format_service()` return type to `ServiceInfo` dataclass, removed vestigial `arn` field
- Flattened arrow-code in `detect_framework()`, `get_next_listener_priority()`,
  `cmd_start()` (extracted `_ensure_rds_available()`),
  `list_repositories_for_environment()`, `cmd_put()` (extracted
  `_get_secret_value_interactively()`) and `check_infrastructure_status()`
