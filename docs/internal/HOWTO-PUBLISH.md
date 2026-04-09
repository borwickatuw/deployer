# Publishing to the Public Repository

This project uses a dual-remote workflow: a private working repo for day-to-day development, and a public repo for open-source releases.

## Setup

The repo has two remotes:

- `private` — the internal working repository (push here day-to-day)
- `public` — the public release repository on GitHub

## Pre-Publish Review Checklist

Before pushing to `public`, review the diff since your last public push:

```bash
# Compare local main with what's on public
git log public/main..main --oneline

# Review the full diff
git diff public/main..main
```

Search for content that should not be published:

```bash
# Real project or environment names
git diff public/main..main | grep -iE 'your-project-names-here'

# AWS account IDs, ARNs, resource IDs
git diff public/main..main | grep -E '[0-9]{12}|arn:aws'

# Internal hostnames, domain names, IP addresses
git diff public/main..main | grep -E '\.(internal|local|corp)\b|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+'

# Credentials, API keys, tokens
git diff public/main..main | grep -iE 'password|secret|token|api.key|credential'

# Check for new files that should be gitignored
git diff public/main..main --name-only
```

## Publishing

Once the review is clean:

```bash
git push public main
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

# Re-add remotes (filter-repo removes them)
git remote add private <private-repo-url>
git remote add public <public-repo-url>

# Force-push the rewritten history
git push --force private main
git push --force public main
```

Note: `filter-repo` removes all remotes as a safety measure. You must re-add them after a rewrite. All commit hashes will change.
