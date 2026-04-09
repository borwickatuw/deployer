# Speed Test Subcommand

## What it did

`deploy.py speed-test` subcommand (~260 lines) that measured deployment visibility time. It:

- Injected a `BUILD_MARKER` into deploy.toml's `[environment]` section
- Authenticated with Cognito (`CognitoAuthenticator` class) to access protected staging
- Ran a deployment while polling the health endpoint (`MarkerPoller` class) for the marker
- Reported time-to-visible vs time-to-stable metrics

## Why it was removed

Not in active use. The Cognito authentication flow, marker injection, and HTTP polling added complexity to the critical deployment script. When production deployments exist, the actual requirements for speed testing will likely differ from this speculative implementation.

## Removal commit

`bdb5891`

## How to restore

1. Recover the code from git history:
   ```bash
   git show bdb5891^:bin/deploy.py > /tmp/old_deploy.py
   ```
1. Copy the `CognitoAuthenticator`, `MarkerPoller`, `_inject_marker`, `_get_health_check_path`, and `cmd_speed_test` functions
1. Re-add the `speed-test` dispatch in `main()`
1. Re-add imports: `json`, `os`, `threading`, `time`, `boto3`, `requests`, `get_staging_url_from_config`, `get_environment_path`, `log_warning`
