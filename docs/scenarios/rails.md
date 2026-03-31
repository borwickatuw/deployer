# How to Configure Rails for Deployer

This guide covers the configuration needed to deploy a Rails application with this deployer.

## Required Configuration

### deploy.toml

Rails applications use port 3000 by default. Configure your `deploy.toml` with the Rails-specific settings:

```toml
[application]
name = "myapp"
source = "."

[images.web]
context = "."
dockerfile = "Dockerfile"

[services.web]
image = "web"
port = 3000
command = ["bundle", "exec", "rails", "server", "-b", "0.0.0.0", "-p", "3000"]
health_check_path = "/health"

# Background worker (Sidekiq)
[services.worker]
image = "web"
command = ["bundle", "exec", "sidekiq"]

[environment]
RAILS_ENV = "production"
RAILS_LOG_TO_STDOUT = "true"
RAILS_SERVE_STATIC_FILES = "true"

[environment.staging]
RAILS_LOG_LEVEL = "debug"

[environment.production]
RAILS_LOG_LEVEL = "info"

[secrets]
SECRET_KEY_BASE = "ssm:/myapp/${environment}/secret-key-base"
DATABASE_URL = "ssm:/myapp/${environment}/database-url"
REDIS_URL = "ssm:/myapp/${environment}/redis-url"

# Non-interactive commands only (interactive commands like console can't run via ecs-run.py)
[commands]
migrate = ["bundle", "exec", "rake", "db:migrate"]
assets = ["bundle", "exec", "rake", "assets:precompile"]
db_seed = ["bundle", "exec", "rake", "db:seed"]

[migrations]
enabled = true
service = "web"
command = ["bundle", "exec", "rake", "db:migrate"]
```

### Container Port

Rails uses port 3000 by default. Set this in your environment's `terraform.tfvars`:

```hcl
container_port = 3000
```

### Health Check Endpoint

Create a simple health check controller:

```ruby
# app/controllers/health_controller.rb
class HealthController < ApplicationController
  skip_before_action :authenticate_user!, if: -> { defined?(authenticate_user!) }

  def show
    render plain: "OK"
  end
end
```

Add the route:

```ruby
# config/routes.rb
get "/health", to: "health#show"
```

## Dockerfile Example

```dockerfile
FROM ruby:3.2-slim

RUN apt-get update -qq && \
    apt-get install -y build-essential libpq-dev nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY Gemfile Gemfile.lock ./
RUN bundle config set --local without development test && \
    bundle install --jobs 4

# Copy application code
COPY . .

# Precompile assets
ARG SECRET_KEY_BASE=build-time-secret
RUN SECRET_KEY_BASE=$SECRET_KEY_BASE bundle exec rails assets:precompile

EXPOSE 3000

CMD ["bundle", "exec", "rails", "server", "-b", "0.0.0.0", "-p", "3000"]
```

## Running Commands

With the `[commands]` section configured, use the `run` subcommand:

```bash
# List available commands
python bin/ecs-run.py run --list-commands --deploy-toml ../myapp/deploy.toml

# Run migrations
python bin/ecs-run.py run myapp-staging migrate --deploy-toml ../myapp/deploy.toml
```

**Note:** Only non-interactive commands are supported via `ecs-run.py run`. For interactive commands like Rails console, use `ecs-run.py exec`:

```bash
python bin/ecs-run.py exec myapp-staging bundle exec rails console
```

## Common Issues

### Assets Not Loading

1. Ensure `RAILS_SERVE_STATIC_FILES=true` is set
1. Verify assets are precompiled in Dockerfile
1. Check that `public/assets/` is not in `.dockerignore`

### Database Connection Issues

1. Verify `DATABASE_URL` SSM parameter is set correctly
1. Check security groups allow ECS tasks to reach RDS
1. Ensure database exists and migrations have run

See [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) for more debugging steps.
