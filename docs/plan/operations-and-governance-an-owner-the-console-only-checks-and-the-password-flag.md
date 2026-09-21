+++
title = "Operations and governance: an owner, the console-only checks, and the password flag"
review = "2026-09-18"
number = 1
+++

Four OPERATIONS/GOVERNANCE escalations the 2026-09-18 comprehensive review
raised and could not settle unattended. Evidence for each is in claude-meta
`docs/investigations/2026-09-18-comprehensive-review-run-ledger.md`,
`## Unit records` → `### deployer` → `#### Pending operator`.

Two of the four were decided by the operator on 2026-09-21 in answer `b17`
(`GovRows`, "one address, ratify rows"): use `borwick@uw.edu` as the
escalation contact in deployer, s3-archive, s3-bagit and blocker, and correct
the `ecs-run.py shell` line. The other two are still open questions that this
item carries rather than loses: the AWS-side runnable checks need credentials
and an attended session, and the `cognito.py --password` remedy is a CLI
contract change the operator picks between.

This is a per-repo item; nothing here is blocked on a fleet family.

### Sub-phases
- **1-1 — OPERATIONS Practice #3/#4a — "No ownership or escalation contact documented anywhere in the repo": grep for 'escalation|@uw.edu|primary owner' over docs/, README.md and CLAUDE.md returns nothing, and the run could not write one without fabricating a contact. Answer b17 decides it: borwick@uw.edu, most naturally a short Ownership block at the top of docs/operations/README.md. (Ledger: deployer → Pending operator, [adjudication, OPERATIONS].)**
- **1-2 — GOVERNANCE — docs/GOVERNANCE.md:26 names `bin/ecs-run.py shell`, which does not exist. Answer b17: correct it to the real subcommands, list/run/exec. (Ledger: deployer → Pending operator, the OPERATIONS ownership bullet; the register note is quoted in the b17 question.)**
- **1-3 — GOVERNANCE risk R4 (held-back behaviour-change) — "bin/cognito.py -p/--password accepts a password as a command-line argument, so it is written to shell history and exposed in ps output while the command runs" (bin/cognito.py:396 and :437; cmd_reset_password at :346). The run drafted two remedies and applied neither because both change the CLI contract for anyone scripting cognito.py create|reset-password: (a) prompt=True, hide_input=True guarded so the generate-a-temp-password default still works when the flag is absent, or (b) a --password-stdin flag reading one line from stdin, mirroring docker login. Operator picks. (Ledger: deployer → Pending operator, [behaviour-change, GOVERNANCE].)**
- **1-4 — OPERATIONS carry-forward — "The guide's AWS-side runnable checks were not executed — no credentials and no environment of deployer's own." All four (aws sns list-subscriptions-by-topic, aws cloudwatch describe-alarms, curl of the health endpoint, aws elbv2 describe-target-health) need live credentials and a named deployed environment; deployer deploys none of its own. Either run them against a real <app>-production environment in an attended session, or decide they belong to the app repos' checkpoints rather than deployer's. PRODUCTION.md's new 'Alarms and Notifications' section (5117cd5) already carries the two alarm-side commands. (Ledger: deployer → Pending operator, [carry-forward, OPERATIONS].)**
