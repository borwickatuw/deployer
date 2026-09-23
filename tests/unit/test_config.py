"""Tests for deployer.config package."""

import pytest

from deployer.config import (
    ImageConfig,
    container_health_check,
    get_compose_services,
    parse_deploy_config,
    parse_deploy_toml,
    parse_docker_compose,
)


class TestParseDeployToml:
    """Tests for parse_deploy_toml function."""

    def test_parse_valid_file(self, sample_deploy_toml):
        """Test parsing a valid deploy.toml file."""
        result = parse_deploy_toml(sample_deploy_toml)

        assert "application" in result
        assert result["application"]["name"] == "testapp"
        assert "services" in result
        assert "images" in result

    def test_parse_nonexistent_file(self, tmp_path):
        """Test parsing a non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            parse_deploy_toml(tmp_path / "nonexistent.toml")


class TestDeployConfigServices:
    """Tests for DeployConfig.services (replaces get_deploy_services)."""

    def test_extract_services(self, tmp_path):
        """Test extracting services from deploy config."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.web]
image = "web"
port = 8000

[services.worker]
image = "worker"
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert "web" in config.services
        assert config.services["web"].image == "web"
        assert config.services["web"].port == 8000
        assert "worker" in config.services

    def test_empty_services(self, tmp_path):
        """Test with no services."""
        (tmp_path / "deploy.toml").write_text('[application]\nname = "test"', encoding="utf-8")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        assert config.services == {}

    def test_interruptible_flag(self, tmp_path):
        """Test interruptible flag parsing."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.web]
image = "web"
port = 8000

[services.worker]
image = "web"
interruptible = true
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert config.services["web"].interruptible is False
        assert config.services["worker"].interruptible is True

    def test_interruptible_in_raw_dict(self, tmp_path):
        """Test interruptible flag roundtrips through get_raw_dict."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.worker]
