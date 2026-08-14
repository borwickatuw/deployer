"""Characterization pins for deploy/task_definition.py's two infra_config readers.

These are characterization pins, not endorsements. They record what
``_get_legacy_secrets()`` and ``_resolve_legacy_placeholders()`` do **today**;
where the behaviour looks wrong it is pinned anyway and called out in a
comment on the test. Nothing here is a fix.

Phase 53f converts ``infra_config`` from a dict to a typed object. These two
functions are the file's only ``infra_config.items()`` consumers -- the
conversion has to rewrite both -- and ``_get_legacy_secrets`` had **no test at
all**: its entire body was uncovered. ``_resolve_legacy_placeholders`` had one
direct test in ``test_deploy.py`` that passed two string-valued keys and no
others, so its numeric arm and its ``services.`` skip had never run either.

What is pinned here:

* ``_get_legacy_secrets`` -- both output arms (``ssm:`` -> a constructed SSM
  parameter ARN, ``secretsmanager:`` -> the ARN with the prefix sliced off),
  the ``${...}`` substring substitution it does *before* looking at the
  prefix, the ``names`` skip, the non-string skip, and the fact that a value
  matching neither prefix is **silently dropped**.
* ``_resolve_legacy_placeholders`` -- the string arm, the ``int``/``float``
  ``str()`` arm, the ``services.`` passthrough, the unknown-placeholder
  passthrough, and the whole-value-only matching rule.
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
* **A latent item 53f-4 must decide, pinned as-is:** ``get_environment_variables``
  guards its legacy-placeholder pass with ``if infra_config:``, but the
  ``infra_config`` it tests is the ``{**ctx.infra_config, "account_id": ...}``
  spread built four lines earlier, which is never empty. The guard is
  permanently true and coverage confirms its false arm never fires. The guard
  is pinned here, not deleted.

Nothing is stubbed: both functions are pure over dicts, and
``get_environment_variables`` is driven with an empty ``env_config`` so the
module system stays out of it. That is the outermost boundary available --
53d-2a's recorded rule -- and it means the pins survive any code motion inside
the package.
"""

import pytest

from deployer.deploy.context import DeploymentContext
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
        "infra_config": {},
        "app_name": "testapp",
        "environment": ENVIRONMENT,
        "region": REGION,
        "account_id": ACCOUNT_ID,
        "env_config": {},
        "dry_run": False,
    }
    defaults.update(overrides)
    return DeploymentContext(**defaults)


def _legacy(secrets: dict, infra: dict | None = None) -> list[dict[str, str]]:
    """Call _get_legacy_secrets with the boilerplate arguments filled in."""
    return _get_legacy_secrets({"secrets": secrets}, ENVIRONMENT, REGION, ACCOUNT_ID, infra or {})


class TestGetLegacySecretsSsmArm:
    """_get_legacy_secrets() -- the `ssm:` prefix builds a parameter ARN."""

    def test_a_parameter_path_becomes_a_full_ssm_arn(self):
        assert _legacy({"SECRET_KEY": "ssm:/app/secret-key"}) == [
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
            {"DB_PASSWORD": "ssm:${param_prefix}/db-password"},
            infra={"param_prefix": "/myapp/prod"},
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
            {"SECRET_KEY": "ssm:/k"}, infra={"region": "eu-west-1", "account_id": "999"}
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
            {"DB_PASSWORD": "secretsmanager:${db_password_secret_arn}"},
            infra={"db_password_secret_arn": arn},
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
        assert _legacy({"SECRET_KEY": "arn:aws:ssm:us-west-2:1:parameter/k"}) == []
        assert _legacy({"SECRET_KEY": "ssm/k"}) == []
        assert _legacy({"SECRET_KEY": ""}) == []

    def test_secrets_keep_their_declaration_order(self):
        secrets = _legacy({"A": "ssm:/a", "SKIPPED": "nope", "B": "secretsmanager:arn-b"})
        assert [s["name"] for s in secrets] == ["A", "B"]


class TestGetLegacySecretsPlaceholderTable:
    """_get_legacy_secrets() -- how it builds placeholders from infra_config."""

    def test_a_numeric_infra_value_is_stringified(self):
        (secret,) = _legacy({"K": "ssm:/db/${db_port}"}, infra={"db_port": 5432})
        assert secret["valueFrom"].endswith("parameter/db/5432")

    def test_a_float_infra_value_is_stringified(self):
        (secret,) = _legacy({"K": "ssm:/v/${version}"}, infra={"version": 1.5})
        assert secret["valueFrom"].endswith("parameter/v/1.5")

    def test_a_bool_infra_value_is_stringified_python_style(self):
        # Pinned, not endorsed: bool is a subclass of int, so it takes the
        # numeric arm and renders as "True"/"False", not "true"/"false".
        (secret,) = _legacy({"K": "ssm:/f/${enabled}"}, infra={"enabled": True})
        assert secret["valueFrom"].endswith("parameter/f/True")

    @pytest.mark.parametrize(
        "value", [None, ["subnet-1"], {"enabled": False}], ids=["none", "list", "dict"]
    )
    def test_a_non_scalar_infra_value_makes_no_placeholder(self, value):
        # _build_infra_config really produces all three shapes -- `database_url`
        # can be None, `subnet_ids` is a list, `scheduler` is a dict -- so an
        # unresolved `${...}` reaches ECS verbatim.
        (secret,) = _legacy({"K": "ssm:/${thing}"}, infra={"thing": value})
        assert secret["valueFrom"].endswith("parameter/${thing}")

    def test_an_infra_key_named_environment_overrides_the_built_in_one(self):
        # The built-in `environment` is seeded first and then overwritten by
        # the infra_config loop. _build_infra_config emits no such key today.
        (secret,) = _legacy({"K": "ssm:/${environment}/x"}, infra={"environment": "shadowed"})
        assert secret["valueFrom"].endswith("parameter/shadowed/x")

    def test_a_placeholder_is_substituted_anywhere_in_the_value(self):
        (secret,) = _legacy({"K": "ssm:/${a}/mid/${a}"}, infra={"a": "X"})
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
                "database": {"port": 5432, "password_secret_arn": "arn:secret"},
                "scheduler": {"enabled": True},
            }
        )
        secrets = _legacy(
            {
                "DB_PASSWORD": "secretsmanager:${db_password_secret_arn}",
                "PORT_PATH": "ssm:/db/${db_port}",
                "SUBNETS": "ssm:/net/${subnet_ids}",
            },
            infra=infra,
        )
        assert secrets[0]["valueFrom"] == "arn:secret"
        assert secrets[1]["valueFrom"].endswith("parameter/db/5432")
        assert secrets[2]["valueFrom"].endswith("parameter/net/${subnet_ids}")


