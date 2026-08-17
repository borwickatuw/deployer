"""Characterization pins for the SSM-secrets read path.

These are pins, not endorsements. They record what the secrets path does
**today**. Four of them were written one commit earlier to pin a live
user-facing crash exactly as it stood, and the commit that fixed it updated
them deliberately -- each carries an ``UPDATED PIN`` docstring saying what it
used to assert. A third commit added the module-style guard and updated three
more the same way: fixing the crash had made a worse path reachable, in which
``check`` reported ``Required: 0`` and advised deleting live secrets.

Why this file exists at all: ``core/ssm_secrets.py`` sat at 77%, with
``get_secrets_from_deploy_toml``'s body (lines 73-74) and the whole of
``check_secrets_exist`` (149-174) at **0%**. ``deploy/preflight.py``'s
``check_ssm_secrets`` (158-176), the only caller of ``check_secrets_exist``, was
also entirely uncovered. ``tests/unit/test_ssm_secrets_cli.py`` exercises
``cmd_check`` thoroughly but monkeypatches ``get_secrets_from_deploy_toml``
itself, so the CLI's own tests could never reach the defect -- which is why the
end-to-end CLI pins live here, next to the crash, rather than there.

What is pinned:

1. **``get_secrets_from_deploy_toml`` on both documented config styles**, from a
   real ``deploy.toml`` on disk:

   * the legacy inline form,
     ``SECRET_KEY = "ssm:/app/${environment}/..."``,  # pragma: allowlist secret
     which works today; and
   * the **module-style** form ``[secrets] names = [...]`` that
     ``docs/resources/secrets.md`` recommends, which used to raise
     ``AttributeError: 'NoneType' object has no attribute 'get'``.

   The crash: ``get_secrets_from_deploy_toml`` declares
   ``env_config: dict | None = None`` and forwarded it to
   ``get_secrets_from_config``, whose signature declared a non-optional
   ``dict`` and which calls ``env_config.get("secrets", {})`` on the
   module-style path. Its only production caller, ``bin/ssm-secrets.py``,
   passes two arguments. The signatures now agree.

2. **``bin/ssm-secrets.py check`` end-to-end on both styles**, so the
   user-visible behaviour is pinned too. The recommended form used to print
   ``Error parsing deploy.toml: 'NoneType' object has no attribute 'get'`` to
   stderr and exit 1. Fixing that crash exposed something worse: ``cmd_check``
   never loads the environment's ``config.toml``, so module-style secrets
   resolved to nothing, ``Required: 0`` was printed, and every live SSM
   parameter under the prefix was listed EXTRA with ``delete`` advice. The
   guard closes it without making ``check`` require a deployed environment:
   ``get_secrets_from_config`` refuses to answer at all when module-style
   names are declared and no ``env_config`` was loaded, and ``cmd_check``
   turns that into an error naming what it could not load. Nothing is
   classified and no ``delete`` line is ever printed on that path.

   The bare ``except Exception`` that turned the AttributeError into a
   config-parse message is narrowed to ``(OSError, ValueError)`` -- exactly what
   ``parse_deploy_config`` documents -- and that is pinned too.

3. **``check_secrets_exist``** -- present/missing split, both config styles, the
   no-secrets short-circuit, and the ``RuntimeError`` on an SSM listing failure.

4. **``deploy/preflight.check_ssm_secrets``** -- its success, missing (
   ``PreflightError`` with remediation commands), no-secrets and drift-warning
   arms.

Stubbing is at the outermost boundary, per the repo's recorded rule: SSM is
**moto**, reached through the real ``boto3`` client inside
``deployer.aws.ssm``, and ``deploy.toml`` is a real file parsed by the real
``parse_deploy_config``. The one exception is the SSM-listing-error pin, where
``list_parameters`` is replaced directly -- moto has no way to make
``describe_parameters`` fail with the ``ClientError`` that arm needs.
"""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import boto3
import pytest

