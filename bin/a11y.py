#!/usr/bin/env python3
"""
Accessibility check for the only HTML this repo emits: the CloudFront 503 page.

`modules/cloudfront-alb/locals.tf` builds a static, non-interactive error page
and uploads it to S3 (`s3.tf`, `aws_s3_object.error_503`). That page is the
repo's entire WCAG surface — everything else is CLI output or OpenTofu. This
script renders the template the same way OpenTofu does, for each environment
variant, and runs pa11y against the rendered files.

Rendering is deliberately literal rather than clever: the template uses exactly
two constructs (`${var.name_prefix}` interpolation and one `%{~for~}` loop over
`local.error_page_reasons`). Anything else in the heredoc means the template
grew a feature this renderer does not model, and the script fails rather than
silently checking a page that is not the real one.

Requires node/npx on PATH (pa11y is fetched on demand, like uvx tools).

Usage:
    uv run bin/a11y.py            # or: make a11y
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LOCALS_TF = Path(__file__).resolve().parent.parent / "modules" / "cloudfront-alb" / "locals.tf"
SAMPLE_NAME_PREFIX = "myapp-staging"
PA11Y_SPEC = "pa11y@8"
PA11Y_STANDARD = "WCAG2AA"

# `%{~for reason in local.error_page_reasons}` ... `%{~endfor}` around one <li>.
LOOP_RE = re.compile(
    r"^[ \t]*%\{~for reason in local\.error_page_reasons\}\n"
    r"(?P<body>.*?)"
    r"^[ \t]*%\{~endfor\}\n",
    re.DOTALL | re.MULTILINE,
)


def read_heredoc(text: str) -> str:
    """Return the dedented HTML heredoc assigned to error_page_html."""
    match = re.search(
        r"error_page_html\s*=\s*.*?<<-EOF\n(?P<body>.*?)^\s*EOF\s*$",
        text,
        re.DOTALL | re.MULTILINE,
    )
    if match is None:
        raise SystemExit(f"error_page_html heredoc not found in {LOCALS_TF}")
    body = match.group("body")
    lines = [line for line in body.splitlines() if line.strip()]
    indent = min(len(line) - len(line.lstrip()) for line in lines)
    return "\n".join(line[indent:] if line.strip() else line for line in body.splitlines()) + "\n"


def read_reason_sets(text: str) -> dict[str, list[str]]:
    """Return the error_page_reasons list for each environment variant."""
    match = re.search(
        r"error_page_reasons\s*=\s*var\.environment == \"staging\" \? \[(?P<staging>.*?)\]"
        r"\s*:\s*\[(?P<other>.*?)\]",
        text,
        re.DOTALL,
    )
    if match is None:
        raise SystemExit(f"error_page_reasons conditional not found in {LOCALS_TF}")
    return {
        "staging": re.findall(r'"([^"]*)"', match.group("staging")),
        "production": re.findall(r'"([^"]*)"', match.group("other")),
    }


def render(template: str, reasons: list[str]) -> str:
    """Render the heredoc the way OpenTofu would, for one set of reasons."""
    loop = LOOP_RE.search(template)
    if loop is None:
        raise SystemExit("the %{~for~} loop over error_page_reasons is no longer in the template")
    expanded = "".join(loop.group("body").replace("${reason}", reason) for reason in reasons)
    rendered = template[: loop.start()] + expanded + template[loop.end() :]
    rendered = rendered.replace("${var.name_prefix}", SAMPLE_NAME_PREFIX)
    leftover = re.search(r"[$%]\{~?[a-z]", rendered)
    if leftover is not None:
        raise SystemExit(
            f"unrendered template construct at offset {leftover.start()}: grew a feature"
        )
    return rendered


def run_pa11y(path: Path) -> int:
    """Run pa11y against a rendered file; return its exit code."""
    print(f"=== pa11y {PA11Y_STANDARD}: {path.name} ===", flush=True)
    result = subprocess.run(
        ["npx", "--yes", PA11Y_SPEC, "--standard", PA11Y_STANDARD, path.as_uri()],
        check=False,
    )
    return result.returncode


def main() -> int:
    if shutil.which("npx") is None:
        raise SystemExit("npx not found on PATH; install node to run the accessibility check")
    text = LOCALS_TF.read_text(encoding="utf-8")
    template = read_heredoc(text)
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for variant, reasons in read_reason_sets(text).items():
            path = Path(tmp) / f"error-503-{variant}.html"
            path.write_text(render(template, reasons), encoding="utf-8")
            failures += 1 if run_pa11y(path) != 0 else 0
    if failures:
        print(f"{failures} error-page variant(s) failed {PA11Y_STANDARD}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
