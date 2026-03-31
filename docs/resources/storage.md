# Storage Module

## Application declares (`deploy.toml`)

```toml
[storage]
type = "s3"
buckets = ["media"]  # or ["originals", "media"]
```

## Environment provides (`config.toml`)

```toml
[storage]
media_bucket = "${tofu:s3_media_bucket}"
media_bucket_region = "us-west-2"  # optional
originals_bucket = "${tofu:s3_originals_bucket}"  # if declared
```

**Injects**: `S3_MEDIA_BUCKET`, `S3_MEDIA_BUCKET_REGION` (if specified), `S3_ORIGINALS_BUCKET` (if declared)
