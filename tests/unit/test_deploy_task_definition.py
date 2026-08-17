"""Characterization pins for deploy/task_definition.py's two placeholder readers.

These are characterization pins, not endorsements. They record what
``_get_legacy_secrets()`` and ``_resolve_legacy_placeholders()`` do **today**;
where the behaviour looks wrong it is pinned anyway and called out in a
comment on the test. Nothing here is a fix.

Phase 53f-1 wrote these pins while both functions still took the raw
``infra_config`` dict and each built its own placeholder table from it, in the
same eight lines, twice. 53f-4 converted ``infra_config`` to
``InfraConfig`` and moved those eight lines into
``InfraConfig.legacy_placeholders()``; both functions now take the finished
``dict[str, str]`` table, which is all they ever needed. The scalar-filter and
``str()`` pins that used to be duplicated here, once per reader, therefore live
in ``TestInfraConfigLegacyPlaceholders`` below -- one copy for the one
implementation. Both readers keep an end-to-end pin driven by the real
``_build_infra_config``.

What is pinned here:

* ``_get_legacy_secrets`` -- both output arms (``ssm:`` -> a constructed SSM
  parameter ARN, ``secretsmanager:`` -> the ARN with the prefix sliced off),
  the ``${...}`` substring substitution it does *before* looking at the
  prefix, the ``names`` skip, the non-string skip, and the fact that a value
  matching neither prefix is **silently dropped**.
* ``_resolve_legacy_placeholders`` -- the string arm, the ``services.``
  passthrough, the unknown-placeholder passthrough, and the whole-value-only
  matching rule.
* ``InfraConfig.legacy_placeholders`` -- the ``int``/``float``/``bool``
  ``str()`` arm and the ``None``/list/dict drop, which is the single place
  those now happen.
* The two functions' **different** placeholder rules, pinned against each
  other: ``_get_legacy_secrets`` substitutes ``${x}`` anywhere inside a value,
  ``_resolve_legacy_placeholders`` only replaces a value that is entirely one
  placeholder. The same ``infra_config`` therefore behaves differently
  depending on which reader sees it.
* The values ``_build_infra_config`` really produces -- lists, nested dicts and
  ``None`` -- against both readers, since a typed ``infra_config`` has to keep
  answering for those.
* ``get_secrets``'s routing into the legacy path, so the pin covers the way
  production actually reaches ``_get_legacy_secrets``.
* **Settled by 53f-4:** ``get_environment_variables`` used to guard its
  legacy-placeholder pass with ``if infra_config:``, testing a spread that
  always carried ``account_id`` and so was permanently true. 53f-4 deleted the
  guard rather than keep a branch whose false arm coverage proved unreachable.
  The behaviour those pins assert -- the pass runs, and ``${account_id}`` is
  offered here and only here -- is unchanged.

Nothing is stubbed: both functions are pure over dicts, and
``get_environment_variables`` is driven with an empty ``env_config`` so the
module system stays out of it. That is the outermost boundary available --
53d-2a's recorded rule -- and it means the pins survive any code motion inside
the package.
"""

import pytest

from deployer.deploy.context import DeploymentContext, InfraConfig
from deployer.deploy.deployer import _build_infra_config
from deployer.deploy.task_definition import (
    _get_legacy_secrets,
    _resolve_legacy_placeholders,
    get_environment_variables,
    get_secrets,
)

REGION = "us-west-2"
ACCOUNT_ID = "123456789012"
ENVIRONMENT = "staging"


def _ctx(**overrides) -> DeploymentContext:
    """A DeploymentContext with the module system switched off.

    An empty ``env_config`` keeps ModuleRegistry out of both functions under
    test, so what runs is the legacy path and nothing else.
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


def _legacy(secrets: dict, placeholders: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Call _get_legacy_secrets with the boilerplate arguments filled in."""
    return _get_legacy_secrets(
        {"secrets": secrets}, ENVIRONMENT, REGION, ACCOUNT_ID, placeholders or {}
    )