from deployer.config import parse_deploy_config
from deployer.core import ssm_secrets
from deployer.core.ssm_secrets import check_secrets_exist, get_secrets_from_deploy_toml
from deployer.deploy.context import EnvironmentTarget
from deployer.deploy.preflight import PreflightError, check_ssm_secrets
from deployer.utils import EnvironmentConfigError

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("ssm_secrets_cli_e2e", bin_dir / "ssm-secrets.py")
ssm_cli = module_from_spec(_spec)
_spec.loader.exec_module(ssm_cli)


MODULE_STYLE_TOML = """\
[application]
name = "myapp"
source = "."

[secrets]
names = ["SECRET_KEY", "SIGNED_URL_SECRET"]
"""

LEGACY_STYLE_TOML = """\
[application]
name = "myapp"
source = "."

[secrets]
DB_PASSWORD = "ssm:/myapp/${environment}/db-password"  # pragma: allowlist secret
"""

NO_SECRETS_TOML = """\
[application]
name = "myapp"
source = "."
"""

EMPTY_NAMES_TOML = """\
[application]
name = "myapp"
source = "."

[secrets]
names = []
"""

ENV_CONFIG = {"secrets": {"provider": "ssm", "path_prefix": "/myapp/staging"}}


def write_deploy_toml(tmp_path: Path, body: str) -> Path:
    """Write a real deploy.toml so parse_deploy_config runs for real."""
    path = tmp_path / "deploy.toml"
    path.write_text(body)
    return path


def put_ssm(*names: str) -> None:
    """Create SecureString parameters in the moto-backed SSM backend."""
    client = boto3.client("ssm", region_name="us-west-2")
    for name in names:
        client.put_parameter(Name=name, Value="value", Type="SecureString")


class TestGetSecretsFromDeployToml:
    """Pins the deploy.toml -> {env var: SSM path} mapping on both styles."""

    def test_legacy_inline_form_resolves_the_environment_placeholder(self, tmp_path):
        path = write_deploy_toml(tmp_path, LEGACY_STYLE_TOML)

        assert get_secrets_from_deploy_toml(path, "staging") == {
            "DB_PASSWORD": "/myapp/staging/db-password"
        }

    def test_no_secrets_section_yields_no_secrets(self, tmp_path):
        path = write_deploy_toml(tmp_path, NO_SECRETS_TOML)

        assert get_secrets_from_deploy_toml(path, "staging") == {}

    def test_module_style_form_refuses_to_answer_without_an_env_config(self, tmp_path):
        """UPDATED PIN -- twice now.

        Originally it asserted the ``AttributeError: 'NoneType' object has no
        attribute 'get'`` crash. The crash fix changed it to assert ``== {}``:
        module-style secrets resolving to nothing when called the way
        production calls it (two positional arguments). That silent empty is
        what let ``check`` conclude "nothing required, everything extra", so it
        is now an ``EnvironmentConfigError``: without the environment's
        ``path_prefix`` the required set is unknown, not empty.

        The loaded-but-silent env_config keeps resolving to nothing -- see
        ``test_core.py::test_module_format_without_path_prefix_returns_empty``.
        """
        path = write_deploy_toml(tmp_path, MODULE_STYLE_TOML)

        with pytest.raises(EnvironmentConfigError, match=r"names = \[\.\.\.\]"):
            get_secrets_from_deploy_toml(path, "staging")

    def test_an_empty_names_list_needs_no_env_config(self, tmp_path):
        """The guard is narrow: with no names declared, nothing is unknown.

        ``names = []`` requires no secrets whatever the ``path_prefix`` is, so
        the answer is knowable and EXTRA classification stays honest.
        """
        path = write_deploy_toml(tmp_path, EMPTY_NAMES_TOML)

        assert get_secrets_from_deploy_toml(path, "staging") == {}

    def test_module_style_form_works_when_an_env_config_is_passed(self, tmp_path):
        path = write_deploy_toml(tmp_path, MODULE_STYLE_TOML)

        assert get_secrets_from_deploy_toml(path, "staging", ENV_CONFIG) == {
            "SECRET_KEY": "/myapp/staging/secret-key",
            "SIGNED_URL_SECRET": "/myapp/staging/signed-url-secret",
        }


