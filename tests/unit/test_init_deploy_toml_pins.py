"""Characterization pins for ``init/deploy_toml.py``'s generator.

Phase 53h-2a wrote these before changing what ``[secrets]`` looks like.
``init/deploy_toml.py`` was **8% covered** -- the lowest-covered module 53h-2a
touches -- and ``_build_environment_config``, the function whose output shape
changes, had no test at all. These pins are what the change is measured
against.

**Driven through the outermost boundary available** (53d-2a's recorded rule):
``generate_deploy_toml(compose_path, app_name, compose_data)`` takes a parsed
compose dict and returns a plain config dict, and ``format_deploy_toml(config)``
turns that dict into the TOML text ``bin/init.py`` writes. Those two are the
whole public surface of the module -- ``bin/init.py`` calls exactly them and
nothing else. Every private helper (``is_likely_secret``, ``_var_to_ssm_name``,
``_normalize_service_name``, ``_build_environment_config``, the eight
``_format_*_section`` functions) is exercised from here, so the pins say nothing
about which private helper does what, and survive any code motion inside the
module.

Nothing is stubbed. ``generate_deploy_toml`` accepts ``compose_data`` directly,
so no YAML file is needed except in the two pins that are specifically about
reading from disk (the Dockerfile probe and the app-name default).

What is pinned:

* **Secret detection** -- which variable names ``generate_deploy_toml`` decides
  are secrets. Read through ``_detected_secret_names`` below, which understands
  *both* emitted shapes, so these pins are about detection only and are
  unaffected by 53h-2a's change to the shape.
* **The emitted shape** -- ``TestEmittedSecretsShape`` and
  ``TestFormatSecretsSection``, which are the pins 53h-2a deliberately moves.
  They are separated from the detection pins for exactly that reason: after
  53h-2a, the diff to this file is confined to those two classes.
* **Service and image mapping** -- ``_normalize_service_name``'s celery/worker
  rules and the app-name-becomes-``web`` rule, both reached only through
  ``generate_deploy_toml``.
* **Environment mapping** -- the ``${database_url}``/``${redis_url}``
  placeholder substitution, which is the *other* consumer of
  ``InfraConfig.legacy_placeholders()`` and which 53h-2a leaves alone. Pinned
  here so that "removing the explicit ``[secrets]`` form does not touch it" is
  a checked claim rather than an assertion in a plan.
* **Migrations, ports, health checks and the audit ignore list** -- everything
  else ``generate_deploy_toml`` derives, so a change to the secrets path that
  perturbed any of them would show up here.

Pinned, not endorsed, and called out on the test where it happens:

* An ``env_file``-provided variable never reaches the generator, because
  ``generate_deploy_toml`` calls ``get_compose_services`` without ``base_dir``.
* ``CELERY_BROKER_URL`` silently also writes ``REDIS_URL``.
* A service whose compose name is already ``web``-free but whose normalized
  name collides with another service's silently overwrites it.
"""

import pytest

from deployer.init.deploy_toml import format_deploy_toml, generate_deploy_toml

APP = "myapp"


def _generate(services: dict, app_name: str = APP) -> dict:
    """Run the generator over a compose ``services`` mapping."""
    return generate_deploy_toml(
        compose_path=None, app_name=app_name, compose_data={"services": services}
    )


def _detected_secret_names(config: dict) -> set[str]:
    """The variable names the generator classified as secrets.

    Deliberately shape-agnostic: it reads the ``names`` list if there is one
    and the top-level keys otherwise, so every detection pin below is written
    against *what* was detected and never against *how* it is written down.
    That is the difference 53h-2a changes.
    """
    secrets = config.get("secrets", {})
    return set(secrets.get("names", secrets))


def _svc(**overrides) -> dict:
    """A minimal buildable compose service."""
    return {"build": ".", **overrides}


