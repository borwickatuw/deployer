"""Characterization pins for the ``modules/`` collect()/validate() interface.

Phase 53h-1 wrote these before touching ``modules/``. They are characterization
pins, not endorsements: they record what the module system does **today**, and
where the behaviour looks wrong it is pinned anyway with a comment saying so.

**Every pin here is driven through a seam that 53h-1 does not move**, which is
53d-2a's recorded rule (test the outermost boundary available) applied to an
interface refactor:

* ``get_environment_variables(ctx, service_name, credential_mode)`` and
  ``get_secrets(ctx, service_name, credential_mode)`` take a
  ``DeploymentContext`` and return plain dicts/lists. They are the only two
  production callers of ``ModuleRegistry.collect_all``, and they own
  ``_build_module_context``. Driving the modules from here means the pins say
  nothing about ``ModuleContext``'s fields or ``collect()``'s parameter list --
  the two things 53h-1 changes.
* ``DatabaseModule.validate(app_config, env_config)`` and friends take two
  dicts. 53h-1 leaves ``validate()``'s signature alone; whether it grows a
  config type is 53h-2's call, and these pins are what that call gets measured
  against.

Nothing is stubbed. The module system is pure over dicts, so a
``DeploymentContext`` carrying a real ``config``/``env_config`` pair drives the
whole path with no AWS in it.

What is pinned:

* ``database.collect`` in all four combinations that matter -- ``app`` and
  ``migrate`` credential modes against ``secretsmanager`` and ``ssm``
  providers. The SSM arm was **entirely uncovered** before this file
  (``database.py`` 170-185); it is the arm that builds an ARN by hand.
* ``database.collect``'s two guards: a ``[database]`` section with no ``type``
  collects nothing, and an unknown ``credential_mode`` raises ``ValueError``.
* ``secrets.collect`` through both routes into it -- the ``collect_all`` route
  and ``get_secrets``' separate ``uses_names_style`` route -- plus its
  ``path_prefix`` normalisation.
* ``cache.collect`` and ``storage.collect``, whose ``context`` parameter is
  unused and whose output must not change when it goes away.
* The ``_MODULE_SECTIONS``-vs-registry gap. ``_MODULE_SECTIONS`` names ``cdn``
  and ``autoscale``, which no registered module implements. The consequences
  are pinned in ``TestModuleSectionsRegistryGap`` and are **not** endorsed:
  they are the evidence 53h-2 adjudicates.
* Every error arm of ``database.validate``, which was the other large uncovered
  region (``database.py`` 76-114) and is the ``feature-envy`` finding 53h-2
  decides.

This takes ``modules/`` to 100% on ``database.py``, ``cache.py``,
``storage.py`` and ``__init__.py``. Two things are deliberately left uncovered
rather than reached by calling past the seam:

* ``base.py`` 60/110/129 -- the ``pass`` bodies of three ``@abstractmethod``
  declarations. Not executable.
* ``secrets.py`` 99 -- ``collect``'s ``if not app_config`` guard. It is
  **unreachable from production**: ``collect_all`` only calls a module whose
  section is truthy, and ``get_secrets``' other route requires a ``names`` key.
  A direct call could reach it, but a direct call is the seam 53h-1 moves.
  Noted for 53h-2 as a dead guard rather than pinned.
"""

import pytest

from deployer.deploy.context import DeploymentContext, InfraConfig
from deployer.deploy.task_definition import get_environment_variables, get_secrets
from deployer.modules import ModuleRegistry
from deployer.modules.cache import CacheModule
from deployer.modules.database import DatabaseModule
from deployer.modules.secrets import SecretsModule
from deployer.modules.storage import StorageModule

REGION = "us-west-2"
ACCOUNT_ID = "123456789012"
ENVIRONMENT = "staging"

SM_ARN = "arn:aws:secretsmanager:us-west-2:123456789012:secret"