class TestGetSecretsRoutesToTheLegacyPath:
    """get_secrets() -- the branch that reaches _get_legacy_secrets at all."""

    def test_no_modules_and_no_names_style_uses_the_legacy_reader(self):
        ctx = _ctx(
            config={"secrets": {"SECRET_KEY": "ssm:/app/secret-key"}},
            infra_config={"unused": "x"},
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

    def test_an_int_infra_value_is_stringified(self):
        resolved = _resolve_legacy_placeholders(
            {"DB_PORT": "${db_port}"}, REGION, ENVIRONMENT, {"db_port": 5432}
        )
        assert resolved == {"DB_PORT": "5432"}

    def test_a_float_infra_value_is_stringified(self):
        resolved = _resolve_legacy_placeholders(
            {"RATIO": "${ratio}"}, REGION, ENVIRONMENT, {"ratio": 0.5}
        )
        assert resolved == {"RATIO": "0.5"}

    def test_a_bool_infra_value_is_stringified_python_style(self):
        # Pinned, not endorsed: same bool-is-an-int arm as _get_legacy_secrets.
        resolved = _resolve_legacy_placeholders(
            {"ON": "${enabled}"}, REGION, ENVIRONMENT, {"enabled": False}
        )
        assert resolved == {"ON": "False"}

    @pytest.mark.parametrize(
        "value", [None, ["subnet-1"], {"enabled": False}], ids=["none", "list", "dict"]
    )
    def test_a_non_scalar_infra_value_makes_no_placeholder(self, value):
        resolved = _resolve_legacy_placeholders(
            {"X": "${thing}"}, REGION, ENVIRONMENT, {"thing": value}
        )
        assert resolved == {"X": "${thing}"}

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


class TestGetEnvironmentVariablesInfraGuard:
    """The `if infra_config:` guard at the end of get_environment_variables().

    Pinned as-is for 53f-4 to decide: the guard tests the spread built four
    lines earlier, which always carries `account_id`, so it is permanently
    true. Coverage confirms the false arm never fires.
    """

    def test_an_empty_ctx_infra_config_still_runs_the_legacy_pass(self):
        # The guard would be false if it tested ctx.infra_config; it tests the
        # spread instead, so the pass runs and ${aws_region} resolves.
        ctx = _ctx(config={"environment": {"REGION": "${aws_region}"}}, infra_config={})
        assert get_environment_variables(ctx) == {"REGION": REGION}

    def test_account_id_is_injected_as_a_placeholder_by_the_spread(self):
        # This is the value that makes the guard permanently true, and it is
        # only reachable through the spread -- get_secrets does not do it.
        ctx = _ctx(config={"environment": {"ACCOUNT": "${account_id}"}}, infra_config={})
        assert get_environment_variables(ctx) == {"ACCOUNT": ACCOUNT_ID}

    def test_an_explicit_account_id_in_infra_config_loses_to_the_spread(self):
        ctx = _ctx(
            config={"environment": {"ACCOUNT": "${account_id}"}},
            infra_config={"account_id": "000000000000"},
        )
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
