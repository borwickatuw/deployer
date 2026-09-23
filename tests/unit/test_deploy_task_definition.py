"""Characterization pins for deploy/task_definition.py's placeholder reader.

These are characterization pins, not endorsements. They record what
``_resolve_legacy_placeholders()`` does **today**; where the behaviour looks
wrong it is pinned anyway and called out in a comment on the test. Nothing
here is a fix.

Phase 53f-1 wrote these pins while ``_resolve_legacy_placeholders`` and its
since-deleted sibling ``_get_legacy_secrets`` both took the raw ``infra_config``
dict and each built its own placeholder table from it, in the same eight lines,
twice. 53f-4 converted ``infra_config`` to ``InfraConfig`` and moved those eight
lines into ``InfraConfig.legacy_placeholders()``.

**53h-2a deleted ``_get_legacy_secrets`` and every pin that named it.** The
explicit ``[secrets]`` form it implemented is gone: it was one of two
contradictory documented styles, and the one that silently dropped every
secret whenever the same deploy.toml also declared a module section. Those pins
went with their code, which is what a characterization pin is for -- they were
never a reason to keep it.

``InfraConfig.legacy_placeholders()`` **stays**, because
``_resolve_legacy_placeholders`` is its other consumer: ``${database_url}``-
style substitution in ``[environment]`` is a separate mechanism that 53h-2a
does not touch, and these pins are what makes that a checked claim.

What is pinned here:

* ``_resolve_legacy_placeholders`` -- the string arm, and since Phase 69 the
  fail-fast rule: an unknown, unset, non-scalar or unresolved ``services.``
  placeholder is a ``ValueError``, and a placeholder is substituted wherever
  it appears in a value. It used to pass all of those through verbatim and
  substitute only a value that was entirely one placeholder.
* ``InfraConfig.legacy_placeholders`` -- the ``int``/``float`` ``str()`` arm
  and the ``None``/list/dict/``bool`` drop, which is the single place those
  now happen (``bool`` joined the drop in Phase 69).
* The values ``_build_infra_config`` really produces -- lists, nested dicts and
  ``None`` -- driven end to end, since a typed ``infra_config`` has to keep
  answering for those.
* ``get_secrets`` with no environment config, which is the one route left into
  it and which now yields nothing rather than falling through to a second
  reader.
* **Settled by 53f-4:** ``get_environment_variables`` used to guard its
  legacy-placeholder pass with ``if infra_config:``, testing a spread that
  always carried ``account_id`` and so was permanently true. 53f-4 deleted the
  guard rather than keep a branch whose false arm coverage proved unreachable.
  The behaviour those pins assert -- the pass runs, and ``${account_id}`` is
  offered here and only here -- is unchanged.

Nothing is stubbed: the function is pure over dicts, and
``get_environment_variables`` is driven with an empty ``env_config`` so the
module system stays out of it. That is the outermost boundary available --
53d-2a's recorded rule -- and it means the pins survive any code motion inside
the package.
"""

import re

import pytest

from deployer.deploy.context import DeploymentContext, InfraConfig
from deployer.deploy.deployer import _build_infra_config
from deployer.deploy.task_definition import (
    _resolve_legacy_placeholders,
    build_task_definition,
    get_environment_variables,
    get_secrets,
)

REGION = "us-west-2"
ACCOUNT_ID = "123456789012"
ENVIRONMENT = "staging"


def _ctx(**overrides) -> DeploymentContext:
    """A DeploymentContext with the module system switched off.

    An empty ``env_config`` keeps ModuleRegistry out, so what runs is the
    placeholder pass and nothing else.
    """
    defaults = {
        "ecs_client": None,
        "cluster_name": "test-cluster",
        "config": {},
        "service_config": {},
        "infra_config": InfraConfig(),
        "app_name": "testapp",
        "environment": ENVIRONMENT,
        "region": REGION,
        "account_id": ACCOUNT_ID,
        "env_config": {},
        "dry_run": False,
    }
    defaults.update(overrides)
    return DeploymentContext(**defaults)


