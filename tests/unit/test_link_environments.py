"""Characterization tests for bin/link-environments.py.

These pin the CLI's behaviour as it stands: which arguments link, list, show
and unlink, what each prints, and what lands in local/environments.toml. Every
command runs through the real Click entry point and the real links module; the
only seams are the deployer root (where local/ lives), DEPLOYER_ENVIRONMENTS_DIR
and HOME, all pointed into tmp_path.
"""

import sys
import tomllib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from click.testing import CliRunner

from deployer.utils import links

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("link_environments", bin_dir / "link-environments.py")
link_environments = module_from_spec(_spec)
_spec.loader.exec_module(link_environments)

ENV = "myapp-staging"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """Point HOME at a directory under tmp_path, where the deploy.toml files live."""
    home_dir = tmp_path.resolve() / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    return home_dir


@pytest.fixture
def links_file(tmp_path, monkeypatch) -> Path:
    """Redirect local/environments.toml into tmp_path (not created).

    get_deployer_root is the seam: get_links_file() builds the path from it, and
    the script reaches get_links_file through the links module either way.
    """
    root = tmp_path / "deployer"
    monkeypatch.setattr(links, "get_deployer_root", lambda: root)
    return root / "local" / "environments.toml"


@pytest.fixture
def environments_dir(tmp_path, monkeypatch) -> Path:
    """An environments directory holding one deployed environment, ENV."""
    env_dir = tmp_path / "environments"
    (env_dir / ENV).mkdir(parents=True)
    (env_dir / ENV / "terraform.tfstate").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DEPLOYER_ENVIRONMENTS_DIR", str(env_dir))
    return env_dir


@pytest.fixture
def deploy_toml(home) -> Path:
    """A deploy.toml inside the fake home directory."""
    path = home / "myapp" / "deploy.toml"
    path.parent.mkdir()
    path.write_text('[application]\nname = "myapp"\n', encoding="utf-8")
    return path


def _invoke(*args: str):
    return CliRunner().invoke(link_environments.cli, list(args))


def _stored(links_file: Path) -> dict:
    with open(links_file, "rb") as f:
        return tomllib.load(f)


class TestHelp:
    """--help, and the bare invocation that falls back to it."""

    def test_help_shows_the_placeholder_example_path(self):
        result = _invoke("--help")

        assert result.exit_code == 0
        assert "link-environments.py myapp-staging /path/to/myapp/deploy.toml" in result.stdout
        assert "~/code/" not in result.stdout

    def test_help_lists_every_option(self):
        result = _invoke("--help")

        for option in ("--list", "--show-file", "--unlink"):
            assert option in result.stdout

    def test_no_arguments_prints_help_and_exits_1(self):
        result = _invoke()

        assert result.exit_code == 1
        assert "Link environments to deploy.toml files." in result.stdout

    def test_environment_without_deploy_toml_prints_help_and_exits_1(self, links_file):
        result = _invoke(ENV)

        assert result.exit_code == 1
        assert "Link environments to deploy.toml files." in result.stdout
        assert not links_file.exists()


