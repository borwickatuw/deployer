+++
title = "Developer Experience: Tofu Placeholder Map Indexing"
+++

**Current state**: The `${tofu:...}` resolver in deploy.py doesn't support indexing into map outputs (e.g., `${tofu:s3_bucket_names.bucket_a}`).

**Enhancement**: Adding map indexing would eliminate the need for per-bucket individual outputs in main.tf, keeping the standardized template truly zero-edit. Without it, projects with custom S3 buckets need hand-added outputs.

**Complexity**: Low-Medium — see `src/deployer/` for the resolver code.