class TestInfraConfigLegacyPlaceholders:
    """InfraConfig.legacy_placeholders() -- the one scalar filter.

    Until 53f-4 this filter was eight lines written twice, once inside
    ``_get_legacy_secrets`` and once inside ``_resolve_legacy_placeholders``,
    and 53f-1 pinned it twice to match. 53f-4 made it one implementation and
    53h-2a deleted the first of its two callers, so the table now serves
    ``[environment]`` substitution alone -- and gets an end-to-end pin over
    real ``_build_infra_config`` output further down.
    """

    def test_a_string_field_is_offered_under_its_own_name(self):
        assert _build_infra_config(
            {"database": {"url": "postgres://db/app"}}
        ).legacy_placeholders() == {"database_url": "postgres://db/app"}

    def test_a_numeric_field_is_stringified(self):
        assert InfraConfig(db_port=5432).legacy_placeholders() == {"db_port": "5432"}

    def test_a_float_field_is_stringified(self):
        # config.toml is TOML and nothing type-checks `port`, so a float
        # really can arrive here; the float arm of the filter is not academic.
        assert _build_infra_config({"database": {"port": 1.5}}).legacy_placeholders() == {
            "db_port": "1.5"
        }

    def test_a_bool_field_makes_no_placeholder(self):
        # Phase 69 member 8: bool subclasses int, so it used to pass the numeric
        # filter and render Python-style as "True". No InfraConfig field is a
        # bool, so a bool here is a malformed config.toml value; it now makes no
        # placeholder, and a reference to it is reported as unresolved.
        assert _build_infra_config({"database": {"port": True}}).legacy_placeholders() == {}

    @pytest.mark.parametrize(
        "infra_config",
        [
            InfraConfig(database_url=None),
            InfraConfig(subnet_ids=["subnet-1"]),
            InfraConfig(scheduler={"enabled": False}),
        ],
        ids=["none", "list", "dict"],
    )
    def test_a_non_scalar_field_makes_no_placeholder(self, infra_config):
        # _build_infra_config really produces all three shapes -- `database_url`
        # can be None, `subnet_ids` is a list, `scheduler` is a dict -- so an
        # unresolved `${...}` reaches ECS verbatim.
        assert infra_config.legacy_placeholders() == {}


class TestGetSecretsWithoutAnEnvironmentConfig:
    """**UPDATED PIN.** ``get_secrets`` used to fall through to a second reader.

    ``TestGetSecretsRoutesToTheLegacyPath`` pinned three routes into
    ``_get_legacy_secrets``, all of which are gone with it. What is left is the
    one rule: no environment config means no module has anything to resolve
    its values from, so nothing is collected.
    """

    def test_an_explicit_secrets_section_yields_nothing_here(self):
        # It never reaches this point in production -- preflight's
        # check_secrets_style rejects it by name -- but the reader itself no
        # longer has any code that would read it.
        ctx = _ctx(config={"secrets": {"SECRET_KEY": "ssm:/app/k"}})  # pragma: allowlist secret
        assert get_secrets(ctx, "web") == []

    def test_a_names_style_section_without_an_env_config_yields_nothing(self):
        ctx = _ctx(config={"secrets": {"names": ["SECRET_KEY"]}})
        assert get_secrets(ctx, "web") == []

    def test_a_declared_database_without_an_env_config_yields_nothing(self):
        ctx = _ctx(config={"database": {"type": "postgresql"}})
        assert get_secrets(ctx, "web") == []


