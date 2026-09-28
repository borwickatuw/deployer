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

## Access

The task role's S3 policy covers every bucket the environment creates, but
the ECS permissions boundary above it admits only the bucket kinds listed in
`data_bucket_kinds` (`modules/bootstrap/iam-boundary.tf`). A kind missing
there is denied whatever the role grants. Deploy preflight simulates the task
role, boundary included, on every declared bucket and stops the deploy on a
denial, naming the bucket, the action and which policy denied it. To declare
a new kind of bucket, add it to `data_bucket_kinds` and apply the bootstrap
first.
