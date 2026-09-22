# Governance Posture

Deployer is operated by one person and holds no application data of its own.
This file is the index for the four governance dimensions — access control,
risk, business continuity, privacy — recording *where* each answer lives rather
than restating it. Where the answer is a decision, it points at
[DECISIONS.md](internal/DECISIONS.md); where it is a procedure, it points at
[operations/](operations/).

Fleet-wide inventories — who holds which AWS account, which repos exist, the
cross-repo risk list — are maintained centrally in the claude-meta repo
(`docs/ACCESS-CONTROL-REGISTER.md`, `docs/RISK-REGISTER.md`) and are
authoritative for anything this file marks "central register."

Reviewed at each comprehensive-review closeout, not on a calendar date.

## 1. Access control

| Question                     | Answer                                                                                                                                                                                     |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Who operates this            | One operator. Onboarding and offboarding are structurally inapplicable; the central register carries the identity.                                                                         |
| Human AWS access             | Named CLI profiles that assume purpose-specific roles — see [MULTIPLE-ACCOUNTS.md](operations/MULTIPLE-ACCOUNTS.md). Three roles, one per workflow: DECISIONS 2026-01-23.                  |
| Long-lived AWS keys          | None in the deploy path. CI authenticates through GitHub OIDC (`modules/ci/`, `modules/ci-role/`); ECS tasks use the task role. The bootstrap IAM user is the account-level exception.     |
| Privilege scoping            | Role policies are ARN-scoped to the prefixes in bootstrap's `project_prefixes`, under the permission boundary in `modules/bootstrap/iam-boundary.tf`.                                      |
| Database access              | Two accounts per database — a migrate user with DDL, an app user without: DECISIONS 2026-02-05 and 2026-01-30.                                                                             |
| Container shell access       | `bin/ecs-run.py shell`, through the same assumed role as a deploy.                                                                                                                         |
| Secrets access               | Values live in SSM Parameter Store / Secrets Manager and are referenced, never copied, by `deploy.toml`: DECISIONS 2026-01-21. `bin/ssm-secrets.py` is the read/write path.                |
| Repository access            | Two remotes: a private working repo and a public release repo. [HOWTO-PUBLISH.md](internal/HOWTO-PUBLISH.md) is the pre-publish review that keeps internal identifiers off the public one. |
| Branch protection            | Not enabled on the public release repo; unavailable on the private working repo under its GitHub plan. Tracked below as R3.                                                                |
| MFA on the bootstrap account | Not expressible in this repo's OpenTofu — it is an account-level control. Central register.                                                                                                |

## 2. Risk awareness

Load-bearing risk decisions live in [DECISIONS.md](internal/DECISIONS.md) and
are not duplicated here; this section indexes them and adds the living risks
that are not yet decisions. Every risk's owner is the operator.

| Decision                                    | Risk it settles                                                                   |
| ------------------------------------------- | --------------------------------------------------------------------------------- |
| 2026-01-21 Secrets via SSM references       | Secret values never enter `deploy.toml`, deploy logs, or CI output                |
| 2026-01-23 Least-privilege IAM roles        | A compromised deploy credential cannot administer the account                     |
| 2026-01-30 IAM policies in OpenTofu         | Policy drift between what is documented and what is applied                       |
| 2026-02-05 Two-account database model       | A runtime compromise cannot alter schema                                          |
| 2026-08-21 Deploy log prints every variable | Accepted: sensitive-but-not-secret values (endpoints, bucket names) print in full |

