# How to Configure Django for Deployer

This guide covers the Django settings and configuration needed to deploy a Django application with this deployer.

## Required Settings

### ALLOWED_HOSTS

Django requires `ALLOWED_HOSTS` to be set in production. Configure it to read from an environment variable:

```python
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
```

In your `deploy.toml`:

```toml
[environment]
ALLOWED_HOSTS = ".example.com"  # Leading dot matches all subdomains
```

The `.example.com` pattern matches `staging.example.com`, `prod.example.com`, etc.

### CSRF_TRUSTED_ORIGINS

Django 4.0+ requires `CSRF_TRUSTED_ORIGINS` for POST requests over HTTPS. Without this, you'll see:

```
Forbidden (403)
CSRF verification failed. Request aborted.
Origin checking failed - https://example.com does not match any trusted origins.
```

Add this to your settings after `ALLOWED_HOSTS`:

```python
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]

# CSRF trusted origins - required for Django 4.0+ with HTTPS
# Can be set explicitly via CSRF_TRUSTED_ORIGINS env var, or derived from ALLOWED_HOSTS
_csrf_origins = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
if _csrf_origins:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(",") if o.strip()]
elif ALLOWED_HOSTS and ALLOWED_HOSTS != ["*"]:
    # Derive from ALLOWED_HOSTS by prepending https://
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h != "*"]
```

This automatically derives `CSRF_TRUSTED_ORIGINS` from `ALLOWED_HOSTS`, so you don't need to duplicate configuration.

### SECRET_KEY

Never hardcode `SECRET_KEY`. Load it from an environment variable with a fallback for development:

```python
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    import secrets
    SECRET_KEY = secrets.token_urlsafe(50)
    import warnings
    warnings.warn(
        "SECRET_KEY not set - using auto-generated key. "
        "Sessions will be invalidated on restart. "
        "Set SECRET_KEY environment variable for production.",
        RuntimeWarning,
    )
```

In `deploy.toml`, reference an SSM parameter:

```toml
[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/django-secret-key"
```

### DEBUG

Always disable debug in production:

```python
DEBUG = os.environ.get("DEBUG", "false").lower() in ("true", "1", "yes")
```

In `deploy.toml`:

```toml
[environment.staging]
DEBUG = "true"

[environment.production]
DEBUG = "false"
```

### Database Configuration

The deployer uses a **two-account database model** for security:

- **App user** (DML only): `SELECT`, `INSERT`, `UPDATE`, `DELETE` - used by runtime services
- **Migrate user** (DDL + DML): `CREATE`, `ALTER`, `DROP` - used only for migrations

This reduces blast radius if the application is compromised - attackers cannot drop tables or alter schema.

#### Settings Configuration

Use `dj-database-url` for database configuration:

```bash
uv add dj-database-url
```

```python
import dj_database_url

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
```

The deployer automatically injects `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, and `DB_PASSWORD` environment variables based on the credential mode. Your settings should construct `DATABASE_URL` from these or use the module system.

#### Commands Requiring DDL

Commands that modify the database schema (like `migrate` and `makemigrations`) need DDL privileges. Mark these in your `deploy.toml` with `ddl = true`:

```toml
[commands]
migrate = { command = ["python", "manage.py", "migrate"], ddl = true }
makemigrations = { command = ["python", "manage.py", "makemigrations"], ddl = true }
showmigrations = ["python", "manage.py", "showmigrations"]  # No DDL needed
collectstatic = ["python", "manage.py", "collectstatic", "--noinput"]
```

When you run a DDL command via `ecs-run.py`, it automatically uses the migrate task definition with DDL+DML credentials:

```bash
# Uses migrate credentials (DDL + DML)
ecs-run.py run myapp-staging migrate

# Uses app credentials (DML only)
ecs-run.py run myapp-staging collectstatic
```

## Static Files with WhiteNoise

Use WhiteNoise to serve static files without a separate web server.

### Install WhiteNoise

```bash
uv add whitenoise
```

### Configure Middleware

Add WhiteNoise middleware immediately after `SecurityMiddleware`:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # Must be here
    "django.contrib.sessions.middleware.SessionMiddleware",
    # ...
]
```

