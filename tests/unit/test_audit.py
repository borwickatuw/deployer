"""Tests for audit functionality (deployer.core.audit and deployer.config)."""

from pathlib import Path

import pytest

from deployer.config import (
    AuditConfig,
    DeployConfig,
    ImageConfig,
    get_compose_services,
    parse_deploy_config,
    parse_deploy_toml,
    parse_docker_compose,
)
from deployer.core.audit import (
    audit_env_vars,
    audit_images,
    audit_services,
    run_audit,
)
from deployer.utils import Colors


class TestParseDockerCompose:
    """Tests for parse_docker_compose function."""

    def test_parse_docker_compose(self, sample_docker_compose):
        """Test parsing a valid docker-compose.yml file."""
        result = parse_docker_compose(sample_docker_compose)

        assert "services" in result
        assert "testapp" in result["services"]
        assert "celery-worker" in result["services"]
        assert "postgres" in result["services"]

    def test_parse_docker_compose_file_not_found(self, tmp_path):
        """Test parsing a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_docker_compose(tmp_path / "nonexistent.yml")


class TestParseDeployToml:
    """Tests for parse_deploy_toml function."""

    def test_parse_deploy_toml(self, sample_deploy_toml):
        """Test parsing a valid deploy.toml file."""
        result = parse_deploy_toml(sample_deploy_toml)

        assert "application" in result
        assert result["application"]["name"] == "testapp"
        assert "services" in result
        assert "web" in result["services"]

    def test_parse_deploy_toml_file_not_found(self, tmp_path):
        """Test parsing a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_deploy_toml(tmp_path / "nonexistent.toml")


class TestParseDeployConfig:
    """Tests for parse_deploy_config function (dataclass approach)."""

    def test_parse_deploy_config(self, sample_deploy_toml):
        """Test parsing into DeployConfig dataclass."""
        config = parse_deploy_config(sample_deploy_toml)

        assert isinstance(config, DeployConfig)
        assert config.application.name == "testapp"
        assert "web" in config.services
        assert config.services["web"].name == "web"

    def test_parse_deploy_config_warnings_for_unknown_keys(self, tmp_path):
        """Test that unknown keys generate warnings."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"
unknown_key = "value"

[images.web]
context = "."
bad_option = true

[unknown_section]
foo = "bar"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")

        warnings = config.get_warnings()
        assert any("unknown_key" in w.lower() for w in warnings)
        assert any("bad_option" in w.lower() for w in warnings)
        assert any("unknown_section" in w.lower() for w in warnings)


class TestGetComposeServices:
    """Tests for get_compose_services function."""

    def test_extract_services_with_build(self):
        """Test extracting services that have build contexts."""
        compose = {
            "services": {
                "web": {
                    "build": {"context": ".", "dockerfile": "Dockerfile"},
                    "ports": ["8000:8000"],
                    "environment": ["DEBUG=true", "API_KEY=secret"],
                },
                "db": {
                    "image": "postgres:15",
                },
            }
        }

        result = get_compose_services(compose)

        assert "web" in result
        assert result["web"]["has_build"] is True
        assert result["web"]["build_context"] == "."
        assert result["web"]["dockerfile"] == "Dockerfile"
        assert "DEBUG" in result["web"]["environment"]
        assert "API_KEY" in result["web"]["environment"]

        assert "db" in result
        assert result["db"]["has_build"] is False

    def test_extract_services_with_profiles(self):
        """Test that profiles are extracted correctly."""
        compose = {
            "services": {
                "dev-tool": {
                    "build": ".",
                    "profiles": ["dev-tools"],
                },
            }
        }

        result = get_compose_services(compose)
        assert result["dev-tool"]["profiles"] == ["dev-tools"]

    def test_build_string_shorthand(self):
        """Test build context specified as string."""
        compose = {
            "services": {
                "app": {"build": "./app"},
            }
        }

        result = get_compose_services(compose)
        assert result["app"]["build_context"] == "./app"

    def test_environment_as_dict(self):
        """Test environment variables specified as dict."""
        compose = {
            "services": {
                "app": {
                    "build": ".",
                    "environment": {"DEBUG": "true", "PORT": "8000"},
                },
            }
        }

        result = get_compose_services(compose)
        assert "DEBUG" in result["app"]["environment"]
        assert "PORT" in result["app"]["environment"]


class TestDeployConfigServices:
    """Tests for DeployConfig.services (replaces get_deploy_services)."""

    def test_extract_services(self, tmp_path):
        """Test extracting services from deploy.toml structure."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[services.web]