class TestUnresolvedPlaceholderInValueFrom:
    """Phase 69 member 9: a ``${...}`` in a secret's valueFrom is an error here.

    ``load_environment_config`` resolves only ``${tofu:...}``, so any other
    ``${...}`` in config.toml -- a placeholder written without ``tofu:``, say
    -- survived into the secret's ARN, and ECS rejected it at
    register-task-definition with no hint of which config value it came from.
    """

    def test_a_placeholder_in_a_secret_arn_is_an_error(self):
        ctx = _ctx(
            config={"secrets": {"names": ["SECRET_KEY"]}},
            env_config={"secrets": {"provider": "ssm", "path_prefix": "/${environment}/app"}},
        )
        with pytest.raises(ValueError, match=r"(?s)SECRET_KEY.*\$\{environment\}.*tofu:"):
            get_secrets(ctx, "web")

    def test_a_resolved_secret_arn_passes(self):
        ctx = _ctx(
            config={"secrets": {"names": ["SECRET_KEY"]}},
            env_config={"secrets": {"provider": "ssm", "path_prefix": "/myapp/staging"}},
        )
        arn = f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/myapp/staging/secret-key"
        assert get_secrets(ctx, "web") == [{"name": "SECRET_KEY", "valueFrom": arn}]


class TestContainerHealthCheckInTheTaskDefinition:
    """``container_health_check`` reaches ``containerDefinitions[].healthCheck``."""

    def test_the_block_is_rendered_in_ecs_shape(self):
        ctx = _ctx(
            config={
                "services": {
                    "worker": {
                        "container_health_check": {
                            "command": ["CMD-SHELL", "test -f /tmp/alive"],
                            "interval": 30,
                            "start_period": 60,
                        }
                    }
                }
            }
        )
        container = build_task_definition(ctx, "worker", "image:tag")["containerDefinitions"][0]
        assert container["healthCheck"] == {
            "command": ["CMD-SHELL", "test -f /tmp/alive"],
            "interval": 30,
            "startPeriod": 60,
        }

    def test_no_block_means_no_health_check(self):
        ctx = _ctx(config={"services": {"worker": {}}})
        container = build_task_definition(ctx, "worker", "image:tag")["containerDefinitions"][0]
        assert "healthCheck" not in container


class TestResolveLegacyPlaceholders:
    """_resolve_legacy_placeholders() -- the substitution rule."""

    def test_a_string_infra_value_is_substituted(self):
        resolved = _resolve_legacy_placeholders(
            {"DATABASE_URL": "${database_url}"},
            REGION,
            ENVIRONMENT,
            {"database_url": "postgres://"},
        )
        assert resolved == {"DATABASE_URL": "postgres://"}

    def test_the_two_built_in_placeholders_are_available(self):
        resolved = _resolve_legacy_placeholders(
            {"R": "${aws_region}", "E": "${environment}"}, REGION, ENVIRONMENT, {}
        )
        assert resolved == {"R": REGION, "E": ENVIRONMENT}

    def test_an_infra_key_can_shadow_a_built_in_placeholder(self):
        resolved = _resolve_legacy_placeholders(
            {"R": "${aws_region}"}, REGION, ENVIRONMENT, {"aws_region": "eu-west-1"}
        )
        assert resolved == {"R": "eu-west-1"}

    # Phase 69 member 7 flipped the next three pins. Each used to hand the
    # container a literal ``${...}`` string, with no warning anywhere.

    def test_an_unknown_placeholder_is_an_error(self):
        with pytest.raises(ValueError, match=r"(?s)'X'.*\$\{nope\}.*aws_region, environment"):
            _resolve_legacy_placeholders({"X": "${nope}"}, REGION, ENVIRONMENT, {})

    def test_an_unresolved_service_url_reference_is_an_error(self):
        # resolve_service_urls runs first; one it left behind could not be
        # resolved (no path_pattern, no domain, no service discovery). It is
        # still never looked up in the table, so an infra key sharing its name
        # cannot clobber it.
        with pytest.raises(ValueError, match=r"(?s)'API'.*\$\{services\.api\.url\}.*path_pattern"):
            _resolve_legacy_placeholders(
                {"API": "${services.api.url}"},
                REGION,
                ENVIRONMENT,
                {"services.api.url": "https://shadowed"},
            )

    def test_an_embedded_placeholder_is_substituted(self):
        # One matching rule, the one ${tofu:...} uses in config.toml: a
        # placeholder is substituted wherever it appears in the value.
        resolved = _resolve_legacy_placeholders(
            {"URL": "https://${host}/path"}, REGION, ENVIRONMENT, {"host": "example.com"}
        )
        assert resolved == {"URL": "https://example.com/path"}

    def test_several_placeholders_in_one_value(self):
        resolved = _resolve_legacy_placeholders(
            {"X": "${environment}-${aws_region}"}, REGION, ENVIRONMENT, {}
        )
        assert resolved == {"X": f"{ENVIRONMENT}-{REGION}"}

    def test_a_non_string_env_var_value_is_copied_through_unchanged(self):
        resolved = _resolve_legacy_placeholders(
            {"PORT": 8000, "DEBUG": True, "NOTHING": None}, REGION, ENVIRONMENT, {}
        )
        assert resolved == {"PORT": 8000, "DEBUG": True, "NOTHING": None}

    def test_every_key_survives_and_order_is_preserved(self):
        env_vars = {"A": "1", "B": "${aws_region}", "C": "3"}
        resolved = _resolve_legacy_placeholders(env_vars, REGION, ENVIRONMENT, {})
        assert list(resolved) == ["A", "B", "C"]