### Configure Storage

```python
STATIC_URL = "static/"
STATIC_ROOT = os.getenv("STATIC_ROOT", BASE_DIR / "staticfiles")

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}
```

Use `CompressedStaticFilesStorage` instead of `CompressedManifestStaticFilesStorage` to avoid issues with missing source maps from third-party packages.

## Dockerfile

Example Dockerfile for a Django application using uv:

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV DJANGO_SETTINGS_MODULE=myapp.settings
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock* ./

# Install dependencies
RUN uv sync --frozen

# Copy application code
COPY . .

# Collect static files (requires SECRET_KEY at build time)
ARG SECRET_KEY=build-time-secret-key
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "myapp.wsgi:application", "--bind", "0.0.0.0:8000"]
```

Key points:

- Install dependencies before copying code for better Docker layer caching
- Use `ARG SECRET_KEY` to provide a dummy key for `collectstatic` at build time
- Run `collectstatic --noinput` during build so static files are baked into the image

## .dockerignore

Exclude development files from the Docker build:

```
# Git
.git
.gitignore

# Python
__pycache__
*.pyc
*.pyo
.pytest_cache
.mypy_cache
.ruff_cache

# Virtual environments
.venv
venv

# Local settings (important!)
local_settings.py
**/local_settings.py

# IDE
.vscode
.idea

# Testing
.coverage
htmlcov

# Development databases
*.sqlite3
db.sqlite3
```

The `local_settings.py` exclusion is critical - if it gets into your Docker image, it may override production settings with development values.

## Health Check Endpoint

Create a simple health check endpoint for the load balancer:

```python
# urls.py
from django.http import HttpResponse

def health_check(request):
    return HttpResponse("OK")

urlpatterns = [
    path("health/", health_check),
    # ...
]
```

Configure it in `deploy.toml`:

```toml
[services.web]
health_check_path = "/health/"
```

Keep the health check simple - don't add database checks unless you want the service marked unhealthy when the database is down.

## deploy.toml Example

Complete example for a Django application:

```toml
[application]
name = "myapp"
source = "."

[images.web]
context = "."
dockerfile = "Dockerfile"

[services.web]
image = "web"
port = 8000
command = ["uv", "run", "gunicorn", "myapp.wsgi:application", "--bind", "0.0.0.0:8000"]
health_check_path = "/health/"

[services.celery]
image = "web"
command = ["uv", "run", "celery", "-A", "myapp", "worker", "-E"]

[environment]
DJANGO_SETTINGS_MODULE = "myapp.settings"
ALLOWED_HOSTS = ".example.com"

[environment.staging]
DEBUG = "true"

[environment.production]
DEBUG = "false"

[secrets]
SECRET_KEY = "ssm:/myapp/${environment}/django-secret-key"
DATABASE_URL = "ssm:/myapp/${environment}/database-url"
REDIS_URL = "ssm:/myapp/${environment}/redis-url"

[migrations]
enabled = true
service = "web"
command = ["uv", "run", "python", "manage.py", "migrate"]
```

## Common Issues

### CSRF Verification Failed

See [CSRF_TRUSTED_ORIGINS](#csrf_trusted_origins) above.

### Static Files 404

1. Verify WhiteNoise middleware is in the correct position
1. Check that `collectstatic` runs in the Dockerfile
1. Ensure `STATIC_ROOT` is set correctly

### ALLOWED_HOSTS Error

1. Check that `local_settings.py` is in `.dockerignore`
1. Verify `ALLOWED_HOSTS` environment variable is set in `deploy.toml`

### Database Connection Refused

1. Verify `DATABASE_URL` SSM parameter exists and is correct
1. Check security groups allow ECS tasks to reach the database

See [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) for more detailed debugging steps.
