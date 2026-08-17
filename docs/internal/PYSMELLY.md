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

Standing total: **38** (measured at the Phase 53h-1 commit `a8d7369`; was 39 at
`4e63c05`, 41 at `bb17c37`/`b5b8465` — the 53f/53g state — 47 at `64e3e18`,
49 at `2b057ae` and `961be51`, 52 at `9c95d79`, 53 at
`d75d24e` and `57bc874`, 56 at
`a304fa1`, 57 at `9903e2b`, 60 at `805d516`,
68 at `db8aa78`, 71 at `26d9290`, 74 at `07d65d6`, 82 at `2d79e33`, 91 at
`a8800cd`, 97 at `8e57264`).

**Register gap, recorded rather than papered over:** 53f and 53g shipped in the
2026-08-13 unattended run without an adjudication entry here. Their outcomes are
in claude-meta `docs/PLAN.md` Phase 53 (53f: `dict-as-dataclass` 6 → 0 across
four units; 53g: zero code units by design, with a recorded skip list). 53h-1
did not fill that in — writing another subphase's adjudication record after the
fact is not a thing a later session should invent.

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

| Commit    | File                              | Pins    | Effect                                        |
| --------- | --------------------------------- | ------- | --------------------------------------------- |
| `b42be87` | `tests/unit/test_deploy_service.py`      | **110** | the deploy path; `service.py` 11% → 44%       |
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

| Shape                        | Sites (pre-commit) | Varies only in       |
| ---------------------------- | ------------------ | -------------------- |
| `deploymentConfiguration` base | 272-275 / 414-417 | the dict key (`serviceName` vs `service`) |
| circuit-breaker injection    | 289-293 / 421-425  | the target variable name |
| `serviceRegistries` injection | 319-326 / 430-439 | line wrapping        |

That is why the two functions **had to move in one commit**. Splitting them
across two would have written `_deployment_configuration` and
`_service_registries` twice — the helpers serve both call paths.

##### The staged measurement, again

| Function          | Stage                      | Lines   | Flagged? |
| ----------------- | -------------------------- | ------- | -------- |
| `create_service`  | baseline                   | 107     | yes      |
|                   | `_require_network_config`  | **101** | **yes**  |
|                   | `_load_balancer_params`    | 82      | no       |
|                   | `_service_registries`      | 74      | no       |
|                   | `_deployment_configuration` | 64     | no       |
| `deploy_services` | baseline                   | 110     | yes      |
|                   | `_update_service`          | 73      | no       |

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

| Stage                     | Lines   | Flagged? |
| ------------------------- | ------- | -------- |
| baseline                  | 118     | yes      |
| `_describe_service_or_raise` | **102** | **yes** |
| `_raise_task_failure`     | 85      | no       |
| `_track_no_progress`      | 75      | no       |

`_describe_service_or_raise` also retired the two locals it was the only reader
of (`ecs_client`, `cluster_name`). And once again the first extraction was not
enough: **102 is still flagged**, so `_raise_task_failure` was load-bearing, not
polish.

##### The obvious first extraction was insufficient in all four decompositions

This is the arc's headline result and the reason to record it as a rule rather
than an anecdote. Across the four functions decomposed in 53e-5 and the prior
slice, the state after the first extraction was:

| Function                   | After one extraction | Bar | Cleared? |
| -------------------------- | -------------------- | --- | -------- |
| `create_service`           | 101                  | 100 | no       |
| `_wait_for_service_stable` | 102                  | 100 | no       |
| `build_and_push_images` (53e-4b) | 107            | 100 | no       |
| `deploy()` (53e-3b)        | 114                  | 100 | no       |

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
  65. Never lowered, at any point, including the three refactor commits.
- **Convergence-hotspot list independently re-derived as empty**, max **2** checks
  per file repo-wide.

**Two measurement corrections, recorded rather than silently fixed** — house
style, since the commit messages are in history and cannot be edited:

| Claim                                       | Commit message | Measured by AST at `64e3e18` |
| ------------------------------------------- | -------------- | ---------------------------- |
| `start_migrations` after 5c (`961be51`)     | 85L            | **84L**                      |
| `_wait_for_service_stable` after 5d (`64e3e18`) | 76L        | **75L**                      |

Both are off by one in the same direction and neither changes an outcome — both
were already well under the 100-line bar — but the register records the measured
value.

#### Latent bugs pinned, not fixed

Continuing the section 53e-3 opened. 53e-5a's pins capture all fourteen as
**current behaviour, not as endorsements**; each is a real defect left for a
subphase that owns the contract.

**From 5a-1 (`b42be87`), the deploy path — nine:**

| Bug                                                                                                                                                                                                                                                              | Status                                                                                                                          |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **`service_exists` swallows `ClusterNotFoundException`** and returns `False`, so a **typo in the cluster name takes the CREATE branch** rather than reporting an unknown cluster.                                                                                | Pinned. The highest-severity item here: a wrong answer that routes the deploy down the wrong path instead of stopping it.       |
| **The `ClientError` swallow makes a partial deploy look successful.** A per-service failure is reported and the loop continues; the run still ends as a success.                                                                                                 | Pinned. Same `except`-shape family recorded under **53i**.                                                                      |
| **`update_service` carries no network configuration, no load balancer and no launch type**, so a target-group change on an existing service has **no effect** — the parameters are built and never sent on the update path.                                     | Pinned. Silent no-op on the operation an operator would most expect to work.                                                    |
| **A load-balanced service can be created with no target group** — nothing fails when the lookup yields nothing.                                                                                                                                                  | Pinned. The service comes up and receives no traffic.                                                                           |
| **`port` is read from the raw table while `load_balanced` comes from merged sizing.** Two keys of one decision sourced from two different config layers.                                                                                                         | Pinned. The kind of split that makes an environment override behave differently from the base.                                  |
| **`--dry-run` previews an *update* for a service that does not exist**, and prints **none of the parameters it just built**.                                                                                                                                     | Pinned. A preview that is wrong about the branch and silent about the payload.                                                  |
| **`_ensure_az_rebalancing_disabled` indexes `services[0]` unconditionally.**                                                                                                                                                                                     | Pinned. `IndexError` on an empty describe response.                                                                             |
| **An empty-string per-service target group falls through to the default.** `""` is falsy, so an explicit "no target group" reads as "unset".                                                                                                                     | Pinned. Config that cannot express what it looks like it expresses.                                                             |
| **`_get_deployment_config` ignores the dataclass field names and does no range validation.**                                                                                                                                                                     | Pinned. A misspelled key is accepted silently; an out-of-range percentage reaches the ECS API.                                   |

**From 5a-2 (`fb561e3`), the migration + wait path — five:**

| Bug                                                                                                                                                                                                                                                                                                | Status                                                                                                                                                     |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **`_wait_for_target_group_healthy` can never succeed against an empty target group.** The condition is `healthy > 0 and healthy == total`, which is `False` at **0/0**, so the wait burns the **full 300s** and then reports the wrong diagnosis.                                                  | Pinned. The worst of the five: a five-minute stall that names the wrong cause.                                                                             |
| **`start_migrations` raises a bare `KeyError` on `networkConfiguration`** — the read sits **outside** the `try`, so a service without one produces an unhandled `KeyError` rather than a diagnosis. Preserved verbatim by 5c's `_migration_network_config`.                                       | Pinned, and explicitly preserved through the 5c extraction.                                                                                                |
| **`_get_deployment_status` raises a bare `KeyError` from `d["status"]`.**                                                                                                                                                                                                                          | Pinned. Same shape one layer down.                                                                                                                         |
| **`_wait_for_service_and_targets` catches only `DeploymentError` / `RuntimeError`**, so a `ClientError` **escapes the worker and bypasses `ServiceWaitResult`** entirely — the result-object contract is not actually total.                                                                       | Pinned. The failure mode the result object exists to prevent.                                                                                              |
| **`wait_for_migrations` uses `.get("exitCode", 1)`** — a container that stopped **without** an exit code is treated as a **failure**.                                                                                                                                                              | **Pinned as load-bearing, not as a defect to fix.** This is the safe default and it is easy to "tidy" into `0`. Recorded so the next reader leaves it alone. |