image = "web"
interruptible = true
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        raw = config.get_raw_dict()

        assert raw["services"]["worker"]["interruptible"] is True

    def test_min_replicas_roundtrips_without_warnings(self, tmp_path):
        """min_replicas parses as a known key and survives get_raw_dict.

        The scaling validation reads it off the raw dict; when the field was
        missing from ServiceConfig, get_raw_dict silently dropped it and a
        declared min_replicas = 0 still validated against the default floor
        of 1 (observed on a 2026-08-31 staging deploy).
        """
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.transcoder]
image = "transcoder"
min_replicas = 0
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert config.get_warnings() == []
        assert config.services["transcoder"].min_replicas == 0
        assert config.get_raw_dict()["services"]["transcoder"]["min_replicas"] == 0

    def test_deployment_override_fields_parse(self, tmp_path):
        """Per-service rollout override keys parse without warnings."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.beat]
image = "web"
minimum_healthy_percent = 0
maximum_percent = 100
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert config.services["beat"].minimum_healthy_percent == 0
        assert config.services["beat"].maximum_percent == 100
        assert config.get_warnings() == []

    def test_deployment_override_fields_default_to_none(self, tmp_path):
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.web]
image = "web"
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert config.services["web"].minimum_healthy_percent is None
        assert config.services["web"].maximum_percent is None

    def test_deployment_override_in_raw_dict(self, tmp_path):
        """The override keys round-trip through get_raw_dict.

        service.py reads raw dicts, not ServiceConfig; a field missing here
        silently never reaches ECS — the exact dual-beat hazard the override
        exists to close.
        """
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.beat]
image = "web"
minimum_healthy_percent = 0
maximum_percent = 100

[services.web]
image = "web"
""",
            encoding="utf-8",
        )
        raw = parse_deploy_config(tmp_path / "deploy.toml").get_raw_dict()

        assert raw["services"]["beat"]["minimum_healthy_percent"] == 0
        assert raw["services"]["beat"]["maximum_percent"] == 100
        # Unset overrides stay absent, so service.py inherits the environment.
        assert "minimum_healthy_percent" not in raw["services"]["web"]
        assert "maximum_percent" not in raw["services"]["web"]

    def test_zero_minimum_healthy_percent_survives_the_raw_dict(self, tmp_path):
        """0 is falsy; the raw-dict emission must test `is not None`, not truth."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.beat]
minimum_healthy_percent = 0
""",
            encoding="utf-8",
        )
        raw = parse_deploy_config(tmp_path / "deploy.toml").get_raw_dict()

        assert raw["services"]["beat"]["minimum_healthy_percent"] == 0

    @pytest.mark.parametrize(
        ("toml_line", "match"),
        [
            ("minimum_healthy_percent = -1", "between 0 and 100"),
            ("minimum_healthy_percent = 101", "between 0 and 100"),
            ("maximum_percent = 99", "at least 100"),
            (
                "minimum_healthy_percent = 100\nmaximum_percent = 100",
                "cannot both be 100",
            ),
        ],
    )
    def test_invalid_deployment_override_fails_at_parse_time(self, tmp_path, toml_line, match):
        """Fail fast: ECS rejects these values only mid-deploy, and
        _update_service would swallow that ClientError into a vague
        per-service failure."""
        (tmp_path / "deploy.toml").write_text(
            f"""
[application]
name = "test"

[services.beat]
{toml_line}
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=match):
            parse_deploy_config(tmp_path / "deploy.toml")

    def test_100_100_is_only_rejected_when_both_are_set_by_the_service(self, tmp_path):
        """A service pinning just one side to 100 is legal — the other side
        comes from the environment, which parse time cannot see."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[services.web]
minimum_healthy_percent = 100

[services.worker]
maximum_percent = 100
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert config.services["web"].minimum_healthy_percent == 100
        assert config.services["worker"].maximum_percent == 100

    def test_additional_contexts_in_raw_dict(self, tmp_path):
        """Test additional_contexts roundtrips through get_raw_dict.

        The production deploy path builds images from get_raw_dict()'s output,
        so a field dropped here silently never reaches `docker build`.
        """
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[images.web]
context = "web"
additional_contexts = { shared = "shared" }
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        raw = config.get_raw_dict()

        assert raw["images"]["web"]["additional_contexts"] == {"shared": "shared"}


def _write_health_check(tmp_path, body: str):
    (tmp_path / "deploy.toml").write_text(
        f"""
[application]
name = "test"

[services.worker]
image = "worker"

[services.worker.container_health_check]
{body}
""",
        encoding="utf-8",
    )
    return tmp_path / "deploy.toml"


class TestContainerHealthCheck:
    """``[services.X.container_health_check]`` -- ECS's container-level healthCheck.

    For a service with no port the ALB health check cannot see, a wedged
    worker otherwise looks healthy to ECS (claude-meta Phase 52).
    """

    FULL = (
        'command = ["CMD-SHELL", "test -f /tmp/alive"]\n'
        "interval = 30\ntimeout = 5\nretries = 3\nstart_period = 60"
    )

    def test_a_full_block_parses_and_round_trips_through_the_raw_dict(self, tmp_path):
        config = parse_deploy_config(_write_health_check(tmp_path, self.FULL))
        block = config.get_raw_dict()["services"]["worker"]["container_health_check"]
        assert block == {
            "command": ["CMD-SHELL", "test -f /tmp/alive"],
            "interval": 30,
            "timeout": 5,
            "retries": 3,
            "start_period": 60,
        }

    def test_rendered_in_ecs_shape(self):
        assert container_health_check(
            "worker",
            {
                "command": ["CMD", "/app/healthcheck", "--max-age", "120"],
                "interval": 30,
                "timeout": 5,
                "retries": 3,
                "start_period": 60,
            },
        ) == {
            "command": ["CMD", "/app/healthcheck", "--max-age", "120"],
            "interval": 30,
            "timeout": 5,
            "retries": 3,
            "startPeriod": 60,
        }

    def test_only_command_is_required(self):
        assert container_health_check("worker", {"command": ["CMD-SHELL", "true"]}) == {
            "command": ["CMD-SHELL", "true"]
        }

    def test_absent_block_is_absent_from_the_raw_dict(self, tmp_path):
        (tmp_path / "deploy.toml").write_text(
            '[application]\nname = "test"\n\n[services.web]\nimage = "web"\n', encoding="utf-8"
        )
        raw = parse_deploy_config(tmp_path / "deploy.toml").get_raw_dict()
        assert "container_health_check" not in raw["services"]["web"]

    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ("interval = 30", "requires 'command'"),
            ('command = "test -f /tmp/alive"', "list"),
            ('command = ["test -f /tmp/alive"]', "CMD or CMD-SHELL"),
            ('command = ["CMD"]', "at least one argument"),
            ('command = ["CMD-SHELL", "a", "b"]', "exactly one shell string"),
            ('command = ["CMD-SHELL", ""]', "non-empty strings"),
            ('command = ["CMD", 1]', "non-empty strings"),
            ('command = ["CMD", "true"]\nintervall = 30', "unknown key.*intervall"),
            ('command = ["CMD", "true"]\nstartPeriod = 30', "unknown key.*startPeriod"),
            ('command = ["CMD", "true"]\ninterval = 4', r"interval.*between 5 and 300"),
            ('command = ["CMD", "true"]\ninterval = 301', r"interval.*between 5 and 300"),
            ('command = ["CMD", "true"]\ntimeout = 1', r"timeout.*between 2 and 60"),
            ('command = ["CMD", "true"]\ntimeout = 61', r"timeout.*between 2 and 60"),
            ('command = ["CMD", "true"]\nretries = 0', r"retries.*between 1 and 10"),
            ('command = ["CMD", "true"]\nretries = 11', r"retries.*between 1 and 10"),
            ('command = ["CMD", "true"]\nstart_period = -1', r"start_period.*between 0 and 300"),
            ('command = ["CMD", "true"]\nstart_period = 301', r"start_period.*between 0 and 300"),
            ('command = ["CMD", "true"]\ninterval = 30.5', r"interval.*whole number"),
            ('command = ["CMD", "true"]\nretries = true', r"retries.*whole number"),
        ],
    )
    def test_an_invalid_block_fails_at_parse_time(self, tmp_path, body, match):
        with pytest.raises(ValueError, match=rf"services\.worker\.container_health_check.*{match}"):
            parse_deploy_config(_write_health_check(tmp_path, body))

    def test_the_range_edges_are_accepted(self, tmp_path):
        body = (
            'command = ["CMD", "true"]\ninterval = 5\ntimeout = 60\nretries = 10\n'
            "start_period = 0"
        )
        parse_deploy_config(_write_health_check(tmp_path, body))


