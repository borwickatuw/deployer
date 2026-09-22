+++
title = "Behaviours the resolve-config and environment characterization tests pinned but did not fix"
+++

Captured 2026-09-22 from the characterization-test phase (deployer item 7,
sub-phases 7-2 and 7-3). The tests pin today's behaviour on purpose; these
are the surprises they recorded in their docstrings, none changed.

**bin/resolve-config.py**

- Log lines go to stdout ahead of the JSON, so `resolve-config.py ENV >
  file.json` writes invalid JSON. The `deploy.py env` subcommand (item 9)
  set the precedent: narration to stderr, document only on stdout.
- tofu runs twice per resolve.
- `--verify`: a JSON file with no `_meta` passes as fresh ("resolved at
  unknown"); the stale re-run hint is not an f-string and prints
  `{environment}` and `{file}` literally; a tofu failure during verify
  escapes as an uncaught RuntimeError traceback while resolve mode catches
  the same failure and exits cleanly.

**bin/environment.py**

- `stop`: an unreadable RDS status exits 1 only after every ECS service is
  already scaled to 0.
- `start`: every RDS failure except "status unreadable" (instance missing,
  start refused, either wait timing out) still scales ECS up; services
  missing from `services.config` come back at 1 replica.
- `status`: a cluster name that does not exist displays exactly like an
  empty cluster ("No ECS services found") — the same sentinel shape the
  6-1 sweep is about.

**Approach.** Each is a one-function fix under the Error Contracts decision
(DECISIONS.md 2026-08-18) with the pinned test updated to assert the new
outcome. The stdout one is the most user-visible; the f-string one is a
one-character fix.

**Complexity**: Low.
