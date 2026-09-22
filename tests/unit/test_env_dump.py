"""Tests for the machine-readable environment encoders (deploy.py env).

The dotenv tests read the document back through a real POSIX shell rather
than only comparing strings: the claim is "a shell reads back exactly these
values", and a string comparison would only restate the implementation.
"""

import json
import shutil
import subprocess
import sys

import pytest

from deployer.deploy.env_dump import encode_dotenv, encode_json
from deployer.deploy.task_definition import stringify_environment

# Every value the encoders have to get right, as stringify_environment hands
# them over. MAX_WORKERS and DEBUG arrive from TOML as int and bool.
AWKWARD = stringify_environment(
    {
        "EMPTY": "",
        "LITERAL_UNSET": "(unset)",
        "SINGLE": "it's",
        "ONLY_QUOTES": "''",
        "DOUBLE": 'say "hi"',
        "NEWLINE": "line one\nline two\n",
        "NON_ASCII": "café ☕ 日本",
        "MAX_WORKERS": 4,
        "DEBUG": False,
        "DOLLAR": "$HOME ${PATH} $(echo pwned)",
        "BACKTICK": "`echo pwned`",
        "BACKSLASH": "C:\\path\\n",
    }
)


def _source_in_shell(document: str, tmp_path) -> dict[str, str]:
    """Source a dotenv document in sh and return the variables it exported."""
    path = tmp_path / "env"
    path.write_text(document, encoding="utf-8")
    dump = "import json, os; print(json.dumps(dict(os.environ)))"
    result = subprocess.run(  # noqa: S603 — fixed argv, test-controlled inputs
        [shutil.which("sh") or "/bin/sh", "-c", 'set -a; . "$1"; exec "$2" -c "$3"', "sh"]
        + [str(path), sys.executable, dump],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    exported = json.loads(result.stdout)
    return {key: exported[key] for key in AWKWARD if key in exported}


class TestStringifyEnvironment:
    """The conversion both encoders' input has already been through."""

    def test_toml_ints_and_bools_become_python_str(self):
        assert AWKWARD["MAX_WORKERS"] == "4"
        assert AWKWARD["DEBUG"] == "False"


class TestEncodeDotenv:
    def test_values_are_single_quoted_one_per_line_sorted(self):
        document = encode_dotenv({"B": "two", "A": "one"})

        assert document == "A='one'\nB='two'\n"

    def test_empty_string_is_two_quotes_not_a_marker(self):
        assert encode_dotenv({"EMPTY": ""}) == "EMPTY=''\n"

    def test_a_literal_unset_value_is_distinguishable_from_empty(self):
        document = encode_dotenv({"EMPTY": "", "LITERAL_UNSET": "(unset)"})

        assert document == "EMPTY=''\nLITERAL_UNSET='(unset)'\n"

    def test_a_single_quote_closes_escapes_and_reopens(self):
        assert encode_dotenv({"SINGLE": "it's"}) == "SINGLE='it'\\''s'\n"

    def test_ints_and_bools_encode_as_the_task_definition_carries_them(self):
        document = encode_dotenv({k: AWKWARD[k] for k in ("DEBUG", "MAX_WORKERS")})

        assert document == "DEBUG='False'\nMAX_WORKERS='4'\n"

    def test_an_empty_environment_encodes_to_nothing(self):
        assert encode_dotenv({}) == ""

    def test_a_shell_reads_back_every_value_exactly(self, tmp_path):
        """The round trip: quotes, newlines, non-ASCII, and $/backticks inert."""
        assert _source_in_shell(encode_dotenv(AWKWARD), tmp_path) == AWKWARD

    def test_dollar_and_backticks_are_not_expanded(self, tmp_path):
        read_back = _source_in_shell(encode_dotenv(AWKWARD), tmp_path)

        assert read_back["DOLLAR"] == "$HOME ${PATH} $(echo pwned)"
        assert read_back["BACKTICK"] == "`echo pwned`"

    @pytest.mark.parametrize("name", ["MY-VAR", "1ST", "A;touch pwned", "A B", ""])
    def test_a_name_a_shell_cannot_assign_fails_fast(self, name):
        with pytest.raises(ValueError, match="not valid shell variable names"):
            encode_dotenv({name: "x", "GOOD": "y"})

    def test_the_error_names_every_bad_key(self):
        with pytest.raises(ValueError, match="1ST, MY-VAR"):
            encode_dotenv({"MY-VAR": "x", "1ST": "y"})


class TestEncodeJson:
    def test_a_flat_object_that_reads_back_exactly(self):
        assert json.loads(encode_json(AWKWARD)) == AWKWARD

    def test_keys_are_sorted_and_the_document_ends_with_a_newline(self):
        document = encode_json({"B": "two", "A": "one"})

        assert document == '{\n  "A": "one",\n  "B": "two"\n}\n'

    def test_non_ascii_is_kept_readable(self):
        document = encode_json({"NON_ASCII": "café ☕ 日本"})

        assert "café ☕ 日本" in document
        assert "\\u" not in document

    def test_empty_and_literal_unset_stay_distinct(self):
        document = encode_json({"EMPTY": "", "LITERAL_UNSET": "(unset)"})

        assert json.loads(document) == {"EMPTY": "", "LITERAL_UNSET": "(unset)"}

    def test_names_json_can_carry_are_not_rejected(self):
        """Only dotenv is limited to shell names; JSON has no such constraint."""
        assert json.loads(encode_json({"MY-VAR": "x"})) == {"MY-VAR": "x"}