| Living risk                                                                                                                  | Likelihood | Impact | Decision                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **R1 Single operator.** Nobody else has run a production deploy.                                                             | High       | High   | Mitigate: the operations guides are written to be followed by someone who did not write them. Central register R1.                                                                                                                                                                                                                                       |
| **R2 A secret reaches the public remote.** The repo is published; its history is public forever.                             | Low        | High   | Mitigate: `make security-secrets` against `.secrets.baseline`, plus the HOWTO-PUBLISH checklist before every public push.                                                                                                                                                                                                                                |
| **R3 No branch protection on either default branch.** A bad local commit reaches a remote unreviewed.                        | Medium     | Low    | Accept: one operator, every push preceded by `make check`, and the private repo plan does not offer the feature. Revisit if a second committer appears.                                                                                                                                                                                                  |
| **R4 A Cognito password in argv.** `bin/cognito.py -p/--password` put a chosen password in shell history and in `ps` output. | Medium     | Medium | Mitigate: `-p/--password` is removed; `create` and `reset-password` take `--password-stdin` (one line, as `docker login` does), and the generated-password default is unchanged. Residual: `aws/cognito.py` still hands the password to the `aws` CLI as an argument, so it is visible to `ps` while that subprocess runs, generated passwords included. |
| **R5 `local/` accumulates indefinitely.** Emergency checkpoints and incident notes are written there with no retention rule. | High       | Low    | Accept: the directory is gitignored, machine-local, and holds ARNs and service state rather than credentials.                                                                                                                                                                                                                                            |

## 3. Business continuity

| Question               | Answer                                                                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Recovery targets       | [PRODUCTION.md](operations/PRODUCTION.md) §Recovery Targets, with §What Recovery Does Not Cover stating the limits explicitly.         |
| Recovery procedures    | PRODUCTION.md §Emergency Procedures — rollback, database recovery, scale up, force new deployment.                                     |
| Recovery testing       | PRODUCTION.md §RDS Backup Testing; the cadence that triggers it is in §Maintenance Cadences.                                           |
| Incident handling      | PRODUCTION.md §Incident Response, with severity definitions. `bin/ops.py incident` keeps the timeline in `local/incidents/`.           |
| Alerting               | The cloudwatch-alarms module and its SNS topic — PRODUCTION.md §Alarms and Notifications, including the unconfirmed-subscription trap. |
| Communication plan     | One operator: alarms reach the address subscribed to the SNS topic. There is no external-communication path to authorize.              |
| AWS unavailable        | Single-region by construction. A region outage is an outage; see the central register's region-outage risk.                            |
| GitHub unavailable     | Blocks CI deploys, not local ones: `bin/deploy.py` runs against AWS directly and does not need GitHub.                                 |
| Provider registry down | `.terraform.lock.hcl` pins provider versions and hashes; a cached `.terraform/` keeps working.                                         |

## 4. Privacy and data protection

Deployer moves other applications' data around but stores none of it. The
inventory is short.

| Data                                  | Where                                                                                       | Sensitivity                 | Retention                                         |
| ------------------------------------- | ------------------------------------------------------------------------------------------- | --------------------------- | ------------------------------------------------- |
| Cognito user email addresses          | Passed to `bin/cognito.py`, stored in Cognito                                               | PII                         | Lives in the user pool until the user is deleted  |
| Secret **references** (SSM paths)     | `deploy.toml`, environment `config.toml`                                                    | Path names, not values      | Lifetime of the environment                       |
| Resolved config JSON                  | S3 `deployer-resolved-configs-<account>`, plus wherever `resolve-config.py --output` points | Endpoints, ARNs, env values | Overwritten on each resolve                       |
| Emergency checkpoints, incident notes | `local/` (gitignored, machine-local)                                                        | Service state, ARNs         | None — see R5                                     |
| Deploy logs                           | Terminal, CI job output, CloudWatch                                                         | Env values print in full    | CI retention; CloudWatch per `log_retention_days` |

- **No end-user application data** passes through deployer. It provisions the
  database and runs the migration; it never reads rows.
- **Error messages** carry AWS resource names and paths, and are addressed to
  the operator running the command, not to an application's users.
- **Data rights requests** for a deployed application are that application's
  responsibility; deployer holds nothing to disclose or delete except the
  Cognito account itself.

## Related

- [SECURITY guide](../CLAUDE.md#security) — what `make security` covers
- [PRODUCTION.md](operations/PRODUCTION.md) — the operational half of continuity
- claude-meta `best-practices/GOVERNANCE.md` — the checklist this file answers
