#!/bin/bash
#
# Wrapper script for OpenTofu that automatically sets the correct AWS profile
# and resolves environment names to directory paths.
#
# Usage:
#   ./bin/tofu.sh <command> <environment> [args...]
#
# Examples:
#   ./bin/tofu.sh init myapp-staging
#   ./bin/tofu.sh plan myapp-staging
#   ./bin/tofu.sh apply myapp-staging
#   ./bin/tofu.sh output myapp-staging database_url
#
# AWS Profile (checked in order):
#   1. AWS_PROFILE - if already set, use it
#   2. config.toml [aws].infra_profile - per-environment setting
#   3. Default: deployer-infra

set -e

# Resolve symlinks to find the real script location (e.g., when invoked via ~/bin/tofu.sh)
_source="${BASH_SOURCE[0]}"
while [[ -L "$_source" ]]; do
    _dir="$(cd "$(dirname "$_source")" && pwd)"
    _source="$(readlink "$_source")"
    [[ "$_source" != /* ]] && _source="$_dir/$_source"
done
SCRIPT_DIR="$(cd "$(dirname "$_source")" && pwd)"
DEPLOYER_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
unset _source _dir

show_help() {
    echo "Usage: $0 <command> <environment> [args...]"
    echo ""
    echo "Wrapper for OpenTofu that auto-selects AWS profile and environment directory."
    echo ""
    echo "Commands:"
    echo "  init, plan, apply, output, ...  - Standard tofu commands"
    echo "  rollout                         - Run init && plan && apply in sequence"
    echo ""
    echo "Examples:"
    echo "  $0 init myapp-staging"
    echo "  $0 plan myapp-staging"
    echo "  $0 apply myapp-staging"
    echo "  $0 rollout myapp-staging        # init + plan + apply"
    echo "  $0 output myapp-staging database_url"
    echo ""
    echo "Available environments:"

    if [[ -n "$DEPLOYER_ENVIRONMENTS_DIR" ]]; then
        local expanded_dir="${DEPLOYER_ENVIRONMENTS_DIR/#\~/$HOME}"
        if [[ -d "$expanded_dir" ]]; then
            for env in "$expanded_dir"/*/; do
                [[ -d "$env" ]] && echo "  - $(basename "$env")"
            done
        fi
    else
        echo "  (DEPLOYER_ENVIRONMENTS_DIR not set)"
    fi

    echo ""
    echo "AWS profile configuration:"
    echo "  AWS_PROFILE env var overrides all other settings."
    echo "  Otherwise, reads [aws].infra_profile from environment's config.toml."
    echo "  Falls back to 'deployer-infra' if not configured."
}

# Function to get infra_profile from environment's config.toml
get_infra_profile() {
    local config_file="$1/config.toml"
    if [[ -f "$config_file" ]]; then
        # Extract infra_profile from [aws] section using awk
        # Handles: infra_profile = "value"  # comment
        awk '
            /^\[aws\]/ { in_aws = 1; next }
            /^\[/ { in_aws = 0 }
            in_aws && /^infra_profile/ {
                # Remove everything up to and including =
                sub(/^infra_profile[[:space:]]*=[[:space:]]*/, "")
                # Remove trailing comment
                sub(/[[:space:]]*#.*$/, "")
                # Remove quotes
                gsub(/"/, "")
                print
                exit
            }
        ' "$config_file"
    fi
}

# Load .env only for DEPLOYER_ENVIRONMENTS_DIR (not AWS profiles)
if [[ -f "$DEPLOYER_ROOT/.env" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        # Skip comments and empty lines
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # Parse KEY=value - only load DEPLOYER_ENVIRONMENTS_DIR
        if [[ "$line" =~ ^DEPLOYER_ENVIRONMENTS_DIR=(.*)$ ]]; then
            value="${BASH_REMATCH[1]}"
            # Remove surrounding quotes
            value="${value#\"}"
            value="${value%\"}"
            value="${value#\'}"
            value="${value%\'}"
            # Only set if not already in environment
            if [[ -z "${DEPLOYER_ENVIRONMENTS_DIR+x}" ]]; then
                export DEPLOYER_ENVIRONMENTS_DIR="$value"
            fi
        fi
    done < "$DEPLOYER_ROOT/.env"
fi

# Expand ~ in DEPLOYER_ENVIRONMENTS_DIR
if [[ -n "$DEPLOYER_ENVIRONMENTS_DIR" ]]; then
    DEPLOYER_ENVIRONMENTS_DIR="${DEPLOYER_ENVIRONMENTS_DIR/#\~/$HOME}"
fi

# Show help if no arguments, -h, or --help
if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" ]]; then
    show_help
    exit 0
fi

# First argument is the tofu command
TOFU_CMD="$1"
shift

# Second argument must be environment name
if [[ $# -eq 0 ]]; then
    echo "Error: Missing environment name" >&2
    echo "Usage: $0 <command> <environment> [args...]" >&2
    echo "Run '$0 --help' for more information." >&2
    exit 1
fi

ENV_NAME="$1"
shift

# Function to resolve environment name to directory path
resolve_env_dir() {
    local env_name="$1"

    if [[ -z "$DEPLOYER_ENVIRONMENTS_DIR" ]]; then
        return 1
    fi

    if [[ -d "$DEPLOYER_ENVIRONMENTS_DIR/$env_name" ]]; then
        echo "$DEPLOYER_ENVIRONMENTS_DIR/$env_name"
        return 0
    fi

    return 1
}

# Resolve environment directory
if [[ -z "$DEPLOYER_ENVIRONMENTS_DIR" ]]; then
    echo "Error: DEPLOYER_ENVIRONMENTS_DIR environment variable is not set." >&2
    echo "Set it in your .env file:" >&2
    echo "  DEPLOYER_ENVIRONMENTS_DIR=~/deployer-environments" >&2
    exit 1
fi

if ENV_DIR=$(resolve_env_dir "$ENV_NAME"); then
    echo "Environment: $ENV_NAME"
    echo "Directory:   $ENV_DIR"
else
    echo "Error: Environment '$ENV_NAME' not found" >&2
    echo "Searched: $DEPLOYER_ENVIRONMENTS_DIR/$ENV_NAME" >&2
    echo "" >&2
    echo "Run '$0 --help' to see available environments." >&2
    exit 1
fi

if [[ ! -f "$ENV_DIR/config.toml" ]]; then
    echo "" >&2
    echo "Warning: $ENV_NAME has no config.toml — this doesn't look like an app environment." >&2
    echo "If this is a bootstrap or module directory, run tofu directly:" >&2
    echo "  AWS_PROFILE=admin tofu -chdir=$ENV_DIR $TOFU_CMD" >&2
    echo "" >&2
    exit 1
fi

# Determine which profile to use
# Priority: AWS_PROFILE > config.toml [aws].infra_profile > default
if [[ -n "$AWS_PROFILE" ]]; then
    echo "AWS Profile: $AWS_PROFILE (from AWS_PROFILE)"
else
    # Check environment's config.toml for [aws].infra_profile
    ENV_PROFILE=$(get_infra_profile "$ENV_DIR")
    if [[ -n "$ENV_PROFILE" ]]; then
        export AWS_PROFILE="$ENV_PROFILE"
        echo "AWS Profile: $AWS_PROFILE (from $ENV_NAME/config.toml [aws].infra_profile)"
    else
        export AWS_PROFILE="deployer-infra"
        echo "AWS Profile: $AWS_PROFILE (default)"
    fi
fi
echo ""

# Plan file management
PLANS_DIR="$DEPLOYER_ROOT/plans"
PLAN_FILE="$PLANS_DIR/$ENV_NAME.tfplan"

# Post-apply hook: resolve config and push to S3
# Only runs after a successful apply, and only if the resolved-configs
# S3 bucket exists. Failure is a warning, not a fatal error.
post_apply_hook() {
    local env_name="$1"

    # Only resolve config for environments that have a config.toml
    # (bootstrap, shared-infra, etc. don't have one)
    if [[ ! -f "$ENV_DIR/config.toml" ]]; then
        return
    fi

    echo ""
    echo "=== Post-apply: resolving config ==="
    if (cd "$DEPLOYER_ROOT" && uv run python bin/resolve-config.py "$env_name" --push-s3); then
        echo ""
    else
        echo "" >&2
        echo "Warning: Failed to push resolved config to S3." >&2
        echo "You can push manually: cd $DEPLOYER_ROOT && uv run python bin/resolve-config.py $env_name --push-s3" >&2
        echo "" >&2
    fi
}

case "$TOFU_CMD" in
    rollout)
        # Run init, plan, and apply in sequence
        echo "=== Running init ==="
        tofu "-chdir=$ENV_DIR" init "$@"
        INIT_EXIT=$?
        if [[ $INIT_EXIT -ne 0 ]]; then
            echo "Init failed with exit code $INIT_EXIT" >&2
            exit $INIT_EXIT
        fi

        echo ""
        echo "=== Running plan ==="
        mkdir -p "$PLANS_DIR"
        tofu "-chdir=$ENV_DIR" plan -out="$PLAN_FILE"
        PLAN_EXIT=$?
        if [[ $PLAN_EXIT -ne 0 ]]; then
            echo "Plan failed with exit code $PLAN_EXIT" >&2
            exit $PLAN_EXIT
        fi

        echo ""
        echo "=== Running apply ==="
        tofu "-chdir=$ENV_DIR" apply "$PLAN_FILE"
        APPLY_EXIT=$?
        rm -f "$PLAN_FILE"

        if [[ $APPLY_EXIT -eq 0 ]]; then
            post_apply_hook "$ENV_NAME"
        fi

        exit $APPLY_EXIT
        ;;
    plan)
        # Create plans directory if needed
        mkdir -p "$PLANS_DIR"

        # Warn if overwriting existing plan
        if [[ -f "$PLAN_FILE" ]]; then
            echo "Overwriting existing plan for $ENV_NAME"
            echo ""
        fi

        # Run plan with -out, passing through any additional arguments
        tofu "-chdir=$ENV_DIR" plan -out="$PLAN_FILE" "$@"
        PLAN_EXIT=$?

        if [[ $PLAN_EXIT -eq 0 ]]; then
            echo ""
            echo "Or run:"
            echo "    bin/tofu.sh apply $ENV_NAME"
        fi

        exit $PLAN_EXIT
        ;;
    apply)
        if [[ -f "$PLAN_FILE" ]]; then
            echo "Using saved plan: $PLAN_FILE"
            echo ""

            # Run apply with the saved plan
            tofu "-chdir=$ENV_DIR" apply "$PLAN_FILE"
            APPLY_EXIT=$?

            # Delete the plan file after apply (regardless of success/failure)
            rm -f "$PLAN_FILE"
            echo ""
            echo "Deleted plan file: $PLAN_FILE"

            if [[ $APPLY_EXIT -eq 0 ]]; then
                post_apply_hook "$ENV_NAME"
            fi

            exit $APPLY_EXIT
        else
            # No saved plan - run apply normally (will prompt for confirmation)
            tofu "-chdir=$ENV_DIR" apply "$@"
            APPLY_EXIT=$?

            if [[ $APPLY_EXIT -eq 0 ]]; then
                post_apply_hook "$ENV_NAME"
            fi

            exit $APPLY_EXIT
        fi
        ;;
    *)
        # All other commands pass through unchanged
        exec tofu "-chdir=$ENV_DIR" "$TOFU_CMD" "$@"
        ;;
esac
