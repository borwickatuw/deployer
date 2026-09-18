# Publishing to the Public Repository

This project uses a dual-remote workflow: a private working repo for day-to-day development, and a public repo for open-source releases.

## Setup

The repo has two remotes, one internal and one public. `git remote -v` says
which is which — the URLs, not the names, are the authority, and a checkout is
free to call them anything. The commands below take the public one as `$PUB`:

```bash
git remote -v                  # identify the public release repository
PUB=origin                     # whatever name that repository has here
```

## Pre-Publish Review Checklist

Before pushing to the public remote, review the diff since your last public
push:

```bash
# Compare local main with what's published
git log $PUB/main..main --oneline

# Review the full diff
git diff $PUB/main..main
```

Search for content that should not be published:

```bash
# Real project or environment names
git diff $PUB/main..main | grep -iE 'your-project-names-here'

# AWS account IDs, ARNs, resource IDs
git diff $PUB/main..main | grep -E '[0-9]{12}|arn:aws'

# Internal hostnames, domain names, IP addresses
git diff $PUB/main..main | grep -E '\.(internal|local|corp)\b|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'

# Credentials, API keys, tokens
git diff $PUB/main..main | grep -iE 'password|secret|token|api.key|credential'

# Check for new files that should be gitignored
git diff $PUB/main..main --name-only
```

These greps read the diff. `make security-secrets` reads every tracked file
against `.secrets.baseline`, which is the check that catches something already
committed on an earlier pass — run it too, not instead.

## Publishing

Once the review is clean:

```bash
git push $PUB main
```

## One-Time History Rewrite (if needed)

If internal names leaked into git history, use `git-filter-repo` to rewrite:

```bash
# Install
uv tool install git-filter-repo

# Create a replacements file (one per line: old==>new)
cat > /tmp/replacements.txt << 'EOF'
internal-name==>generic-name
EOF

# Rewrite blob content
git filter-repo --replace-text /tmp/replacements.txt --force

# Rewrite commit messages too
git filter-repo --replace-message /tmp/replacements.txt --force

# Verify
git log --all -p | grep -i 'internal-name'

# Re-add remotes (filter-repo removes them), under the names this checkout used
git remote add <internal-name> <private-repo-url>
git remote add <public-name> <public-repo-url>

# Force-push the rewritten history
git push --force <internal-name> main
git push --force <public-name> main
```

Note: `filter-repo` removes all remotes as a safety measure. You must re-add them after a rewrite. All commit hashes will change.
