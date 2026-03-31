# CDN Module

## Application declares (`deploy.toml`)

```toml
[cdn]
type = "cloudfront"
```

## Environment provides (`config.toml`)

```toml
[cdn]
domain = "${tofu:cloudfront_domain}"
key_id = "${tofu:cloudfront_key_id}"
private_key_param = "/myapp/staging/cloudfront-private-key"
```

**Injects**: `CLOUDFRONT_DOMAIN`, `CLOUDFRONT_KEY_ID`, `CLOUDFRONT_PRIVATE_KEY` (secret)
