# Autoscale Module (Python)

## What it did

Python resource module (`src/deployer/modules/autoscale.py`) that supported queue-depth autoscaling for ECS services. When an app declared `[autoscale] services = ["transcoder"]`, the module validated config and injected `AUTOSCALE_NAMESPACE` and `AUTOSCALE_SERVICES` environment variables.

## Why it was removed

Speculative feature with zero real-world usage. No corresponding Terraform autoscaling infrastructure was ever built.

## Removal commit

`bdb5891`

## How to restore

1. Recover files:
   ```bash
   git checkout bdb5891^ -- src/deployer/modules/autoscale.py
   ```
2. Re-add `"autoscale"` to `KNOWN_SECTIONS` in `src/deployer/config/deploy_config.py`
3. Re-add `autoscale` field to `DeployConfig` dataclass and its `from_dict`/`get_raw_dict`/`_get_module_injected_vars`
4. Re-add `AutoscaleModule` to `src/deployer/modules/__init__.py` registry
5. Run tests to verify
