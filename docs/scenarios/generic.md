# Framework-Agnostic Deployment Guide

This guide covers deploying any containerized application with the deployer, regardless of framework.

## Core Requirements

Your application must:

1. **Run in a Docker container** - Build from a Dockerfile
1. **Listen on a configurable port** - Receive HTTP traffic from the ALB
1. **Have a health check endpoint** - Return 200-399 for health checks
1. **Read configuration from environment variables** - No hardcoded secrets

## deploy.toml Configuration

```toml
[application]
name = "myapp"
source = "."

[images.web]
context = "."
dockerfile = "Dockerfile"

[services.web]
image = "web"
port = 3000                    # Your application's port
command = ["./start.sh"]       # Your start command
health_check_path = "/health"  # Your health endpoint

# Optional: background workers
[services.worker]
image = "web"
command = ["./worker.sh"]

[environment]
# Base environment variables for all environments
LOG_LEVEL = "info"

[environment.staging]
# Staging-specific overrides
DEBUG = "true"

[environment.production]
# Production-specific overrides
DEBUG = "false"

[secrets]
# References to SSM Parameter Store
API_KEY = "ssm:/myapp/${environment}/api-key"
DATABASE_URL = "ssm:/myapp/${environment}/database-url"

# Non-interactive commands only (interactive commands can't run via ecs-run.py)
[commands]
migrate = ["./run-migrations.sh"]
setup = ["./setup.sh"]
healthcheck = ["./healthcheck.sh"]

[migrations]
enabled = true
service = "web"
command = ["./run-migrations.sh"]
```

## Container Port

Set the port in your environment's `terraform.tfvars`:

```hcl
# Common ports by framework:
# - Django: 8000 (default)
# - Rails: 3000
# - Node.js/Express: 3000
# - Go: 8080
# - Java/Spring: 8080

container_port = 3000
```

## Health Check Endpoint

Your health check should:

1. Return HTTP 200-399 when healthy
1. Be fast (under 10 seconds)
1. Not require authentication
1. Optionally check critical dependencies

Example (any language):

```
GET /health
Response: 200 OK
Body: "OK" (or JSON: {"status": "healthy"})
```

## Custom Commands

The `[commands]` section lets you define named non-interactive commands for your framework:

```toml
[commands]
# Node.js example
migrate = ["npm", "run", "migrate"]
seed = ["npm", "run", "seed"]
check = ["npm", "run", "lint"]

# Go example
migrate = ["./app", "migrate"]
seed = ["./app", "seed"]

# Generic shell scripts
migrate = ["./bin/migrate.sh"]
healthcheck = ["./bin/healthcheck.sh"]
```

**Note:** Only non-interactive commands are supported. Interactive commands cannot run via ecs-run.py since there's no TTY attached.

Run commands with:

```bash
# List available commands
python bin/ecs-run.py run --list-commands --deploy-toml ../myapp/deploy.toml

# Run a command
python bin/ecs-run.py run myapp-staging migrate --deploy-toml ../myapp/deploy.toml
```

**Note:** Only non-interactive commands are supported. For interactive commands, use `ecs-run.py exec`.

## Dockerfile Guidelines

```dockerfile
# Use appropriate base image
FROM node:20-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY package*.json ./
RUN npm ci --only=production

# Copy application code
COPY . .

# Build if needed
RUN npm run build

# Expose your port
EXPOSE 3000

# Use array form for CMD
CMD ["node", "server.js"]
```

Key points:

- Copy dependency files first for better caching
- Use multi-stage builds if needed
- Expose the port your app listens on
- Use array form for CMD (not shell form)

## .dockerignore

```
# Version control
.git
.gitignore

# Dependencies (reinstalled in container)
node_modules
vendor

# Local development
.env
.env.local
*.local.*

# IDE
.vscode
.idea

# Build artifacts
dist
build
*.log
```

## Secrets Management

Reference secrets from SSM Parameter Store:

```toml
[secrets]
DATABASE_URL = "ssm:/myapp/${environment}/database-url"
API_KEY = "ssm:/myapp/${environment}/api-key"
SECRET_KEY = "ssm:/myapp/${environment}/secret-key"
```

The `${environment}` placeholder resolves to `staging` or `production`.

Create secrets with:

```bash
aws ssm put-parameter \
  --name "/myapp/staging/api-key" \
  --type SecureString \
  --value "your-secret-value"
```

## Multiple Services

For applications with multiple services (web + workers):

```toml
[services.web]
image = "web"
port = 3000
command = ["./web-server.sh"]
health_check_path = "/health"

[services.worker]
image = "web"  # Can reuse same image
command = ["./worker.sh"]
# No port - workers don't receive HTTP traffic

[services.scheduler]
image = "web"
command = ["./scheduler.sh"]
```

Configure sizing in `terraform.tfvars`:

```hcl
services = {
  web = {
    cpu           = 512
    memory        = 1024
    replicas      = 2
    load_balanced = true
    port          = 3000
  }
  worker = {
    cpu           = 256
    memory        = 512
    replicas      = 1
    load_balanced = false
  }
  scheduler = {
    cpu           = 256
    memory        = 512
    replicas      = 1
    load_balanced = false
  }
}
```

## Framework-Specific Guides

For detailed configuration:

- [Django](django.md) - Python web framework
- [Rails](rails.md) - Ruby web framework

## Common Issues

### Container Exits Immediately

- Check your CMD runs in the foreground (not background)
- Verify all required environment variables are set
- Check CloudWatch logs for error messages

### Health Check Failures

- Verify health endpoint returns 200-399
- Ensure health endpoint doesn't require auth
- Check that the port matches `container_port`

### Connection Refused

- Verify your app binds to `0.0.0.0`, not `localhost`
- Check the port matches what's configured
- Ensure security groups allow traffic

See [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) for more debugging steps.
