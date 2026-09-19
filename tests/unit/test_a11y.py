"""Pins for bin/a11y.py, the CloudFront error-page accessibility check.

The script renders `modules/cloudfront-alb/locals.tf`'s HTML heredoc the way
OpenTofu would and hands each environment variant to pa11y. These tests run the
real template through the real renderer -- the parsing is the part that can
silently drift when the heredoc grows a construct -- and stub the `npx` call, so
no node, no network and no pa11y download are involved.
"""

import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

bin_dir = Path(__file__).parent.parent.parent / "bin"
sys.path.insert(0, str(bin_dir))

_spec = spec_from_file_location("a11y_script", bin_dir / "a11y.py")
a11y = module_from_spec(_spec)
_spec.loader.exec_module(a11y)


class FakeNpx:
    """Record each pa11y argv and reply with a queue of exit codes."""

    def __init__(self, *returncodes: int):
        self.calls: list[list[str]] = []
        self.returncodes = list(returncodes)

    def __call__(self, cmd: list[str], check: bool = True):
        self.calls.append(cmd)
        code = self.returncodes.pop(0) if len(self.returncodes) > 1 else self.returncodes[0]
        return subprocess.CompletedProcess(cmd, code)


@pytest.fixture
def npx(monkeypatch):
    """Stub the subprocess seam main() reaches pa11y through."""

    def install(*returncodes: int) -> FakeNpx:
        fake = FakeNpx(*returncodes)
        monkeypatch.setattr(a11y.shutil, "which", lambda _name: "/usr/bin/npx")
        monkeypatch.setattr(a11y.subprocess, "run", fake)
        return fake

    return install


class TestTemplateParsing:
    """The renderer against the real locals.tf, not a synthetic fixture."""

    def test_reason_sets_cover_both_variants(self):
        text = a11y.LOCALS_TF.read_text(encoding="utf-8")
        reasons = a11y.read_reason_sets(text)
        assert set(reasons) == {"staging", "production"}
        assert all(reasons[variant] for variant in reasons)

    def test_render_expands_loop_and_prefix(self):
        text = a11y.LOCALS_TF.read_text(encoding="utf-8")
        template = a11y.read_heredoc(text)
        rendered = a11y.render(template, ["first reason", "second reason"])
        assert "first reason" in rendered
        assert "second reason" in rendered
        assert a11y.SAMPLE_NAME_PREFIX in rendered
        assert "${" not in rendered

    def test_render_rejects_an_unmodelled_construct(self):
        text = a11y.LOCALS_TF.read_text(encoding="utf-8")
        template = a11y.read_heredoc(text).replace("</body>", "${var.grew_a_feature}</body>")
        with pytest.raises(SystemExit, match="grew a feature"):
            a11y.render(template, ["only reason"])

    def test_missing_heredoc_is_fatal(self):
        with pytest.raises(SystemExit, match="heredoc not found"):
            a11y.read_heredoc("locals {\n  unrelated = 1\n}\n")


class TestMain:
    """The check loop: one pa11y run per variant, exit code from the failures."""

    def test_runs_pa11y_once_per_variant(self, npx):
        fake = npx(0)
        assert a11y.main() == 0
        assert len(fake.calls) == 2
        for cmd in fake.calls:
            assert cmd[:5] == [
                "npx",
                "--yes",
                a11y.PA11Y_SPEC,
                "--standard",
                a11y.PA11Y_STANDARD,
            ]
            assert cmd[5].startswith("file://")
        variants = sorted(cmd[5].rsplit("/", 1)[-1] for cmd in fake.calls)
        assert variants == ["error-503-production.html", "error-503-staging.html"]

    def test_one_failing_variant_fails_the_check(self, npx, capsys):
        npx(2, 0)
        assert a11y.main() == 1
        assert "1 error-page variant(s) failed" in capsys.readouterr().out

    def test_every_failing_variant_is_counted(self, npx, capsys):
        npx(2)
        assert a11y.main() == 1
        assert "2 error-page variant(s) failed" in capsys.readouterr().out

    def test_missing_npx_is_fatal(self, monkeypatch):
        monkeypatch.setattr(a11y.shutil, "which", lambda _name: None)
        with pytest.raises(SystemExit, match="npx not found"):
            a11y.main()