#### Side effects and mints

Nothing cleared as a side effect and **nothing was minted** — the line-level diff
above is the evidence, not an assertion. `service.py` is down to a **single**
finding.

| Finding                                                                                | Disposition                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `param-clumps` — `service.py:197` `(credential_mode, ctx, service_name)` in 3 functions | **Routed to 53g, not a leave-standing.** **Nothing inside `service.py` can clear it**: only one of the three signatures is here (`register_task_definition`); the other two are `task_definition.py:get_environment_variables():140` and `task_definition.py:build_task_definition():383`. |

### 53e arc summary — closeout (2026-08-13)

Five slices, thirteen commits, `805d516` → `64e3e18`.

| Measure                     | 53e-1 start (`805d516`) | 53e-3 start (`a304fa1`) | End (`64e3e18`) |
| --------------------------- | ----------------------- | ----------------------- | --------------- |
| pysmelly total              | 60                      | **56**                  | **47**          |
| `long-function`             | 9                       | **7**                   | **0**           |
| Files on the hotspot list   | 2                       | 1                       | **0**           |
| Coverage floor (`fail_under`) | 53                    | **54**                  | **65**          |
| Tests                       | 852                     | **891**                 | **1339**        |

**`long-function` is empty as a category** and the **convergence-hotspot list is
empty** — no file in the repo is flagged by three or more checks, and the maximum
is now 2.

The three files 53e-3 → 53e-5 took on, in coverage order:

| File                 | Coverage at split | Now                                   |
| -------------------- | ----------------- | ------------------------------------- |
| `deploy/deployer.py` | 35%               | **100%**                              |
| `deploy/images.py`   | 16%               | **100%**                              |
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

**Live per-category counts, re-measured 2026-08-13 at `64e3e18` (47 total).**
This table is the authoritative one; scope each subphase from it, not from the
prose below. Measured with `pysmelly . --more-please` — **the plain
`make pysmelly` view truncates to the top ten categories and under-reports
`inconsistent-error-handling` as 3.**

`pass-through-params` 14, `param-clumps` 7, `dict-as-dataclass` 6,
`inconsistent-error-handling` 4, `foo-equals-foo` 3, `single-call-site` 3,
`arrow-code` 2, `law-of-demeter` 2, `feature-envy` 2,
`return-none-instead-of-raise` 1, `duplicate-blocks` 1,
`write-only-attributes` 1, `temp-accumulators` 1. (Sums to 47.)

53e-5 moved exactly two numbers: **`long-function` 4 → 0** (the category is now
absent from the report entirely) and `arrow-code` 3 → 2. Every other category is
unchanged from the `9c95d79` measurement — verified by a line-level diff of the
full finding list, not by comparing totals.

12 of the 47 are adjudicated leave-standings (53a 2, 53b 3, 53c 2, 53d-1 1,
53d-2a 1, 53d-2b 2, 53e-3 1 — itemized total is self-consistent; 53e-4 and
53e-5 each added none). The remainder is queued behind claude-meta
`docs/PLAN.md` Phase 53f–53i, plus the one `dict-as-dataclass` 53e-3c minted
and routed to 53f.

**Correction 2026-08-13 (unattended run W0): the earlier per-category split of
the remainder was arithmetically broken and is withdrawn rather than
repaired.** It claimed 11 adjudicated + **45** queued = 56, but the queued
category list it gave summed to **47** (→ 58). The **56 total is correct**;
the prose figure 45 reconciles and the list did not. Two of its entries were
also stale against live counts: `pass-through-params` listed 9 / live **14**,
`param-clumps` listed 5 / live **7**.