class TestLink:
    """link-environments.py <environment> <deploy_toml>."""

    def test_links_and_displays_the_path_relative_to_home(
        self, links_file, environments_dir, deploy_toml
    ):
        result = _invoke(ENV, str(deploy_toml))

        assert result.exit_code == 0
        assert result.stdout == f"Linked: {ENV} -> ~/myapp/deploy.toml\n"
        assert _stored(links_file) == {ENV: {"deploy_toml": "~/myapp/deploy.toml"}}
        assert links.get_linked_deploy_toml(ENV) == deploy_toml

    def test_path_outside_home_is_stored_and_displayed_absolute(
        self, tmp_path, home, links_file, environments_dir
    ):
        outside = tmp_path.resolve() / "elsewhere" / "deploy.toml"
        outside.parent.mkdir()
        outside.write_text("", encoding="utf-8")

        result = _invoke(ENV, str(outside))

        assert result.exit_code == 0
        assert result.stdout == f"Linked: {ENV} -> {outside}\n"
        assert _stored(links_file) == {ENV: {"deploy_toml": str(outside)}}

    def test_relative_path_is_resolved_against_the_working_directory(
        self, monkeypatch, home, links_file, environments_dir, deploy_toml
    ):
        monkeypatch.chdir(home / "myapp")

        result = _invoke(ENV, "../myapp/deploy.toml")

        assert result.exit_code == 0
        assert _stored(links_file) == {ENV: {"deploy_toml": "~/myapp/deploy.toml"}}

    def test_tilde_argument_is_expanded(self, links_file, environments_dir, deploy_toml):
        result = _invoke(ENV, "~/myapp/deploy.toml")

        assert result.exit_code == 0
        assert _stored(links_file) == {ENV: {"deploy_toml": "~/myapp/deploy.toml"}}

    def test_relinking_overwrites_the_previous_path(
        self, home, links_file, environments_dir, deploy_toml
    ):
        _invoke(ENV, str(deploy_toml))
        other = home / "myapp-v2" / "deploy.toml"
        other.parent.mkdir()
        other.write_text("", encoding="utf-8")

        result = _invoke(ENV, str(other))

        assert result.exit_code == 0
        assert result.stdout == f"Linked: {ENV} -> ~/myapp-v2/deploy.toml\n"
        assert _stored(links_file) == {ENV: {"deploy_toml": "~/myapp-v2/deploy.toml"}}

    def test_linking_keeps_other_environments_links(
        self, links_file, environments_dir, deploy_toml
    ):
        links_file.parent.mkdir(parents=True)
        links_file.write_text(
            '[otherapp-staging]\ndeploy_toml = "~/otherapp/deploy.toml"\n', encoding="utf-8"
        )

        result = _invoke(ENV, str(deploy_toml))

        assert result.exit_code == 0
        assert _stored(links_file) == {
            "otherapp-staging": {"deploy_toml": "~/otherapp/deploy.toml"},
            ENV: {"deploy_toml": "~/myapp/deploy.toml"},
        }

    def test_file_without_toml_suffix_warns_but_still_links(
        self, home, links_file, environments_dir
    ):
        odd = home / "myapp" / "deploy.cfg"
        odd.parent.mkdir()
        odd.write_text("", encoding="utf-8")

        result = _invoke(ENV, str(odd))

        assert result.exit_code == 0
        assert result.stderr == f"Warning: File does not end with .toml: {odd}\n"
        assert _stored(links_file) == {ENV: {"deploy_toml": "~/myapp/deploy.cfg"}}

    def test_missing_environment_directory_exits_1_without_linking(
        self, links_file, environments_dir, deploy_toml
    ):
        result = _invoke("otherapp-staging", str(deploy_toml))

        assert result.exit_code == 1
        assert result.stderr == (
            f"Error: Environment directory not found: {environments_dir / 'otherapp-staging'}\n"
        )
        assert not links_file.exists()

    def test_undeployed_environment_exits_1_without_linking(
        self, links_file, environments_dir, deploy_toml
    ):
        (environments_dir / ENV / "terraform.tfstate").unlink()

        result = _invoke(ENV, str(deploy_toml))

        assert result.exit_code == 1
        assert result.stderr == f"Error: Environment '{ENV}' is not deployed\n"
        assert not links_file.exists()

    def test_missing_deploy_toml_exits_1_without_linking(self, home, links_file, environments_dir):
        missing = home / "myapp" / "deploy.toml"

        result = _invoke(ENV, str(missing))

        assert result.exit_code == 1
        assert result.stderr == f"Error: File not found: {missing}\n"
        assert not links_file.exists()