# Every on-disk pin below has to spell `dockerfile:` out, because a compose
# file that omits it crashes the generator -- see
# ``TestDockerfileProbeCrashesWithoutAnExplicitDockerfile``.
_COMPOSE_WITH_DOCKERFILE = (
    "services:\n  web:\n    build:\n      context: .\n      dockerfile: Dockerfile\n"
)


# ------------------------------------------------------------------------------
# Secret detection -- shape-independent
# ------------------------------------------------------------------------------


class TestSecretDetection:
    """Which variable names the generator treats as secrets."""

    @pytest.mark.parametrize(
        "var_name",
        [
            "SECRET_KEY",
            "DJANGO_SECRET_KEY",
            "DATACITE_PASSWORD",
            "API_KEY",
            "APIKEY",
            "GITHUB_TOKEN",
            "AWS_CREDENTIAL_FILE",
            "BASIC_AUTH_USER",
            "PRIVATE_KEY_PATH",
        ],
    )
    def test_a_name_matching_a_secret_pattern_is_detected(self, var_name):
        config = _generate({"web": _svc(environment=[f"{var_name}=x"])})
        assert _detected_secret_names(config) == {var_name}

    @pytest.mark.parametrize(
        "var_name",
        [
            "DATABASE_URL",
            "REDIS_URL",
            "CELERY_BROKER_URL",
            "CACHE_URL",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
            "AWS_STORAGE_BUCKET_NAME",
            "ALLOWED_HOSTS",
            "DEBUG",
            "LOG_LEVEL",
        ],
    )
    def test_an_explicit_non_secret_wins_over_the_patterns(self, var_name):
        # AWS_STORAGE_BUCKET_NAME and AWS_DEFAULT_REGION would otherwise be
        # caught -- the allowlist is checked first.
        config = _generate({"web": _svc(environment=[f"{var_name}=x"])})
        assert _detected_secret_names(config) == set()

    @pytest.mark.parametrize(
        "var_name",
        ["SENTRY_DSN", "DJANGO_SETTINGS_MODULE", "PORT", "TZ", "STATIC_ROOT"],
    )
    def test_an_unremarkable_name_is_not_a_secret(self, var_name):
        config = _generate({"web": _svc(environment=[f"{var_name}=x"])})
        assert _detected_secret_names(config) == set()

    def test_the_match_is_a_substring_not_a_word(self):
        # Pinned, not endorsed: "KEY" matches inside MON-KEY, so MONKEY_COUNT
        # is declared a secret and `deployer init` writes an SSM path for it.
        config = _generate({"web": _svc(environment=["MONKEY_COUNT=3"])})
        assert _detected_secret_names(config) == {"MONKEY_COUNT"}

    def test_detection_is_case_insensitive_on_the_variable(self):
        config = _generate({"web": _svc(environment=["lowercase_secret=x"])})
        assert _detected_secret_names(config) == {"lowercase_secret"}

    def test_secrets_are_collected_across_every_service(self):
        config = _generate(
            {
                "web": _svc(environment=["SECRET_KEY=x"]),
                "worker": _svc(environment=["WORKER_TOKEN=y"]),
            }
        )
        assert _detected_secret_names(config) == {"SECRET_KEY", "WORKER_TOKEN"}

    def test_infrastructure_services_still_contribute_their_secrets(self):
        # Pinned, not endorsed: postgres is filtered out of [images] and
        # [services], but its environment was already folded into the set that
        # feeds secret detection, so POSTGRES_PASSWORD is declared as an app
        # secret the application never asked for.
        config = _generate(
            {
                "web": _svc(environment=["SECRET_KEY=x"]),
                "postgres": {"image": "postgres:16", "environment": ["POSTGRES_PASSWORD=y"]},
            }
        )
        assert _detected_secret_names(config) == {"SECRET_KEY", "POSTGRES_PASSWORD"}

    def test_no_secrets_means_no_secrets_section_at_all(self):
        config = _generate({"web": _svc(environment=["ALLOWED_HOSTS=*"])})
        assert "secrets" not in config

    def test_an_env_file_variable_never_reaches_detection(self, tmp_path):
        # Pinned, not endorsed: generate_deploy_toml calls
        # get_compose_services() without base_dir, so env_file entries are
        # skipped entirely and a secret declared only in .env is missed.
        (tmp_path / ".env").write_text("SECRET_KEY=x\n")
        (tmp_path / "docker-compose.yml").write_text(
            _COMPOSE_WITH_DOCKERFILE + "    env_file: .env\n"
        )
        config = generate_deploy_toml(
            compose_path=tmp_path / "docker-compose.yml", app_name=APP, compose_data=None
        )
        assert "secrets" not in config


