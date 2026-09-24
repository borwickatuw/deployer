+++
title = "Two gaps from the staging reset: the ungated manage passthrough, and one SSM path for the Cognito test password"
+++

Two findings from the 2026-09-24 havoc staging reset (o-snap
`docs/investigations/2026-09-24-staging-reset-and-reload.md`).

**The generic `manage` passthrough is not DDL-gated.** havoc's
`deploy.toml` declares `manage = ["python", "manage.py"]` in the
bare-array form, which carries no `ddl` flag, so
`ecs-run.py run havoc-staging manage flush -- --noinput` (the most
destructive thing on the box) runs with none of the confirmation that
`[commands.migrate]` gets. CONFIG-REFERENCE.md tells authors to flag
each command individually and never mentions the passthrough gap.
Options: a `ddl_subcommands` list on a passthrough command (`flush`,
`sqlflush`, `migrate`, `dbshell`) that the runner gates by the first
forwarded argument; or a documented rule that a passthrough command
must not exist where a DDL-gated alternative is needed, enforced by the
deploy.toml audit.

**The staging Cognito test password is documented at two SSM paths.**
`docs/operations/STAGING.md`, `docs/CONFIG-REFERENCE.md`, both templates
and `modules/bootstrap/iam-*.tf` all say
`/deployer/<environment>/cognito-test-password`; the deployed value in
`deployer-environments/havoc-staging/config.toml` and that repo's
`docs/SECURITY.md` is `/deployer/staging-shared/cognito-test-password`,
and havoc's own OPERATIONS.md cites the per-environment path. One of
them is wrong for whoever follows it at 2 a.m. Decide which is
canonical (shared across staging tiers, or per environment), make the
IAM policy and the docs agree, and have `ssm-secrets.py check` prove
the path exists.

**Trigger.** The next deployer release, or the next time someone runs
`manage flush` by hand.
