<!-- pysmelly-guidance 0679ec1c1685 -->

# pysmelly — findings register and review conventions

Run `make pysmelly` (repo root — config lives in `[tool.pysmelly]` in
`pyproject.toml`). Full guidance: https://github.com/borwickatuw/pysmelly#readme
or regenerate the generic guide with `pysmelly init --short`.

## Deployer's convention

**Near-zero suppression.** Findings are fixed, or left standing as an
operator-visible decision recorded below. Inline `# pysmelly: ignore` is reserved
for cut-and-dry false positives (Lambda handler signatures, JSON-serialized dict
returns), each with a rationale and a `re-evaluate-by:` tag. Suppression comments
go on the finding line or the line immediately above it — pysmelly does not see
them anywhere else.

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

Standing total: **32** (measured at `a9327ab`, the 53i-1 closeout; was 35 at
the 53f/53g closeout `722d50b`, 37 at `aacee1b`, 38 at `a8d7369`, 39 at
`4e63c05`, 41 at `bb17c37`/`b5b8465` — the 53f/53g state — 47 at `64e3e18`,
49 at `2b057ae` and `961be51`, 52 at `9c95d79`, 53 at `d75d24e` and `57bc874`,
56 at `a304fa1`, 57 at `9903e2b`, 60 at `805d516`, 68 at `db8aa78`, 71 at
`26d9290`, 74 at `07d65d6`, 82 at `2d79e33`, 91 at `a8800cd`, 97 at `8e57264`).

**Every live finding is attributed, and nothing is unowned.** See § "Remainder
— the reconciled adjudication split": 20 adjudicated leave-standings, 7
escalated by 53i-1 with measured diffs and awaiting the operator, and 5 open
under 53i-2. The 3 findings that 53f/53g's reconciliation found owned by no
subphase were folded into 53i-1 by operator decision (2026-08-18); one of them
is cleared and two are among the seven escalations.

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

| Bug                                                                                                                                                                                                                                                                                                                                                                                                | Status                                                                                                                                           |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`print_environment_config` raises `AttributeError` on a non-string value.** `value.startswith("ssm:")` assumes `str`, but `[environment] MAX_WORKERS = 4` in `deploy.toml` arrives from TOML as an `int` and `get_environment_variables` never stringifies it. Only bites names that miss **every** mask substring, because a masked name short-circuits first — which is why nobody has hit it. | Pinned by `test_a_non_string_value_under_a_non_masked_name_raises`.                                                                              |
| **Masking is name-substring-based and wrong in both directions.** `BASE_URL` and `MONKEY_BUSINESS` get masked (`url`, `key`), while a real secret under a name like `PUBLIC_HOSTNAME` prints in full. It also renders the `ssm:` / `secretsmanager:` `elif` nearly dead: a value referencing a secret almost always sits under a name the substring list already catches.                          | Pinned, not endorsed. Fixing it is a display-contract decision, not a refactor.                                                                  |
| **`check_infrastructure_status`'s bare `except Exception` reports a _clean_ status.** A credentials failure or a network timeout is indistinguishable from "the database is healthy" — the one direction this function must never get wrong.                                                                                                                                                       | Same family already recorded under **53i** for `extensions.py` and `bin/init.py`; this is the third instance and the one with real blast radius. |

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