# ------------------------------------------------------------------------------
# The emitted secrets shape -- what 53h-2a changes
# ------------------------------------------------------------------------------


class TestEmittedSecretsShape:
    """The concrete ``[secrets]`` value ``generate_deploy_toml`` writes today.

    This is the one thing 53h-2a deliberately moves; every other pin in this
    file is written to be unaffected by it. Keeping it in its own class makes
    the refactor's diff to this file exactly this class plus
    ``TestFormatSecretsSection``.

    Pinned, **emphatically** not endorsed. ``deployer init`` is the only
    producer of the explicit ``VAR = "ssm:/path"`` form, and the form it
    produces is the one that 53h-2a found silently drops every secret when the
    same deploy.toml also carries a module section.
    """

    def test_each_secret_is_an_ssm_path_keyed_by_its_variable_name(self):
        config = _generate({"web": _svc(environment=["SECRET_KEY=x", "API_TOKEN=y"])})
        assert config["secrets"] == {
            "API_TOKEN": "ssm:/myapp/${environment}/api-token",  # pragma: allowlist secret
            "SECRET_KEY": "ssm:/myapp/${environment}/secret-key",  # pragma: allowlist secret
        }

    def test_the_variable_name_is_lowercased_and_hyphenated_for_the_path(self):
        config = _generate({"web": _svc(environment=["DATACITE_PASSWORD=x"])})
        assert config["secrets"]["DATACITE_PASSWORD"].endswith("/datacite-password")

    def test_the_app_name_is_baked_into_the_application_s_own_file(self):
        # Pinned, not endorsed: this is the contradiction 53h-2a resolves. The
        # architecture's premise (modules/base.py) is that deploy.toml declares
        # *what* the application needs and config.toml says *how* an
        # environment provides it -- but the generated path names the app and
        # the environment, so an environment's SSM layout lives in the app's
        # checked-in file.
        config = _generate({"web": _svc(environment=["SECRET_KEY=x"])}, app_name="someapp")
        assert config["secrets"]["SECRET_KEY"].startswith("ssm:/someapp/${environment}/")

    def test_the_keys_arrive_in_sorted_variable_order(self):
        config = _generate({"web": _svc(environment=["ZED_KEY=x", "API_TOKEN=y"])})
        assert list(config["secrets"]) == ["API_TOKEN", "ZED_KEY"]


class TestFormatSecretsSection:
    """``format_deploy_toml``'s rendering of ``[secrets]`` today."""

    def test_the_section_is_rendered_as_one_assignment_per_secret(self):
        config = _generate({"web": _svc(environment=["SECRET_KEY=x", "API_TOKEN=y"])})
        assert (
            "[secrets]\n"
            'API_TOKEN = "ssm:/myapp/${environment}/api-token"\n'  # pragma: allowlist secret
            'SECRET_KEY = "ssm:/myapp/${environment}/secret-key"'  # pragma: allowlist secret
        ) in format_deploy_toml(config)

    def test_the_comment_block_names_the_ssm_parameter_to_create(self):
        config = _generate({"web": _svc(environment=["SECRET_KEY=x"])})
        text = format_deploy_toml(config)
        assert "#   aws ssm put-parameter --name" in text
        assert '"/myapp/staging/secret-key"' in text

    def test_the_comment_hardcodes_staging_while_the_value_uses_a_placeholder(self):
        # Pinned, not endorsed: the copy-pasteable command creates the staging
        # parameter only, with no hint that production needs its own.
        config = _generate({"web": _svc(environment=["SECRET_KEY=x"])})
        text = format_deploy_toml(config)
        assert "/myapp/staging/secret-key" in text
        assert "${environment}" in text

    def test_no_secrets_renders_no_section(self):
        config = _generate({"web": _svc(environment=["ALLOWED_HOSTS=*"])})
        assert "[secrets]" not in format_deploy_toml(config)


