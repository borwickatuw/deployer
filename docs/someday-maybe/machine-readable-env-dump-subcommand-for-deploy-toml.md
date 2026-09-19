+++
title = "Machine-readable env dump subcommand for deploy.toml"
source = "deployer"
captured = "2026-08-21"
+++

A machine-readable env dump for deploy.toml -- its own subcommand with real shell/dotenv quoting, not the deploy log. 53j-4a settled that print_environment_config is human narration: it prints (unset) for an empty value, which is a marker and not a parseable encoding, and a value literally equal to '(unset)' is indistinguishable from an empty one. A $NONE sentinel was rejected for the log because a dotenv parser takes it literally, a shell expands it, set -u errors on it, and it collides with a real value. If anyone ever wants to pipe the environment somewhere, that is a separate command with quoting done properly -- explicitly out of scope for 53j-4.
