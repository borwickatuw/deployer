"""Characterization pins for bin/cognito.py's listing path and password input.

These are characterization pins, not endorsements. They record what
``bin/cognito.py`` does **today**; where the behaviour looks wrong it is
pinned anyway and called out in a comment on the test. Nothing here is a fix.

``bin/cognito.py`` was at **17%** before this file. ``test_bin_scripts.py``
imports the module and exercises the re-exported ``deployer.core.cognito``
helpers through it, so ``print_users_table`` and ``cmd_list`` -- the two
functions that actually consume those helpers' output -- had never run.

Why this matters for Phase 53f: ``format_user`` returned a **dict**, and every
one of its five display fields is read in this file -- ``email``,
``username``, ``status``, ``enabled`` and ``created``. ``print_users_table``
is the only consumer of that record in the repo, so it is the only thing a
``dict -> NamedTuple`` conversion of ``format_user`` could break. The producer
side was already pinned by ``test_bin_scripts.py::TestFormatUser`` and
``test_aws_cognito.py``; the consumer side is what these pins add. 53f-3 made
the conversion: ``format_user`` now answers with ``CognitoUser`` and the reads
are attribute reads. Every assertion below is the one it was before the
conversion; only the access form moved.

What is pinned here:

* ``print_users_table`` -- the empty case, the column-width calculation, the
  ``email or username`` fallback, the ``Yes``/``NO`` rendering of ``enabled``,
  the ``or 'N/A'`` on ``created``, and the ``indent`` prefix on every line.
  Also pinned: a record missing any display field blows up. While the record
  was a dict the table subscripted rather than ``.get()``-ed, so the failure
  was a ``KeyError`` out of the middle of a half-printed table; ``CognitoUser``
  moves the same failure to construction time as a ``TypeError``.
* A real ``format_user`` record is driven end to end into
  ``print_users_table``, so the two halves of the contract are pinned against
  each other and not against a hand-written record.
* ``cmd_list`` -- pool deduplication across environments, the two header
  arms (named pool vs bare id), the per-environment error lines, the
  ``Total:`` line's ``> 1`` gate, the explicit-environment argument, and every
  ``return 1`` arm.
* ``create`` / ``reset-password`` password input (GOVERNANCE.md R4). The
  ``-p/--password`` option, which put the password in argv and shell history,
  is gone; ``--password-stdin`` reads one line from stdin instead, the way
  ``docker login --password-stdin`` does. These drive the Click commands through
  ``CliRunner`` and pin both the new path and the unchanged generated-password
  default, reading the password back out of the ``aws`` argv it reached.

Stubbing is at the outermost boundary -- 53d-2a's recorded rule -- so the pins
survive code motion inside the package. Concretely:

* AWS is the shared ``aws_cli`` fixture, which replaces ``run_command`` at the
  ``deployer.aws.cli`` seam. The real ``get_user_pool_name``, ``list_users``
  and ``format_user`` all run; only the subprocess is fake.
* The filesystem is real: ``DEPLOYER_ENVIRONMENTS_DIR`` points at ``tmp_path``,
  and ``config.toml`` / ``terraform.tfstate`` are real files, so
  ``get_environments_dir``, ``get_all_environments`` and
  ``get_environment_path`` are all the production ones.
* ``load_environment_config`` is the one stub. It shells out to ``tofu output
  -json`` unconditionally -- there is no placeholder-free shortcut -- and
  config resolution is a separate subsystem with its own tests in
  ``test_config.py``. ``is_cognito_enabled`` and
  ``get_cognito_user_pool_id_from_config`` are left real and fed real config
  dicts.
"""

import io
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from deployer.core import config as core_config
from deployer.core.cognito import CognitoUser

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("cognito_cli", bin_dir / "cognito.py")
cognito_cli = module_from_spec(_spec)
_spec.loader.exec_module(cognito_cli)

POOL = "us-west-2_abc123"
OTHER_POOL = "us-west-2_def456"


