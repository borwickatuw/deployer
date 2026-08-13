"""Characterization tests for bin/init.py's file-writing commands.

These pin today's behaviour of cmd_bootstrap(), cmd_bootstrap_migrate(),
cmd_deploy_toml(), cmd_environment() and _print_next_steps() — return codes,
every validation error, the dry-run preview path, which files get written and
with which permissions, and the text and order of every next-steps list — so
that a decomposition of the two long commands can be shown to preserve it.

Nothing reaches AWS, tofu or the real environments directory: the generators,
get_environments_dir() and _run_tofu() are stubbed at the module boundary, and
the interactive prompts are fed through a queue the way
tests/unit/test_bootstrap.py's TestPromptAccountIdAndRegion does.

Blank-line placement *between* next-steps entries is deliberately not pinned:
the four next-steps lists in bin/init.py disagree about it today, and unifying
them on the blank-separated form is the point of the _numbered_steps() helper.
Step text, numbering and order are pinned and must not change.

"pinned, not endorsed": cmd_deploy_toml() catches bare `Exception` around
generate_deploy_toml() and reports it as a compose-parsing error, so a bug in
the generator is misattributed to the operator's input. That is an
error-contract question tracked as claude-meta Phase 53i; this pins today's
behaviour so 53i's change is visible when it happens.
"""

import stat
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("init_cli", bin_dir / "init.py")
init_cli = module_from_spec(_spec)
_spec.loader.exec_module(init_cli)

ACCOUNT_ID = "123456789012"
REGION = "us-west-2"


def _prompts(monkeypatch, *answers):
    """Answer successive click.prompt() calls from a queue."""
    queue = list(answers)
    monkeypatch.setattr(init_cli.click, "prompt", lambda *_a, **_kw: queue.pop(0))


def _confirms(monkeypatch, *answers):
    """Answer successive click.confirm() calls from a queue."""
    queue = list(answers)
    monkeypatch.setattr(init_cli.click, "confirm", lambda *_a, **_kw: queue.pop(0))


def _lines(capsys):
    """Return stdout's non-blank lines.

    Blank lines are dropped on purpose — see the module docstring.
    """
    return [line for line in capsys.readouterr().out.splitlines() if line.strip()]


@pytest.fixture
def env_dir(tmp_path, monkeypatch):
    """Point bin/init.py's get_environments_dir() at a temporary directory."""
    directory = tmp_path / "environments"
    monkeypatch.setattr(init_cli, "get_environments_dir", lambda: directory)
    return directory


# =============================================================================
# cmd_bootstrap
# =============================================================================

BOOTSTRAP_FILES = {
    "main.tf": 'terraform {\n  required_version = ">= 1.6.0"\n}\n',
    "import-existing.sh": "#!/usr/bin/env bash\necho import\n",
}


@pytest.fixture
def bootstrap_stubs(monkeypatch, env_dir):
    """Stub every collaborator cmd_bootstrap() reaches out to.

    Returns a dict recording what generate_bootstrap(), _run_tofu() and
    cmd_bootstrap_migrate() were called with.
    """
    calls: dict = {"generate": None, "tofu": [], "migrate": None}

    def fake_generate(**kwargs):
        calls["generate"] = kwargs
        return dict(BOOTSTRAP_FILES)

    def fake_tofu(subcommand, env_path, admin_profile):
        calls["tofu"].append((subcommand, env_path, admin_profile))
        return True

    def fake_migrate(env_name, dry_run):
        calls["migrate"] = (env_name, dry_run)
        return 0

    monkeypatch.setattr(init_cli, "prompt_account_id_and_region", lambda: (ACCOUNT_ID, REGION))
    monkeypatch.setattr(init_cli, "ensure_environments_symlinks", list)
    monkeypatch.setattr(init_cli, "generate_bootstrap", fake_generate)
    monkeypatch.setattr(init_cli, "_run_tofu", fake_tofu)
    monkeypatch.setattr(init_cli, "cmd_bootstrap_migrate", fake_migrate)
    return calls


def _answer_bootstrap(monkeypatch, *, cognito=False, apply=False, extra_prompts=()):
    """Feed cmd_bootstrap()'s prompts with a valid set of answers."""
    answers = ["staging", "myapp, otherapp", f"arn:aws:iam::{ACCOUNT_ID}:user/deployer"]
    if cognito:
        answers.append("myapp=myapp.example.com")
    answers.extend(extra_prompts)
    _prompts(monkeypatch, *answers)
    _confirms(monkeypatch, cognito, apply)


