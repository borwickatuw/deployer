+++
title = "Docs and plan plumbing: example paths, the project memory, the mdformat pathspec, and the blocked-on key"
review = "2026-09-18"
blocked-on = ["fileplan-claude-meta:87", "fileplan-claude-meta:84", "fileplan-claude-meta:85"]
closed = "2026-09-22"
+++

Four DOCS/FILEPLAN carry-forwards from the 2026-09-18 comprehensive review.
Evidence for each is in claude-meta
`docs/investigations/2026-09-18-comprehensive-review-run-ledger.md`,
`## Unit records` → `### deployer` → `#### Pending operator`.

Three of the four are deployer's slice of a fleet family, cited as
`blocked-on` keys in the head now that sub-phase 3-4 has declared the key.
fileplan-claude-meta:89 is not cited: its deployer half *is* 3-4, so once
that lands deployer waits on nothing there — claude-meta's 89-2 waits on
deployer.

- blocked-on: fileplan-claude-meta:87 — finish DOCS Practice 12, migrated
  memories become pointers, across pysmelly, outscience, **deployer**,
  storage-scripts, sword-client, s3-archive, s3-slurm, havoc, o-snap and
  blocker.
- blocked-on: fileplan-claude-meta:84 — mdformat has front-matter support
  now, so retire the Makefile workarounds, across 18 Makefiles.
- blocked-on: fileplan-claude-meta:85 — revert the narrowed mdformat
  plan-archive pathspec, FILEPLAN P4, in havoc, dslab and **deployer**.
- blocked-on: fileplan-claude-meta:89 — declare the `blocked-on` key in the
  sibling repos, FILEPLAN P12, in s3-archive, **deployer** and every
  converted repo. This is the precondition for citing 77-88 by key; until it
  lands every item in this repo cites its families in prose, as these do.

Not filed here, deliberately: `docs/internal/SIMILAR-TOOLS.md` living outside
`docs/investigations/` was ratified as a leave-standing on 2026-09-21
(answer `c10`, `LsSmallA`) and is recorded as a row in
[docs/PYSMELLY.md](../PYSMELLY.md) under "Non-pysmelly verdicts ratified in
the same review", not as work.