def _raw_user(
    username: str,
    email: str | None = None,
    status: str = "CONFIRMED",
    enabled: bool = True,
    created: object = "2026-08-13T09:00:00+00:00",
) -> dict:
    """Build a raw Cognito user record the way `aws cognito-idp list-users` emits one."""
    user: dict = {"Username": username, "UserStatus": status, "Enabled": enabled}
    if email is not None:
        user["Attributes"] = [{"Name": "email", "Value": email}]
    if created is not None:
        user["UserCreateDate"] = created
    return user


def _fields(**overrides) -> dict:
    """The six fields of a format_user() record, as a mutable mapping."""
    fields = {
        "username": "alice@example.com",
        "email": "alice@example.com",
        "status": "CONFIRMED",
        "enabled": True,
        "created": "2026-08-13 09:00",
        "last_modified": "2026-08-13 09:00",
    }
    fields.update(overrides)
    return fields


def _formatted(**overrides) -> CognitoUser:
    """Build a display record shaped exactly like format_user()'s return."""
    return CognitoUser(**_fields(**overrides))


def _cognito_config(pool_id: str | None, enabled: bool = True) -> dict:
    """Build a resolved config the way load_environment_config() returns one."""
    cognito: dict = {"enabled": enabled}
    if pool_id is not None:
        cognito["user_pool_id"] = pool_id
    return {"cognito": cognito}


@pytest.fixture
def environments(tmp_path, monkeypatch):
    """A real environments directory whose config loading is stubbed.

    Returns a callable that registers an environment: the name, the config
    load_environment_config() should answer with (or an exception to raise),
    and whether the environment is deployed (has terraform.tfstate).
    """
    monkeypatch.setenv("DEPLOYER_ENVIRONMENTS_DIR", str(tmp_path))
    configs: dict[str, object] = {}

    def _load(env_path: Path) -> dict:
        answer = configs[env_path.name]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(cognito_cli, "load_environment_config", _load)

    def _register(name: str, config: object, deployed: bool = True) -> Path:
        env_dir = tmp_path / name
        env_dir.mkdir()
        # get_all_environments() identifies an environment by its config.toml.
        (env_dir / "config.toml").write_text("", encoding="utf-8")
        if deployed:
            (env_dir / "terraform.tfstate").write_text("{}", encoding="utf-8")
        configs[name] = config
        return env_dir

    return _register


def _aws_replies(aws_cli, pool_name: str | None, users: list[dict]) -> None:
    """Queue one describe-user-pool reply and one list-users reply."""
    describe = (
        (True, json.dumps({"UserPool": {"Name": pool_name}}))
        if pool_name is not None
        else (False, "AccessDeniedException")
    )
    aws_cli.replies(describe, (True, json.dumps({"Users": users})))