image = "web"
port = 8000
command = ["gunicorn"]

[services.worker]
image = "worker"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert "web" in config.services
        assert config.services["web"].image == "web"
        assert config.services["web"].port == 8000
        assert "worker" in config.services

    def test_empty_services(self, tmp_path):
        """Test with no services defined."""
        (tmp_path / "deploy.toml").write_text('[application]\nname = "test"')
        config = parse_deploy_config(tmp_path / "deploy.toml")
        assert config.services == {}


class TestDeployConfigImages:
    """Tests for DeployConfig.images (replaces get_deploy_images)."""

    def test_extract_images(self, tmp_path):
        """Test extracting images from deploy.toml structure."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[images.web]
context = "."
dockerfile = "Dockerfile"

[images.worker]
context = "."
dockerfile = "Dockerfile.worker"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")

        assert "web" in config.images
        assert config.images["web"].context == "."
        assert config.images["web"].dockerfile == "Dockerfile"
        assert config.images["worker"].dockerfile == "Dockerfile.worker"

    def test_default_dockerfile(self, tmp_path):
        """Test that default dockerfile is 'Dockerfile'."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[images.app]
context = "."
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        assert config.images["app"].dockerfile == "Dockerfile"


class TestDeployConfigEnvVars:
    """Tests for DeployConfig.get_all_env_var_names (replaces get_deploy_env_vars)."""

    def test_extract_env_vars(self, tmp_path):
        """Test extracting environment variables."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[environment]
DEBUG = "false"
API_URL = "https://api.example.com"

[secrets]
SECRET_KEY = "ssm:/app/secret"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "DEBUG" in result
        assert "API_URL" in result
        assert "SECRET_KEY" in result

    def test_extract_service_specific_env_vars(self, tmp_path):
        """Test extracting environment variables from service-specific sections."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[environment]
GLOBAL_VAR = "value"

[services.web]
image = "web"
port = 8000

[services.api]
image = "api"

[services.api.environment]
ENABLE_CACHE = "true"
DJANGO_URL = "${services.web.url}"
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        result = config.get_all_env_var_names()

        assert "GLOBAL_VAR" in result
        assert "ENABLE_CACHE" in result
        assert "DJANGO_URL" in result


class TestDeployConfigAudit:
    """Tests for DeployConfig.audit (replaces get_audit_config)."""

    def test_extract_audit_config(self, tmp_path):
        """Test extracting audit configuration."""
        (tmp_path / "deploy.toml").write_text("""
[application]
name = "test"

[audit]
ignore_services = ["postgres", "redis"]
service_mapping = { app = "web" }
ignore_env_vars = ["DEBUG"]
ignore_images = ["base"]
""")
        config = parse_deploy_config(tmp_path / "deploy.toml")
        audit = config.audit

        assert audit.ignore_services == {"postgres", "redis"}
        assert audit.service_mapping == {"app": "web"}
        assert audit.ignore_env_vars == {"DEBUG"}
        assert audit.ignore_images == {"base"}

    def test_empty_audit_config(self, tmp_path):
        """Test with no audit config defined."""
        (tmp_path / "deploy.toml").write_text('[application]\nname = "test"')
        config = parse_deploy_config(tmp_path / "deploy.toml")
        audit = config.audit

        assert audit.ignore_services == set()
        assert audit.service_mapping == {}
        assert audit.ignore_env_vars == set()


