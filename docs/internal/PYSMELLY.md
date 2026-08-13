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

Standing total: **60** (measured at the Phase 53d-2b commit; was 68 at
`db8aa78`, 71 at `26d9290`, 74 at `07d65d6`, 82 at `2d79e33`, 91 at `a8800cd`,
97 at `8e57264`).

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

### Remainder (not yet adjudicated)

11 of the 60 are adjudicated leave-standings (53a 2, 53b 3, 53c 2, 53d-1 1,
53d-2a 1, 53d-2b 2). The other **49** are queued behind claude-meta
`docs/PLAN.md` Phase 53e–53i:

`long-function` 9, `pass-through-params` 9, `param-clumps` 5, `arrow-code` 3,
`dict-as-dataclass` 5, `inconsistent-error-handling` 4, `law-of-demeter` 4,
`foo-equals-foo` 3, `single-call-site` 3, `feature-envy` 2,
`write-only-attributes` 1, `temp-accumulators` 1.

They are concentrated in `src/deployer/`, not in `modules/` or `bin/`. Every
remaining `long-function` is in `src/deployer/`, four of the nine in
`deploy/service.py`. `bin/emergency.py` has one finding left (the adjudicated
`param-clump`) and `bin/init.py` three (two `foo-equals-foo` routed to 53i, one
adjudicated `duplicate-blocks`).

`duplicate-except-blocks` is empty as a category, and `duplicate-blocks` has no
open items left — every one of its 9 findings is an adjudicated leave-standing.
