"""Machine-readable encodings of a deploy's merged environment.

``Deployer.print_environment_config`` narrates the environment for a human
reading the deploy log: ``KEY=value`` unquoted, ``(unset)`` for an empty
string. That is not a format anything can read back -- a value that is
literally ``(unset)`` is indistinguishable from an empty one, and a value
holding a newline or a quote breaks the line. These encoders are the
parseable forms, used by ``deploy.py env``.

Both take the map ``Deployer.environment_variables()`` returns, which is
already stringified by ``task_definition.stringify_environment`` -- the same
conversion the task definition applies -- so the document is what deploys.
"""

import json
import re
from collections.abc import Mapping

# A name a POSIX shell accepts on the left of an assignment. Anything else
# would make a dotenv line that a shell misparses -- or, for a name carrying
# `;` or `$(`, runs -- when the document is sourced.
_SHELL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def encode_dotenv(env: Mapping[str, str]) -> str:
    """Encode an environment as shell-sourceable ``KEY='value'`` lines.

    Every value is single-quoted, so nothing inside it is expanded: ``$``,
    backticks, backslashes and newlines are all literal. A single quote cannot
    appear inside single quotes, so each one closes the quote, adds an escaped
    quote and reopens it -- ``'`` becomes ``'\\''``. An empty string is
    ``KEY=''``, never a marker. Keys are sorted, one per line, and the
    document ends with a newline (an empty environment encodes to ``""``).

    Args:
        env: Variable names to string values.

    Returns:
        The document, readable back with ``set -a; . FILE; set +a``.

    Raises:
        ValueError: If a name is not a valid shell variable name.
    """
    bad = sorted(key for key in env if not _SHELL_NAME.fullmatch(key))
    if bad:
        raise ValueError(
            f"Cannot encode as dotenv: not valid shell variable names: {', '.join(bad)}. "
            "Use --format json, or rename them in deploy.toml."
        )
    escaped = {key: value.replace("'", r"'\''") for key, value in env.items()}
    return "".join(f"{key}='{escaped[key]}'\n" for key in sorted(escaped))


def encode_json(env: Mapping[str, str]) -> str:
    """Encode an environment as a flat JSON object.

    Keys are sorted and non-ASCII is kept as-is (``ensure_ascii=False``), the
    project's convention for documents an operator reads back. The document
    ends with a newline.

    Args:
        env: Variable names to string values.

    Returns:
        The JSON document.
    """
    return json.dumps(dict(env), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