class TestGetLegacySecretsSsmArm:
    """_get_legacy_secrets() -- the `ssm:` prefix builds a parameter ARN."""

    def test_a_parameter_path_becomes_a_full_ssm_arn(self):
        assert _legacy({"SECRET_KEY": "ssm:/app/secret-key"}) == [  # pragma: allowlist secret
            {
                "name": "SECRET_KEY",
                "valueFrom": f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/app/secret-key",
            }
        ]

    def test_the_environment_placeholder_is_substituted_into_the_path(self):
        (secret,) = _legacy({"SECRET_KEY": "ssm:/app/${environment}/secret-key"})
        assert secret["valueFrom"].endswith(f"parameter/app/{ENVIRONMENT}/secret-key")

    def test_an_infra_config_placeholder_is_substituted_into_the_path(self):
        (secret,) = _legacy(
            {"DB_PASSWORD": "ssm:${param_prefix}/db-password"},  # pragma: allowlist secret
            placeholders={"param_prefix": "/myapp/prod"},
        )
        assert secret["valueFrom"].endswith("parameter/myapp/prod/db-password")

    def test_a_leading_slash_is_not_added(self):
        # Pinned, not endorsed: the ARN is built by string concatenation, so a
        # path without a leading slash produces `:parameterapp/key`, which SSM
        # will reject at register-task-definition time rather than here.
        (secret,) = _legacy({"SECRET_KEY": "ssm:app/key"})
        assert secret["valueFrom"].endswith(":parameterapp/key")

    def test_the_region_and_account_come_from_the_arguments_not_infra_config(self):
        (secret,) = _legacy(
            {"SECRET_KEY": "ssm:/k"},  # pragma: allowlist secret
            placeholders={"region": "eu-west-1", "account_id": "999"},
        )
        assert secret["valueFrom"].startswith(f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:")


class TestGetLegacySecretsSecretsManagerArm:
    """_get_legacy_secrets() -- the `secretsmanager:` prefix is sliced off."""

    def test_the_prefix_is_stripped_and_the_rest_passed_through(self):
        arn = "arn:aws:secretsmanager:us-west-2:123456789012:secret:db-pw-AbCdEf"
        assert _legacy({"DB_PASSWORD": f"secretsmanager:{arn}"}) == [
            {"name": "DB_PASSWORD", "valueFrom": arn}
        ]

    def test_an_infra_config_placeholder_is_substituted_before_the_prefix_check(self):
        arn = "arn:aws:secretsmanager:us-west-2:123456789012:secret:db-pw-AbCdEf"
        (secret,) = _legacy(
            {"DB_PASSWORD": "secretsmanager:${db_password_secret_arn}"},  # pragma: allowlist secret
            placeholders={"db_password_secret_arn": arn},
        )
        assert secret["valueFrom"] == arn

    def test_an_unresolved_placeholder_is_carried_into_the_output(self):
        # Pinned, not endorsed: an infra_config that has not got the key leaves
        # the literal `${...}` in the valueFrom, which ECS rejects at
        # register-task-definition time with no hint at where it came from.
        (secret,) = _legacy({"DB_PASSWORD": "secretsmanager:${db_password_secret_arn}"})
        assert secret["valueFrom"] == "${db_password_secret_arn}"


class TestGetLegacySecretsSkips:
    """_get_legacy_secrets() -- everything it declines to emit."""

    def test_no_secrets_section_gives_an_empty_list(self):
        assert _get_legacy_secrets({}, ENVIRONMENT, REGION, ACCOUNT_ID, {}) == []

    def test_the_names_key_is_skipped(self):
        # `names` belongs to the new declarative style; the legacy reader steps
        # over it rather than trying to treat the list as a path.
        assert _legacy({"names": ["SECRET_KEY"], "OTHER": "ssm:/k"}) == [
            {
                "name": "OTHER",
                "valueFrom": f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/k",
            }
        ]

    @pytest.mark.parametrize("value", [42, None, ["ssm:/k"], {"path": "ssm:/k"}, True])
    def test_a_non_string_value_is_skipped(self, value):
        assert _legacy({"SECRET_KEY": value}) == []

    def test_a_value_with_neither_prefix_is_dropped_silently(self):
        # Pinned, not endorsed: a typo'd prefix -- or a bare ARN -- makes the
        # secret vanish from the task definition with no warning at all. The
        # service then starts without it.
        assert (
            _legacy(
                {"SECRET_KEY": "arn:aws:ssm:us-west-2:1:parameter/k"}  # pragma: allowlist secret
            )
            == []
        )
        assert _legacy({"SECRET_KEY": "ssm/k"}) == []  # pragma: allowlist secret
        assert _legacy({"SECRET_KEY": ""}) == []

    def test_secrets_keep_their_declaration_order(self):
        secrets = _legacy({"A": "ssm:/a", "SKIPPED": "nope", "B": "secretsmanager:arn-b"})
        assert [s["name"] for s in secrets] == ["A", "B"]


class TestInfraConfigLegacyPlaceholders:
    """InfraConfig.legacy_placeholders() -- the one scalar filter.

    Until 53f-4 this filter was eight lines written twice, once inside
    ``_get_legacy_secrets`` and once inside ``_resolve_legacy_placeholders``,
    and 53f-1 pinned it twice to match. There is now one implementation, so
    there is one set of pins. Both readers still get an end-to-end pin over
    the real ``_build_infra_config`` output further down.
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

    def test_a_bool_field_is_stringified_python_style(self):
        # Pinned, not endorsed: bool is a subclass of int, so it passes the
        # numeric filter and renders as "True"/"False", not "true"/"false".
        assert _build_infra_config({"database": {"port": True}}).legacy_placeholders() == {
            "db_port": "True"
        }

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


class TestGetLegacySecretsPlaceholderTable:
    """_get_legacy_secrets() -- how it merges the infra placeholder table."""

    def test_an_infra_key_named_environment_overrides_the_built_in_one(self):
        # The built-in `environment` is seeded first and then overwritten by
        # the infra table. InfraConfig has no such field today.
        (secret,) = _legacy(
            {"K": "ssm:/${environment}/x"}, placeholders={"environment": "shadowed"}
        )
        assert secret["valueFrom"].endswith("parameter/shadowed/x")

    def test_a_placeholder_is_substituted_anywhere_in_the_value(self):
        (secret,) = _legacy({"K": "ssm:/${a}/mid/${a}"}, placeholders={"a": "X"})
        assert secret["valueFrom"].endswith("parameter/X/mid/X")

    def test_every_value_of_a_real_build_infra_config_is_survivable(self):
        # The real producer's output, driven straight through the reader: the
        # nested and list-valued entries must not raise.
        infra = _build_infra_config(
            {
                "infrastructure": {
                    "private_subnet_ids": ["subnet-1", "subnet-2"],
                    "rds_instance_id": "myapp-db",
                },
                "database": {
                    "port": 5432,
                    "password_secret_arn": "arn:secret",  # pragma: allowlist secret
                },
                "scheduler": {"enabled": True},
            }
        )
        secrets = _legacy(
            {
                "DB_PASSWORD": (
                    "secretsmanager:${db_password_secret_arn}"  # pragma: allowlist secret
                ),
                "PORT_PATH": "ssm:/db/${db_port}",
                "SUBNETS": "ssm:/net/${subnet_ids}",
            },
            placeholders=infra.legacy_placeholders(),
        )
        assert secrets[0]["valueFrom"] == "arn:secret"
        assert secrets[1]["valueFrom"].endswith("parameter/db/5432")
        assert secrets[2]["valueFrom"].endswith("parameter/net/${subnet_ids}")


class TestGetSecretsRoutesToTheLegacyPath:
    """get_secrets() -- the branch that reaches _get_legacy_secrets at all."""

    def test_no_modules_and_no_names_style_uses_the_legacy_reader(self):
        ctx = _ctx(
            config={"secrets": {"SECRET_KEY": "ssm:/app/secret-key"}},  # pragma: allowlist secret
            infra_config=InfraConfig(rds_instance_id="unused"),
        )
        assert get_secrets(ctx, "web") == [
            {
                "name": "SECRET_KEY",
                "valueFrom": f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}:parameter/app/secret-key",
            }
        ]

    def test_the_names_style_without_an_env_config_falls_through_to_legacy(self):
        # `uses_names_style and env_config` -- an empty env_config drops a
        # names-style config into the legacy reader, which skips `names` and
        # emits nothing.
        ctx = _ctx(config={"secrets": {"names": ["SECRET_KEY"]}})
        assert get_secrets(ctx, "web") == []

    def test_the_legacy_reader_sees_ctx_infra_config_unspread(self):
        # Unlike get_environment_variables, get_secrets passes
        # ctx.infra_config straight through -- account_id is *not* mixed in,
        # so `${account_id}` is not resolvable here.
        ctx = _ctx(config={"secrets": {"K": "ssm:/${account_id}"}})
        (secret,) = get_secrets(ctx, "web")
        assert secret["valueFrom"].endswith("parameter/${account_id}")


class TestResolveLegacyPlaceholders:
    """_resolve_legacy_placeholders() -- the whole-value substitution rule."""

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

    def test_an_unknown_placeholder_is_left_alone(self):
        resolved = _resolve_legacy_placeholders({"X": "${nope}"}, REGION, ENVIRONMENT, {})
        assert resolved == {"X": "${nope}"}

    def test_a_service_url_reference_is_passed_through_untouched(self):
        # Service URL references are resolved earlier by resolve_service_urls;
        # this branch exists so an *unresolved* one is not clobbered by an
        # infra_config key that happens to share its name.
        resolved = _resolve_legacy_placeholders(
            {"API": "${services.api.url}"},
            REGION,
            ENVIRONMENT,
            {"services.api.url": "https://shadowed"},
        )
        assert resolved == {"API": "${services.api.url}"}

    def test_only_a_whole_value_placeholder_is_substituted(self):
        # Pinned, not endorsed: this is the *opposite* rule to the one
        # _get_legacy_secrets uses on the same infra_config a few lines below.
        # An embedded placeholder survives into the container's environment.
        resolved = _resolve_legacy_placeholders(
            {"URL": "https://${host}/path"}, REGION, ENVIRONMENT, {"host": "example.com"}
        )
        assert resolved == {"URL": "https://${host}/path"}

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
                    "SUBNETS": "${subnet_ids}",
                    "SCHEDULER": "${scheduler}",
                }
            },
            infra_config=infra,
        )
        assert get_environment_variables(ctx) == {
            "DATABASE_URL": "postgres://db/app",
            "DB_PORT": "5432",
            # Pinned, not endorsed: the list- and dict-valued entries
            # _build_infra_config emits are not placeholder material, so these
            # two reach the container as literal `${...}` strings.
            "SUBNETS": "${subnet_ids}",
            "SCHEDULER": "${scheduler}",
        }
