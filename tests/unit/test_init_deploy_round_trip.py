"""End-to-end: what ``deployer init`` writes must survive being deployed.

Phase 53h-2a's bug was not caught by any individual guard. ``preflight``
confirmed every SSM parameter existed and passed. ``DeployConfig.
get_all_env_var_names`` counted the explicit ``[secrets]`` keys as provided, so
the audit passed. ``get_secrets`` then returned only the module secrets and
never read ``[secrets]`` at all, and the container started without them.

Every stage said yes; the composition said no. So this file tests the
composition: generate a deploy.toml the way ``bin/init.py`` does, write it to
disk, parse it back with the real parser, and drive the result through
``check_secrets_style``, ``check_modules``, ``get_all_env_var_names`` and
``get_secrets`` -- the four readers that disagreed.

Nothing is stubbed except SSM listing, which the checks under test do not
touch: ``check_secrets_style`` and ``check_modules`` are pure over config, and
``get_secrets`` resolves ARNs by string construction.
"""

import pytest

from deployer.config import parse_deploy_config
from deployer.deploy.context import DeploymentContext, InfraConfig
from deployer.deploy.preflight import PreflightError, check_modules, check_secrets_style
from deployer.deploy.task_definition import get_secrets
from deployer.init.deploy_toml import format_deploy_toml, generate_deploy_toml

REGION = "us-west-2"
ACCOUNT_ID = "123456789012"
APP = "myapp"

#: What every generated environment's config.toml already carries. All four
#: templates emit exactly this, which is why the `names` form needed no
#: environment-side change.
ENV_CONFIG = {
    "infrastructure": {
        "cluster_name": "myapp-staging",
        "ecr_prefix": "myapp",
        "execution_role_arn": "arn:aws:iam::123:role/exec",
        "task_role_arn": "arn:aws:iam::123:role/task",
        "security_group_id": "sg-123",
        "private_subnet_ids": ["subnet-1"],
    },
    "secrets": {"provider": "ssm", "path_prefix": "/myapp/staging"},
}

COMPOSE = """\
services:
  web:
    build:
      context: .
    ports:
      - "8000:8000"
    environment:
      - DJANGO_SETTINGS_MODULE=myapp.settings
      - SECRET_KEY=local-dev-only
      - DATACITE_PASSWORD=local-dev-only
      - DATABASE_URL=postgres://postgres@db/myapp
      - ALLOWED_HOSTS=localhost
  postgres:
    image: postgres:16
"""


@pytest.fixture
def generated_deploy_toml(tmp_path):
    """Run `deployer init deploy-toml` end to end and parse the result back.

    This is bin/init.py's cmd_deploy_toml with the argument parsing and the
    file-location logic removed -- generate, format, write, parse.
    """
    (tmp_path / "docker-compose.yml").write_text(COMPOSE)
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\nCMD gunicorn myapp.wsgi\n")

    config = generate_deploy_toml(
        compose_path=tmp_path / "docker-compose.yml", app_name=APP, compose_data=None
    )
    deploy_toml = tmp_path / "deploy.toml"
    deploy_toml.write_text(format_deploy_toml(config))
    return parse_deploy_config(deploy_toml)


def _ctx(raw_config: dict, env_config: dict) -> DeploymentContext:
    return DeploymentContext(
        ecs_client=None,
        cluster_name="myapp-staging",
        config=raw_config,
        service_config={},
        infra_config=InfraConfig(),
        app_name=APP,
        environment="staging",
        region=REGION,
        account_id=ACCOUNT_ID,
        env_config=env_config,
        dry_run=False,
    )