class TestCheckCommandEndToEnd:
    """Pins `ssm-secrets.py check` with a real deploy.toml and a moto SSM."""

    @pytest.fixture(autouse=True)
    def _no_disk_resolution(self, monkeypatch, mocked_aws):
        """Skip the link-file lookup; every test passes an explicit path."""
        monkeypatch.setattr(
            ssm_cli,
            "resolve_deploy_toml_or_exit",
            lambda _environment, deploy_toml, **_kw: Path(deploy_toml),
        )

    def test_legacy_form_reports_present_and_missing(self, tmp_path, capsys):
        path = write_deploy_toml(tmp_path, LEGACY_STYLE_TOML)
        put_ssm("/myapp/staging/db-password")

        assert ssm_cli.cmd_check("myapp-staging", str(path)) == 0
        out = capsys.readouterr().out
        assert "DB_PASSWORD" in out
        assert "Present: 1, Missing: 0, Extra: 0" in out

    def test_legacy_form_missing_secret_exits_1_with_put_commands(self, tmp_path, capsys):
        path = write_deploy_toml(tmp_path, LEGACY_STYLE_TOML)

        assert ssm_cli.cmd_check("myapp-staging", str(path)) == 1
        out = capsys.readouterr().out
        assert "MISSING" in out
        assert "  uv run python bin/ssm-secrets.py put myapp-staging db-password" in out

    def test_documented_module_style_form_fails_with_actionable_advice(self, tmp_path, capsys):
        """UPDATED PIN -- twice now.

        Originally it asserted the crash: ``Error parsing deploy.toml:
        'NoneType' object has no attribute 'get'`` on stderr, exit 1, for every
        app using the recommended ``[secrets] names = [...]`` format. The crash
        fix changed it to assert exit 0 plus "No SSM secrets defined in
        deploy.toml and none found in SSM." -- a confident answer produced
        without ever loading the file that defines "required".

        Now: exit 1 with the guard's message, which names the config.toml it
        could not load and the two commands that do work. The crash message
        stays gone.
        """
        path = write_deploy_toml(tmp_path, MODULE_STYLE_TOML)

        assert ssm_cli.cmd_check("myapp-staging", str(path)) == 1
        captured = capsys.readouterr()
        assert "Error parsing deploy.toml" not in captured.err
        assert "No SSM secrets defined in deploy.toml" not in captured.out
        assert "The set of required secrets is unknown, not empty." in captured.err
        assert "it never loads myapp-staging's config.toml" in captured.err
        assert "  uv run python bin/deploy.py deploy myapp-staging --dry-run" in captured.err
        assert "  uv run python bin/ssm-secrets.py list myapp-staging" in captured.err

    def test_module_style_secrets_in_ssm_are_no_longer_reported_as_extra(self, tmp_path, capsys):
        """UPDATED PIN -- this pinned the delete-advice behaviour as-is.

        It asserted exit 1 with ``Required: 0 secret(s)`` and
        ``uv run python bin/ssm-secrets.py delete myapp-staging secret-key``
        -- i.e. the tool telling an operator to delete a live, in-use secret,
        because ``cmd_check`` never loads the ``config.toml`` that defines
        which secrets are required. It was pinned rather than fixed on the
        grounds that fixing it meant deciding whether ``check`` may require a
        deployed environment.

        It does not: the guard refuses to compute a required set it cannot
        know, which is neither a new demand on the operator nor a wrong answer.
        The same live secret now produces no ``Required:`` line, no EXTRA row
        and no ``delete`` command.
        """
        path = write_deploy_toml(tmp_path, MODULE_STYLE_TOML)
        put_ssm("/myapp/staging/secret-key")

        assert ssm_cli.cmd_check("myapp-staging", str(path)) == 1
        captured = capsys.readouterr()
        assert "Required:" not in captured.out
        assert "EXTRA" not in captured.out
        assert "ssm-secrets.py delete" not in captured.out + captured.err
        assert "The set of required secrets is unknown, not empty." in captured.err

    def test_the_guard_fires_before_ssm_is_ever_listed(self, tmp_path, monkeypatch, capsys):
        """Nothing is classified, so SSM is not even consulted.

        The old path listed the prefix first and then compared it against an
        empty required set; the guard has to run before that, or a listing
        failure could mask it.
        """
        path = write_deploy_toml(tmp_path, MODULE_STYLE_TOML)

        def unexpected(_prefix):
            raise AssertionError("list_parameters must not be called")

        monkeypatch.setattr(ssm_cli.ssm, "list_parameters", unexpected)

        assert ssm_cli.cmd_check("myapp-staging", str(path)) == 1
        assert "The set of required secrets is unknown, not empty." in capsys.readouterr().err

    def test_legacy_form_still_reports_extra_secrets_with_delete_advice(self, tmp_path, capsys):
        """The guard leaves legacy inline apps completely alone.

        Legacy ``ssm:/path`` secrets never read ``env_config`` -- which is why
        they survived the original crash -- so ``check`` still resolves them,
        still finds unreferenced parameters, and still advises deleting those.
        """
        path = write_deploy_toml(tmp_path, LEGACY_STYLE_TOML)
        put_ssm("/myapp/staging/db-password", "/myapp/staging/retired-secret")

        assert ssm_cli.cmd_check("myapp-staging", str(path)) == 1
        out = capsys.readouterr().out
        assert "Required: 1 secret(s)" in out
        assert "Present: 1, Missing: 0, Extra: 1" in out
        assert "  uv run python bin/ssm-secrets.py delete myapp-staging retired-secret" in out

    def test_a_deployer_bug_is_no_longer_reported_as_a_parse_error(self, tmp_path, monkeypatch):
        """The narrowed ``except`` lets programming errors surface as tracebacks.

        ``parse_deploy_config`` documents FileNotFoundError, TOMLDecodeError and
        ValueError; ``cmd_check`` used to catch bare ``Exception`` and print all
        of them -- including the AttributeError above -- as
        ``Error parsing deploy.toml``.
        """
        path = write_deploy_toml(tmp_path, MODULE_STYLE_TOML)

        def bug(*_args, **_kwargs):
            raise AttributeError("'NoneType' object has no attribute 'get'")

        monkeypatch.setattr(ssm_cli, "get_secrets_from_deploy_toml", bug)

        with pytest.raises(AttributeError):
            ssm_cli.cmd_check("myapp-staging", str(path))