class TestDeployConfigImages:
    """Tests for DeployConfig.images (replaces get_deploy_images)."""

    def test_extract_images(self, tmp_path):
        """Test extracting images from deploy config."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[images.web]
context = "."
dockerfile = "Dockerfile.web"

[images.base]
context = "./base"
push = false
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert "web" in config.images
        assert config.images["web"].context == "."
        assert config.images["web"].dockerfile == "Dockerfile.web"
        assert config.images["web"].push is True

        assert config.images["base"].push is False

    def test_default_dockerfile(self, tmp_path):
        """Test default Dockerfile value."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[images.app]
context = "."
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        assert config.images["app"].dockerfile == "Dockerfile"


class TestDeployConfigEnvVars:
    """Tests for DeployConfig.get_all_env_var_names (replaces get_deploy_env_vars)."""

    def test_extract_env_vars(self, tmp_path):
        """Test extracting environment variables."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[environment]
DEBUG = "false"

[environment.staging]
DEBUG = "true"

[secrets]
names = ["API_KEY"]
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "DEBUG" in result
        assert "API_KEY" in result

    def test_module_injected_database_vars(self, tmp_path):
        """Test that database module vars are included."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[database]
type = "postgresql"
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "DB_HOST" in result
        assert "DB_PORT" in result
        assert "DB_NAME" in result
        assert "DB_USERNAME" in result
        assert "DB_PASSWORD" in result

    def test_module_injected_cache_vars(self, tmp_path):
        """Test that cache module vars are included."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[cache]
type = "redis"
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "REDIS_URL" in result

    def test_module_injected_storage_vars(self, tmp_path):
        """Test that storage module vars are included."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[storage]