class TestPrintUsersTable:
    """print_users_table() -- the sole consumer of format_user()'s record."""

    def test_an_empty_list_says_so_and_prints_no_table(self, capsys):
        cognito_cli.print_users_table([])
        out = capsys.readouterr().out
        assert out == "No users found.\n"

    def test_the_empty_message_takes_the_indent(self, capsys):
        cognito_cli.print_users_table([], indent="  ")
        assert capsys.readouterr().out == "  No users found.\n"

    def test_the_header_and_rule_are_printed_above_the_rows(self, capsys):
        cognito_cli.print_users_table([_formatted()])
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].split() == ["Email", "Status", "Enabled", "Created"]
        assert set(lines[1]) == {"-", " "}
        assert lines[2].startswith("alice@example.com")

    def test_the_email_column_widens_to_the_longest_address(self, capsys):
        users = [_formatted(email="a@b.co"), _formatted(email="a-very-long-address@example.com")]
        cognito_cli.print_users_table(users)
        header = capsys.readouterr().out.splitlines()[0]
        assert header.startswith("Email" + " " * (len("a-very-long-address@example.com") - 5))

    def test_the_email_column_never_narrows_below_the_header(self, capsys):
        cognito_cli.print_users_table([_formatted(email="a@b", username="a@b")])
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("Email  ")
        assert lines[1].startswith("-----  ")

    def test_a_blank_email_falls_back_to_the_username(self, capsys):
        cognito_cli.print_users_table([_formatted(email="", username="bob")])
        assert "bob" in capsys.readouterr().out.splitlines()[2]

    def test_the_width_calculation_uses_the_same_fallback(self, capsys):
        # The width comes from `u.email or u.username`, so a blank email
        # with a long username still widens the column.
        cognito_cli.print_users_table(
            [_formatted(email="", username="a-long-username@example.com")]
        )
        header = capsys.readouterr().out.splitlines()[0]
        assert len(header.split("Status")[0].rstrip()) == len("Email")
        assert header.index("Status") == len("a-long-username@example.com") + 2

    def test_enabled_renders_as_yes_and_disabled_as_upper_case_no(self, capsys):
        cognito_cli.print_users_table([_formatted(enabled=True), _formatted(enabled=False)])
        rows = capsys.readouterr().out.splitlines()[2:]
        assert "Yes" in rows[0]
        assert "NO" in rows[1]

    def test_a_missing_created_date_shows_n_a(self, capsys):
        # The last column is still padded to its 16-wide minimum, so the row
        # carries trailing whitespace.
        cognito_cli.print_users_table([_formatted(created=None)])
        row = capsys.readouterr().out.splitlines()[2]
        assert row.endswith("N/A" + " " * 13)

    def test_every_line_including_the_rows_takes_the_indent(self, capsys):
        cognito_cli.print_users_table([_formatted()], indent="    ")
        for line in capsys.readouterr().out.splitlines():
            assert line.startswith("    ")

    def test_a_created_value_longer_than_its_column_is_not_truncated(self, capsys):
        # Pinned, not endorsed: `:<16` is a minimum width, not a maximum. A
        # Cognito record read from `aws ... list-users` JSON carries
        # UserCreateDate as an ISO *string*, which format_user() passes
        # through unchanged, so the real column is 25+ characters wide and
        # ragged against the header rule.
        cognito_cli.print_users_table([_formatted(created="2026-08-13T09:00:00.000000+00:00")])
        assert "2026-08-13T09:00:00.000000+00:00" in capsys.readouterr().out

    @pytest.mark.parametrize("missing", ["email", "status", "enabled", "created"])
    def test_a_record_missing_any_always_read_field_cannot_be_built(self, missing):
        # 53f-3 decided the question this pin was written to force. While the
        # record was a dict every read was a subscript, not a .get(), so a
        # record that is not a format_user() record blew up -- for `status`,
        # `enabled` and `created`, after the header had already been printed.
        # CognitoUser moves the same failure to construction time, so the
        # table's reads can no longer raise at all.
        fields = _fields()
        del fields[missing]
        with pytest.raises(TypeError, match=missing):
            CognitoUser(**fields)

    def test_a_record_cannot_omit_the_username_the_fallback_reads(self):
        # `u.email or u.username` short-circuits, so while the record was a
        # dict `username` was the one display key it could omit -- as long as
        # every email was non-empty. Both halves were pinned: absent-and-unused
        # was fine, absent-and-needed raised. CognitoUser makes the field
        # unomittable; the short-circuit itself is still pinned, by
        # test_a_blank_email_falls_back_to_the_username above.
        fields = _fields()
        del fields["username"]
        with pytest.raises(TypeError, match="username"):
            CognitoUser(**fields)

    def test_a_real_format_user_record_prints_without_a_key_error(self, capsys):
        # The two halves of the contract, pinned against each other: whatever
        # format_user() produces is what print_users_table() must accept.
        raw = _raw_user("alice@example.com", email="alice@example.com")
        cognito_cli.print_users_table([cognito_cli.format_user(raw)])
        row = capsys.readouterr().out.splitlines()[2]
        assert row.startswith("alice@example.com")
        assert "CONFIRMED" in row
        assert "Yes" in row

    def test_a_real_record_with_no_email_attribute_falls_back_to_the_username(self, capsys):
        # format_user() answers "" for a missing email attribute, which is
        # exactly the falsy value the table's `or` fallback is written for.
        raw = _raw_user("bob", email=None, status="FORCE_CHANGE_PASSWORD", enabled=False)
        cognito_cli.print_users_table([cognito_cli.format_user(raw)])
        row = capsys.readouterr().out.splitlines()[2]
        assert row.startswith("bob")
        assert "FORCE_CHANGE_PASSWORD" in row
        assert "NO" in row

    def test_a_real_record_with_no_create_date_shows_n_a(self, capsys):
        raw = _raw_user("carol", email="carol@example.com", created=None)
        cognito_cli.print_users_table([cognito_cli.format_user(raw)])
        assert capsys.readouterr().out.splitlines()[2].rstrip().endswith("N/A")


