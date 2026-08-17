# Explicit `[secrets]` paths (`VAR = "ssm:/path"`)

## What it did

`[secrets]` accepted a mapping of environment-variable name to a storage
reference, resolved by `_get_legacy_secrets` in `deploy/task_definition.py`:

```toml
[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"
DB_PASSWORD = "secretsmanager:${db_password_secret_arn}"
```

An `ssm:` value became an SSM parameter ARN built by string concatenation; a
`secretsmanager:` value had the prefix sliced off and the remainder passed
through as the ARN. `${...}` placeholders were substituted anywhere inside the
value from `InfraConfig.legacy_placeholders()`.

`deployer init` generated this form, and `docs/CONFIG-REFERENCE.md` §`[secrets]`
was the only place that documented it.

## Why it was removed

**It was one of two contradictory styles, and it silently lost secrets.**

`SecretsModule` implemented a second style — `names = ["SECRET_KEY"]`, with the
SSM path coming from the environment's `config.toml` `path_prefix` — documented
in `docs/resources/secrets.md`. Neither document mentioned the other. Calling
the explicit form "legacy" in the code is what kept the contradiction invisible:
it was what the primary reference taught and what the tool generated.

`get_secrets` chose between three routes from the shape of deploy.toml. A
deploy.toml carrying explicit `[secrets]` **and** any module section took the
module route, which returned only module secrets and never read `[secrets]` at
all:

```
{"secrets": {"SECRET_KEY": "ssm:/app/secret-key"}}              -> [SECRET_KEY]
{"database": {...}, "secrets": {"SECRET_KEY": "ssm:/app/..."}}  -> [DB_USERNAME, DB_PASSWORD]
```

Nothing caught it. Preflight confirmed the SSM parameters existed and passed.
`DeployConfig.get_all_env_var_names` counted the explicit keys as provided, so
the audit passed. The container started without its secrets.

**`names` is canonical on the architecture's own stated premise**
(`modules/base.py`): deploy.toml declares *what the application needs*,
config.toml says *how the environment provides it*. `names = ["ARCHIVE_IT"]` is
a declaration; `ARCHIVE_IT = "ssm:/myapp/staging/archive-it"` puts one
environment's SSM layout into the application's checked-in file.

The migration cost was near zero. Both fleet deploy.tomls (`havoc`,
`archive/uwlib-storage`) already used `names`, and all four `config.toml`
templates already emitted `[secrets] provider = "ssm"` with a `path_prefix`, so
nothing changed on the environment side.

## Secrets Manager as an application-secret source

The explicit form's `secretsmanager:` arm went with it, and **was not
reimplemented** on `SecretsModule` as a `provider = "secretsmanager"` option.
Same precedent as the cdn and autoscale modules: documented, speculative, zero
real usage. No repo in the fleet ever used it for an application secret.

This does **not** affect database credentials. `[database]` still supports
`credentials = "secretsmanager"`, which is the one place Secrets Manager is
actually used — see `docs/resources/database.md`. Only *application* secrets
are SSM-only.

## Replacement

```toml
# deploy.toml -- what the application needs
[secrets]
names = ["SECRET_KEY", "DATACITE_PASSWORD"]
```

```toml
# config.toml -- how this environment provides it
[secrets]
provider = "ssm"
path_prefix = "/myapp/staging"
```

`SECRET_KEY` resolves to `/myapp/staging/secret-key`.

## Removal commit

`6d34179` (Phase 53h-2a)

## How it fails now

`preflight.check_secrets_style` rejects the explicit form by name, ahead of
every other module check, with the replacement spelled out.
`core.ssm_secrets.get_secrets_from_config` raises `ValueError` on the same
input rather than answering `{}` — an unreadable declaration must not be
reported as an empty one, which is how `bin/ssm-secrets.py check` would come to
advise deleting live parameters.

## How to restore

Don't. If a genuine need for per-secret paths appears, the right shape is an
environment-side mapping in `config.toml` (`[secrets.paths]`), not a
storage reference in the application's file — that keeps the premise intact.

The deleted implementation is at `504cc4d:src/deployer/deploy/task_definition.py`
(`_get_legacy_secrets`) with its pins at
`504cc4d:tests/unit/test_deploy_task_definition.py`.
