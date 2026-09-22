+++
title = "Templates and deployer.tf can drop the per-key S3 bucket outputs now that placeholders index maps"
+++

Captured 2026-09-22 from the map-indexing phase (deployer item 8), which
added `${tofu:NAME.KEY}` but left the consumers alone.

**Current state.** `environments/deployer.tf` hand-defines `s3_media_bucket`,
`s3_originals_bucket` and `s3_cache_bucket` as
`try(module.infrastructure.s3_bucket_names["media"], null)` and so on; the
standalone and shared-app templates' `config.toml.example` and the
CONFIG-REFERENCE example use `${tofu:s3_media_bucket}`. Those per-key
outputs are exactly what the dotted form makes unnecessary, and the dotted
form gives a better error when a key is missing.

**Proposed enhancement.** Switch the templates and the example to
`${tofu:s3_bucket_names.media}`; then retire the three per-key outputs from
deployer.tf.

**Dependencies.** Every existing environment's config.toml still says
`${tofu:s3_media_bucket}`, so the outputs cannot go until each linked
environment's config.toml is migrated (one grep across the environments
directory). Do the templates first; drop the outputs in a second step with
that grep clean.

**Complexity**: Low.
