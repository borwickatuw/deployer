"""Characterization pins for the SSM-secrets read path.

These are pins, not endorsements. They record what the secrets path does
**today**, including a live user-facing crash, so that the fix commit shows the
behaviour change in its own diff rather than hiding it.

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

   * the legacy inline form, ``SECRET_KEY = "ssm:/app/${environment}/..."``,
     which works today; and
   * the **module-style** form ``[secrets] names = [...]`` that
     ``docs/resources/secrets.md`` recommends, which today raises
     ``AttributeError: 'NoneType' object has no attribute 'get'``.

   The crash is pinned **as it stands**. ``get_secrets_from_deploy_toml``
   declares ``env_config: dict | None = None`` and forwards it to
   ``get_secrets_from_config``, whose signature declares a non-optional
   ``dict`` and which calls ``env_config.get("secrets", {})`` on the
   module-style path. Its only production caller, ``bin/ssm-secrets.py``,
   passes two arguments.

2. **``bin/ssm-secrets.py check`` end-to-end on both styles**, so the
   user-visible message is pinned too: on the recommended form the command
   prints ``Error parsing deploy.toml: 'NoneType' object has no attribute
   'get'`` to stderr and exits 1 -- a programming error reported as an operator
   config-parse failure.

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
DB_PASSWORD = "ssm:/myapp/${environment}/db-password"
"""

NO_SECRETS_TOML = """\
[application]
name = "myapp"
source = "."
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

    def test_module_style_form_crashes_without_an_env_config(self, tmp_path):
        """PINNED AS-IS, NOT ENDORSED -- this is the live bug.

        The documented ``[secrets] names = [...]`` form raises through the only
        way production calls this function (two positional arguments), because
        ``env_config`` defaults to ``None`` and is forwarded to a callee that
        declares it non-optional.
        """
        path = write_deploy_toml(tmp_path, MODULE_STYLE_TOML)

        with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'get'"):
            get_secrets_from_deploy_toml(path, "staging")

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

    def test_documented_module_style_form_fails_the_whole_command(self, tmp_path, capsys):
        """PINNED AS-IS, NOT ENDORSED -- the user-visible face of the bug.

        Every app using the recommended ``[secrets] names = [...]`` format gets
        a config-parse error message for what is a signature mismatch inside
        deployer, and the command exits 1 without checking anything.
        """
        path = write_deploy_toml(tmp_path, MODULE_STYLE_TOML)
        put_ssm("/myapp/staging/secret-key")

        assert ssm_cli.cmd_check("myapp-staging", str(path)) == 1
        assert (
            "Error parsing deploy.toml: 'NoneType' object has no attribute 'get'"
            in capsys.readouterr().err
        )


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