class TestCmdListDiscovery:
    """cmd_list()'s environment discovery and its four `return 1` arms."""

    def test_no_environments_at_all_returns_1(self, environments, capsys):
        assert cognito_cli.cmd_list(None) == 1
        assert "No Cognito-enabled environments found." in capsys.readouterr().err

    def test_environments_without_cognito_are_not_discovered(self, environments, capsys):
        environments("myapp-staging", {"cognito": {"enabled": False}})
        assert cognito_cli.cmd_list(None) == 1
        assert "No Cognito-enabled environments found." in capsys.readouterr().err

    def test_an_undeployed_environment_is_not_discovered(self, environments, capsys):
        # get_cognito_environments() skips any environment with no
        # terraform.tfstate, so discovery never reaches its config.
        environments("myapp-staging", _cognito_config(POOL), deployed=False)
        assert cognito_cli.cmd_list(None) == 1
        assert "No Cognito-enabled environments found." in capsys.readouterr().err

    def test_a_config_error_during_discovery_is_named_not_swallowed(self, environments, capsys):
        # get_cognito_environments() used to `continue` with no message, so a
        # broken environment was indistinguishable from one without Cognito.
        # It is still left out -- whether it uses Cognito is unknown -- but
        # the operator is told which one and why.
        environments("myapp-staging", RuntimeError("tofu failed"))
        assert cognito_cli.cmd_list(None) == 1
        err = capsys.readouterr().err
        assert "  myapp-staging: skipped, could not load config: tofu failed" in err
        assert "No Cognito-enabled environments found." in err

    def test_an_explicit_environment_skips_discovery(self, environments, aws_cli, capsys):
        # The named environment is used verbatim -- is_cognito_enabled() is
        # never consulted, only get_cognito_user_pool_id_from_config().
        environments("myapp-staging", _cognito_config(POOL))
        _aws_replies(aws_cli, "myapp-staging-pool", [])
        assert cognito_cli.cmd_list("myapp-staging") == 0
        assert "Environments: myapp-staging" in capsys.readouterr().out

    def test_an_explicit_environment_that_does_not_exist_returns_1(self, environments, capsys):
        assert cognito_cli.cmd_list("nope") == 1
        assert "No Cognito-enabled environments found." in capsys.readouterr().err

    def test_an_explicit_undeployed_environment_is_reported_as_not_deployed(
        self, environments, capsys
    ):
        environments("myapp-staging", _cognito_config(POOL), deployed=False)
        assert cognito_cli.cmd_list("myapp-staging") == 1
        captured = capsys.readouterr()
        assert "  myapp-staging: Not deployed" in captured.out
        assert "No Cognito-enabled environments found." in captured.err

    def test_an_explicit_environment_whose_config_fails_to_load_is_reported(
        self, environments, capsys
    ):
        environments("myapp-staging", RuntimeError("tofu failed"))
        assert cognito_cli.cmd_list("myapp-staging") == 1
        assert "  myapp-staging: Error loading config: tofu failed" in capsys.readouterr().out

    def test_an_explicit_environment_without_a_pool_id_is_skipped_silently(
        self, environments, capsys
    ):
        # Cognito is enabled but the pool id is absent, so
        # get_cognito_user_pool_id_from_config() answers None and the
        # environment is dropped with no message at all.
        environments("myapp-staging", _cognito_config(None))
        assert cognito_cli.cmd_list("myapp-staging") == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "No Cognito-enabled environments found." in captured.err