class TestCheckSecretsExist:
    """Pins the present/missing split used by preflight."""

    def test_module_style_splits_present_from_missing(self, mocked_aws, tmp_path):
        config = parse_deploy_config(write_deploy_toml(tmp_path, MODULE_STYLE_TOML)).get_raw_dict()
        put_ssm("/myapp/staging/secret-key")

        missing, present = check_secrets_exist(config, "staging", "myapp-staging", ENV_CONFIG)

        assert present == [("SECRET_KEY", "/myapp/staging/secret-key")]
        assert missing == [("SIGNED_URL_SECRET", "/myapp/staging/signed-url-secret")]

    def test_legacy_style_is_checked_too(self, mocked_aws, tmp_path):
        config = parse_deploy_config(write_deploy_toml(tmp_path, LEGACY_STYLE_TOML)).get_raw_dict()
        put_ssm("/myapp/staging/db-password")

        missing, present = check_secrets_exist(config, "staging", "myapp-staging", ENV_CONFIG)

        assert present == [("DB_PASSWORD", "/myapp/staging/db-password")]
        assert missing == []

    def test_module_style_without_an_env_config_refuses_before_touching_ssm(
        self, monkeypatch, tmp_path
    ):
        """The guard lives under preflight's helper too, not only under the CLI.

        ``check_secrets_exist`` types ``env_config`` as a plain ``dict`` and
        production always loads one, but a caller that lost it must not be told
        that nothing is required.
        """
        config = parse_deploy_config(write_deploy_toml(tmp_path, MODULE_STYLE_TOML)).get_raw_dict()

        def unexpected(_prefix):
            raise AssertionError("list_parameters must not be called")

        monkeypatch.setattr(ssm_secrets.ssm, "list_parameters", unexpected)

        with pytest.raises(EnvironmentConfigError, match="unknown, not empty"):
            check_secrets_exist(config, "staging", "myapp-staging", None)

    def test_no_required_secrets_short_circuits_before_touching_ssm(self, monkeypatch):
        def unexpected(_prefix):
            raise AssertionError("list_parameters must not be called")

        monkeypatch.setattr(ssm_secrets.ssm, "list_parameters", unexpected)

        assert check_secrets_exist({}, "staging", "myapp-staging", ENV_CONFIG) == ([], [])

    def test_ssm_listing_failure_raises_runtimeerror(self, monkeypatch, tmp_path):
        config = parse_deploy_config(write_deploy_toml(tmp_path, MODULE_STYLE_TOML)).get_raw_dict()
        monkeypatch.setattr(
            ssm_secrets.ssm, "list_parameters", lambda _prefix: ([], "AccessDeniedException")
        )

        with pytest.raises(RuntimeError, match="Failed to list SSM parameters: AccessDenied"):
            check_secrets_exist(config, "staging", "myapp-staging", ENV_CONFIG)


