# Resource Module System

The deployer uses a module system to separate **what an application needs** from **how an environment provides it**.

## Overview

Applications declare their needs declaratively in `deploy.toml`:

```toml
[database]
type = "postgresql"

[cache]
type = "redis"

[secrets]
names = ["SECRET_KEY", "SIGNED_URL_SECRET"]
```

Environments provide implementation details in `config.toml`:

```toml
[database]
host = "${tofu:db_host}"
port = "${tofu:db_port}"
name = "${tofu:db_name}"
credentials = "secretsmanager"
username_secret = "${tofu:db_username_secret_arn}"
password_secret = "${tofu:db_password_secret_arn}"

[secrets]
provider = "ssm"
path_prefix = "/myapp/staging"
```

## Benefits

- **Applications are infrastructure-agnostic**: Apps don't know if credentials come from SSM, Secrets Manager, or environment files.
- **Environment controls implementation**: Staging might use one secret store, production another - the app doesn't care.
- **Clear validation**: Modules validate that config.toml provides what deploy.toml declares.
- **Extensible pattern**: Adding new resource types follows a consistent pattern.

## Built-in Modules

### Database Module

**Application declares** (`deploy.toml`):
```toml
[database]
type = "postgresql"
```

**Environment provides** (`config.toml`):
```toml
[database]
host = "${tofu:db_host}"
port = "${tofu:db_port}"
name = "${tofu:db_name}"
credentials = "secretsmanager"  # or "ssm"
username_secret = "${tofu:db_username_secret_arn}"
password_secret = "${tofu:db_password_secret_arn}"
```

**Injects**: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME` (secret), `DB_PASSWORD` (secret)

### Cache Module

**Application declares** (`deploy.toml`):
```toml
[cache]
type = "redis"
```

**Environment provides** (`config.toml`):
```toml
[cache]
url = "${tofu:redis_url}"
```

**Injects**: `REDIS_URL`

### Storage Module

**Application declares** (`deploy.toml`):
```toml
[storage]
type = "s3"
buckets = ["media"]  # or ["originals", "media"]
```

**Environment provides** (`config.toml`):
```toml
[storage]
media_bucket = "${tofu:s3_media_bucket}"
media_bucket_region = "us-west-2"  # optional
originals_bucket = "${tofu:s3_originals_bucket}"  # if declared
```

**Injects**: `S3_MEDIA_BUCKET`, `S3_MEDIA_BUCKET_REGION` (if specified), `S3_ORIGINALS_BUCKET` (if declared)

### CDN Module

**Application declares** (`deploy.toml`):
```toml
[cdn]
type = "cloudfront"
```

**Environment provides** (`config.toml`):
```toml
[cdn]
domain = "${tofu:cloudfront_domain}"
key_id = "${tofu:cloudfront_key_id}"
private_key_param = "/myapp/staging/cloudfront-private-key"
```

**Injects**: `CLOUDFRONT_DOMAIN`, `CLOUDFRONT_KEY_ID`, `CLOUDFRONT_PRIVATE_KEY` (secret)

### Secrets Module

**Application declares** (`deploy.toml`):
```toml
[secrets]
names = ["SECRET_KEY", "SIGNED_URL_SECRET", "DATACITE_PASSWORD"]
```

**Environment provides** (`config.toml`):
```toml
[secrets]
provider = "ssm"
path_prefix = "/myapp/staging"
```

**Name normalization**: `SECRET_KEY` → `secret-key`, `SIGNED_URL_SECRET` → `signed-url-secret`

**Injects**: Each named secret from SSM Parameter Store

## Service URL References

For referencing other services' URLs in environment variables:

```toml
[environment]
API_BASE_URL = "${services.api.url}"
```

The deployer calculates this from:
- Domain from config.toml's `[environment].domain_name`
- Path from the service's `path_pattern` in deploy.toml

For `api` with `path_pattern = "/iiif/*"` → `https://example.com/iiif`

## Validation

When deploying, the module system validates that:

1. If an app declares `[database]`, config.toml must have a `[database]` section
2. Required fields are present in the config
3. Credential types are valid (`secretsmanager` or `ssm`)

Validation errors are shown before deployment starts, helping catch configuration mismatches early.

## Summary Table

| Module | App Declares | Environment Provides | Injects |
|--------|--------------|---------------------|---------|
| database | `type = "postgresql"` | host, port, name, credentials | DB_HOST, DB_PORT, DB_NAME, DB_USERNAME, DB_PASSWORD |
| cache | `type = "redis"` | url | REDIS_URL |
| storage | `type = "s3"`, `buckets = [...]` | bucket names per declared bucket | S3_{NAME}_BUCKET |
| cdn | `type = "cloudfront"` | domain, key_id, private_key_param | CLOUDFRONT_DOMAIN, CLOUDFRONT_KEY_ID, CLOUDFRONT_PRIVATE_KEY |
| secrets | `names = [...]` | provider, path_prefix | Each named secret |