class TestCmdBootstrapInputs:
    """Tests for cmd_bootstrap()'s interactive input collection."""

    def test_prompted_values_reach_generate_bootstrap(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that every prompted value is parsed and handed to the generator."""
        _answer_bootstrap(monkeypatch)
        assert init_cli.cmd_bootstrap(dry_run=True) == 0
        capsys.readouterr()
        assert bootstrap_stubs["generate"] == {
            "account_id": ACCOUNT_ID,
            "region": REGION,
            "env_label": "staging",
            "project_prefixes": ["myapp", "otherapp"],
            "trusted_user_arns": [f"arn:aws:iam::{ACCOUNT_ID}:user/deployer"],
            "include_cognito": False,
            "cognito_app_domains": None,
        }

    def test_cognito_app_domains_are_parsed_into_a_map(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that appname=domain pairs become a dict when Cognito is included."""
        _answer_bootstrap(monkeypatch, cognito=True)
        assert init_cli.cmd_bootstrap(dry_run=True) == 0
        capsys.readouterr()
        assert bootstrap_stubs["generate"]["include_cognito"] is True
        assert bootstrap_stubs["generate"]["cognito_app_domains"] == {"myapp": "myapp.example.com"}

    def test_empty_project_prefixes_returns_1(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that a blank prefix list is rejected before anything is generated."""
        _prompts(monkeypatch, "staging", " , ", "arn:aws:iam::x:user/y")
        _confirms(monkeypatch, False, False)
        assert init_cli.cmd_bootstrap(dry_run=True) == 1
        assert "At least one project prefix is required" in capsys.readouterr().err
        assert bootstrap_stubs["generate"] is None

    def test_malformed_app_domain_returns_1(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that an app domain without '=' is rejected by name."""
        _answer_bootstrap(monkeypatch, cognito=True)
        _prompts(monkeypatch, "staging", "myapp", "arn:aws:iam::x:user/y", "nodomainsign")
        assert init_cli.cmd_bootstrap(dry_run=True) == 1
        err = capsys.readouterr().err
        assert "Invalid app domain format 'nodomainsign'" in err
        assert "Expected appname=domain" in err
        assert bootstrap_stubs["generate"] is None

    def test_generator_error_returns_1(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that a ValueError from the generator becomes a clean 'Error: ...'."""

        def boom(**_kwargs):
            raise ValueError("bad template")

        monkeypatch.setattr(init_cli, "generate_bootstrap", boom)
        _answer_bootstrap(monkeypatch)
        assert init_cli.cmd_bootstrap(dry_run=True) == 1
        assert "Error: bad template" in capsys.readouterr().err

    def test_environments_dir_unset_returns_1(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that an unset DEPLOYER_ENVIRONMENTS_DIR is named, with the .env fix."""

        def unset():
            raise RuntimeError("not set")

        monkeypatch.setattr(init_cli, "get_environments_dir", unset)
        _answer_bootstrap(monkeypatch)
        assert init_cli.cmd_bootstrap(dry_run=True) == 1
        err = capsys.readouterr().err
        assert "Error: DEPLOYER_ENVIRONMENTS_DIR is not set." in err
        assert "DEPLOYER_ENVIRONMENTS_DIR=~/deployer-environments" in err

    def test_existing_directory_returns_1(self, monkeypatch, bootstrap_stubs, capsys, env_dir):
        """Test that an existing bootstrap directory stops a non-dry run."""
        (env_dir / "bootstrap-staging").mkdir(parents=True)
        _answer_bootstrap(monkeypatch)
        assert init_cli.cmd_bootstrap(dry_run=False) == 1
        assert "Directory already exists" in capsys.readouterr().err

    def test_existing_directory_is_allowed_for_dry_run(
        self, monkeypatch, bootstrap_stubs, capsys, env_dir
    ):
        """Test that --dry-run does not care whether the directory exists."""
        (env_dir / "bootstrap-staging").mkdir(parents=True)
        _answer_bootstrap(monkeypatch)
        assert init_cli.cmd_bootstrap(dry_run=True) == 0
        assert "Would create directory" in capsys.readouterr().out


class TestCmdBootstrapDryRun:
    """Tests for cmd_bootstrap()'s --dry-run preview."""

    def test_previews_every_file_and_writes_nothing(
        self, monkeypatch, bootstrap_stubs, capsys, env_dir
    ):
        """Test that the preview names the directory and each file's content."""
        _answer_bootstrap(monkeypatch)
        assert init_cli.cmd_bootstrap(dry_run=True) == 0
        out = capsys.readouterr().out
        assert f"Would create directory: {env_dir / 'bootstrap-staging'}" in out
        assert "--- main.tf ---" in out
        assert "--- import-existing.sh ---" in out
        assert 'required_version = ">= 1.6.0"' in out
        assert not env_dir.exists()


class TestCmdBootstrapWrites:
    """Tests for the files cmd_bootstrap() writes."""

    def test_writes_files_and_marks_the_import_script_executable(
        self, monkeypatch, bootstrap_stubs, capsys, env_dir
    ):
        """Test that each generated file lands on disk and import-existing.sh is +x."""
        _answer_bootstrap(monkeypatch)
        assert init_cli.cmd_bootstrap(dry_run=False) == 0
        env_path = env_dir / "bootstrap-staging"
        assert (env_path / "main.tf").read_text() == BOOTSTRAP_FILES["main.tf"]
        script = env_path / "import-existing.sh"
        assert script.read_text() == BOOTSTRAP_FILES["import-existing.sh"]
        mode = script.stat().st_mode
        assert mode & stat.S_IXUSR and mode & stat.S_IXGRP and mode & stat.S_IXOTH
        out = capsys.readouterr().out
        assert f"Created: {env_path / 'main.tf'}" in out

    def test_created_symlinks_are_reported(self, monkeypatch, bootstrap_stubs, capsys, env_dir):
        """Test that ensure_environments_symlinks()'s result is announced."""
        monkeypatch.setattr(
            init_cli, "ensure_environments_symlinks", lambda: ["modules", "main.tf"]
        )
        _answer_bootstrap(monkeypatch)
        assert init_cli.cmd_bootstrap(dry_run=False) == 0
        assert f"Created symlinks in {env_dir}: modules, main.tf" in capsys.readouterr().out

    def test_declining_apply_prints_the_manual_next_steps(
        self, monkeypatch, bootstrap_stubs, capsys, env_dir
    ):
        """Test the numbered manual next steps shown when the apply is declined."""
        _answer_bootstrap(monkeypatch, apply=False)
        assert init_cli.cmd_bootstrap(dry_run=False) == 0
        env_path = env_dir / "bootstrap-staging"
        assert _lines(capsys)[-6:-1] == [
            "Next steps:",
            f"  1. cd {env_path}",
            "  2. AWS_PROFILE=admin tofu init",
            "  3. AWS_PROFILE=admin tofu apply",
            "  After successful apply, enable S3 backend:",
        ]
        assert bootstrap_stubs["tofu"] == []

    def test_declining_apply_names_the_migrate_state_command(
        self, monkeypatch, bootstrap_stubs, capsys, env_dir
    ):
        """Test that the manual trailer names the --migrate-state follow-up."""
        _answer_bootstrap(monkeypatch, apply=False)
        assert init_cli.cmd_bootstrap(dry_run=False) == 0
        assert (
            "    uv run python bin/init.py bootstrap --migrate-state bootstrap-staging"
            in _lines(capsys)
        )


class TestCmdBootstrapApply:
    """Tests for cmd_bootstrap()'s tofu init/apply path."""

    def test_runs_init_then_apply_then_migrates_state(
        self, monkeypatch, bootstrap_stubs, capsys, env_dir
    ):
        """Test the full apply sequence, ending in the -migrate-state next step."""
        _answer_bootstrap(monkeypatch, apply=True, extra_prompts=["ops-admin"])
        assert init_cli.cmd_bootstrap(dry_run=False) == 0
        env_path = env_dir / "bootstrap-staging"
        assert bootstrap_stubs["tofu"] == [
            ("init", env_path, "ops-admin"),
            ("apply", env_path, "ops-admin"),
        ]
        assert bootstrap_stubs["migrate"] == ("bootstrap-staging", False)
        assert _lines(capsys)[-5:-1] == [
            "Apply succeeded. Enabling S3 backend...",
            "Next step:",
            f"  cd {env_path}",
            "  AWS_PROFILE=ops-admin tofu init -migrate-state",
        ]

    def test_final_hint_mentions_answering_yes(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that the closing line tells the operator to answer 'yes'."""
        _answer_bootstrap(monkeypatch, apply=True, extra_prompts=["admin"])
        assert init_cli.cmd_bootstrap(dry_run=False) == 0
        assert '  (answer "yes" to copy state to S3)' in _lines(capsys)

    def test_failed_init_returns_1_without_applying(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that a failed 'tofu init' stops before 'tofu apply'."""
        monkeypatch.setattr(init_cli, "_run_tofu", lambda *_a: False)
        _answer_bootstrap(monkeypatch, apply=True, extra_prompts=["admin"])
        assert init_cli.cmd_bootstrap(dry_run=False) == 1
        capsys.readouterr()
        assert bootstrap_stubs["migrate"] is None

    def test_failed_apply_returns_1(self, monkeypatch, bootstrap_stubs, capsys):
        """Test that a failed 'tofu apply' stops before the state migration."""
        results = iter([True, False])
        monkeypatch.setattr(init_cli, "_run_tofu", lambda *_a: next(results))
        _answer_bootstrap(monkeypatch, apply=True, extra_prompts=["admin"])
        assert init_cli.cmd_bootstrap(dry_run=False) == 1
        capsys.readouterr()
        assert bootstrap_stubs["migrate"] is None

    def test_failed_migration_propagates_its_return_code(
        self, monkeypatch, bootstrap_stubs, capsys
    ):
        """Test that cmd_bootstrap_migrate()'s non-zero code is returned as-is."""
        monkeypatch.setattr(init_cli, "cmd_bootstrap_migrate", lambda *_a, **_kw: 1)
        _answer_bootstrap(monkeypatch, apply=True, extra_prompts=["admin"])
        assert init_cli.cmd_bootstrap(dry_run=False) == 1
        capsys.readouterr()


# =============================================================================
# cmd_bootstrap_migrate
# =============================================================================


@pytest.fixture
def migrate_env(monkeypatch, env_dir):
    """Create a bootstrap directory with a main.tf, and stub the uncommenter."""
    env_path = env_dir / "bootstrap-staging"
    env_path.mkdir(parents=True)
    (env_path / "main.tf").write_text("# BOOTSTRAP-BACKEND-START\n")
    monkeypatch.setattr(init_cli, "uncomment_backend_block", lambda _c: 'backend "s3" {}\n')
    return env_path


class TestCmdBootstrapMigrate:
    """Tests for cmd_bootstrap_migrate()."""

    def test_writes_the_uncommented_backend(self, capsys, migrate_env):
        """Test that main.tf is rewritten with the backend block enabled."""
        assert init_cli.cmd_bootstrap_migrate("bootstrap-staging", dry_run=False) == 0
        assert (migrate_env / "main.tf").read_text() == 'backend "s3" {}\n'

    def test_next_steps_are_numbered_in_order(self, capsys, migrate_env):
        """Test the three-step migration checklist, in order."""
        assert init_cli.cmd_bootstrap_migrate("bootstrap-staging", dry_run=False) == 0
        main_tf = migrate_env / "main.tf"
        assert _lines(capsys) == [
            f"S3 backend enabled in {main_tf}",
            "Next steps:",
            f"  1. cd {migrate_env}",
            "  2. AWS_PROFILE=admin tofu init -migrate-state",
            '     (answer "yes" to copy state to S3)',
            "  3. AWS_PROFILE=admin tofu plan",
            '     (should show "No changes")',
        ]

    def test_dry_run_previews_without_writing(self, capsys, migrate_env):
        """Test that --dry-run shows the updated content and leaves main.tf alone."""
        assert init_cli.cmd_bootstrap_migrate("bootstrap-staging", dry_run=True) == 0
        out = capsys.readouterr().out
        assert f"Would update: {migrate_env / 'main.tf'}" in out
        assert 'backend "s3" {}' in out
        assert (migrate_env / "main.tf").read_text() == "# BOOTSTRAP-BACKEND-START\n"

    def test_missing_main_tf_returns_1(self, capsys, env_dir):
        """Test that a bootstrap directory without main.tf is reported by path."""
        (env_dir / "bootstrap-staging").mkdir(parents=True)
        assert init_cli.cmd_bootstrap_migrate("bootstrap-staging", dry_run=False) == 1
        assert "main.tf not found" in capsys.readouterr().err

    def test_environments_dir_unset_returns_1(self, monkeypatch, capsys):
        """Test the short DEPLOYER_ENVIRONMENTS_DIR error this command prints."""

        def unset():
            raise RuntimeError("not set")

        monkeypatch.setattr(init_cli, "get_environments_dir", unset)
        assert init_cli.cmd_bootstrap_migrate("bootstrap-staging", dry_run=False) == 1
        assert "Error: DEPLOYER_ENVIRONMENTS_DIR is not set." in capsys.readouterr().err

    def test_unrecognised_main_tf_exits_1(self, monkeypatch, capsys, migrate_env):
        """Test that uncomment_backend_block()'s ValueError becomes a clean exit."""

        def boom(_content):
            raise ValueError("no backend block found")

        monkeypatch.setattr(init_cli, "uncomment_backend_block", boom)
        with pytest.raises(SystemExit) as exc_info:
            init_cli.cmd_bootstrap_migrate("bootstrap-staging", dry_run=False)
        assert exc_info.value.code == 1
        assert "no backend block found" in capsys.readouterr().err


# =============================================================================
# cmd_deploy_toml
# =============================================================================


@pytest.fixture
def compose_stubs(monkeypatch):
    """Stub the deploy.toml generator and formatter."""
    monkeypatch.setattr(
        init_cli, "generate_deploy_toml", lambda **_kw: {"application": {"name": "myapp"}}
    )
    monkeypatch.setattr(init_cli, "format_deploy_toml", lambda _config: "[application]\n")


def _compose(tmp_path):
    """Write a docker-compose.yml and return its path."""
    path = tmp_path / "docker-compose.yml"
    path.write_text("services:\n  web:\n    image: nginx\n")
    return path


class TestCmdDeployToml:
    """Tests for cmd_deploy_toml()."""

    def test_writes_next_to_the_compose_file_by_default(self, tmp_path, capsys, compose_stubs):
        """Test that deploy.toml lands beside docker-compose.yml."""
        compose = _compose(tmp_path)
        assert init_cli.cmd_deploy_toml(str(compose), None, None, False) == 0
        assert (tmp_path / "deploy.toml").read_text() == "[application]\n"
        assert f"Generated: {tmp_path / 'deploy.toml'}" in capsys.readouterr().out

    def test_output_option_overrides_the_destination(self, tmp_path, capsys, compose_stubs):
        """Test that --output picks the destination path."""
        compose = _compose(tmp_path)
        target = tmp_path / "elsewhere.toml"
        assert init_cli.cmd_deploy_toml(str(compose), None, str(target), False) == 0
        capsys.readouterr()
        assert target.read_text() == "[application]\n"

    def test_next_steps_are_numbered_in_order(self, tmp_path, capsys, compose_stubs):
        """Test the two-step follow-up, naming the generated app in the command."""
        compose = _compose(tmp_path)
        assert init_cli.cmd_deploy_toml(str(compose), None, None, False) == 0
        assert _lines(capsys)[-5:-1] == [
            f"Generated: {tmp_path / 'deploy.toml'}",
            "Next steps:",
            "  1. Review and customize the generated deploy.toml",
            "  2. Create environment directory:",
        ]

    def test_next_steps_name_the_environment_command(self, tmp_path, capsys, compose_stubs):
        """Test that the follow-up spells out the init.py environment invocation."""
        compose = _compose(tmp_path)
        assert init_cli.cmd_deploy_toml(str(compose), None, None, False) == 0
        assert (
            "     uv run python bin/init.py environment --app-name myapp "
            "--template standalone-staging" in _lines(capsys)
        )

    def test_dry_run_previews_without_writing(self, tmp_path, capsys, compose_stubs):
        """Test that --dry-run prints the TOML and writes nothing."""
        compose = _compose(tmp_path)
        assert init_cli.cmd_deploy_toml(str(compose), None, None, True) == 0
        assert "[application]" in capsys.readouterr().out
        assert not (tmp_path / "deploy.toml").exists()

    def test_missing_from_compose_path_returns_1(self, tmp_path, capsys, compose_stubs):
        """Test that an explicit --from-compose path must exist."""
        missing = tmp_path / "nope.yml"
        assert init_cli.cmd_deploy_toml(str(missing), None, None, False) == 1
        assert f"docker-compose.yml not found at {missing}" in capsys.readouterr().err

    def test_missing_default_compose_returns_1(self, tmp_path, monkeypatch, capsys, compose_stubs):
        """Test the hint shown when there is no docker-compose.yml in cwd."""
        monkeypatch.chdir(tmp_path)
        assert init_cli.cmd_deploy_toml(None, None, None, False) == 1
        assert "Use --from-compose to specify path" in capsys.readouterr().err

    def test_default_compose_in_cwd_is_used(self, tmp_path, monkeypatch, capsys, compose_stubs):
        """Test that ./docker-compose.yml is picked up with no --from-compose."""
        _compose(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert init_cli.cmd_deploy_toml(None, None, None, False) == 0
        capsys.readouterr()
        assert (tmp_path / "deploy.toml").exists()

    def test_generator_value_error_returns_1(self, tmp_path, monkeypatch, capsys, compose_stubs):
        """Test that a ValueError is reported verbatim."""

        def boom(**_kw):
            raise ValueError("no services defined")

        monkeypatch.setattr(init_cli, "generate_deploy_toml", boom)
        assert init_cli.cmd_deploy_toml(str(_compose(tmp_path)), None, None, False) == 1
        assert "Error: no services defined" in capsys.readouterr().err

    def test_other_generator_errors_are_blamed_on_the_compose_file(
        self, tmp_path, monkeypatch, capsys, compose_stubs
    ):
        """Test the bare-Exception branch (pinned, not endorsed — 53i).

        Any non-ValueError from the generator is reported as a compose-parsing
        failure, which misattributes a generator bug to the operator's input.
        """

        def boom(**_kw):
            raise KeyError("image")

        monkeypatch.setattr(init_cli, "generate_deploy_toml", boom)
        assert init_cli.cmd_deploy_toml(str(_compose(tmp_path)), None, None, False) == 1
        assert "Error parsing docker-compose.yml" in capsys.readouterr().err


# =============================================================================
# cmd_environment
# =============================================================================

ENVIRONMENT_FILE_NAMES = ("main.tf", "terraform.tfvars", "services.auto.tfvars")


@pytest.fixture
def environment_stubs(monkeypatch, env_dir):
    """Stub every collaborator cmd_environment() reaches out to.

    generate_environment() is made to return absolute paths under the
    environment directory, which is the shape the real one returns.
    """
    calls: dict = {"generate": None, "symlink": [], "priority": []}

    def fake_generate(**kwargs):
        calls["generate"] = kwargs
        name = kwargs["template_name"]
        if not name.startswith("shared-infra-"):
            name = f"{kwargs['app_name']}-{init_cli.extract_env_type(name)}"
        env_path = env_dir / name
        return {str(env_path / filename): f"# {filename}\n" for filename in ENVIRONMENT_FILE_NAMES}

    def fake_symlink(env_path):
        calls["symlink"].append(env_path)
        return True

    def fake_priority(env_type):
        calls["priority"].append(env_type)
        return 300

    monkeypatch.setattr(init_cli, "bootstrap_dir_exists", lambda: "bootstrap-staging")
    monkeypatch.setattr(init_cli, "list_templates", lambda: ["shared-app-staging", "standalone-x"])
    monkeypatch.setattr(init_cli, "ensure_environments_symlinks", list)
    monkeypatch.setattr(init_cli, "generate_environment", fake_generate)
    monkeypatch.setattr(init_cli, "create_deployer_tf_symlink", fake_symlink)
    monkeypatch.setattr(init_cli, "get_next_listener_priority", fake_priority)
    return calls


def _environment(app_name=None, template=None, **overrides):
    """Call cmd_environment() with the boilerplate arguments filled in."""
    kwargs = {
        "list_templates_flag": False,
        "deploy_toml": None,
        "domain": None,
        "dry_run": False,
    }
    kwargs.update(overrides)
    return init_cli.cmd_environment(app_name, template, **kwargs)


class TestCmdEnvironmentGuards:
    """Tests for cmd_environment()'s up-front checks."""

    def test_list_templates_prints_and_returns_0(self, capsys, environment_stubs):
        """Test that --list-templates prints the names and skips everything else."""
        assert _environment(list_templates_flag=True) == 0
        assert _lines(capsys) == [
            "Available templates:",
            "  shared-app-staging",
            "  standalone-x",
        ]
        assert environment_stubs["generate"] is None

    def test_missing_bootstrap_returns_1(self, monkeypatch, capsys, environment_stubs):
        """Test that environments cannot be created before bootstrap has run."""
        monkeypatch.setattr(init_cli, "bootstrap_dir_exists", lambda: None)
        assert _environment("myapp", "standalone-staging") == 1
        err = capsys.readouterr().err
        assert "Error: No bootstrap directory found." in err
        assert "bin/init.py bootstrap" in err

    def test_missing_template_returns_1(self, capsys, environment_stubs):
        """Test that --template is required."""
        assert _environment("myapp", None) == 1
        assert "--template is required" in capsys.readouterr().err

    def test_unrecognised_template_exits_1(self, capsys, environment_stubs):
        """Test that a template with no staging/production segment exits 1."""
        with pytest.raises(SystemExit) as exc_info:
            _environment("myapp", "standalone-nonsense")
        assert exc_info.value.code == 1
        assert "Cannot determine environment type" in capsys.readouterr().err

    def test_missing_app_name_returns_1(self, capsys, environment_stubs):
        """Test that --app-name is required for non-shared-infra templates."""
        assert _environment(None, "standalone-staging") == 1
        assert "--app-name is required for non-shared-infra templates" in capsys.readouterr().err

    def test_shared_infra_needs_no_app_name(self, capsys, environment_stubs, env_dir):
        """Test that shared-infra templates are exempt from the --app-name rule."""
        assert _environment(None, "shared-infra-staging") == 0
        capsys.readouterr()
        assert (env_dir / "shared-infra-staging" / "main.tf").exists()

    def test_existing_directory_returns_1(self, capsys, environment_stubs, env_dir):
        """Test that an existing environment directory stops a non-dry run."""
        (env_dir / "myapp-staging").mkdir(parents=True)
        assert _environment("myapp", "standalone-staging") == 1
        err = capsys.readouterr().err
        assert "Environment directory already exists" in err
        assert "Remove it first or use a different name." in err

    def test_missing_deploy_toml_returns_1(self, tmp_path, capsys, environment_stubs):
        """Test that an explicit --deploy-toml path must exist."""
        missing = tmp_path / "deploy.toml"
        assert _environment("myapp", "standalone-staging", deploy_toml=str(missing)) == 1
        assert f"deploy.toml not found at {missing}" in capsys.readouterr().err

    def test_generator_error_returns_1(self, monkeypatch, capsys, environment_stubs):
        """Test that a FileNotFoundError from the generator becomes 'Error: ...'."""

        def boom(**_kw):
            raise FileNotFoundError("template missing")

        monkeypatch.setattr(init_cli, "generate_environment", boom)
        assert _environment("myapp", "standalone-staging") == 1
        assert "Error: template missing" in capsys.readouterr().err


class TestCmdEnvironmentGeneration:
    """Tests for what cmd_environment() hands the generator and writes."""

    def test_generator_arguments(self, tmp_path, capsys, environment_stubs):
        """Test that every CLI option reaches generate_environment()."""
        deploy_toml = tmp_path / "deploy.toml"
        deploy_toml.write_text("[application]\n")
        assert (
            _environment(
                "myapp",
                "standalone-staging",
                deploy_toml=str(deploy_toml),
                domain="myapp.example.com",
            )
            == 0
        )
        capsys.readouterr()
        assert environment_stubs["generate"] == {
            "app_name": "myapp",
            "template_name": "standalone-staging",
            "deploy_toml_path": deploy_toml,
            "domain": "myapp.example.com",
            "listener_priority": None,
        }

    def test_shared_app_gets_an_auto_assigned_listener_priority(self, capsys, environment_stubs):
        """Test that shared-app templates ask for the next listener priority."""
        assert _environment("myapp", "shared-app-production") == 0
        capsys.readouterr()
        assert environment_stubs["priority"] == ["production"]
        assert environment_stubs["generate"]["listener_priority"] == 300

    def test_writes_every_generated_file(self, capsys, environment_stubs, env_dir):
        """Test that each path the generator returns is written and announced."""
        assert _environment("myapp", "standalone-staging") == 0
        out = capsys.readouterr().out
        env_path = env_dir / "myapp-staging"
        for filename in ENVIRONMENT_FILE_NAMES:
            assert (env_path / filename).read_text() == f"# {filename}\n"
            assert f"Created: {env_path / filename}" in out

    def test_standalone_creates_symlinks_and_deployer_tf(
        self, monkeypatch, capsys, environment_stubs, env_dir
    ):
        """Test the standalone-only symlink work."""
        monkeypatch.setattr(init_cli, "ensure_environments_symlinks", lambda: ["modules"])
        assert _environment("myapp", "standalone-staging") == 0
        out = capsys.readouterr().out
        env_path = env_dir / "myapp-staging"
        assert f"Created symlinks in {env_dir}: modules" in out
        assert environment_stubs["symlink"] == [env_path]
        assert f"Created: {env_path}/deployer.tf -> shared environment config" in out

    def test_shared_app_skips_the_standalone_symlinks(self, capsys, environment_stubs):
        """Test that non-standalone templates get no deployer.tf symlink."""
        assert _environment("myapp", "shared-app-staging") == 0
        capsys.readouterr()
        assert environment_stubs["symlink"] == []

    def test_dry_run_previews_without_writing(self, capsys, environment_stubs, env_dir):
        """Test that --dry-run shows the files and creates no directory."""
        assert _environment("myapp", "standalone-staging", dry_run=True) == 0
        out = capsys.readouterr().out
        assert f"Would create directory: {env_dir / 'myapp-staging'}" in out
        assert "# main.tf" in out
        assert not env_dir.exists()


# =============================================================================
# _print_next_steps
# =============================================================================


def _next_steps(capsys, template_name, app_name="myapp", env_type="staging"):
    """Run _print_next_steps() for a template and return its non-blank lines."""
    env_name = f"{app_name}-{env_type}" if app_name else template_name
    init_cli._print_next_steps(
        env_name, Path("/envs") / env_name, env_type, app_name, template_name
    )
    return _lines(capsys)


class TestPrintNextSteps:
    """Tests for _print_next_steps()'s per-template checklists."""

    def test_standalone_has_four_numbered_steps_in_order(self, capsys):
        """Test the standalone checklist: two edits, deploy, then secrets."""
        assert _next_steps(capsys, "standalone-staging") == [
            "Next steps:",
            "  1. Edit /envs/myapp-staging/terraform.tfvars:",
            "     - Set database credentials",
            "  2. Edit /envs/myapp-staging/services.auto.tfvars:",
            "     - Configure domain and Route53 zone ID",
            "     - Adjust service sizing if needed",
            "  3. Deploy infrastructure:",
            "     ./bin/tofu.sh plan myapp-staging",
            "     ./bin/tofu.sh apply myapp-staging",
            "  4. Create SSM secrets and deploy:",
            '     aws ssm put-parameter --name "/myapp/staging/secret-key" '
            '--value "..." --type SecureString',
            "     uv run python bin/deploy.py myapp-staging",
        ]

    def test_shared_infra_stops_after_the_deploy_step(self, capsys):
        """Test that shared-infra gets two steps and no SSM step."""
        assert _next_steps(capsys, "shared-infra-staging", app_name=None) == [
            "Next steps:",
            "  1. Edit /envs/shared-infra-staging/terraform.tfvars:",
            "     - Set domain and Route53 zone ID",
            "     - Configure Cognito if needed",
            "  2. Deploy infrastructure:",
            "     ./bin/tofu.sh plan shared-infra-staging",
            "     ./bin/tofu.sh apply shared-infra-staging",
        ]

    def test_shared_app_edits_one_file_and_checks_the_priority(self, capsys):
        """Test the shared-app checklist, including the priority-uniqueness check."""
        assert _next_steps(capsys, "shared-app-production", env_type="production") == [
            "Next steps:",
            "  1. Edit /envs/myapp-production/terraform.tfvars:",
            "     - Set database credentials",
            "     - Configure domain and Route53 zone ID",
            "     - Verify listener_rule_priority is unique",
            "  2. Deploy infrastructure:",
            "     ./bin/tofu.sh plan myapp-production",
            "     ./bin/tofu.sh apply myapp-production",
            "  3. Create SSM secrets and deploy:",
            '     aws ssm put-parameter --name "/myapp/production/secret-key" '
            '--value "..." --type SecureString',
            "     uv run python bin/deploy.py myapp-production",
        ]

    def test_no_app_name_drops_the_ssm_step(self, capsys):
        """Test that the SSM step needs an app name, not just a non-shared template."""
        lines = _next_steps(capsys, "shared-app-staging", app_name=None)
        assert not any("put-parameter" in line for line in lines)
        assert lines[-1] == "     ./bin/tofu.sh apply shared-app-staging"
