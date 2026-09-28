+++
title = "A self-expiring hold on the staging scheduler's stop"
+++

**Source.** Operator conversation, 2026-09-28: a GPU queue of several
hours' work needed to run past the evening stop. The only lever was
`aws events disable-rule --name myapp-staging-stop` by hand, to be
re-enabled the next morning.

**Current state.** The staging scheduler is two EventBridge rules (start,
stop) driving the scheduler Lambda. There is no way to skip one night.
Disabling the stop rule works but is a hazard twice over: nothing
re-enables it, so a forgotten disable bills every night, and it is drift
from tofu state, so the next `tofu apply` of the environment quietly
re-enables it mid-run.

**Proposed enhancement.** A self-expiring hold. The stop path in the
scheduler Lambda reads an SSM parameter, `/deployer/<env>/scheduler-hold-until`
(`YYYY-MM-DD`); while today, in the schedule's own time zone, is on or before
that date it skips the stop and logs that it did, naming the date. A
date in the past is ignored, so the hold ends by itself and nothing has
to be undone. The start path ignores it.

- `bin/environment.py hold <env> --until YYYY-MM-DD` writes the parameter,
  refusing a date more than a few days out; `hold <env> --clear` deletes
  it; `status` prints any active hold.
- The Lambda role gains `ssm:GetParameter` on that one parameter path.
- A missing parameter means no hold. A parameter that does not parse
  fails the stop run loudly rather than skipping or stopping silently.

**Complexity.** Low: one Lambda branch, one IAM statement, one CLI verb,
tests for the date edge (the stop runs after midnight UTC for an evening
local stop, so "tonight" must be computed in the schedule's time zone).
An environment apply is needed to ship it.

**Dependencies.** None.
