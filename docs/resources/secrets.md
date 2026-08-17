# Secrets Module

## Application declares (`deploy.toml`)

```toml
[secrets]
names = ["SECRET_KEY", "SIGNED_URL_SECRET", "DATACITE_PASSWORD"]
```

## Environment provides (`config.toml`)

```toml
[secrets]
provider = "ssm"
path_prefix = "/myapp/staging"
```

**Name normalization**: `SECRET_KEY` → `secret-key`, `SIGNED_URL_SECRET` → `signed-url-secret`

**Injects**: Each named secret from SSM Parameter Store

## This is the only style

`names` is the only key `[secrets]` accepts. A deploy.toml written in the
removed explicit-path form — `SECRET_KEY = "ssm:/myapp/staging/secret-key"` —
is rejected at preflight with the migration named. See
[removed-features/explicit-secret-paths.md](../internal/removed-features/explicit-secret-paths.md).

`provider` must be `"ssm"`. Secrets Manager is not available for application
secrets; it is available for database credentials via `[database] credentials = "secretsmanager"` — see [database.md](database.md).

## Creating the parameters

```bash
uv run python bin/ssm-secrets.py put myapp-staging secret-key
```

Preflight fails if a declared name has no parameter under the prefix, and warns
about parameters under the prefix that no name references.
