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

deployer has no repo-local phases yet; the cross-repo backlog queued against
this repo is recorded below.

Cross-repo backlog queued against this repo: claude-meta `docs/plan/`
Phase 52 (container-level health checks for non-HTTP services). **Phase 53
(the pysmelly subphase arc, 53a–53p) closed 2026-08-25** — its record is in
claude-meta `docs/plan-archive/PLAN-ARCHIVE-2026-09.md`.

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
kept, with no current commitment — the state the repo's old monolithic
someday-maybe list under `docs/internal/` used to be, now one file per
idea. Items land here when something is worth remembering but nothing has
been promised: enhancement sketches, deferred Checkov findings,
alternatives considered.

Each item carries the shape the old list used: current state, the proposed
enhancement, an implementation approach, and a complexity estimate
(Low/Medium/High/Very High), plus any dependencies or prerequisites.

The state is unordered and unnumbered on purpose: nothing here has been
committed to. `capture` files one here; `promote` is the way out. An idea
that is rejected rather than deferred has no state — delete the file and
record the rejection with rationale in [DECISIONS.md](internal/DECISIONS.md).

## plan

Active work committed to, one item per phase. The state is `numbered`, with
[PLAN-ARCHIVE.md](plan-archive/PLAN-ARCHIVE.md) as its register: every item carries a
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
in the register, [PLAN-ARCHIVE.md](plan-archive/PLAN-ARCHIVE.md) — once that heading
exists (summarized from the item's body), the file here is freely prunable.

Everything deployer closed before adopting fileplan lives in the rotated
segment [PLAN-ARCHIVE-2026-09.md](plan-archive/PLAN-ARCHIVE-2026-09.md), which stays as
the historical record — nothing rewrites it. Those entries are
claude-meta-numbered (`## Phase 53:`, `## Phase 54:`) or unnumbered, which
is why they were rotated out whole rather than reheaded into this repo's
register.

## closed

The date the phase was closed out, `YYYY-MM-DD`. **Passed to `archive` as
`--closed`, not filled in by it** — a run without the flag archives the item
with no stamp and says nothing. `archive` is terminal, so there is no later
transition whose `requires` could catch the miss; the date that has to
survive is the one opening the register entry (see [archive](#archive)).

## source

The repo whose session captured the idea — provenance, not destination
(the fleet-standard key from claude-meta's capture point). An idea refiled
from claude-meta's `docs/ideas/` keeps this stamp.

## captured

The date the idea was captured, `YYYY-MM-DD`. Stamped at capture; kept on
refile.

## draft

The design document an item decides from: a repo-relative path into
`docs/drafts/`. Optional — an item whose body says everything needs none.

`docs/drafts/` holds future-state design only: plans and ideas for work
not yet built. A draft is **deleted when it is 100% implemented** — its
durable content refactored into the current-state docs
([DESIGN.md](internal/DESIGN.md), [ARCHITECTURE.md](internal/ARCHITECTURE.md),
the phase's archive record) first; nothing may reference a draft that
shipped. A draft abandoned rather than built moves to an `abandoned/` home
or is deleted with its rationale recorded in
[DECISIONS.md](internal/DECISIONS.md). fileplan does not validate the
path; keeping items and drafts pointing at each other is review work.

## capture

File an idea into someday-maybe. Write the idea shape into the body —
current state, proposed enhancement, implementation approach, complexity
(Low/Medium/High/Very High), any dependencies or prerequisites — and note
where the idea came from (a review session, an operator conversation, a
finding that was deliberately not scoped).

Ideas for deployer are captured cross-repo with `claude-idea deployer "…"`,
which files them in claude-meta's `docs/ideas/`; repo-local ideas are
written straight into `docs/someday-maybe/` by this transition.

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

## session-verbs

Four `plan → plan` verbs track the work *inside* a phase, so its status is
counted rather than narrated:

| Verb       | What a run does                                             |
| ---------- | ----------------------------------------------------------- |
| `start`    | Take this item's claim for the session                      |
| `subphase` | Write one sub-phase bullet into the body (`--title --last`) |
| `finish`   | Mark one sub-phase done (`--note`)                          |
| `drop`     | Mark one absorbed or abandoned (`--note`)                   |

Every row then carries `claimed-by`/`claim-status` and
`sub-phases`/`sub-phases-left`/`next-sub-phase`:

```bash
uv run fileplan list --state plan --lacks claimed-by        # unclaimed
uv run fileplan list --state plan --has 'sub-phases-left>0' # work left
uv run fileplan list --state plan --has 'stale-days>=30'    # gone cold
```

A held item **refuses every transition for another session** at the `--check`
stage. A claim is that session's hold and nothing frees it automatically:
`archive` frees it at close-out, and `uv run fileplan release ITEM` frees your
own or a dead local one — never a live claim and never another host's. A row
reading `claim-status` `dead`, `elsewhere` or `unknown` gets reported by name,
not cleared on sight.

Sub-phases are named `{number}-{ordinal}` (1-1, 1-2), against this repo's own
numbering — not the `53a`/`70c` lettering that appears in
[Cross-repo backlog](#cross-repo-backlog), which is claude-meta's and which
fileplan never reads.

Full rationale: claude-meta `docs/PLAN-METHOD.md` and
`best-practices/FILEPLAN.md` Practices 13-14.

## archive

Close a phase out. Before running it: write the outcome into the item's body
(what shipped, final counts, SHAs — the record register entries have always
carried). `archive` then mints a `## N. Title` heading at its ordered place
in the register ([PLAN-ARCHIVE.md](plan-archive/PLAN-ARCHIVE.md)) and moves
the file to `docs/plan-archive/items/`. Pass the date — `archive` **takes**
[closed](#closed) rather than supplying it, and a run without `--closed`
archives silently:

```bash
uv run fileplan archive ITEM --closed YYYY-MM-DD
```

After the move, condense the outcome into a short summary under the minted
heading with a pointer to where the full record lives; the
`docs/plan-archive/items/` file may then be deleted at any point.

## Rotating the register

PLAN-ARCHIVE.md stays bounded by rotation. When it grows long:

1. Cut the **oldest contiguous** `## N.` entries out into a dated segment,
   `docs/plan-archive/PLAN-ARCHIVE-<YYYY-MM>.md` (create it with an H1 and a one-line
   preamble; the entries move verbatim).
1. Raise `first-number` in `plan.toml` to the number of the first entry
   **kept**.
1. Run `uv run fileplan list` — it must come back gap-free. A missing
   number between the floor and the highest is refused loudly, per number.

**Constraint:** the floor must stay at or below the oldest number still
held by a live item or a remaining register heading — rotation trails the
slowest open arc. A floor above a live item's number is refused when the
next heading is minted.

**Prune rule:** files in `docs/plan-archive/items/` are deletable once their
register heading exists; the register (plus rotated segments) is the
durable record.
