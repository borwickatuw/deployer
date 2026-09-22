+++
title = "Seven failure-reporting gaps the sentinel sweep named but left"
+++

Captured 2026-09-22 from the sentinel-returned-from-except sweep (deployer
item 6, sub-phase 6-1), whose per-hit table in docs/internal/PYSMELLY.md
names these as deliberately left. None touches a ratified register row.

1. The emergency mutators return `False` without the AWS reason text.
2. `create_emergency_snapshot`: a `WaiterError` after a successful create
   escapes `emergency.py`'s boundary as a traceback.
3. `ci-deploy --strict` passes when `resolved_at` is missing or
   unparseable — a policy question for the operator.
4. `get_stored_migrations_hash` logs "No stored hash found" for an
   access-denied read; the outcome is still the safe one.
5. `_compute_context_hash` skips the contents of unreadable build-context
   files.
6. `capacity-report` prints a made-up 256/512 as "Current memory" when it
   cannot read the task definition.
7. `environment.py stop/start` exit 0 after a failed scale — an exit-code
   change the sweep would not make unasked.

**Approach.** 1, 2, 4, 5, 6 are one-function fixes under the Error Contracts
decision with a pinned test each; 3 and 7 need an operator answer first
(strict means strict? a failed scale is a failed command?).

**Complexity**: Low each.