| Bug                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Status                                                                                                                                              |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`build_args.<env>` dispatch divergence — a production crash on a typo.** The dict arm's `.update(<non-dict>)` raises `TypeError` for an int and `ValueError: dictionary update sequence…` for a string; the `ImageConfig` arm does not raise and passes the scalar through as a literal `--build-arg staging=5`. **Production always takes the dict arm** (`deployer.py:112` passes `get_raw_dict()`), so a typo'd `build_args.staging = "x"` crashes the deploy with an opaque message naming **neither the image nor the key**. | Pinned by `TestBuildArgsDispatchDiverges`. The highest-severity item in this table; belongs with whichever subphase owns the config-error contract. |
| **A non-existent `context` directory is not an error.** `rglob` yields nothing, so the image gets the digest of nothing (`e3b0c44298fc`) and `docker build` then runs against a path that does not exist.                                                                                                                                                                                                                                                                                                                           | Pinned. Two failures for the price of one typo — a wrong cache tag *and* a misleading docker error.                                                 |
| **`ecr_login` discards both streams** (`DEVNULL`), so a `docker login` failure surfaces as a bare `RuntimeError("ECR login failed")` with no diagnostics.                                                                                                                                                                                                                                                                                                                                                                           | Pinned. Same family as the `except Exception` items already recorded under **53i**.                                                                 |
| **The Dockerfile is hashed twice** — once under the `Dockerfile:` prefix, then again as an ordinary context file.                                                                                                                                                                                                                                                                                                                                                                                                                   | Pinned. Harmless today (the tag is still stable and still changes when it should), but it makes the hash inputs read wrong.                         |
| **`.dockerignore` edits always bust the cache**, even comment-only ones, because the file is hashed as context.                                                                                                                                                                                                                                                                                                                                                                                                                     | Pinned. A rebuild of every image for a comment.                                                                                                     |
| **`should_ignore`'s final `fnmatch` is dead** for real files — it can only fire when the path *is* the context root, which `rglob` never yields.                                                                                                                                                                                                                                                                                                                                                                                    | Pinned. Dead code that looks like a pattern-matching feature.                                                                                       |
| **`parse_dockerignore` does not de-duplicate `.git`.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Pinned. Cosmetic.                                                                                                                                   |
| **`NullTimer` would raise `AttributeError` in `_run_timed_subprocess`** — it is truthy and has no `_current_step`.                                                                                                                                                                                                                                                                                                                                                                                                                  | Pinned. Confirms the 53e-3b null-object never reaches this module, and pins the trap for whoever tries to extend it here.                           |

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

| Bug                                                                                                                                                                                                                         | Status                                                                                                                    |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **`service_exists` swallows `ClusterNotFoundException`** and returns `False`, so a **typo in the cluster name takes the CREATE branch** rather than reporting an unknown cluster.                                           | Pinned. The highest-severity item here: a wrong answer that routes the deploy down the wrong path instead of stopping it. |
| **The `ClientError` swallow makes a partial deploy look successful.** A per-service failure is reported and the loop continues; the run still ends as a success.                                                            | Pinned. Same `except`-shape family recorded under **53i**.                                                                |
| **`update_service` carries no network configuration, no load balancer and no launch type**, so a target-group change on an existing service has **no effect** — the parameters are built and never sent on the update path. | Pinned. Silent no-op on the operation an operator would most expect to work.                                              |
| **A load-balanced service can be created with no target group** — nothing fails when the lookup yields nothing.                                                                                                             | Pinned. The service comes up and receives no traffic.                                                                     |
| **`port` is read from the raw table while `load_balanced` comes from merged sizing.** Two keys of one decision sourced from two different config layers.                                                                    | Pinned. The kind of split that makes an environment override behave differently from the base.                            |
| **`--dry-run` previews an *update* for a service that does not exist**, and prints **none of the parameters it just built**.                                                                                                | Pinned. A preview that is wrong about the branch and silent about the payload.                                            |
| **`_ensure_az_rebalancing_disabled` indexes `services[0]` unconditionally.**                                                                                                                                                | Pinned. `IndexError` on an empty describe response.                                                                       |
| **An empty-string per-service target group falls through to the default.** `""` is falsy, so an explicit "no target group" reads as "unset".                                                                                | Pinned. Config that cannot express what it looks like it expresses.                                                       |
| **`_get_deployment_config` ignores the dataclass field names and does no range validation.**                                                                                                                                | Pinned. A misspelled key is accepted silently; an out-of-range percentage reaches the ECS API.                            |

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

**Noticed during 53c, not fixed here.** 22 `# pysmelly: ignore` lines stand
repo-wide, not the three above, and the blanket claim that all of them carry a
rationale and a `re-evaluate-by:` tag is **not true**. Five carry neither:

| Location                                                | Check                        |
| ------------------------------------------------------- | ---------------------------- |
| `init/bootstrap.py` `bootstrap_dir_exists`              | return-none-instead-of-raise |
| `core/config.py` `get_cognito_user_pool_id_from_config` | return-none-instead-of-raise |
| `core/config.py` `get_commands_from_deploy_toml`        | isinstance-chain             |
| `config/compose.py` `get_compose_services`              | isinstance-chain             |
| `utils/links.py` `get_linked_deploy_toml`               | return-none-instead-of-raise |

(`aws/rds.py get_status` and `emergency/checkpoint.py` do carry both, on the line
above the suppression rather than on it.) Three of the five are
`return-none-instead-of-raise`, which is exactly the policy **53i** owns — the
right place to write their rationales or delete them, alongside the unsuppressed
`run_aws_json` instance 53c minted. `modules/staging-scheduler/lambda/handler.py`
`handler` is a fourth Lambda `vestigial-params` suppression the table above
should have listed and does not.