class TestAuditServices:
    """Tests for audit_services function."""

    def test_all_services_accounted_for(self):
        """Test when all compose services have matching deploy.toml entries."""
        compose_services = {
            "web": {"has_build": True, "profiles": []},
            "worker": {"has_build": True, "profiles": []},
            "postgres": {"has_build": False, "profiles": []},
        }
        deploy_services = {
            "web": {},
            "worker": {},
        }
        audit_config = AuditConfig(
            ignore_services=set(),
            service_mapping={},
        )

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert issues == []

    def test_missing_service_in_deploy_toml(self):
        """Test detection of service missing from deploy.toml."""
        compose_services = {
            "web": {"has_build": True, "profiles": []},
            "celery": {"has_build": True, "profiles": []},
        }
        deploy_services = {
            "web": {},
        }
        audit_config = AuditConfig(
            ignore_services=set(),
            service_mapping={},
        )

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert len(issues) == 1
        assert "celery" in issues[0]

    def test_service_mapping(self):
        """Test that service mapping is respected."""
        compose_services = {
            "app": {"has_build": True, "profiles": []},
        }
        deploy_services = {
            "web": {},
        }
        audit_config = AuditConfig(
            ignore_services=set(),
            service_mapping={"app": "web"},
        )

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert issues == []

    def test_ignored_services(self):
        """Test that ignored services are skipped."""
        compose_services = {
            "db-seeder": {"has_build": True, "profiles": []},
        }
        deploy_services = {}
        audit_config = AuditConfig(
            ignore_services={"db-seeder"},
            service_mapping={},
        )

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert issues == []

    def test_services_with_profiles_skipped(self):
        """Test that services with profiles are skipped."""
        compose_services = {
            "dev-tool": {"has_build": True, "profiles": ["dev-tools"]},
        }
        deploy_services = {}
        audit_config = AuditConfig(
            ignore_services=set(),
            service_mapping={},
        )

        issues = audit_services(compose_services, deploy_services, audit_config)
        assert issues == []


class TestAuditImages:
    """Tests for audit_images function."""

    def test_all_images_accounted_for(self):
        """Test when all build contexts have matching images."""
        # The audit function normalizes "./something" to "something"
        # and "." becomes empty string, so we use a subdir context
        compose_services = {
            "web": {"has_build": True, "build_context": "./web", "profiles": []},
        }
        deploy_images = {
            "web": ImageConfig(name="web", context="web"),
        }
        audit_config = AuditConfig(
            ignore_services=set(),
            ignore_images=set(),
        )

        issues = audit_images(compose_services, deploy_images, audit_config)
        assert issues == []

    def test_missing_image_context(self):
        """Test detection of missing build context in deploy.toml."""
        compose_services = {
            "web": {"has_build": True, "build_context": "./web", "profiles": []},
        }
        deploy_images = {
            "api": ImageConfig(name="api", context="./api"),
        }
        audit_config = AuditConfig(
            ignore_services=set(),
            ignore_images=set(),
        )

        issues = audit_images(compose_services, deploy_images, audit_config)
        assert len(issues) == 1
        assert "web" in issues[0]

    def test_build_service_without_a_context_is_skipped(self):
        """A build service reporting no context contributes no expectation."""
        issues = audit_images(
            {"web": {"has_build": True, "build_context": "", "profiles": []}},
            {},
            AuditConfig(ignore_services=set(), ignore_images=set()),
        )

        assert issues == []