# A [database] config.toml section carrying both credential pairs, so a single
# fixture drives the app/migrate fork in either direction.
DB_ENV_SECRETSMANAGER = {
    "host": "db.example.com",
    "port": 5432,
    "name": "testdb",
    "credentials": "secretsmanager",
    "app_username_secret": f"{SM_ARN}:app-username",  # pragma: allowlist secret
    "app_password_secret": f"{SM_ARN}:app-password",  # pragma: allowlist secret
    "migrate_username_secret": f"{SM_ARN}:migrate-username",  # pragma: allowlist secret
    "migrate_password_secret": f"{SM_ARN}:migrate-password",  # pragma: allowlist secret
}

DB_ENV_SSM = {
    "host": "db.example.com",
    "port": 5432,
    "name": "testdb",
    "credentials": "ssm",
    "app_username_param": "/myapp/staging/db-app-username",
    "app_password_param": "/myapp/staging/db-app-password",  # pragma: allowlist secret
    "migrate_username_param": "/myapp/staging/db-migrate-username",
    "migrate_password_param": "/myapp/staging/db-migrate-password",  # pragma: allowlist secret
}


def _ctx(config: dict, env_config: dict) -> DeploymentContext:
    """A DeploymentContext carrying a real deploy.toml/config.toml pair.

    Only the fields the module path reads are meaningful: ``config``,
    ``env_config``, ``region``, ``account_id`` and ``environment``. The rest
    are the inert defaults every other DeploymentContext test uses.
    """
    return DeploymentContext(
        ecs_client=None,
        cluster_name="test-cluster",
        config=config,
        service_config={},
        infra_config=InfraConfig(),
        app_name="testapp",
        environment=ENVIRONMENT,
        region=REGION,
        account_id=ACCOUNT_ID,
        env_config=env_config,
        dry_run=False,
    )


def _secret_map(secrets: list[dict[str, str]]) -> dict[str, str]:
    """ECS-format secrets as {name: valueFrom}."""
    return {s["name"]: s["valueFrom"] for s in secrets}


def _ssm_arn(path: str) -> str:
    """The SSM parameter ARN the module system builds for a parameter path."""
    return f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter{path}"


class TestDatabaseCollectSecretsManager:
    """database.collect() -- the `secretsmanager` provider, both modes.

    The provider passes the configured ARN straight through; the credential
    mode picks which pair of ARNs is read.
    """

    def test_app_mode_reads_the_app_credential_pair(self):
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SECRETSMANAGER})

        secrets = _secret_map(get_secrets(ctx, None, credential_mode="app"))

        assert secrets == {
            "DB_USERNAME": f"{SM_ARN}:app-username",
            "DB_PASSWORD": f"{SM_ARN}:app-password",  # pragma: allowlist secret
        }

    def test_migrate_mode_reads_the_migrate_credential_pair(self):
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SECRETSMANAGER})

        secrets = _secret_map(get_secrets(ctx, None, credential_mode="migrate"))

        assert secrets == {
            "DB_USERNAME": f"{SM_ARN}:migrate-username",
            "DB_PASSWORD": f"{SM_ARN}:migrate-password",  # pragma: allowlist secret
        }

    def test_app_is_the_default_credential_mode(self):
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SECRETSMANAGER})

        assert get_secrets(ctx, None) == get_secrets(ctx, None, credential_mode="app")

    def test_the_connection_env_vars_do_not_vary_with_credential_mode(self):
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SECRETSMANAGER})

        app_env = get_environment_variables(ctx, credential_mode="app")
        migrate_env = get_environment_variables(ctx, credential_mode="migrate")

        assert app_env == migrate_env
        assert app_env["DB_HOST"] == "db.example.com"
        assert app_env["DB_NAME"] == "testdb"

    def test_the_port_is_stringified(self):
        """config.toml may give port as an int; ECS env vars are strings."""
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SECRETSMANAGER})

        assert get_environment_variables(ctx)["DB_PORT"] == "5432"