### Remainder — the reconciled adjudication split (rebuilt 2026-08-18)

**Live total: 32, measured at `a9327ab`** (the 53i-1 closeout; it was 35 at
`722d50b`, and the line numbers below are re-measured at `a9327ab` — the
53i-1 fixes drifted `core/ssm_secrets.py` by +4) with
`uvx pysmelly . --more-please` —
**the plain `make pysmelly` view truncates to the top ten categories and
under-reports `inconsistent-error-handling` as 3.** This table is the
authoritative one; scope each subphase from it.

This replaces a table pinned to `64e3e18` at 47 findings — three subphases out
of date — whose per-category adjudication split was withdrawn on 2026-08-13 as
arithmetically broken (11 + 45 claimed as 56; the list summed to 47) and
explicitly left for an operator-in-the-loop session. **This is that session.**
Every finding below is attributed to either an adjudicated leave-standing
(naming the subphase) or an open owner. No finding is unattributed.

| Category                     | Live | Settled | Escalated | Open | Open owner |
| ---------------------------- | ---- | ------- | --------- | ---- | ---------- |
| pass-through-params          | 13   | 13      | 0         | 0    | —          |
| param-clumps                 | 5    | 5       | 0         | 0    | —          |
| inconsistent-error-handling  | 4    | 0       | 0         | 4    | 53i-2      |
| foo-equals-foo               | 3    | 0       | 3         | 0    | —          |
| single-call-site             | 2    | 0       | 2         | 0    | —          |
| arrow-code                   | 1    | 0       | 1         | 0    | —          |
| law-of-demeter               | 1    | 1       | 0         | 0    | —          |
| duplicate-blocks             | 1    | 1       | 0         | 0    | —          |
| return-none-instead-of-raise | 1    | 0       | 0         | 1    | 53i-2      |
| temp-accumulators            | 1    | 0       | 1         | 0    | —          |
| **Total**                    | 32   | **20**  | **7**     | 5    | 53i-2 5    |

**Escalated** means 53i-1 drafted a fix, measured it, and handed the decision to
the operator rather than recording a self-authored justification; the diffs are
listed in §53i-1. Nothing is unowned. **Three findings cleared in 53i-1** and
left this table: `single-call-site` `modules/secrets.py:86` and `law-of-demeter`
`init/template.py:26` (both were open under 53i), and `arrow-code`
`cli/ci_deploy.py:181` (one of the three orphans).

#### Settled — 20 adjudicated leave-standings

| Finding                                                   | Adjudicated by                                |
| --------------------------------------------------------- | --------------------------------------------- |
| `duplicate-blocks` `db-on-shared-rds/lambda/index.py:131` | 53a                                           |
| `param-clumps` `db-on-shared-rds/lambda/index.py:50`      | 53a                                           |
| `pass-through-params` `utils/cli.py:208` ×2, `:225`       | 53b (minted), 53g                             |
| `pass-through-params` `aws/cognito.py:46`                 | 53c (minted), 53g                             |
| `pass-through-params` `core/ssm_secrets.py:268`           | 53d-1 (minted), 53g, operator-confirmed 53i-1 |
| `param-clumps` `bin/emergency.py:386`                     | 53d-2a, 53g                                   |
| `law-of-demeter` `deploy/deployer.py:219`                 | 53e-3                                         |
| `pass-through-params` `aws/cloudwatch.py:50` ×2           | 53g                                           |
| `pass-through-params` `aws/ssm.py:131` (false positive)   | 53g                                           |
| `pass-through-params` `core/ssm_secrets.py:36`, `:49`     | 53g                                           |
| `pass-through-params` `core/ssm_secrets.py:63` ×2         | 53g                                           |
| `pass-through-params` `deploy/preflight.py:46`            | 53g                                           |
| `param-clumps` `aws/ecs.py:92`                            | 53g                                           |
| `param-clumps` `bin/cognito.py:214`                       | 53g                                           |
| `param-clumps` `deploy/service.py:197`                    | 53e-5 routed, 53g                             |

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
53c's `DeployOptions`; claude-meta `docs/PLAN.md` already records that one.
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
| `temp-accumulators` `deploy/images.py:301`       | 53e-5's closeout listed it as "open after the arc, routed" — and named no destination, unlike its two siblings (53f, 53g). | 53i-1 — drafted, measured, escalated                      |