class TestAuditEnvVars:
    """Tests for audit_env_vars function."""

    def test_env_vars_of_non_build_services_are_ignored(self):
        """Only services with a build contribute env vars to the audit."""
        issues = audit_env_vars(
            {
                "vendor": {
                    "has_build": False,
                    "environment": ["VENDOR_ONLY"],
                    "profiles": [],
                }
            },
            set(),
            AuditConfig(ignore_env_vars=set(), ignore_services=set()),
        )

        assert issues == []

    def test_all_env_vars_accounted_for(self):
        """Test when all env vars are in deploy.toml."""
        compose_services = {
            "web": {
                "has_build": True,
                "environment": ["DATABASE_URL", "REDIS_URL"],
                "profiles": [],
            },
        }
        deploy_env_vars = {"DATABASE_URL", "REDIS_URL"}
        audit_config = AuditConfig(
            ignore_env_vars=set(),
            ignore_services=set(),
        )

        issues = audit_env_vars(compose_services, deploy_env_vars, audit_config)
        assert issues == []

    def test_missing_env_var(self):
        """Test detection of missing environment variable."""
        compose_services = {
            "web": {
                "has_build": True,
                "environment": ["DATABASE_URL", "CUSTOM_VAR"],
                "profiles": [],
            },
        }
        deploy_env_vars = {"DATABASE_URL"}
        audit_config = AuditConfig(
            ignore_env_vars=set(),
            ignore_services=set(),
        )

        issues = audit_env_vars(compose_services, deploy_env_vars, audit_config)
        assert len(issues) == 1
        assert "CUSTOM_VAR" in issues[0]

    def test_ignored_env_vars(self):
        """Test that ignored env vars are not flagged."""
        compose_services = {
            "web": {
                "has_build": True,
                "environment": ["DEBUG", "PYTHONUNBUFFERED"],
                "profiles": [],
            },
        }
        deploy_env_vars = set()
        audit_config = AuditConfig(
            ignore_env_vars=set(),  # DEBUG and PYTHONUNBUFFERED are default ignores
            ignore_services=set(),
        )

        issues = audit_env_vars(compose_services, deploy_env_vars, audit_config)
        # DEBUG and PYTHONUNBUFFERED should be in default_ignore
        assert issues == []

    def test_aws_credential_vars_auto_ignored(self):
        """Test that AWS credential/endpoint vars are auto-ignored.

        On ECS Fargate, AWS credentials come from the task role, not env vars.
        These only appear in docker-compose for local S3-compatible services.
        """
        compose_services = {
            "web": {
                "has_build": True,
                "environment": [
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                    "AWS_S3_ENDPOINT_URL",
                ],
                "profiles": [],
            },
        }
        deploy_env_vars = set()
        audit_config = AuditConfig(
            ignore_env_vars=set(),
            ignore_services=set(),
        )

        issues = audit_env_vars(compose_services, deploy_env_vars, audit_config)
        assert issues == []


class TestRunAudit:
    """Tests for run_audit function."""

    def test_run_audit_no_issues(self, temp_project_dir):
        """Test run_audit returns exactly 0 issues for the sample fixtures.

        This previously asserted `issue_count >= 0`, which is true of every
        return value the function can produce except the -1 not-found case, so
        it pinned nothing. The sample fixtures are built to match; pin that.
        """
        issue_count, issues = run_audit(temp_project_dir, verbose=False)

        assert (issue_count, issues) == (0, [])

    def test_run_audit_missing_compose_file(self, tmp_path):
        """Test run_audit handles missing docker-compose.yml."""
        # Create only deploy.toml
        (tmp_path / "deploy.toml").write_text('[application]\nname = "test"')

        issue_count, issues = run_audit(tmp_path, verbose=False)

        assert issue_count == -1
        assert "not found" in issues[0].lower()

    def test_run_audit_missing_deploy_toml(self, tmp_path):
        """Test run_audit handles missing deploy.toml."""
        # Create only docker-compose.yml
        (tmp_path / "docker-compose.yml").write_text("services: {}")

        issue_count, issues = run_audit(tmp_path, verbose=False)

        assert issue_count == -1
        assert "not found" in issues[0].lower()


# =============================================================================
# run_audit terminal output
# =============================================================================


def _lines(capsys):
    """Return stdout's non-blank lines.

    Blank lines are dropped on purpose — see TestRunAuditOutput's docstring.
    """
    return [line for line in capsys.readouterr().out.splitlines() if line.strip()]