class TestGeneratedConfigRoundTrips:
    """The generated file survives every reader that used to disagree."""

    def test_the_only_warning_left_is_the_audit_key_name(self, generated_deploy_toml):
        # Pinned, not endorsed, and the second thing this round trip caught:
        # the generator writes `[audit] ignore` but AuditConfig's key is
        # `ignore_services`, so the list is warned about and then discarded.
        # Fixed in the following commit; pinned here as it stands.
        assert generated_deploy_toml.get_warnings() == ["Unknown key in [audit]: ignore"]

    def test_the_secrets_survive_the_toml_round_trip(self, generated_deploy_toml):
        raw = generated_deploy_toml.get_raw_dict()
        assert raw["secrets"] == {"names": ["DATACITE_PASSWORD", "SECRET_KEY"]}

    def test_preflight_accepts_it(self, generated_deploy_toml):
        check_secrets_style(generated_deploy_toml)
        check_modules(generated_deploy_toml, ENV_CONFIG)

    def test_the_audit_counts_the_declared_secrets_as_provided(self, generated_deploy_toml):
        names = generated_deploy_toml.get_all_env_var_names()
        assert {"SECRET_KEY", "DATACITE_PASSWORD"} <= names
        # ...and the non-secret variables it routed to [environment].
        assert {"DATABASE_URL", "ALLOWED_HOSTS", "DJANGO_SETTINGS_MODULE"} <= names

    def test_the_task_definition_actually_receives_them(self, generated_deploy_toml):
        """The stage that used to drop everything.

        ``get_all_env_var_names`` said these were provided; before 53h-2a
        ``get_secrets`` could disagree, and only the running container found
        out.
        """
        ctx = _ctx(generated_deploy_toml.get_raw_dict(), ENV_CONFIG)

        assert {s["name"]: s["valueFrom"] for s in get_secrets(ctx, "web")} == {
            "DATACITE_PASSWORD": (
                f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}"
                ":parameter/myapp/staging/datacite-password"  # pragma: allowlist secret
            ),
            "SECRET_KEY": (
                f"arn:aws:ssm:{REGION}:{ACCOUNT_ID}"
                ":parameter/myapp/staging/secret-key"  # pragma: allowlist secret
            ),
        }

    def test_every_declared_secret_is_delivered_none_dropped(self, generated_deploy_toml):
        declared = set(generated_deploy_toml.get_raw_dict()["secrets"]["names"])
        ctx = _ctx(generated_deploy_toml.get_raw_dict(), ENV_CONFIG)

        assert {s["name"] for s in get_secrets(ctx, "web")} == declared


class TestTheOriginalBugShape:
    """`[secrets]` plus a module section -- the combination that dropped them.

    ``deployer init`` cannot produce this any more, but a checked-in
    deploy.toml can still be written by hand, so the rejection is tested on the
    exact shape rather than on the generator's output.
    """

    @staticmethod
    def _hand_written(tmp_path, body: str):
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text(f'[application]\nname = "{APP}"\n{body}')
        return parse_deploy_config(deploy_toml)

    #: Explicit [secrets] *and* a module section. Both are typed fields, so
    #: both survive DeployConfig.get_raw_dict() into ctx.config -- this shape
    #: is fully reachable, unlike the [cdn] variant.
    BUG_SHAPE = (
        "[database]\n"
        'type = "postgresql"\n'
        "[secrets]\n"
        'SECRET_KEY = "ssm:/myapp/staging/secret-key"\n'  # pragma: allowlist secret
    )

    def test_preflight_now_refuses_it(self, tmp_path):
        with pytest.raises(PreflightError, match="explicit-path form"):
            check_secrets_style(self._hand_written(tmp_path, self.BUG_SHAPE))

    def test_the_message_names_the_secret_that_would_have_vanished(self, tmp_path):
        with pytest.raises(PreflightError, match="SECRET_KEY"):
            check_secrets_style(self._hand_written(tmp_path, self.BUG_SHAPE))

    def test_the_same_secrets_declared_as_names_are_delivered_alongside_the_database(
        self, tmp_path
    ):
        """The migration the message tells the operator to make, carried out.

        The point of the fix is not only that the bad shape is rejected but
        that the good one works: the database's credentials and the named
        application secret arrive together, which is exactly what the dropped
        route could not do.
        """
        config = self._hand_written(
            tmp_path,
            '[database]\ntype = "postgresql"\n[secrets]\nnames = ["SECRET_KEY"]\n',
        )
        check_secrets_style(config)

        env_config = {
            **ENV_CONFIG,
            "database": {
                "credentials": "ssm",
                "host": "db.example.com",
                "port": 5432,
                "name": "myapp",
                "app_username_param": "/myapp/staging/db-app-username",
                "app_password_param": (
                    "/myapp/staging/db-app-password"  # pragma: allowlist secret
                ),
            },
        }
        ctx = _ctx(config.get_raw_dict(), env_config)

        assert {s["name"] for s in get_secrets(ctx, "web")} == {
            "SECRET_KEY",
            "DB_USERNAME",
            "DB_PASSWORD",
        }