# ------------------------------------------------------------------------------
# Service and image mapping
# ------------------------------------------------------------------------------


class TestServiceMapping:
    """How compose service names become deploy.toml service and image names."""

    def test_the_service_named_after_the_app_becomes_web(self):
        config = _generate({APP: _svc()}, app_name=APP)
        assert set(config["services"]) == {"web"}
        assert config["services"]["web"]["image"] == "web"

    def test_the_app_name_match_is_case_insensitive_for_the_service(self):
        # Pinned, not endorsed: the service name is compared case-insensitively
        # but the image name is compared exactly, so "MYAPP" is service `web`
        # built from image `MYAPP` -- and no [images.web] exists.
        config = _generate({"MYAPP": _svc()}, app_name=APP)
        assert set(config["services"]) == {"web"}
        assert config["services"]["web"]["image"] == "MYAPP"
        assert set(config["images"]) == {"MYAPP"}

    @pytest.mark.parametrize(
        ("compose_name", "service_name"),
        [
            ("celery-worker", "celery"),
            ("celeryworker", "celery"),
            ("worker", "worker"),
            ("background-worker", "worker"),
            ("celery-beat", "celery"),
            ("api-gateway", "api_gateway"),
            ("my service", "my_service"),
        ],
    )
    def test_the_name_normalization_rules(self, compose_name, service_name):
        config = _generate({compose_name: _svc()})
        assert set(config["services"]) == {service_name}

    def test_the_image_name_only_replaces_separators(self):
        config = _generate({"celery-worker": _svc()})
        assert set(config["images"]) == {"celery_worker"}
        assert config["services"]["celery"]["image"] == "celery_worker"

    def test_two_services_normalizing_to_the_same_name_collide(self):
        # Pinned, not endorsed: `worker` and `background-worker` both normalize
        # to `worker`, and the second silently replaces the first -- so one of
        # the two images is built and never deployed.
        config = _generate({"worker": _svc(ports=["1:1"]), "background-worker": _svc()})
        assert set(config["services"]) == {"worker"}
        assert config["services"]["worker"]["image"] == "background_worker"
        assert set(config["images"]) == {"worker", "background_worker"}

    def test_a_service_without_a_build_is_not_deployed(self):
        config = _generate({"web": _svc(), "sidecar": {"image": "vendor/thing:1"}})
        assert set(config["services"]) == {"web"}

    def test_a_profiled_service_is_not_deployed(self):
        config = _generate({"web": _svc(), "tools": _svc(profiles=["dev"])})
        assert set(config["services"]) == {"web"}

    def test_an_infrastructure_service_is_not_deployed_even_with_a_build(self):
        config = _generate({"web": _svc(), "postgres": _svc()})
        assert set(config["services"]) == {"web"}

    def test_no_deployable_service_is_an_error(self):
        with pytest.raises(ValueError, match="No application services found"):
            _generate({"postgres": {"image": "postgres:16"}})

    def test_the_build_context_and_dockerfile_are_carried_over(self):
        config = _generate(
            {"web": {"build": {"context": "./svc", "dockerfile": "Dockerfile.prod"}}}
        )
        assert config["images"]["web"] == {"context": "./svc", "dockerfile": "Dockerfile.prod"}

    def test_a_string_build_gets_a_null_dockerfile(self):
        config = _generate({"web": {"build": "./svc"}})
        assert config["images"]["web"] == {"context": "./svc", "dockerfile": None}
        # ...and format_deploy_toml omits the key rather than writing "null".
        assert "dockerfile" not in format_deploy_toml(config)


