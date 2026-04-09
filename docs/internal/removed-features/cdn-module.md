# CDN Module (Python + Terraform)

## What it did

**Python module** (`src/deployer/modules/cdn.py`): Declared `[cdn]` as a deploy.toml resource module. When an app declared `[cdn] type = "cloudfront"`, the module validated config and injected `CLOUDFRONT_DOMAIN`, `CLOUDFRONT_KEY_ID`, and `CLOUDFRONT_PRIVATE_KEY` environment variables into ECS tasks.

**Terraform module** (`modules/cloudfront/`): Created a CloudFront distribution for S3 media delivery with Origin Access Control, signed URL support, and optional custom domains. Used by both the standalone root `main.tf` and `shared-infrastructure/cloudfront.tf`.

## Why it was removed

Neither the Python module nor the Terraform module had any real-world usage. The CloudFront media CDN pattern was speculative. The `cloudfront-alb` module (custom error pages in front of ALB) IS actively used and was kept.

## Removal commit

`bdb5891`

## How to restore

1. Recover Python files:
   ```bash
   git checkout bdb5891^ -- src/deployer/modules/cdn.py src/deployer/modules/autoscale.py
   ```
1. Re-add `"cdn"` to `KNOWN_SECTIONS` in `src/deployer/config/deploy_config.py`
1. Re-add `cdn` field to `DeployConfig` dataclass and its `from_dict`/`get_raw_dict`/`_get_module_injected_vars`
1. Re-add `CdnModule` to `src/deployer/modules/__init__.py` registry
1. Recover Terraform files:
   ```bash
   git checkout bdb5891^ -- modules/cloudfront/ modules/shared-infrastructure/cloudfront.tf
   ```
1. Re-add CloudFront variables to `modules/shared-infrastructure/variables.tf` and outputs to `outputs.tf`
1. Run tests to verify