type = "s3"
buckets = ["media", "originals"]
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "S3_MEDIA_BUCKET" in result
        assert "S3_ORIGINALS_BUCKET" in result
        # UPDATED (53h-2b): the two _REGION variables used to be asserted here.
        # StorageModule has never injected them, so claiming them made the
        # audit report a variable as satisfied by nothing.
        assert "S3_MEDIA_BUCKET_REGION" not in result
        assert "S3_ORIGINALS_BUCKET_REGION" not in result

    def test_module_injected_secrets_vars(self, tmp_path):
        """Test that secrets module vars are included."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[secrets]
names = ["SECRET_KEY", "API_TOKEN"]
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "SECRET_KEY" in result
        assert "API_TOKEN" in result

    def test_all_modules_combined(self, tmp_path):
        """Test that all modules work together."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[environment]
CUSTOM_VAR = "value"

[database]
type = "postgresql"

[cache]
type = "redis"

[storage]
type = "s3"
buckets = ["media"]

[secrets]
names = ["SECRET_KEY"]
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        # Explicit env var
        assert "CUSTOM_VAR" in result
        # Database
        assert "DB_HOST" in result
        # Cache
        assert "REDIS_URL" in result
        # Storage
        assert "S3_MEDIA_BUCKET" in result
        # Secrets
        assert "SECRET_KEY" in result


class TestDeployConfigAudit:
    """Tests for DeployConfig.audit (replaces get_audit_config)."""

    def test_extract_audit_config(self, tmp_path):
        """Test extracting audit configuration."""
        (tmp_path / "deploy.toml").write_text(
            """
[application]
name = "test"

[audit]
ignore_services = ["db"]
service_mapping = { app = "web" }
ignore_env_vars = ["DEBUG"]
""",
            encoding="utf-8",
        )
        config = parse_deploy_config(tmp_path / "deploy.toml")
        audit = config.audit

        assert "db" in audit.ignore_services
        assert audit.service_mapping["app"] == "web"
        assert "DEBUG" in audit.ignore_env_vars

    def test_empty_audit_config(self, tmp_path):
        """Test with no audit config."""
        (tmp_path / "deploy.toml").write_text('[application]\nname = "test"', encoding="utf-8")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        audit = config.audit
        assert audit.ignore_services == set()
        assert audit.service_mapping == {}