class TestDatabaseCollectSsm:
    """database.collect() -- the `ssm` provider, both modes.

    Unlike the `secretsmanager` arm, this one **constructs** the ARN from the
    region and account id in the module context and the configured parameter
    path. This whole arm was uncovered before Phase 53h-1.
    """

    def test_app_mode_builds_arns_from_the_app_parameter_pair(self):
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SSM})

        secrets = _secret_map(get_secrets(ctx, None, credential_mode="app"))

        assert secrets == {
            "DB_USERNAME": _ssm_arn("/myapp/staging/db-app-username"),
            "DB_PASSWORD": _ssm_arn("/myapp/staging/db-app-password"),  # pragma: allowlist secret
        }

    def test_migrate_mode_builds_arns_from_the_migrate_parameter_pair(self):
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SSM})

        secrets = _secret_map(get_secrets(ctx, None, credential_mode="migrate"))

        assert secrets == {
            "DB_USERNAME": _ssm_arn("/myapp/staging/db-migrate-username"),
            "DB_PASSWORD": _ssm_arn(
                "/myapp/staging/db-migrate-password"  # pragma: allowlist secret
            ),
        }

    def test_the_arn_carries_the_deployments_region_and_account(self):
        """The ARN is built from the context, not from the parameter path."""
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SSM})

        username = _secret_map(get_secrets(ctx, None))["DB_USERNAME"]

        assert username.startswith(f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter")

    def test_the_parameter_path_is_concatenated_verbatim(self):
        """No separator is inserted: a path without a leading / runs together.

        Pinned as-is. ``secrets.collect`` normalises its ``path_prefix``;
        ``database.collect`` does not normalise its parameter paths.
        """
        env_config = {"database": {**DB_ENV_SSM, "app_username_param": "no-leading-slash"}}
        ctx = _ctx({"database": {"type": "postgresql"}}, env_config)

        assert _secret_map(get_secrets(ctx, None))["DB_USERNAME"].endswith(
            ":parameterno-leading-slash"
        )


class TestDatabaseCollectGuards:
    """database.collect()'s two early exits."""

    def test_a_database_section_without_a_type_collects_nothing(self):
        """[database] present but typeless: no env vars, no secrets, no error."""
        ctx = _ctx({"database": {"extensions": ["unaccent"]}}, {"database": DB_ENV_SSM})

        assert get_secrets(ctx, None) == []
        assert "DB_HOST" not in get_environment_variables(ctx)

    def test_an_unknown_credential_mode_raises(self):
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": DB_ENV_SECRETSMANAGER})

        with pytest.raises(ValueError, match="credential_mode must be 'app' or 'migrate'"):
            get_secrets(ctx, None, credential_mode="readonly")

    def test_the_credential_mode_is_checked_before_the_provider(self):
        """The guard fires even for a provider that ignores the mode entirely."""
        ctx = _ctx({"database": {"type": "postgresql"}}, {"database": {"credentials": "ssm"}})

        with pytest.raises(ValueError, match="credential_mode"):
            get_secrets(ctx, None, credential_mode="")

    def test_an_unsupported_provider_yields_no_credentials_and_no_error(self):
        """collect() does not re-check what validate() rejects.

        ``validate`` calls ``credentials = "vault"`` an error; ``collect`` just
        falls off the end of its if/elif and emits the connection env vars with
        no credentials at all. Pinned as-is: the container would start and fail
        to authenticate. Nothing here fixes it.
        """
        env_config = {"database": {**DB_ENV_SECRETSMANAGER, "credentials": "vault"}}
        ctx = _ctx({"database": {"type": "postgresql"}}, env_config)

        assert get_secrets(ctx, None) == []
        assert get_environment_variables(ctx)["DB_HOST"] == "db.example.com"


class TestSecretsCollect:
    """secrets.collect() -- SSM parameter paths built from a prefix.

    ``get_secrets`` reaches this module by two different routes, and both are
    pinned: via ``collect_all`` when another module section is present, and via
    its own ``uses_names_style`` branch when ``[secrets]`` is alone.
    """

    ENV = {"secrets": {"provider": "ssm", "path_prefix": "/myapp/staging"}}

    def test_names_are_normalised_to_hyphenated_lowercase_paths(self):
        ctx = _ctx({"secrets": {"names": ["SECRET_KEY", "DATACITE_PASSWORD"]}}, self.ENV)

        secrets = _secret_map(get_secrets(ctx, None))

        assert secrets == {
            "SECRET_KEY": _ssm_arn("/myapp/staging/secret-key"),  # pragma: allowlist secret
            "DATACITE_PASSWORD": _ssm_arn(
                "/myapp/staging/datacite-password"  # pragma: allowlist secret
            ),
        }

    def test_the_collect_all_route_produces_the_same_paths(self):
        """With [database] also declared, secrets arrive via collect_all."""
        config = {
            "database": {"type": "postgresql"},
            "secrets": {"names": ["SECRET_KEY"]},
        }
        env_config = {**self.ENV, "database": DB_ENV_SECRETSMANAGER}
        ctx = _ctx(config, env_config)

        secrets = _secret_map(get_secrets(ctx, None))

        assert secrets["SECRET_KEY"] == _ssm_arn(
            "/myapp/staging/secret-key"  # pragma: allowlist secret
        )
        assert "DB_USERNAME" in secrets

    def test_a_path_prefix_is_normalised_at_both_ends(self):
        """A missing leading slash is added; a trailing slash is stripped."""
        env_config = {"secrets": {"provider": "ssm", "path_prefix": "myapp/staging/"}}
        ctx = _ctx({"secrets": {"names": ["SECRET_KEY"]}}, env_config)

        assert _secret_map(get_secrets(ctx, None))["SECRET_KEY"] == _ssm_arn(
            "/myapp/staging/secret-key"  # pragma: allowlist secret
        )

    def test_an_empty_names_list_collects_nothing(self):
        ctx = _ctx({"secrets": {"names": []}}, self.ENV)

        assert get_secrets(ctx, None) == []

    def test_secrets_contribute_no_environment_variables(self):
        ctx = _ctx({"secrets": {"names": ["SECRET_KEY"]}}, self.ENV)

        assert "SECRET_KEY" not in get_environment_variables(ctx)


class TestCacheAndStorageCollect:
    """cache.collect() and storage.collect() -- the two that ignore `context`.

    53h-1 retires their ``vestigial-params`` suppressions. Their output must be
    unchanged by that, which is what these pins hold.
    """

    def test_cache_injects_the_configured_redis_url(self):
        ctx = _ctx({"cache": {"type": "redis"}}, {"cache": {"url": "redis://cache:6379/0"}})

        assert get_environment_variables(ctx)["REDIS_URL"] == "redis://cache:6379/0"

    def test_cache_without_a_type_injects_nothing(self):
        ctx = _ctx({"cache": {"enabled": True}}, {"cache": {"url": "redis://cache:6379/0"}})

        assert "REDIS_URL" not in get_environment_variables(ctx)

    def test_storage_injects_one_env_var_per_declared_bucket(self):
        config = {"storage": {"type": "s3", "buckets": ["originals", "media"]}}
        env_config = {"storage": {"originals_bucket": "app-originals", "media_bucket": "app-media"}}
        ctx = _ctx(config, env_config)

        env = get_environment_variables(ctx)

        assert env["S3_ORIGINALS_BUCKET"] == "app-originals"
        assert env["S3_MEDIA_BUCKET"] == "app-media"

    def test_storage_without_a_type_injects_nothing(self):
        ctx = _ctx({"storage": {"buckets": ["media"]}}, {"storage": {"media_bucket": "app-media"}})

        assert get_environment_variables(ctx) == {}

    def test_neither_module_contributes_secrets(self):
        config = {"cache": {"type": "redis"}, "storage": {"type": "s3", "buckets": ["media"]}}
        env_config = {
            "cache": {"url": "redis://cache:6379/0"},
            "storage": {"media_bucket": "app-media"},
        }

        assert get_secrets(_ctx(config, env_config), None) == []


class TestCollectAllRouting:
    """ModuleRegistry.collect_all() -- which sections are collected from."""

    def test_a_section_absent_from_deploy_toml_is_skipped(self):
        """config.toml may describe more than deploy.toml declares."""
        env_config = {"database": DB_ENV_SECRETSMANAGER, "cache": {"url": "redis://cache:6379/0"}}
        ctx = _ctx({"database": {"type": "postgresql"}}, env_config)

        assert "REDIS_URL" not in get_environment_variables(ctx)

    def test_outputs_from_several_modules_merge(self):
        config = {"database": {"type": "postgresql"}, "cache": {"type": "redis"}}
        env_config = {"database": DB_ENV_SSM, "cache": {"url": "redis://cache:6379/0"}}
        ctx = _ctx(config, env_config)

        env = get_environment_variables(ctx)

        assert {"DB_HOST", "DB_PORT", "DB_NAME", "REDIS_URL"} <= set(env)
        assert set(_secret_map(get_secrets(ctx, None))) == {"DB_USERNAME", "DB_PASSWORD"}

    def test_an_empty_env_config_keeps_the_module_system_out(self):
        """No config.toml means the legacy path runs, whatever deploy.toml says."""
        ctx = _ctx({"database": {"type": "postgresql"}}, {})

        assert get_secrets(ctx, None) == []
        assert get_environment_variables(ctx) == {}


class TestModuleSectionsRegistryGap:
    """`_MODULE_SECTIONS` and the module registry do not agree. Pinned, not endorsed.

    ``_MODULE_SECTIONS`` names ``cdn`` and ``autoscale``; the registry holds
    Database, Cache, Storage and Secrets. So a section can flip "the module
    system is in use" without any module ever validating or collecting it, and
    ``secrets`` -- which *is* a registered module -- is missing from the tuple
    and special-cased instead. 53h-2 owns the fix; these pins are the evidence.
    """

    def test_an_unimplemented_section_is_never_validated(self):
        """[cdn] can say anything at all and validate_all reports nothing."""
        assert ModuleRegistry.validate_all({"cdn": {"type": "not-a-real-type"}}, {}) == []

    def test_an_unimplemented_section_still_switches_on_the_module_path(self):
        """[cdn] flips uses_modules, so legacy [secrets] are silently dropped.

        Declaring an unimplemented module changes which secrets a container
        gets. This is a real trap, pinned as it stands today.
        """
        config = {
            "cdn": {"enabled": True},
            # Legacy explicit style.
            "secrets": {"SECRET_KEY": "ssm:/myapp/staging/secret-key"},  # pragma: allowlist secret
        }
        ctx = _ctx(config, {"environment": {"domain_name": "app.example.com"}})

        assert get_secrets(ctx, None) == []

    def test_without_the_cdn_section_the_same_config_uses_the_legacy_path(self):
        """The contrast: drop [cdn] and the legacy secret resolves normally."""
        config = {
            "secrets": {"SECRET_KEY": "ssm:/myapp/staging/secret-key"}  # pragma: allowlist secret
        }
        ctx = _ctx(config, {"environment": {"domain_name": "app.example.com"}})

        assert _secret_map(get_secrets(ctx, None)) == {
            "SECRET_KEY": _ssm_arn("/myapp/staging/secret-key")  # pragma: allowlist secret
        }

    def test_secrets_alone_switches_env_vars_but_not_secrets_onto_the_module_path(self):
        """The two readers disagree: get_environment_variables counts
        ``secrets`` as a module section, get_secrets does not."""
        config = {"secrets": {"names": ["SECRET_KEY"]}}
        env_config = {"secrets": {"provider": "ssm", "path_prefix": "/myapp/staging"}}
        ctx = _ctx(config, env_config)

        # Same answer by two different routes -- for now.
        assert _secret_map(get_secrets(ctx, None)) == {
            "SECRET_KEY": _ssm_arn("/myapp/staging/secret-key")  # pragma: allowlist secret
        }
        assert get_environment_variables(ctx) == {}


class TestDatabaseValidate:
    """database.validate() -- every error arm.

    This is the ``feature-envy`` finding 53h-2 adjudicates: 11 reads of
    ``env_config`` against 1 of ``self``. Whatever shape the config takes
    afterwards, these are the messages it has to keep producing.
    """

    APP = {"type": "postgresql"}

    def _errors(self, env_config: dict, app_config: dict | None = None) -> list[str]:
        return DatabaseModule().validate(app_config or self.APP, env_config)

    def test_a_valid_secretsmanager_config_passes(self):
        assert self._errors(DB_ENV_SECRETSMANAGER) == []

    def test_a_valid_ssm_config_passes(self):
        assert self._errors(DB_ENV_SSM) == []

    def test_an_undeclared_module_is_not_an_error(self):
        assert DatabaseModule().validate({}, {}) == []

    def test_a_declaration_without_a_type_is_an_error(self):
        assert self._errors({}, app_config={"extensions": []}) == [
            "[database] section missing 'type' in deploy.toml"
        ]

    def test_an_unsupported_type_is_an_error(self):
        assert self._errors({}, app_config={"type": "mysql"}) == [
            "[database] type 'mysql' not supported (only 'postgresql')"
        ]

    def test_a_missing_config_toml_section_is_an_error(self):
        assert self._errors({}) == ["[database] section missing from config.toml"]

    @pytest.mark.parametrize("field", ["host", "port", "name"])
    def test_each_connection_field_is_required(self, field):
        env_config = {k: v for k, v in DB_ENV_SECRETSMANAGER.items() if k != field}

        assert self._errors(env_config) == [f"[database] section missing '{field}' in config.toml"]

    def test_declared_extensions_require_an_extensions_lambda(self):
        errors = self._errors(
            DB_ENV_SECRETSMANAGER, app_config={"type": "postgresql", "extensions": ["unaccent"]}
        )

        assert any("extensions_lambda" in e for e in errors)

    def test_declared_extensions_pass_once_the_lambda_is_configured(self):
        env_config = {**DB_ENV_SECRETSMANAGER, "extensions_lambda": "myapp-staging-db-users"}

        assert (
            self._errors(env_config, app_config={"type": "postgresql", "extensions": ["unaccent"]})
            == []
        )

    @pytest.mark.parametrize(
        "field",
        [
            "app_username_secret",
            "app_password_secret",
            "migrate_username_secret",
            "migrate_password_secret",
        ],
    )
    def test_secretsmanager_requires_both_credential_pairs(self, field):
        env_config = {k: v for k, v in DB_ENV_SECRETSMANAGER.items() if k != field}

        assert self._errors(env_config) == [
            f"[database] using secretsmanager but missing '{field}' in config.toml"
        ]

    @pytest.mark.parametrize(
        "field",
        [
            "app_username_param",
            "app_password_param",
            "migrate_username_param",
            "migrate_password_param",
        ],
    )
    def test_ssm_requires_both_parameter_pairs(self, field):
        env_config = {k: v for k, v in DB_ENV_SSM.items() if k != field}

        assert self._errors(env_config) == [
            f"[database] using ssm but missing '{field}' in config.toml"
        ]

    def test_an_unsupported_credentials_provider_is_an_error(self):
        env_config = {**DB_ENV_SECRETSMANAGER, "credentials": "vault"}

        assert self._errors(env_config) == [
            "[database] credentials 'vault' not supported (use 'secretsmanager' or 'ssm')"
        ]

    def test_missing_credentials_is_its_own_error(self):
        env_config = {k: v for k, v in DB_ENV_SECRETSMANAGER.items() if k != "credentials"}

        assert self._errors(env_config) == [
            "[database] section missing 'credentials' in config.toml "
            "(use 'secretsmanager' or 'ssm')"
        ]

    def test_errors_accumulate_across_arms(self):
        """A section missing everything reports every missing field at once."""
        errors = self._errors({"host": "db.example.com"})

        assert len(errors) == 3  # port, name, credentials


class TestOtherModulesValidate:
    """The error arms of the three other modules' validate()."""

    def test_cache_reports_a_missing_url(self):
        assert CacheModule().validate({"type": "redis"}, {"other": "x"}) == [
            "[cache] section missing 'url' in config.toml"
        ]

    def test_cache_rejects_an_unsupported_type(self):
        assert CacheModule().validate({"type": "memcached"}, {}) == [
            "[cache] type 'memcached' not supported (only 'redis')"
        ]

    def test_storage_rejects_an_unsupported_type(self):
        assert StorageModule().validate({"type": "efs"}, {}) == [
            "[storage] type 'efs' not supported (only 's3')"
        ]

    def test_storage_reports_a_missing_config_toml_section(self):
        assert StorageModule().validate({"type": "s3", "buckets": ["media"]}, {}) == [
            "[storage] section missing from config.toml"
        ]

    def test_storage_requires_a_buckets_list(self):
        assert StorageModule().validate({"type": "s3"}, {"media_bucket": "b"}) == [
            "[storage] section missing 'buckets' list in deploy.toml"
        ]

    def test_storage_rejects_a_non_list_buckets_value(self):
        assert StorageModule().validate({"type": "s3", "buckets": "media"}, {"x": "y"}) == [
            "[storage] 'buckets' must be a list in deploy.toml"
        ]

    def test_storage_reports_each_unbacked_bucket(self):
        errors = StorageModule().validate(
            {"type": "s3", "buckets": ["originals", "media"]}, {"media_bucket": "app-media"}
        )

        assert errors == [
            "[storage] missing 'originals_bucket' in config.toml " "for declared bucket 'originals'"
        ]

    def test_secrets_undeclared_is_not_an_error(self):
        assert SecretsModule().validate({}, {}) == []

    def test_secrets_with_an_empty_names_list_is_not_an_error(self):
        assert SecretsModule().validate({"names": []}, {}) == []

    def test_secrets_names_must_be_a_list(self):
        assert SecretsModule().validate({"names": "SECRET_KEY"}, {}) == [
            "[secrets] 'names' must be a list in deploy.toml"
        ]

    def test_a_non_string_secret_name_is_reported_by_type(self):
        errors = SecretsModule().validate({"names": [7]}, {"provider": "ssm", "path_prefix": "/p"})

        assert errors == ["[secrets] name must be a string, got int"]

    def test_a_lowercase_secret_name_is_reported(self):
        errors = SecretsModule().validate(
            {"names": ["secret_key"]}, {"provider": "ssm", "path_prefix": "/p"}
        )

        assert errors == [
            "[secrets] name 'secret_key' should be uppercase " "with underscores (e.g., SECRET_KEY)"
        ]

    def test_secrets_reports_a_missing_config_toml_section(self):
        assert SecretsModule().validate({"names": ["SECRET_KEY"]}, {}) == [
            "[secrets] section missing from config.toml"
        ]

    def test_secrets_rejects_a_non_ssm_provider(self):
        errors = SecretsModule().validate(
            {"names": ["SECRET_KEY"]}, {"provider": "vault", "path_prefix": "/p"}
        )

        assert errors == ["[secrets] provider 'vault' not supported (only 'ssm')"]

    def test_secrets_requires_a_path_prefix(self):
        errors = SecretsModule().validate({"names": ["SECRET_KEY"]}, {"provider": "ssm"})

        assert errors == ["[secrets] section missing 'path_prefix' in config.toml"]