def _section(msg: str) -> str:
    """Return the line log_section() prints for `msg`."""
    return f"{Colors.BLUE}=== {msg} ==={Colors.NC}"


def _ok(msg: str) -> str:
    """Return the line log_ok() prints for `msg`."""
    return f"  {Colors.GREEN}✓{Colors.NC} {msg}"


def _info(msg: str) -> str:
    """Return the line log_info() prints for `msg`."""
    return f"  {Colors.CYAN}ℹ{Colors.NC} {msg}"


def _warn(msg: str) -> str:
    """Return the line log_warning() prints for `msg`."""
    return f"  {Colors.YELLOW}⚠{Colors.NC} {msg}"


def _project(tmp_path: Path, compose: str, deploy: str) -> Path:
    """Write a docker-compose.yml and deploy.toml into a fresh directory."""
    (tmp_path / "docker-compose.yml").write_text(compose)
    (tmp_path / "deploy.toml").write_text(deploy)
    return tmp_path


MINIMAL_COMPOSE = "services: {}\n"
MINIMAL_DEPLOY = '[application]\nname = "t"\n'

MISMATCHED_COMPOSE = """\
services:
  web:
    build:
      context: ./web
    environment:
      - MYSTERY_VAR=1
"""
MISMATCHED_DEPLOY = """\
[application]
name = "t"

[services]
ghost = { image = "g" }
"""