class TestParseDockerCompose:
    """Tests for parse_docker_compose function."""

    def test_parse_valid_file(self, sample_docker_compose):
        """Test parsing a valid docker-compose.yml."""
        result = parse_docker_compose(sample_docker_compose)

        assert "services" in result
        assert "testapp" in result["services"]

    def test_parse_nonexistent_file(self, tmp_path):
        """Test parsing a non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            parse_docker_compose(tmp_path / "nonexistent.yml")


class TestGetComposeServices:
    """Tests for get_compose_services function."""

    def test_extract_services_with_build(self):
        """Test extracting services with build contexts."""
        compose = {
            "services": {
                "web": {
                    "build": {"context": "./app", "dockerfile": "Dockerfile.web"},
                    "environment": ["PORT=8000"],
                },
                "db": {"image": "postgres:15"},
            }
        }

        result = get_compose_services(compose)

        assert result["web"]["has_build"] is True
        assert result["web"]["build_context"] == "./app"
        assert result["web"]["dockerfile"] == "Dockerfile.web"
        assert "PORT" in result["web"]["environment"]

        assert result["db"]["has_build"] is False

    def test_build_string_shorthand(self):
        """Test build context as string."""
        compose = {"services": {"app": {"build": "./src"}}}
        result = get_compose_services(compose)
        assert result["app"]["build_context"] == "./src"

    def test_env_as_dict(self):
        """Test environment as dictionary."""
        compose = {
            "services": {"app": {"build": ".", "environment": {"PORT": "8000", "DEBUG": "true"}}}
        }
        result = get_compose_services(compose)
        assert "PORT" in result["app"]["environment"]
        assert "DEBUG" in result["app"]["environment"]

    def test_profiles(self):
        """Test profiles extraction."""
        compose = {"services": {"tool": {"build": ".", "profiles": ["dev"]}}}
        result = get_compose_services(compose)
        assert result["tool"]["profiles"] == ["dev"]

    def test_env_file_vars_merged(self, tmp_path):
        """Vars from env_file must be seen by the audit, or removing a
        variable from a shared env file is never flagged."""
        (tmp_path / "django.env").write_text(
            "# comment\n\nDB_HOST=postgres\nexport LOG_LEVEL=INFO\nEMPTY=\n",
            encoding="utf-8",
        )
        compose = {
            "services": {
                "app": {
                    "build": ".",
                    "env_file": "django.env",
                    "environment": ["SECRET_KEY=${SECRET_KEY}"],
                }
            }
        }

        result = get_compose_services(compose, base_dir=tmp_path)

        assert "DB_HOST" in result["app"]["environment"]
        assert "LOG_LEVEL" in result["app"]["environment"]
        assert "EMPTY" in result["app"]["environment"]
        assert "SECRET_KEY" in result["app"]["environment"]

    def test_env_file_list_and_mapping_forms(self, tmp_path):
        """env_file accepts a list of strings or {path: ...} mappings."""
        (tmp_path / "a.env").write_text("FROM_A=1\n", encoding="utf-8")
        (tmp_path / "b.env").write_text("FROM_B=2\n", encoding="utf-8")
        compose = {
            "services": {
                "app": {
                    "build": ".",
                    "env_file": ["a.env", {"path": "b.env", "required": False}],
                }
            }
        }

        result = get_compose_services(compose, base_dir=tmp_path)

        assert "FROM_A" in result["app"]["environment"]
        assert "FROM_B" in result["app"]["environment"]

    def test_env_file_missing_optional_ignored(self, tmp_path):
        """A missing env file marked required: false is skipped; without
        base_dir env_file entries are ignored entirely."""
        compose = {
            "services": {
                "app": {
                    "build": ".",
                    "env_file": [{"path": "absent.env", "required": False}],
                }
            }
        }

        result = get_compose_services(compose, base_dir=tmp_path)
        assert result["app"]["environment"] == []

        # No base_dir: env_file silently skipped (backward compatible)
        result = get_compose_services(compose)
        assert result["app"]["environment"] == []


class TestImageConfig:
    """Tests for ImageConfig dataclass."""

    def test_get_target_string(self):
        """Test get_target with string value."""
        img = ImageConfig(name="web", context=".", target="production")
        assert img.get_target("staging") == "production"
        assert img.get_target("production") == "production"

    def test_get_target_dict(self):
        """Test get_target with environment-specific dict."""
        img = ImageConfig(
            name="web",
            context=".",
            target={"staging": "development", "production": "production"},
        )
        assert img.get_target("staging") == "development"
        assert img.get_target("production") == "production"

    def test_get_target_none(self):
        """Test get_target when not specified."""
        img = ImageConfig(name="web", context=".")
        assert img.get_target("staging") is None

    def test_get_build_args_base(self):
        """Test get_build_args with base args only."""
        img = ImageConfig(
            name="web", context=".", build_args={"PYTHON_VERSION": "3.12", "DEBUG": "0"}
        )
        args = img.get_build_args("staging")
        assert args == {"PYTHON_VERSION": "3.12", "DEBUG": "0"}

    def test_get_build_args_with_env_override(self):
        """Test get_build_args with environment-specific overrides."""
        img = ImageConfig(
            name="web",
            context=".",
            build_args={
                "PYTHON_VERSION": "3.12",
                "staging": {"DEBUG": "1"},
                "production": {"DEBUG": "0"},
            },
        )
        staging_args = img.get_build_args("staging")
        assert staging_args == {"PYTHON_VERSION": "3.12", "DEBUG": "1"}

        prod_args = img.get_build_args("production")
        assert prod_args == {"PYTHON_VERSION": "3.12", "DEBUG": "0"}
