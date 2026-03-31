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
- **Clear validation**: Modules validate that config.toml provides what deploy.toml declares.
- **Environment controls implementation**: Staging might use one secret store, production another - the app doesn't care.
- **Extensible pattern**: Adding new resource types follows a consistent pattern.

## Built-in Modules

| Module | App Declares | Environment Provides | Injects |
| --- | --- | --- | --- |
| [Cache](cache.md) | `type = "redis"` | url | REDIS_URL |
| [CDN](cdn.md) | `type = "cloudfront"` | domain, key_id, private_key_param | CLOUDFRONT_DOMAIN, CLOUDFRONT_KEY_ID, CLOUDFRONT_PRIVATE_KEY |
| [Database](database.md) | `type = "postgresql"`, `extensions = [...]` | host, port, name, credentials (app + migrate), lambda | DB_HOST, DB_PORT, DB_NAME, DB_USERNAME, DB_PASSWORD |
| [Secrets](secrets.md) | `names = [...]` | provider, path_prefix | Each named secret |
| [Storage](storage.md) | `type = "s3"`, `buckets = [...]` | bucket names per declared bucket | S3\_{NAME}\_BUCKET |

## Service URL References

For referencing other services' URLs in environment variables:

```toml
[environment]
API_BASE_URL = "${services.api.url}"
```

The deployer calculates this from:

- Domain from config.toml's `[environment].domain_name`
- Path from the service's `path_pattern` in deploy.toml

For `api` with `path_pattern = "/api/*"` → `https://example.com/api`

## Validation

When deploying, the module system validates that:

1. If an app declares `[database]`, config.toml must have a `[database]` section
1. Required fields are present in the config
1. Credential types are valid (`secretsmanager` or `ssm`)

Validation errors are shown before deployment starts, helping catch configuration mismatches early.