class TestList:
    """--list / -l."""

    def test_no_links_file_prints_how_to_link(self, links_file):
        result = _invoke("--list")

        assert result.exit_code == 0
        assert result.stdout == (
            "No environments linked.\n"
            "\nTo link an environment:\n"
            "  python bin/link-environments.py <environment> <path/to/deploy.toml>\n"
        )

    def test_lists_links_sorted_with_the_file_location(self, links_file):
        links_file.parent.mkdir(parents=True)
        links_file.write_text(
            '[otherapp-staging]\ndeploy_toml = "~/otherapp/deploy.toml"\n'
            '[myapp-staging]\ndeploy_toml = "~/myapp/deploy.toml"\n',
            encoding="utf-8",
        )

        result = _invoke("-l")

        assert result.exit_code == 0
        assert result.stdout == (
            "Environment links:\n"
            + "-" * 60
            + "\n"
            + "  myapp-staging -> ~/myapp/deploy.toml\n"
            + "  otherapp-staging -> ~/otherapp/deploy.toml\n"
            + f"\nStored in: {links_file}\n"
        )


class TestShowFile:
    """--show-file."""

    def test_reports_location_and_absence(self, links_file):
        result = _invoke("--show-file")

        assert result.exit_code == 0
        assert result.stdout == f"Links file: {links_file}\nExists: False\n"

    def test_reports_presence_once_written(self, links_file, environments_dir, deploy_toml):
        _invoke(ENV, str(deploy_toml))

        result = _invoke("--show-file")

        assert result.stdout == f"Links file: {links_file}\nExists: True\n"


class TestUnlink:
    """--unlink / -u."""

    def test_requires_an_environment(self, links_file):
        result = _invoke("--unlink")

        assert result.exit_code == 1
        assert result.stderr == "Error: environment is required with --unlink\n"

    def test_unlinked_environment_exits_1(self, links_file):
        result = _invoke("--unlink", ENV)

        assert result.exit_code == 1
        assert result.stderr == f"No link found for '{ENV}'\n"

    def test_removes_only_that_environments_link(
        self, home, links_file, environments_dir, deploy_toml
    ):
        links_file.parent.mkdir(parents=True)
        links_file.write_text(
            '[otherapp-staging]\ndeploy_toml = "~/otherapp/deploy.toml"\n', encoding="utf-8"
        )
        _invoke(ENV, str(deploy_toml))

        result = _invoke("-u", ENV)

        assert result.exit_code == 0
        assert result.stdout == f"Unlinked: {ENV} (was -> {deploy_toml})\n"
        assert _stored(links_file) == {
            "otherapp-staging": {"deploy_toml": "~/otherapp/deploy.toml"}
        }


CORRUPT = '[otherapp-staging\ndeploy_toml = "~/otherapp/deploy.toml"\n'


class TestCorruptLinksFile:
    """A links file that will not parse is "could not look", never "no links".

    The reader already raised (get_linked_deploy_toml); the writer, the lister
    and unlink each answered the corrupt file as empty -- and the writer then
    overwrote it, dropping every other environment's link.
    """

    @pytest.fixture
    def corrupt(self, links_file) -> Path:
        links_file.parent.mkdir(parents=True)
        links_file.write_text(CORRUPT, encoding="utf-8")
        return links_file

    def test_link_refuses_rather_than_overwriting_the_other_links(
        self, corrupt, environments_dir, deploy_toml
    ):
        result = _invoke(ENV, str(deploy_toml))

        assert result.exit_code == 1
        assert "Could not read the links file" in result.stderr
        assert corrupt.read_text(encoding="utf-8") == CORRUPT

    def test_list_exits_1_rather_than_saying_nothing_is_linked(self, corrupt):
        result = _invoke("--list")

        assert result.exit_code == 1
        assert "Could not read the links file" in result.stderr
        assert "No environments linked." not in result.stdout

    def test_unlink_raises_rather_than_reading_as_not_linked(self, corrupt):
        with pytest.raises(RuntimeError, match="Could not read the links file"):
            links.unlink_deploy_toml(ENV)
        assert corrupt.read_text(encoding="utf-8") == CORRUPT