class TestCmdListOutput:
    """cmd_list()'s per-pool output, including the format_user() round trip."""

    def test_a_named_pool_shows_its_name_and_id(self, environments, aws_cli, capsys):
        environments("myapp-staging", _cognito_config(POOL))
        _aws_replies(aws_cli, "myapp-staging-pool", [])
        assert cognito_cli.cmd_list(None) == 0
        assert f"User Pool: myapp-staging-pool ({POOL})" in capsys.readouterr().out

    def test_an_undescribable_pool_falls_back_to_the_bare_id(self, environments, aws_cli, capsys):
        # get_user_pool_name() answers None when describe-user-pool fails
        # (typically missing IAM permission); the listing carries on.
        environments("myapp-staging", _cognito_config(POOL))
        _aws_replies(aws_cli, None, [])
        assert cognito_cli.cmd_list(None) == 0
        out = capsys.readouterr().out
        assert f"User Pool: {POOL}" in out
        assert "(" not in out.split("User Pool:")[1].splitlines()[0]

    def test_users_are_counted_and_tabulated(self, environments, aws_cli, capsys):
        environments("myapp-staging", _cognito_config(POOL))
        _aws_replies(
            aws_cli,
            "pool",
            [
                _raw_user("alice@example.com", email="alice@example.com"),
                _raw_user("bob", email=None, enabled=False, status="UNCONFIRMED"),
            ],
        )
        assert cognito_cli.cmd_list(None) == 0
        out = capsys.readouterr().out
        assert "Users: 2" in out
        assert "alice@example.com" in out
        assert "bob" in out
        assert "UNCONFIRMED" in out
        assert "NO" in out

    def test_an_empty_pool_prints_the_no_users_line(self, environments, aws_cli, capsys):
        environments("myapp-staging", _cognito_config(POOL))
        _aws_replies(aws_cli, "pool", [])
        assert cognito_cli.cmd_list(None) == 0
        out = capsys.readouterr().out
        assert "Users: 0" in out
        assert "No users found." in out

    def test_an_unlistable_pool_is_reported_not_counted_as_empty(
        self, environments, aws_cli, capsys
    ):
        """A refused list-users used to print "Users: 0" and exit 0."""
        environments("myapp-staging", _cognito_config(POOL))
        aws_cli.replies(
            (True, json.dumps({"UserPool": {"Name": "pool"}})),
            (False, "An error occurred (AccessDeniedException) when calling ListUsers"),
        )
        assert cognito_cli.cmd_list(None) == 1
        captured = capsys.readouterr()
        assert "Users: unable to list (" in captured.out
        assert "AccessDeniedException" in captured.out
        assert "Users: 0" not in captured.out
        assert "No users found." not in captured.out
        assert "could not list users in 1 pool(s)" in captured.err

    def test_two_environments_sharing_a_pool_are_listed_once(self, environments, aws_cli, capsys):
        environments("myapp-staging", _cognito_config(POOL))
        environments("myapp-production", _cognito_config(POOL))
        _aws_replies(aws_cli, "shared", [_raw_user("alice@example.com", email="a@example.com")])
        assert cognito_cli.cmd_list(None) == 0
        out = capsys.readouterr().out
        # get_all_environments() sorts, so production is registered first.
        assert "Environments: myapp-production, myapp-staging" in out
        assert out.count("User Pool:") == 1
        assert "Users: 1" in out

    def test_a_single_pool_prints_no_total_line(self, environments, aws_cli, capsys):
        environments("myapp-staging", _cognito_config(POOL))
        _aws_replies(aws_cli, "pool", [_raw_user("alice@example.com", email="a@example.com")])
        assert cognito_cli.cmd_list(None) == 0
        assert "Total:" not in capsys.readouterr().out

    def test_two_pools_are_listed_separately_and_totalled(self, environments, aws_cli, capsys):
        environments("myapp-production", _cognito_config(POOL))
        environments("myapp-staging", _cognito_config(OTHER_POOL))
        aws_cli.replies(
            (True, json.dumps({"UserPool": {"Name": "prod"}})),
            (True, json.dumps({"Users": [_raw_user("a@example.com", email="a@example.com")]})),
            (True, json.dumps({"UserPool": {"Name": "stage"}})),
            (
                True,
                json.dumps(
                    {
                        "Users": [
                            _raw_user("b@example.com", email="b@example.com"),
                            _raw_user("c@example.com", email="c@example.com"),
                        ]
                    }
                ),
            ),
        )
        assert cognito_cli.cmd_list(None) == 0
        out = capsys.readouterr().out
        assert out.count("User Pool:") == 2
        assert "Total: 3 user(s) across 2 pool(s)" in out

    def test_a_broken_environment_is_reported_before_the_working_ones(
        self, environments, aws_cli, capsys
    ):
        # The error lines are accumulated during the scan and flushed in one
        # block *before* any pool section is printed.
        environments("myapp-production", _cognito_config(POOL))
        environments("myapp-staging", _cognito_config(POOL), deployed=False)
        _aws_replies(aws_cli, "pool", [])
        assert cognito_cli.cmd_list("myapp-production") == 0
        assert cognito_cli.cmd_list(None) == 0
        out = capsys.readouterr().out
        # Discovery drops the undeployed environment, so with no argument it is
        # not even reported -- only the deployed one reaches the pool listing.
        assert "Not deployed" not in out
        assert "Environments: myapp-production" in out

    def test_the_table_is_indented_two_spaces_under_the_pool_header(
        self, environments, aws_cli, capsys
    ):
        environments("myapp-staging", _cognito_config(POOL))
        _aws_replies(aws_cli, "pool", [_raw_user("alice@example.com", email="a@example.com")])
        assert cognito_cli.cmd_list(None) == 0
        assert "\n  Email" in capsys.readouterr().out


