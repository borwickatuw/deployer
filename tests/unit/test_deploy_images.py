"""Characterization tests for deployer.deploy.images.

These are characterization pins, not endorsements. They record what
``deploy/images.py`` does **today** so that the 53e-4b refactor can be shown to
be behaviour-preserving. Where the current behaviour looks wrong it is pinned
anyway, and called out in a comment on the test.

What is pinned here:

* Every one of the ten module functions. Eight of them were 100% unexecuted
  before this file existed: ``_run_timed_subprocess``, ``_check_subprocess_result``,
  ``parse_dockerignore``, ``should_ignore``, ``compute_context_hash``,
  ``image_exists_in_ecr``, ``ecr_login`` and the whole body of
  ``build_and_push_images``. ``validate_ecr_repositories``'s ``ImageConfig``
  arm was the ninth gap; the pre-existing ``test_images.py`` only ever passed
  raw dicts.

Five pins exist specifically to make 53e-4b's extractions verifiable:

1. **The ``tag_cmd`` failure-mode divergence.** ``docker tag`` is the one
   subprocess call in the module that goes through a bare
   ``subprocess.run(..., check=True)``: no captured output, no timer sub-step,
   no ``_check_subprocess_result``. A tag failure therefore raises
   ``CalledProcessError`` and prints nothing, while a build or push failure
   raises ``RuntimeError`` after echoing stdout and stderr. Collapsing the
   three dry-run/execute blocks into one helper would silently change that.
   ``TestTagFailureModeDiverges`` pins both shapes.
2. **Both arms of the ``DeployConfig``-vs-``dict`` dispatch**, and that they
   now *agree*. They used to diverge: the inline "legacy" copy lacked the
   ``isinstance(env_override, dict)`` guard that ``ImageConfig.get_build_args``
   had, so a non-dict ``build_args.<environment>`` raised on one path with a
   message naming neither the image nor the key, and was silently passed
   through as a literal build arg on the other. Both route through
   ``config.merge_build_args`` and ``_resolve_context``;
   ``TestBuildArgsDispatchAgrees`` pins that the two messages are identical.
3. **The cache-hit ``continue``** — the loop's only early exit, gated on
   ``ecr_client and not dry_run and not force_build``. All four gate states are
   pinned in ``TestCacheHit``.
4. **``hash_modifiers`` ordering.** The list feeds a SHA-256 digest, so
   swapping ``args:`` and ``target:`` changes every cache tag and would force a
   fleet-wide rebuild. ``TestHashModifiers`` pins the order against a digest
   computed from the literal string, and asserts the reversed order produces a
   different tag.
5. **Both timer arms**, mirroring 53e-3a's ``TestDeployTimerArmsAgree``.
   ``images.py`` reads the module-global via ``get_timer()``, so the two real
   arms are "a ``DeploymentTimer`` inside a ``step()`` context" and "``None``".
   ``TestTimerArmsAgree`` asserts the two produce byte-identical subprocess
   calls and byte-identical output. ``NullTimer`` is a *substitutable* third
   arm rather than a distinct one: it answers ``in_step`` False, so it takes
   the untimed path — pinned in ``TestRunTimedSubprocess``.

Stubbing is at the outermost boundary — ``subprocess.run``, ``subprocess.Popen``
and the real filesystem — never at a ``deployer`` binding. That is 53d-2a's
recorded rule: pins that stub the stdlib survive code motion inside the package.
The build context, ``.dockerignore`` and ``Dockerfile`` are all real files under
``tmp_path``, so the hashing pins exercise the real ``rglob`` walk.

Production caller note, recorded while writing these: ``deployer.py:112`` does
``self.config = self.deploy_config.get_raw_dict()``, so the sole production
caller of ``build_and_push_images`` always passes a **raw dict** — the
``DeployConfig``/``ImageConfig`` arms are dead in production and the inline
"legacy" branch is the live path. ``validate_ecr_repositories`` is the opposite:
``preflight.py:139`` hands it a real ``DeployConfig``. Both arms are pinned
regardless.
"""

import base64
import hashlib
import os
import re
import subprocess

import pytest
from botocore.exceptions import ClientError

from deployer.config import (
    ApplicationConfig,
    AuditConfig,
    DeployConfig,
    ImageConfig,
    MigrationConfig,
)
from deployer.deploy.images import (
    _check_subprocess_result,
    _run_timed_subprocess,
    build_and_push_images,
    compute_context_hash,
    ecr_login,
    image_exists_in_ecr,
    parse_dockerignore,
    should_ignore,
    validate_ecr_repositories,
)
from deployer.timing import DeploymentTimer, NullTimer, set_timer

ACCOUNT_ID = "123456789012"
REGION = "us-west-2"
ECR_PREFIX = "testapp-staging"
ENVIRONMENT = "staging"
REGISTRY = f"https://{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com"
ECR_HOST = f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com"

_ANSI = re.compile(r"\033\[[0-9;]*m")


def _plain(captured: str) -> str:
    """Strip ANSI colour from a captured stream."""
    return _ANSI.sub("", captured)


def _lines(captured: str) -> list[str]:
    """Return a captured stream's non-blank lines, without ANSI colour."""
    return [line for line in _plain(captured).splitlines() if line.strip()]


def _client_error(code: str, operation: str = "DescribeImages") -> ClientError:
    """Build a botocore ClientError carrying the given error code."""
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


def _expected_tag(
    context,
    dockerfile: str = "Dockerfile",
    build_args: dict | None = None,
    target: str | None = None,
    additional_contexts: dict | None = None,
) -> str:
    """Recompute the cache tag the way build_and_push_images does.

    Written out longhand rather than imported, so that a change to the digest
    recipe fails these tests instead of being mirrored into them.
    """
    content_hash = compute_context_hash(context, dockerfile)
    modifiers = []
    if build_args:
        args_str = ",".join(f"{k}={v}" for k, v in sorted(build_args.items()))
        modifiers.append(f"args:{args_str}")
    if target:
        modifiers.append(f"target:{target}")
    for name, path in sorted((additional_contexts or {}).items()):
        modifiers.append(f"context:{name}:{compute_context_hash(path, None)}")
    if modifiers:
        combined = f"{content_hash}:{';'.join(modifiers)}"
        return hashlib.sha256(combined.encode()).hexdigest()[:12]
    return content_hash


class _RunRecorder:
    """Stands in for ``subprocess.run``, recording argv and kwargs verbatim."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []
        # Keyed on the docker verb (build/tag/push); anything absent succeeds.
        self.returncodes: dict[str, int] = {}
        self.stdout: dict[str, str] = {}
        self.stderr: dict[str, str] = {}

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        verb = cmd[1] if len(cmd) > 1 else ""
        returncode = self.returncodes.get(verb, 0)
        result = subprocess.CompletedProcess(
            args=list(cmd),
            returncode=returncode,
            stdout=self.stdout.get(verb, ""),
            stderr=self.stderr.get(verb, ""),
        )
        # Mirror the real subprocess.run: check=True raises on failure.
        if kwargs.get("check") and returncode != 0:
            raise subprocess.CalledProcessError(returncode, list(cmd))
        return result

    @property
    def argv(self) -> list[list[str]]:
        return [cmd for cmd, _ in self.calls]

    @property
    def verbs(self) -> list[str]:
        return [cmd[1] for cmd, _ in self.calls]

    @property
    def kwargs(self) -> list[dict]:
        return [kw for _, kw in self.calls]


class _FakeProc:
    """The Popen object ecr_login drives: communicate() then returncode."""

    def __init__(self, recorder):
        self._recorder = recorder
        self.returncode = None

    def communicate(self, input=None):  # noqa: A002 — mirrors Popen.communicate
        self._recorder.stdin_payloads.append(input)
        self.returncode = self._recorder.returncode
        return (None, self._recorder.stderr)


class _PopenRecorder:
    """Stands in for ``subprocess.Popen``."""

    def __init__(self):
        self.calls: list[tuple[list[str], dict]] = []
        self.stdin_payloads: list[bytes | None] = []
        self.returncode = 0
        self.stderr: bytes | None = None

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs)))
        return _FakeProc(self)


class _FakeEcr:
    """Stands in for a boto3 ECR client."""

    def __init__(self):
        self.describe_images_calls: list[dict] = []
        self.describe_repositories_calls: list[dict] = []
        self.token_calls = 0
        self.existing: set[tuple[str, str]] = set()
        self.describe_images_error: Exception | None = None
        self.authorization_data = [
            {
                "proxyEndpoint": REGISTRY,
                "authorizationToken": base64.b64encode(b"AWS:s3cr3t").decode(),
            }
        ]

    def describe_images(self, **kwargs):
        self.describe_images_calls.append(kwargs)
        if self.describe_images_error is not None:
            raise self.describe_images_error
        repo = kwargs["repositoryName"]
        tag = kwargs["imageIds"][0]["imageTag"]
        if (repo, tag) in self.existing:
            return {"imageDetails": [{"imageTags": [tag]}]}
        raise _client_error("ImageNotFoundException")

    def describe_repositories(self, **kwargs):
        self.describe_repositories_calls.append(kwargs)
        return {"repositories": [{"repositoryName": kwargs["repositoryNames"][0]}]}

    def get_authorization_token(self):
        self.token_calls += 1
        return {"authorizationData": self.authorization_data}


@pytest.fixture(autouse=True)
def _reset_global_timer():
    """images.py reads a process-global timer; don't leak one into other tests."""
    yield
    set_timer(None)


