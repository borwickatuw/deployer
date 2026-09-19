<!-- pysmelly-guidance 0679ec1c1685 -->

# pysmelly — findings register and review conventions

Run `make pysmelly` (repo root — config lives in `[tool.pysmelly]` in
`pyproject.toml`). Full guidance: https://github.com/borwickatuw/pysmelly#readme
or regenerate the generic guide with `pysmelly init --short`.

## Deployer's convention

**Near-zero suppression.** Findings are fixed, or left standing as an
operator-visible decision recorded below. Inline `# pysmelly: ignore` is reserved
for cut-and-dry false positives (Lambda handler signatures, JSON-serialized dict
returns), each with a rationale and a `re-evaluate-by:` tag. Enumerate the
standing directives rather than trusting a count written here:
`grep -rn 'pysmelly: ignore' --include='*.py' . | grep -v .venv`. Suppression
comments go on the finding line or the line immediately above it — pysmelly does
not see them anywhere else, and a directive that suppresses nothing is deleted
(see "Standing inline suppressions"), not left as scenery.

Whole-category `skip` config is used only where a finding is structurally
inapplicable to this repo rather than merely inconvenient; the three entries in
`pyproject.toml` (`internal-only`, `shared-mutable-module-state`,
`scattered-constants`) each carry their rationale inline. `exclude = ["tests/"]`
is set because pytest setup boilerplate otherwise dominates `duplicate-blocks`.

**Scan scope note.** `make pysmelly` runs `uvx pysmelly .` over the whole repo,
which includes `modules/` — that is deliberate, because the OpenTofu modules
carry Python Lambda code. The per-module `lambda/` directories also hold
pip-vendored packages at apply time; those are gitignored build artifacts and are
not present in a clean tree, so they do not pollute the count. The tracked shared
Lambda code lives in `modules/lambda-shared/`.

## Adjudication record

**The current total is not written here — derive it.** `make pysmelly` prints
the top ten categories; `uvx pysmelly . --more-please` prints all of them.
Every count in this register is pinned to the commit it was measured at, and a
pinned count is history: it describes that tree, not the one you have checked
out. The most recent measurement, and the drift since the Phase 53 close, are
at the head of § "Remainder — the reconciled adjudication split".

Standing total at the Phase 53 close: **36** (measured at `187b2f9`, the 53p
close and the close of Phase 53 — re-verified 2026-08-25. The rise from 33 is
deliberate: 53p-1
cleared one and 53p-3 surfaced four by deleting rationale-free suppressions,
moving those four decisions out of comments and into this register. It was 33
at `411c8f0`, the 53n close, and unmoved by
53m and 53n; at `983237c`, the 53l re-verification; and at `8d5629d`, the 53k
re-measure; 33 at
the 53i-3 closeout `600c788` and unmoved by the whole of 53j, 32 at `a9327ab`,
35 at the 53f/53g closeout `722d50b`, 37 at `aacee1b`, 38 at `a8d7369`, 39 at
`4e63c05`, 41 at `bb17c37`/`b5b8465` — the 53f/53g state — 47 at `64e3e18`,
49 at `2b057ae` and `961be51`, 52 at `9c95d79`, 53 at `d75d24e` and `57bc874`,
56 at `a304fa1`, 57 at `9903e2b`, 60 at `805d516`, 68 at `db8aa78`, 71 at
`26d9290`, 74 at `07d65d6`, 82 at `2d79e33`, 91 at `a8800cd`, 97 at `8e57264`).

**Phase 53 is closed (2026-08-25).** At `187b2f9` every live finding was
attributed, nothing was unowned, nothing was open, **and nothing was
escalated**. See § "Remainder — the reconciled adjudication split": **36**
adjudicated leave-standings, **0** escalated, **0** open — the state of the
tree at the close, not a standing property of the repo; the findings that have
landed since it are unadjudicated. The operator took the last ten verdicts on
2026-08-25;
§53p applies the four that were work and records the seven that were not. The 3
findings that 53f/53g's reconciliation found owned by no subphase were folded
into 53i-1 by operator decision (2026-08-18); one of them is cleared and two are
among the leave-standings the operator confirmed in §53p.

**The 53f/53g register gap is closed** (2026-08-18). Both shipped in the
2026-08-13 unattended run without an adjudication entry here; the gap was
carried forward three times and blocked 53i, which cannot be scoped against a
settled/open split that does not exist. §53f and §53g below are **backfilled
from their commit messages and the run ledger, not re-derived** — and §53g's
skip list was **re-verified at HEAD before transcription**, which found three
stale verdicts and one dead parameter. Writing another subphase's record after
the fact is still not something a session should invent; transcribing a
recorded one, and re-running its escalations first, is a different act.

### 53a — db-\* Lambda twin consolidation (2026-08-12)

`modules/db-users/lambda/index.py` and `modules/db-on-shared-rds/lambda/index.py`
were near-identical twins of security-sensitive user/privilege code with no test
coverage. Eight findings across them; **six cleared, two left standing**.

**Consolidated** into `modules/lambda-shared/db_common.py` — one tracked copy,
vendored into each bundle at apply time by `null_resource.lambda_dependencies`
and gitignored in the `lambda/` dirs:

`escape_literal`, `escape_identifier`, `get_secret`, `connect`, `user_exists`,
`create_user`, `update_user_password`, `grant_dml_on_existing`,
`grant_all_on_existing`, `set_default_privileges`, `transfer_ownership`,
`handle_create_extensions`, plus the `DbUser` and `DbCredentials` dataclasses.

**Deliberately left divergent** — `create_app_user` and `create_migrate_user` in
both modules, plus `setup_schema_privileges` (db-on-shared-rds), stay in each
`index.py`. They encode two different security
models: dedicated instance grants schema + table privileges at user-creation
time; shared instance grants only CONNECT on one database, then sets schema
privileges from inside that database. Merging them is how a shared-RDS tenant
would silently gain access it should not have. The shape is **shared vocabulary,
per-module policy**: the individual GRANT statements are shared, their
composition is not. db-users' former `setup_default_privileges` and
`grant_app_permissions` were pure wrappers around what is now
`set_default_privileges` / `grant_dml_on_existing`, so they were dropped rather
than kept as aliases.

Rode along: db-users now identifier-quotes `db_name` at its three
`ON DATABASE {db_name}` sites, matching db-on-shared-rds (a no-op for lowercase
names; fixes hyphenated database names, a syntax error before). Usernames stay
unquoted in both — existing roles were created unquoted and Postgres folded them
to lowercase, so quoting now would retarget `CREATE USER`/`GRANT` on any
environment whose `name_prefix` contains uppercase. Revisiting that needs a
survey of deployed `name_prefix` values first.

First test signal this code has ever had:
`tests/unit/test_lambda_db_common.py` (36 tests), asserting on the **SQL text**
each helper emits, plus a drift guard that AST-parses both `index.py` files and
checks every `from db_common import …` name resolves.

| Check            | Count | Disposition                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ---------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| duplicate-blocks | 1     | **Recommended leave-standing, pending operator confirmation.** 5 statements shared by `handle_create_users` / `handle_setup_database`: load credentials, log, open a connection. The env-var contract itself is already consolidated into `DbCredentials.from_environment()`. Drafted fix — a `connected_as_master(creds, db)` context manager — buys two lines and hides the connection lifetime that *is* the shared-instance model (db-on-shared-rds opens two connections, one to `postgres` and one to the app database, with `db_created` state crossing the boundary). Reads worse. |
| param-clumps     | 1     | **Recommended leave-standing, pending operator confirmation.** `(conn, db_name, user)` across the four `create_*_user` functions. Drafted fix — a dataclass bundling connection + database + user — mixes a live connection with data, and the connection differs per call in db-on-shared-rds. Relocation, not consolidation: `create_app_user(conn, user, db_name)` is already the clearest signature. Collapsing the four functions into one is out of the question (see "deliberately left divergent" above).                                                                          |

Cleared by 53a: 4 × `duplicate-blocks` (the `transfer_ownership`,
`handle_create_extensions`, `create_app_user`/`create_migrate_user` intra-file,
and `setup_schema_privileges` twins), 1 × `duplicate-except-blocks`, 1 ×
`param-clumps` (`(conn, password, username)` across six functions, cleared by
`DbUser`). One `single-call-site` finding appeared mid-arc when
`create_extensions` became a single-caller helper in the shared module; it was
inlined into `handle_create_extensions` rather than suppressed.

#### The `duplicate-blocks` leave-standing is resolved (2026-09-18)

The row above, and the §53l re-verification that later marked it "Holds", both
judged **one** drafted fix — a `connected_as_master(creds, db)` *context
manager* — and rejected it for the right reason: it hides a connection lifetime
that is part of the shared-instance model. That verdict stands for the context
manager and was carried for a month as if it were a verdict on the finding.

A plain function does not have the problem the context manager had.
`db_common.connect_as_master(creds, database)` logs the endpoint and **returns**
a connection; every `try` / `finally: conn.close()` stays exactly where it was,
in the handler. Both twins' prologues drop from five structurally identical
statements to four, under `duplicate-blocks`' threshold, and the check reports
nothing in the repo. Nothing was minted in the trade (repo total 51 → 50; run
`make pysmelly` to re-derive).

The consolidation also fixed a real inconsistency the duplication was hiding:
the two twins announced the endpoint in different words, and the second
connection db-on-shared-rds opens — against the application database, for the
schema-privilege pass — logged the database name but no host or port at all.
All three sites now log one line from one place. `create_app_user` /
`create_migrate_user` and the create-or-update pairing around them stay per
module, unchanged: that is the privilege composition 53a deliberately left
divergent, and it is not what the check was pointing at.

Tests: three in `tests/unit/test_lambda_db_common.py` on the helper (named
database wins over `creds.db_name`, the endpoint reaches the log, the
connection comes back open), plus a drift guard asserting each twin imports
`connect_as_master` and not bare `connect` — importing `connect` directly is
how the announced endpoint and the opened connection would drift apart again.

### 53b — CLI boilerplate dedup (2026-08-13)

The duplication spread across `bin/` and the `src/deployer` modules those
scripts call. **19 findings** at `a8800cd`: 14 `duplicate-blocks` +
5 `duplicate-except-blocks`. **11 fixed, 8 left standing.** Six side-effect
clears and six mints came along with them (both listed below); the repo total
went **91 → 82**.

Note for anyone re-reading the plan: `pysmelly bin/` alone reports only 11 of
the 19. The cross-file partners are in `src/deployer/init/setup_profiles.py`,
`src/deployer/deploy/extensions.py`, `src/deployer/cli/ci_deploy.py` and
`src/deployer/utils/cli.py`.

**Extracted to `src/deployer/utils/cli.py`** (reached through the curated
`utils/__init__.py` façade — every `bin/` script imports from the façade and
none reach into submodules):

| Helper                            | Cleared                                                  |
| --------------------------------- | -------------------------------------------------------- |
| `prompt_or_exit`                  | `emergency.py:637` + `cli.py:confirm_action:30`          |
| `exit_on`                         | `ecs-run.py:327` + `init.py:256/306/375`                 |
| `configure_profile_or_exit`       | `deploy.py:124` + `resolve-config.py:229`                |
| `configure_aws_for_operation`     | `cognito.py:58` / `ssm-secrets.py:381` twins (unflagged) |
| `validate_and_configure`          | `emergency.py:228` / `ops.py:380` twins (unflagged)      |
| `load_environment_infrastructure` | `emergency.py:192` + `ops.py:402`                        |
| `iter_deployed_environments`      | `capacity-report.py:207` + `environment.py:90`           |

Plus `utils/datetime.format_timestamp` (promoted from `ops.py`'s local, the more
general of two timestamp twins), `deploy/pipeline.run_deploy_pipeline` (the
`deploy.py` / `ci-deploy` clone from preflight onward),
`init/bootstrap.prompt_account_id_and_region`, and three `bin/init.py`-local
extractions (`_print_dry_run_preview`, `_run_tofu`, and in `ops.py`
`_append_timeline_note`).

**Error contract.** An `except` clause cannot be extracted on its own, so the
try body *and* its handler move into a named `*_or_exit` helper that calls
`sys.exit(1)`. Where each try body is a *different* call and only the handler
is shared, `exit_on(*excs, prefix="")` wraps the single failing call — never a
whole `cmd_*` body, which would swallow an unrelated exception of the same type.

**Two behaviour changes**, both deliberate:

- The `emergency.py` / `ops.py` banners now print *before* validation rather
  than after, so a not-deployed environment shows the banner and then the
  error. This is what lets one helper serve both scripts without a banner-text
  parameter.
- `_append_timeline_note` pins one `.replace()` anchor where the two incident
  paths had used different ones, so the note path no longer leaves a blank line
  between timeline entries. Both paths now emit one tight Markdown list.
  `tests/unit/test_ops.py` pins the contract.

**Latent bug fixed:** `ci_deploy` guarded `parse_deploy_config` in its own try;
`bin/deploy.py` inlined it as an argument, so a `deploy.toml` parse failure
there surfaced as a traceback. The merged pipeline guards it once.

**Coverage:** `utils/cli.py` had **zero** tests — nothing anywhere imported
`confirm_action`, `require_environment`, `require_validated_environment` or
`EnvironmentConfigError` from a test. It is now at 98%, `deploy/pipeline.py` at
100%, and `make test-cov` moved 33.04% → 36.59% (`fail_under` 32 → 36).
New files: `tests/unit/test_utils_cli.py`, `tests/unit/test_deploy_pipeline.py`,
`tests/unit/test_ops.py`.

#### Left standing

| Check            | Count | Disposition                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ---------------- | ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| duplicate-blocks | 8     | **Recommended leave-standing, pending operator confirmation.** `init.py:231, 254, 284, 289, 345, 480 (×2), 481` (anchors after the 53b extractions; they were `191, 192, 230, 260, 269, 329, 476 ×2, 477` at `a8800cd`). pysmelly is matching *runs of consecutive `print(<literal>)` statements that share no content* — no two sites share a single step string. Drafted fix: `print_next_steps(steps: list[str], *, header: str = "Next steps:")`. Kept out because every site would still own its full prose, and the sites vary on header text, numbering (hardcoded / absent / dynamic counter), indent (`"  1. "` / `"  "` / `"     - "`) and interleaved blank lines — so the helper either takes pre-formatted strings and does nothing, or grows a flag per axis. `init.py:_print_next_steps` is the evidence: it is what happens when you *do* generalize this, and it grew a dynamic `step` counter and a three-way `template_name` branch and still has exactly one caller. **Re-measure after 53d**, which decomposes `cmd_bootstrap` (131L) and `cmd_environment`; those regions move and some of these overlapping findings may clear or change shape as a side effect. |

`bin/init.py:cmd_deploy_toml` keeps its explicit `except ValueError` /
`except Exception` pair rather than a second `exit_on`: nesting two `exit_on`
blocks to preserve that ordering reads far worse than the try/except does.
Converting the other three sites was enough to drop the group below the
reporting threshold.

`src/deployer/deploy/extensions.py` was listed for 53b in claude-meta's plan but
does not belong here. Its three flagged line ranges match `bin/init.py` only by
AST shape — different module, different output convention (`log_*` vs bare
`print`), terminates by `raise` not `return 1`. Its *real* duplication is
internal to `create_database_extensions` (five `log_error → print advice → raise RuntimeError` blocks) and belongs with **53e**.

#### Side effects and mints

Cleared as a side effect (not counted against the 11): `inconsistent-error-handling`
6 → 4 (`emergency.py:cmd_restore_db`, `config/deploy_config.parse_deploy_config`),
`arrow-code` 6 → 5 (`cmd_restore_db` flattened by `prompt_or_exit`),
`long-function` 17 → 16 (`ci_deploy.main` 122L → below threshold; `deploy.py`
155L → 115L).

Minted, all recommended leave-standing:

| Finding                                                                                                  | Disposition                                                                                                                                                                                                                                                                                                                                                                                      |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `pass-through-params` ×3 — `cli.py:configure_profile_or_exit` (×2), `cli.py:configure_aws_for_operation` | The forwarding *is* the wrapper; the value they add (a `try/except` and a branch) is what pysmelly's check does not model. The category already carries nine standing instances of this exact shape elsewhere in the repo.                                                                                                                                                                       |
| `foo-equals-foo` — `deploy.py:167` `run_deploy_pipeline()` 4 `foo=foo` args                              | Click parameter forwarding, same shape as the two standing `init.py` instances. `timer` cannot be inlined: it is built conditionally two statements earlier.                                                                                                                                                                                                                                     |
| `param-clumps` — `pipeline.py:23` `(env_config, environment, environment_type)` now in 3 functions       | Crossed the threshold because `preflight.py` already had two. Drafted fix — an `EnvironmentTarget` dataclass — has to be threaded through `preflight.py`, which is **53c** scope. Routed there.                                                                                                                                                                                                  |
| `boolean-param-explosion` — `pipeline.py:23` 4 booleans                                                  | `dry_run`/`force`/`force_build` arrive as a clump from `common_deploy_options` and continue into `Deployer`, which takes them individually; `ecr_hint` is the fourth. Drafted fix — a `DeployOptions` dataclass — is only worth it threaded all the way through `Deployer`, which is **53c/53d** scope. A pipeline-only options object that unpacks immediately is flag-shuffling. Routed there. |

**Noticed, not acted on:** `utils.require_environment` is exported but has zero
callers anywhere. `internal-only` is in this repo's pysmelly `skip` list, so no
finding covers it. Deleting a public export is a separate operator call.

### 53c — src/deployer dedup, plus the two findings 53b routed forward (2026-08-13)

**7 targets** at `2d79e33` (repo total 82): the five `duplicate-blocks` that
live entirely inside `src/deployer`, plus the `param-clumps` and
`boolean-param-explosion` that 53b minted and explicitly routed here.
**All 7 cleared.** Three more cleared as a side effect and two were minted; the
repo total went **82 → 74**.

claude-meta's plan called this subphase "small, mechanical". It is not. Reading
every site showed three distinct classes, and two of the five were the tip of
duplication pysmelly cannot see.

#### The advice vocabulary (clears C1 `preflight.py:52`, C4 `deployer.py:420`)

"An error message with actionable advice" was implemented five ways in this
repo. Two functions in `utils/logging.py`, exported through the curated
`utils/__init__.py` façade, are now the one vocabulary:

| Function                                        | For                                        |
| ----------------------------------------------- | ------------------------------------------ |
| `advice_block(heading, items, advice, bullet=)` | composing a string to `raise SomeError(…)` |
| `print_with_advice(message, *advice)`           | writing the same shape to the terminal     |

Adopted at six sites: `preflight.check_environment_config` /
`check_audit` / `check_modules` (the third an unflagged rider),
`ssm_secrets.format_missing_secrets_error`, `deployer.handle_push_error`,
`deployer.deploy`. Output is byte-for-byte unchanged — verified against the real
functions, not only the unit tests.

C4 was drafted as a "measure, don't assume": its two blocks share no content and
looked like the `bin/init.py` print-run class 53b left standing. It cleared.

Out of scope by operator decision: `images.format_missing_ecr_error` stays a
triple-quoted template (numbered, embeds shell commands, reads worse as a
builder). `extensions.py`'s five advice blocks are **53e**, which inherits this
vocabulary.

#### `aws/cli.py` (clears C3 `cognito.py:27`)

`aws/` shells out to the `aws` CLI in three modules — cognito (7 commands), rds
(3), cloudwatch (1) — and all 11 hand-built the same argv ending in
`--region AWS_REGION`. pysmelly flagged **one** pair. It missed five more, all 3
statements and under the threshold:

- `cognito.delete_user` / `disable_user` / `enable_user` — three 12-line
  functions identical but for `admin-delete-user` / `admin-disable-user` /
  `admin-enable-user`, now one line each via `_admin_user_action`
- `rds.stop` / `rds.start` — the same, via `_instance_action`

`aws_command` / `run_aws` / `run_aws_json` are the new shared surface.
`run_aws` is the workhorse because several callers need the output text **on
failure** (`_handle_user_not_found` greps it for `UserNotFoundException`).
`aws/ecs.py` and `aws/ssm.py` are deliberately absent: they use boto3.

**Two behaviour changes, both deliberate:**

- **Argv order.** Where a command conditionally extended *after* `--region`
  (`list-users --pagination-token`, `admin-create-user --message-action` /
  `--temporary-password`, `get-log-events --start-time`), `--region` now comes
  last in every case. Semantically identical to the `aws` CLI; the new order is
  what the tests assert.
- **`JSONDecodeError` no longer escapes.** `cloudwatch.get_log_events` already
  answered `None` on unparseable output; `cognito` and `rds` let it raise. All
  three now answer `None`, matching "None means the query did not answer".

**First test signal this package has ever had** — cognito was at 12%, rds 18%.
`tests/unit/test_aws_cli.py`, `test_aws_cognito.py`, `test_aws_rds.py` assert on
the **emitted argv**, with `run_command` stubbed at the one seam all three
modules share (`aws_cli` fixture in `tests/conftest.py`). cli 100%, cognito 100%,
rds 100%. cloudwatch stays at 15% — only its single CLI-based function moved.

#### `preflight._infrastructure_value` (clears C2 `preflight.py:97`)

`check_ecr_repositories` and `check_ecs_cluster` shared "log a banner, make a
boto3 client, read an `[infrastructure]` key, warn-and-skip if absent". The read
half is now one helper; the client moves **after** the guard, so no client is
built for a check that is about to be skipped, and is passed straight to the
validator rather than through a single-use local.

Extracting the helper alone left the finding standing at 5 statements — because
pysmelly counts the **docstring** as one of them, the remaining run was
docstring + banner + read + guard + client. Removing the single-use client local
is what dropped it below the threshold. (The docstring-anchoring behaviour is
already filed as a guide-backlog item in claude-meta under source `58`.)

Both `log_warning` strings are preserved exactly — existing tests assert on them.
Two `--verbose`-only `log_debug` labels changed to name the config key they
report: `"ECR prefix: …"` → `"ecr_prefix: …"`, `"Cluster name: …"` →
`"cluster_name: …"`.

#### `modules/__init__.py` resolvers (clears C5)

`resolve_service_url` and `resolve_internal_service_url` each merged their two
guards and inlined the service lookup: 9 statements → 3. No behaviour change —
the original's early `domain_name` / namespace return skipped a dict lookup with
no observable effect. Both are covered at 99% through `resolve_service_urls`.

#### `EnvironmentTarget` + `DeployOptions` (clears the two routed-in findings)