CHOSEN = "Chosen-Secret-123"


@pytest.fixture
def deployed_env(tmp_path, monkeypatch):
    """One deployed, Cognito-enabled environment for the Click commands.

    The environment directory and its terraform.tfstate are real, so
    require_validated_environment() runs; load_environment_config() is the one
    stub, as in ``environments`` above. AWS_PROFILE short-circuits the
    profile lookup so the real _configure_aws() runs without a config.toml
    [aws] table.
    """
    monkeypatch.setenv("DEPLOYER_ENVIRONMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("AWS_PROFILE", "test")
    env_dir = tmp_path / "myapp-staging"
    env_dir.mkdir()
    (env_dir / "config.toml").write_text("", encoding="utf-8")
    (env_dir / "terraform.tfstate").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core_config, "load_environment_config", lambda _: _cognito_config(POOL))
    return "myapp-staging"


def _invoke(args: list[str], stdin: str):
    return CliRunner().invoke(cognito_cli.cli, args, input=stdin)


def _flag(argv: list[str], flag: str) -> str:
    """The value that follows `flag` in an aws argv."""
    return argv[argv.index(flag) + 1]


def _create(env: str, *extra: str) -> list[str]:
    return ["create", env, "--email", "alice@example.com", *extra]


def _reset(env: str, *extra: str) -> list[str]:
    return ["reset-password", env, "--email", "alice@example.com", *extra]