class TestPreflightCheckSsmSecrets:
    """Pins preflight's secrets gate -- the only caller of check_secrets_exist."""

    def _target(self, env_config=None):
        return EnvironmentTarget(
            "myapp-staging", "staging", ENV_CONFIG if env_config is None else env_config
        )

    def test_all_present_passes_and_reports_the_count(self, mocked_aws, tmp_path, capsys):
        deploy_config = parse_deploy_config(write_deploy_toml(tmp_path, MODULE_STYLE_TOML))
        put_ssm("/myapp/staging/secret-key", "/myapp/staging/signed-url-secret")

        check_ssm_secrets(deploy_config, self._target())

        assert "All 2 secret(s) present" in capsys.readouterr().out

    def test_missing_secret_raises_with_remediation_commands(self, mocked_aws, tmp_path):
        deploy_config = parse_deploy_config(write_deploy_toml(tmp_path, MODULE_STYLE_TOML))
        put_ssm("/myapp/staging/secret-key")

        with pytest.raises(PreflightError) as exc_info:
            check_ssm_secrets(deploy_config, self._target())

        message = str(exc_info.value)
        assert "Missing 1 required SSM secret(s)" in message
        assert "uv run python bin/ssm-secrets.py put myapp-staging signed-url-secret" in message

    def test_no_secrets_declared_says_so(self, mocked_aws, tmp_path, capsys):
        deploy_config = parse_deploy_config(write_deploy_toml(tmp_path, NO_SECRETS_TOML))

        check_ssm_secrets(deploy_config, self._target())

        assert "No secrets defined in deploy.toml" in capsys.readouterr().out

    def test_unreferenced_ssm_parameters_warn_without_raising(self, mocked_aws, tmp_path, capsys):
        deploy_config = parse_deploy_config(write_deploy_toml(tmp_path, MODULE_STYLE_TOML))
        put_ssm(
            "/myapp/staging/secret-key",
            "/myapp/staging/signed-url-secret",
            "/myapp/staging/retired-secret",
        )

        check_ssm_secrets(deploy_config, self._target())

        out = capsys.readouterr().out
        assert "1 SSM secret(s) not referenced in deploy.toml" in out
        assert "/myapp/staging/retired-secret" in out