class TestRunAuditOutput:
    """Characterization tests for run_audit()'s verbose terminal output.

    Every pre-existing run_audit test passes verbose=False, so the entire
    reporting half of the function — the header, the audit-config section, the
    three check sections and the summary — was unexercised. These pin it so a
    decomposition can be shown to preserve it.

    Blank-line placement is deliberately not pinned: _lines() drops blank lines
    the way tests/unit/test_init_cli.py's helper of the same name does. Section
    text, order and the issue lines under each heading are pinned and must not
    change.
    """

    def test_header_names_the_directory_and_both_files(self, tmp_path, capsys):
        """The header echoes the resolved project name and both filenames."""
        project = _project(tmp_path, MINIMAL_COMPOSE, MINIMAL_DEPLOY)

        run_audit(project, verbose=True)

        assert _lines(capsys)[:3] == [
            f"Auditing {Colors.CYAN}{project.name}{Colors.NC}",
            "  docker-compose: docker-compose.yml",
            "  deploy.toml: deploy.toml",
        ]

    def test_custom_filenames_are_echoed(self, tmp_path, capsys):
        """Non-default filenames appear in the header and are the files read."""
        (tmp_path / "compose.prod.yml").write_text(MINIMAL_COMPOSE)
        (tmp_path / "deploy.prod.toml").write_text(MINIMAL_DEPLOY)

        run_audit(
            tmp_path,
            compose_filename="compose.prod.yml",
            deploy_filename="deploy.prod.toml",
            verbose=True,
        )

        assert _lines(capsys)[1:3] == [
            "  docker-compose: compose.prod.yml",
            "  deploy.toml: deploy.prod.toml",
        ]

    def test_no_audit_config_section_when_nothing_configured(self, tmp_path, capsys):
        """A deploy.toml with no [audit] section prints no configuration block."""
        run_audit(_project(tmp_path, MINIMAL_COMPOSE, MINIMAL_DEPLOY), verbose=True)

        assert _section("Audit Configuration") not in _lines(capsys)

    def test_audit_config_section_lists_every_configured_key(self, tmp_path, capsys):
        """Ignored services, mappings and ignored env vars each get a line."""
        deploy = """\
[application]
name = "t"

[audit]
ignore_services = ["seeder"]
service_mapping = { "app" = "web" }
ignore_env_vars = ["NOISY"]
"""
        run_audit(_project(tmp_path, MINIMAL_COMPOSE, deploy), verbose=True)

        out = _lines(capsys)
        start = out.index(_section("Audit Configuration"))
        assert out[start : start + 4] == [
            _section("Audit Configuration"),
            _info("Ignoring services: seeder"),
            _info("Service mappings: app→web"),
            _info("Ignoring env vars: NOISY"),
        ]

    def test_ignore_images_alone_still_opens_the_section(self, tmp_path, capsys):
        """ignore_images gates the Audit Configuration section.

        What it prints inside that section is asserted separately — see
        test_ignore_images_is_listed.
        """
        deploy = """\
[application]
name = "t"

[audit]
ignore_images = ["legacy"]
"""
        run_audit(_project(tmp_path, MINIMAL_COMPOSE, deploy), verbose=True)

        assert _section("Audit Configuration") in _lines(capsys)

    def test_ignore_images_is_listed(self, tmp_path, capsys):
        """ignore_images gets its own line, like the other three keys.

        Before this was fixed, ignore_images opened the section but printed
        nothing inside it, so a deploy.toml configuring only ignore_images
        showed an empty heading and no way to confirm the setting took.
        """
        deploy = """\
[application]
name = "t"

[audit]
ignore_images = ["legacy", "vendor"]
"""
        run_audit(_project(tmp_path, MINIMAL_COMPOSE, deploy), verbose=True)

        out = _lines(capsys)
        start = out.index(_section("Audit Configuration"))
        assert out[start : start + 2] == [
            _section("Audit Configuration"),
            _info("Ignoring images: legacy, vendor"),
        ]

    def test_every_audit_config_key_is_listed_together(self, tmp_path, capsys):
        """All four keys set at once print in declaration order."""
        deploy = """\
[application]
name = "t"

[audit]
ignore_services = ["seeder"]
service_mapping = { "app" = "web" }
ignore_env_vars = ["NOISY"]
ignore_images = ["legacy"]
"""
        run_audit(_project(tmp_path, MINIMAL_COMPOSE, deploy), verbose=True)

        out = _lines(capsys)
        start = out.index(_section("Audit Configuration"))
        assert out[start : start + 5] == [
            _section("Audit Configuration"),
            _info("Ignoring services: seeder"),
            _info("Service mappings: app→web"),
            _info("Ignoring env vars: NOISY"),
            _info("Ignoring images: legacy"),
        ]

    def test_all_clear_prints_an_ok_line_per_section(self, tmp_path, capsys):
        """Three headings, each followed by its own all-accounted-for line."""
        run_audit(_project(tmp_path, MINIMAL_COMPOSE, MINIMAL_DEPLOY), verbose=True)

        assert _lines(capsys)[3:] == [
            _section("Services"),
            _ok("All services accounted for"),
            _section("Images"),
            _ok("All build contexts accounted for"),
            _section("Environment Variables"),
            _ok("All environment variables accounted for"),
            _section("Summary"),
            f"  {Colors.GREEN}No issues found!{Colors.NC}",
        ]

    def test_issues_are_warned_under_their_own_heading(self, tmp_path, capsys):
        """Each check's issues print as warnings beneath that check's heading."""
        project = _project(tmp_path, MISMATCHED_COMPOSE, MISMATCHED_DEPLOY)

        run_audit(project, verbose=True)

        assert _lines(capsys)[3:] == [
            _section("Services"),
            _warn("Service 'web' in docker-compose not found in deploy.toml"),
            _warn("Service 'ghost' in deploy.toml not found in docker-compose"),
            _section("Images"),
            _warn("Build context 'web' (from service 'web') not found in deploy.toml [images]"),
            _section("Environment Variables"),
            _warn("Environment variable 'MYSTERY_VAR' in docker-compose not in deploy.toml"),
            _section("Summary"),
            f"  {Colors.YELLOW}4 issue(s) found{Colors.NC}",
            "  To acknowledge intentional differences, add an [audit] section",
            "  to deploy.toml. Run with --help or see script docstring for examples.",
        ]

    def test_return_value_matches_the_reported_issues(self, tmp_path, capsys):
        """The count and list returned are the same issues that were printed."""
        project = _project(tmp_path, MISMATCHED_COMPOSE, MISMATCHED_DEPLOY)

        count, issues = run_audit(project, verbose=True)

        assert count == len(issues) == 4
        assert issues == [
            "Service 'web' in docker-compose not found in deploy.toml",
            "Service 'ghost' in deploy.toml not found in docker-compose",
            "Build context 'web' (from service 'web') not found in deploy.toml [images]",
            "Environment variable 'MYSTERY_VAR' in docker-compose not in deploy.toml",
        ]

    def test_only_some_checks_failing_mixes_warnings_and_ok_lines(self, tmp_path, capsys):
        """A services-only mismatch leaves the other two checks reporting OK."""
        compose = """\
services:
  web:
    build:
      context: .
"""
        deploy = """\
[application]
name = "t"

[images]
web = { context = ".", dockerfile = "Dockerfile" }
"""
        run_audit(_project(tmp_path, compose, deploy), verbose=True)

        assert _lines(capsys)[3:] == [
            _section("Services"),
            _warn("Service 'web' in docker-compose not found in deploy.toml"),
            _section("Images"),
            _ok("All build contexts accounted for"),
            _section("Environment Variables"),
            _ok("All environment variables accounted for"),
            _section("Summary"),
            f"  {Colors.YELLOW}1 issue(s) found{Colors.NC}",
            "  To acknowledge intentional differences, add an [audit] section",
            "  to deploy.toml. Run with --help or see script docstring for examples.",
        ]

    def test_quiet_prints_nothing_at_all(self, tmp_path, capsys):
        """verbose=False suppresses every line, issues or not."""
        project = _project(tmp_path, MISMATCHED_COMPOSE, MISMATCHED_DEPLOY)

        count, _issues = run_audit(project, verbose=False)

        assert count == 4
        assert capsys.readouterr().out == ""

    def test_missing_file_reports_before_any_output(self, tmp_path, capsys):
        """The not-found guards run before the header, even when verbose."""
        (tmp_path / "deploy.toml").write_text(MINIMAL_DEPLOY)

        count, issues = run_audit(tmp_path, verbose=True)

        assert count == -1
        assert issues == [f"docker-compose file not found: {tmp_path / 'docker-compose.yml'}"]
        assert capsys.readouterr().out == ""

    def test_missing_deploy_toml_reports_before_any_output(self, tmp_path, capsys):
        """The deploy.toml guard likewise precedes the header."""
        (tmp_path / "docker-compose.yml").write_text(MINIMAL_COMPOSE)

        count, issues = run_audit(tmp_path, verbose=True)

        assert count == -1
        assert issues == [f"deploy.toml not found: {tmp_path / 'deploy.toml'}"]
        assert capsys.readouterr().out == ""


class TestAuditServicesMappingBranches:
    """The two audit_services() branches no other test reaches."""

    def test_mapped_name_is_named_in_the_issue(self):
        """A mapped service that is still missing reports both names."""
        issues = audit_services(
            {"app": {"has_build": True, "profiles": []}},
            {},
            AuditConfig(ignore_services=set(), service_mapping={"app": "web"}),
        )

        assert issues == [
            "Service 'app' in docker-compose not found in deploy.toml (checked as 'web')"
        ]

    def test_deploy_only_service_is_reported(self):
        """A deploy.toml service with no compose counterpart is an issue."""
        issues = audit_services(
            {},
            {"ghost": {}},
            AuditConfig(ignore_services=set(), service_mapping={}),
        )

        assert issues == ["Service 'ghost' in deploy.toml not found in docker-compose"]

    def test_reverse_mapping_excuses_a_deploy_only_service(self):
        """A mapped deploy.toml name is matched via its compose original."""
        issues = audit_services(
            {"app": {"has_build": True, "profiles": []}},
            {"web": {}},
            AuditConfig(ignore_services=set(), service_mapping={"app": "web"}),
        )

        assert issues == []