@pytest.fixture
def run(monkeypatch) -> _RunRecorder:
    """Stub subprocess.run at the stdlib, the outermost boundary."""
    recorder = _RunRecorder()
    monkeypatch.setattr(subprocess, "run", recorder)
    return recorder


@pytest.fixture
def popen(monkeypatch) -> _PopenRecorder:
    """Stub subprocess.Popen at the stdlib, the outermost boundary."""
    recorder = _PopenRecorder()
    monkeypatch.setattr(subprocess, "Popen", recorder)
    return recorder


@pytest.fixture
def ecr() -> _FakeEcr:
    return _FakeEcr()


@pytest.fixture
def source_dir(tmp_path):
    """A source tree with two real build contexts, ``web`` and ``base``."""
    for name in ("web", "base"):
        context = tmp_path / name
        context.mkdir()
        (context / "Dockerfile").write_text(f"FROM scratch\n# {name}\n", encoding="utf-8")
        (context / "app.py").write_text(f"print('{name}')\n", encoding="utf-8")
    return tmp_path


def _dict_config(**images) -> dict:
    """A raw-dict config — the shape production actually passes."""
    return {"images": images}


def _deploy_config(**images) -> DeployConfig:
    """A DeployConfig carrying ImageConfig objects — the preflight shape."""
    return DeployConfig(
        application=ApplicationConfig(name="testapp"),
        images=images,
        services={},
        migrations=MigrationConfig(),
        audit=AuditConfig(),
    )


def _build(config, source_dir, ecr_client=None, **kwargs) -> dict[str, str]:
    """Call build_and_push_images with this suite's fixed AWS coordinates."""
    return build_and_push_images(
        config,
        source_dir,
        ECR_PREFIX,
        ACCOUNT_ID,
        REGION,
        ENVIRONMENT,
        ecr_client,
        **kwargs,
    )


class TestRunTimedSubprocess:
    """Pins for _run_timed_subprocess's two timer arms."""

    def test_no_timer_runs_the_command_captured_unchecked(self, run):
        set_timer(None)

        result = _run_timed_subprocess(["docker", "build", "."], "web_build")

        assert run.argv == [["docker", "build", "."]]
        assert run.kwargs == [{"capture_output": True, "text": True, "check": False}]
        assert result.returncode == 0

    def test_a_timer_outside_a_step_takes_the_untimed_arm(self, run):
        """``timer._current_step`` is None outside step(), so no sub_step is opened."""
        timer = DeploymentTimer("run-1")
        set_timer(timer)

        _run_timed_subprocess(["docker", "build", "."], "web_build")

        assert run.kwargs == [{"capture_output": True, "text": True, "check": False}]
        assert timer.report.steps == []

    def test_a_timer_inside_a_step_records_a_named_sub_step(self, run):
        timer = DeploymentTimer("run-1")
        set_timer(timer)

        with timer.step("build_and_push_images"):
            _run_timed_subprocess(["docker", "build", "."], "web_build")

        (step,) = timer.report.steps
        assert [sub.name for sub in step.sub_steps] == ["web_build"]
        assert step.sub_steps[0].success is True

    def test_both_arms_issue_the_identical_subprocess_call(self, run):
        """The pin that makes collapsing the two arms verifiable."""
        set_timer(None)
        _run_timed_subprocess(["docker", "push", "x"], "web_push")
        untimed = run.calls[:]

        run.calls.clear()
        timer = DeploymentTimer("run-1")
        set_timer(timer)
        with timer.step("build_and_push_images"):
            _run_timed_subprocess(["docker", "push", "x"], "web_push")

        assert run.calls == untimed

    def test_a_raising_command_marks_the_sub_step_failed_and_propagates(self, monkeypatch):
        def boom(*args, **kwargs):
            raise FileNotFoundError("docker")

        monkeypatch.setattr(subprocess, "run", boom)
        timer = DeploymentTimer("run-1")
        set_timer(timer)

        with pytest.raises(FileNotFoundError), timer.step("build_and_push_images"):
            _run_timed_subprocess(["docker", "build", "."], "web_build")

        (step,) = timer.report.steps
        assert step.sub_steps[0].success is False
        assert step.sub_steps[0].error == "docker"

    def test_a_null_timer_set_globally_runs_the_command(self, run):
        """A NullTimer used to raise here: truthy, but with no _current_step.

        The reach for that private attribute is what made the null object
        unsubstitutable. It now answers ``in_step`` like anything else, so
        installing one globally runs the pipeline untimed rather than crashing.
        """
        set_timer(NullTimer())

        result = _run_timed_subprocess(["docker", "build", "."], "web_build")

        assert run.argv == [["docker", "build", "."]]
        assert run.kwargs == [{"capture_output": True, "text": True, "check": False}]
        assert result.returncode == 0

    def test_a_null_timer_inside_its_own_step_still_runs_the_command(self, run):
        """The sub_step arm: NullTimer.step() opens nothing, so in_step stays
        False — but NullTimer carries a sub_step anyway, so a future caller that
        does reach it gets a no-op rather than an AttributeError."""
        timer = NullTimer()
        set_timer(timer)

        with timer.step("build_and_push_images"), timer.sub_step("web_build") as sub:
            _run_timed_subprocess(["docker", "build", "."], "web_build")

        assert sub.name == "web_build"
        assert run.argv == [["docker", "build", "."]]

    def test_the_real_timer_still_requires_a_step_around_sub_step(self, run):
        """The contrast NullTimer.sub_step deliberately does not copy: the real
        timer has nowhere to file a sub-step outside one, so it refuses."""
        timer = DeploymentTimer("run-1")

        with (
            pytest.raises(RuntimeError, match="within a step context"),
            timer.sub_step("web_build"),
        ):
            pass


