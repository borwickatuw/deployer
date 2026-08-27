#!/bin/sh
# Canonical build step for the db lambda bundles: pip dependencies, the shared
# db_common.py, and the .deps-installed marker that the archive_file
# precondition checks. Called by null_resource.lambda_dependencies in the
# db-users and db-on-shared-rds modules, and by `make lambda-deps` to
# bootstrap a fresh checkout.
set -eu
module_dir="$1"

rm -f "$module_dir"/lambda-*.zip
pip install -r "$module_dir/lambda/requirements.txt" -t "$module_dir/lambda" --upgrade --quiet
# pip byte-compiles into per-package __pycache__ dirs, and .pyc files embed
# mtimes, which would make the zip nondeterministic across installs.
find "$module_dir/lambda" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$(dirname "$0")/db_common.py" "$module_dir/lambda/db_common.py"
touch "$module_dir/lambda/.deps-installed"
