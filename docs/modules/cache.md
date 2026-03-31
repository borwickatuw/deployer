# Cache Module

## Application declares (`deploy.toml`)

```toml
[cache]
type = "redis"
```

## Environment provides (`config.toml`)

```toml
[cache]
url = "${tofu:redis_url}"
```

**Injects**: `REDIS_URL`