class TestPortsAndHealthChecks:
    """Port extraction and the health-check path that follows from it."""

    @pytest.mark.parametrize(
        ("ports", "expected"),
        [(["8000:8000"], 8000), (["80:8080"], 8080), (["8000"], 8000), ([3000], 3000)],
    )
    def test_the_container_port_is_taken_from_the_mapping(self, ports, expected):
        config = _generate({"web": _svc(ports=ports)})
        assert config["services"]["web"]["port"] == expected

    def test_a_service_without_ports_gets_no_port_and_no_health_check(self):
        config = _generate({"worker": _svc()})
        assert "port" not in config["services"]["worker"]
        assert "health_check_path" not in config["services"]["worker"]

    def test_django_gets_a_trailing_slash_health_check(self):
        config = _generate(
            {"web": _svc(ports=["8000:8000"], environment=["DJANGO_SETTINGS_MODULE=app.settings"])}
        )
        assert config["services"]["web"]["health_check_path"] == "/health/"

    def test_every_other_framework_gets_the_bare_path(self):
        config = _generate({"web": _svc(ports=["3000:3000"], environment=["RAILS_ENV=production"])})
        assert config["services"]["web"]["health_check_path"] == "/health"


# ------------------------------------------------------------------------------
# Environment mapping -- the other legacy_placeholders() consumer
# ------------------------------------------------------------------------------


class TestEnvironmentMapping:
    """The ``[environment]`` section, including the ``${...}`` placeholders.

    ``${database_url}`` and ``${redis_url}`` are resolved by
    ``_resolve_legacy_placeholders`` at deploy time from
    ``InfraConfig.legacy_placeholders()`` -- the *second* consumer of that
    table, and the one 53h-2a leaves in place. These pins are what makes
    "removing the explicit [secrets] form does not touch it" checkable.
    """

    def test_database_url_becomes_a_placeholder(self):
        config = _generate({"web": _svc(environment=["DATABASE_URL=postgres://local"])})
        assert config["environment"]["DATABASE_URL"] == "${database_url}"

    def test_redis_url_becomes_a_placeholder(self):
        config = _generate({"web": _svc(environment=["REDIS_URL=redis://local"])})
        assert config["environment"]["REDIS_URL"] == "${redis_url}"

    def test_celery_broker_url_also_writes_redis_url(self):
        # Pinned, not endorsed: declaring only CELERY_BROKER_URL produces two
        # environment variables, one of which the application never mentioned.
        config = _generate({"web": _svc(environment=["CELERY_BROKER_URL=redis://local"])})
        assert config["environment"]["CELERY_BROKER_URL"] == "${redis_url}"
        assert config["environment"]["REDIS_URL"] == "${redis_url}"

    def test_allowed_hosts_is_always_present_and_wide_open(self):
        config = _generate({"web": _svc()})
        assert config["environment"]["ALLOWED_HOSTS"] == "*"

    def test_a_declared_allowed_hosts_is_overwritten_with_the_wildcard(self):
        config = _generate({"web": _svc(environment=["ALLOWED_HOSTS=example.com"])})
        assert config["environment"]["ALLOWED_HOSTS"] == "*"

    def test_debug_and_log_level_are_left_to_the_per_environment_overrides(self):
        config = _generate({"web": _svc(environment=["DEBUG=1", "LOG_LEVEL=INFO"])})
        assert "DEBUG" not in config["environment"]
        assert "LOG_LEVEL" not in config["environment"]
        assert config["environment.staging"] == {"DEBUG": "true", "LOG_LEVEL": "DEBUG"}
        assert config["environment.production"] == {"DEBUG": "false", "LOG_LEVEL": "INFO"}

    def test_any_other_variable_is_declared_empty_for_the_operator_to_fill(self):
        config = _generate({"web": _svc(environment=["SENTRY_DSN=https://local"])})
        assert config["environment"]["SENTRY_DSN"] == ""

    def test_the_compose_value_is_never_carried_across(self):
        # deploy.toml is checked in, so a local value must not leak into it.
        config = _generate({"web": _svc(environment=["SENTRY_DSN=https://secret@sentry.io/1"])})
        assert "sentry.io" not in format_deploy_toml(config)

    def test_a_mapping_style_environment_block_is_read_too(self):
        config = _generate({"web": _svc(environment={"SENTRY_DSN": "x", "SECRET_KEY": "y"})})
        assert config["environment"]["SENTRY_DSN"] == ""
        assert _detected_secret_names(config) == {"SECRET_KEY"}


