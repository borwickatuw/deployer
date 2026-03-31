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
