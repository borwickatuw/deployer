+++
title = "Security gates and static-analysis config: the bandit skips, the reporter's name, and what check gates on"
review = "2026-09-18"
blocked-on = ["fileplan-claude-meta:77", "fileplan-claude-meta:78"]
number = 2
+++

Three SECURITY/PYTHON configuration decisions the 2026-09-18 comprehensive
review escalated rather than took, because this run's standing rules forbid
adding skip entries, adding `# nosec`, and changing what `make check` gates
on. Evidence for each is in claude-meta
`docs/investigations/2026-09-18-comprehensive-review-run-ledger.md`,
`## Unit records` → `### deployer` → `#### Pending operator`.

Two of the three are deployer's slice of a fleet family and must not land
before the family decides its shape:

- blocked-on: fileplan-claude-meta:77 — bandit skips aligned with ruff
  per-file-ignores (answer `a2`), across pysmelly, outscience, **deployer**,
  storage-scripts, sword-client, s3-archive, porter, claude-utils, dslab,
  o-snap and blocker. Eleven repos get the same edit; deployer's is the one
  below.
- blocked-on: fileplan-claude-meta:78 — rename the report-only target to
  `security-report` (answer `a3`), in every Makefile carrying the reporter.
  deployer carries both targets today, adjacent in `make help`, plus the
  hazard comment `6032603` added at the point of use.

Both are cited as `blocked-on` keys in the head (the key was declared by
sub-phase 3-4 of the docs-and-plan item, deployer's half of
fileplan-claude-meta:89). Both families have decided their shape (answers
`a2` and `a3`, guides amended), so deployer's slices below are landable;
the citation records which fleet sub-phase each one closes (77-3, 78-3/78-4).

### Sub-phases
- **2-1 — SECURITY Practice #4b/#4c (suppression-proposed; blocked-on fileplan-claude-meta:77) — "bandit skips are not aligned with ruff's per-file-ignores (Practice #4b), and two noqa'd lines lack the paired bare # nosec (Practice #4c)." Evidence: bandit over bin src modules/lambda-shared modules/staging-scheduler/lambda reports B404 LOW x8, B603 LOW x14, B607 LOW x8, B110 LOW x2, 0 Medium, 0 High, while [tool.ruff.lint.per-file-ignores] already ignores S603/S607 for the bin and src globs and [tool.bandit] skips is only ['B101','B608']. Fix in hand: skips = ['B101','B608','B404','B603','B607'], plus a bare # nosec at the end of src/deployer/init/deploy_toml.py:146 and src/deployer/init/environment.py:58. The lead's own caveat: with -ll the Lows never print, so the payoff is the honest metrics line, not saved triage time. (Ledger: deployer → Pending operator, [suppression-proposed, SECURITY].)**
- **2-2 — PYTHON §15 / SECURITY §6b (adjudication; blocked-on fileplan-claude-meta:78) — "Two guides name two different jobs one character apart: PYTHON.md §15 make security-update REWRITES uv.lock, SECURITY.md §6b make security-updates only reports." deployer's Makefile:233 and :246 carry both, adjacent in make help, with the hazard documented at the point of use (6032603). Make does no fuzzy matching, so dropping the trailing s silently runs the mutating one. Answer a3 settles the fleet shape: the reporter becomes security-report and security-update stays the rewriter. deployer renames its reporter and greps for references. (Ledger: deployer → Pending operator, [adjudication, PYTHON].)**
- **2-3 — SECURITY / MAKEFILE (held-back behaviour-change, operator's choice still open) — "make check excludes the security family, so security-secrets drift stays invisible until someone runs it by hand." Evidence: Makefile:126 is 'check: lint test format-docs-check'; drift accumulated across six commits (67874d8..ad50a02) and was caught only by a verifier running make security explicitly, which was EXIT=2 at ad50a02. bump-version and storage-scripts fold security into check. The three drafted options, verbatim: (a) add security-secrets alone to check — catches exactly this drift class at zero network cost, but makes check tree-mutating on drift; (b) add the whole security target, at the cost of network and checkov runtime every time; (c) leave as-is and treat 'run make security before the last commit of a session' as the discipline. The lead recommends (a) only if tree-mutation is acceptable, otherwise (c). Note deployer is NOT a member of answer a7 (NeverRun, fileplan-claude-meta:80) — that family named pysmelly, porter, o-snap, dslab and outscience — so nothing has decided this one yet. (Ledger: deployer → Pending operator, [behaviour-change, SECURITY, fixup]; see also its Plan corrections, 'Neither make security nor make vulture is in make check'.)**