class TestGetEnvironmentVariablesLegacyPass:
    """The legacy-placeholder pass at the end of get_environment_variables().

    53f-1 pinned this as `if infra_config:`, a guard on a spread that always
    carried `account_id` and so was permanently true; coverage confirmed the
    false arm never fired. 53f-4 deleted the guard. What these pins assert --
    the pass always runs, and `${account_id}` is offered here and nowhere
    else -- is unchanged by that.

    `test_an_explicit_account_id_in_infra_config_loses_to_the_spread` is gone:
    it pinned which of two `account_id` entries won a collision, and
    InfraConfig has no `account_id` field, so the collision is no longer
    constructible. The surviving pin below still fixes the one value that
    reaches `${account_id}`.
    """

    def test_an_empty_ctx_infra_config_still_runs_the_legacy_pass(self):
        ctx = _ctx(config={"environment": {"REGION": "${aws_region}"}}, infra_config=InfraConfig())
        assert get_environment_variables(ctx) == {"REGION": REGION}

    def test_account_id_is_injected_as_a_placeholder_by_the_spread(self):
        # Only reachable through this path -- get_secrets does not add it.
        ctx = _ctx(config={"environment": {"ACCOUNT": "${account_id}"}}, infra_config=InfraConfig())
        assert get_environment_variables(ctx) == {"ACCOUNT": ACCOUNT_ID}

    def test_a_real_build_infra_config_resolves_its_scalar_entries(self):
        infra = _build_infra_config(
            {
                "infrastructure": {"rds_instance_id": "myapp-db"},
                "database": {"url": "postgres://db/app", "port": 5432},
            }
        )
        ctx = _ctx(
            config={
                "environment": {
                    "DATABASE_URL": "${database_url}",
                    "DB_PORT": "${db_port}",
                }
            },
            infra_config=infra,
        )
        assert get_environment_variables(ctx) == {
            "DATABASE_URL": "postgres://db/app",
            "DB_PORT": "5432",
        }

    @pytest.mark.parametrize("placeholder", ["${subnet_ids}", "${scheduler}", "${db_host}"])
    def test_a_non_scalar_or_unset_field_is_an_error(self, placeholder):
        # Phase 69 member 7: list- and dict-valued fields, and scalar fields the
        # environment left unset, make no placeholder. They used to reach the
        # container as literal `${...}` strings.
        infra = _build_infra_config({"infrastructure": {"rds_instance_id": "myapp-db"}})
        ctx = _ctx(config={"environment": {"X": placeholder}}, infra_config=infra)
        with pytest.raises(ValueError, match=re.escape(placeholder)):
            get_environment_variables(ctx)
