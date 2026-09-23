+++
title = "Two sources for one SSM prefix, an unused SSMGroup, and a test-only parameter"
+++

Captured 2026-09-22 from the pass-through-params adjudication (deployer
item 6, sub-phase 6-2), which recorded these as out of scope.

1. **Two sources for one SSM prefix.** `check_secrets_exist` and
   `ssm-secrets.py check` list secrets under a prefix derived from the
   environment name, then compare against required paths built from
   config.toml's `[secrets] path_prefix`. The templates set both to
   `/{app}/{env}`, so they agree today; an environment whose `path_prefix`
   differs would report every secret missing. One canonical location: derive
   the listing prefix from `path_prefix` too, or drop `path_prefix`.
2. **`get_secrets_from_deploy_toml`'s `env_config` parameter** is only ever
   passed by tests; its one production caller never uses it. Removable under
   "tests reflect actual usage", but §53l cleared it as a documented
   contract, so the operator decides.
3. **`SSMGroup` in `bin/ssm-secrets.py`** is unused and its `invoke` body is
   a `pass` placeholder.

**Complexity**: Low each; 1 is the one that can bite.
