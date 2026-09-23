+++
title = "preflight's unreferenced-SSM-secrets warning flags deployer's own parameters. `make"
source = "deployer"
captured = "2026-09-23"
+++

preflight's unreferenced-SSM-secrets warning flags deployer's own parameters. `make staging` in havoc (2026-09-23, deployer v4.0.0) warned "8 SSM secret(s) not referenced in deploy.toml": /havoc/staging/db-extensions and the seven /havoc/staging/service-state-hash-<service> entries. All eight are deployer's bookkeeping, written by deploy/service.py (state hashes) and deploy/extensions.py (db-extensions) under the app's SSM prefix; none is a secret and none could ever be referenced from deploy.toml. The check in deploy/preflight.py (and bin/ssm-secrets.py's "Extra secrets" list) should exclude the parameter names deployer owns — one place that names them, used by both — so the warning only fires on a stale operator-created secret, which is what it exists to catch. Today every deploy of an app with N services prints N+1 false warnings and trains the operator to skip the block.
