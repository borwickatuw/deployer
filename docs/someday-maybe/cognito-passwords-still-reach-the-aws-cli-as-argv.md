+++
title = "Cognito passwords still reach the aws CLI as argv"
+++

Captured 2026-09-22 from the --password-stdin change (deployer item 1,
sub-phase 1-3), which took the password off cognito.py's own argv and out of
shell history but not off the child process.

**Current state.** `src/deployer/aws/cognito.py` passes the password to the
`aws` CLI as `--password` (`set_user_password`) and `--temporary-password`
(`create_user`). Any local user can read it from `ps` while that subprocess
runs, generated passwords included. GOVERNANCE.md R4 records this residual.
The STAGING.md test-account recipe has the same shape with
`aws ssm put-parameter --value "$PASSWORD"`.

**Proposed enhancement.** Feed the sensitive parameters to `aws` through
`--cli-input-json` read from stdin (or a mode-0600 temp file), so the
password never appears in the child's argv. Apply the same to the SSM
recipe.

**Implementation approach.** One helper in `src/deployer/aws/` that runs an
aws subcommand with a JSON input document on stdin; `create_user` and
`set_user_password` switch to it; characterization tests pin the exact
argv and the stdin document. The change touches the `aws/` package, whose
error-contract and pysmelly verdicts are ratified (docs/PYSMELLY.md), so
read those rows first.

**Complexity**: Low-Medium.