class TestCheckSubprocessResult:
    """Pins for the build/push failure reporting shape."""

    @staticmethod
    def _result(returncode, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_a_zero_return_code_is_silent(self, capsys):
        assert _check_subprocess_result(self._result(0, "out", "err"), "web", "build") is None
        assert capsys.readouterr().out == ""

    def test_a_failure_echoes_stdout_then_stderr_then_raises(self, capsys):
        with pytest.raises(RuntimeError) as exc:
            _check_subprocess_result(self._result(1, "the-stdout", "the-stderr"), "web", "build")

        assert str(exc.value) == "Build failed for web"
        assert _lines(capsys.readouterr().out) == [
            "  ✗ Build failed for web",
            "the-stdout",
            "the-stderr",
        ]

    def test_empty_streams_are_not_printed(self, capsys):
        with pytest.raises(RuntimeError):
            _check_subprocess_result(self._result(2), "web", "push")

        assert _lines(capsys.readouterr().out) == ["  ✗ Push failed for web"]

    def test_only_stderr_is_printed_when_stdout_is_empty(self, capsys):
        with pytest.raises(RuntimeError):
            _check_subprocess_result(self._result(1, "", "boom"), "web", "push")

        assert _lines(capsys.readouterr().out) == ["  ✗ Push failed for web", "boom"]

    def test_the_operation_word_is_capitalized_in_both_the_log_and_the_error(self, capsys):
        with pytest.raises(RuntimeError) as exc:
            _check_subprocess_result(self._result(1), "worker", "push")

        assert str(exc.value) == "Push failed for worker"
        assert "Push failed for worker" in _plain(capsys.readouterr().out)


class TestParseDockerignore:
    """Pins for .dockerignore parsing."""

    def test_a_context_without_a_dockerignore_still_ignores_git(self, tmp_path):
        assert parse_dockerignore(tmp_path) == [".git"]

    def test_git_is_always_first_then_the_file_in_order(self, tmp_path):
        (tmp_path / ".dockerignore").write_text("node_modules\n*.pyc\nbuild/\n", encoding="utf-8")

        assert parse_dockerignore(tmp_path) == [".git", "node_modules", "*.pyc", "build/"]

    def test_blank_lines_and_comments_are_skipped(self, tmp_path):
        (tmp_path / ".dockerignore").write_text(
            "\n# a comment\n\n  *.log  \n#another\n", encoding="utf-8"
        )

        assert parse_dockerignore(tmp_path) == [".git", "*.log"]

    def test_a_hash_that_is_not_the_first_character_is_kept(self, tmp_path):
        (tmp_path / ".dockerignore").write_text("foo#bar\n", encoding="utf-8")

        assert parse_dockerignore(tmp_path) == [".git", "foo#bar"]

    def test_negation_lines_survive_parsing(self, tmp_path):
        """parse_dockerignore does not filter ``!``; should_ignore skips them later."""
        (tmp_path / ".dockerignore").write_text("*\n!keep.txt\n", encoding="utf-8")

        assert parse_dockerignore(tmp_path) == [".git", "*", "!keep.txt"]

    def test_a_dockerignore_listing_git_yields_it_once(self, tmp_path):
        """The implicit ``.git`` and an explicit one collapse to a single pattern."""
        (tmp_path / ".dockerignore").write_text(".git\n", encoding="utf-8")

        assert parse_dockerignore(tmp_path) == [".git"]

    def test_an_ordinary_repeated_pattern_is_de_duplicated_too(self, tmp_path):
        """De-duplication is not special-cased to ``.git``; first occurrence wins."""
        (tmp_path / ".dockerignore").write_text("*.log\nbuild/\n*.log\n", encoding="utf-8")

        assert parse_dockerignore(tmp_path) == [".git", "*.log", "build/"]

    def test_an_empty_dockerignore_yields_only_git(self, tmp_path):
        (tmp_path / ".dockerignore").write_text("", encoding="utf-8")

        assert parse_dockerignore(tmp_path) == [".git"]


class TestShouldIgnore:
    """Pins for the fnmatch branches."""

    def test_no_patterns_means_nothing_is_ignored(self, tmp_path):
        assert should_ignore(tmp_path / "a.txt", tmp_path, []) is False

    def test_a_non_matching_pattern_returns_false(self, tmp_path):
        assert should_ignore(tmp_path / "a.txt", tmp_path, ["*.pyc"]) is False

    def test_negation_patterns_are_skipped_entirely(self, tmp_path):
        """Pinned, not endorsed: ``!a.txt`` does not re-include, it is just ignored."""
        assert should_ignore(tmp_path / "a.txt", tmp_path, ["!a.txt"]) is False
        assert should_ignore(tmp_path / "a.txt", tmp_path, ["*", "!a.txt"]) is True

    def test_a_directory_pattern_loses_its_trailing_slash(self, tmp_path):
        assert should_ignore(tmp_path / "build" / "out.o", tmp_path, ["build/"]) is True

    def test_a_prefix_directory_matches_everything_under_it(self, tmp_path):
        assert should_ignore(tmp_path / ".git" / "config", tmp_path, [".git"]) is True

    def test_a_bare_name_matches_a_nested_component(self, tmp_path):
        """The per-component fnmatch: ``target.txt`` matches ``sub/target.txt``."""
        assert should_ignore(tmp_path / "sub" / "target.txt", tmp_path, ["target.txt"]) is True

    def test_a_glob_matches_across_separators(self, tmp_path):
        """fnmatch's ``*`` spans ``/``, so ``*.pyc`` matches any depth."""
        assert should_ignore(tmp_path / "a" / "b" / "c.pyc", tmp_path, ["*.pyc"]) is True

    def test_the_top_level_file_matches_on_the_first_component(self, tmp_path):
        assert should_ignore(tmp_path / "a.pyc", tmp_path, ["*.pyc"]) is True

    def test_the_first_matching_pattern_wins(self, tmp_path):
        assert should_ignore(tmp_path / "a.txt", tmp_path, ["nope", "a.txt", "also-nope"]) is True

    def test_the_context_root_itself_is_no_longer_special_cased(self, tmp_path):
        """The context root matches nothing, because it has no path components.

        A trailing ``fnmatch(str(rel_path), pattern)`` used to sit after the
        loop. For any file under the context it was dead — the last iteration
        already tests the full relative path against the same pattern — and it
        could only fire when ``rel_path.parts`` is empty, i.e. when the path
        *is* the context directory, whose relative path is ``"."``.
        ``compute_context_hash`` never passes that, because ``rglob`` does not
        yield the root, so removing it changed no digest.
        """
        assert should_ignore(tmp_path, tmp_path, ["*"]) is False
        assert should_ignore(tmp_path, tmp_path, ["."]) is False
        assert should_ignore(tmp_path, tmp_path, ["nope"]) is False

    def test_a_path_outside_the_context_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not in the subpath|does not start with"):
            should_ignore(tmp_path.parent / "elsewhere.txt", tmp_path / "ctx", ["*"])


class TestComputeContextHash:
    """Pins for the content hash that drives the ECR cache tag."""

    def test_the_hash_is_twelve_hex_characters(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

        digest = compute_context_hash(tmp_path, "Dockerfile")

        assert len(digest) == 12
        assert re.fullmatch(r"[0-9a-f]{12}", digest)

    def test_the_same_content_hashes_the_same(self, tmp_path):
        for name in ("a", "b"):
            context = tmp_path / name
            context.mkdir()
            (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (context / "app.py").write_text("x = 1\n", encoding="utf-8")

        assert compute_context_hash(tmp_path / "a", "Dockerfile") == compute_context_hash(
            tmp_path / "b", "Dockerfile"
        )

    def test_creation_order_does_not_change_the_hash(self, tmp_path):
        first = tmp_path / "first"
        first.mkdir()
        (first / "z.py").write_text("z\n", encoding="utf-8")
        (first / "a.py").write_text("a\n", encoding="utf-8")

        second = tmp_path / "second"
        second.mkdir()
        (second / "a.py").write_text("a\n", encoding="utf-8")
        (second / "z.py").write_text("z\n", encoding="utf-8")

        assert compute_context_hash(first, "Dockerfile") == compute_context_hash(
            second, "Dockerfile"
        )

    def test_changing_the_dockerfile_changes_the_hash(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        (tmp_path / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")

        assert compute_context_hash(tmp_path, "Dockerfile") != before

    def test_a_missing_dockerfile_is_not_an_error(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

        assert len(compute_context_hash(tmp_path, "Dockerfile")) == 12

    def test_naming_a_different_dockerfile_changes_the_hash(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (tmp_path / "Dockerfile.dev").write_text("FROM alpine\n", encoding="utf-8")

        assert compute_context_hash(tmp_path, "Dockerfile") != compute_context_hash(
            tmp_path, "Dockerfile.dev"
        )

    def test_changing_a_context_file_changes_the_hash(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")

        assert compute_context_hash(tmp_path, "Dockerfile") != before

    def test_renaming_a_file_changes_the_hash(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        (tmp_path / "app.py").rename(tmp_path / "main.py")

        assert compute_context_hash(tmp_path, "Dockerfile") != before

    def test_nested_files_are_included(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        nested = tmp_path / "pkg" / "deep"
        nested.mkdir(parents=True)
        (nested / "mod.py").write_text("y = 1\n", encoding="utf-8")

        assert compute_context_hash(tmp_path, "Dockerfile") != before

    def test_an_ignored_file_does_not_affect_the_hash(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (tmp_path / ".dockerignore").write_text("*.log\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        (tmp_path / "noisy.log").write_text("lots of noise\n", encoding="utf-8")

        assert compute_context_hash(tmp_path, "Dockerfile") == before

    def test_git_contents_are_excluded_without_a_dockerignore(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        git = tmp_path / ".git"
        git.mkdir()
        (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

        assert compute_context_hash(tmp_path, "Dockerfile") == before

    def test_a_comment_only_dockerignore_edit_does_not_bust_the_tag(self, tmp_path):
        """.dockerignore is build metadata, not build input: it is excluded from
        the walk, so an edit that changes no file selection changes no digest."""
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (tmp_path / ".dockerignore").write_text("*.log\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        (tmp_path / ".dockerignore").write_text("*.log\n# a comment\n", encoding="utf-8")

        assert compute_context_hash(tmp_path, "Dockerfile") == before

    def test_a_real_ignore_set_change_still_busts_the_tag(self, tmp_path):
        """The half that proves the exclusion did not go too far: a pattern that
        actually hides a file reaches the digest through the file set."""
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (tmp_path / ".dockerignore").write_text("*.log\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        (tmp_path / ".dockerignore").write_text("*.log\napp.py\n", encoding="utf-8")

        assert compute_context_hash(tmp_path, "Dockerfile") != before

    def test_deleting_the_dockerignore_busts_the_tag(self, tmp_path):
        """The files it hid come back into the walk."""
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (tmp_path / ".dockerignore").write_text("*.log\n", encoding="utf-8")
        (tmp_path / "noisy.log").write_text("lots of noise\n", encoding="utf-8")
        before = compute_context_hash(tmp_path, "Dockerfile")

        (tmp_path / ".dockerignore").unlink()

        assert compute_context_hash(tmp_path, "Dockerfile") != before

    def test_naming_the_dockerfile_in_the_dockerignore_makes_no_difference(self, tmp_path):
        """Two contexts with identical files hash the same whether or not a
        .dockerignore names the Dockerfile, because the walk excludes both files
        either way.

        This is why havoc's and transcoder's contexts -- whose .dockerignore
        files already list ``Dockerfile`` and ``.dockerignore`` -- did not move
        when the exclusions became implicit, and cantaloupe's, which has no
        .dockerignore at all, did.
        """
        listed, absent = tmp_path / "listed", tmp_path / "absent"
        for context in (listed, absent):
            context.mkdir()
            (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (context / "app.py").write_text("x = 1\n", encoding="utf-8")
        (listed / ".dockerignore").write_text("Dockerfile\n.dockerignore\n", encoding="utf-8")

        assert compute_context_hash(listed, "Dockerfile") == compute_context_hash(
            absent, "Dockerfile"
        )

    def test_the_dockerfile_is_hashed_once_under_its_prefix(self, tmp_path):
        """The Dockerfile reaches the hash only through the ``Dockerfile:``
        prefix, which is what records *which* Dockerfile was selected. It is
        excluded from the context walk, so it is not counted a second time."""
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        hasher = hashlib.sha256()
        hasher.update(b"Dockerfile:")
        hasher.update(b"FROM scratch\n")

        assert compute_context_hash(tmp_path, "Dockerfile") == hasher.hexdigest()[:12]

    def test_an_empty_context_hashes_the_empty_digest(self, tmp_path):
        assert compute_context_hash(tmp_path, "Dockerfile") == hashlib.sha256().hexdigest()[:12]

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can read mode-000 files"
    )
    def test_an_unreadable_file_is_skipped_but_its_path_still_counts(self, tmp_path):
        """The except (PermissionError, OSError) arm: content is skipped, but the
        ``\\n<path>:`` header was already fed to the hasher before the open."""
        (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        without = compute_context_hash(tmp_path, "Dockerfile")

        secret = tmp_path / "secret.txt"
        secret.write_text("classified\n", encoding="utf-8")
        secret.chmod(0o000)
        try:
            with_unreadable = compute_context_hash(tmp_path, "Dockerfile")
        finally:
            secret.chmod(0o600)

        assert with_unreadable != without
        # Content genuinely did not contribute: rewriting it changes nothing.
        secret.write_text("something else entirely\n", encoding="utf-8")
        secret.chmod(0o000)
        try:
            assert compute_context_hash(tmp_path, "Dockerfile") == with_unreadable
        finally:
            secret.chmod(0o600)


class TestImageExistsInEcr:
    """Pins for the ClientError code discrimination."""

    def test_a_present_image_returns_true_and_the_query_shape(self, ecr):
        ecr.existing.add(("testapp-web", "abc123"))

        assert image_exists_in_ecr(ecr, "testapp-web", "abc123") is True
        assert ecr.describe_images_calls == [
            {"repositoryName": "testapp-web", "imageIds": [{"imageTag": "abc123"}]}
        ]

    def test_an_empty_image_details_list_is_false(self, ecr):
        def describe(**kwargs):
            return {"imageDetails": []}

        ecr.describe_images = describe

        assert image_exists_in_ecr(ecr, "testapp-web", "abc123") is False

    def test_a_response_without_image_details_is_false(self, ecr):
        def describe(**kwargs):
            return {}

        ecr.describe_images = describe

        assert image_exists_in_ecr(ecr, "testapp-web", "abc123") is False

    def test_image_not_found_is_false(self, ecr):
        ecr.describe_images_error = _client_error("ImageNotFoundException")

        assert image_exists_in_ecr(ecr, "testapp-web", "abc123") is False

    def test_repository_not_found_is_false(self, ecr):
        ecr.describe_images_error = _client_error("RepositoryNotFoundException")

        assert image_exists_in_ecr(ecr, "testapp-web", "abc123") is False

    def test_any_other_client_error_propagates(self, ecr):
        ecr.describe_images_error = _client_error("AccessDeniedException")

        with pytest.raises(ClientError) as exc:
            image_exists_in_ecr(ecr, "testapp-web", "abc123")

        assert exc.value.response["Error"]["Code"] == "AccessDeniedException"

    def test_a_non_client_error_is_not_caught(self, ecr):
        ecr.describe_images_error = RuntimeError("socket closed")

        with pytest.raises(RuntimeError, match="socket closed"):
            image_exists_in_ecr(ecr, "testapp-web", "abc123")


class TestEcrLogin:
    """Pins for the auth-token decode and the docker login handoff."""

    def test_dry_run_prints_the_plan_and_touches_nothing(self, ecr, popen, capsys):
        ecr_login(ecr, dry_run=True)

        assert _lines(capsys.readouterr().out) == [
            "Logging into ECR...",
            "  [dry-run] aws ecr get-login-password | docker login",
        ]
        assert ecr.token_calls == 0
        assert popen.calls == []

    def test_a_successful_login_feeds_the_password_on_stdin(self, ecr, popen, capsys):
        ecr_login(ecr)

        assert ecr.token_calls == 1
        assert popen.calls == [
            (
                ["docker", "login", "--username", "AWS", "--password-stdin", REGISTRY],
                {
                    "stdin": subprocess.PIPE,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.PIPE,
                },
            )
        ]
        assert popen.stdin_payloads == [b"s3cr3t"]
        assert _lines(capsys.readouterr().out) == [
            "Logging into ECR...",
            "  ECR login [done]",
        ]

    def test_only_the_first_authorization_entry_is_used(self, ecr, popen):
        ecr.authorization_data.append(
            {
                "proxyEndpoint": "https://other.example",
                "authorizationToken": base64.b64encode(b"AWS:other").decode(),
            }
        )

        ecr_login(ecr)

        assert popen.calls[0][0][-1] == REGISTRY
        assert popen.stdin_payloads == [b"s3cr3t"]

    def test_a_password_containing_colons_is_split_only_once(self, ecr, popen):
        ecr.authorization_data[0]["authorizationToken"] = base64.b64encode(
            b"AWS:pass:with:colons"
        ).decode()

        ecr_login(ecr)

        assert popen.stdin_payloads == [b"pass:with:colons"]

    def test_a_failed_login_raises_with_dockers_own_diagnostics(self, ecr, popen, capsys):
        """The error carries what docker said, rather than discarding it.

        stderr used to go to DEVNULL, so "ECR login failed" was the whole of
        what an operator had to work from.
        """
        popen.returncode = 1
        popen.stderr = b"Error response from daemon: login attempt failed\n"

        with pytest.raises(RuntimeError, match="ECR login failed: Error response from daemon"):
            ecr_login(ecr)

        assert _lines(capsys.readouterr().out) == ["Logging into ECR..."]

    def test_a_failed_login_that_printed_nothing_still_says_so(self, ecr, popen):
        popen.returncode = 1

        with pytest.raises(RuntimeError, match="ECR login failed: docker printed nothing"):
            ecr_login(ecr)


class TestBuildAndPushRawDict:
    """Pins for the live production path: a raw dict config."""

    def test_the_full_build_tag_push_sequence(self, source_dir, run, capsys):
        config = _dict_config(web={"context": "web"})

        uris = _build(config, source_dir)

        tag = _expected_tag(source_dir / "web")
        repo = f"{ECR_PREFIX}-web"
        cache_ref = f"{ECR_HOST}/{repo}:buildcache"
        assert uris == {"web": f"{ECR_HOST}/{repo}:{tag}"}
        assert run.argv == [
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "-t",
                f"{repo}:{tag}",
                "-f",
                str(source_dir / "web" / "Dockerfile"),
                "--cache-from",
                f"type=registry,ref={cache_ref}",
                "--cache-to",
                f"type=registry,ref={cache_ref},mode=max,image-manifest=true,oci-mediatypes=true",
                str(source_dir / "web"),
            ],
            ["docker", "tag", f"{repo}:{tag}", f"{ECR_HOST}/{repo}:{tag}"],
            ["docker", "push", f"{ECR_HOST}/{repo}:{tag}"],
        ]
        assert _lines(capsys.readouterr().out) == [
            "Building and pushing images...",
            f"  web (build {tag[:8]}) [done]",
            "  web (push) [done]",
        ]

    def test_a_custom_dockerfile_name_is_used_for_the_f_flag(self, source_dir, run):
        (source_dir / "web" / "Dockerfile.prod").write_text("FROM alpine\n", encoding="utf-8")
        config = _dict_config(web={"context": "web", "dockerfile": "Dockerfile.prod"})

        _build(config, source_dir)

        assert "-f" in run.argv[0]
        assert run.argv[0][run.argv[0].index("-f") + 1] == str(
            source_dir / "web" / "Dockerfile.prod"
        )

    def test_a_target_adds_the_target_flag_before_the_build_args(self, source_dir, run):
        config = _dict_config(web={"context": "web", "target": "runtime", "build_args": {"A": "1"}})

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("--target") + 1] == "runtime"
        assert argv.index("--target") < argv.index("--build-arg")
        assert argv[-1] == str(source_dir / "web")

    def test_an_environment_scoped_target_is_selected(self, source_dir, run):
        config = _dict_config(
            web={"context": "web", "target": {"staging": "dev", "production": "prod"}}
        )

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("--target") + 1] == "dev"

    def test_a_target_dict_without_this_environment_adds_no_flag(self, source_dir, run):
        config = _dict_config(web={"context": "web", "target": {"production": "prod"}})

        _build(config, source_dir)

        assert "--target" not in run.argv[0]

    def test_build_args_are_emitted_in_dict_order(self, source_dir, run):
        config = _dict_config(web={"context": "web", "build_args": {"Z": "1", "A": "2"}})

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("--build-arg") : argv.index("--cache-from")] == [
            "--build-arg",
            "Z=1",
            "--build-arg",
            "A=2",
        ]

    def test_environment_build_args_override_the_base_ones(self, source_dir, run):
        config = _dict_config(
            web={
                "context": "web",
                "build_args": {"A": "base", "B": "keep", "staging": {"A": "staged"}},
            }
        )

        _build(config, source_dir)

        argv = run.argv[0]
        pairs = [argv[i + 1] for i, item in enumerate(argv) if item == "--build-arg"]
        assert pairs == ["A=staged", "B=keep"]

    def test_a_local_only_image_is_tagged_by_name_and_never_pushed(self, source_dir, run, capsys):
        config = _dict_config(base={"context": "base", "push": False})

        uris = _build(config, source_dir)

        tag = _expected_tag(source_dir / "base")
        assert uris == {}
        assert run.verbs == ["build"]
        assert run.argv[0][run.argv[0].index("-t") + 1] == f"base:{tag}"
        assert _lines(capsys.readouterr().out) == [
            "Building and pushing images...",
            f"  base (build {tag[:8]}) [done]",
            "  base [local only]",
        ]

    def test_a_local_only_image_builds_without_registry_cache_flags(self, source_dir, run):
        config = _dict_config(base={"context": "base", "push": False})

        _build(config, source_dir)

        assert "--cache-from" not in run.argv[0]
        assert "--cache-to" not in run.argv[0]

    def test_push_defaults_to_true(self, source_dir, run):
        config = _dict_config(web={"context": "web"})

        uris = _build(config, source_dir)

        assert list(uris) == ["web"]
        assert run.verbs == ["build", "tag", "push"]

    def test_dependencies_decide_the_build_order(self, source_dir, run):
        config = _dict_config(
            web={"context": "web", "depends_on": ["base"]},
            base={"context": "base", "push": False},
        )

        _build(config, source_dir)

        assert [
            argv[argv.index("-t") + 1].split(":")[0] for argv in run.argv if argv[1] == "build"
        ][0].endswith("base")

    def test_a_dependency_cycle_is_logged_then_re_raised(self, source_dir, run, capsys):
        config = _dict_config(
            a={"context": "web", "depends_on": ["b"]},
            b={"context": "base", "depends_on": ["a"]},
        )

        with pytest.raises(ValueError):
            _build(config, source_dir)

        out = _plain(capsys.readouterr().out)
        assert "Building and pushing images..." in out
        assert "✗" in out
        assert run.calls == []

    def test_an_unknown_dependency_is_logged_then_re_raised(self, source_dir, run, capsys):
        config = _dict_config(web={"context": "web", "depends_on": ["ghost"]})

        with pytest.raises(ValueError, match="unknown image 'ghost'"):
            _build(config, source_dir)

        assert "unknown image 'ghost'" in _plain(capsys.readouterr().out)

    def test_an_empty_images_section_builds_nothing(self, source_dir, run, capsys):
        assert _build(_dict_config(), source_dir) == {}
        assert run.calls == []
        assert _lines(capsys.readouterr().out) == ["Building and pushing images..."]

    def test_a_config_without_an_images_key_builds_nothing(self, source_dir, run):
        assert _build({}, source_dir) == {}
        assert run.calls == []

    def test_a_missing_context_directory_names_the_image_and_the_path(self, source_dir, run):
        """A context that does not exist used to hash to the digest of nothing.

        rglob over a missing directory yields nothing, so the image took the
        empty digest and docker build then failed against a path that does not
        exist — two failures for one typo, neither naming the typo.
        """
        config = _dict_config(web={"context": "nope"})

        with pytest.raises(RuntimeError, match=r"Image 'web': build context .*nope"):
            _build(config, source_dir)

        assert run.calls == []

    def test_a_missing_context_directory_raises_on_the_image_config_arm_too(self, source_dir, run):
        config = _deploy_config(web=ImageConfig(name="web", context="nope"))

        with pytest.raises(RuntimeError, match=r"Image 'web': build context .*nope"):
            _build(config, source_dir)

    def test_a_context_that_is_a_file_is_rejected(self, source_dir, run):
        """is_dir(), not exists(): docker build needs a directory."""
        (source_dir / "Dockerfile.web").write_text("FROM scratch\n", encoding="utf-8")
        config = _dict_config(web={"context": "Dockerfile.web"})

        with pytest.raises(RuntimeError, match="is not a directory"):
            _build(config, source_dir)


class TestBuildFailures:
    """Pins for the build and push failure shapes."""

    def test_a_build_failure_echoes_output_and_raises_runtime_error(self, source_dir, run, capsys):
        run.returncodes["build"] = 1
        run.stdout["build"] = "step 3/5"
        run.stderr["build"] = "no such file"
        config = _dict_config(web={"context": "web"})

        with pytest.raises(RuntimeError, match="Build failed for web"):
            _build(config, source_dir)

        out = _lines(capsys.readouterr().out)
        assert out[-3:] == ["  ✗ Build failed for web", "step 3/5", "no such file"]
        assert run.verbs == ["build"]

    def test_a_push_failure_echoes_output_and_raises_runtime_error(self, source_dir, run, capsys):
        run.returncodes["push"] = 1
        run.stderr["push"] = "denied"
        config = _dict_config(web={"context": "web"})

        with pytest.raises(RuntimeError, match="Push failed for web"):
            _build(config, source_dir)

        assert _lines(capsys.readouterr().out)[-2:] == ["  ✗ Push failed for web", "denied"]
        assert run.verbs == ["build", "tag", "push"]

    def test_a_failure_stops_before_later_images(self, source_dir, run):
        run.returncodes["build"] = 1
        config = _dict_config(
            base={"context": "base", "push": False},
            web={"context": "web", "depends_on": ["base"]},
        )

        with pytest.raises(RuntimeError):
            _build(config, source_dir)

        assert run.verbs == ["build"]


class TestTagFailureModeDiverges:
    """MUST-PIN 1: ``docker tag`` fails differently from build and push.

    ``subprocess.run(tag_cmd, check=True)`` does not capture output, is not
    timed, and does not route through ``_check_subprocess_result``. Collapsing
    the three dry-run/execute blocks into one helper would change all three
    properties at once, silently.
    """

    def test_the_tag_call_is_unchecked_uncaptured_and_untimed(self, source_dir, run):
        config = _dict_config(web={"context": "web"})

        _build(config, source_dir)

        build_kwargs, tag_kwargs, push_kwargs = run.kwargs
        assert tag_kwargs == {"check": True}
        assert build_kwargs == {"capture_output": True, "text": True, "check": False}
        assert push_kwargs == {"capture_output": True, "text": True, "check": False}

    def test_a_tag_failure_raises_called_process_error_not_runtime_error(
        self, source_dir, run, capsys
    ):
        run.returncodes["tag"] = 125
        run.stdout["tag"] = "would-have-been-printed"
        run.stderr["tag"] = "no such image"
        config = _dict_config(web={"context": "web"})

        with pytest.raises(subprocess.CalledProcessError) as exc:
            _build(config, source_dir)

        assert exc.value.returncode == 125
        # Nothing from the failed command reaches the operator.
        out = _plain(capsys.readouterr().out)
        assert "would-have-been-printed" not in out
        assert "no such image" not in out
        assert "Tag failed" not in out
        assert run.verbs == ["build", "tag"]

    def test_the_tag_step_never_appears_in_the_timer_report(self, source_dir, run):
        timer = DeploymentTimer("run-1")
        set_timer(timer)
        config = _dict_config(web={"context": "web"})

        with timer.step("build_and_push_images"):
            _build(config, source_dir)

        (step,) = timer.report.steps
        assert [sub.name for sub in step.sub_steps] == ["web_build", "web_push"]


class TestCacheHit:
    """MUST-PIN 3: the loop's only early exit."""

    def test_a_cached_image_skips_build_tag_and_push(self, source_dir, run, ecr, capsys):
        tag = _expected_tag(source_dir / "web")
        ecr.existing.add((f"{ECR_PREFIX}-web", tag))
        config = _dict_config(web={"context": "web"})

        uris = _build(config, source_dir, ecr_client=ecr)

        assert uris == {"web": f"{ECR_HOST}/{ECR_PREFIX}-web:{tag}"}
        assert run.calls == []
        assert _lines(capsys.readouterr().out) == [
            "Building and pushing images...",
            f"  web [cached ({tag[:8]})]",
        ]

    def test_a_cache_miss_builds_normally(self, source_dir, run, ecr):
        config = _dict_config(web={"context": "web"})

        _build(config, source_dir, ecr_client=ecr)

        assert run.verbs == ["build", "tag", "push"]
        assert len(ecr.describe_images_calls) == 1

    def test_force_build_skips_the_cache_lookup_entirely(self, source_dir, run, ecr):
        tag = _expected_tag(source_dir / "web")
        ecr.existing.add((f"{ECR_PREFIX}-web", tag))
        config = _dict_config(web={"context": "web"})

        _build(config, source_dir, ecr_client=ecr, force_build=True)

        assert ecr.describe_images_calls == []
        assert run.verbs == ["build", "tag", "push"]

    def test_dry_run_skips_the_cache_lookup_entirely(self, source_dir, run, ecr):
        tag = _expected_tag(source_dir / "web")
        ecr.existing.add((f"{ECR_PREFIX}-web", tag))
        config = _dict_config(web={"context": "web"})

        _build(config, source_dir, ecr_client=ecr, dry_run=True)

        assert ecr.describe_images_calls == []
        assert run.calls == []

    def test_no_ecr_client_skips_the_cache_lookup_entirely(self, source_dir, run):
        config = _dict_config(web={"context": "web"})

        _build(config, source_dir, ecr_client=None)

        assert run.verbs == ["build", "tag", "push"]

    def test_a_local_only_image_is_never_cache_checked(self, source_dir, run, ecr):
        config = _dict_config(base={"context": "base", "push": False})

        _build(config, source_dir, ecr_client=ecr)

        assert ecr.describe_images_calls == []
        assert run.verbs == ["build"]

    def test_a_cached_image_still_satisfies_a_later_dependant(self, source_dir, run, ecr):
        base_tag = _expected_tag(source_dir / "base")
        ecr.existing.add((f"{ECR_PREFIX}-base", base_tag))
        config = _dict_config(
            base={"context": "base"},
            web={"context": "web", "depends_on": ["base"]},
        )

        uris = _build(config, source_dir, ecr_client=ecr)

        assert set(uris) == {"base", "web"}
        assert run.verbs == ["build", "tag", "push"]


class TestDryRun:
    """Pins for the dry-run print shape."""

    def test_dry_run_prints_all_three_commands_and_runs_none(self, source_dir, run, capsys):
        config = _dict_config(web={"context": "web"})

        uris = _build(config, source_dir, dry_run=True)

        tag = _expected_tag(source_dir / "web")
        repo = f"{ECR_PREFIX}-web"
        assert run.calls == []
        assert uris == {"web": f"{ECR_HOST}/{repo}:{tag}"}
        out = _lines(capsys.readouterr().out)
        assert out == [
            "Building and pushing images...",
            "  [dry-run] docker build --platform linux/amd64 -t "
            f"{repo}:{tag} -f {source_dir / 'web' / 'Dockerfile'} "
            f"--cache-from type=registry,ref={ECR_HOST}/{repo}:buildcache "
            f"--cache-to type=registry,ref={ECR_HOST}/{repo}:buildcache,"
            f"mode=max,image-manifest=true,oci-mediatypes=true {source_dir / 'web'}",
            f"  web (build {tag[:8]}) [done]",
            f"  [dry-run] docker tag {repo}:{tag} {ECR_HOST}/{repo}:{tag}",
            f"  [dry-run] docker push {ECR_HOST}/{repo}:{tag}",
            "  web (push) [done]",
        ]

    def test_a_local_only_dry_run_prints_only_the_build(self, source_dir, run, capsys):
        config = _dict_config(base={"context": "base", "push": False})

        _build(config, source_dir, dry_run=True)

        out = _lines(capsys.readouterr().out)
        assert sum(1 for line in out if "[dry-run]" in line) == 1
        assert out[-1] == "  base [local only]"


class TestHashModifiers:
    """MUST-PIN 4: ``args:`` before ``target:``, and the digest recipe itself."""

    def test_no_modifiers_leaves_the_raw_content_hash(self, source_dir, run):
        config = _dict_config(web={"context": "web"})

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("-t") + 1].split(":")[-1] == compute_context_hash(
            source_dir / "web", "Dockerfile"
        )

    def test_args_come_before_target_in_the_digest_input(self, source_dir, run):
        config = _dict_config(
            web={"context": "web", "target": "runtime", "build_args": {"B": "2", "A": "1"}}
        )

        _build(config, source_dir)

        base = compute_context_hash(source_dir / "web", "Dockerfile")
        args_first = hashlib.sha256(f"{base}:args:A=1,B=2;target:runtime".encode()).hexdigest()[:12]
        target_first = hashlib.sha256(f"{base}:target:runtime;args:A=1,B=2".encode()).hexdigest()[
            :12
        ]

        argv = run.argv[0]
        actual = argv[argv.index("-t") + 1].split(":")[-1]
        assert actual == args_first
        assert actual != target_first

    def test_build_args_are_sorted_inside_the_digest_but_not_on_the_command_line(
        self, source_dir, run
    ):
        config = _dict_config(web={"context": "web", "build_args": {"B": "2", "A": "1"}})

        _build(config, source_dir)

        base = compute_context_hash(source_dir / "web", "Dockerfile")
        expected = hashlib.sha256(f"{base}:args:A=1,B=2".encode()).hexdigest()[:12]
        argv = run.argv[0]
        assert argv[argv.index("-t") + 1].split(":")[-1] == expected
        # The command line keeps insertion order; only the digest is sorted.
        assert argv[argv.index("--build-arg") + 1] == "B=2"

    def test_a_target_alone_still_rehashes(self, source_dir, run):
        config = _dict_config(web={"context": "web", "target": "runtime"})

        _build(config, source_dir)

        base = compute_context_hash(source_dir / "web", "Dockerfile")
        expected = hashlib.sha256(f"{base}:target:runtime".encode()).hexdigest()[:12]
        argv = run.argv[0]
        assert argv[argv.index("-t") + 1].split(":")[-1] == expected

    def test_empty_build_args_are_falsy_and_add_no_modifier(self, source_dir, run):
        config = _dict_config(web={"context": "web", "build_args": {}})

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("-t") + 1].split(":")[-1] == compute_context_hash(
            source_dir / "web", "Dockerfile"
        )
        assert "--build-arg" not in argv

    def test_changing_a_build_arg_changes_the_tag(self, source_dir, run):
        _build(_dict_config(web={"context": "web", "build_args": {"A": "1"}}), source_dir)
        first = run.argv[0][run.argv[0].index("-t") + 1]

        run.calls.clear()
        _build(_dict_config(web={"context": "web", "build_args": {"A": "2"}}), source_dir)

        assert run.argv[0][run.argv[0].index("-t") + 1] != first


class TestBuildAndPushDeployConfig:
    """MUST-PIN 2 (first half): the ``DeployConfig``/``ImageConfig`` arm.

    Dead in production — ``deployer.py:112`` passes ``get_raw_dict()`` — but
    live in the type signature, so 53e-4b must not drop it.
    """

    def test_an_image_config_produces_the_same_commands_as_the_dict_arm(
        self, source_dir, run, capsys
    ):
        _build(_dict_config(web={"context": "web"}), source_dir)
        dict_argv = run.argv[:]
        dict_out = capsys.readouterr().out

        run.calls.clear()
        config = _deploy_config(web=ImageConfig(name="web", context="web"))
        _build(config, source_dir)

        assert run.argv == dict_argv
        assert capsys.readouterr().out == dict_out

    def test_image_config_push_false_is_honoured(self, source_dir, run):
        config = _deploy_config(base=ImageConfig(name="base", context="base", push=False))

        uris = _build(config, source_dir)

        assert uris == {}
        assert run.verbs == ["build"]

    def test_image_config_target_and_build_args_reach_the_command(self, source_dir, run):
        config = _deploy_config(
            web=ImageConfig(
                name="web",
                context="web",
                target={"staging": "dev"},
                build_args={"A": "base", "staging": {"A": "staged"}},
            )
        )

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("--target") + 1] == "dev"
        assert argv[argv.index("--build-arg") + 1] == "A=staged"

    def test_image_config_dependencies_are_projected_for_the_topological_sort(
        self, source_dir, run
    ):
        config = _deploy_config(
            web=ImageConfig(name="web", context="web", depends_on=["base"]),
            base=ImageConfig(name="base", context="base", push=False),
        )

        _build(config, source_dir)

        first_build = next(argv for argv in run.argv if argv[1] == "build")
        assert first_build[first_build.index("-t") + 1].startswith("base:")

    def test_an_image_config_cycle_is_logged_then_re_raised(self, source_dir, run, capsys):
        config = _deploy_config(
            a=ImageConfig(name="a", context="web", depends_on=["b"]),
            b=ImageConfig(name="b", context="base", depends_on=["a"]),
        )

        with pytest.raises(ValueError):
            _build(config, source_dir)

        assert "✗" in _plain(capsys.readouterr().out)

    def test_an_image_config_dockerfile_default_is_dockerfile(self, source_dir, run):
        config = _deploy_config(web=ImageConfig(name="web", context="web"))

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("-f") + 1] == str(source_dir / "web" / "Dockerfile")


class TestAdditionalContexts:
    """Named additional contexts: the --build-context flag, the cache tag,
    and the fail-fast on a missing directory."""

    @pytest.fixture
    def shared_dir(self, source_dir):
        """A real ``shared`` directory next to the build contexts."""
        shared = source_dir / "shared"
        shared.mkdir()
        (shared / "constants.py").write_text("X = 1\n", encoding="utf-8")
        return shared

    def test_the_flag_is_emitted_with_the_resolved_path(self, source_dir, shared_dir, run):
        config = _dict_config(web={"context": "web", "additional_contexts": {"shared": "shared"}})

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("--build-context") + 1] == f"shared={shared_dir}"
        assert argv[-1] == str(source_dir / "web")

    def test_no_additional_contexts_means_no_flag(self, source_dir, run):
        _build(_dict_config(web={"context": "web"}), source_dir)

        assert "--build-context" not in run.argv[0]

    def test_the_image_config_arm_agrees_with_the_dict_arm(
        self, source_dir, shared_dir, run, capsys
    ):
        _build(
            _dict_config(web={"context": "web", "additional_contexts": {"shared": "shared"}}),
            source_dir,
        )
        dict_argv = run.argv[:]
        dict_out = capsys.readouterr().out

        run.calls.clear()
        config = _deploy_config(
            web=ImageConfig(name="web", context="web", additional_contexts={"shared": "shared"})
        )
        _build(config, source_dir)

        assert run.argv == dict_argv
        assert capsys.readouterr().out == dict_out

    def test_the_context_contents_reach_the_cache_tag(self, source_dir, shared_dir, run):
        config = _dict_config(web={"context": "web", "additional_contexts": {"shared": "shared"}})

        _build(config, source_dir)

        tag = _expected_tag(source_dir / "web", additional_contexts={"shared": shared_dir})
        assert run.argv[0][run.argv[0].index("-t") + 1].endswith(f":{tag}")

    def test_editing_the_shared_context_changes_the_tag(self, source_dir, shared_dir, run):
        config = _dict_config(web={"context": "web", "additional_contexts": {"shared": "shared"}})
        _build(config, source_dir)
        first = run.argv[0][run.argv[0].index("-t") + 1]

        (shared_dir / "constants.py").write_text("X = 2\n", encoding="utf-8")
        run.calls.clear()
        _build(config, source_dir)

        assert run.argv[0][run.argv[0].index("-t") + 1] != first

    def test_a_missing_context_directory_raises_naming_image_and_context(self, source_dir):
        config = _dict_config(web={"context": "web", "additional_contexts": {"shared": "nope"}})

        with pytest.raises(RuntimeError, match="Image 'web': additional context 'shared'"):
            _build(config, source_dir)


class TestBuildArgsDispatchAgrees:
    """MUST-PIN 2 (second half): the two arms must answer a scalar the same way.

    They used to disagree. ``ImageConfig.get_build_args`` guarded with
    ``isinstance(env_override, dict)`` and passed the scalar through as a
    literal ``--build-arg staging=5``; the inline "legacy" copy in ``images.py``
    did not guard, so ``dict.update(<non-dict>)`` raised — ``TypeError`` for an
    int, ``ValueError`` for a string, and neither named the image or the key.
    Same deploy.toml, two outcomes. Both now route through ``merge_build_args``.
    """

    EXPECTED = (
        "Image 'web': build_args.staging must be a table of build args, got {type}. "
        r"Did you mean \[images.web.build_args.staging\]\?"
    )

    def test_the_dict_arm_rejects_an_int_environment_override(self, source_dir, run):
        config = _dict_config(web={"context": "web", "build_args": {"staging": 5}})

        with pytest.raises(RuntimeError, match=self.EXPECTED.format(type="int")):
            _build(config, source_dir)

    def test_the_dict_arm_rejects_a_string_environment_override(self, source_dir, run):
        """A string is iterable, so ``update`` used to get past ``TypeError`` and
        die on the element shape instead — a second failure mode, same bug."""
        config = _dict_config(web={"context": "web", "build_args": {"staging": "oops"}})

        with pytest.raises(RuntimeError, match=self.EXPECTED.format(type="str")):
            _build(config, source_dir)

    def test_the_image_config_arm_rejects_the_same_input_identically(self, source_dir, run):
        config = _deploy_config(
            web=ImageConfig(name="web", context="web", build_args={"staging": 5})
        )

        with pytest.raises(RuntimeError, match=self.EXPECTED.format(type="int")):
            _build(config, source_dir)

    def test_both_arms_produce_the_identical_message(self, source_dir, run):
        """The pin that keeps the two arms from drifting apart again."""
        build_args = {"staging": 5}

        with pytest.raises(RuntimeError) as dict_arm:
            _build(_dict_config(web={"context": "web", "build_args": build_args}), source_dir)
        with pytest.raises(RuntimeError) as image_config_arm:
            _build(
                _deploy_config(web=ImageConfig(name="web", context="web", build_args=build_args)),
                source_dir,
            )

        assert str(dict_arm.value) == str(image_config_arm.value)

    def test_a_non_matching_environment_key_is_harmless_on_the_dict_arm(self, source_dir, run):
        """The guard's absence only bites when the sub-table key *is* the
        environment: ``production`` under a staging deploy stays a filtered-out
        dict, so nothing calls ``update`` with it."""
        config = _dict_config(
            web={"context": "web", "build_args": {"A": "1", "production": {"A": "2"}}}
        )

        _build(config, source_dir)

        argv = run.argv[0]
        assert argv[argv.index("--build-arg") + 1] == "A=1"


class TestTimerArmsAgree:
    """MUST-PIN 5: the timed and untimed arms must stay indistinguishable.

    ``images.py`` reads the module-global timer, so the two real arms are a
    ``DeploymentTimer`` inside a ``step()`` and ``None``. Without this pin,
    collapsing ``_run_timed_subprocess``'s two branches cannot be shown safe.
    """

    @staticmethod
    def _run_both(source_dir, run, capsys, **kwargs):
        config = _dict_config(
            base={"context": "base", "push": False},
            web={"context": "web", "depends_on": ["base"], "target": "runtime"},
        )

        set_timer(None)
        untimed_uris = _build(config, source_dir, **kwargs)
        untimed_calls = run.calls[:]
        untimed_out = capsys.readouterr().out

        run.calls.clear()
        timer = DeploymentTimer("run-1")
        set_timer(timer)
        with timer.step("build_and_push_images"):
            timed_uris = _build(config, source_dir, **kwargs)
        timed_calls = run.calls[:]
        timed_out = capsys.readouterr().out

        return (
            (untimed_uris, untimed_calls, untimed_out),
            (
                timed_uris,
                timed_calls,
                timed_out,
            ),
            timer,
        )

    def test_a_clean_run_is_identical_on_both_arms(self, source_dir, run, capsys):
        untimed, timed, _ = self._run_both(source_dir, run, capsys)

        assert timed == untimed

    def test_a_dry_run_is_identical_on_both_arms(self, source_dir, run, capsys):
        untimed, timed, _ = self._run_both(source_dir, run, capsys, dry_run=True)

        assert timed == untimed

    def test_a_forced_run_is_identical_on_both_arms(self, source_dir, run, capsys):
        untimed, timed, _ = self._run_both(source_dir, run, capsys, force_build=True)

        assert timed == untimed

    def test_only_the_timed_arm_records_sub_steps_and_it_names_them_per_image(
        self, source_dir, run, capsys
    ):
        _, _, timer = self._run_both(source_dir, run, capsys)

        (step,) = timer.report.steps
        assert [sub.name for sub in step.sub_steps] == ["base_build", "web_build", "web_push"]

    def test_a_dry_run_records_no_sub_steps_on_the_timed_arm(self, source_dir, run, capsys):
        _, _, timer = self._run_both(source_dir, run, capsys, dry_run=True)

        (step,) = timer.report.steps
        assert step.sub_steps == []

    def test_a_build_failure_is_identical_on_both_arms(self, source_dir, run, capsys):
        run.returncodes["build"] = 1
        run.stderr["build"] = "boom"
        config = _dict_config(web={"context": "web"})

        set_timer(None)
        with pytest.raises(RuntimeError) as untimed_exc:
            _build(config, source_dir)
        untimed_out = capsys.readouterr().out

        run.calls.clear()
        timer = DeploymentTimer("run-1")
        set_timer(timer)
        with pytest.raises(RuntimeError) as timed_exc, timer.step("build_and_push_images"):
            _build(config, source_dir)
        timed_out = capsys.readouterr().out

        assert str(timed_exc.value) == str(untimed_exc.value)
        assert timed_out == untimed_out


class TestValidateEcrRepositoriesImageConfigArm:
    """The ``ImageConfig`` arm at images.py:408 — the live preflight shape.

    ``preflight.py:139`` hands ``validate_ecr_repositories`` a real
    ``DeployConfig``; the pre-existing ``test_images.py`` only ever passed raw
    dicts, which is why this arm was unexecuted.
    """

    def test_a_deploy_config_checks_every_pushed_image(self, ecr):
        config = _deploy_config(
            web=ImageConfig(name="web", context="."),
            worker=ImageConfig(name="worker", context="."),
        )

        assert validate_ecr_repositories(ecr, config, "myapp") == []
        assert ecr.describe_repositories_calls == [
            {"repositoryNames": ["myapp-web"]},
            {"repositoryNames": ["myapp-worker"]},
        ]

    def test_an_image_config_with_push_false_is_skipped(self, ecr):
        config = _deploy_config(
            base=ImageConfig(name="base", context=".", push=False),
            web=ImageConfig(name="web", context="."),
        )

        assert validate_ecr_repositories(ecr, config, "myapp") == []
        assert ecr.describe_repositories_calls == [{"repositoryNames": ["myapp-web"]}]

    def test_a_missing_repository_is_reported_from_the_deploy_config_arm(self, ecr):
        def describe(**kwargs):
            ecr.describe_repositories_calls.append(kwargs)
            raise _client_error("RepositoryNotFoundException", "DescribeRepositories")

        ecr.describe_repositories = describe
        config = _deploy_config(web=ImageConfig(name="web", context="."))

        assert validate_ecr_repositories(ecr, config, "myapp") == ["myapp-web"]

    def test_a_deploy_config_with_no_images_checks_nothing(self, ecr):
        assert validate_ecr_repositories(ecr, _deploy_config(), "myapp") == []
        assert ecr.describe_repositories_calls == []