# ------------------------------------------------------------------------------
# Migrations and the audit ignore list
# ------------------------------------------------------------------------------


class TestMigrations:
    """The ``[migrations]`` section follows from framework detection."""

    @pytest.mark.parametrize(
        ("env_var", "command"),
        [
            ("DJANGO_SETTINGS_MODULE", ["python", "manage.py", "migrate"]),
            ("RAILS_ENV", ["bundle", "exec", "rails", "db:migrate"]),
            ("FLASK_APP", ["flask", "db", "upgrade"]),
            ("FASTAPI_ENV", ["alembic", "upgrade", "head"]),
        ],
    )
    def test_each_framework_gets_its_migration_command(self, env_var, command):
        config = _generate({"web": _svc(environment=[f"{env_var}=x"])})
        assert config["migrations"] == {"enabled": True, "service": "web", "command": command}

    def test_a_framework_without_migrations_gets_no_section(self):
        config = _generate({"web": _svc(environment=["NODE_ENV=production"])})
        assert "migrations" not in config

    def test_an_undetectable_framework_gets_no_section(self):
        config = _generate({"web": _svc(environment=["TZ=UTC"])})
        assert "migrations" not in config

    def test_migrations_run_on_the_first_service_whose_name_contains_web(self):
        config = _generate(
            {"worker": _svc(environment=["DJANGO_SETTINGS_MODULE=x"]), "web": _svc()}
        )
        assert config["migrations"]["service"] == "web"

    def test_without_a_web_service_migrations_fall_back_to_the_first_one(self):
        config = _generate(
            {"api-gateway": _svc(environment=["DJANGO_SETTINGS_MODULE=x"]), "worker": _svc()}
        )
        assert config["migrations"]["service"] == "api_gateway"

    def test_the_dockerfile_is_read_for_framework_detection(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python\nCMD gunicorn myapp.wsgi\n")
        (tmp_path / "docker-compose.yml").write_text(_COMPOSE_WITH_DOCKERFILE)
        config = generate_deploy_toml(
            compose_path=tmp_path / "docker-compose.yml", app_name=APP, compose_data=None
        )
        assert config["migrations"]["command"] == ["python", "manage.py", "migrate"]

    def test_an_unreadable_dockerfile_is_ignored_rather_than_raising(self, tmp_path):
        (tmp_path / "Dockerfile").mkdir()  # a directory read_text() cannot read
        (tmp_path / "docker-compose.yml").write_text(_COMPOSE_WITH_DOCKERFILE)
        config = generate_deploy_toml(
            compose_path=tmp_path / "docker-compose.yml", app_name=APP, compose_data=None
        )
        assert "migrations" not in config


class TestDockerfileProbeCrashesWithoutAnExplicitDockerfile:
    """``generate_deploy_toml`` raises ``TypeError`` on an ordinary compose file.

    **A live bug, found by these pins, not introduced by them.**
    ``get_compose_services`` sets ``dockerfile`` to ``None`` unless the compose
    file spells the key out, so ``svc.get("dockerfile", "Dockerfile")`` in
    ``_read_dockerfile_content`` returns ``None`` -- the default never fires,
    because the key is present. ``compose_path.parent / context / None`` then
    raises before ``.exists()`` is ever reached.

    Both ordinary spellings of ``build`` are affected, so essentially every
    real docker-compose.yml hits it. ``bin/init.py`` catches bare ``Exception``
    around ``generate_deploy_toml`` and prints "Error parsing
    docker-compose.yml", so the operator is told their YAML is wrong.

    Pinned here as it stands; 53h-2a fixes it in its own commit, because
    ``deployer init`` has to run at all before the round-trip verification the
    phase requires can be performed.
    """

    @pytest.mark.parametrize(
        "build_block",
        ["    build: .\n", "    build:\n      context: .\n"],
        ids=["string-form", "mapping-without-dockerfile"],
    )
    def test_a_compose_file_without_an_explicit_dockerfile_raises(self, tmp_path, build_block):
        (tmp_path / "docker-compose.yml").write_text(f"services:\n  web:\n{build_block}")
        with pytest.raises(TypeError):
            generate_deploy_toml(
                compose_path=tmp_path / "docker-compose.yml", app_name=APP, compose_data=None
            )

    def test_the_same_compose_data_passed_in_memory_is_fine(self):
        # The crash is in the Dockerfile probe, which only runs when a
        # compose_path was given -- so the in-memory pins above never see it.
        assert _generate({"web": {"build": "."}})["images"]["web"]["dockerfile"] is None


class TestAuditIgnore:
    """The ``[audit] ignore`` list of infrastructure services."""

    def test_infrastructure_services_are_listed_for_the_audit_to_skip(self):
        config = _generate(
            {"web": _svc(), "postgres": {"image": "postgres:16"}, "redis": {"image": "redis:7"}}
        )
        assert config["audit"] == {"ignore": ["postgres", "redis"]}

    def test_the_match_is_a_substring_of_the_service_name(self):
        config = _generate({"web": _svc(), "primary-mongodb-replica": {"image": "mongo:7"}})
        assert config["audit"] == {"ignore": ["primary-mongodb-replica"]}

    def test_no_infrastructure_means_no_audit_section(self):
        config = _generate({"web": _svc()})
        assert "audit" not in config


# ------------------------------------------------------------------------------
# Entry-point behaviour
# ------------------------------------------------------------------------------


class TestGenerateEntryPoint:
    """``generate_deploy_toml``'s own arguments."""

    def test_neither_a_path_nor_data_is_an_error(self):
        with pytest.raises(ValueError, match="compose_path or compose_data"):
            generate_deploy_toml(compose_path=None, app_name=APP, compose_data=None)

    def test_an_omitted_app_name_comes_from_the_compose_directory(self, tmp_path):
        project = tmp_path / "some-project"
        project.mkdir()
        (project / "docker-compose.yml").write_text(_COMPOSE_WITH_DOCKERFILE)
        config = generate_deploy_toml(
            compose_path=project / "docker-compose.yml", app_name=None, compose_data=None
        )
        assert config["application"]["name"] == "some-project"

    def test_an_omitted_app_name_without_a_path_falls_back_to_myapp(self):
        config = generate_deploy_toml(
            compose_path=None, app_name=None, compose_data={"services": {"api": {"build": "."}}}
        )
        assert config["application"]["name"] == "myapp"

    def test_the_application_section_is_derived_from_the_app_name(self):
        config = _generate({"web": _svc()}, app_name="some-project")
        assert config["application"] == {
            "name": "some-project",
            "description": "Some-Project application",
            "source": ".",
        }


class TestFormatDeployToml:
    """``format_deploy_toml``'s section order and header."""

    def test_the_sections_are_written_in_a_fixed_order(self):
        config = _generate(
            {
                "web": _svc(ports=["8000:8000"], environment=["DJANGO_SETTINGS_MODULE=x", "KEY=y"]),
                "postgres": {"image": "postgres:16"},
            }
        )
        text = format_deploy_toml(config)
        order = [
            "[application]",
            "[images.web]",
            "[services.web]",
            "[environment]",
            "[environment.staging]",
            "[environment.production]",
            "[secrets]",
            "[migrations]",
            "[audit]",
        ]
        positions = [text.index(section) for section in order]
        assert positions == sorted(positions)

    def test_the_header_points_at_the_config_reference(self):
        assert "docs/CONFIG-REFERENCE.md" in format_deploy_toml(_generate({"web": _svc()}))