`deploy/context.py` already held `DeploymentContext` ("This replaces the 10+
individual parameters that were threaded through…"). Both fixes are that same
pattern in that same file:

- **`EnvironmentTarget(name, type, config)`** — adopted by the three functions
  the `param-clumps` finding named (`run_preflight_checks`, `check_ssm_secrets`,
  `run_deploy_pipeline`) plus `check_ecr_repositories`, which took two of the
  three and is `check_ssm_secrets`' sibling. `check_environment_config`,
  `check_modules` and `check_ecs_cluster` keep taking `env_config`: each needs
  exactly one field, and passing a three-field object to read one is worse.
- **`DeployOptions(dry_run, force, force_build)`** — replaces
  `Deployer.__init__`'s three booleans; ~11 internal references became
  `self.options.<flag>`. **`DeploymentContext.dry_run` stays a bare bool** — it
  genuinely needs only that flag and `ctx.dry_run` has seven call sites in
  `service.py`. Threading the options object further would be churn.

`run_deploy_pipeline` now takes `preflight=` (which checks to skip) alongside
`options=` (how to deploy), leaving one boolean — `ecr_hint` — so
`boolean-param-explosion` clears.

**Coverage:** `make test-cov` 36.59% → 37.92%, `fail_under` 36 → 37.

#### Side effects and mints

Cleared as a side effect (not counted against the 7):

| Finding                                                                     | Cleared by                                 |
| --------------------------------------------------------------------------- | ------------------------------------------ |
| `param-clumps` `preflight.py:92` `(deploy_config, env_config, environment)` | `EnvironmentTarget`                        |
| `foo-equals-foo` `deploy.py:167` (4 `foo=foo` args, minted by 53b)          | `DeployOptions` bundling three of the four |
| `foo-equals-foo` `pipeline.py:68` `run_preflight_checks()`                  | `EnvironmentTarget` collapsing four kwargs |

Minted, both recommended leave-standing:

| Finding                                                                                        | Disposition                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pass-through-params` — `cognito.get_user_pool_name` forwards `user_pool_id` to `run_aws_json` | Same shape as the three 53b minted on `utils/cli.py` wrappers; the category already carries twelve standing instances. Drafted fix: inline the function into its one caller, `bin/cognito.py` — which puts argv-building back into a `bin/` script, the exact thing this subphase removed.                                                                                                  |
| `return-none-instead-of-raise` — `aws/cli.py:48` `run_aws_json`, 3 of 4 callers guard          | **Left unsuppressed on purpose.** Four `return-none-instead-of-raise` suppressions already stand in this repo (`bootstrap.py`, `core/config.py`, `utils/links.py`, `aws/rds.py:get_status`), and whether query helpers should raise is **53i**'s call for the whole repo. Adding a fifth suppression would hide the decision; leaving the finding visible puts it in front of the operator. |

**Correction to what commit `9dfb8ea` claimed.** Its message said the repo stood
at 80 findings and that `return-none-instead-of-raise` did not fire on
`run_aws_json`. Re-measured against that commit, the real count is **81** and the
check does fire. The mid-commit check that said otherwise was run wrong. The
per-commit ladder is 82 → 80 → 80 → 81 → 79 → 74.

`duplicate-blocks` now has **no open items**: all 9 remaining are adjudicated
leave-standings (8 `bin/init.py` print-runs from 53b, 1 Lambda from 53a).

### 53d-1 — the deploy.toml-resolution family in `bin/` (2026-08-13)

**4 targets** at `07d65d6` (repo total 74), all `long-function`:
`deploy.py:72 deploy` (111L), `ssm-secrets.py:88 cmd_check` (110L),
`ecs-run.py:233 cmd_run` (108L), `capacity-report.py:55 check_environment`
(123L). **All 4 cleared**, one `pass-through-params` minted; the repo total went
**74 → 71**.

Re-measuring at HEAD before starting corrected claude-meta's plan entry, which
said 53d had **6** long-function findings in `bin/`. There are **7** —
`capacity-report.py` was in the entry's file list but not in its count. The
operator split the subphase: 53d-1 is these four files, 53d-2 is `emergency.py`,
`ops.py` and `init.py`.

#### `resolve_deploy_toml_or_exit` (clears D1, D2 and D3 together)

`deploy.py:97-121`, `ssm-secrets.py:92-123` and `ecs-run.py:251-299` each
hand-rolled the same resolution — explicit `--deploy-toml`, else the link
registry, else an error naming `link-environments.py`; then, if explicit, a
"link it" nudge. Three copies, ~30 lines each, inside three of the four
functions this subphase decomposes.

pysmelly does not flag it: the message strings differ per script (`deploy.py deploy` vs `ssm-secrets.py check` vs `ecs-run.py run`), the same blind spot the
five `aws/` twins hit in 53c. The helper takes two string parameters for the two
axes that genuinely differ — `specify_hint` and `link_benefit` — following
`exit_on(*excs, prefix=)`. It lives in `utils/cli.py` and is reached through the
curated `utils/__init__.py` façade.

Extracting it was most of D1–D3's decomposition. All three `cmd_*` fell under
the threshold on that commit alone, before their own per-file splits landed.

Three deliberate behaviour changes:

| Change                                                                                                                     | Why                                                                                                                                                                                                                                                                                                                             |
| -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Errors go to stderr.** `deploy.py`'s three error lines move stdout → stderr.                                             | The helper uses `log_error_stderr`, matching `exit_on`, the one existing `*_or_exit` that reports failures. The repo is inconsistent (`prompt_or_exit`, `configure_profile_or_exit` use stdout), so this picks a side rather than inheriting one. A rider on the `print` → `log_*` subphase 53b filed, not a substitute for it. |
| **One error message, not three.** `ecs-run.py`'s four-line "To link this environment to its deploy.toml:" wording is gone. | That is the point of the extraction.                                                                                                                                                                                                                                                                                            |
| **Path validation is uniform.** Missing path, directory and non-`.toml` suffix are all rejected.                           | `deploy.py` did all three; `ssm-secrets.py` checked only existence; `ecs-run.py` relied on `load_deploy_toml` raising. A tightening for two of the three.                                                                                                                                                                       |

`ecs-run.py` keeps its own pre-check: no environment **and** no `--deploy-toml`
is its own usage error, since there is no link to consult. The helper's
`environment` parameter is therefore `str | None`, and it skips the tip when
there is no environment to link.

#### Per-file splits

| File                 | Extracted                                                                                                                                       |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `deploy.py`          | `_load_env_config_or_exit(environment) -> (config, type)`                                                                                       |
| `ssm-secrets.py`     | `_print_secret_table(present, missing, extra)`; the two trailing command lists became `advice_block(…, bullet="  ")`, the vocabulary 53c landed |
| `ecs-run.py`         | `_print_available_commands(dt) -> int`                                                                                                          |
| `capacity-report.py` | `_deployment_cutoff(last_deployment_at)`, `_count_ecs_oom`, `_count_log_oom`                                                                    |

All four `# noqa: C901` suppressions on those functions were removed, not moved.

`capacity-report.py` is the one target with no cross-file twin — its duplication
is *inside* the function. The `last_deployment_at` parse appeared twice in two
variants, one bare and one with a `try/except` and a different fallback.
`_deployment_cutoff` returns `None` and each caller keeps its own fallback,
because the fallbacks genuinely differ (the log scan falls back to the window
start). Behaviour change: the ECS scan previously **crashed** on an unparseable
timestamp — the bare `fromisoformat` had no guard — and now degrades to
"unknown", matching what the log scan already did.

An unflagged rider: the `put`-command derivation
(`ssm_path.split("/")[-1]` → a `bin/ssm-secrets.py put` invocation) was
duplicated between `cmd_check` and `core.ssm_secrets.format_missing_secrets_error`,
which 53c had rewritten onto `advice_block` a day earlier. Both now call
`ssm_put_commands(env_name, missing)`.

#### Tests and coverage

`ssm-secrets.py`, `ecs-run.py` and `capacity-report.py` were at **0%** and had
never had a test. Three new files —
`tests/unit/test_ssm_secrets_cli.py`, `test_ecs_run.py`, `test_capacity_report.py`
— plus extensions to `test_utils_cli.py` and `test_deploy.py`. Helpers are
tested directly; each `cmd_*` is tested through its control-flow branches with
collaborators monkeypatched at the seam, the 53b/53c pattern. 632 → 722 tests.

`make test-cov` went **37.92% → 44.11%**; the floor moved 37 → 44.
`ssm-secrets.py` 0 → 73%, `ecs-run.py` 0 → 61%, `capacity-report.py` 0 → 87%,
`deploy.py` 67%, `utils/cli.py` 98%.

#### Side effects and mints

Nothing cleared as a side effect. One minted:

| Finding                                                                                                               | Disposition                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pass-through-params` — `ssm_secrets.py:250` `format_missing_secrets_error` forwards `env_name` to `ssm_put_commands` | **Left unsuppressed, recommended leave-standing.** `env_name` used to be interpolated inline, so the forward is an artifact of the extraction that removed the duplication. Drafted fix: have the one production caller (`preflight.py:164`) build the commands and pass them in — that makes it pass `missing` twice and forces callers to know the pairing. Worse. The category already carries thirteen standing instances of this shape. |

`long-function` is now **12**, all of them `bin/` (3) or `src/` work queued
behind 53d-2 and later subphases.

**Noticed, not changed.** `capacity-report.check_environment` returns `1` for
"config failed to load", "no cluster name", "no services found" *and* "OOM
found", and `cli` collapses all four into one exit code — a config error reports
as a capacity problem. Changing a report script's exit contract is an operator
call; routed to **53i** alongside the other error-contract work.

### 53d-2a — `emergency.py` and `ops.py` (2026-08-13)

**4 targets** at `26d9290` (repo total 71): `emergency.py:230 cmd_rollback`
(155L, `long-function`), `emergency.py:392 cmd_scale` (depth 5, `arrow-code`),
`ops.py:387 cmd_status` (depth 5, `arrow-code`), and the
`(environment, service, yes)` `param-clump` across `cmd_rollback` / `cmd_scale`
/ `cmd_force_deploy`. **3 cleared, 1 left standing**; the repo total went
**71 → 68**. Nothing minted — the `pass-through-params` list is unchanged,
finding for finding.

The operator split 53d-2 in half: this is `emergency.py` + `ops.py`; `init.py`
and the re-measure of 53b's print-run leave-standings are **53d-2b**. The two
halves cleave along the twin below — `init.py` shares nothing with either file.

#### Characterization tests first

`cmd_rollback` and `cmd_scale` mutate ECS and write checkpoints, at **0%**
coverage. The first commit pinned today's behaviour — return codes, output,
and the exact arguments handed to each mutator — and every later commit had to
leave those tests passing **unchanged**. That is the only thing that actually
shows a decomposition was behaviour-preserving, and it is the pattern
sword-client 55a uses.

One test-design decision earned its keep immediately: interactive prompts are
stubbed at `builtins.input`, not at `prompt_or_exit`. When the prompt moved
into `select_index` two commits later, the pins did not notice.

#### The unflagged twin: a guard that does nothing

`if x and "T" in x: x = format_timestamp(x)` appeared at **six** sites —
`ops.py` 425/451/564 and `emergency.py` 291/574/626. `format_timestamp` already
returns its input unchanged on any parse failure (its `except (ValueError, AttributeError)` arm covers non-ISO strings *and* `None`), so all six were
no-ops around a function that had done their job since it was written.

`cmd_status`'s depth-5 chain was `if cluster_name` → `for name` →
`if revisions` → `for rev` → **the guard**. Deleting it cleared that
`arrow-code` finding on its own — one of the four targets fixed by a deletion.

`emergency.py:626` was the exception, and not in a good way: it had no
truthiness half, so `"T" in timestamp` raised `TypeError` on a null checkpoint
timestamp. The bare call cannot.

#### `select_index` (the numbered-pick twin)

`cmd_rollback` held two interactive numbered-pick blocks with the same shape —
header, numbered list, prompt, digit check, bounds check, index — differing on
five axes. Four fold into parameters (`start`, `default`, `invalid_message`,
plus the header/prompt strings); the fifth, per-item rendering, disappears
because callers pass already-rendered labels. It lives in `utils/cli.py` beside
`prompt_or_exit` and `confirm_action` and is exported through the façade, where
53b and 53d-1 put this class of helper.

**The lower bound stayed in the caller.** Revision 0 is displayed on purpose
and rejected on purpose; that is `cmd_rollback`'s domain rule, not the menu's.
Pushing it in would have bought a sixth parameter to hide one `if`.

**A third candidate, evaluated and excluded:** `cmd_restore_db`'s prompt has the
same shape but reads non-digit input as a *timestamp* and tail-recurses into
itself. That is a two-mode prompt, not a menu.

#### Per-file splits

| File           | Extracted                                                                                                                                      |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `emergency.py` | `_require_service`, `_select_service`, `_select_revision`, `_print_env_var_diff`, `_checkpoint_and_log`, `_await_deployment`, `_scaled_counts` |
| `ops.py`       | `_print_service_table`, `_print_recent_task_definitions`, `_print_rds_status`, `_print_recent_snapshots`, `_print_scaling_config`              |

`cmd_rollback`'s `# noqa: C901` was removed, not moved.

Two of those are twins pysmelly did not flag. `_checkpoint_and_log` was
byte-identical in shape between `cmd_rollback` and `cmd_scale`, differing only
in two strings. `_require_service` unified three copies of the membership check
— `emergency.py:240` and `:706` were byte-identical, and `:411` was a shorter
variant that told the operator the service was missing but not what was there;
adopting it gives `cmd_scale` the "Available: ..." list it lacked.

`cmd_scale`'s `arrow-code` needed only `_scaled_counts` — the `--all` branch's
dict comprehension was the depth-5 leaf. The whole four-way `to_scale` dispatch
was deliberately **not** extracted: it takes seven inputs, and would have traded
an `arrow-code` finding for a `param-clump`.

`ops.py`'s five print sections did not move the number — `cmd_status` cleared in
the guard-deletion commit and at 85 lines was never long enough to be flagged.
It is the readability half of the same work, and it is what made the section
order testable.

#### Tests and coverage

`bin/emergency.py` was at **0%** and had never had a test. New
`tests/unit/test_emergency_cli.py` (42 tests) plus `cmd_status` coverage in
`test_ops.py` and `select_index` coverage in `test_utils_cli.py`: 722 → 791.

`make test-cov` went **44.11% → 49.20%**; the floor moved 44 → 49.
`emergency.py` 0 → 57%, `ops.py` 12 → 24%, `utils/cli.py` 98%. The uncovered
half of `emergency.py` is the RDS side (`snapshot`, `restore-db`, `revert`),
which this subphase did not touch.

Assertions pinning behaviour worth revisiting are marked "pinned, not endorsed"
and name **53i**: `cmd_rollback` returns `1` both when the operator declines and
when the update fails, and `cmd_scale` / `cmd_force_deploy` return `0` even when
individual services fail.

#### Side effects and mints

`bin/emergency.py` dropped off the convergence-hotspot list (3 files → 2;
`bin/init.py` and `deploy/service.py` remain). Nothing minted.

| Finding                                                                                           | Disposition                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `param-clumps` — `(environment, service, yes)` in `cmd_rollback`, `cmd_scale`, `cmd_force_deploy` | **Left unsuppressed, recommended leave-standing.** Drafted fix: an `EmergencyTarget(environment, service, yes)` dataclass. `yes` is a confirmation flag, not an attribute of a target, so the bundle reads worse than the three parameters — and these are Click parameters unpacked immediately at the wrapper, the shape 53c's register already rejected as flag-shuffling. The real duplication behind the clump was the membership check and its error, and that **was** fixed (`_require_service`). |

**Noticed, not changed.** `cmd_restore_db` (`# noqa: C901`, not a pysmelly
finding) carries a real snapshot/PITR twin at 525-536 / 550-561; it belongs with
a subphase that owns the file's RDS half. `cmd_restore_db:539-544` and
`cmd_revert:638-642` are hand-rolled versions of what `exit_on` covers — routed
to **53i**, in functions this subphase did not touch.

### 53d-2b — `bin/init.py` and the print-run re-measure (2026-08-13)

**10 targets** at `db8aa78` (repo total 68): `init.py:132 cmd_bootstrap` (131L,
`long-function`), `init.py:357 cmd_environment` (104L, `long-function`), and the
**eight `duplicate-blocks` print-runs 53b left standing** with "re-measure after
53d". **8 cleared, 2 left standing**; the repo total went **68 → 60**, the
largest single-subphase drop in the arc. `bin/init.py` dropped off the
convergence-hotspot list, leaving `deploy/service.py` as the only file flagged
by three checks. Nothing minted.

Re-measuring first was worth it: none of the eight had moved (53d-1 and 53d-2a
touched none of those functions), but only three sat inside `cmd_bootstrap` and
**none** inside `cmd_environment`, so the decomposition and the re-measure only
partly overlapped. The re-measure was owed either way.

#### Characterization tests first

`bin/init.py` was the repo's **last 0%-coverage file** (318 statements) and it
writes files, chmods scripts and shells out to `tofu apply`. The first commit
pinned today's behaviour in `tests/unit/test_init_cli.py` — 53 tests over return
codes, every validation error, the dry-run preview, which files get written,
`import-existing.sh`'s exec bits, and the text and order of all four next-steps
lists — and every later commit had to leave them passing **unchanged**. They
did: the test file only ever gained lines (64, then 20), never edited one.

One pinning decision paid for itself in the very next commit. Blank-line
placement *between* steps was deliberately **not** pinned, because the four
lists disagreed about it and unifying them was commit 2's job; step text,
numbering and order were. Commit 2 then *added* blank-separation assertions
rather than editing any, so "tests pass unchanged" stayed literally true while
the output did change.

Pinned but not endorsed, naming **53i**: `cmd_deploy_toml`'s bare
`except Exception` reports any non-`ValueError` from the generator as a
compose-parsing failure, misattributing a generator bug to the operator's input.

#### Two families, not one

Reading the cross-file legs settled what 53b could not. The eight findings were
**two different things sharing an AST shape**:

- **Next-steps runs** — `bin/init.py` ×4 and `setup_profiles.py` ×1, "here is
  what to do next" after a successful write.
- **Error advice blocks** — `extensions.py` ×3, which 53c's
  `print_with_advice` / `advice_block` already covers and **53e** owns adopting.

Four of the five next-steps runs live in `bin/init.py`, which is what made
`_numbered_steps(heading, *steps)` a **module-local** decision rather than the
cross-file scope creep 53d-2a's plan worried about. It is not in
`utils/cli.py`, where 53b/53c/53d-1/53d-2a put genuinely shared helpers; if 53e
wants it for `extensions.py`, it can be promoted then.

**The win 53b could not see is the counter, not the printing.**
`_print_next_steps` carried a dynamic `step` variable with six `step += 1`
sites, purely because the number of steps varies by template — and 53b cited
exactly that counter as the evidence *against* generalizing. With the helper
owning the counter, that function became list-building and the bookkeeping
disappeared.

**One deliberate output change**: three sites gained a blank line between steps,
matching `_print_next_steps`, so all four lists in the file read the same. No
step text, numbering or order changed.

The two **unnumbered** runs stayed plain `print` calls — `cmd_bootstrap`'s
"Next step:" (singular) and its "After successful apply" trailer. The helper is
for numbered lists; an `unnumbered=` flag is the flag-per-axis shape 53b warned
about.

#### The decompositions

`cmd_bootstrap` → `_prompt_bootstrap_inputs` (with a frozen `_BootstrapInputs`
dataclass deriving `env_name` from `env_label`), `_resolve_bootstrap_path`,
`_write_bootstrap_files`, `_apply_bootstrap`; 131L → 33L reading as collect →
resolve → generate → dry-run → write → apply.

`cmd_environment` → `_list_available_templates`, `_require_bootstrap`,
`_resolve_environment_target`, `_write_environment_files`; 104L → 46L. 104 was
only 4 over the threshold, so the target here was structure, not the number.

Both `# noqa: C901` lines were **removed, not moved** — no extracted function
needs one.

#### The latent bug, same shape as 53b's `ci_deploy` find

`cmd_environment` wrapped its bootstrap check in `except RuntimeError: pass`
with the comment *"let the existing error handling below catch it"*. There is no
handling below — and the `except` was dead anyway, because
`bootstrap_dir_exists` catches the `RuntimeError` itself and returns `None`. So
an unset `DEPLOYER_ENVIRONMENTS_DIR` was reported as *"No bootstrap directory
found. Run bootstrap first"*, sending the operator to a command that fails for
the same unnamed reason. `_require_bootstrap` now checks the variable first and
reports it the way `cmd_bootstrap` does, naming the variable and the `.env` fix;
the message is a module constant shared by both.

(The phase plan predicted a *traceback* from the bare `get_environments_dir()`
further down. That line is unreachable with the variable unset — the misleading
message is what actually happens. Recorded because the plan's premise was
half-wrong and the fix is the same either way.)

#### Coverage

`bin/init.py` **0% → 92%**, and the repo has no 0%-coverage file left. Total
49.20% → 53.51%; floor 49 → 53.

#### Side effects and mints

| Finding                                                                       | Disposition                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `duplicate-blocks` — `init.py:302 _apply_bootstrap` ↔ `setup_profiles.py:111` | **Belongs to the `setup_profiles.py` neighbourhood (53e), not re-deferred here.** This is the surviving leg of 53b's #8, and both halves are the *unnumbered* runs `_numbered_steps` deliberately does not cover. Whoever adopts `print_with_advice` in `extensions.py` should decide these two at the same time; the other surviving `duplicate-blocks`, `extensions.py:56` ↔ `setup_profiles.py:109`, is the same pairing.                                                               |
| `foo-equals-foo` — `init.py:220 _BootstrapInputs()`                           | **53i, now measured rather than predicted.** The plan predicted the old `:194` would clear as a side effect of the decomposition. Half right: the `generate_bootstrap` call is now `name=inputs.attr` and is no longer flagged, but the check **re-anchored** onto the `_BootstrapInputs()` constructor with the same three single-use locals. Inlining them puts a tuple unpack, a multi-line `click.prompt` and a list comprehension inside keyword arguments. Net count unchanged at 3. |
| `foo-equals-foo` — `init.py:557 generate_environment()` (`listener_priority`) | **53i, unchanged.** Inlining makes a conditional expression inside a call argument, which is not clearly better. Drafted and left, as the plan scoped it.                                                                                                                                                                                                                                                                                                                                  |

Six of the eight print-runs cleared in commit 2 alone (`duplicate-blocks`
9 → 3). The two that did not are the two whose remaining legs are
`setup_profiles.py`, which this subphase's scope explicitly excluded.

### 53e-1 — `extensions.py` + `setup_profiles.py` (2026-08-13)

**3 targets** at `805d516` (repo total 60): `extensions.py:18 create_database_extensions` (114L, `long-function`) and **both surviving
`duplicate-blocks`**, `extensions.py:56` ↔ `setup_profiles.py:109` and
`init.py:302` ↔ `setup_profiles.py:111`. **All 3 cleared**; repo total
**60 → 57** (`long-function` 9 → 8, `duplicate-blocks` 3 → 1). Nothing minted:
the diff against the baseline moves exactly those two category counts and no
others, and the three new helpers do not trip `single-call-site`.

`extensions.py` now has **no pysmelly findings of any category**.

#### 53e was split before it was run

The plan named 53e as "deploy/ pipeline decomposition". Re-measured at
`805d516`, that scope was **six files and ~16 findings** — three or four
sessions, not one. 53d was scoped the same way and had to be split twice
*mid-arc*. So 53e was split up front, ordered by existing coverage descending
so the test idiom is established on small well-covered files before it reaches
the untested heart:

| Slice | Scope                                                          | Coverage at split |
| ----- | -------------------------------------------------------------- | ----------------- |
| 53e-1 | `extensions.py` + `setup_profiles.py`                          | 94% / 39%         |
| 53e-2 | `core/audit.py` — `run_audit` (the odd one out, not `deploy/`) | 66%               |
| 53e-3 | `deployer.py` — `__init__`, `deploy`, 3 × `law-of-demeter`     | 35%               |
| 53e-4 | `images.py` — `build_and_push_images`, `temp-accumulators`     | 16%               |
| 53e-5 | `service.py` — 4 × `long-function` + `arrow-code`              | 11%               |

`service.py` is 1003 lines at 11% with four targets: a session of
characterization tests before a line moves.

#### This closed a thread open since 53b

53b found five `log_error → print advice → raise RuntimeError` blocks in
`create_database_extensions` and called them the file's real duplication. 53c
landed the vocabulary (`print_with_advice` / `advice_block`). 53d-2b routed the
two surviving `duplicate-blocks` here. Nobody had done the adoption. 53e-1 did.

#### Both `duplicate-blocks` were shape matches, and the fix was not a helper

Six/five consecutive `print()` calls whose *content* had nothing in common:
`extensions.py` printed config-fix advice, `setup_profiles.py` "add credentials
next", `init.py` "tofu init -migrate-state next". No shared print helper was
possible or wanted. Both cleared as a **side effect** of adopting the existing
vocabulary — the `print()` statements became string arguments, so the run
stopped existing. Same reasoning 55a recorded for sword-client's five
shape-matches.

#### The adoption did not shrink the function — it grew it

Predicted 114L → ~102L. **Measured 114L → 118L.** black puts each
`print_with_advice(` call and its closing paren on their own line, and that
costs more than the removed `log_error(...)` / `print()` scaffolding saves.
Wrong in direction, not in conclusion: commit 3's decomposition is what cleared
the `long-function`, and the plan had said out loud not to skip it on the
assumption the adoption covered it. Worth keeping as a caution — "adopt a
vocabulary" and "shorten a function" are not the same operation.

The decomposition took it 118L → **21L** via three helpers that each own one
failure mode: `_require_lambda_name` (the config.toml lookup and its raise),
`_invoke_extensions_lambda` (the boto3 call and all four `except` arms), and
`_raise_on_function_error` (the Lambda-level error). The two orienting comments
went with them — the helper names say the same thing.

No `_fail(message, *advice, cause=)` wrapper was written. Each block's
`log_error` text and its `RuntimeError` text differ on purpose (block 1 tells
the operator "deploy.toml declares database extensions, but config.toml is
missing [database] extensions_lambda" and the caller "Missing extensions_lambda
in config.toml [database] section"); a one-message helper would flatten that.
The generic `ClientError` arm kept its bare `log_error` — it has no advice
lines, so `print_with_advice` would add only framing.

#### `advice_block` on a success path: the objection did not survive reading it

Commit 4's draft was judged on the diff, as planned. The objection was real —
`advice_block`'s four existing callers are all error paths, and all four are
`raise SomeError(advice_block(...))`, so this would be the first
`print(advice_block(...))` and the first success use. It was **kept** anyway:
the helper has no error semantics in its name, signature or docstring, and its
`bullet` default is `"    "` — four spaces, no bullet character — so
`bullet="  "` is the default's own shape, not an off-label use. The cost is
honest: 5 lines became 12.

#### Characterization tests first

`tests/unit/test_extensions.py` already had 11 tests over every error branch,
but each asserted only on the raised `RuntimeError`. **The advice text — the
entire point of those blocks — was unpinned.** `cmd_setup_profiles` was
untested outright.

Following 53d-2b's discipline: advice text and order pinned via a non-blank-lines
`_lines()` helper, blank-line placement deliberately **not** pinned, because
`print_with_advice` adds a leading blank line the old code did not. Commit 2
then *added* blank-line assertions rather than editing any, so "commits 2–5
leave commit 1's tests passing unchanged" stayed literally true while the output
did change. Second subphase running that pattern; it works.

Pinned but not endorsed, naming **53i**: `extensions.py`'s bare
`except Exception` reports any non-`ClientError` failure as "Unexpected error
invoking Lambda", so a bug inside boto3 reaches the operator as a credentials or
network problem.

#### Coverage

`deploy/extensions.py` **94% → 100%**, `init/setup_profiles.py` **39% → 100%** —
both better than the plan's ~100%/~90% prediction. Total 53.51% → **53.91%**;
floor stays at **53** (53 is still the integer just under). 852 → 872 tests.

#### Side effects and mints

None. Only `long-function` and `duplicate-blocks` moved.

### 53e-2 — `core/audit.py` (2026-08-13)

**1 target** at `9903e2b` (repo total 57): `audit.py:180 run_audit` (114L,
`long-function`, carrying `# noqa: C901`). **Cleared**; repo total **57 → 56**,
`long-function` 8 → 7, and the `# noqa: C901` came off (8 → 7 repo-wide).
Nothing minted — the category diff against the baseline moves `long-function`
and nothing else. `core/audit.py` now has **no findings of any category**.

#### The length was triplication pysmelly could not see

33 of the 114 lines were the same block written three times: print a heading,
run a check, warn each issue or print an all-clear, accumulate. pysmelly
flagged the length but not the repetition, because the three copies
**interleave with their own audit calls** and so are not runs of consecutive
statements — the shape `duplicate-blocks` keys on. Same lesson as 53d-1's
unflagged three-way `deploy.toml`-resolution twin and 53d-2a's six no-op
`format_timestamp` guards: **on this codebase, the long-function findings keep
turning out to be duplication findings the checker could not reach.**

The three blocks became a three-entry table (`_audit_checks`) and a four-line
loop, alongside `_print_header`, `_print_audit_config`, `_report_check` and
`_print_summary`. `run_audit` went **114L → 33L** and reads as its own outline.
The orienting comments (`# Parse files`, `# Extract data`, `# Audit services`)
went with them.

#### Two simplifications the length was hiding

- **`total_issues` duplicated `len(all_issues)`.** It was incremented by
  `len()` of each list that had just been `extend`ed onto `all_issues`. Two
  names for one number, kept in sync by hand across three blocks — a
  `temp-accumulators` shape that the check did not flag here.
- **Three single-use aliases.** `deploy_services` / `deploy_images` /
  `deploy_env_vars` were bound in one paragraph and each read once, many lines
  later. Now read at the point of use.

#### One deliberate behaviour change, named

The three checks now all run **before** any section prints; previously each
heading printed just ahead of its own check. Every observable output is
byte-identical — the audit functions are pure list-builders over already-parsed
data, verified against the sample fixtures — but "output-identical" is not
"behaviour-identical": if a check ever raised, the operator would no longer see
that check's heading before the traceback. Recorded rather than glossed.

#### Latent bug: a section header with no body

Found by reading before refactoring, same family as 53d-2b's dead
`except RuntimeError: pass`. The Audit Configuration section is gated on **any**
of the four `[audit]` keys being set — including `ignore_images` — but only
**three** of them had a `log_info` line inside it. A `deploy.toml` configuring
only `ignore_images` printed an empty `=== Audit Configuration ===` heading and
nothing under it.

Display-only: `ignore_images` was already honoured by `audit_images`, which
folds it into its ignore set. The setting worked; it just never said so, and
every other suppressing key reports itself. Fixed in its own commit so the
decomposition stayed behaviour-preserving.

#### Characterization tests first

`run_audit`'s **entire reporting half was unexercised** — all four pre-existing
tests passed `verbose=False`, which is exactly why the file sat at 66% while
its pure helpers were well covered. `TestRunAuditOutput` pins section text and
order, the issue lines under each heading, custom filenames, the mixed
one-check-fails case, that `verbose=False` prints nothing at all, and that both
not-found guards return before any output.

Third subphase running the same pinning discipline, and the same
pin-what-survives trick: the `ignore_images` case was pinned in commit 1 as
"the section opens" — still true after the fix — so commit 2 *added* the
body assertion rather than editing the test.

**`assert issue_count >= 0` was deleted, not kept.** That assertion held for
every value `run_audit` can return except the `-1` not-found case, so it pinned
nothing while looking like coverage. Replaced with an exact pin on the sample
fixtures, which are built to match.

#### Coverage

`core/audit.py` **66% → 100%**, with no partial branches left. Total 53.91% →
**54.62%**; floor **53 → 54**. 872 → 891 tests.

#### Side effects and mints

None. Only `long-function` moved.

### 53e-3 — `deploy/deployer.py` (2026-08-13)

**5 targets** at `a304fa1` (repo total 56), the scope 53e-1's split table
reserved: `deployer.py:__init__` (122L) and `deployer.py:deploy` (151L), both
`long-function` and both carrying `# noqa: C901`, plus the **three
`law-of-demeter`** findings in the file. **4 cleared, 1 left standing**; one
`dict-as-dataclass` minted and handed to 53f. The repo total went **56 → 53**.

Three commits: `3cf319e` characterization tests (0 findings cleared),
`b66a56a` `deploy()` (56 → 55), `57bc874` `__init__` (55 → 53).

#### 53e-3a — characterization tests first (`3cf319e`)

`tests/unit/test_deploy_deployer.py`, **72 tests**, 891 → 963 repo-wide.
`src/deployer/deploy/deployer.py` **35% → 100%** — 0 missing statements, 0
partial branches. Total 54.62% → **56.79%**; floor **54 → 56**. pysmelly
unchanged at 56, every category identical. No production line moved.

The load-bearing pin is **`TestDeployTimerArmsAgree`**. It runs each scenario
**twice inside one test** — timed and untimed — against the same cached fake
AWS clients, so the two `DeploymentContext` values compare equal, and asserts
both an identical step-call list **and** identical stdout. The step stubs print
a `[step_name]` marker, so `print()` placement is pinned too, not just call
order. That single test is what makes 53e-3b's collapse of nine
`if self.timer: … else: …` conditionals demonstrably safe; without it the
collapse is an assertion, not a result.

Fourth subphase running the pin-first discipline (53d-2a, 53d-2b, 53e-1, now
this), and the first where the pin was designed *for a specific planned
refactor* rather than for the file in general.

#### 53e-3b — `deploy()` was duplication pysmelly could not see (`b66a56a`)

**151L → 96L**; `long-function` 7 → 6, nothing else moved.

Not complexity — **nine repetitions** of

```
if self.timer:
    <call, wrapped in a timing context>
else:
    <the same call written verbatim>
```

`duplicate-blocks` cannot see it: `print()` statements and comments sit between
the copies, so they are not runs of consecutive statements. The worst instance
was an **11-line kwarg call repeated 15 lines later**. Same lesson as 53d-1's
three-way `deploy.toml`-resolution twin, 53d-2a's six no-op `format_timestamp`
guards and 53e-2's triplicated audit block: **on this codebase, `long-function`
keeps turning out to be a duplication finding the checker could not reach.**

The fix is a `NullTimer` null-object in `timing.py`; `deploy()` binds
`timer = self.timer or NullTimer()` once and every conditional pair becomes the
timed branch alone.

**Measured at each stage: 151 → 114 (the collapse alone) → 96 (after extracting
`_check_infrastructure_or_abort()`).** Recorded explicitly because it is this
arc's recurring lesson, the same one 53e-1 wrote up in the other direction: the
collapse left the function **over** the ≥100 threshold, and the decomposition
was still owed. "Remove the duplication" and "clear the `long-function`" are not
the same operation.

`# noqa: C901` **removed, not moved** — ruff is clean without it.

**A minted `feature-envy` was fixed rather than suppressed.** The first draft
passed `InfraStatus` into `_check_infrastructure_or_abort()` as a parameter,
which tripped the check — it counts *parameters* only. Moving the
`check_infrastructure_status()` call inside the helper makes `infra` a local and
the finding never exists. A better call site anyway: the helper now owns both
halves of "check, then decide whether to abort".

#### 53e-3c — `__init__` was the opposite: one oversized literal (`57bc874`)

**122L → 91L**; `long-function` 6 → 5, `law-of-demeter` 4 → 2,
`dict-as-dataclass` **5 → 6 minted**.

Read before refactoring showed `__init__` is **not** duplication — the exact
inverse of `deploy()`. One ~29-line, 18-key `infra_config` dict literal plus the
six feeder extractions that fed only it accounted for a quarter of the function.
One lift: a **module-level, single-argument** `_build_infra_config(env_config)`
(44L).

**Two further mints were designed around in advance**, from the same source
read rather than discovered by re-running:

- **module-level, not a method** — as a method it would read `env_config` ~8
  times and `self` zero times, which is textbook `feature-envy`.
- **single-argument** — a multi-arg signature over the four config sections
  would have minted `param-clumps`; a body over the inline thresholds avoids
  `single-call-site`.

One hoist — binding `application = self.deploy_config.application` once —
cleared **both** `law-of-demeter` findings, at `:68` and `:70`: the same depth-4
chain four lines apart. It also retires `source_path`, a single-use local
consumed on the very next line.

##### The mint is a surfaced pre-existing condition, not a new defect

`dict-as-dataclass` fires on `_build_infra_config` because pysmelly's
`check_dict_as_dataclass` sources its candidates only from
`_collect_dict_returning_functions` (`callers.py:1249`) and inspects dict
literals **only in `return` position**. The identical 18-key literal existed
before this commit and was invisible purely because it was an *assignment
target*. Extracting it changed nothing about the design; it changed the literal's
syntactic position.

**Left standing with no ignore comment**, and handed to **53f**. The three
available dodges were drafted and rejected as suppression by shape rather than
design: return a named local instead of the literal; write `dict(**kwargs)`;
split into sub-builders of fewer than 4 keys each. All three keep the same
18-key payload and only move it out of the checker's view.

The `infra_config` **dict shape itself is deliberately not converted here** —
that is 53f/53g scope and doing it mid-slice would collide with both. Scoping
note for whoever owns it, measured at `57bc874`: **49 references across 6
files** (`service.py` 15, `task_definition.py` 14, `test_deploy_deployer.py` 12,
`deployer.py` 4, `test_deploy.py` 3, `context.py` 1) — **not** the ~37 across 5
previously recorded. 21 are direct `.get()`/`[]` key reads, but
`task_definition.py` also consumes the payload **dynamically**: `.items()` at
`:254` and `:345`, and a spread `{**ctx.infra_config, …}` at `:166`. **A
dataclass conversion cannot be done by field access alone.**

#### Latent bugs pinned, not fixed

53e-3a's tests pin all three as **current behaviour, not as endorsements**; each
is a real defect left for a subphase that owns the contract.

| Bug                                                                                                                                                                                                                                                                                                                                                                                                | Status                                                                                                                                                                   |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`print_environment_config` raises `AttributeError` on a non-string value.** `value.startswith("ssm:")` assumes `str`, but `[environment] MAX_WORKERS = 4` in `deploy.toml` arrives from TOML as an `int` and `get_environment_variables` never stringifies it. Only bites names that miss **every** mask substring, because a masked name short-circuits first — which is why nobody has hit it. | **FIXED by 53j-2** (`0760a27`). Stringified once at the top of the loop body; the deploy path was never affected.                                                        |
| **Masking is name-substring-based and wrong in both directions.** `BASE_URL` and `MONKEY_BUSINESS` get masked (`url`, `key`), while a real secret under a name like `PUBLIC_HOSTNAME` prints in full. It also renders the `ssm:` / `secretsmanager:` `elif` nearly dead: a value referencing a secret almost always sits under a name the substring list already catches.                          | **FIXED by 53j-4a** (`630740c`). Masking deleted outright — 9 of havoc's 40 were masked and none held a secret. The `elif` went with it. ADR in DECISIONS.md 2026-08-21. |
| **`check_infrastructure_status`'s bare `except Exception` reports a _clean_ status.** A credentials failure or a network timeout is indistinguishable from "the database is healthy" — the one direction this function must never get wrong.                                                                                                                                                       | **FIXED by 53i-3d** (`600c788`). It now returns a non-critical warning naming the instance, and a non-AWS error propagates instead of being answered as "healthy".       |

#### Side effects and mints

| Finding                                                               | Disposition                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `dict-as-dataclass` — `deployer.py:38` `_build_infra_config`, 18 keys | **Minted, left unsuppressed, routed to 53f.** A pre-existing 18-key literal made visible by moving to `return` position; see above for the mechanism and for the three rejected dodges.                                                                                                                                                                                                                                                                      |
| `law-of-demeter` — `deployer.py:217` (was `:202`)                     | **Left unsuppressed, recommended leave-standing.** `except self.rds.exceptions.DBInstanceNotFoundFault:` reaches through a **boto3 client's runtime-only `.exceptions` namespace** — a botocore idiom, not a design chain; the intermediate object has no meaningful thing to be asked. Drafted fix: cache the exception class as an attribute in `__init__`. That trades a real finding for an odd attribute. Not fixed, not suppressed, no ignore comment. |

One `feature-envy` was minted mid-draft in 53e-3b and **fixed rather than
recorded** (see above), so it never reached a commit.

### 53e-4 — `deploy/images.py` (2026-08-13)

**1 target** at `d75d24e` (repo total 53), the scope 53e-1's split table
reserved: `images.py:214 build_and_push_images` (166L, `long-function`, carrying
`# noqa: C901`), alongside the file's `temp-accumulators` finding. **The target
cleared; the accumulator relocated and did not clear**, which is what the plan
predicted. The repo total went **53 → 52**. One `foo-equals-foo` was minted
mid-draft and **fixed, not suppressed**, so it never reached a commit; nothing
else moved.

Two commits: `1c55ce9` characterization tests (0 findings cleared), `9c95d79`
the decomposition (53 → 52).

#### 53e-4a — characterization tests first (`1c55ce9`)

`tests/unit/test_deploy_images.py`, **112 tests**, 963 → 1075 repo-wide.
`src/deployer/deploy/images.py` **16% → 100%** — 198 statements, 80 branches, 0
partials. Total 56.70% → **59.42%**; floor **56 → 59**. pysmelly unchanged at
53, every category identical. No production line moved.

Fifth subphase running the pin-first discipline (53d-2a, 53d-2b, 53e-1, 53e-3,
now this).

**The test-design decision that carried the slice: stubbing is at
`subprocess.run` / `subprocess.Popen` / the real filesystem — no `deployer`
binding is patched anywhere.** That is why all 112 pins survived 53e-4b's code
motion without an edit: six functions were extracted out from under them and the
seams the tests hold are all outside this module. It is 53d-2a's
`builtins.input` lesson applied one layer down.

Five pins exist specifically to make the decomposition verifiable, and each one
guards a property a naïve extraction would have silently changed:

| Class                           | Pins                                                                                                      |
| ------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `TestTagFailureModeDiverges`    | `docker tag` raises `CalledProcessError` printing nothing; build/push raise `RuntimeError` after echoing  |
| `TestBuildArgsDispatchDiverges` | both arms of the `DeployConfig`-vs-dict dispatch, including the divergence itself                         |
| `TestCacheHit`                  | the loop's only early exit, across all four states of the `ecr_client` / `dry_run` / `force_build` gate   |
| `TestHashModifiers`             | `args:` before `target:`, against a digest of the literal string — swapping them rewrites every cache tag |
| `TestTimerArmsAgree`            | timed and untimed runs produce byte-identical output and call lists, mirroring 53e-3a's timer pin         |

#### 53e-4b — the target that was **not** duplication (`9c95d79`)

**166L → 79L**; `long-function` 5 → 4, nothing else moved.

Five slices running, every `long-function` target in this arc turned out to be
repetition pysmelly could not see — 53d-1's three-way `deploy.toml` resolution,
53d-2a's six no-op guards, 53e-2's triplicated audit block, 53e-3b's nine timer
conditionals. **This is the sixth and it breaks the pattern.**
`build_and_push_images` is one linear pipeline of six sequential phases inside a
single loop: normalize → resolve → cache-check → build → tag/push → record.
Measured, not assumed: the available duplication was ~31 lines against a 67-line
requirement. Collapsing every repeated line in the function would not have
cleared the finding. Only decomposition could.

**Measure the span, not the body.** pysmelly's `check_long_function`
(`checks/structure.py:962`, `min_lines = 100`) counts `end_lineno - lineno + 1`.
The 24-line docstring and the 11-line signature are inside the 166 and are not
available to shrink — 35 of the lines were untouchable before a single statement
moved.

##### The staged measurement, which is the point

| Stage    | Lines   | Flagged? |
| -------- | ------- | -------- |
| baseline | 166     | yes      |
| E1       | 159     | yes      |
| E2       | 144     | yes      |
| E3       | 130     | yes      |
| **E4**   | **107** | **yes**  |
| E5 + E6  | 79      | no       |

A three-extraction plan stops at **107** — above the bar, with the work
apparently finished and the function reading much better. **53e-1 made exactly
that mistake in the other direction** (114L → 118L, an adoption that grew the
function). Both are the same lesson: the number is not a corollary of the
refactor, and it has to be re-measured at every stage rather than at the end.

##### The six extractions

`_normalize_images`, `_resolve_image_spec` (returning an `ImageBuildSpec`
**`NamedTuple`**), `_cache_tag`, `_docker_build_cmd`, `_tag_and_push`,
`_run_docker` — plus `_merge_build_args`, which exists to fix the one mint
(below) rather than to shorten anything.

**Complexity 17 → 7** against `max-complexity = 15`, so the
`# noqa: C901` came off and ruff is clean without it. Worth recording that the
suppression was **independently load-bearing**: getting under 100 lines was
necessary but not sufficient, and a decomposition that cleared the pysmelly
finding while leaving the function at complexity 16 would have had to keep it.

##### One mint, fixed rather than suppressed

The first draft's `ImageBuildSpec(context=context, dockerfile=dockerfile, …)`
minted `foo-equals-foo`. The locals existed only to hold left-to-right
evaluation order — they were not names for anything. Extracting
`_merge_build_args` freed the constructor to take inline expressions **in the
original order**, so the finding never exists. The only statement hoisted is a
side-effect-free `image_config.get("target")`.

##### Every designed-around mint held

The discipline 53e-3c introduced — read pysmelly's source *before* choosing the
extraction shape, rather than re-running and reacting — is now **2 for 2**:

| Would have minted                        | Avoided by                                                                                                                                |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `dict-as-dataclass` (guaranteed, 5 keys) | a `NamedTuple` — `callers.py:1246` only walks `ast.Return` whose value is an `ast.Dict` with 4+ string keys                               |
| `pass-through-params` ×up to 3           | merging print + timed-run + result-check into one `_run_docker` — `callers.py:682` skips functions with more than 2 meaningful statements |
| `param-clumps`                           | passing the spec object rather than its fields                                                                                            |
| `single-call-site` ×7                    | all seven helpers clear `callers.py:231`, which skips a function with more than 4 top-level statements **or** a span over 10 lines        |

##### The accumulator relocated and did not clear

`temp-accumulators` moved `:295 → :301`, into `_cache_tag`. **Predicted, and the
predicted outcome**: the two independent conditions building `hash_modifiers`
travel together, so extracting them into a helper changes the finding's address
and nothing else. `images.py` now carries exactly that one finding.

##### Two divergences preserved deliberately

Both are 53e-4a pins, and both are now stated in the helper docstrings so the
next reader does not "tidy" them:

- **`_tag_and_push` keeps `subprocess.run(tag_cmd, check=True)` outside
  `_run_docker`.** A tag failure raises `CalledProcessError` and prints nothing;
  build and push raise `RuntimeError` after echoing both streams. Routing all
  three through one helper would have changed three properties at once,
  invisibly.
- **The `build_args` dispatch divergence is carried verbatim into
  `_merge_build_args`** — no `isinstance` guard, so `.update(5)` raises
  `TypeError` and `.update("abc")` raises `ValueError`, while the `ImageConfig`
  arm guards and passes scalars straight through. See the latent-bug table
  below: this is a real defect, and preserving it is what keeps the commit a
  refactor.

112/112 pins pass with `git diff tests/` **empty**. Coverage 59.42% →
**59.45%**, `images.py` 100%.

#### Independent validation: ACCEPT, zero defects

Over both commits. The validator reproduced every numeric claim, re-measured
complexity itself rather than taking the commit message's word, and ran a
line-level `comm` diff of the full finding list: **2 vanished, 1 appeared** —
the `long-function` clear and the accumulator relocation — with **zero
unreported mints**.

**Fragility note, from the validator and worth acting on before anyone edits
this file.** `_normalize_images` (span 19, 4 statements) and `_merge_build_args`
(span 21, 4 statements) clear `single-call-site` **only** on the >10-line span
filter, and both spans are **docstring-dominated**. Trimming either docstring
would mint the finding. The docstrings are load-bearing in a way that is
invisible from the code.

#### Latent bugs pinned, not fixed

Continuing the section 53e-3 opened. 53e-4a's tests pin all eight as **current
behaviour, not as endorsements**; each is a real defect left for a subphase that
owns the contract. Ordered by importance.

| Bug                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Status                                                                                                                                                                      |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`build_args.<env>` dispatch divergence — a production crash on a typo.** The dict arm's `.update(<non-dict>)` raises `TypeError` for an int and `ValueError: dictionary update sequence…` for a string; the `ImageConfig` arm does not raise and passes the scalar through as a literal `--build-arg staging=5`. **Production always takes the dict arm** (`deployer.py:112` passes `get_raw_dict()`), so a typo'd `build_args.staging = "x"` crashes the deploy with an opaque message naming **neither the image nor the key**. | **FIXED by 53j-2** (`0760a27`). Both arms reject a scalar identically via one shared `merge_build_args`; ADR in DECISIONS.md 2026-08-20.                                    |
| **A non-existent `context` directory is not an error.** `rglob` yields nothing, so the image gets the digest of nothing (`e3b0c44298fc`) and `docker build` then runs against a path that does not exist.                                                                                                                                                                                                                                                                                                                           | **FIXED by 53j-1** (`752e146`). `_resolve_context` raises naming the image and the resolved path.                                                                           |
| **`ecr_login` discards both streams** (`DEVNULL`), so a `docker login` failure surfaces as a bare `RuntimeError("ECR login failed")` with no diagnostics.                                                                                                                                                                                                                                                                                                                                                                           | **FIXED by 53i-3d** (`600c788`). stderr is captured and carried in the message; stdout stays discarded.                                                                     |
| **The Dockerfile is hashed twice** — once under the `Dockerfile:` prefix, then again as an ordinary context file.                                                                                                                                                                                                                                                                                                                                                                                                                   | **FIXED by 53j-3b** (`3e1c474`). `_context_files` excludes the selected Dockerfile from the walk; the `Dockerfile:` prefix stays, because it is what records the selection. |
| **`.dockerignore` edits always bust the cache**, even comment-only ones, because the file is hashed as context.                                                                                                                                                                                                                                                                                                                                                                                                                     | **FIXED by 53j-3b** (`3e1c474`). Excluded from the walk, not hashed as a patterns digest; ADR in DECISIONS.md 2026-08-20.                                                   |
| **`should_ignore`'s final `fnmatch` is dead** for real files — it can only fire when the path *is* the context root, which `rglob` never yields.                                                                                                                                                                                                                                                                                                                                                                                    | **FIXED by 53j-3a** (`d306897`). Deleted with its `rel_str` local; the root-only pin flips `True` → `False`. Digest-neutral.                                                |
| **`parse_dockerignore` does not de-duplicate `.git`.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | **FIXED by 53j-3a** (`d306897`). `dict.fromkeys` de-dupes any repeat, first occurrence wins. Digest-neutral.                                                                |
| **`NullTimer` would raise `AttributeError` in `_run_timed_subprocess`** — it is truthy and has no `_current_step`.                                                                                                                                                                                                                                                                                                                                                                                                                  | **FIXED by 53j-2** (`0760a27`). Both timers answer a public `in_step`; `NullTimer` gained a no-op `sub_step`.                                                               |

#### Side effects and mints

Nothing cleared as a side effect and nothing was minted that survived to a
commit. The one mid-draft mint (`foo-equals-foo` on the `ImageBuildSpec`
constructor) was fixed rather than recorded — see above — as were the four
categories designed around before the extraction shape was chosen.

### 53e-5 — `deploy/service.py` (2026-08-13)

**5 targets** at `9c95d79` (repo total 52), the scope 53e-1's split table
reserved and the last slice of the arc: all four remaining `long-function`
findings — `create_service():235` (107L), `deploy_services():344` (110L),
`start_migrations():456` (100L), `_wait_for_service_stable():810` (118L) — plus
`arrow-code:344`. **All 5 cleared, none left standing.** Nothing minted. The
repo total went **52 → 47**, and `long-function` is now **empty as a category**.

Five commits: `b42be87` + `fb561e3` characterization tests (0 findings cleared),
`2b057ae` `create_service` + `deploy_services` together (52 → 49), `961be51`
`start_migrations` (49 → 49), `64e3e18` `_wait_for_service_stable` plus a
dead-code deletion (49 → 47).

#### 53e-5a — characterization tests first, in two commits (`b42be87`, `fb561e3`)

`service.py` was 1003 lines at **11%** — the least-covered file in the split
table and the only one that needed the pin split across two commits.

| Commit    | File                                     | Pins    | Effect                                       |
| --------- | ---------------------------------------- | ------- | -------------------------------------------- |
| `b42be87` | `tests/unit/test_deploy_service.py`      | **110** | the deploy path; `service.py` 11% → 44%      |
| `fb561e3` | `tests/unit/test_deploy_service_wait.py` | **154** | the migration + wait path; floor **59 → 65** |

**264 pins total**, and with both files `service.py` reaches **99%** — 375
statements, **0 missing**, 124 branches, 1 partial. Total coverage 59.45% →
**65.73%**. 1075 → 1339 tests. pysmelly unchanged at 52 across both commits;
no production line moved.

Sixth subphase running the pin-first discipline (53d-2a, 53d-2b, 53e-1, 53e-3,
53e-4, now this).

**Two test-environment findings worth keeping**, both discovered the expensive
way:

- **moto cannot be used for `ecs.run_task` here.** Its
  `_calculate_task_resource_requirements` sums the **per-container** `memory`
  key, which `build_task_definition` never sets — this repo puts CPU and memory
  at task level only. Every `run_task` against moto fails on a resource
  calculation that has nothing to do with the code under test.
- **moto does not echo `deploymentConfiguration`, `capacityProviderStrategy` or
  `healthCheckGracePeriodSeconds` back from `CreateService`**, and does not model
  `availabilityZoneRebalancing` at all. Any pin asserting on those parameters has
  to hold the boto3 client seam, not a moto backend.

#### 53e-5b — `create_service` + `deploy_services`, one commit by necessity (`2b057ae`)

**`create_service` 107L → 64L; `deploy_services` 110L → 73L and nesting depth
5 → 3.** One extraction cleared two findings at once: `_update_service` took
`deploy_services` under both the `long-function` and the `arrow-code` bar.
pysmelly **52 → 49** (`long-function` 4 → 2, `arrow-code` 3 → 2, nothing else).

**This is the arc's one duplication shape; the other three targets were linear.**
And the duplication was **between** the two functions, not inside either. Three
shapes, all invisible to `duplicate-blocks` because **each is a single
multi-line assignment** and so sits below the check's 5-statement bar:

| Shape                          | Sites (pre-commit) | Varies only in                            |
| ------------------------------ | ------------------ | ----------------------------------------- |
| `deploymentConfiguration` base | 272-275 / 414-417  | the dict key (`serviceName` vs `service`) |
| circuit-breaker injection      | 289-293 / 421-425  | the target variable name                  |
| `serviceRegistries` injection  | 319-326 / 430-439  | line wrapping                             |

That is why the two functions **had to move in one commit**. Splitting them
across two would have written `_deployment_configuration` and
`_service_registries` twice — the helpers serve both call paths.

##### The staged measurement, again

| Function          | Stage                       | Lines   | Flagged? |
| ----------------- | --------------------------- | ------- | -------- |
| `create_service`  | baseline                    | 107     | yes      |
|                   | `_require_network_config`   | **101** | **yes**  |
|                   | `_load_balancer_params`     | 82      | no       |
|                   | `_service_registries`       | 74      | no       |
|                   | `_deployment_configuration` | 64      | no       |
| `deploy_services` | baseline                    | 110     | yes      |
|                   | `_update_service`           | 73      | no       |

The obvious first extraction left `create_service` at **101** — one line over
the bar, with the work apparently finished. `_load_balancer_params` is what
actually cleared it.

#### 53e-5c — `start_migrations` (`961be51`)

**100L → 84L.** `long-function` 2 → 1; pysmelly stays at 49 (the finding it
cleared is one of the two counted in the 52 → 49 → 47 span; see the per-category
delta below). Two helpers: `_migration_network_config` (the
`describe_services` / `networkConfiguration` block, 13L → 3) and
`_resolve_migration_image` (service → image name → ECR URI lookup, 10L → 4).

The function sat at **exactly 100** — one line over. Recorded because the trap
is real: reflowing a signature or dropping a comment clears the finding at 99
and leaves a 99-line function behind. The smallest honest extraction was worth
16 lines, not 1.

#### 53e-5d — `_wait_for_service_stable`, and a deletion (`64e3e18`)

**118L → 75L**, the last `long-function` in the repo. pysmelly **49 → 47**.

| Stage                        | Lines   | Flagged? |
| ---------------------------- | ------- | -------- |
| baseline                     | 118     | yes      |
| `_describe_service_or_raise` | **102** | **yes**  |
| `_raise_task_failure`        | 85      | no       |
| `_track_no_progress`         | 75      | no       |

`_describe_service_or_raise` also retired the two locals it was the only reader
of (`ecs_client`, `cluster_name`). And once again the first extraction was not
enough: **102 is still flagged**, so `_raise_task_failure` was load-bearing, not
polish.

##### The obvious first extraction was insufficient in all four decompositions

This is the arc's headline result and the reason to record it as a rule rather
than an anecdote. Across the four functions decomposed in 53e-5 and the prior
slice, the state after the first extraction was:

| Function                         | After one extraction | Bar | Cleared? |
| -------------------------------- | -------------------- | --- | -------- |
| `create_service`                 | 101                  | 100 | no       |
| `_wait_for_service_stable`       | 102                  | 100 | no       |
| `build_and_push_images` (53e-4b) | 107                  | 100 | no       |
| `deploy()` (53e-3b)              | 114                  | 100 | no       |

**Four for four. This is the base rate, not an edge case.** A decomposition plan
that stops at one extraction should be assumed wrong until measured. 53e-1 made
the same error in the other direction (114L → **118L**, an adoption that grew
the function). Re-measure at every stage, not at the end.

##### Coverage used as proof, not as a score

5d deleted an inner block inside the failure-threshold arm:

```
try:
    _check_for_fatal_errors(events, service_name)
except DeploymentError:
    raise
```

The **coverage report is the evidence**, and it is the cleanest form this arc
found. Before the commit, those two statements were the file's **only uncovered
lines** — 264 pins exercising every other statement could not reach them. After
deletion, `service.py` has **zero uncovered statements**. A test suite that
cannot reach a line is either an incomplete suite or a dead line, and the
coverage delta is what distinguishes the two.

A validator independently confirmed unreachability from the code, on four legs:
the predecessor `_check_for_fatal_errors` call is **unconditional in the same
loop iteration**; `events` is **bound once and never rebound** between the two
calls; `_check_for_fatal_errors` is **pure** (no state, no I/O); and
`FATAL_ERROR_PATTERNS` **has no writer anywhere in the repo**. The second call
therefore cannot raise if the first did not, and if the first did the arm is
never reached.

#### The mint defence became a measurement

53e-3c introduced reading pysmelly's source before choosing an extraction shape.
**53e-5 went one step further and ran the checker's own internals on hypothetical
code.** Both 5b and 5c/5d imported pysmelly's `_extract_all_signatures`
(`checks/structure.py:447`), ran it over the repo, **injected the signatures they
were about to write**, and ran `_find_param_clumps` (`:498`) — **before writing a
line of production code**. Result both times: **17 raw clumps before, 17 after.**

This is the technique to reuse. It converts "I think this signature is safe" into
a measurement, and it costs one throwaway script.

#### The `_`-prefix rule is a mint defence, not cosmetics

`build_function_index` (`checks/helpers.py`, the skip at `:103-104` in pysmelly
**3.2.1.dev15**) drops any function whose name starts with `_` or `test`, plus
decorated functions and methods. **Eight checks cannot fire on a private
helper**: the seven that reach the repo through `ctx.function_index` —
`dead-code`, `single-call-site`, `internal-only`, `constant-args`,
`return-none-instead-of-raise`, `pass-through-params`,
`inconsistent-error-handling` — plus `trivial-wrappers`, which carries its own
`startswith("_")` skip at `checks/patterns.py:634`.

Every helper in 5b, 5c and 5d is `_`-prefixed **by design**. That is what let
three commits add ten functions to a file under active pysmelly pressure and
mint nothing.

The two cross-file design checks that **do** still see private functions are
`param-clumps` (`checks/structure.py:381`) and `dict-as-dataclass`
(`callers.py:1263`, via `_collect_dict_returning_functions` at `:1210`), so those
are the two that had to be held inside budget by hand: `_update_service` puts
`(ctx, service_name, task_def_arn)` into a **second** signature only — a third
would mint a clump — and no helper returns a dict literal with 4+ string keys.

**Two corrections to the 5b commit message's version of this rule**, both
re-measured at `64e3e18` against pysmelly 3.2.1.dev15:

- It lists `vestigial-params` among the eight. **`check_vestigial_params`
  (`callers.py:1149`) walks `ctx.all_trees` with no name filter and *can* fire on
  a private helper.** Its neighbour `unused-defaults` (`:70`) is the same. A `_`
  prefix does not buy immunity from either.
- It omits `dead-code`, which *is* a `ctx.function_index` consumer
  (`callers.py:174`) and is blocked. The count of eight happens to be right; the
  membership was not.

#### Independent validation: ACCEPT, no behavioural defect

Over all five commits. Reproduced here at `64e3e18` rather than taken on trust:

- **264/264 pins pass with `git diff tests/` empty** across all three refactor
  commits. Ten functions were extracted out from under the pins and not one
  assertion was edited.
- **Line-level finding diff, `9c95d79` → `64e3e18`: 8 lines vanished, 2
  appeared.** Seven of the vanished are findings (4 × `long-function`, 1 ×
  `arrow-code`, and 2 `param-clumps` lines that reappear renumbered); the eighth
  is the convergence-hotspot summary line for `service.py`. **Both appeared lines
  are line-number reflows** — `aws/ecs.py:92`'s clump re-anchoring its
  `service.py` legs to `:121/:175/:1058`, and `service.py:196 → :197` — so
  **nothing was minted**. Net **−5**, which reconciles 52 → 47 exactly.
- **`fail_under` monotonic across all five commits**: 59 → 59 → 65 → 65 → 65 →
  65\. Never lowered, at any point, including the three refactor commits.
- **Convergence-hotspot list independently re-derived as empty**, max **2** checks
  per file repo-wide.

**Two measurement corrections, recorded rather than silently fixed** — house
style, since the commit messages are in history and cannot be edited:

| Claim                                           | Commit message | Measured by AST at `64e3e18` |
| ----------------------------------------------- | -------------- | ---------------------------- |
| `start_migrations` after 5c (`961be51`)         | 85L            | **84L**                      |
| `_wait_for_service_stable` after 5d (`64e3e18`) | 76L            | **75L**                      |

Both are off by one in the same direction and neither changes an outcome — both
were already well under the 100-line bar — but the register records the measured
value.

#### Latent bugs pinned, not fixed

Continuing the section 53e-3 opened. 53e-5a's pins capture all fourteen as
**current behaviour, not as endorsements**; each is a real defect left for a
subphase that owns the contract.

**From 5a-1 (`b42be87`), the deploy path — nine:**

| Bug                                                                                                                                                                                                                         | Status                                                                                                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| **`service_exists` swallows `ClusterNotFoundException`** and returns `False`, so a **typo in the cluster name takes the CREATE branch** rather than reporting an unknown cluster.                                           | **FIXED by 53j-1** (`752e146`). Raises naming the cluster; deliberately uncaught by `_deploy_one_service`.            |
| **The `ClientError` swallow makes a partial deploy look successful.** A per-service failure is reported and the loop continues; the run still ends as a success.                                                            | **FIXED by 53i-3d** (`600c788`). `deploy_services` collects per-service failures and raises once, naming all of them. |
| **`update_service` carries no network configuration, no load balancer and no launch type**, so a target-group change on an existing service has **no effect** — the parameters are built and never sent on the update path. | Pinned, and **owned by Phase 69**, member 2 — "an instruction is ignored". Not Phase 53 residue.                      |
| **A load-balanced service can be created with no target group** — nothing fails when the lookup yields nothing.                                                                                                             | Pinned. The service comes up and receives no traffic.                                                                 |
| **`port` is read from the raw table while `load_balanced` comes from merged sizing.** Two keys of one decision sourced from two different config layers.                                                                    | Pinned. The kind of split that makes an environment override behave differently from the base.                        |
| **`--dry-run` previews an *update* for a service that does not exist**, and prints **none of the parameters it just built**.                                                                                                | Pinned. A preview that is wrong about the branch and silent about the payload.                                        |
| **`_ensure_az_rebalancing_disabled` indexes `services[0]` unconditionally.**                                                                                                                                                | Pinned. `IndexError` on an empty describe response.                                                                   |
| **An empty-string per-service target group falls through to the default.** `""` is falsy, so an explicit "no target group" reads as "unset".                                                                                | Pinned. Config that cannot express what it looks like it expresses.                                                   |
| **`_get_deployment_config` ignores the dataclass field names and does no range validation.**                                                                                                                                | Pinned. A misspelled key is accepted silently; an out-of-range percentage reaches the ECS API.                        |

**From 5a-2 (`fb561e3`), the migration + wait path — five:**

| Bug                                                                                                                                                                                                                                                         | Status                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`_wait_for_target_group_healthy` can never succeed against an empty target group.** The condition is `healthy > 0 and healthy == total`, which is `False` at **0/0**, so the wait burns the **full 300s** and then reports the wrong diagnosis.           | Pinned. The worst of the five: a five-minute stall that names the wrong cause.                                                                               |
| **`start_migrations` raises a bare `KeyError` on `networkConfiguration`** — the read sits **outside** the `try`, so a service without one produces an unhandled `KeyError` rather than a diagnosis. Preserved verbatim by 5c's `_migration_network_config`. | Pinned, and explicitly preserved through the 5c extraction.                                                                                                  |
| **`_get_deployment_status` raises a bare `KeyError` from `d["status"]`.**                                                                                                                                                                                   | Pinned. Same shape one layer down.                                                                                                                           |
| **`_wait_for_service_and_targets` catches only `DeploymentError` / `RuntimeError`**, so a `ClientError` **escapes the worker and bypasses `ServiceWaitResult`** entirely — the result-object contract is not actually total.                                | Pinned. The failure mode the result object exists to prevent.                                                                                                |
| **`wait_for_migrations` uses `.get("exitCode", 1)`** — a container that stopped **without** an exit code is treated as a **failure**.                                                                                                                       | **Pinned as load-bearing, not as a defect to fix.** This is the safe default and it is easy to "tidy" into `0`. Recorded so the next reader leaves it alone. |

#### Side effects and mints

Nothing cleared as a side effect and **nothing was minted** — the line-level diff
above is the evidence, not an assertion. `service.py` is down to a **single**
finding.

| Finding                                                                                 | Disposition                                                                                                                                                                                                                                                                                |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `param-clumps` — `service.py:197` `(credential_mode, ctx, service_name)` in 3 functions | **Routed to 53g, not a leave-standing.** **Nothing inside `service.py` can clear it**: only one of the three signatures is here (`register_task_definition`); the other two are `task_definition.py:get_environment_variables():140` and `task_definition.py:build_task_definition():383`. |

### 53e arc summary — closeout (2026-08-13)

Five slices, thirteen commits, `805d516` → `64e3e18`.

| Measure                       | 53e-1 start (`805d516`) | 53e-3 start (`a304fa1`) | End (`64e3e18`) |
| ----------------------------- | ----------------------- | ----------------------- | --------------- |
| pysmelly total                | 60                      | **56**                  | **47**          |
| `long-function`               | 9                       | **7**                   | **0**           |
| Files on the hotspot list     | 2                       | 1                       | **0**           |
| Coverage floor (`fail_under`) | 53                      | **54**                  | **65**          |
| Tests                         | 852                     | **891**                 | **1339**        |

**`long-function` is empty as a category** and the **convergence-hotspot list is
empty** — no file in the repo is flagged by three or more checks, and the maximum
is now 2.

The three files 53e-3 → 53e-5 took on, in coverage order:

| File                 | Coverage at split | Now                                                |
| -------------------- | ----------------- | -------------------------------------------------- |
| `deploy/deployer.py` | 35%               | **100%**                                           |
| `deploy/images.py`   | 16%               | **100%**                                           |
| `deploy/service.py`  | 11%               | **99%** — 0 uncovered statements, 1 partial branch |

**The arc's transferable results**, in the order they are worth reusing:

1. **The obvious first extraction was insufficient in all four decompositions**
   (101, 102, 107, 114 — all still flagged). Base rate, not edge case. Measure at
   every stage.
1. **`long-function` kept turning out to be duplication the checker could not
   reach** — 53d-1's three-way resolution, 53d-2a's six no-op guards, 53e-2's
   triplicated audit block, 53e-3b's nine timer conditionals, 53e-5b's three
   sub-threshold assignments. **53e-4b and 53e-5c/5d broke the pattern**: some
   long functions really are linear pipelines and only decomposition helps. Read
   before assuming which kind you have.
1. **Pin first, in a separate commit.** Six subphases ran it; the pins survived
   every code motion untouched because they hold seams **outside** the module
   under refactor (`subprocess`, `builtins.input`, the boto3 client), never a
   `deployer` binding.
1. **Run the checker's internals on hypothetical signatures before writing
   code** (53e-5's `_extract_all_signatures` + `_find_param_clumps` measurement).
   Cheaper than reacting to a mint.
1. **`_`-prefix every extraction.** Eight checks cannot fire on a private helper;
   two design checks still can, and those are the ones to budget by hand.
1. **Coverage is evidence, not a score.** "These two statements are the file's
   only uncovered lines" is how 5d proved a dead branch dead.

**Open after the arc**, all routed and none left standing by 53e-5: the
`dict-as-dataclass` on `deployer.py:38` (53f), the `param-clumps` on
`service.py:197` (53g), and the `temp-accumulators` on `images.py:301`.

### 53f — `dict-as-dataclass` sweep (2026-08-13, backfilled 2026-08-18)

**Backfilled.** 53f shipped in the 2026-08-13 unattended run without an entry
here; this is transcribed from its four commit messages (`b337424`, `942f2d1`,
`995e022`, `b5b8465`) and claude-meta `docs/PLAN-ARCHIVE.md` § 53f, not
re-derived. The commits are near-register-quality and are the primary source.

**Shipped** `64e3e18` → `b5b8465`, four commits. pysmelly **47 → 41**;
**`dict-as-dataclass` 6 → 0, the category is empty.** Nothing minted; every
other per-category count byte-identical at every step. Coverage 65.73% →
68.58%, floor ratcheted **65 → 68**; tests 1339 → 1456.

| Unit  | Scope                                        | Commit    | Total   | Category |
| ----- | -------------------------------------------- | --------- | ------- | -------- |
| 53f-1 | pin the consumers — no production code moves | `b337424` | 47 → 47 | 6 → 6    |
| 53f-2 | `emergency/` payloads                        | `942f2d1` | 47 → 43 | 6 → 2    |
| 53f-3 | `format_user`'s record                       | `995e022` | 43 → 42 | 2 → 1    |
| 53f-4 | `infra_config` → `InfraConfig`               | `b5b8465` | 42 → 41 | 1 → 0    |

#### Pin the *consumers*, not the producers

53f-1 is the pin-first commit, and it inverted the usual target. The producers
were already at 100% behind moto, so botocore validated the real request
shapes. **The consumers were the gap — and a dict → object conversion breaks
consumers, not producers.** Three named gaps closed: `bin/emergency.py`
57% → 76% (`cmd_restore_db` and `_print_restore_success` were entirely
uncovered and hold every subscript read of the restore payloads),
`bin/cognito.py` 17% → 53% (`print_users_table` / `cmd_list` hold all five
subscript reads of `format_user`'s output), `deploy/task_definition.py`
72% → 86% (`_get_legacy_secrets` had no test at all).

#### "Accessed from N files" is not a consumer count — the third instance of the bare-name lesson

The finding on `get_rds_instance_details` said **"accessed from 15 files"**.
The check matches **bare key names**, and that payload's keys are `id`,
`status`, `engine`, `port` — names that appear all over the tree in unrelated
dicts. The real production consumers were **two**, reading **four of ten**
fields. `get_task_definition_details`: "6 files" → **one** consumer, one
field. `format_user`: "10 files" → **one** consumer, five fields.

Same mechanic as 53h-1's `write-only-attributes` under-report and 53g's
`aws/ssm.py:131` false positive, in a **third** check. Grep the real consumers
before sizing the work.

#### NamedTuple vs `@dataclass` is a finding-count decision, not a style one

53f-2 and 53f-3 used `NamedTuple` throughout. Most fields on
`RdsInstanceDetails` and `TaskDefinitionDetails` are genuinely unread in
production — they describe a resource to an operator — so as dataclasses they
would have minted **roughly a dozen `write-only-attributes`**, that check
requiring a dataclass decorator. NamedTuple is structurally immune and gives
`_asdict()` for free.

**Returning the object is what clears the finding.** Returning
`asdict(Thing(...))` would not: `dict-as-dataclass` only suppresses a dict
literal that projects `self` or a dataclass-annotated parameter, so a
re-serialized record still reads as a bare dict literal — the finding would
stand while the code got worse.

53f-4 needed `@dataclass` because `legacy_placeholders()` calls `asdict()`, so
`write-only-attributes` was live. It did not fire: pysmelly's
`_class_serializes_self` exempts a class that calls `asdict(self)` in its own
body. **Verified by experiment rather than assumed** — replacing `asdict(self)`
with an equivalent pysmelly cannot see mints **7** `write-only-attributes` on
`InfraConfig`; restoring it clears all 7. `legacy_placeholders()` is
load-bearing for the finding count as well as for the design.

#### The 18-key dict: eight of the keys are a user-facing contract

`_build_infra_config`'s 18 keys split 10/8. Ten are read by name, all through
`.get()` with a default, so all eleven production reads became attribute reads.
The other eight — `database_url`, `db_host`, `db_port`, `db_name`,
`db_password_secret_arn`, `db_username_secret_arn`, `redis_url`,
`s3_media_bucket` — are read by name **nowhere** in `src/` or `bin/`; they
reach code only through two `.items()` loops.

They are fields under exactly their existing names anyway, because an
application's own deploy.toml writes `DATABASE_URL = "${database_url}"`.
**The contract was verified programmatically, not by eye:** all 8 placeholder
names are fields, all 8 are emitted by `legacy_placeholders()`, and every
`${…}` in `CONFIG-REFERENCE.md`, `sample_deploy.toml` and `README.md` resolves
to a field or a built-in.

`legacy_placeholders()` is the design fix, not a shim: the placeholder table
was previously computed ad hoc in the same eight lines **twice**, and both
`.items()` loops are now deleted. A mapping-surface dataclass
(`__getitem__`/`keys()`/`.items()`) was rejected on 53e-3c's grounds — **a
class whose entire purpose is to be indistinguishable from a dict is
suppression by shape.**

#### Both 53f-1 blockers settled in the open, and one refuted the framing

1. **`health_check_config` — settled by wiring the source, not deleting the
   read.** `service.py` read a key `_build_infra_config` never produced, so
   production always took the `{}` default and the grace period was always 60.
   The key is not invented: `modules/app-in-shared-env/variables.tf` declares
   `var.health_check` with a `grace_period` member defaulting to 60,
   `outputs.tf` exports it, all four `config.toml.example` templates set it,
   and CONFIG-REFERENCE documents the mapping. **Only the last hop was
   missing.** Deleting the read would have meant deleting a tofu variable, an
   output, four templates and two doc tables.
1. **`if infra_config:` — deleted, not re-expressed.** It tested the
   `{**ctx.infra_config, "account_id": …}` spread built four lines above, never
   `ctx.infra_config`, so it was permanently true (coverage's `222→225` partial
   branch confirmed the false arm never fired). **A dataclass instance is
   always truthy, so keeping it would only have relocated the dead branch** —
   which refutes the framing that the conversion had to preserve it.

#### Tests went 1456 from 1461, net −5, every decision recorded

Not absorbed: **−12/+7** because 53f-1's twelve scalar-filter pins existed in
two copies **since they pinned code duplicated in two copies** — now one
implementation, one class of 7, including a new pin that each field is offered
under its own name. Nothing goes unpinned. **−1 inexpressible**: a pin on which
of two competing `account_id` entries won is no longer constructible.
**Half a pin shifted**: a top-level deployment key is now a build-time
`TypeError` rather than silently ignored; case kept, shift commented. **+1**:
`TestHealthCheckConfigIsNeverProduced` → `TestHealthCheckConfigSource`.

The recurring shape across all three units: a missing-key failure moves from a
**read-time `KeyError`** in the middle of half-printed output to a
**construction-time `TypeError`**, which is where a NamedTuple puts it. That
happened three times and each time the pin was kept and its shift commented,
never relaxed.

#### Coverage dropped arithmetically, for the third time in the run

`task_definition.py` reads 84% against 86% with **the same 18 missed
statements** — the conversion deleted 13 covered statements and 14 branches
(both filter loops plus the dead guard), shrinking the denominator. Repo total
68.58%, identical.

#### Latent bugs pinned, not fixed

Called out in 53f-1's comments and left alone: `cmd_restore_db`'s `idx < 0`
guard is unreachable because `str.isdigit()` already rejects a leading minus; a
non-`DBInstanceAlreadyExists` `ClientError` propagates out of `restore-db` as a
traceback; `_get_legacy_secrets` silently drops a secret whose value matches
neither prefix; and both placeholder readers stringify bools Python-style.

#### Side effects and mints

None. `param-clumps` stayed at 7 across all four units, **all pre-existing and
byte-identical** — `legacy_placeholders()` takes no parameters and no
hand-written helper gained one. 53g's `(credential_mode, ctx, service_name)`
clump was untouched, which is what let 53g re-measure against an unmoved
baseline.

### 53g — parameter plumbing: zero code units, by design (2026-08-13, backfilled and re-verified 2026-08-18)

**Backfilled.** 53g shipped in the 2026-08-13 unattended run without an entry
here; its skip list lived only in claude-meta
`docs/investigations/2026-08-13-unattended-run-ledger.md` § "Skipped findings".
Transcribed from that list plus `3fcfe6c`, `83c8890`, `8f4d238`, `bb17c37` and
claude-meta `docs/PLAN-ARCHIVE.md` § 53g — and **re-verified against HEAD**,
which is where the transcription stopped being paperwork (below).

**Shipped zero code units, and that is the correct outcome, not
underperformance.** 0 of 14 `pass-through-params` and 0 of 5 open
`param-clumps` were clearly better fixed. pysmelly **41 → 41**. 53f moved
nothing here — it changed `infra_config`'s *type*, not any signature; the only
effect was a one-line anchor drift.

#### Four corrections 53g made to its own plan entry

1. **PLAN's "nine recommended leave-standing" was INVERTED**, and 53g's own
   brief repeated the inversion. **Five** were recorded with drafted fixes (the
   ones 53b/53c/53d-1 minted, in this file). The **nine were the pre-existing
   ones that had never been individually examined** — so the nine were exactly
   what needed verdicts. 53g greped all nine rather than inheriting a
   recommendation that did not exist.
1. **`preflight.py`'s clump was listed but 53c had already cleared it** with
   `EnvironmentTarget`. The plan entry said so two sentences later and still
   listed it.
1. **Two open clumps were unnamed by the entry** — `modules/__init__.py:85` and
   `bin/cognito.py:214`. In scope by arithmetic, never looked at.
1. **`aws/ssm.py:131` is a false positive, not a design question.** See below.

#### The skip list, as recorded and as re-verified

**Superseded at HEAD by §53l (2026-08-21)**, which re-verified the eleven of
these that are still live. The column below is the `722d50b` state and is kept
as the record of that re-verification, not as the current one; §53l corrects
two of its rationales — `core/ssm_secrets.py:36`/`:49`'s call-site count, which
was already stale when this column was written, and the anchor drift on
`aws/cognito.py:46`, `core/ssm_secrets.py:268`, `deploy/preflight.py:46`,
`aws/cloudwatch.py:50` and `bin/cognito.py:218`.

19 findings in 13 lines (the ledger's prose says "eighteen lines", counting the
whole block including two non-53g entries; the finding count is what matters
and it is **19**). Re-verified 2026-08-18 at `c283f5e`/`722d50b`, per the
standing rule that an escalation from an earlier subphase is re-run before it
is acted on. **Three verdicts had gone stale** — marked ⚠ below.

| Finding (53g anchor)                                                   | 53g's rationale                                        | Re-verified at `722d50b`                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ---------------------------------------------------------------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pass-through-params` `aws/cloudwatch.py:50` ×2 (`log_group`, `limit`) | encodes the ECS log-stream naming convention           | **Holds.** `get_task_logs` exists to build `f"{prefix}/{container}/{task_id}"` and has **two** callers (`deploy/service.py:718`, `bin/ecs-run.py:72`). Inlining duplicates the convention.                                                                                                                                                                                                                                                                                          |
| `pass-through-params` `aws/cognito.py:46` (`user_pool_id`)             | extracts a nested response field                       | **Verdict holds; rationale corrected.** It does extract `data["UserPool"]["Name"]`, but that is not what makes the skip right. One caller (`bin/cognito.py:190`), two-line body — inlining puts a raw `run_aws_json("cognito-idp", …)` back into `bin/`, crossing the boundary 53c established. **The layering is the reason.**                                                                                                                                                     |
| `pass-through-params` `aws/ssm.py:131` (`name`)                        | **false positive**, boto3 method-name collision        | **Holds, confirmed by reading.** The `get_parameter()` it "forwards to" is `client.get_parameter(Name=name)`; `_get_call_target_name` returns `.attr` for an `ast.Attribute`, which bare-name-collides with the module's own `def get_parameter`.                                                                                                                                                                                                                                   |
| `pass-through-params` `core/ssm_secrets.py:31, :44` (now `:32`, `:45`) | encode the SSM path convention                         | **Holds.** `get_path_prefix` has 5 call sites and `get_parameter_path` 3; both wrap `parse_environment` in an f-string that *is* the convention.                                                                                                                                                                                                                                                                                                                                    |
| `pass-through-params` `core/ssm_secrets.py:58` ×3 (now `:59`)          | file-loading adapter, one caller                       | ⚠ **One of the three was stale.** `'environment'` forwarded a parameter nothing read — see below. **Fixed, not re-argued.** The other two (`deploy_toml_path`, `env_config`) hold: one caller, and the adapter's job is parse-then-delegate.                                                                                                                                                                                                                                        |
| `pass-through-params` `core/ssm_secrets.py:250` (now `:264`)           | composes an advice block                               | **Verdict holds; rationale corrected, and this one was drafted and measured** — see below.                                                                                                                                                                                                                                                                                                                                                                                          |
| `pass-through-params` `deploy/preflight.py:45` (now `:46`)             | converts a list into a typed error                     | **Holds.** One caller (`preflight.py:254`); the body turns `validate_environment_config`'s error list into a `PreflightError` carrying a six-line advice block.                                                                                                                                                                                                                                                                                                                     |
| `pass-through-params` `utils/cli.py:208` ×2, `:225`                    | the try/except and the branch are the value            | **Holds.** Two production callers each (`bin/deploy.py` + `bin/resolve-config.py`; `bin/ssm-secrets.py` + `bin/cognito.py`). Also 53b leave-standings.                                                                                                                                                                                                                                                                                                                              |
| `param-clumps` `aws/ecs.py:92`                                         | converting does not clear it; layering forbids the fix | **Holds.** Still 6 functions, 3 in `ecs.py` and 3 in `service.py`. Converting the three `service.py` signatures leaves `ecs.py`'s three firing at `min_occurrences=3`, and `aws/` cannot import `deploy/context` without a layering inversion.                                                                                                                                                                                                                                      |
| `param-clumps` `modules/__init__.py:85`                                | ABC plugin-protocol overrides                          | ⚠ **Already cleared — the entry describes a finding that no longer fires.** 53h-1's `@override` retired it. `:85` is now `collect_all`, a classmethod.                                                                                                                                                                                                                                                                                                                              |
| `param-clumps` `bin/cognito.py:214`                                    | Click parameters, 53d-2a precedent                     | **Holds, with a refinement.** `cmd_create`/`cmd_reset_password` are Click-invoked via wrappers at `:399`/`:438`. Unlike `ecs.py:92`, bundling **would** clear it — the third member (`core/cognito.py:format_welcome_message:97`) alone drops below `min_occurrences=3`. It is still rejected: Click parameters unpacked immediately at the wrapper is the flag-shuffling shape 53c's register rejected and 53d-2a re-rejected. **Rejected on design, not on "it does not clear".** |
| `param-clumps` `core/ssm_secrets.py:77` (now `:84`)                    | fix needs a layering decision                          | ⚠ **Invalidated by 53h-2a.** There is no layering decision: `environment` was a dead parameter. **Fixed** — see below.                                                                                                                                                                                                                                                                                                                                                              |
| `param-clumps` `deploy/service.py:197`                                 | `ctx` already is the extracted dataclass               | **Holds.** Two of the three signatures live in `task_definition.py`; `ctx` is already `DeploymentContext`, so the "extract a dataclass" advice is asking for a second one.                                                                                                                                                                                                                                                                                                          |

#### One finding worth sending upstream

`modules/__init__.py:85` was the `ResourceModule` ABC plugin protocol — the four
`collect()` are overrides, and `cache.py:47` already carried
`# noqa: ARG002 — required by Module interface`. pysmelly deliberately excludes
protocol dunders and Click callbacks from `param-clumps`; **it just does not
detect ABC overrides.** Filed as a pysmelly feature request. 53h-1's `@override`
sweep retired the finding by a different route, which is why it no longer fires.

#### One measurement worth keeping

The `(cluster_name, ecs_client, service_name)` clump *looks* like the textbook
context-object fix, and the context object **already exists**. The clump
pre-check showed converting the three `service.py` signatures leaves `ecs.py`'s
three still firing. Net: churn ~36 freshly-written characterization call sites,
each forced to build an 11-field `DeploymentContext` to test one
`describe_services` call, for **zero finding reduction**. **Measured before
drafting, not after.**

#### The re-verification was not paperwork: three stale verdicts, one code fix

53h-2a deleted the explicit `[secrets]` form, the one that gave each name its
own `ssm:`-prefixed path inline. That left
`get_secrets_from_config`'s `environment` parameter unread — it carried a
`del environment` line saying so — and the deadness propagated up through
`check_secrets_exist`, `check_secrets_drift` and `get_secrets_from_deploy_toml`,
each of which only passed it on. **A 53g finding whose skip verdict a later
subphase invalidated**, which is exactly what the re-run rule exists to catch.

Fixed at `722d50b`: the parameter deleted from all four signatures and its three
call sites. **Measured, not assumed** — 37 → **35**, retiring
`param-clumps ssm_secrets.py:84` (the shared set drops to `(config, env_config)`,
below the 3-parameter threshold) and one of the three `pass-through-params` at
`:59`. Nothing minted; line-by-line diff against the `c283f5e` baseline.

**One trap, found by reading rather than mid-edit.** `bin/ssm-secrets.py:154`
was `_project, env = parse_environment(environment)`, whose only purpose was
producing `env` for the call at `:161`. That `parse_environment` is the **CLI
wrapper at `:56`**, which `sys.exit(1)`s on a malformed name — pinned by
`TestParseEnvironment::test_invalid_name_exits_1`. The call is **kept as an
explicit up-front validation** even though nothing consumes its result now;
dropping it would have let `get_path_prefix` raise `ValueError` out of core
instead of exiting cleanly. Verified by hand:
`check nodashes --deploy-toml <path>` still exits 1 with "Invalid environment
name", not a traceback.

#### `format_missing_secrets_error` — drafted, measured, and still recommended standing

The one rationale that did not survive re-reading on its own terms. 53d-1 and
53g both recorded it as "composes an advice block", which is a description, not
a reason. One caller (`preflight.py:165`); `ssm_put_commands` is separately
shared with `bin/ssm-secrets.py:208`, so the composition might not be earning
its keep.

**Drafted and measured** (diff kept at
`scratchpad/draft-fmse.diff`): inlining the body into `check_ssm_secrets` and
deleting the function gives **37 → 36**, clearing exactly this finding and
minting nothing. The cost, also measured:

- `check_ssm_secrets` gains an 11-line message literal inside a `raise`.
- It **breaks a symmetric pair.** `format_missing_ecr_error` (`images.py:550`)
  has the same one caller, at the adjacent call position `preflight.py:143`,
  and the same role. It is unflagged only because it interpolates `environment`
  into an f-string instead of forwarding it to a helper. Inlining one and not
  the other splits a deliberate pair on a tool artifact.
- It destroys `test_format_missing_secrets_error_uses_the_same_commands`
  (`test_ssm_secrets_cli.py:35`), which pins that preflight's advice and
  `bin/ssm-secrets.py:208`'s advice emit **the same** commands. That
  cross-surface pin is *why* `ssm_put_commands` was split out. Confirmed: the
  draft fails collection with `ImportError`.

**Recommended leave-standing with a corrected rationale**, escalated with the
measured diff rather than a rewritten sentence: *`env_name` is forwarded to
`ssm_put_commands` because a test pins that this message and the CLI's own
advice share those commands; the composition is the assertion.*

#### Side effects and mints

None — zero code units. The one code change recorded above belongs to the
2026-08-18 closeout, not to 53g, and is dated as such.

### Standing inline suppressions

Suppressions adjudicated by an entry above:

| Location                                                           | Check             | Rationale                                             |
| ------------------------------------------------------------------ | ----------------- | ----------------------------------------------------- |
| `modules/db-users/lambda/index.py` `handler`                       | vestigial-params  | `context` is required by the Lambda handler signature |
| `modules/db-on-shared-rds/lambda/index.py` `handler`               | vestigial-params  | same                                                  |
| `modules/db-on-shared-rds/lambda/index.py` `handle_setup_database` | dict-as-dataclass | Lambda return must be a dict for JSON serialization   |

`modules/staging-scheduler/lambda/handler.py` `handler` is a fourth Lambda
`vestigial-params` suppression the table above should have listed and does not.

#### Three of the standing directives suppressed nothing (strip-audit, 2026-09-18)

The 53i-2a claim below — *"the suppressions never stopped working"* — was true
when written and is no longer. A per-directive strip-audit (delete one
directive, re-run `pysmelly . --check <name>`, compare) found three that fire no
finding either way and deleted them:

| Location                     | Check                        | Class                                                                                                                                                                      |
| ---------------------------- | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bin/capacity-report.py:222` | shotgun-surgery              | **Redundant companion.** The check's single finding anchors at `bin/cognito.py`, which carries its own live directive; a non-anchor ignore on a multi-file check is inert. |
| `emergency/checkpoint.py:38` | write-only-attributes        | **Dead.** The check reports nothing repo-wide; `RdsState` is read back through `asdict()` / `RdsState(**rds_data)`.                                                        |
| `utils/links.py:31`          | return-none-instead-of-raise | **Dead.** The check now fires only at `core/config.py` and `init/bootstrap.py`.                                                                                            |

The rationale prose above the `checkpoint.py` and `links.py` directives stayed, demoted to plain comments: it
documents why `RdsState`'s fields have no direct attribute reads, and the
error-contract split (`docs/internal/DECISIONS.md`, "Error Contracts") that
makes `None` an absence sentinel rather than a swallowed failure. The
`re-evaluate-by:` tags went with the directives — there is no longer a
suppression to re-evaluate.

#### The corpus, classified by where the rationale sits (re-measured 2026-08-18)

**The paragraph that stood here from 53c until §53i-2a was wrong in three ways.**
It claimed 22 tracked `# pysmelly: ignore` lines, of which five carried
**neither** a rationale nor a `re-evaluate-by:` tag, and it excused
`aws/rds.py get_status` as correct-but-offset. Re-measured over
`git ls-files '*.py'` by classifying every directive on where its rationale sits:

**The "After" column is wrong, and §53l re-measured it to 7 / 8 / 0 / 5** — a
bare `(re-evaluate-by: …)` tag was counted as a rationale, so five directives in
`utils/logging.py` that carry no reason at all landed in the on-line bucket.
The total, 20, is right.

| Placement                                                | Before §53i-2a | After |
| -------------------------------------------------------- | -------------- | ----- |
| Rationale on the directive line                          | 13             | 13    |
| Rationale on the line(s) directly above, no blank line   | 1              | **7** |
| Rationale detached from the directive by two blank lines | **6**          | **0** |
| No rationale at all                                      | 0              | 0     |
| **Total tracked suppressions**                           | **20**         | 20    |

So: **20**, not 22; **six** detached, not five; every one of them carries
**both** a rationale and a `re-evaluate-by: 2026-11 review` tag; and
`aws/rds.py get_status` was not a different case — it was the sixth instance of
the same defect.

**The cause is exact.** `5074069` wrote all six correctly as a single line:

```python
# pysmelly: ignore return-none-instead-of-raise — existence check, None means "not found"
```

`df01cdb` (2026-08-07, _"Tag pysmelly acceptances with re-evaluate-by"_) split
that line in two and pushed the rationale up, leaving a grammatical fragment
orphaned from the thing it explains — and, because black stabilises two blank
lines before a top-level `def`, nothing ever flagged the result. A commit meant
to improve the record damaged it in six places, and this section then recorded
the damage as absence.

**The suppressions never stopped working.** The directive stayed immediately
above the `def`, inside pysmelly's window, so all four suppressed
`return-none-instead-of-raise` instances were absent from the live count
throughout. This was a documentation defect with **zero effect on the count** —
which is what made it a mechanical unit (§53i-2a) rather than part of 53i-2's
adjudication.

### Remainder — the reconciled adjudication split (rebuilt 2026-08-21 at HEAD)

#### Drift since the close — re-measured at `329a1e6` (2026-09-18)

The tree has moved 99 commits past the pin below, and the finding set moved
with it: **50 findings in 12 categories at `329a1e6`, against 36 in 10 at
`187b2f9`.** Both numbers were measured with pysmelly
`3.4.1.dev2+g67d5d9772` against a `git archive` extract of each tree — one
tool version, two trees — so the delta is code drift, not tool drift.

| Check                        | `187b2f9` | `329a1e6` |
| ---------------------------- | --------- | --------- |
| pass-through-params          | 13        | 14        |
| inconsistent-error-handling  | 9         | 10        |
| param-clumps                 | 5         | 9         |
| foo-equals-foo               | 3         | 3         |
| single-call-site             | 1         | 3         |
| arrow-code                   | 1         | 1         |
| law-of-demeter               | 1         | 1         |
| temp-accumulators            | 1         | 1         |
| duplicate-blocks             | 1         | 0         |
| return-none-instead-of-raise | 1         | 0         |
| env-fallbacks                | 0         | 4         |
| long-function                | 0         | 3         |
| dict-as-dataclass            | 0         | 1         |
| **Total**                    | **36**    | **50**    |

Three checks that reported nothing at the close now report eight findings
between them (`env-fallbacks`, `long-function`, `dict-as-dataclass`), and
`param-clumps` and `single-call-site` have grown. **The attribution table
below covers the 36 findings at `187b2f9` and nothing else.** A finding at
HEAD that is absent from it is unadjudicated — it is not a leave-standing, and
its absence is not a verdict. Re-pinning the split, and adjudicating what has
landed since, is a Phase-53-style pass that has not been run; do not infer it
from this note. Re-derive the current set before scoping anything:
`uvx pysmelly . --more-please`.

**Re-pinned at `187b2f9` (2026-08-25), the Phase 53 close.** §53p applied the
operator's ten verdicts: **the Escalated column is now 0**. Seven escalations
became confirmed leave-standings, one (`single-call-site`
`emergency/checkpoint.py:105`) cleared, and four new
`inconsistent-error-handling` rows on `utils/logging.py` were **surfaced on
purpose** by deleting suppressions that carried a tag and no rationale. The
`aws/cli.py:48` and `utils/environment.py:17` rationales are corrected below,
because 53p-2 changed the caller ratios they cited.

**Prior pin, `411c8f0` (2026-08-24).** §53l re-verified all **25** settled
rows at `983237c`: **0 stale, 22 hold, 3 rationales corrected**, and the
corrections are in §53l, not applied to the table below — the *rows* are
unchanged. **53m and 53n moved no row and no count**; the one effect on this
table is an anchor, `param-clumps bin/emergency.py:427` → **`:458`** (its
members are now `cmd_rollback():458`, `cmd_scale():553`,
`cmd_force_deploy():821`).

**Live total: 36, measured at `187b2f9`** with
`uvx pysmelly . --more-please` —
**the plain `make pysmelly` view truncates to the top ten categories and that
tree had exactly ten, so any drift hid a row.** (It has since: `329a1e6`
carries twelve categories, so the truncated view now drops two outright.) This
table is authoritative for `187b2f9`, not for HEAD — re-pin it before scoping
a subphase from it.

This replaces a table pinned to `a9327ab` at **32** findings — *four* subphases
out of date (53i-2a/b/c, 53i-3a/b/c/d, 53j-1/2/3/4, twenty commits), and so
worse than the `64e3e18` table it itself replaced on 2026-08-18 for being three
out of date. That is the failure mode the 2026-08-18 rebuild existed to end,
and it recurred within three days. **A rebuilt table is a snapshot, not a
fixture**: re-pin it at every closeout, or it decays the same way again.

Every finding below is attributed to either an adjudicated leave-standing
(naming the subphase) or an escalation awaiting the operator. **No finding is
unattributed, and the Open column is empty** — the five rows this table last
listed as "open under 53i-3" were adjudicated in §53i-2c and §53i-3c and are
absorbed below.

| Category                     | Live | Settled | Escalated | Open  |
| ---------------------------- | ---- | ------- | --------- | ----- |
| pass-through-params          | 13   | 13      | 0         | 0     |
| inconsistent-error-handling  | 9    | 9       | 0         | 0     |
| param-clumps                 | 5    | 5       | 0         | 0     |
| foo-equals-foo               | 3    | 3       | 0         | 0     |
| single-call-site             | 1    | 1       | 0         | 0     |
| arrow-code                   | 1    | 1       | 0         | 0     |
| law-of-demeter               | 1    | 1       | 0         | 0     |
| duplicate-blocks             | 1    | 1       | 0         | 0     |
| return-none-instead-of-raise | 1    | 1       | 0         | 0     |
| temp-accumulators            | 1    | 1       | 0         | 0     |
| **Total**                    | 36   | **36**  | **0**     | **0** |

**Nothing at `187b2f9` was open work, and nothing awaited a verdict.** Every
finding live at that commit is an adjudicated leave-standing. This is what
closes Phase 53. It is not a claim about HEAD — see the drift note above.

**Escalated** meant a fix was drafted, measured and handed to the operator
rather than recorded as a self-authored justification; the diffs and their
re-measured costs are in §53i-1 (seven) and §53k (all eight, re-measured at
HEAD). **All eight were decided on 2026-08-25** — seven confirmed as
leave-standings with the operator's reasons in §53p, one
(`emergency/checkpoint.py:105`) inlined by 53p-1.
**Three findings cleared in 53i-1** and left this table:
`single-call-site` `modules/secrets.py:86` and `law-of-demeter`
`init/template.py:26` (both were open under 53i), and `arrow-code`
`cli/ci_deploy.py:181` (one of the three orphans).

#### Settled — 36 adjudicated leave-standings

Anchors are at `187b2f9` (2026-08-25). **Three drifted under 53p-2's
eight-line insertion into `resolve_deploy_toml_or_exit`** and are corrected
here: `utils/cli.py:223`×2 → **`:231`**×2, `:240` → **`:248`**. The
`bin/emergency.py:427` → **`:458`** correction from 53m stands. Earlier anchors
were at `8d5629d`, re-verified unchanged at `983237c` (§53l).

| Finding                                                   | Adjudicated by                                   |
| --------------------------------------------------------- | ------------------------------------------------ |
| `duplicate-blocks` `db-on-shared-rds/lambda/index.py:131` | 53a                                              |
| `param-clumps` `db-on-shared-rds/lambda/index.py:50`      | 53a                                              |
| `pass-through-params` `utils/cli.py:231` ×2, `:248`       | 53b (minted), 53g                                |
| `pass-through-params` `aws/cognito.py:46`                 | 53c (minted), 53g                                |
| `pass-through-params` `core/ssm_secrets.py:268`           | 53d-1 (minted), 53g, operator-confirmed 53i-1    |
| `param-clumps` `bin/emergency.py:427`                     | 53d-2a, 53g                                      |
| `law-of-demeter` `deploy/deployer.py:240`                 | 53e-3                                            |
| `pass-through-params` `aws/cloudwatch.py:50` ×2           | 53g                                              |
| `pass-through-params` `aws/ssm.py:131` (false positive)   | 53g                                              |
| `pass-through-params` `core/ssm_secrets.py:36`, `:49`     | 53g                                              |
| `pass-through-params` `core/ssm_secrets.py:63` ×2         | 53g                                              |
| `pass-through-params` `deploy/preflight.py:46`            | 53g                                              |
| `param-clumps` `aws/ecs.py:92`                            | 53g                                              |
| `param-clumps` `bin/cognito.py:218`                       | 53g                                              |
| `param-clumps` `deploy/service.py:211`                    | 53e-5 routed, 53g                                |
| `return-none-instead-of-raise` `aws/cli.py:48`            | 53i-2c                                           |
| `inconsistent-error-handling` `utils/aws_profile.py:85`   | 53i-2c (false positive)                          |
| `inconsistent-error-handling` `init/template.py:126`      | 53i-2c (callers legitimately differ)             |
| `inconsistent-error-handling` `core/config.py:205`        | 53i-3c (fixed-but-unseen)                        |
| `inconsistent-error-handling` `utils/environment.py:17`   | 53i-3c (fixed-but-unseen), ratio corrected 53p-2 |
| `single-call-site` `init/deploy_toml.py:77`               | **operator-confirmed 2026-08-25** (§53p)         |
| `foo-equals-foo` `bin/init.py:220`                        | **operator-confirmed 2026-08-25** (§53p)         |
| `foo-equals-foo` `bin/init.py:563`                        | **operator-confirmed 2026-08-25** (§53p)         |
| `foo-equals-foo` `config/deploy_config.py:435`            | **operator-confirmed 2026-08-25** (§53p)         |
| `arrow-code` `init/deploy_toml.py:195`                    | **operator-confirmed 2026-08-25** (§53p)         |
| `temp-accumulators` `deploy/images.py:342`                | **operator-confirmed 2026-08-25** (§53p)         |
| `inconsistent-error-handling` `emergency/ecs.py:81`       | **operator-confirmed 2026-08-25** (§53p)         |
| `inconsistent-error-handling` `utils/logging.py:47`       | 53p-3 (unsuppressed and adjudicated)             |
| `inconsistent-error-handling` `utils/logging.py:67`       | 53p-3 (unsuppressed and adjudicated)             |
| `inconsistent-error-handling` `utils/logging.py:72`       | 53p-3 (unsuppressed and adjudicated)             |
| `inconsistent-error-handling` `utils/logging.py:77`       | 53p-3 (unsuppressed and adjudicated)             |

**The bottom five were settled in prose and never posted here.** They are the
rows this table listed as "open under 53i-3" through four subphases after they
had been decided. Their verdicts, transcribed from the sections that took them:

| Finding                                                 | Verdict of record                                                                                                                                                                                                                                                                                                                                                                                                                                   | Where         |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| `return-none-instead-of-raise` `aws/cli.py:48`          | Its `None` means **failure only** — PYTHON.md #19 contract 3, and correct. The defect was one layer down, in `aws/rds.py`, **and 53p-2 fixed it**: `get_status` no longer translates a failure-`None` into "not found". §53l flagged this sentence as past tense for something never fixed; it is now past tense for something done. The check reads **2 of 3** callers guarding since that fix (was 3 of 4) — a ratio the verdict never rested on. | §53i-2c, §53p |
| `inconsistent-error-handling` `utils/aws_profile.py:85` | **False positive.** It raises only inside `if validate:`; the two unhandled callers pass the default and cannot reach it.                                                                                                                                                                                                                                                                                                                           | §53i-2c       |
| `inconsistent-error-handling` `init/template.py:126`    | **Callers legitimately differ — document only.** One `except KeyError` is a fallback *dispatch*; three others want it.                                                                                                                                                                                                                                                                                                                              | §53i-2c       |
| `inconsistent-error-handling` `core/config.py:205`      | Real bug, **fixed by 53i-3c** with `exit_on` — which is a context manager, so the check that asked for it cannot see it.                                                                                                                                                                                                                                                                                                                            | §53i-3c       |
| `inconsistent-error-handling` `utils/environment.py:17` | Same shape: **fixed-but-unseen.** Wrapping five sites moved the tally by one, and that one was a *deleted* site. 53p-2 deleted a sixth — `bootstrap_dir_exists`' redundant guard — taking the read to **6 specific / 10 unhandled** (was 7/9), again by removing a site rather than adding one.                                                                                                                                                     | §53i-3c, §53p |

The last two are adjudicated as **fixed-but-unseen, not as open work**. Taking
a count from either would mean writing a per-caller `try` at every site purely
to be seen — the thing DECISIONS.md § "2026-08-18: Error Contracts" rejected in
writing. §53k measured what that costs on the one finding of this shape that is
still escalated, and the answer is that it does not even buy the count.

**One rationale corrected, by operator decision (2026-08-18).**
`format_missing_secrets_error` (`core/ssm_secrets.py:268`) had stood since
53d-1 on "composes an advice block", which §53g re-read and found to be a
description rather than a reason. 53g drafted the inlining, measured it at
37 → 36 with nothing minted, and recommended the finding stand on a corrected
rationale. **The operator confirmed it**, so the rationale of record is now:

> `env_name` is forwarded to `ssm_put_commands` because
> `test_ssm_secrets_cli.py:35` pins that preflight's advice and
> `bin/ssm-secrets.py:208`'s advice emit the *same* commands. The composition
> is the assertion. Inlining also breaks its symmetric pair,
> `format_missing_ecr_error` (`images.py:550`), which is unflagged only
> because it interpolates `environment` into an f-string instead of
> forwarding it.

#### The 12 claimed by §53a–§53e reconcile to 9 — later subphases cleared three silently

The withdrawn split's "12 adjudicated leave-standings (53a 2, 53b 3, 53c 2,
53d-1 1, 53d-2a 1, 53d-2b 2, 53e-3 1)" was never mapped onto findings that
still fire. Mapped now:

| Claim    | Status at `722d50b`                                                                                                                                 |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| 53a 2    | Both live.                                                                                                                                          |
| 53b 3    | All three live (`utils/cli.py`).                                                                                                                    |
| 53c 2    | Both live — but one is `run_aws_json`, which 53c **routed to 53i** rather than settling. Counted **open** here, so 53c contributes **1**.           |
| 53d-1 1  | Live (`format_missing_secrets_error`).                                                                                                              |
| 53d-2a 1 | Live (`bin/emergency.py:386`).                                                                                                                      |
| 53d-2b 2 | **Both gone.** They were the two print-runs whose remaining leg was `setup_profiles.py`, and **53e-1 cleared both** when it adopted `advice_block`. |
| 53e-3 1  | Live (`deployer.py:219`).                                                                                                                           |

**9 of the 12 still fire.** A thirteenth, unlisted by the split — 53b's
`foo-equals-foo` on `deploy.py:167` `run_deploy_pipeline` — also cleared, by
53c's `DeployOptions`; claude-meta `docs/plan/` already records that one.
The remaining 11 settled findings come from 53g's skip list, re-verified above.

**This is the drift the withdrawn split could not express**, and the reason a
count-only tally is not maintainable: a leave-standing is not permanent, and a
later subphase clears one without ever knowing it was adjudicated.

#### The 12 that were open under 53i, resolved by the 53i split

53i split into 53i-1 (mechanical), 53i-2 (the raise-vs-return policy) and
53i-3 (applying it). Where each of the twelve went:

| Finding                                                                                                                          | Outcome                                   |
| -------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| `single-call-site` `modules/secrets.py:86`                                                                                       | **Cleared** in 53i-1 (`af23287`)          |
| `law-of-demeter` `init/template.py:26`                                                                                           | **Cleared** in 53i-1 (`78c3a6c`)          |
| `single-call-site` `emergency/checkpoint.py:105`                                                                                 | 53i-1 — drafted, measured, escalated      |
| `single-call-site` `init/deploy_toml.py:77`                                                                                      | 53i-1 — drafted, measured, escalated      |
| `foo-equals-foo` `bin/init.py:220`, `:557`, `config/deploy_config.py:379`                                                        | 53i-1 — drafted, measured, escalated      |
| `return-none-instead-of-raise` `aws/cli.py:48` `run_aws_json`                                                                    | **53i-2** — the policy, not a code motion |
| `inconsistent-error-handling` `core/config.py:205`, `init/template.py:126`, `utils/aws_profile.py:85`, `utils/environment.py:17` | **53i-2**                                 |

`run_aws_json` was left unsuppressed by 53c precisely so the repo-wide
raise-vs-return policy lands in front of the operator; it belongs with 53i-2's
corpus of 50 "pinned, not endorsed" markers, not with code motion.

**Two corrections to 53i's scope, made while attributing these.** Its entry
named `config/deploy_config.py` among the *`inconsistent-error-handling`
contracts* — that file carries no such finding; its live finding is the third
`foo-equals-foo`, which the entry counted ("×3") but never named. And the entry
named only `bin/init.py`'s two `foo-equals-foo` explicitly.

#### The 3 owned by no subphase — folded into 53i-1

Surfaced by this reconciliation; they appeared in no skip list and in no
subphase's scope. **The operator folded all three into 53i-1 (2026-08-18)**
rather than leave them unowned:

| Finding                                          | Note                                                                                                                       | Outcome                                                   |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `arrow-code` `cli/ci_deploy.py:181` (depth 5)    | Never in any subphase's scope.                                                                                             | **Cleared** in 53i-1 (`a9327ab`)                          |
| `arrow-code` `init/deploy_toml.py:195` (depth 6) | `_build_environment_config`, the function 53h-2a edited without ever owning this finding.                                  | 53i-1 — escalated; `elif` artifact, feature request filed |
| `temp-accumulators` `deploy/images.py:342`       | 53e-5's closeout listed it as "open after the arc, routed" — and named no destination, unlike its two siblings (53f, 53g). | 53i-1 — drafted, measured, escalated                      |

**No finding is now unowned.** That was the condition 53i was blocked on, and
it is the reason the three were folded rather than tallied.

#### Category notes

**Empty categories**: `long-function` (9 → 0 across 53d–53e),
`dict-as-dataclass` (6 → 0 in 53f), `write-only-attributes` (1 → 0 in 53h-1),
`duplicate-except-blocks` (cleared by 53a and 53b) and
`boolean-param-explosion` (cleared by 53c's `DeployOptions`).

**The convergence-hotspot list is empty.** No file is flagged by three or more
checks. Re-derived at `a9327ab` and **again at `983237c` by §53l, unchanged**,
the repo-wide maximum is **2**, and only two files hold it: `init/deploy_toml.py` (`single-call-site` + `arrow-code`) and
`modules/db-on-shared-rds/lambda/index.py` (`duplicate-blocks` +
`param-clumps`). `init/template.py` dropped to one when 53i-1 cleared its
`law-of-demeter`, `core/ssm_secrets.py` when `722d50b` took its clump, and
`deploy/deployer.py` when 53f cleared its `dict-as-dataclass`.

**`duplicate-blocks` has no open items** — the single remaining finding is
53a's adjudicated Lambda pair. `duplicate-except-blocks` is empty.

### 53h-1 — what `ModuleContext` carries (2026-08-17)

**Shipped** `bb17c37` → `a8d7369`, four commits. pysmelly **41 → 38**;
`write-only-attributes` and `param-clumps`-in-`modules/` both to **0**,
`feature-envy` 2 → 1. Two `# pysmelly: ignore vestigial-params` suppressions
retired. Nothing minted.

53h was split before it was run — the second time in this arc a subphase was
split on planning rather than mid-arc (53e was the first). Reading the code
found **four items pysmelly does not flag**, and one of them changed the
subphase's shape, so what 53h-1 could decide alone was separated from what
needs the answer first. 53h-2 is gated on this slice being re-measured.

#### The pin, first and in its own commit

`60cbfc0`, 67 characterization tests in
`tests/unit/test_module_collect_pins.py`. This was an interface refactor over a
**54%-covered** implementation whose call sites were themselves uncovered —
`database.py`'s entire SSM credential arm (170-185), most of `validate`'s error
arms, `_build_module_context`, and **both** `collect_all` call sites had no
test at all.

Every pin is driven through a seam 53h-1 does not move:
`get_environment_variables`/`get_secrets` take a `DeploymentContext` and return
dicts and lists, and `validate()` takes two dicts. No pin constructs a
`ModuleContext` or calls `collect()` directly, which is 53d-2a's
outermost-boundary rule applied to an interface change: the two things the
refactor alters are exactly the two things the pins must not name. All 67
passed unchanged through all three refactor commits.

| File                        | Before | After                                       |
| --------------------------- | ------ | ------------------------------------------- |
| `modules/database.py`       | 54%    | **100%**                                    |
| `modules/storage.py`        | 78%    | **100%**                                    |
| `modules/cache.py`          | 83%    | **100%**                                    |
| `modules/secrets.py`        | 71%    | 97% — one dead guard, see below             |
| `modules/base.py`           | 82%    | 95% — three `@abstractmethod` `pass` bodies |
| `deploy/task_definition.py` | 84%    | **93%**                                     |

Coverage floor **69 → 70** (70.44% measured), 1477 → 1544 tests.

#### The four findings

| Finding                                               | Disposition                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `write-only-attributes` — `base.py:140` `domain_name` | **Cleared**, along with three sibling fields the check never saw. See the under-reporting mechanic below.                                                                                                                                                                                                                                                                                                                                         |
| `feature-envy` — `database.py:126` `collect()`        | **Cleared.** `ModuleContext.ssm_parameter_arn()` and reading `credential_mode` once take the `context` accesses from 4 to 2, under the check's 3-access threshold.                                                                                                                                                                                                                                                                                |
| `feature-envy` — `database.py:47` `validate()`        | **Survives by design; 53h-2's call.** Measured, not assumed: a `DatabaseEnvConfig` dataclass would **not** clear it, because `env_config.host` is still an `ast.Attribute` Load. Only moving the logic onto the config type, or extracting *module-level* `_`-helpers (`check_feature_envy` only walks `ClassDef` bodies), does. Extracting **methods** would *mint* findings — a helper reading `env_config` 4× with `self` 0× fires on its own. |
| `param-clumps` — `__init__.py:85`                     | **Cleared as a side effect of `@override`, which the plan predicted only bundling could do.** See below — this is the one result worth carrying forward.                                                                                                                                                                                                                                                                                          |

#### `write-only-attributes` under-reports via name collision

Four of `ModuleContext`'s six fields were read by no module. pysmelly flagged
**one**. `_collect_all_attr_reads` (`pysmelly/checks/architecture.py:398`)
builds a **single global set** of attribute names read *anywhere* in the
codebase, so `environment`, `app_name` and `services` were covered by
`DeploymentContext.app_name`/`.environment` and
`DeployConfig.services`/`Checkpoint.services`. `domain_name` had no colliding
reader, which is the only reason it surfaced.

This is `feedback-verify-pysmelly-caller-counts` running **in reverse**: the
same bare-name matching that makes cross-file checks *over*-report callers makes
this one *under*-report dead fields. A `write-only-attributes` finding on a
common field name is a floor, not a count. Candidate for PYSMELLY-REVIEW; filed
in claude-meta GUIDE-BACKLOG under source key `53`.

#### The LSP break the tool could not see

`DatabaseModule.collect` widened the ABC with a fifth parameter
(`credential_mode: str = "app"`) that no other module had, so `collect_all` had
to dispatch on `if module.name == "database"` — a Liskov violation repaired by
a string comparison, and invisible to every check. `credential_mode` describes
the deployment, not one module's parameter list, so it moved onto
`ModuleContext` and the registry now calls one signature for everything.

Validation moved with it, into `__post_init__`, which makes it strictly
**earlier**: a bad mode now raises before any module runs, rather than only when
`[database]` happens to be declared with a `type`.

#### The ARN written three times

`f"arn:aws:ssm:{context.region}:{context.account_id}:parameter{…}"` appeared at
`database.py:182`, `database.py:188` and `secrets.py:119` — below
`duplicate-blocks`' consecutive-statement threshold, so unflagged. It is now
`ModuleContext.ssm_parameter_arn()`, one method on the type that owns both
halves of the ARN. **Fourth time in this arc** that reading a target found
duplication the tool could not reach (53d-1, 53d-2a, 53e-2).

`collect()`'s two credential forks became lookups in `_SECRETSMANAGER_KEYS` and
`_SSM_KEYS`. All eight config keys stay literal in both tables and in
`validate()`, so each is greppable from either end.

#### `@override`, and why the `param-clump` fell with it

**Deployer is the first fleet repo to use `typing.override`** (only pysmelly
itself did). Operator-approved 2026-08-17, deployer-first.

`cache.collect` and `storage.collect` do not use their `context` parameter and
cannot drop it — the ABC dictates the signature. That was two
`# pysmelly: ignore vestigial-params` comments with `re-evaluate-by: 2026-11`
tags. Saying it in the type system instead retires them **legitimately** rather
than silencing them: `_has_interface_decorator` (`checks/callers.py:1133`) skips
`@abstractmethod` and `@override` precisely because such a signature is a
contract. `requires-python` is `>=3.12`, so no backport. The `# noqa: ARG002`
comments stay — ruff has no equivalent skip.

Applied to all **twelve** overriding members (`name`/`validate`/`collect` on each
of four modules), not only the two carrying suppressions. PEP 698 is
all-or-nothing per class; half-decorating reads as an accident.

**Then the `param-clump` cleared too**, which the 53h-1 plan explicitly
predicted only bundling could do. `param-clumps` builds its table from
`_extract_all_signatures` (`checks/structure.py:543`), which applies the *same*
interface-decorator skip. The four decorated `collect()` impls stop contributing
signatures, leaving `collect_all` alone below the 3-function threshold.

Recorded loudly because of what it does to 53h-2: **the
`(app_config, env_config, context)` bundling decision is no longer forced by a
finding.** 53h-2 decides it on merit. The gate must not be read as "the tool
stopped complaining, therefore the signature is fine" — the tool stopped
complaining because 53h-1 told it the signature is a contract, which is true and
is a different statement.

#### Pinned, not endorsed — handed to 53h-2

Three behaviours the pins record as-is, in
`tests/unit/test_module_collect_pins.py`:

1. **`_MODULE_SECTIONS` and the registry disagree.**
   `task_definition.py:16` names `cdn` and `autoscale`; the registry
   (`__init__.py:50`) holds Database, Cache, Storage, Secrets. A deploy.toml
   with `[cdn]` flips `uses_modules` true — **changing which secrets path runs,
   so a legacy `[secrets]` block is silently dropped** — while `validate_all`
   skips the section, meaning `[cdn]` is never validated at all. Conversely
   `secrets` is in the registry but not in `_MODULE_SECTIONS`, and is
   special-cased at `task_definition.py:311`; `get_environment_variables` counts
   it as a module section and `get_secrets` does not. Same "what is a module"
   question as the signature, so it rides with 53h-2.
1. **`collect()` does not re-check what `validate()` rejects.**
   `credentials = "vault"` emits the connection env vars with **no credentials
   at all** — a container that starts and fails to authenticate.
1. **`secrets.collect`'s `if not app_config` guard is dead.** Unreachable from
   production: `collect_all` only calls a module whose section is truthy, and
   `get_secrets`' other route requires a `names` key. Left uncovered rather than
   reached by calling past the seam.

______________________________________________________________________

### 53h-2a — one `[secrets]` style, and the module boundary (2026-08-17)

**38 → 38.** No movement, and none expected: this subphase is correctness, not
findings. It ships anyway because researching 53h-1's three handed-forward
questions found that the first was not an adjudication but **a live bug**.

Commits: `4bc2e9e` (pins), `c31f0f3` (crash fix), `6d34179` (the change),
`36d33d5` (audit-key fix), `62dd547` (docs).

#### The bug

A deploy.toml carrying explicit `[secrets]` **and** any module section silently
dropped every secret:

```
{"secrets": {"SECRET_KEY": "ssm:/app/secret-key"}}              -> [SECRET_KEY]
{"database": {...}, "secrets": {"SECRET_KEY": "ssm:/app/..."}}  -> [DB_USERNAME, DB_PASSWORD]
```

`get_secrets` picked one of three routes from the shape of `config`; the module
route returned only module secrets and never read `[secrets]`. **Nothing caught
it.** Preflight confirmed the SSM parameters existed and passed.
`DeployConfig.get_all_env_var_names` counted the explicit keys as provided, so
the audit passed. The container started without its secrets.

53h-1 pinned this as `TestModuleSectionsRegistryGap`'s second case and labelled
it "a real trap, pinned as it stands today". It is worth being clear that the
pin was written believing `[cdn]` was the trigger, and `[cdn]` is *not*
reachable — `get_raw_dict()` rebuilds from typed fields and drops unknown
sections. `[secrets]` + `[database]` is, because both are typed fields. **The
pinned example was unreachable and the bug was real anyway**; what saved the
finding was pinning the mechanism, not the example.

#### Root cause: two contradictory documented styles

| Style                      | Implemented by        | Documented in               | Written by      |
| -------------------------- | --------------------- | --------------------------- | --------------- |
| `names = ["SECRET_KEY"]`   | `SecretsModule`       | `docs/resources/secrets.md` | nothing         |
| `SECRET_KEY = "ssm:/path"` | `_get_legacy_secrets` | `docs/CONFIG-REFERENCE.md`  | `deployer init` |

Neither document mentioned the other. **Calling the explicit form "legacy" in
the code is what made this hard to see**: it was what the primary reference
taught and what the tool generated. A comment describing a style as legacy is a
claim about the world, and this one was false for eight months.

`names` is canonical on the architecture's own premise (`modules/base.py`):
deploy.toml declares *what the application needs*, config.toml says *how the
environment provides it*.

#### What shipped

- `_get_legacy_secrets` deleted, with the explicit branch in
  `get_secrets_from_config` and the now-dead legacy loop in
  `get_all_env_var_names`.
- **Routing collapsed.** Both readers collect from every declared module
  whenever `env_config` is present, full stop. `collect_all` already skips
  undeclared modules. The drop is unreachable *by construction*, not by a guard
  — which is the only kind of fix worth making for a bug that four separate
  guards let through.
- **`_MODULE_SECTIONS` deleted rather than derived from the registry.** The
  plan's step 5 said derive; deleting is the stronger form of the same goal.
  With no reader consulting the shape of deploy.toml there is no second list
  left to drift. It had named `cdn` and `autoscale`, deleted at `bdb5891`.
- `preflight.check_secrets_style` rejects the explicit form by name, ahead of
  `check_modules` — its own check rather than a bullet inside an aggregated
  list, because it has one fix and deserves to state it.
- `get_secrets_from_config` **raises** rather than answering `{}`. An
  unreadable declaration reported as an empty one is exactly how
  `ssm-secrets.py check` came to advise deleting live parameters (53f/53g).
- The rule lives once, in `modules.secrets.explicit_path_keys` /
  `explicit_path_error`; preflight and `core.ssm_secrets` both ask there.
- `deployer init` emits `names`. Detection is unchanged; only the shape moves.

#### Pin coverage, and what pinning found

`init/deploy_toml.py` was **8% covered** — the lowest of anything the phase
touched — and `_build_environment_config`, the function being changed, had no
test at all. Pinned end to end through `generate_deploy_toml`/`format_deploy_toml`:
**8% → 97%**, floor 70 → 74. `preflight.check_modules` got its first test.

Secret *detection* was pinned separately from the emitted *shape*, read through
a shape-agnostic helper. That was the whole design of the file, and it worked:
the refactor's diff to it is exactly the two shape classes.

**Two live bugs fell out of writing the pins**, both fixed in their own commits
and both the same shape as the one the phase was for — a producer and a consumer
never run against each other:

1. `_read_dockerfile_content` did `svc.get("dockerfile", "Dockerfile")` on a
   dict where `get_compose_services` had already set the key to `None`, so the
   default never fired and `path / None` raised `TypeError`. **Every** ordinary
   docker-compose.yml hit it, and `bin/init.py`'s bare `except Exception`
   reported it as "Error parsing docker-compose.yml". Fixed at `c31f0f3` —
   without it `deployer init` could not be run at all, and the phase's
   verification required running it.
1. The generator wrote `[audit] ignore` while `AuditConfig`'s key is
   `ignore_services`, so the list it derived was warned about and discarded.
   Fixed at `36d33d5`.

`tests/unit/test_init_deploy_round_trip.py` is new and is the point: it drives
`deployer init`'s output through all four readers that disagreed. It found
both.

#### Known consequence, accepted

`bin/ssm-secrets.py check` never loads config.toml, so with the explicit form
gone it can no longer classify anything on its own. **This is not new** — it was
already true for both fleet deploy.tomls, which use `names` — and the advice
block names the two commands that do work. Teaching `check` to load config.toml
is follow-on work, recorded in `docs/PLAN.md`.

#### Left standing

`get_secrets_from_config`'s `environment` parameter is now unread. Removing it
ripples through `check_secrets_exist`, `check_secrets_drift`,
`get_secrets_from_deploy_toml`, `preflight.check_ssm_secrets` and
`bin/ssm-secrets.py`, and would retire the `param-clumps` finding on
`ssm_secrets.py:84`. Kept out of a correctness slice deliberately; it is a
finding-moving change and belongs with the others.

______________________________________________________________________

### 53h-2b — the two signature adjudications (2026-08-17)

**38 → 37.** Commit `aacee1b`; the rejected probe is at `235a215` on
`probe/module-inputs`, unmerged.

#### `database.validate` feature-envy — cleared

11 reads of `env_config` against 1 of `self`. Split into module-level
`_connection_errors`, `_extension_errors` and `_credential_errors`, with the
eight credential-key checks driven off the key table `collect()` already used.
`_SECRETSMANAGER_KEYS`/`_SSM_KEYS` merged into one `_CREDENTIAL_KEYS` keyed by
provider then mode, so "which config key holds what" and "which providers
exist" each have one home. Retires the `# noqa: C901`.

Every 53h-1 pin passes **unchanged** — the messages are byte-identical, which is
the check that this was a restatement removed and not a behaviour change.

**Recorded as the plan required: it clears partly via a mechanic.**
`check_feature_envy` only walks `ClassDef` bodies, so moving the reads to module
level would have cleared the finding with or without the table. Measured and
rejected beforehand: a `DatabaseEnvConfig` dataclass does *not* clear it
(`env_config.host` is still an attribute load), and extracting *methods* mints
new findings. The table is what makes the change worth doing; the clear is a
side effect.

#### `ModuleInputs` bundle — drafted, measured, rejected

Not forced by any finding (53h-1's `@override` cleared the `param-clump`), so
per operator-owns-skips it was drafted and measured rather than argued.

**37 → 43.** It clears nothing and mints six findings:

| Check             | Δ   | Why                                                              |
| ----------------- | --- | ---------------------------------------------------------------- |
| `feature-envy`    | +4  | every `collect()` reads 4 attributes of `inputs` and 0 of `self` |
| `shotgun-surgery` | +2  | `inputs.app_config` and `inputs.env_config` each read in 4 files |

It also broke four tests that call `collect()` with three arguments — the calls
the bundle exists to remove.

The design argument survives the measurement rather than being replaced by it:
`app_config` and `env_config` are per-module slices while `context` is
deployment-wide, so bundling boxes three different lifetimes. The tool sees the
same thing from the other side — **a parameter object that is only ever unpacked
is a parameter list with extra steps.**

Kept as an unmerged branch, not discarded, so it can be re-measured rather than
re-argued.

#### `DeployConfig._get_module_injected_vars` — deleted, not fixed

The fourth place hardcoding module knowledge, and **already wrong**: it claimed
`S3_{NAME}_BUCKET_REGION`, which `StorageModule` has never injected, so the
audit reported that variable as satisfied by nothing.

Fixing the divergence in place would have left the fourth copy. Instead
`ResourceModule.injected_names(app_config)` — **abstract**, not defaulting to the
empty set, because a silent empty is precisely the failure mode above. Each
module answers for itself from deploy.toml alone, since the audit runs before an
environment is chosen and so cannot call `collect()`.
`ModuleRegistry.injected_names` unions them.

New pins check the *property*, not the literal sets: for each module, what
`collect()` delivers under a full config.toml is what `injected_names()`
promised. A module that grows or drops an injected variable now fails a test
instead of misinforming the audit.

Second behaviour change: a `[database]` section with no `type` now promises
nothing, matching `collect()`. The old copy keyed on the section being truthy.

#### 53h-1's other two handed-forward items

- **`collect()` does not re-check what `validate()` rejects** — unchanged. A
  `credentials = "vault"` still emits connection env vars with no credentials.
  Preflight rejects it first; this is a defence-in-depth question, not this
  arc's.
- **`secrets.collect`'s dead `if not app_config` guard** — still dead, and more
  so: 53h-2a deleted the second route into `collect`, so `collect_all` is the
  only caller and it only calls modules with a truthy section.

### 53i-1 — the mechanical residue (2026-08-18)

53i split at planning time into three units — the arc's fourth planning-time
split, after 53d (twice), 53e (up front) and 53h (twice):

| Unit      | Scope                                                      | Status              |
| --------- | ---------------------------------------------------------- | ------------------- |
| **53i-1** | 10 mechanical findings — code motion and adjudication      | **this entry**      |
| 53i-2     | The raise-vs-return policy, written from the pinned corpus | scoped, not planned |
| 53i-3     | Apply that policy across the call sites 53i-2 names        | blocked on 53i-2    |

**Both numbers below are superseded — see §53l.** The corpus is **0** at HEAD,
consumed by 53i-3b/3c/3d as designed; and the measurement recorded here
(`grep -c 53i`) counted *route-to-53i* references rather than
"pinned, not endorsed" markers, which are two different populations.

The split is not about size. 53i-2's corpus is **31** "pinned, not endorsed"
markers across 6 test files, plus 4 `inconsistent-error-handling` findings,
`aws/cli.run_aws_json`, and the 5 inline suppressions carrying neither a
rationale nor a `re-evaluate-by:` tag. None of 53i-1's ten findings touches any
of it.

**The suppression half of that corpus was wrong**, in the way §"Standing inline
suppressions" now records: there are six, not five, and they carry both a
rationale and a tag — detached from the directive rather than missing. §53i-2a
repaired them, which removed them from 53i-2's corpus entirely.

**Corrected while transcribing**: the 53i-1 plan entry said "50 markers across
6 test files" and then listed the per-file counts, which sum to 31
(`test_emergency_ecs.py` 11, `test_emergency_cli.py` 7, `test_emergency_rds.py`
5, `test_extensions.py` 3, `test_init_cli.py` 3,
`test_emergency_cli_restore.py` 2). Re-measured at `a9327ab` — `grep -c 53i` —
the per-file list is exactly right and the total is 31. The 5 untagged
suppressions re-verified at the same SHA and still stand at 5, three of them
`return-none-instead-of-raise`, which is why they belong to 53i-2 rather than
53i-1.

**35 → 32 across three code commits.** Three findings cleared, seven drafted
and measured and escalated. Every draft diff is kept in the session scratchpad.

#### Two findings were not what their category said

Both were read before being scoped, and in both cases the *category* was
misleading — the arc's standing lesson, twice more.

**`single-call-site` `modules/secrets.py:86` is the opposite of an inlining
candidate.** `normalize_secret_name` was 1 of 3 copies of the same
SSM-parameter-leaf-name transform, and the third had its operations reversed:

| Site                                                   | Form                                |
| ------------------------------------------------------ | ----------------------------------- |
| `modules/secrets.py:95`                                | `name.replace("_","-").lower()`     |
| `core/ssm_secrets.py:144` (inline, in a comprehension) | `name.replace('_','-').lower()`     |
| `init/deploy_toml.py:127` `_var_to_ssm_name`           | `var_name.lower().replace("_","-")` |

The third fed the `aws ssm put-parameter --name` hint `deployer init` writes
into a generated deploy.toml; its consumer, `ssm-secrets.py check`, never runs
alongside it. A divergence would have surfaced as init telling the operator to
create a parameter at a path `check` never looks for, then `check` reporting it
EXTRA and advising deletion — the same producer-and-consumer-never-run-together
shape 53h-2a hit three times, and the **sixth** instance of "the real defect is
duplication pysmelly could not reach".

**`arrow-code` `init/deploy_toml.py:199` is an `elif` artifact.** Traced
through the AST rather than eyeballed. At HEAD the depth-6 path is
`L205 For > L206 If > L208 If > L210 If > L214 If > L216 If`, and 208/210/214/216
are all `elif` — one flat five-arm chain. `check_arrow_code` counts each `elif`
as a nesting level, so the chain *alone* accounts for depth 6. The only genuine
nesting is the `CELERY_BROKER_URL` arm at depth 5, of which three levels are the
chain; a reader perceives depth 3. **Feature request filed** in claude-meta
`docs/GUIDE-BACKLOG.md`, source key `53` — the **fourth** "the check's mechanic,
not the code" find in this arc, after 53f's "accessed from N files", 53g's
`aws/ssm.py` bare-name collision and 53h-1's `write-only-attributes`
under-report.

`cli/ci_deploy.py:181` is the opposite and was real: `if max_config_age` →
`if resolved_at_str` → `try` → `if age_hours >` → `if strict / else`.

#### The pin, first and in its own commit (`d0330c2`)

`cli/ci_deploy.py` was the one file 53i-1 touches that sat below the bar — 34%,
with uncovered range `203-256` containing exactly the block to be extracted.
Everything else was 92–100%.

Five pins on the `--max-config-age`/`--strict` gate, driven through the
outermost boundary (`CliRunner` → argv in, exit code and message out), with
`run_deploy_pipeline` stubbed so "did it reach the pipeline" is observable:
fresh config silent; stale warns and still deploys; `--strict` exits 1 *before*
the pipeline; `--strict` alone gates nothing; an unparseable `resolved_at` warns
even under `--strict`.

**Verified they pin**, not merely pass: neutering `if strict:` fails the third.
`ci_deploy.py` 34% → 72%, total 74.23% → 74.91% — headroom bought before
anything moved, against a 0.23-point margin over the floor of 74.

#### The three cleared

**`af23287` — one SSM leaf-name transform, not three (35 → 34).**
`modules/secrets.normalize_secret_name` made canonical; `core/ssm_secrets.py`
calls it inside the comprehension, `init/deploy_toml._var_to_ssm_name` deleted
in favour of a direct call. `init/` → `modules/` is a new import edge but not a
new direction — `core/ssm_secrets.py:7` and `deploy/preflight.py:28` already
import this very module for `explicit_path_keys`.

The order difference was **settled, not normalised away**: `-` is unchanged by
`lower()` and `lower()` never produces `_`, so neither operation can create or
destroy the other's input. Checked exhaustively over all 0x110000 code points in
seven surrounding contexts, including the context-sensitive final-sigma rule —
zero divergences — and pinned as eleven parametrized cases in `test_modules.py`
*against the deleted order*, so reintroducing it would be a no-op rather than a
silent change to secret paths. Hand-verified end to end: `format_deploy_toml`'s
four hint paths are identical to `get_secrets_from_config`'s four lookup paths.

**`78c3a6c` — `template.py` asks for the deployer root (34 → 33).**
`module_dir.parent.parent.parent` hardcoded the distance from
`src/deployer/init/` to the project root. The repo already had the named anchor,
`utils.get_deployer_root()`, used by five call sites; `template.py` was
hand-rolling a sixth. Asking for it *removes* the chain rather than shortening
it. `template.py:30`'s `FileNotFoundError` arm was uncovered and is now pinned —
patching `get_deployer_root` to an empty `tmp_path` is possible **because** of
the refactor, so the fix is what made the arm testable. 94% → 95%.

**`a9327ab` — lift ci_deploy's staleness gate out of `main()` (33 → 32).**
`enforce_max_config_age` is the other half of the pair `print_config_age`
already established two lines above: one reports the age, one enforces a limit
against it. Guard clauses replace the pyramid, leaving the strict/warn choice at
depth 1. Narrowing the `try` changes nothing — `sys.exit` raises `SystemExit`,
never caught by `except (ValueError, TypeError)`. The five pins passed unchanged
across the move, which is what they were for.

**Dropped `# noqa: C901` from `main()`** — the extraction took its complexity
under the threshold, so the suppression was stale. Verified by re-running ruff
with `--select C901` and the suppression gone.

#### Seven drafted, measured, escalated — pending operator

Per the arc's rule: a fix is drafted for every finding, and a skip is escalated
with its measured diff rather than a rewritten rationale. All seven diffs are
kept; none is applied.

**The counts in the "Draft result" column are this unit's own, taken at
`a9327ab` against 32 findings.** Read them as the record of what 53i-1 saw, not
as a statement about HEAD: §53k re-applied all seven at `8d5629d` and re-measured
them against 33. **Three anchors below have been corrected for line drift** —
`bin/init.py:557` → `:563`, `config/deploy_config.py:379` → `:435`,
`deploy/images.py:301` → `:342` — so the table names findings that still fire.

| Finding                                                                         | Draft result                                        | Measured cost of applying it                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------------------------------------------------- | --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `single-call-site` `init/deploy_toml.py:77` `is_likely_secret`                  | **clears**, 32 → 31, tests pass, −9/+4              | The name *is* the policy statement over `SECRET_PATTERNS`/`NON_SECRET_ENV_VARS`; inlining buries it as a 3-line boolean inside a loop, and widens the head of the `elif` chain above.                                                                                                                                                                                                                                                                           |
| `single-call-site` `emergency/checkpoint.py:105` `generate_checkpoint_filename` | **clears**, 32 → 31, tests pass, −22/+1             | Deletes two direct pins. `FILENAME_PATTERN` survives at `test_emergency_checkpoint.py:190` via `create_checkpoint`, but `test_filename_uses_current_utc_date` — the only assertion that the stamp is UTC, not local — has nowhere left to live.                                                                                                                                                                                                                 |
| `foo-equals-foo` `bin/init.py:563` `generate_environment`                       | **clears**, 32 → 31, tests pass, −6/+4              | Byte-for-byte the shape the register already adjudicated for 53b's `deploy.py:167` `timer` ("built conditionally two statements earlier"). **And it widens a `try`**: `get_next_listener_priority` → `get_environments_dir().iterdir()` raises `FileNotFoundError`, which the enclosing `except (ValueError, FileNotFoundError)` would then swallow. Confirmed by running it against a missing directory. A cosmetic finding silently changing error behaviour. |
| `foo-equals-foo` `bin/init.py:220` `_BootstrapInputs`                           | **does not clear** — 32 → 32, and **10 tests fail** | The three locals are `click.prompt` results. Keyword arguments evaluate at the call, which is the last statement, so inlining moves two prompts *after* the Cognito prompts — it does not hide the interaction order, it changes it. The finding only mutates (7 args/3 locals → 5 args/1 local), because `region` arrives from a tuple unpack that cannot be inlined at all.                                                                                   |
| `foo-equals-foo` `config/deploy_config.py:435` `cls()`                          | **clears**, 32 → 31, tests pass, −6/+3              | Moves three `dacite.from_dict(...)` calls into an argument list, splitting each parse from the unknown-key warnings loop that immediately precedes it. The section-by-section "validate keys, then parse" pairing is the readable part.                                                                                                                                                                                                                         |
| `arrow-code` `init/deploy_toml.py:195`                                          | **clears**, 32 → 31, tests pass, +17/−10            | Converts a flat five-arm dispatch a reader sees as depth 3 into a second function plus a `.update()` indirection, and forces reordering `CELERY_BROKER_URL` ahead of `REDIS_URL` to preserve behaviour. Paid entirely to satisfy a check that counts `elif` as nesting.                                                                                                                                                                                         |
| `temp-accumulators` `deploy/images.py:342`                                      | **clears**, 32 → 31, tests pass, +9/−6              | 53e-4b *deliberately* relocated this accumulator into `_cache_tag` and recorded that it did not clear. The comprehension form must evaluate both modifier strings eagerly and filter a tuple of pairs. pysmelly's own message reads "accumulator may be appropriate here". File is at 100%.                                                                                                                                                                     |

#### Side effects and mints

**None.** Each of the three cleared findings was measured against the previous
unit's finding set, not against the run total: 35 → 34 → 33 → 32, with every
other line differing only by line-number drift. No new finding appeared in any
category — notable because extractions have minted `pass-through-params` before,
and `enforce_max_config_age` was the kind of extraction that does it. It takes
three parameters, none of them a pure forward, and its body is well above the
size at which `single-call-site` fires (`print_config_age`, 1 param and 1 call
site, has never been flagged for the same reason).

`.secrets.baseline` drifted once — `ssm_secrets.py` line 90 → 94, same
`hashed_secret`, from the four-line import expansion. Verified as drift before
staging. `make security` was run **after** staging each unit, since
`security-secrets` scans `git ls-files` and silently skips untracked files.

#### Coverage

74.23% → **74.94%**, floor 74. The arc has hit an arithmetic coverage *drop*
three times from extraction shrinking denominators; here the pin-first commit
bought 0.68 points before any production code moved, and no unit gave any back.
Re-measured after each unit, not once at the end.

Both CLI entry points were exercised **by hand** as well as through pins:
`ci-deploy` with stale/fresh/unparseable configs × `--strict`, confirming that
under `--strict` the pipeline is never reached and without it the pipeline runs.

### 53i-2a — the six detached rationales (2026-08-18)

**Zero code units, zero count movement, by design** — the same shape as 53g.
53i-2 was scoped as adjudication; this unit is the mechanical repair that had to
come out of it first, by operator decision.

Reading the corpus before planning 53i-2 found that its stated "5 inline
suppressions carrying neither a rationale nor a `re-evaluate-by:` tag" was wrong
in three ways. The measurement and its cause are recorded in §"Standing inline
suppressions"; this entry records the repair.

#### The repair

Six files, one edit each — the two blank lines between rationale and directive
deleted, so the comment block sits contiguously above the `def`:

| Location                                                    | Check                        |
| ----------------------------------------------------------- | ---------------------------- |
| `init/bootstrap.py:187` `bootstrap_dir_exists`              | return-none-instead-of-raise |
| `core/config.py:293` `get_cognito_user_pool_id_from_config` | return-none-instead-of-raise |
| `core/config.py:355` `get_commands_from_deploy_toml`        | isinstance-chain             |
| `config/compose.py:65` `get_compose_services`               | isinstance-chain             |
| `utils/links.py:31` `get_linked_deploy_toml`                | return-none-instead-of-raise |
| `aws/rds.py:11` `get_status`                                | return-none-instead-of-raise |

#### The single-line form does not fit, and that is a config fact, not a taste call

The plan's first choice was the single-line form the 13 correct suppressions
use. **All six exceed 100 characters in that form**, and `E501` is ignored only
under `bin/*` — `src/deployer/*` is held to `line-length = 100` by both ruff and
black. The budget is arithmetic:

| Check                          | Prefix + tag | Rationale budget | Shortest actual rationale |
| ------------------------------ | ------------ | ---------------- | ------------------------- |
| `return-none-instead-of-raise` | 84 chars     | **16**           | 31 (`utils/links.py`)     |
| `isinstance-chain`             | 72 chars     | **28**           | 37 (`config/compose.py`)  |

So all six took the repo's **second sanctioned placement** — the
`emergency/checkpoint.py:38` shape, rationale on the line directly above with no
blank line — which is why the "adjacent" bucket went 1 → 7 rather than the
"inline" bucket going 13 → 19. The three `bin/` suppressions that do use the
single-line form at ~125 characters are legal only because of that per-file
ignore; they are not a precedent `src/` can follow.

`aws/rds.py` needed a second edit black asked for: with the rationale no longer
separated, the two blank lines that had sat between rationale and directive were
the ones satisfying black's rule for a top-level `def`, and one had to move
above the comment block.

#### Verification

- `uvx pysmelly . --more-please` read **32** before and after, diffed as a
  finding set rather than as a total — identical, line for line.
- The placement classifier over `git ls-files '*.py'` read 13/1/6/0 before and
  13/7/0/0 after. The detached bucket is empty and asserted so.
- `make check` (lint + black + 1668 tests), `make format-docs-check`, and
  `make security` after staging.

#### What this leaves for 53i-2

Nothing. These six were listed in 53i-2's corpus on the strength of a claim that
did not survive re-measurement; the corpus that remains is the 4
`inconsistent-error-handling` contracts, `aws/cli.run_aws_json`, the 31
"pinned, not endorsed" markers across 6 test files, and the `emergency/` sentinel
family. The four suppressed `return-none-instead-of-raise` instances are
adjudicated leave-standings with tags that fall due at the 2026-11 review, not
open policy questions.

### 53i-2b/2c — the policy, and what it decided (2026-08-18)

**Zero code units.** 53i-2b wrote the policy **fleet-wide** in claude-meta's
`best-practices/PYTHON.md` (new Practice 19) and `best-practices/PYSMELLY-REVIEW.md`,
by operator decision; 53i-2c wrote deployer's own ADR,
[DECISIONS.md](DECISIONS.md) "2026-08-18: Error Contracts". Neither touches
code, and the count stayed at 32.

**Why fleet-wide rather than here.** Measured across all 13 repos:
`return-none-instead-of-raise` fires **13** times and
`inconsistent-error-handling` **23**, and **storage-scripts (5+7) and
claude-meta (4+3) each carry more than deployer (1+4)**. A policy written from
this repo's evidence alone would have been drawn from the third-largest sample
and labelled fleet-wide. No repo skips either guide, so the guide applies
everywhere by default.

#### The 5 open findings, now adjudicated

The ADR classifies each, and names the call sites so 53i-3 executes a list
rather than re-deriving one:

| Finding                                                     | Verdict                                                                                                                                                                                                                                                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `return-none-instead-of-raise` `aws/cli.py:48 run_aws_json` | Its `None` means **failure only** — contract 3, correct. The defect is one layer down: `aws/rds.py:12 get_status` translates that failure-`None` into its own documented "or None if **not found**".                                                          |
| `inconsistent-error-handling` `utils/aws_profile.py:85`     | **False positive, no change.** It raises `RuntimeError` only inside `if validate:`; the two unhandled callers pass the default `validate=False` and cannot reach it. The check does not model the flag.                                                       |
| `inconsistent-error-handling` `init/template.py:126`        | **Callers legitimately differ — document only.** `init/environment.py:163`'s `except KeyError` is a fallback *dispatch* to `substitute_optional`, not error handling; `init/bootstrap.py:170/175/179` want the `KeyError`.                                    |
| `inconsistent-error-handling` `core/config.py:205`          | **A caller has a real bug.** Four sites unhandled (`bin/ops.py:504`, `:615`, `:686`, `bin/resolve-config.py:109`) where nine others catch the documented pair; two more catch broad `Exception`.                                                              |
| `inconsistent-error-handling` `utils/environment.py:17`     | **A caller has a real bug.** Six `bin/` entry points turn an actionable `RuntimeError` into a traceback (`capacity-report.py:224`, `cognito.py:72`, `deploy.py:67`, `environment.py:67`, `init.py:501`, `:520`) while `init.py:239/347/457` already catch it. |

Both "real bug" rows take the boundary fix — `utils/cli.py:127 exit_on` around
the single failing call — not a per-caller `try`.

#### Two things the corpus did not contain, found by measuring it

**`emergency/` has eleven sentinel-from-`except` functions, not the twelve the
plan carried.** Enumerated by AST rather than by grep: six queries, four
mutators, and `wait_for_deployment`. The six queries and `wait_for_deployment`
are the dangerous half — a `ClientError` becomes "nothing is there" during an
incident — while the four mutators returning `False` are at least honest.

**`cmd_revert` is a third exit-code swallow that none of the eleven pins
covers.** `bin/emergency.py:742/747`: a failed task-definition update or a
failed scale logs, `continue`s, and the command still prints `Revert completed`
and returns `0`. It is the checkpoint-restore path, so 53i-3 needs a
characterization test there **before** changing it — unlike `cmd_scale` and
`cmd_force_deploy`, nothing pins today's behaviour.

#### The discriminator the plan proposed did not survive verification

53i-2b was to be built on pysmelly's printed `N of M caller(s) guard` ratio as a
triage rule: all callers guard → the `None` contract works; a gap → latent
`AttributeError`s. Checked call site by call site across the fleet, **all four
findings with a gap were measurement artifacts** — every real call site guards.
Four different causes: a compound guard (`if x is None or …`) not credited; a
**docstring** mentioning the function counted as a caller (that one is this
repo's, `aws/cli.py:39`, printed as 3 of 4 when it is 3 of 3); expression-form
guards not credited; and a bare-name collision. The guide ships the ratio as a
work list of call sites to open, never a verdict. This is the **fifth** "the
check's mechanic, not the code" find in this arc, after 53f's "accessed from N
files", 53g's `aws/ssm.py` bare-name collision, 53h-1's `write-only-attributes`
under-report and 53i-1's `elif`-as-nesting.

#### Escalated, not decided

Two items in the ADR change behaviour an operator depends on and are marked
pending sign-off rather than assumed: **(a)** the `emergency/` queries raising
instead of returning a sentinel, and **(b)** exit code `2` for "operator
declined". 53i-3 applies neither until they are confirmed. Everything else in
the ADR follows from PYTHON.md #19 and needs no separate call.

### 53i-3a — pinning the consumers before changing them (2026-08-19)

**Zero production change; the count stayed at 32, and the finding *set* is
byte-identical to `4678c1e`'s.** That is the result this unit was written to
produce: coverage moves, adjudication does not.

| Measure                  | Before | After   |
| ------------------------ | ------ | ------- |
| Tests                    | 1668   | 1724    |
| Total coverage           | 74.94% | 78.43%  |
| `bin/ops.py`             | 24%    | **53%** |
| `bin/emergency.py`       | 76%    | **89%** |
| `bin/resolve-config.py`  | 18%    | 26%     |
| `bin/capacity-report.py` | 87%    | 88%     |
| pysmelly                 | 32     | 32      |

**Why tests-only was a prerequisite and not a nicety.** Every call site 53i-3b
and 53i-3c change in `bin/` was unexecuted by any test. `cmd_revert` — the
checkpoint-restore path, and the ADR's third exit-code swallow — was at **0%**;
so were `cmd_health`, `cmd_maintenance`, `cmd_ecr` and `cmd_incident_start`.
A diff cannot be shown to be behaviour-preserving against code nothing runs.

#### What the pins run, and why it is not a stub

The three `cmd_status()` pins and the `compare_task_definitions` pin drive the
**real** `emergency/` producer against a boto3 client that refuses every call,
rather than stubbing the producer to return its sentinel directly. What is
pinned is therefore the end-to-end "failure reads as absence", not the test's
own shortcut — which matters because 53i-3b changes the producer, and a stub
returning `[]` would keep passing after the fix.

#### The asymmetry the ops.py pins expose

`_print_rds_status` reports an unreadable status **in place** ("Unable to
retrieve status"); `_print_recent_snapshots`, two functions down, does
`if not snapshots: return`, so a denied `describe-db-snapshots` **deletes the
whole "Recent Snapshots" heading** from the report. Same file, same section,
opposite answers. The render-boundary pattern 53i-3b applies is not new — it is
already in this file, applied once.

#### `compare_task_definitions` — the twelfth instance

`emergency/ecs.py:208` has no `except` of its own; it inherits
`get_task_definition_details`'s `None` and answers `{}`. `bin/emergency.py:322`
renders that `{}` as the environment-variable diff shown to the operator
**immediately before confirming a production rollback**, so an unreadable task
definition displays as "this rollback changes nothing". Both halves are now
pinned — the producer in `test_emergency_ecs.py`, the render in
`test_emergency_cli.py`.

#### New file

`tests/unit/test_bin_error_boundaries.py` pins all eleven layer-2 call sites
in one place: the six `get_environments_dir` sites that traceback, the three
`bin/init.py` sites that already catch it correctly (the model 53i-3c follows,
and the ones the fix must leave alone), `resolve-config.py:109`, and
`bin/deploy.py:79`'s broad `except Exception` swallowing a `TypeError` from
inside the resolver as "Failed to load deployment config". The three
`bin/ops.py` sites are pinned in `test_ops.py` with the rest of that file.

### 53i-3b — the producers raise, the consumers catch where they render (2026-08-19)

**Layer 1 of the ADR, applied.** The six `emergency/` queries and
`wait_for_deployment` raise `RuntimeError` from `except ClientError`, chained
with `from e` and naming the resource; the four mutators keep their
`False`/`None` with `Returns:` now saying it is a **failure** sentinel and
`Raises:` saying `BotoCoreError` was never caught. Every changed function's
docstring states which of PYTHON.md #19's three contracts it has.

| Measure            | 53i-3a | 53i-3b |
| ------------------ | ------ | ------ |
| pysmelly           | 32     | **33** |
| Tests              | 1724   | 1727   |
| Total coverage     | 78.43% | 78.46% |
| `emergency/ecs.py` | 100%   | 100%   |
| `emergency/rds.py` | 100%   | 100%   |

#### The count went **up**, and the new finding is the honest kind

`inconsistent-error-handling` gained
`emergency/ecs.py:81 get_all_services_state` — "3 callers: 1 catch specific
(RuntimeError), 1 catch broad Exception, 1 unhandled". It fires **because** the
fix made three different boundaries visible, and the three are right to differ:

| Caller                                | Handling              | Why                                                         |
| ------------------------------------- | --------------------- | ----------------------------------------------------------- |
| `ops.py _print_ecs_sections`          | `except RuntimeError` | Read-only report: say so in place, keep printing RDS below. |
| `ops.py cmd_incident_start`           | `except Exception`    | Must never fail; records the reason in the incident file.   |
| `emergency.py _load_cluster_services` | unhandled             | Destructive; propagates to the one boundary `exit_on`.      |

**The fix was drafted and is not worth taking.** Silencing the check means all
three catching `RuntimeError`: `_load_cluster_services` would grow a
per-caller `try` that does exactly what the boundary already does — the thing
the ADR explicitly rejected — and `cmd_incident_start` would have to narrow,
so a non-`RuntimeError` from `load_environment_config` would abort the incident
start. That is a regression traded for a count. **Left standing, unsuppressed,
for the operator**; it is the same "callers legitimately differ" shape already
adjudicated for `init/template.py:126`.

#### Two things the fix taught

**A sentinel that can no longer be produced is dead code, and the type says
so.** Once `get_task_definition_details` raised, it had no `None` left to
give — `describe-task-definition` answers with a task definition or a
`ClientError`, never an empty response. Its return type stopped being
`| None`, and `compare_task_definitions`'s `if not details1 or not details2: return {}` guard went with it. Coverage caught this before the review did: the
line went from covered to unreachable, dropping `emergency/ecs.py` from 100% to
99%.

**AWS distinguishes absence from failure, and using that is the whole point.**
`get_rds_instance_details` keeps `None` — but only for
`Error.Code == "DBInstanceNotFound"`, matched by code the way
`_handle_restore_error` already matched `DBInstanceAlreadyExists`. Everything
else raises. That single branch is what keeps `_prepare_restore`, both restore
entry points and `cmd_restore_db`'s "Failed to initiate restore" meaning
exactly "there is no such source instance". Without it the whole `None` chain
would have become dead code and the message a lie.

#### `wait_for_deployment` raises on the *first* poll, not at the timeout

The ADR called this the worst of the eleven. The alternative considered was to
keep polling and raise only if no poll ever succeeded — which tolerates a
transient throttle. Rejected: **botocore has already retried a throttled
request** by the time a `ClientError` reaches this code, so the first one is
real, and making the operator wait out a 300-second timeout during an incident
to learn they lack a permission is the defect in a different costume.

#### Test-marker hygiene

The flipped pins cite **`DECISIONS.md § "2026-08-18: Error Contracts"`**, not a
phase number. A phase number is ephemeral and its meaning is gone once the arc
closes; the ADR is the standing record. This also keeps `grep -c 53i tests/`
honest as a measure of *pending* markers rather than of prose.

### 53i-3c — the boundary rule, and the corrected exit-code ladder (2026-08-19)

**Layers 2 and 3 of the ADR, applied together** because they touch the same two
files and rest on the same pins.

| Measure                 | 53i-3b | 53i-3c  |
| ----------------------- | ------ | ------- |
| pysmelly                | 33     | **33**  |
| Tests                   | 1727   | 1731    |
| Total coverage          | 78.46% | 78.72%  |
| `bin/emergency.py`      | 89%    | **90%** |
| `bin/resolve-config.py` | 26%    | **34%** |

#### The finding will never clear, and that is the honest answer

`exit_on` is a **context manager**, and `inconsistent-error-handling` looks for
`try`/`except`. Five call sites were wrapped in `exit_on(RuntimeError)` and the
`get_environments_dir` tally moved from "17 callers, 10 unhandled" to "16
callers, 9 unhandled" — the single site that moved is the one that was
**deleted**, not any of the five that were fixed.

So the ADR's chosen fix is invisible to the check that prompted it. Taking a
count from this finding would mean writing a per-caller `try` at every site
purely to be seen — the thing the ADR rejected in writing. **Both remaining
rows are adjudicated as fixed-but-unseen**, not as open work. This is the
**sixth** "the check's mechanic, not the code" find in the arc, after 53f's
"accessed from N files", 53g's bare-name collision, 53h-1's
`write-only-attributes` under-report, 53i-1's `elif`-as-nesting and 53i-2b's
`N of M caller(s) guard` ratio.

#### One of the ADR's eleven sites was not a defect

It recorded `bin/resolve-config.py:109` as a traceback "in the CI config
resolver". Measured: `resolve_config()` is a library function with a documented
`Raises:`, and `cli()` **already** caught `FileNotFoundError`, `RuntimeError`
and `ValueError` around it — exit 1, clean message, no traceback, verified by
running it. The check keys on the immediately enclosing function rather than on
the boundary, which is exactly the mechanic that made `utils/aws_profile.py:85`
a false positive. Both halves are now pinned in
`test_bin_error_boundaries.py`.

#### The eleventh site had no `exit_on` to add

`bin/init.py:520` called `get_environments_dir()` a second time only to name,
in a log line, the directory the function had already been handed.
`env_path.parent` is the same value and cannot fail. **The site is gone rather
than guarded** — the cheapest fix in the unit, and the only one that reduced
the caller count.

#### The exit-code ladder, verified by hand

`EXIT_DECLINED = 3` lives in `utils/cli.py` with the reason on it. The prompt
family (`prompt_or_exit`, `confirm_action`, `select_index`) is reached **only**
from `bin/emergency.py`, so Ctrl-C at a prompt now carries the same status as
typing "n" — a decline reported two ways would have been the same defect in
miniature. Exercised against a real `havoc-staging`:

| Path                                     | Status      |
| ---------------------------------------- | ----------- |
| `ops status havoc-staging`               | `0`         |
| `emergency rollback … --service nope`    | `1`         |
| a denied ECS read through `_run_or_exit` | `1`         |
| `emergency rollback --bogus-flag`        | `2` (Click) |
| `emergency rollback …` answered "n"      | **`3`**     |

#### Three commands had the same shape, and it cost three new smells

`cmd_scale`, `cmd_force_deploy` and `cmd_revert` all attempt every service,
keep going past a failure, and report once — and all three ended with an
unconditional `return 0`. The first fix introduced three `failed = []`
loop-and-append accumulators, and pysmelly caught all three (32 → 36). They
collapsed into a per-service predicate plus one comprehension each, feeding a
shared `_report_outcome`. **The count came back to 33 because the check found
the mess the fix made** — the intended use of a run mid-unit rather than at
the end.

`_report_outcome` takes the printed summary as a parameter rather than deriving
it. "Force deploy **initiated**" is accurate and "Force deploy completed" would
not be: tasks are replaced over the following minutes. Three previously
unasserted success strings are now pinned, because the first version of the
helper silently changed all three.

### 53i-3d — the `except Exception` misattribution family (2026-08-19)

**Layer 4 — the one the ADR did not decide**, added by operator decision.
Layers 1-3 are all "an error is reported as absence". This is the adjacent
shape: an error is reported as *a different error*, so the operator is sent to
fix something that is not broken.

| Site                         | Was                                                           | Now                                                                        |
| ---------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `deploy/deployer.py:223`     | `except Exception` → a **clean** `InfraStatus()`              | `ClientError`/`BotoCoreError` → a non-critical warning naming the instance |
| `deploy/extensions.py:148`   | `except Exception` → "check your credentials and network"     | `BotoCoreError` only — which *is* that family                              |
| `bin/init.py:412`            | `except Exception` → "Error parsing docker-compose.yml"       | gone; the `ValueError` arm stays and anything else keeps its traceback     |
| `deploy/images.py ecr_login` | `RuntimeError("ECR login failed")`, both streams to `DEVNULL` | stderr captured and carried in the message                                 |
| `deploy/service.py:431`      | logged, loop continued, **run ended as a success**            | failures collected per service and raised once, naming all of them         |

#### The one with real blast radius

`check_infrastructure_status` returned `InfraStatus()` — the *healthy* answer —
for any exception at all, so a credentials failure was indistinguishable from
"RDS is available" and the deploy proceeded on that reading. It is now a
warning, and deliberately **not** critical: a pre-flight check that cannot run
must not block a deploy by itself. The distinction the fix preserves is between
"I checked and it is fine" and "I could not check" — the same distinction
layer 1 is about, one subsystem over.

#### Where the family differs from layer 1

Two of these five are **not** "make it raise":

- `deployer.py` still returns a value, because its caller's whole job is to
  weigh warnings. What changed is that the value stopped lying.
- `service.py` still catches per-service, because stopping halfway through a
  deploy leaves a worse state than finishing. What changed is that
  `deploy_services` now raises **once, at the end, naming every service that
  failed** — and that the `if not image_uri: continue` skip one branch up,
  which produced the identical "service silently not deployed", counts as a
  failure too.

The rule is not "raise more". It is **catch what you can attribute, and
re-raise or report the rest as itself.**

#### The three-command shape, a third time

Extracting `_deploy_one_service` out of `deploy_services` was not tidying: the
first version of the fix put a fourth `failed = []` loop-and-append accumulator
in the codebase and pysmelly caught it, exactly as it caught the three in
53i-3c. The same predicate-plus-comprehension answer applied, and the function
went from a 45-line loop to a 5-line one.

#### Closing measurements for the 53i-3 unit

| Measure                      | `4678c1e` | 53i-3d closeout |
| ---------------------------- | --------- | --------------- |
| pysmelly                     | 32        | **33**          |
| Tests                        | 1668      | **1735**        |
| Total coverage               | 74.94%    | **78.73%**      |
| `grep -c 53i tests/unit`     | 32 lines  | **1**           |
| `deploy/deployer.py`         | 100%      | 100%            |
| `deploy/extensions.py`       | 100%      | 100%            |
| `deploy/images.py`           | 100%      | 100%            |
| `deploy/service.py`          | 99%       | 99%             |
| `emergency/ecs.py`, `rds.py` | 100%      | 100%            |

**The one surviving `53i` marker is `tests/unit/test_modules.py:304`**, and it
is not a pending marker: it is provenance for 53i-1's completed consolidation
of three copies of the secret-name transform, naming the subphase whose record
explains why the deleted operation order was equivalent. Every "pinned, not
endorsed — 53i" marker is gone; the flipped assertions cite
`DECISIONS.md § "2026-08-18: Error Contracts"` instead, because a phase number
stops meaning anything once the arc closes.

#### The net count, honestly

**32 → 33.** One finding was added (`emergency/ecs.py:81`, adjudicated in
§53i-3b as three boundaries that are right to differ) and none was removed —
because the two `inconsistent-error-handling` rows this unit set out to fix
are fixed in a form the check cannot see (§53i-3c). Every other finding in the
set is byte-identical to `4678c1e`'s apart from line-number drift. **This unit
was never going to move the count downward**, and the plan said so before it
started; what it moved is 3.79 points of coverage and eleven places where the
tool told an operator something untrue.

### The latent-bug ledger, reconciled (2026-08-19)

53e-3a, 53e-4a and 53e-5a pinned **14 latent bugs** across three tables above,
each "current behaviour, not an endorsement". Reconciled at `b62a0f9`:

> **This section reconciles three tables of five, and saying so was not
> enough.** §53f's four and §53h-1's three sat unswept until 53m/53n because a
> reader checking for completeness found a heading that said "reconciled" and a
> total that balanced. The complete population is in
> § "The pin ledger, reconciled across all five populations".

| State                                         | Count  | Where                                                         |
| --------------------------------------------- | ------ | ------------------------------------------------------------- |
| **Fixed** by 53i-3d as a by-product           | 3      | rows marked FIXED above                                       |
| **Owned by Phase 69** ("instruction ignored") | 1      | `update_service` carries no network config / LB / launch type |
| **Unowned residue**                           | **10** | **10 of 10 done** — the 53j arc, closed 2026-08-21            |

**Closed 2026-08-21.** All **10 of 10** unowned residue rows are FIXED: 53j-1
and 53j-2 took five (`752e146`, `0760a27`), 53j-3 took four (`d306897`,
`3e1c474`), and **53j-4a took the last one** (`630740c`) once the operator made
the display-contract decision the row had been waiting on. **The 53j arc is
closed.** See §53j-1 / 53j-2, §53j-3 and §53j-4 below.

53j-4b — the preflight overlap check — is **additive scope the decision
justified, not a ledger row**. It closes nothing in the table above.

**The three 53i-3d fixed were never planned as 53i-3's work.** They fell out
of applying layer 4 of the error contract, which is the argument for keeping a
pin ledger at all: a pin written in 53e-3a in one subphase is what let a
53i-3d commit three subphases later be recognised as closing it. Without the
ledger the fixes would have landed unattributed and the rows would still read
"Pinned".

**Re-verify before acting on any row here.** These four moved without anyone
touching the ledger — see the standing lesson on stale escalations.

### 53j-1 / 53j-2 — five latent bugs, all pinned, none flagged (2026-08-20)

**The first unit in the arc measured by a diff rather than a total.** Every
member is pinned by a test asserting today's behaviour and **carries no
pysmelly finding** — no check will ever surface one. The count was **33** at
`f9a5843` and **33** at `0760a27`, and the finding set is identical line for
line (only line-number shifts from the edits). That is the result, not a
disappointment: measured between the two commits as well as at the end, exactly
because 53i-3c and 53i-3d each caught an accumulator mid-unit that way.

Two commits: `752e146` (53j-1, a failure reported as a usable answer),
`0760a27` (53j-2, a crash whose message names nothing). Scope was **5 of the
10** unowned residue rows above, taken by shape. 53j-3 took the next four (see
below); 53j-4 took the tenth and last (see §53j-4), once the operator made the
display-contract decision it was waiting on.

**No pin-first commit, for the first time in the arc.** Every prior subphase
touching production code opened with a tests-only commit because the target was
uncovered. Measured at `b62a0f9`: `images.py` **100%**, `deployer.py` **100%**,
`service.py` **99%** — and both affected consumers covered too, `service_exists`
having exactly one production caller and `print_environment_config` one. Every
fix landed as a visible diff against an existing pin. Recorded because pin-first
had been right six times running: **skipping it was a measurement, not a
judgement call**, and the measurement is repeatable.

#### 53j-1 — the answer was wrong, not missing

| Fix                                                                                                                                                       | Discriminator                                                                                                                                                                                  |
| --------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `service_exists` raises `RuntimeError` naming the cluster, chained `from e`, instead of answering `False` for every `ClientError`                         | `describe_services` answers a genuinely absent service with an **empty `services` list**, not an error. So every `ClientError` reaching that `except` is a failure and **none** is an absence. |
| `_resolve_image_spec` resolves the context through a new `_resolve_context`, which raises naming the image and the resolved path if it is not a directory | `rglob` over a missing directory yields nothing, so the image took the digest of the empty string (`e3b0c44298fc`) and `docker build` then failed against a path that does not exist.          |

The `service_exists` discriminator is **the same one 53i-3b used for
`emergency/`** — "does the API distinguish absence from failure?" — arrived at
independently, one subsystem over. That is now three uses of it in this arc.

**Where the raise must *not* be caught.** `_deploy_one_service` deliberately
does not catch it. A cluster-level failure is not per-service: every service
would fail identically, so 53i-3d's per-service collector would turn one bad
cluster name into N identical failures. It propagates through `deploy_services`
to `deploy()` and aborts the run. `test_a_mistyped_cluster_aborts_instead_of_creating_services`
pins both halves — no `create_service`, and no `Failed to deploy service(s)`.

**Why the context check is not in `compute_context_hash`.** ~20 direct pins on
that function use real `tmp_path` directories, so putting the check there would
have left them all untouched — and would have put a policy decision inside a
pure hasher. `_resolve_image_spec` is where `source_dir / context` is built, and
it needed `image_name` threaded through it for the `build_args` fix anyway: one
threading change served both.

#### 53j-2 — the message named nothing

| Fix                                                                                                                               | Note                                                                                                                                             |
| --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `build_args.<environment>` scalars rejected identically on both arms, via one shared `config.merge_build_args`                    | Operator decision; **ADR in [DECISIONS.md](DECISIONS.md) 2026-08-20** because it changes what a `deploy.toml` may contain.                       |
| `print_environment_config` stringifies once at the top of the loop body                                                           | Display-only: `task_definition.py` already `str()`s the same value, so the deploy always handled `[environment] MAX_WORKERS = 4` correctly.      |
| `DeploymentTimer`/`NullTimer` both answer a public `in_step`; `NullTimer` gains a no-op `sub_step`; `get_timer`/`set_timer` widen | Fixed at the **reach** — `timer._current_step` — not at the symptom. `NullTimer` never reaches the global today, so this is a trap, not a crash. |

**The `ImageConfig` arm is a real path, not dead code.** `deployer.py` passes
`get_raw_dict()` so `build_and_push_images` always takes the dict arm, but
`ImageConfig.get_build_args` is live through `validate_ecr_repositories` and
`core/audit.py`. Both arms had to agree, which is why the fix reaches into
`config/deploy_config.py` rather than staying inside `images.py`.

**`print_environment_config` is not in the register** and never was: the crash
is display-only, and the fix cannot regress the deploy. Named here so the next
reader does not go looking for a finding that explains it.

**The masking rule is untouched.** It is wrong in both directions — `BASE_URL`
and `MONKEY_BUSINESS` are masked, a real secret under `PUBLIC_HOSTNAME` is not
— and that row stays **Pinned** below. It is 53j-4, and it is a display-contract
decision for the operator, not a refactor. *(Taken 2026-08-21: the masking was
deleted. See §53j-4.)*

#### Verification, and the one step the plan got wrong

`make check` (1741 tests), `make format-docs-check` and `make security` all
pass, security **after staging** since `security-secrets` scans `git ls-files`.
Coverage floor 74; `images.py` and `deployer.py` stayed at **100%**,
`service.py` at **99%**, and `timing.py` rose **78% → 80%** (the last gap in the
changed area, `DeploymentTimer.sub_step`'s outside-a-step guard, is now pinned
as the contrast `NullTimer.sub_step` deliberately does not copy).

**Exercised by hand against `havoc-staging` with `deploy --dry-run`**, which
proved three of the five and disproved the plan's fourth claim:

- a typo'd `context` aborts with `Image 'transcoder': build context '/Users/borwick/code/havoc/transcodr' is not a directory.`
- `build_args.staging = "oops"` aborts with the ADR's message, naming
  `transcoder` and the key.
- `MAX_WORKERS = 4` / `RELOAD = false` print as `4` and `False` instead of
  raising.
- **The cluster typo cannot be exercised by dry-run at all.**
  `_deploy_one_service` reads `service_exists(...) if not ctx.dry_run else True`
  — so a dry run never calls it, and previews an *update* for every service.
  That short-circuit is itself one of 53e-5a's still-pinned rows ("`--dry-run`
  previews an update for a service that does not exist"). What a dry run *does*
  show is the **preflight cluster check** catching the typo first, with
  `--skip-cluster-check` off; the raise is the backstop for when it is skipped,
  and it is proven by the moto-backed pin, which raises a real
  `ClusterNotFoundException`.

The lesson generalises: **a dry-run is only a witness for code a dry-run
runs.** Checking that before writing "exercise by hand against X" into a plan is
cheaper than finding out afterwards.

#### Side effects and mints

Nothing minted, nothing cleared. `_merge_build_args` was **deleted** from
`images.py` rather than fixed in place — the duplicate was the bug — so the
module lost a function and gained a smaller one (`_resolve_context`).

One thing was **noticed and left alone**: a config-error `RuntimeError` from
this path surfaces as a full traceback, because `pipeline.py` only translates
push errors and re-raises the rest. That is pre-existing and identical for every
other `RuntimeError` `images.py` raises, including 53i-3d's `ecr_login` one.
Out of scope for 53j; captured as an idea rather than fixed here.

### 53j-3 — the cache-tag hash inputs, four members in one file (2026-08-20)

**The four remaining `images.py` rows, taken as one unit** because fixing them
separately means re-deriving what the cache tag should hash four times. Like
53j-1/2, **none carries a pysmelly finding**, so the result is a **diffed
finding set**, not a total: **33** at `b1e3db7`, **33** at `d306897`, **33** at
`3e1c474`, identical modulo line-number shifts. `images.py`'s only finding
throughout is the standing `hash_modifiers` `temp-accumulators`, which this unit
does not touch.

**No pin-first commit, again for the same measured reason.** `images.py` was at
**100%** at `b1e3db7` and all four members were already pinned in
`tests/unit/test_deploy_images.py`; `tests/unit/test_images.py` does not touch
these functions. Every fix landed as a visible diff against an existing pin.
Coverage stayed at **100%** — the new `_context_files` helper is exercised by
every `compute_context_hash` pin.

Two commits, split by whether a digest can move:

| Commit             | Fix                                                                                                  | Digest             |
| ------------------ | ---------------------------------------------------------------------------------------------------- | ------------------ |
| `d306897` (53j-3a) | `should_ignore`'s trailing `fnmatch(rel_str, pattern)` deleted, with its now-unused local            | Cannot move        |
| `d306897` (53j-3a) | `parse_dockerignore` returns `list(dict.fromkeys(patterns))`                                         | Cannot move        |
| `3e1c474` (53j-3b) | `_context_files` excludes the selected Dockerfile from the walk; the `Dockerfile:` prefix hash stays | Moves one image    |
| `3e1c474` (53j-3b) | `_context_files` excludes `.dockerignore` from the walk                                              | Moves the same one |

**The split is the point.** 53j-3a's two members are digest-neutral *by
argument*: the dead `fnmatch` can only fire when `rel_path.parts` is empty —
i.e. when the path *is* the context root, whose relative path is `"."` and whose
`.parts` is `()` — and `rglob` never yields the root; de-duplicating patterns
changes nothing because `should_ignore` short-circuits on the first match and
the pattern list is not itself a hash input. **Both re-measured rather than
asserted**, before and after: havoc `446952b9b7a2`, cantaloupe `5ec6bfaec77d`,
transcoder `c074ce44013a`, all unchanged.

#### The blast radius decided the recipe

The written plan proposed hashing a **digest of the parsed patterns** in place
of the raw `.dockerignore`. Measuring the two candidates against havoc's three
real contexts overruled it:

| context      | before         | exclude-from-walk | patterns-digest |
| ------------ | -------------- | ----------------- | --------------- |
| `havoc`      | `446952b9b7a2` | `446952b9b7a2`    | *changes*       |
| `cantaloupe` | `5ec6bfaec77d` | `5c3ff5cc515e` \* | *changes*       |
| `transcoder` | `c074ce44013a` | `c074ce44013a`    | *changes*       |

The patterns-digest column is deliberately not given as digests: what it lands
on depends on the framing bytes chosen for the pattern list, which is arbitrary.
What is not arbitrary is that it moves **all three** — necessarily, since it
feeds every context a hash input it did not have before, `.dockerignore` or not.
So a patterns digest triples the churn for no benefit. **Every context that has a
`.dockerignore` already self-ignores it** — havoc's lists `Dockerfile*` and
`.dockerignore`, transcoder's lists `Dockerfile` and `.dockerignore` — and a
pattern change that actually matters already reaches the digest **through the
file set it selects**. It would also have flipped a fifth pin,
`test_an_empty_context_hashes_the_empty_digest`, because `[".git"]` would be
hashed even for a context with nothing in it.

**The blast radius is one image, not the fleet.** `havoc` is the only live app
with `[images]` (`archive/uwlib-storage` is archived; `outscience-staging` has
no `deploy.toml` with images). Only `cantaloupe` moves — it has no
`.dockerignore`, so it is the only one whose Dockerfile was reaching the walk.
One rebuild-and-push on the next havoc deploy; `docker build` still layer-caches
locally, and a changed tag is a cache *miss*, never a failure. **ADR in
[DECISIONS.md](DECISIONS.md) 2026-08-20**, because it changes what does and does
not invalidate a built image.

#### Pins

Four flipped, each losing its "pinned, not endorsed" framing and citing the
defect rather than a phase number:

| Was                                                                          | Now                                                           |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `test_a_dockerignore_listing_git_yields_it_twice`                            | `..._yields_it_once`                                          |
| `test_the_context_root_itself_is_the_only_path_reaching_the_full_path_check` | `..._is_no_longer_special_cased` — `True` → `False`           |
| `test_the_dockerignore_file_is_itself_hashed`                                | `test_a_comment_only_dockerignore_edit_does_not_bust_the_tag` |
| `test_the_dockerfile_is_hashed_twice`                                        | `test_the_dockerfile_is_hashed_once_under_its_prefix`         |

Four added, three of them the half that proves the exclusion did not go too far:
a pattern that hides a real file still busts the tag; deleting `.dockerignore`
busts it; two contexts with identical files hash the same whether or not a
`.dockerignore` names the Dockerfile (the pin that encodes why havoc and
transcoder do not move and cantaloupe does); and `parse_dockerignore` de-dupes
an ordinary repeated pattern, not only `.git`.

Six were checked-not-edited and still pass unchanged, including
`test_an_empty_context_hashes_the_empty_digest` and
`test_naming_a_different_dockerfile_changes_the_hash` — the latter is why
`compute_context_hash` keeps its `Dockerfile:` pre-hash: dropping it would let
`(ctx, "Dockerfile")` and `(ctx, "Dockerfile.dev")` collide.

#### Verification

`make check` (1745 tests), `make format-docs-check`, `make security` — the last
**after staging**, since `security-secrets` scans `git ls-files`. **Exercised by
hand against `havoc-staging`** with `deploy --dry-run --skip-secrets-check --ignore-audit`: `web` `1f48e716652f` and `transcoder` `c074ce44013a`
unchanged, `cantaloupe` `5ec6bfaec77d` → `5c3ff5cc515e`.

**This time the dry-run genuinely was a witness.** `_resolve_image_spec` and
`_cache_tag` both run before the build, unlike 53j-1's cluster check — which is
exactly the lesson the previous unit wrote down, applied.

#### Side effects and mints

Nothing minted, nothing cleared. The `files_to_hash` accumulator disappeared
into `_context_files`'s comprehension **rather than growing two more
conditions**, which is how this unit would otherwise have newly tripped
`temp-accumulators` — the check 53i-3c and 53i-3d each caught mid-unit, which is
why pysmelly was run *between* the two commits and not only at the end.

### 53j-4 — the masking display contract, and the ADR it stood in for (2026-08-21)

**The tenth and last unowned-residue row, and the only one that was a decision
rather than a defect.** It sat Pinned through 53j-1, 53j-2 and 53j-3 because the
register said so in as many words: masking too little leaks, masking too much
makes the output useless for debugging, and that trade is the operator's. The
operator took it on 2026-08-21, and **the corpus made it lopsided.**

Two commits: `630740c` (53j-4a, the display contract — the ledger row) and
`540d1d2` (53j-4b, the preflight overlap check — **additive scope the decision
justified, not a row**). Full reasoning in
[DECISIONS.md](DECISIONS.md) 2026-08-21.

#### What the measurement settled

`deploy --dry-run` against `havoc-staging`, the only live app: **40 variables, 9
masked, and all 9 held nothing secret.** A public base URL, `/health/`, an
internal service URL, UW's public IdP metadata URL, `3600` (masked because
`SIGNED_URL_EXPIRY` contains `url`), a Redis URL with no AUTH token — verified
in `modules/shared-infrastructure/outputs.tf`, which builds it as
`redis://${endpoint}:6379` — and **three empty strings**, where `***` claimed
there was something to hide. Meanwhile `CSRF_TRUSTED_ORIGINS` printed in full
holding the same value `BASE_URL` was masked for. **The contradiction was on one
screen, in every deploy log, for months.**

The other direction cannot happen. `get_environment_variables` and `get_secrets`
are disjoint routes off `_collect_modules` — `.environment` at
`task_definition.py:346`, `.secrets` at `:350`. A `[secrets] names` entry
reaches the container through the task definition's `secrets` block via SSM and
never enters the environment map. That is the repo's own ADR, DECISIONS.md
2026-01-21. **The masking had been defending a shape the repo banned by ADR, and
charging nine wrong answers out of forty for it.**

So the plan's own framing — "masking too little leaks" — was answered by the
corpus rather than argued with: it cannot leak, and the entire cost was on the
other side.

#### 53j-4a — the display contract

Both branches of the `if/elif/else` deleted. With nothing masked the
`ssm:`/`secretsmanager:` arm is dead too: "show the reference, not the value"
and "show the value" are the same statement. The body is two lines:

```python
value = str(raw_value)
print(f"  {key}={value or '(unset)'}")
```

`str(raw_value)` is 53j-2's fix and it is **what makes `or` safe** — only `""`
is falsy afterwards, so `0` prints `0` and `false` prints `False`. The docstring
now carries the contract and the argument for it rather than the word "Mask".

**Pins that flipped**, all in `TestPrintEnvironmentConfig`. The class docstring
said the two consequences were "NOT endorsed"; it is now a statement of the
contract.

| Was                                                           | Now                                                              |
| ------------------------------------------------------------- | ---------------------------------------------------------------- |
| `test_names_matching_a_sensitive_substring_are_masked`        | `..._matching_the_old_mask_substrings_print_their_values`        |
| `test_innocuous_names_containing_url_or_key_are_masked_too`   | folded into the above — over-masking is gone                     |
| `test_a_non_string_value_under_a_masked_name_is_fine`         | `..._under_an_old_mask_substring_prints_too` — `4`, not `***`    |
| `test_ssm_and_secretsmanager_references_are_shown_not_masked` | `..._print_like_any_other_value` — passes for a different reason |

Three added: an empty value prints `(unset)`; `0` and `false` do **not** (the
guard that makes `or` correct); and a value literally equal to `(unset)` is
indistinguishable from an empty one — **pinned as an accepted ambiguity**, not
worked around, because this block is narration and nothing in the fleet parses
it. A `$NONE` sentinel was rejected: a dotenv parser would take it literally, a
shell would expand it, `set -u` would error on it.

Five were checked-not-edited and still pass unchanged, including the
whole-output pin, which uses an empty `[environment]` and so never saw a mask.

#### 53j-4b — enforcing the invariant instead of compensating for it

`check_environment_secrets_overlap` raises `PreflightError` when a name appears
in both `[secrets] names` and any `[environment]` table — base, per-environment,
`[services.X.environment]`, or its sub-table. Wired into `run_preflight_checks`
directly after `check_secrets_style` and, like its neighbour, **unconditional**:
no `--skip` reaches it.

**A collision is an error on its own terms**, whatever ECS makes of it — the
deployer emits the colliding name **twice on the same container definition**,
once per block, and reconciles them nowhere. The check fires before the task
definition is ever built, so its justification rests on no assumption about
precedence.

**The check is exact.** Also aborting on credential-*shaped* values was
considered and rejected: a heuristic with a deploy riding on each false
positive, made of exactly the substring guesswork 4a had just deleted.

**The name set was the one trap.** `get_all_env_var_names` walks all four
declaration shapes, but then unions `ModuleRegistry.injected_names()` — **which
is where `[secrets] names` arrive from**, so every declared secret would have
reported as colliding with itself. The deploy.toml-declared half is now
`declared_env_var_names()` and `get_all_env_var_names` calls it: one traversal,
two callers. The comment at `deploy_config.py` records that module knowledge was
once a fourth hand-maintained copy that had already gone wrong; this did not add
a fifth, and secret names come from `SecretsModule.injected_names` for the same
reason. A test pins the self-collision that would otherwise have shipped.

One inherited quirk is pinned rather than fixed: `[environment.staging]` puts
`staging` into `self._environment` beside the real variables, so the walk counts
it. That predates the split, and changing it would change what the audit
considers declared.

**Blast radius zero, measured before shipping.** havoc's `[secrets] names` are
`SECRET_KEY`, `SIGNED_URL_SECRET`, `IIIF_ACCESS_CHECK_SECRET`; the intersection <!-- pragma: allowlist secret -->
with every `[environment]` table, service tables included, is empty.

#### Verification

`make check` (1758 tests), `make lint`, `make format-docs-check` and `make security` all pass, security **after staging** since `security-secrets` scans
`git ls-files` — it flagged three new assertions holding the fixture's fake
values, allowlisted inline, and rewrote `.secrets.baseline`.

pysmelly **diffed as a finding set**, not read as a total: **33** at `78c059c`,
**33** after 4a, **33** after 4b, and the sets are identical apart from
line-number drift. Run *between* the two commits as well as at the end, for the
reason 53i-3c and 53i-3d each established. Neither target file carries a finding
this touched.

Coverage: floor 74, `make test-cov` total **78.73% → 78.81%**.
`deployer.py` held at **100%**;
`preflight.py` rose **85% → 86%** (remaining gaps are `check_audit` and the
optional branches), because the new check is unconditional and every existing
preflight test reaches it.

#### Exercised by hand, both ways

`deploy --dry-run --skip-secrets-check --ignore-audit` against `havoc-staging`:
**40 variables, 0 masked**, the nine formerly-masked values printing exactly as
measured, and `Secrets and environment variables are disjoint`.

**The plan predicted 7 `(unset)` and the real number is 9.** The three
formerly-masked empties are there, plus six that had been printing as a bare
trailing `=` and were never masked at all — `AUTOSCALE_NAMESPACE`,
`AUTOSCALE_SERVICES`, `CLOUDFRONT_DOMAIN`, `S3_ADDRESSING_STYLE`,
`SAML_ENTITY_ID`, `SAML_SP_CERT_PATH`. The prediction counted the masked empties
and forgot the unmasked ones; the marker applies to both.

The abort was exercised without touching another repo's tree: havoc's
`deploy.toml` copied to the scratchpad, `SECRET_KEY` added to `[environment]` in
the copy, run with `--deploy-toml <scratch path>`. It aborted naming
`SECRET_KEY`. `git status` in havoc stayed clean.

#### Side effects and mints

Nothing minted, nothing cleared. `declared_env_var_names` is a second public
method on `DeployConfig` with **two** call sites, so it does not trip
`single-call-site`; `check_environment_secrets_overlap` has one caller and one
test class, matching every check beside it.

### 53k — the eight escalations re-measured at HEAD (2026-08-21)

**A zero-code unit**, the same shape as §53g and §53i-2a. Nothing production
changed; every draft applied here was reverted. The count read **33** before and
**33** after, and the finding *set* is byte-identical — for a unit whose whole
output is documentation, a moved count would mean something failed to revert.

The reason to run it: Phase 53's code work finished with 53j, and what stands
between the arc and its closeout is eight escalations awaiting the operator. All
eight still fire at HEAD, all seven kept diffs still apply cleanly — **but every
number attached to them was taken at `a9327ab` against 32 findings**, twenty
commits and four subphases ago. "Clears, 32 → 31" was not a statement about
HEAD, and the operator's eight verdicts should not rest on it.

**53k takes no verdict.** A skip is the operator's to take. Each row below
arrives measured, with its cost.

#### Method

Each draft was applied **one at a time against a clean tree and reverted before
the next**. Never stacked: stacked drafts interact, and an individual verdict
then has no attributable measurement. Reverting was by **reverse-applying the
same patch** (`git apply -R`), which touches only the lines the draft wrote —
not `git checkout .` or `git stash`, and not a bare pathspec discard either.

Per draft: does it clear (the `--more-please` output **diffed as a finding set**
against the HEAD baseline, never read as a total), does it mint anything (the
same diff, other direction), `env -u VIRTUAL_ENV make check`, and
`git diff --stat`. `--more-please` throughout: the plain view truncates to ten
categories and HEAD has exactly ten, so any drift hides a row.

The eighth finding has no kept draft — §53i-3b describes its fix in prose — so
it was **re-derived** and measured the same way, rather than being the one row
the operator has to take on prose alone.

#### The eight, re-measured at `8d5629d`

Baseline: **33** findings, **1758** tests passing.

| Finding                                             | Recorded at `a9327ab`       | At HEAD                                                                                                       | Tests                    | Diffstat | Changed?            |
| --------------------------------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------- | ------------------------ | -------- | ------------------- |
| `single-call-site` `init/deploy_toml.py:77`         | clears, 32 → 31, −9/+4      | **clears, 33 → 32**, nothing minted                                                                           | 1758 pass                | +4/−9    | no                  |
| `single-call-site` `emergency/checkpoint.py:105`    | clears, 32 → 31, −22/+1     | **clears, 33 → 32**, nothing minted                                                                           | **1756** pass (−2 pins)  | +1/−22   | no                  |
| `foo-equals-foo` `bin/init.py:563`                  | clears, 32 → 31, −6/+4      | **clears, 33 → 32**, nothing minted                                                                           | 1758 pass                | +4/−6    | anchor only         |
| `foo-equals-foo` `bin/init.py:220`                  | **does not clear**, 10 fail | **does not clear, 33 → 33**; finding mutates 7 args/3 locals → **5 args/1 local**                             | **10 failed**, 1748 pass | +14/−11  | no                  |
| `foo-equals-foo` `config/deploy_config.py:435`      | clears, 32 → 31, −6/+3      | **clears, 33 → 32**, nothing minted                                                                           | 1758 pass                | +3/−6    | anchor only         |
| `arrow-code` `init/deploy_toml.py:195`              | clears, 32 → 31, +17/−10    | **clears, 33 → 32**, nothing minted                                                                           | 1758 pass                | +17/−10  | no                  |
| `temp-accumulators` `deploy/images.py:342`          | clears, 32 → 31, +9/−6      | **clears, 33 → 32**, nothing minted                                                                           | 1758 pass                | +9/−6    | anchor only         |
| `inconsistent-error-handling` `emergency/ecs.py:81` | prose only (§53i-3b)        | **does not clear, 33 → 33** — clears `ecs.py:81` and **mints** `utils/environment.py:17 get_environment_path` | **1 failed**, 1757 pass  | +11/−8   | **yes — see below** |

**Seven of eight came back unchanged**, diffstat for diffstat, and the two rows
whose recorded evidence was *about failure* both still hold: `bin/init.py:220`
still fails to clear and still fails exactly **10** tests, and it still mutates
to precisely "5 args, 1 local". `deploy/images.py:342` was the other one worth
watching — it lives in the file 53j-3b rewrote — and it clears cleanly anyway,
because the draft targets `_cache_tag`'s `hash_modifiers` and 53j-3b rewrote
`_context_files`.

#### The eighth is worse than its prose said, and that is the find

§53i-3b drafted this fix and rejected it on correctness: silencing the check
means all three callers of `get_all_services_state` catching `RuntimeError`, so
`bin/emergency.py:_load_cluster_services` grows a per-caller `try` doing what
the boundary `exit_on` already does, and `bin/ops.py:cmd_incident_start` has to
**narrow** its deliberate `except Exception`. Re-derived and run, it costs two
things the prose did not name:

**It breaks a pin.** `test_ops.py:749`
`test_a_failed_config_read_is_recorded_in_the_incident_file` fails with an
uncaught `FileNotFoundError`. That test exists to assert the exact behaviour the
narrowing destroys — an unreadable config is *recorded in the incident file*
rather than aborting the incident start. The regression §53i-3b predicted in
words has a named test standing on it.

**It does not even buy the count.** 33 → **33**. Clearing `ecs.py:81` mints
`utils/environment.py:17 get_environment_path` — "13 callers: 5 catch specific,
8 unhandled" — because narrowing `cmd_incident_start` reclassifies that
function's caller mix into a shape the check flags. `core/config.py:205` also
shifts (10 specific/1 broad/3 unhandled → 11 specific/3 unhandled) without
clearing.

So the row the operator was going to weigh as "correctness versus one count"
is really **correctness versus nothing**. It is the seventh "the check's
mechanic, not the code" find in this arc, after 53f's "accessed from N files",
53g's bare-name collision, 53h-1's `write-only-attributes` under-report,
53i-1's `elif`-as-nesting, 53i-2b's `N of M caller(s) guard` ratio and 53i-3c's
`exit_on`-is-a-context-manager. The diff is kept at
`draft-ecs_error_contract.diff` alongside the other seven.

#### The drift found

**The authoritative remainder table was four subphases stale** — pinned to
`a9327ab` at 32, which is worse than the `64e3e18` table it replaced on
2026-08-18 for being three out of date. Rebuilt at HEAD; see § "Remainder".

**Five verdicts existed only in prose.** §53i-2c adjudicated all five rows the
table still listed as "open under 53i-3", and §53i-3c settled two of them a
second time as fixed-but-unseen. None had been posted to the table. Absorbed
now, and the arithmetic that falls out is the headline: 20 + 5 = **25 settled**,
7 + 1 = **8 escalated**, **0 open**, 25 + 8 = 33.

**Ten anchors had drifted.** Three in §53i-1's escalation table
(`bin/init.py:557` → `:563`, `config/deploy_config.py:379` → `:435`,
`deploy/images.py:301` → `:342`) and — found only by rebuilding the settled list
against HEAD rather than carrying it forward — seven more among the twenty
settled rows (`utils/cli.py:208`×2/`:225` → `:223`×2/`:240`,
`bin/cognito.py:214` → `:218`, `bin/emergency.py:386` → `:427`,
`deploy/service.py:197` → `:211`, `deploy/deployer.py:219` → `:240`). Carried
tables drift silently; a re-pin is the only thing that catches it.

#### Verification

`make check` clean at HEAD after every revert. `uvx pysmelly . --more-please`
reads **33** and the finding set is byte-identical to the pre-unit baseline.
`git status --short` empty outside `docs/` before staging. Coverage untouched:
`make test-cov` **78.81%**, floor 74, 1758 tests. Every measurement was captured
to a file and read back from the file, not from memory.

**`.secrets.baseline` moved, and it is drift.** Four `docs/internal/PYSMELLY.md`
entries shifted line number with identical `hashed_secret` values, plus the
`generated_at` stamp — the same pattern §53i-1 recorded, here caused by a
documentation edit rather than an import expansion. Verified as drift before
staging. `make security` was run **after** staging, since `security-secrets`
scans `git ls-files` and silently skips untracked files; it is the one file
outside `docs/` this zero-code unit touches, and it is a companion to the doc
edit, not a change to the tree's behaviour.

### 53l — the 25 settled leave-standings re-verified at HEAD (2026-08-21)

**53k rebuilt the split and deferred one thing**: re-verifying the 25 settled
rows, as "worth doing… but it roughly doubles the unit and is a separate call."
The operator made the call. §53g is the precedent and the justification — its
2026-08-18 re-verification of 19 skips found **three stale verdicts**, one of
which became a measured 37 → 35 code fix.

**Result: 25 rows read at HEAD, 0 stale, 22 hold, 3 rationales corrected.**
pysmelly **33 → 33**, diffed as a finding set and identical line for line. The
unit was **not** zero-code — three test docstrings were repaired (`983237c`) and
one escalation was drafted and measured.

**Zero stale is the number this plan said to distrust**, so the confirmations
are recorded with what was actually read rather than asserted. Two of them are
stronger than the verdicts they confirm: see `core/config.py:205` below, where
the arithmetic now closes exactly, and `aws/ecs.py:92`, where the six members
were re-derived from the live output rather than carried.

#### Scope of the re-read

16 of the 25 were last re-verified at `722d50b`, 26 commits back; **9 had never
been re-verified at all**. **9 of the 17 anchor files had changed** since
`e539257` — `utils/cli.py`, `core/ssm_secrets.py`, `bin/emergency.py`,
`deploy/deployer.py`, `deploy/preflight.py`, `bin/cognito.py`,
`deploy/service.py`, `init/template.py`, `core/config.py`. Cross-file findings
were churn-checked across every file they name, not just the anchor.

#### The 25, re-verified at `983237c`

| Finding                                                   | Adjudicated by                       | Verdict at HEAD                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| --------------------------------------------------------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `duplicate-blocks` `db-on-shared-rds/lambda/index.py:131` | 53a                                  | **Holds.** Both Lambda files unchanged since `bcd2218`, the 53a commit itself. `handle_setup_database` still opens two connections (`conn_admin` to `postgres`, `conn_app` to the app db) with `db_created` crossing between them; `handle_create_users` opens one. The context manager still hides the lifetime that *is* the shared-instance model.                                                                                              |
| `param-clumps` `db-on-shared-rds/lambda/index.py:50`      | 53a                                  | **Holds.** Signatures are still `create_app_user(conn, user, db_name)` in both modules, and `conn` still differs per call site — `conn_admin` in db-on-shared-rds, the single `conn` in db-users. A dataclass would bundle a live connection with data.                                                                                                                                                                                            |
| `pass-through-params` `utils/cli.py:223` ×2, `:240`       | 53b, 53g                             | **Holds.** Two production callers each, unchanged: `configure_profile_or_exit` ← `bin/deploy.py:147`, `bin/resolve-config.py:230`; `configure_aws_for_operation` ← `bin/ssm-secrets.py:414`, `bin/cognito.py:63`. The `try`/`except RuntimeError` → `sys.exit(1)` and the `if environment` branch are still the whole value. 53i-3c added `exit_on`/`EXIT_DECLINED` to this file without touching either.                                          |
| `pass-through-params` `aws/cognito.py:46`                 | 53c, 53g                             | **Holds** on 53g's corrected rationale. Still one caller, `bin/cognito.py:194` (was `:190`). Two-line body; inlining would put a raw `run_aws_json("cognito-idp", …)` back into `bin/`. The layering is still the reason.                                                                                                                                                                                                                          |
| `pass-through-params` `core/ssm_secrets.py:268`           | 53d-1, 53g, operator-confirmed 53i-1 | **Holds** on the operator-confirmed rationale; **all four of its anchors had drifted**. One caller `preflight.py:163` (was `:165`); `ssm_put_commands` still shared with `bin/ssm-secrets.py:211` (was `:208`); the test pin `test_ssm_secrets_cli.py:35` is intact; the symmetric pair `format_missing_ecr_error` is now `images.py:591` (was `:550`), still called one position earlier at `preflight.py:143`.                                   |
| `param-clumps` `bin/emergency.py:427`                     | 53d-2a, 53g                          | **Holds, rationale corrected** — see below. The highest-risk row and the one §53g never covered.                                                                                                                                                                                                                                                                                                                                                   |
| `law-of-demeter` `deploy/deployer.py:240`                 | 53e-3                                | **Holds.** Three commits touched the file and none touched this. `self.rds = boto3.client("rds")` at `:145`, `except self.rds.exceptions.DBInstanceNotFoundFault:` at `:240` — still the botocore runtime-only namespace, still no intermediate object worth asking.                                                                                                                                                                               |
| `pass-through-params` `aws/cloudwatch.py:50` ×2           | 53g                                  | **Holds.** `get_task_logs` still exists to build `f"{stream_prefix}/{container_name}/{task_id}"`, still two callers — but one anchor moved with a changed file: `deploy/service.py:759` (was `:718`) and `bin/ecs-run.py:72`.                                                                                                                                                                                                                      |
| `pass-through-params` `aws/ssm.py:131` (false positive)   | 53g                                  | **Holds, re-read.** `parameter_exists` calls `client.get_parameter(Name=name)`; `_get_call_target_name` returns `.attr` for an `ast.Attribute`, colliding with the module's own `def get_parameter` at `:46`.                                                                                                                                                                                                                                      |
| `pass-through-params` `core/ssm_secrets.py:36`, `:49`     | 53g                                  | **Holds, rationale corrected** — the supporting count was already stale when §53g wrote it. See below.                                                                                                                                                                                                                                                                                                                                             |
| `pass-through-params` `core/ssm_secrets.py:63` ×2         | 53g                                  | **Holds.** One production caller, `bin/ssm-secrets.py:164`; the body is still parse-then-delegate (`parse_deploy_config` ← `deploy_toml_path`, `get_secrets_from_config` ← `env_config`). Noted, not a defect: that sole caller passes only `deploy_toml_path`, and `env_config`'s `None` path is a *documented contract* — `EnvironmentConfigError` — not a dead parameter. **This is the shape §53g's stale verdict took, checked and cleared.** |
| `pass-through-params` `deploy/preflight.py:46`            | 53g                                  | **Holds** through 53j-4b's rewrite. Still one caller, now `preflight.py:316` (was `:254`) and now passing `target.config`; the body still turns `validate_environment_config`'s error list into a `PreflightError` carrying an `advice_block`.                                                                                                                                                                                                     |
| `param-clumps` `aws/ecs.py:92`                            | 53g                                  | **Holds**, re-derived from the live output rather than carried: still 6 functions, still 3 in `ecs.py` (`:92`, `:298`, `:450`) and 3 in `service.py` (`:121`, `:175`, `:1097`), the same six names. `deploy/context.py` imports nothing from `deployer`, so `aws/` importing it is still an inversion.                                                                                                                                             |
| `param-clumps` `bin/cognito.py:218`                       | 53g                                  | **Holds.** Still `cmd_create` / `cmd_reset_password` / `core/cognito.py:format_welcome_message:97`; the Click wrappers moved to `:403`/`:442` (were `:399`/`:438`). Still rejected on design — Click parameters unpacked immediately at the wrapper — not on "it does not clear".                                                                                                                                                                  |
| `param-clumps` `deploy/service.py:211`                    | 53e-5, 53g                           | **Holds** through two commits to the file. `register_task_definition(ctx, service_name, image_uri, credential_mode)`; the other two are still `task_definition.py:get_environment_variables:165` and `:build_task_definition:314`; `ctx` is still `DeploymentContext`. The advice still asks for a second dataclass.                                                                                                                               |
| `return-none-instead-of-raise` `aws/cli.py:48`            | 53i-2c                               | **Holds, rationale corrected.** `run_aws_json`'s `None` is still failure-only (command failed, or output was not JSON) and still contract 3. Still 4 callers, 3 guarding. But the rationale's second half — "the defect **was** one layer down, in `aws/rds.py`" — is past tense for something never fixed. See below.                                                                                                                             |
| `inconsistent-error-handling` `utils/aws_profile.py:85`   | 53i-2c                               | **Holds.** `raise RuntimeError` is still reachable only inside `if validate:` (`:138`/`:141`). Three callers, all in `utils/cli.py`: `:234` passes `validate=True` and catches `RuntimeError`; `:248` and `:269` take the default and cannot reach the raise.                                                                                                                                                                                      |
| `inconsistent-error-handling` `init/template.py:126`      | 53i-2c                               | **Holds** through one commit to the file. Still 4 callers: `init/environment.py:163`'s `except KeyError` is still a fallback *dispatch* to `substitute_optional`, and `bootstrap.py:170`/`:175`/`:179` still want the `KeyError`.                                                                                                                                                                                                                  |
| `inconsistent-error-handling` `core/config.py:205`        | 53i-3c                               | **Holds — and the arithmetic now closes exactly.** See below.                                                                                                                                                                                                                                                                                                                                                                                      |
| `inconsistent-error-handling` `utils/environment.py:17`   | 53i-3c                               | **Holds.** All five `exit_on(RuntimeError)` wrappers 53i-3c added are still in place and still wrap a `get_environments_dir()` call: `capacity-report.py:225`, `cognito.py:73`, `deploy.py:67`, `environment.py:68`, `init.py:503`. The eleventh site is still gone, with the comment saying why at `bin/init.py:522`.                                                                                                                             |

#### `param-clumps` `bin/emergency.py:427` — the rationale under-described its own subject

The verdict is right and unchanged: `yes` is a confirmation flag rather than an
attribute of a target, and all three functions are still Click parameters
unpacked immediately at the wrapper (`:920`, `:934`, `:979`) — the flag-shuffling
shape 53c's register rejected and 53d-2a re-rejected.

**What needed correcting is the third leg.** 53d-2a wrote that "the real
duplication behind the clump was the membership check and its error, and that
**was** fixed (`_require_service`)" — a sentence that reads as though the
clump's shared shape had been fully accounted for. It had not. **53i-3c found a
second one in the same three functions** — attempt every service, keep going
past a failure, report once, and all three ending on an unconditional
`return 0` — and extracted `_report_outcome` for it. One subphase's "the real
duplication" was another's starting point.

The rationale of record is now:

> The clump's shared shape has been extracted **twice**, by two subphases, and
> neither extraction needed a bundle: `_require_service` (53d-2a) for the
> membership check, `_report_outcome` (53i-3c) for the per-service loop's
> outcome and exit status. What is left is a two-line
> `if not confirm_action(skip=yes): return EXIT_DECLINED` — identical in
> `cmd_rollback`, `cmd_scale`, `cmd_force_deploy` **and** `cmd_revert`, and
> unextractable because a helper cannot early-return for its caller — plus a
> three-line `_load_cluster_services` opener shared by two of the three, below
> `duplicate-blocks`' five-statement threshold. `yes` is still a flag, and
> `environment` / `service` are consumed differently in each function.

#### `core/ssm_secrets.py:36`, `:49` — a re-verification that transcribed a stale count

§53g's re-verified column reads "`get_path_prefix` has 5 call sites and
`get_parameter_path` 3". At HEAD `get_path_prefix` has **3**
(`ssm_secrets.py:174`, `bin/ssm-secrets.py:181`, `:330`) and `get_parameter_path`
has 3 (`bin/ssm-secrets.py:264`, `:311`, `:372`).

**It was already 3 at `c283f5e`** — §53g's own re-verification pin, checked with
`git grep`. So 53g re-verified the *verdict* against HEAD and carried the
*supporting count* forward from its 2026-08-13 skip list without re-measuring
it. The verdict is unaffected: both functions still wrap `parse_environment` in
an f-string that **is** the SSM path convention, and inlining would put that
convention at three sites. But a rationale is only as good as the measurement
under it, and this is the failure mode the caller-count rule exists for.

Rationale of record, corrected: *encode the SSM path convention;
`get_path_prefix` 3 call sites across two files, `get_parameter_path` 3.*

#### `core/config.py:205` — the fixed-but-unseen verdict, now closed arithmetically

53i-3c adjudicated this **fixed-but-unseen**: the three unhandled `bin/ops.py`
callers took `exit_on`, which is a context manager and therefore invisible to a
check that looks for `try`/`except`. The verdict was right, but it rested on
"the check cannot see it" rather than on showing *which* callers the check still
counts.

Enumerated at HEAD, the check reads **14 callers: 10 catch specific, 1 catch
broad, 3 unhandled** — and the three unhandled are **exactly** the three
`exit_on` sites, `bin/ops.py:544`, `:656`, `:728`. The one broad is
`cmd_incident_start:913`, the documented honest-degradation case 53i-3b and
53i-3d deliberately left alone. Every other caller catches
`(FileNotFoundError, RuntimeError)` explicitly.

**The set the check calls "unhandled" is now identical to the set that was
fixed, with nothing left over.** That is a stronger statement than 53i-3c could
make, and it is what re-verification is for.

#### The one escalation 53l produces: `aws/rds.py:get_status`

§53i-2c's verdict on `aws/cli.py:48` says the defect "was one layer down, in
`aws/rds.py`" — `get_status` translating a failure-`None` into its own
documented "or None if **not found**". Re-read at HEAD: **it is still there, and
no subphase ever owned it.** The docstring still says "or None if not found" and
the standing suppression rationale still says `None means "not found"`, while
`if not data: return None` covers a failed read just as much as a missing
instance.

This is not an open question. DECISIONS.md § "2026-08-18: Error Contracts"
decides it for **every function in this repo**: *one sentinel never means both
"nothing is there" and "I could not look."* 53i-3 applied that to `emergency/`
and never came back for `aws/`. The finding is suppressed, so it appeared in no
count the arc used to scope itself — which is exactly how it survived four
subphases of an error-contract arc.

**Two of its three siblings are the same shape.** Of the four standing
`return-none-instead-of-raise` suppressions, `core/config.py:291`
(`get_cognito_user_pool_id_from_config`) is a pure dict read that cannot fail and
is compliant; `utils/links.py:29` reports a corrupt links file as "not linked"
(behind a `# noqa: BLE001`), and `init/bootstrap.py:185` reports an unresolvable
environments dir as "no bootstrap directory". **Three of four conflict with the
decision.**

**Drafted and measured on the anchor** (diff at
`scratchpad/53l-draft-get-status.diff`): `get_status` switches to `run_aws`,
returns `None` only on `DBInstanceNotFound` in the error text, and raises
`RuntimeError` otherwise. Cost, measured against a clean tree and reverted with
`git apply -R`:

- pysmelly **33 → 33.** Nothing clears. `aws/cli.py:48` — an *escalated* finding
  — degrades from "3 of 4 callers guard" to "2 of 3", because `get_status` stops
  being a `run_aws_json` caller.
- **2 tests fail**, and the second is the substance:
  `TestWaitForStatus::test_reports_each_status_to_the_callback` feeds a failed
  read and expects the poll loop to continue. `wait_for_status` (`rds.py:111`)
  reads `get_status`'s `None` as `"unknown"` **on purpose**, so a transient
  throttle does not abort a wait. Making `get_status` raise turns a retryable
  blip into an aborted wait. Preserving the polling contract needs a second
  decision — a `try`/`except` inside `wait_for_status` — that the ADR does not
  make.
- +19 / −4 lines in `aws/rds.py`.

**Escalated, not decided**, and not shipped: the anchor's fix has a live
behaviour conflict, and the other two members carry their own callers. 53l takes
no verdict here, as it takes none on the original eight.

#### Two routed items whose destination ran without consuming them

53d-2a routed `cmd_restore_db` and `cmd_revert`'s hand-rolled error handling to
**53i** as "hand-rolled versions of what `exit_on` covers", and no subphase ever
recorded what became of them. 53i-3c ran; `bin/emergency.py` has exactly **one**
`exit_on` call at HEAD, at `:897`, in `_run_or_exit`.

Read at HEAD, they were **correctly not consumed**, and the routing was wrong
rather than forgotten:

- `cmd_restore_db:654` — `except ValueError` around `datetime.fromisoformat`,
  replacing the exception's own text with `"Invalid time format: {time}"` plus
  an ISO-format hint, and **returning 1**. `exit_on` surfaces the exception's
  message and `sys.exit`s; here that would print
  `Invalid isoformat string: …` and bypass the status ladder `_run_or_exit`
  owns.
- `cmd_revert:749` — `except FileNotFoundError` around `load_checkpoint`,
  same shape, naming the checkpoint.

Both replace the exception's message with a domain message and answer through
the command's return status. **Neither is the `exit_on` shape.** Disposition:
**not applicable, closed** — recorded here because a routed item whose
destination has already run is precisely what goes stale in silence.

#### Three stale pins in this document, re-measured

| Claim                                               | Was                                            | At HEAD                                                                                                                                                                                                                                   |
| --------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 53i-2's "pinned, not endorsed" corpus               | **31** markers, 6 test files, pinned `a9327ab` | **0.** Consumed by 53i-3b/3c/3d exactly as designed — `600c788`'s message records "markers 32 → 0, with one provenance reference left standing", and that reference is `test_modules.py:1`, never part of the 31.                         |
| Convergence-hotspot list empty, repo max 2          | pinned `a9327ab`                               | **Re-derived at HEAD, unchanged.** No file at 3+; max **2**, held by the same two files — `init/deploy_toml.py` (`single-call-site` + `arrow-code`) and `modules/db-on-shared-rds/lambda/index.py` (`duplicate-blocks` + `param-clumps`). |
| Suppression placement classifier **13 / 7 / 0 / 0** | re-measured 2026-08-18                         | **7 / 8 / 0 / 5.** Total still 20; **the split was wrong when it was written.** See below.                                                                                                                                                |

**The corpus claim also mismeasured what it counted.** §53i-1's entry describes
"31 *pinned, not endorsed* markers" but records the measurement as
`grep -c 53i` — it counted *route-to-53i* references and labelled them
"pinned, not endorsed". The two are different populations: the phrase
`"pinned, not endorsed"` still appears in **3** files at HEAD
(`test_emergency_rds.py:7`, `test_extensions.py:13`, `test_init_cli.py:19`) as
file-header prose, with **no per-test marker anywhere in the repo**. Counting
one population under another's name is why the number could go to zero without
the label looking false.

#### The suppression classifier counted a tag as a rationale

Re-measured over `git ls-files '*.py'` at HEAD — 20 directives, classified by
reading each one's neighbourhood rather than by the presence of a comment:

| Placement                                              | §53i-2a said | 53l measures |
| ------------------------------------------------------ | ------------ | ------------ |
| Rationale on the directive line                        | 13           | **7**        |
| Rationale on the line(s) directly above, no blank line | 7            | **8**        |
| Rationale detached by blank lines                      | 0            | 0            |
| **No rationale at all**                                | **0**        | **5**        |
| **Total**                                              | 20           | 20           |

The five with no rationale are **`src/deployer/utils/logging.py` `:41`, `:57`,
`:63`, `:69`, `:75`** — all `inconsistent-error-handling`, each reading
`# pysmelly: ignore inconsistent-error-handling  (re-evaluate-by: 2026-11 review)`
and preceded by two blank lines and unrelated code. They carry a **tag** and no
reason. The eighth "directly above" is `utils/datetime.py:8`, which has a real
two-line rationale immediately above it and was counted on-line.

**They are byte-identical at `a9327ab`**, so this is not drift: §53i-2a's
classifier treated a bare `(re-evaluate-by: …)` tag as a rationale, and the six
genuinely detached ones it *was* repairing masked the six it was miscounting.
The convention this document states in § "Deployer's convention" — "each with a
rationale and a `re-evaluate-by:` tag" — is false for five of twenty.

**Not self-authored a fix.** Writing five rationales for suppressions whose
reason nobody recorded is exactly the self-authored justification the register
weighs at ~zero. **Escalated**: either a reason is written by the operator, or
the five directives come out and the findings are adjudicated like everything
else here.

#### The docstring repairs (`983237c`)

Three test docstrings, all edit scars from earlier subphases, found by reading
the files the settled rows pointed at:

- `test_extensions.py` — 53i-3d substituted "decided in DECISIONS.md" for
  "tracked as claude-meta Phase 53i" and left `tracked` stranded on the previous
  line: *"an error-contract question tracked decided in"*.
- `test_emergency_rds.py` — the header claimed *"Several tests are marked
  'pinned, not endorsed'"* and described the swallow-`ClientError` behaviour in
  the present tense. 53i-3b made the two queries raise and deleted every marker
  in the file, so the paragraph contradicted its own next sentence.
- `test_ssm_secrets.py` — *"Its only production caller, `bin/ssm-secrets.py`,
  passes two arguments"* **inverts the crash it explains**. `bin/ssm-secrets.py:164`
  passes one, which is why `env_config` arrived as `None` and
  `get_secrets_from_config` raised `AttributeError` on it. Found while
  re-verifying `ssm_secrets.py:63`.

#### Verification

`env -u VIRTUAL_ENV make check` clean; **1758 tests**, unchanged.
`uvx pysmelly . --more-please` reads **33** and the finding set is identical to
the pre-unit baseline line for line — diffed sorted, because pysmelly's emit
order within a repeated anchor is not stable and an unsorted diff shows three
false moves (`cloudwatch.py:50`, `ssm_secrets.py:63`, `utils/cli.py:223`).
Coverage untouched at **78.81%**, floor 74 — the unit changes no executable
line. `.secrets.baseline` **did not move** this time; verified before staging.
`make security` run after staging. The one drafted fix was applied against a
clean tree, measured, and reverted with `git apply -R`; every measurement was
teed to the scratchpad and read back from the file.

### 53m / 53n — the residue no ledger ever swept (2026-08-24)

**53l closed the settled rows; these close the populations nobody counted.**
§"The latent-bug ledger, reconciled" states its own scope: "53e-3a, 53e-4a and
53e-5a pinned 14 latent bugs across **three tables above**." There is a
**fourth** *Latent bugs pinned, not fixed* table — §53f's — and a **fifth**
population, §53h-1's *"Pinned, not endorsed — handed to 53h-2"*. Neither was
ever in scope for 53j, which was scoped from the ledger, or for anything else.

**Six items were live at HEAD; a seventh belongs to Phase 69 and an eighth was
already retired.** Every one was re-verified before being acted on, per the
standing rule, and that re-verification is what caught the two that had moved.

pysmelly **33 → 33** across both units, finding set identical; the only movement
is one settled anchor, `bin/emergency.py:427` → **`:458`**. Tests 1758 → **1760**.

#### 53m — the RDS half of `bin/emergency.py`

The half 53d-2a named and no subphase ever got. Its "Noticed, not changed" note
routed the twin to "a subphase that owns the file's RDS half"; §53l closed the
*other* two items in that note as not-applicable, and this closes the twin.

| Item                                                   | Verified at HEAD                                                                                                                                                                                                                                | Outcome                                                                                                                                        |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `idx < 0` unreachable (53f)                            | Live. `if not choice.isdigit()` returns first, so `int(choice)` cannot be negative.                                                                                                                                                             | **Deleted**, reason left as a comment at the site.                                                                                             |
| non-`DBInstanceAlreadyExists` `ClientError` (53f)      | Live, and **sharper than when pinned**: `_handle_restore_error` re-raises deliberately and now documents it (`emergency/rds.py:279`, `:318`), but nothing between there and the boundary catches it and `exit_on(RuntimeError)` does not match. | **Fixed at the boundary**, not inside the command. See below.                                                                                  |
| `cmd_restore_db`'s snapshot/PITR twin (53d-2a, routed) | Live. The two arms differ only in the mutator called and the line logged before calling it; the eleven lines after the call were identical.                                                                                                     | **`_report_restore` extracted.** It also **retired `cmd_restore_db`'s `# noqa: C901`** — removed, not moved, as 53d-2a did for `cmd_rollback`. |

**Why the boundary and not a local catch.** `_run_or_exit` is this file's one
process boundary, and its docstring already argues why `exit_on`'s
"don't wrap a whole command body" warning does not apply there — there is no
frame above it, so the alternative is a traceback rather than "the caller
handles it". Widening it to `(RuntimeError, ClientError)` keeps the 53i-3c
ladder intact (`1` for failed) and covers every future re-raise in the file,
where a local `try` would have covered exactly one call.

**The old pin was right and stayed.** `cmd_restore_db` still propagates the
`ClientError` — that is `_handle_restore_error` doing its job. What was
unpinned was the *boundary*: nothing asserted what the CLI does with it, which
is why 1758 tests passed both before and after the fix. The new pin asserts the
exit status, and the file docstring's "pinned as-is rather than fixed"
paragraph is now a record of what 53m fixed.

**Coverage moved and it is denominator shrink, not regression.**
`bin/emergency.py` reads 90% → 89% and the total 78.81% → 78.78%; statements
**468 → 460**, branches **126 → 122**, **missed unchanged at 55**. The
extraction deleted covered duplicate statements. Every remaining missed line
maps to a pre-existing one offset by the helper's length, and `_report_restore`
is itself fully covered. The same mechanism §53f recorded for `task_definition.py`.

#### 53n — the module-system residue handed to 53h-2 and never taken

| Item                                                    | Verified at HEAD                                                                                     | Outcome                                                              |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `collect()` does not re-check what `validate()` rejects | Live, and `database.py` said so in a comment. But the *premise* did not survive reading — see below. | **Fixed**, and the pin flipped with its rationale rewritten.         |
| the dead `if not app_config` guard, `secrets.py` ×2     | Live. Coverage confirmed `:169` unreachable.                                                         | **Deleted both.** The file went **98% → 100%**.                      |
| unimplemented sections never validated                  | Live, but a **different mechanism already reports them**.                                            | **Adjudicated, not fixed**, with a companion pin for the other half. |

**53h-1's premise did not survive reading, and that is the find.** It pinned
the `credentials` gap as "a container that starts and fails to authenticate."
At HEAD that container **is not reachable**: `check_modules` sits in
`run_preflight_checks`' always-run block — no skip flag, unlike audit, ECR,
secrets and cluster — ahead of `Deployer`'s construction, and **both** routes
into `collect()` are downstream of it (`build_task_definition` via
`register_task_definition`, and `print_environment_config` at `deployer.py:323`,
called from `deploy()`).

So the fix is **defence in depth made load-bearing, not a live-bug fix**, and
the record says so rather than inheriting the scarier framing. It still earns
its line: the failure it guards is silent — the task definition would be
*valid*, merely credential-less — and reaching it is a **deployer** bug, which
this repo's standing rule (§53i-3d, and `bin/ssm-secrets.py:173`) says must not
be dressed up as the operator's error. Measured cost: **exactly one test**, the
pin asserting the old silent behaviour. Nothing else moved.

**The guards were redundant, not merely unreachable** — which is a stronger
reason to delete them than 53h-1's. `validate_all` and `collect_all` both skip a
module whose section is falsy, so neither guard can fire from the registry
route; and with `app_config = {}` the very next line,
`app_config.get("names", [])`, returns the identical answer. Deleting them
changes no behaviour on any input, reachable or not.

**The unimplemented-section pin is an adjudication.** `validate_all` reporting
nothing for `[cdn]` is correct division of labour: `DeployConfig.get_warnings()`
compares every top-level section against `KNOWN_SECTIONS` (which contains
neither `cdn` nor `autoscale`) and `Deployer.__init__` surfaces it on the deploy
path at `deployer.py:108`. Making `validate_all` reject unknown sections would
move that judgement into the module registry and **re-create the second list
53h-2a deleted**. A companion test now pins the reporting channel, so the
adjudication carries both halves rather than resting on prose.

#### One item routed out, with its reason

53f's fourth pin — *"both placeholder readers stringify bools Python-style"*
(`core/config.py:190`'s `return str(resolved)` turns a tofu `true` into
`"True"` in a container environment variable) — is **routed to Phase 69, not
fixed here.** Phase 69 already owns *"the two placeholder readers use opposite
matching rules"* on the **same two functions**, and its sequencing note says
that item and `_get_legacy_secrets` are one file and move together. The region
(`core/config.py:163-202`) is at **0%**, so the pin-first cost belongs with
whoever touches it, once. Splitting two defects in the same two functions across
two phases would pay that cost twice and land two unrelated diffs on the same
lines.

**Recording the routing *and its reason* is the §53l lesson applied forward**:
a routed item whose destination has run is exactly what goes stale in silence.

#### 53f's third pin was already gone, and Phase 69's anchor has rotted

`_get_legacy_secrets` "silently drops a secret whose value matches neither
prefix" is **not residue** — it is Phase 69's first member. But re-verifying it
found that **`_get_legacy_secrets` does not exist at HEAD**: `deploy/task_definition.py`
holds only `_resolve_legacy_placeholders`, and the `secretsmanager:` prefix
logic now lives in `modules/secrets.py:35`. Phase 69's member 1 and its
"same file, should move together" sequencing are both anchored to a function
that is gone. Filed against Phase 69 in claude-meta `docs/plan/`; **not
re-derived here**, because inventing a replacement anchor for another phase is
the same error as writing its record for it.

### The pin ledger, reconciled across all five populations (2026-08-24)

**The ledger above reconciled three tables and said so.** That sentence is what
let §53f's four sit unswept: a reader checking whether everything was closed
found a section titled "reconciled" and a total that balanced. This is the
complete population.

| Population                           | Count | Disposition                                                                                                                                                                                             |
| ------------------------------------ | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 53e-3a / 53e-4a / 53e-5a (3 tables)  | 14    | 3 fixed by 53i-3d as a by-product, 1 owned by Phase 69 (`update_service`), **10 closed by the 53j arc** — 2026-08-21.                                                                                   |
| §53f *Latent bugs pinned, not fixed* | 4     | 2 fixed by **53m** (`idx < 0`, the `ClientError` traceback); 1 **routed to Phase 69** with its reason (placeholder bools); 1 **is** Phase 69 member 1 (`_get_legacy_secrets`), whose anchor has rotted. |
| §53h-1 *handed to 53h-2*             | 3     | 2 fixed by **53n** (the `credentials` re-check, the dead guards); 1 **adjudicated** by 53n (unimplemented sections).                                                                                    |
| §53d-2a *Noticed, not changed*       | 3     | 2 closed **not-applicable** by §53l (the `exit_on` routings); 1 fixed by **53m** (the restore twin).                                                                                                    |
| 53i route-markers (`grep -c 53i`)    | 31    | **0** — consumed by 53i-3b/3c/3d, re-measured by §53l.                                                                                                                                                  |

**Nothing pinned by any subphase of Phase 53 is now unowned.** Four items sit
with Phase 69 by explicit routing, each with the reason recorded.

### 53p — the ten verdicts applied, and Phase 53 closed (2026-08-25)

**The operator took all ten outstanding verdicts on 2026-08-25.** Phase 53 had
no code work left that a session could decide: the register read 33 live = 25
settled + 8 escalated + 0 open, every pin population was reconciled, and the
closeout was gated on verdicts only the operator can take. Seven of the ten are
confirmed leave-standings needing no code; four are work.

**The Escalated column goes to 0.** Arithmetic, stated up front and accounted
for finding-by-finding below: **33 → 32 (53p-1) → 32 (53p-2) → 36 (53p-3)**.
The rise is deliberate — four decisions moved out of comments and into this
register.

#### The seven confirmed leave-standings (operator, 2026-08-25)

Each was drafted and measured by an earlier subphase; the operator's reason of
record is below. **These are not to be re-litigated.**

| Finding                                                        | Reason of record                                                                                         |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `single-call-site` `init/deploy_toml.py:77` `is_likely_secret` | The named predicate heads a 4-way dispatch; inlining buries secret-detection policy in a 3-line boolean. |
| `foo-equals-foo` `bin/init.py:563`                             | Inlining widens a `try` over `get_next_listener_priority`, whose `iterdir()` raises `FileNotFoundError`. |
| `foo-equals-foo` `bin/init.py:220`                             | The fix does not clear (33 → 33), 10 tests fail, and it reorders operator prompts.                       |
| `foo-equals-foo` `config/deploy_config.py:435`                 | Splits each `dacite` parse from its own unknown-key loop; the `*_data` local survives anyway.            |
| `arrow-code` `init/deploy_toml.py:195`                         | Depth 6 counts `elif` as nesting; real depth is 4. Filed as a check mechanic by 53i-1.                   |
| `temp-accumulators` `deploy/images.py:342`                     | The list *is* the image cache-tag format; changing it re-tags the fleet.                                 |
| `inconsistent-error-handling` `emergency/ecs.py:81`            | The fix clears one and mints `utils/environment.py:17` (33 → 33), and breaks `test_ops.py:749`.          |

#### 53p-1 — `generate_checkpoint_filename` inlined (verdict 2)

**33 → 32**, one row, no mints; tests 1760 → 1759. The two-line body moved into
`create_checkpoint`, its only caller. `test_filename_shape` was redundant with
`test_creates_directory_and_parseable_file`, which already asserts
`FILENAME_PATTERN` through `create_checkpoint`; `test_filename_uses_current_utc_date`
— the only assertion that the stamp is UTC and not local — moved into
`TestCreateCheckpoint` and drives the same path. The filename and the checkpoint
timestamp now read the clock once between them rather than twice.

#### 53p-2 — the error-contract ADR's unapplied corner (verdicts 9, 9b)

DECISIONS.md § "2026-08-18: Error Contracts" decides for **every function in
this repo** that one sentinel never means both "nothing is there" and "I could
not look". 53i-3 applied it to `emergency/` only. All three sites below were
**suppressed**, so none moves the count — the deliverable is that their
rationales became *true*. Measured **32 → 32**.

**`aws/rds.py get_status`** — `run_aws` instead of `run_aws_json`, so the error
text survives. `None` now means `DBInstanceNotFound` and nothing else; every
other failure, and a `JSONDecodeError`, raises `RuntimeError`. The docstring's
`Returns:` and the standing suppression rationale — both of which claimed "not
found" while the code meant either — now say what the code does.

- **`wait_for_status` catches it deliberately.** It reads a failed describe as
  `"unknown"` so a transient throttle cannot abort a poll loop; only the
  timeout ends that wait. A prior draft that skipped this failed
  `TestWaitForStatus::test_reports_each_status_to_the_callback`.
- **Five `bin/` call sites needed a boundary**, and the ADR already said which
  kind. `bin/emergency.py` needed nothing: its `exit_on(RuntimeError, ClientError)` at `:912` already aborts, which is the ADR's answer for
  destructive commands. `bin/ops.py` `_print_rds_status` and
  `bin/environment.py` `cmd_status` report in place. `bin/environment.py`'s
  `cmd_stop` and `cmd_start` abort — `cmd_stop` previously printed
  "stop initiated" after a describe that failed.

**`init/bootstrap.py:185`** — the `try/except RuntimeError` is deleted, verified
dead at all three call sites: `bin/init.py:462` and `verify.py:87` each call
`get_environments_dir()` in their own `try` and return first, and
`verify.py:137` `_check_bootstrap_plan` is reached only after `cmd_verify`
confirms `_check_deployer_config()` returned True, which requires that same call
to have succeeded. Same shape as the redundant guards 53n deleted from
`modules/secrets.py`. **One test asserted the swallow directly**
(`test_returns_none_when_env_var_unset`) and now asserts the raise — production
callers decide the interface, not test callers.

**`utils/links.py:29`** — its `except Exception: return None` made a *corrupt*
links file report as "not linked", so `utils/cli.py:199` told the operator to
link an environment that already was. Narrowed: `None` for "no links file" /
"not linked", `RuntimeError` naming the file on a parse failure.

> **The plan verified the three `resolve_deploy_toml_or_exit` callers and
> missed a second caller of `get_linked_deploy_toml`.** `bin/link-environments.py:135`
> `cmd_unlink` reads it too, and that file has **no exception handling of any
> kind** — the `RuntimeError` would have reached the operator as a traceback.
> Both boundaries are now pinned in `test_bin_error_boundaries.py`.

**Two settled rows degrade, and their rationales are corrected below**, since
`get_status` stops being a `run_aws_json` caller and `bootstrap_dir_exists`
stops being a `get_environments_dir` catcher:

| Row                                                     | Was                      | Now              |
| ------------------------------------------------------- | ------------------------ | ---------------- |
| `return-none-instead-of-raise` `aws/cli.py:48`          | 3 of 4 guard             | **2 of 3** guard |
| `inconsistent-error-handling` `utils/environment.py:17` | 7 specific / 9 unhandled | **6 / 10**       |

Neither verdict changes. `aws/cli.py:48`'s `None` still means failure only
(PYTHON.md #19 contract 3); the ratio was always incidental to that reading,
which is the argument for not citing a ratio in a rationale at all.
`utils/environment.py:17` is still fixed-but-unseen — and the tally moved
because a *deleted* site left it, exactly as §53i-3c observed the first time.

#### 53p-3 — `utils/logging.py` unsuppressed and adjudicated (verdict 10)

The five `# pysmelly: ignore inconsistent-error-handling (re-evaluate-by: 2026-11 review)` directives carried a tag and **no rationale** — 53l's find,
which also corrected this register's placement classifier from 13/7/0/0 to
**7/8/0/5**. The reasoning existed, but in a file header forty lines from the
findings it justified, where no reader of a finding would meet it.

**Measured: 32 → 36. Only four of the five fire** — `log_success` (`:57`) has
suppressed nothing since at least `a9327ab`, which is the cost of a directive
nobody re-measures.

| Surfaced        | Callers                                |
| --------------- | -------------------------------------- |
| `log()`         | 46 — 23 specific, 23 unhandled         |
| `log_status()`  | 5 — 2 specific, 3 unhandled            |
| `log_warning()` | 31 — 2 specific, 1 broad, 28 unhandled |
| `log_error()`   | 66 — 1 specific, 65 unhandled          |

All four are **adjudicated leave-standings** on this reason:

> `print()` wrappers with no error contract of their own. The check reports the
> **callers'** handling of their own unrelated work, not this function's
> contract — the same mechanic that made `utils/aws_profile.py:85` a false
> positive. Left visible rather than suppressed, per this repo's stated
> convention of near-zero suppression.

The file header now carries that reasoning instead of a pointer to directives
that no longer exist.

#### Verification

`make check` passes at `187b2f9`: **1769 tests** (1760 → 1759 after 53p-1, then
+10 pins across 53p-2), ruff and black clean. Coverage **79.35%** against a
floor of 74, up from 78.78%; `aws/rds.py`, `emergency/rds.py` and
`init/bootstrap.py` are each at 100%, and `utils/links.py` rose from 11% to 35%.
Every pysmelly measurement was taken with `uvx pysmelly . --more-please` and
diffed as a **sorted** finding set — HEAD has exactly ten categories, so the
plain view truncates.

**The final 36, accounted for finding-by-finding:** 32 after 53p-1 and 53p-2,
plus the four `logging.py` rows surfaced by 53p-3. Nothing was minted anywhere
else. The only other movement in the whole subphase was text: `aws/cli.py:48`
and `utils/environment.py:17` restating their caller ratios, and
`utils/cli.py:223`/`:240` drifting to `:231`/`:248` under an eight-line
insertion.

#### Phase 53 is closed

**36 live = 36 settled, 0 escalated, 0 open.** From 97 findings at triage.