**No finding is now unowned.** That was the condition 53i was blocked on, and
it is the reason the three were folded rather than tallied.

#### Category notes

**Empty categories**: `long-function` (9 → 0 across 53d–53e),
`dict-as-dataclass` (6 → 0 in 53f), `write-only-attributes` (1 → 0 in 53h-1),
`duplicate-except-blocks` (cleared by 53a and 53b) and
`boolean-param-explosion` (cleared by 53c's `DeployOptions`).

**The convergence-hotspot list is empty.** No file is flagged by three or more
checks. Re-derived at `a9327ab`, the repo-wide maximum is **2**, and only two
files hold it: `init/deploy_toml.py` (`single-call-site` + `arrow-code`) and
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

The split is not about size. 53i-2's corpus is **31** "pinned, not endorsed"
markers across 6 test files, plus 4 `inconsistent-error-handling` findings,
`aws/cli.run_aws_json`, and the 5 inline suppressions carrying neither a
rationale nor a `re-evaluate-by:` tag. None of 53i-1's ten findings touches any
of it.

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

| Finding                                                                         | Draft result                                        | Measured cost of applying it                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------------------------------------------------- | --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `single-call-site` `init/deploy_toml.py:77` `is_likely_secret`                  | **clears**, 32 → 31, tests pass, −9/+4              | The name *is* the policy statement over `SECRET_PATTERNS`/`NON_SECRET_ENV_VARS`; inlining buries it as a 3-line boolean inside a loop, and widens the head of the `elif` chain above.                                                                                                                                                                                                                                                                           |
| `single-call-site` `emergency/checkpoint.py:105` `generate_checkpoint_filename` | **clears**, 32 → 31, tests pass, −22/+1             | Deletes two direct pins. `FILENAME_PATTERN` survives at `test_emergency_checkpoint.py:190` via `create_checkpoint`, but `test_filename_uses_current_utc_date` — the only assertion that the stamp is UTC, not local — has nowhere left to live.                                                                                                                                                                                                                 |
| `foo-equals-foo` `bin/init.py:557` `generate_environment`                       | **clears**, 32 → 31, tests pass, −6/+4              | Byte-for-byte the shape the register already adjudicated for 53b's `deploy.py:167` `timer` ("built conditionally two statements earlier"). **And it widens a `try`**: `get_next_listener_priority` → `get_environments_dir().iterdir()` raises `FileNotFoundError`, which the enclosing `except (ValueError, FileNotFoundError)` would then swallow. Confirmed by running it against a missing directory. A cosmetic finding silently changing error behaviour. |
| `foo-equals-foo` `bin/init.py:220` `_BootstrapInputs`                           | **does not clear** — 32 → 32, and **10 tests fail** | The three locals are `click.prompt` results. Keyword arguments evaluate at the call, which is the last statement, so inlining moves two prompts *after* the Cognito prompts — it does not hide the interaction order, it changes it. The finding only mutates (7 args/3 locals → 5 args/1 local), because `region` arrives from a tuple unpack that cannot be inlined at all.                                                                                   |
| `foo-equals-foo` `config/deploy_config.py:379` `cls()`                          | **clears**, 32 → 31, tests pass, −6/+3              | Moves three `dacite.from_dict(...)` calls into an argument list, splitting each parse from the unknown-key warnings loop that immediately precedes it. The section-by-section "validate keys, then parse" pairing is the readable part.                                                                                                                                                                                                                         |
| `arrow-code` `init/deploy_toml.py:195`                                          | **clears**, 32 → 31, tests pass, +17/−10            | Converts a flat five-arm dispatch a reader sees as depth 3 into a second function plus a `.update()` indirection, and forces reordering `CELERY_BROKER_URL` ahead of `REDIS_URL` to preserve behaviour. Paid entirely to satisfy a check that counts `elif` as nesting.                                                                                                                                                                                         |
| `temp-accumulators` `deploy/images.py:301`                                      | **clears**, 32 → 31, tests pass, +9/−6              | 53e-4b *deliberately* relocated this accumulator into `_cache_tag` and recorded that it did not clear. The comprehension form must evaluate both modifier strings eagerly and filter a tuple of pairs. pysmelly's own message reads "accumulator may be appropriate here". File is at 100%.                                                                                                                                                                     |

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
