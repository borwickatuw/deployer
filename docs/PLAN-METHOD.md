# The plan method

What every word in this repo's `plan.toml` means. `plan.toml` (repo root)
declares the states, keys and transitions; `fileplan` reads it and builds the
commands. This document is where each declared name gets its meaning — every
`doc =` pointer in `plan.toml` resolves to a heading here, and fileplan
checks the pointers on every run.

Run `uv run fileplan` to see the workflow, `uv run fileplan list` to see what
is filed. Items are Markdown files with a `+++`-fenced TOML head; the state
is the directory the file sits in.

The three state directories are excluded from `make format-docs` /
`format-docs-check`: mdformat escapes Markdown punctuation inside the TOML
head (a trailing `_` becomes `\_`), which corrupts the head. Item bodies are
working notes and go unformatted by design.

## Cross-repo backlog

_No repo-local phases yet._ This file exists so cross-repo backlog has a
visible home in-repo (fleet convention).

Cross-repo backlog queued against this repo: claude-meta `docs/plan/`
Phase 52 (container-level health checks for non-HTTP services). **Phase 53
(the pysmelly subphase arc, 53a–53p) closed 2026-08-25** — its record is in
claude-meta `docs/PLAN-ARCHIVE-2026-09.md`.

Cross-repo arc in progress: claude-meta `docs/plan/` **Phase 70**
(queue-depth autoscaling). deployer's pieces — the `scaling` tfvars schema,
`deploy/autoscaling.py` apply step, IAM grants, and the `min_replicas`
floor — landed 2026-08-31; the environment applies and staging verification
(70c/70e) are pending. The boundary rule it settled is recorded in
[docs/internal/DESIGN.md](internal/DESIGN.md).

Per-finding dispositions for the Phase 53 arc are in
[docs/internal/PYSMELLY.md](internal/PYSMELLY.md); size and simplification
candidates in [docs/internal/HOWTO-SIMPLIFY.md](internal/HOWTO-SIMPLIFY.md).

**Two numbering vocabularies meet here, and they are not the same.** The
phase numbers in the pointers above are **claude-meta's** — they are minted
by claude-meta's register and cited fleet-wide. The `number` key on an item
in `docs/plan/` is **deployer's own**, minted by this repo's register
starting at 1. A cross-repo arc keeps its claude-meta number in the pointer
prose; it does not get a deployer number, and a deployer number never
renames a claude-meta phase.

## someday-maybe

Deep backlog: ideas for future improvements that aren't urgent. An idea
kept, with no current commitment — the state this repo's
`docs/internal/SOMEDAY-MAYBE.md` used to be, one file per idea. Items land
here when something is worth remembering but nothing has been promised:
enhancement sketches, deferred Checkov findings, alternatives considered.

Each item carries the shape the old list used: current state, the proposed
enhancement, an implementation approach, and a complexity estimate
(Low/Medium/High/Very High), plus any dependencies or prerequisites.

The state is unordered and unnumbered on purpose: nothing here has been
committed to. `capture` files one here; `promote` is the way out. An idea
that is rejected rather than deferred has no state — delete the file and
record the rejection with rationale in [DECISIONS.md](internal/DECISIONS.md).

## plan

Active work committed to, one item per phase. The state is `numbered`, with
[PLAN-ARCHIVE.md](PLAN-ARCHIVE.md) as its register: every item carries a
`number` key in its head, minted automatically at entry as one more than the
highest number anywhere — live items, register headings, or the register
floor (`first-number`, which stands in for everything rotated out).
Numbers are never reused or renumbered.

deployer's own numbering starts at 1: the repo has never had a repo-local
phase, and every phase it has worked was queued and numbered by claude-meta
(see [Cross-repo backlog](#cross-repo-backlog)). A repo-local phase is work
this repo decides to do on its own account — a deployer feature, a
refactor, a docs arc — as opposed to deployer's slice of a fleet arc, which
stays under claude-meta's number.

The item's body is the whole record: scope, subphases, sequencing, operator
decisions, status, and finally the outcome. Status is never recorded in a
shared header or a cross-item table — the file is the unit.

## plan-archive

Recent full records of closed phases, one file each, moved here whole by
`archive`. The body keeps the full working record; the `closed` key says
when. The durable record is the `## N. Title` heading that `archive` mints
in the register, [PLAN-ARCHIVE.md](PLAN-ARCHIVE.md) — once that heading
exists (summarized from the item's body), the file here is freely prunable.

Everything deployer closed before adopting fileplan lives in the rotated
segment [PLAN-ARCHIVE-2026-09.md](PLAN-ARCHIVE-2026-09.md), which stays as
the historical record — nothing rewrites it. Those entries are
claude-meta-numbered (`## Phase 53:`, `## Phase 54:`) or unnumbered, which
is why they were rotated out whole rather than reheaded into this repo's
register.

## closed

The date the phase was closed out, `YYYY-MM-DD`. Stamped by `archive`.

## capture

File an idea into someday-maybe. Write the idea shape into the body —
current state, proposed enhancement, implementation approach, complexity —
and note where the idea came from (a review session, an operator
conversation, a finding that was deliberately not scoped).

## promote

Commit to a someday-maybe idea: move it into the plan. fileplan mints the
next phase number into the item's head. Rewrite the body from idea-shape
into work-shape: scope and approach, not just motivation.

## scope

Open a phase directly in the plan, without passing through someday-maybe;
fileplan mints the next phase number. This is the path for review findings,
carry-forward work items and deployer's slice of a cross-repo arc that the
repo decides to track on its own number — commitments the moment they are
queued.

## archive

Close a phase out. Before running it: write the outcome into the item's body
(what shipped, final counts, SHAs — the record register entries have always
carried). `archive` then mints a `## N. Title` heading at its ordered place
in the register ([PLAN-ARCHIVE.md](PLAN-ARCHIVE.md)), stamps
[closed](#closed), and moves the file to `docs/plan-archive/`. After the
move, condense the outcome into a short summary under the minted heading
with a pointer to where the full record lives; the `docs/plan-archive/`
file may then be deleted at any point.

## Rotating the register

PLAN-ARCHIVE.md stays bounded by rotation. When it grows long:

1. Cut the **oldest contiguous** `## N.` entries out into a dated segment,
   `docs/PLAN-ARCHIVE-<YYYY-MM>.md` (create it with an H1 and a one-line
   preamble; the entries move verbatim).
1. Raise `first-number` in `plan.toml` to the number of the first entry
   **kept**.
1. Run `uv run fileplan list` — it must come back gap-free. A missing
   number between the floor and the highest is refused loudly, per number.

**Constraint:** the floor must stay at or below the oldest number still
held by a live item or a remaining register heading — rotation trails the
slowest open arc. A floor above a live item's number is refused when the
next heading is minted.

**Prune rule:** files in `docs/plan-archive/` are deletable once their
register heading exists; the register (plus rotated segments) is the
durable record.