### Sub-phases
- **3-1 — DOCS Practice 13 (carry-forward) — "Python docstrings and Click help text still carry ~/code/myapp example paths." Re-verified live at f33c84c: bin/deploy.py:17,130,182; bin/ecs-run.py:23; bin/link-environments.py:9,48; src/deployer/utils/links.py:9,12 (the line numbers drifted from the ledger's 135/194 as the run moved the tree). Three of them — deploy.py:130, deploy.py:182, link-environments.py:48 — are Click command docstrings, so they print in --help. The run's standing rules put Python docstrings out of scope, so it left all six files untouched rather than fix half the class. Fix in hand: sd '~/code/myapp/' '/path/to/myapp/' bin/deploy.py bin/ecs-run.py bin/link-environments.py src/deployer/utils/links.py, which brings the audit grep down to the two legitimate hits (the PYSMELLY.md quoted error message and tests/unit/test_core.py:483's tilde-expansion fixture). (Ledger: deployer → Pending operator, [carry-forward, DOCS].)** **done** sd '~/code/(myapp|otherapp)' '/path/to/$1' over the four files (the plan's pattern missed the no-slash and otherapp hits); audit grep down to the tilde fixture and the quoted message, which is in docs/internal/PYSMELLY.md:3303 not docs/PYSMELLY.md (ad7a573)
- **3-2 — DOCS Practice 12 (carry-forward; blocked-on fileplan-claude-meta:87) — "The Claude project memory for deployer holds doc-worthy architecture content and a pointer to a file deleted in the fileplan conversion; the memory file is outside the repo, so I could not touch it." Evidence at ~/.claude/projects/-Users-borwick-code-deployer/memory/MEMORY.md: (a) '## Future Work / Ideas' points at docs/internal/SOMEDAY-MAYBE.md, split into docs/someday-maybe/ on 2026-09-14 — the item is now docs/someday-maybe/tofu-placeholder-map-indexing.md; (b) an '## Architecture' block (templates/, the bootstrap module moved from deployer-environments, init.py --template, substitute/substitute_optional, replace_hcl_block); (c) '## Planned Work' and '## Recent Major Work', which are plan/archive material. Verified against the repo: templates location is in docs/internal/DESIGN.md:273, --template is in four docs, replace_hcl_block is in no doc at all. Recommendation: repoint the stale line, move the Planned Work bootstrap item into docs/someday-maybe/ as a fileplan item, reduce Architecture and Recent Major Work to pointers; replace_hcl_block's brace-counting is implementation detail and belongs in a docstring, not docs/. (Ledger: deployer → Pending operator, [carry-forward, DOCS].)** **done** MEMORY.md reduced to two pointers (DESIGN/ARCHITECTURE, fileplan state dirs); stale SOMEDAY-MAYBE.md and project_bootstrap_init.md pointers removed; the Planned Work bootstrap item had already shipped as init.py bootstrap so nothing to file; replace_hcl_block no longer exists
- **3-3 — MAKEFILE / FILEPLAN P4 (blocked-on fileplan-claude-meta:84 and :85) — the format-docs pathspec. Makefile:295 and :300 exclude ':!docs/someday-maybe :!docs/plan :!docs/plan-archive/items :!.claude/skills', with a comment explaining that mdformat escapes Markdown punctuation inside an item's +++ TOML head and rewrites a SKILL.md's YAML frontmatter. Family :84 says mdformat has front-matter support now, so the workarounds retire; family :85 says revert the narrowed plan-archive pathspec (deployer narrowed it to docs/plan-archive/items so the registers stay formatted, which FILEPLAN P4 wants back at docs/plan-archive). Both land in the same two lines, so do them together and re-run make format-docs-check. Precondition: answer d3 has the operator installing mdformat with --with mdformat-frontmatter; do not retire a workaround against an mdformat that still lacks the support. (Ledger: claude-meta's family list; d3 DevEnv names the six Makefile workarounds as carry-forward items.)** **done** landed by the fleet sweep: 7d3c042 reverts the pathspec to ':!docs/plan-archive' (fileplan-claude-meta:85-3, narrowing dated to e35a6fa during the review) and 0357299 retires the .claude/skills exclusion and names --with mdformat-frontmatter (84-3/84-5); host mdformat carries mdformat-frontmatter 2.1.2
- **3-4 — FILEPLAN Practice 12 (carry-forward; blocked-on fileplan-claude-meta:89) — "Practice 12's declared blocked-on key and the reciprocal #scope/#archive hooks cannot be added from deployer alone — they must be declared identically in claude-meta's plan.toml and named in both method docs." Evidence: deployer's plan.toml declares closed/source/captured/draft and no dependency key; claude-meta queues arcs against deployer (fileplan-claude-meta:52 live, :70 in progress) and until this review docs/plan/ was empty, so the dependency was one-directional and literally inexpressible. Re-verification found this half-closed on the claude-meta side only, so deployer's half is live. The run did the greppability half (the fileplan- scheme prefix, 7 citations) and stopped at the declaration. Recommendation, verbatim: declare [keys.blocked-on] in both plan.toml files with the same doc/help/list-valued shape, add the #scope and #archive hooks to both method docs, and leave it plain — never wired to a state's dependencies field, which would report every cross-repo citation as an unknown dependency forever. Once it lands, the prose citations in items 2, 3 and 6 of this review's filing become keys. (Ledger: deployer → Pending operator, [carry-forward, FILEPLAN].)** **done** declared verbatim from claude-meta, hooks added to PLAN-METHOD scope/promote/archive, items 2 and 3 now cite by key (9101134)

### Outcome

Closed 2026-09-22. 3-4 first (`9101134`): the `blocked-on` key declared
verbatim from claude-meta, the `#blocked-on` heading and the
scope/promote/archive hooks in PLAN-METHOD.md, items 2 and 3 citing by key
— deployer's half of fileplan-claude-meta:89. 3-2 (`be1443d`): the project
memory reduced to two pointers, its stale SOMEDAY-MAYBE.md and planned-work
pointers gone. 3-1 (`ad7a573`): `~/code/(myapp|otherapp)` → `/path/to/...`
in four files; the audit grep is down to the tilde fixture and one quoted
message. 3-3 landed through the concurrent fleet sweep (`7d3c042`,
`0357299`): pathspec back to `':!docs/plan-archive'`, `.claude/skills`
exclusion retired, install hint names `--with mdformat-frontmatter`.