The split is withdrawn, not corrected, because this record never captured a
reliable per-category attribution for the 11 and the two documents that would
supply it disagree: claude-meta `docs/PLAN.md` 53g reads all **14**
`pass-through-params` as open and `param-clumps` as 7 total less 2 adjudicated
(53a's Lambda one, 53d-2a's `emergency.py` one) = **5 open** — which would put
only 2 adjudicated in these two categories, not the 9 the arithmetic above
would need. Reconciling that is an adjudication question, so it is left for an
operator-in-the-loop session. **Each subphase re-measures at HEAD anyway**,
which is what the live table above is for.

They are concentrated in `src/deployer/`, not in `modules/` or `bin/`.

**`long-function` is empty as a category** — no file in the repo carries one,
and the check does not appear in the report. 53e cleared all nine.

**The convergence-hotspot list is empty.** No file is flagged by three or more
checks; the repo-wide maximum is **2**, held by `init/deploy_toml.py`
(`single-call-site` + `arrow-code`), `core/ssm_secrets.py` (`pass-through-params`
+ `param-clumps`), `init/template.py` (`inconsistent-error-handling` +
`law-of-demeter`), `deploy/deployer.py` (`dict-as-dataclass` + `law-of-demeter`)
and `modules/db-on-shared-rds/lambda/index.py` (`duplicate-blocks` +
`param-clumps`). `deploy/service.py` was the last entry and dropped off at
`2b057ae`.

Per-file remainder in the files this arc touched: **`deploy/service.py` is down
to one finding**, the `param-clumps` at `:197` routed to **53g** — and nothing
inside `service.py` can clear it, because two of its three signatures live in
`task_definition.py`. `deploy/images.py` is down to one finding, the
`temp-accumulators` 53e-4b relocated into `_cache_tag`. `deploy/deployer.py` is
down to two findings, both adjudicated by 53e-3 (the standing `law-of-demeter`
and the `dict-as-dataclass` routed to 53f). `core/audit.py`,
`deploy/extensions.py` and `init/setup_profiles.py` carry none. `bin/emergency.py`
has one finding left (the adjudicated `param-clump`) and `bin/init.py` two (both
`foo-equals-foo`, routed to 53i).

`duplicate-except-blocks` is empty as a category, and `duplicate-blocks` is down
to a single finding — the `db-on-shared-rds` ↔ `db-users` Lambda pair 53a
adjudicated as a leave-standing. The category has no open items.

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

| File                  | Before | After                                     |
| --------------------- | ------ | ----------------------------------------- |
| `modules/database.py` | 54%    | **100%**                                  |
| `modules/storage.py`  | 78%    | **100%**                                  |
| `modules/cache.py`    | 83%    | **100%**                                  |
| `modules/secrets.py`  | 71%    | 97% — one dead guard, see below           |
| `modules/base.py`     | 82%    | 95% — three `@abstractmethod` `pass` bodies |
| `deploy/task_definition.py` | 84% | **93%**                                 |

Coverage floor **69 → 70** (70.44% measured), 1477 → 1544 tests.

#### The four findings

| Finding                                              | Disposition                                                                                                                                                                                                            |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `write-only-attributes` — `base.py:140` `domain_name` | **Cleared**, along with three sibling fields the check never saw. See the under-reporting mechanic below.                                                                                                              |
| `feature-envy` — `database.py:126` `collect()`        | **Cleared.** `ModuleContext.ssm_parameter_arn()` and reading `credential_mode` once take the `context` accesses from 4 to 2, under the check's 3-access threshold.                                                     |
| `feature-envy` — `database.py:47` `validate()`        | **Survives by design; 53h-2's call.** Measured, not assumed: a `DatabaseEnvConfig` dataclass would **not** clear it, because `env_config.host` is still an `ast.Attribute` Load. Only moving the logic onto the config type, or extracting *module-level* `_`-helpers (`check_feature_envy` only walks `ClassDef` bodies), does. Extracting **methods** would *mint* findings — a helper reading `env_config` 4× with `self` 0× fires on its own. |
| `param-clumps` — `__init__.py:85`                     | **Cleared as a side effect of `@override`, which the plan predicted only bundling could do.** See below — this is the one result worth carrying forward.                                                               |

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