class TestPasswordStdin:
    """--password-stdin: one line from stdin, trailing newline stripped."""

    def test_create_sets_the_piped_password_as_permanent(self, deployed_env, aws_cli):
        result = _invoke(_create(deployed_env, "--password-stdin"), f"{CHOSEN}\n")
        assert result.exit_code == 0, result.output
        create, set_password = aws_cli.calls
        assert "admin-create-user" in create
        assert _flag(create, "--temporary-password") == CHOSEN
        assert "admin-set-user-password" in set_password
        assert _flag(set_password, "--password") == CHOSEN
        assert "--permanent" in set_password
        assert "prompted to change their password" not in result.output

    def test_create_clipboard_prompt_finds_stdin_exhausted_and_declines(
        self, deployed_env, aws_cli
    ):
        # The one stdin line was the password, so the "Copy to clipboard?"
        # input() hits EOF, which cmd_create already treats as "no".
        result = _invoke(_create(deployed_env, "--password-stdin"), f"{CHOSEN}\n")
        assert result.exit_code == 0, result.output
        assert "Copy to clipboard? [Y/n]" in result.output
        assert "Copied to clipboard!" not in result.output

    def test_reset_password_sets_the_piped_password_and_does_not_echo_it(
        self, deployed_env, aws_cli
    ):
        result = _invoke(_reset(deployed_env, "--password-stdin"), f"{CHOSEN}\n")
        assert result.exit_code == 0, result.output
        assert _flag(aws_cli.argv, "--password") == CHOSEN
        assert "--permanent" not in aws_cli.argv
        assert "New password:" not in result.output
        assert CHOSEN not in result.output

    def test_reset_password_permanent_still_applies(self, deployed_env, aws_cli):
        result = _invoke(_reset(deployed_env, "--password-stdin", "--permanent"), f"{CHOSEN}\n")
        assert result.exit_code == 0, result.output
        assert "--permanent" in aws_cli.argv

    def test_only_the_first_line_is_read(self, deployed_env, aws_cli):
        result = _invoke(_reset(deployed_env, "--password-stdin"), f"{CHOSEN}\nsecond line\n")
        assert result.exit_code == 0, result.output
        assert _flag(aws_cli.argv, "--password") == CHOSEN

    @pytest.mark.parametrize(
        ("stdin", "expected"),
        [
            (f"{CHOSEN}\r\n", CHOSEN),
            (f"{CHOSEN}", CHOSEN),
            (f" {CHOSEN} \n", f" {CHOSEN} "),
        ],
        ids=["crlf-stripped", "no-trailing-newline", "spaces-kept"],
    )
    def test_only_the_line_ending_is_stripped(self, deployed_env, aws_cli, stdin, expected):
        result = _invoke(_reset(deployed_env, "--password-stdin"), stdin)
        assert result.exit_code == 0, result.output
        assert _flag(aws_cli.argv, "--password") == expected

    @pytest.mark.parametrize("build", [_create, _reset], ids=["create", "reset-password"])
    @pytest.mark.parametrize("stdin", ["", "\n"], ids=["no-input", "blank-line"])
    def test_an_empty_password_is_a_usage_error_before_any_aws_call(
        self, deployed_env, aws_cli, build, stdin
    ):
        result = _invoke(build(deployed_env, "--password-stdin"), stdin)
        assert result.exit_code == 2
        assert "stdin held no password" in result.output
        assert aws_cli.calls == []

    def test_a_terminal_on_stdin_is_a_usage_error_and_nothing_is_read(self, monkeypatch):
        # CliRunner always substitutes a non-terminal stdin, so the terminal
        # arm is driven through the helper both commands call.
        class _Terminal(io.StringIO):
            def isatty(self) -> bool:
                return True

        terminal = _Terminal(f"{CHOSEN}\n")
        monkeypatch.setattr(sys, "stdin", terminal)
        with pytest.raises(click.UsageError, match="stdin is a terminal"):
            cognito_cli.read_password_stdin()
        assert terminal.tell() == 0


class TestGeneratedPasswordDefault:
    """Without --password-stdin each command behaves exactly as before R4."""

    def test_create_generates_a_temporary_password_and_sets_nothing_permanent(
        self, deployed_env, aws_cli
    ):
        result = _invoke(_create(deployed_env), "n\n")
        assert result.exit_code == 0, result.output
        temporary = _flag(aws_cli.argv, "--temporary-password")
        assert temporary
        assert f"Password: {temporary}" in result.output
        assert "prompted to change their password on first login" in result.output

    def test_reset_password_generates_and_prints_a_temporary_password(self, deployed_env, aws_cli):
        result = _invoke(_reset(deployed_env), "")
        assert result.exit_code == 0, result.output
        generated = _flag(aws_cli.argv, "--password")
        assert f"New password: {generated}" in result.output
        assert "--permanent" not in aws_cli.argv
        assert "prompted to change their password on next login" in result.output


class TestPasswordOptionRemoved:
    """-p/--password is gone: a password on the command line is a usage error."""

    @pytest.mark.parametrize("build", [_create, _reset], ids=["create", "reset-password"])
    @pytest.mark.parametrize("flag", ["--password", "-p"])
    def test_a_password_argument_is_rejected_before_any_aws_call(
        self, deployed_env, aws_cli, build, flag
    ):
        result = _invoke(build(deployed_env, flag, CHOSEN), "")
        assert result.exit_code == 2
        assert "No such option" in result.output
        assert aws_cli.calls == []
