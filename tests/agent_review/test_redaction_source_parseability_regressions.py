"""#200-G2: parseability regressions found by the real-source differential
oracle (`scripts/agent-review-safe-material-differential-oracle.py`).

Each case here reproduces an ACTUAL defect the oracle found when running
the engine across this repository's own `app/`, `scripts/`, and `tests/`
trees -- not a hand-imagined edge case. All of them share one root
mechanism: a value-scanning boundary that didn't exclude a character with
its own syntactic meaning in the SURROUNDING source (a quote, a bracket, a
backslash-escape), so the scanner consumed that character as if it were
part of "the secret value" and discarded it on replacement, corrupting the
source around the redaction rather than just the redaction itself.

Every case is checked with `ast.parse` -- the transformed output must
remain valid Python, not merely "close enough".
"""

from __future__ import annotations

import ast

import pytest

from app.agent_review.redaction import RedactionState, redact_text


def _assert_parses(source: str) -> str:
    out = redact_text(source, RedactionState())
    try:
        ast.parse(out)
    except SyntaxError as exc:  # pragma: no cover - failure path
        pytest.fail(f"transformed output is not valid Python: {exc}\n--- output ---\n{out}")
    return out


def test_bearer_value_does_not_consume_enclosing_quote() -> None:
    # `_AUTHORIZATION_BEARER_RE`'s value class did not exclude `"`/`'`, so
    # this consumed the string's own closing quote as "part of the token".
    source = 'redacted, report = redact_content("Authorization: Bearer abcdefghijklmnop")\n'
    out = _assert_parses(source)
    assert "abcdefghijklmnop" not in out


def test_cookie_value_does_not_consume_enclosing_quote() -> None:
    # `_COOKIE_RE`'s value class was `[^\r\n]+` -- same defect, plus it
    # consumed through to end of physical line regardless of quotes.
    source = 'headers = ["Authorization:", "Bearer ", "Cookie: session=abc123", "next"]\n'
    out = _assert_parses(source)
    assert "session=abc123" not in out


def test_bare_value_scan_does_not_consume_enclosing_quote() -> None:
    # The key/value scanner's bare-value terminator set did not include
    # `"`/`'`, so a value ending right at the enclosing string's own
    # closing quote (e.g. two adjacent `key=value` pairs inside one
    # call argument) consumed that quote too.
    source = 'redact_content("password=secret-value client_secret=another-secret")\n'
    out = _assert_parses(source)
    assert "secret-value" not in out
    assert "another-secret" not in out


def test_bare_value_scan_stops_before_closing_paren() -> None:
    # `def f(token: str) -> bool:` -- the bare-value scan swallowed `str)`
    # as one token and discarded the `)` with it, breaking the signature.
    source = "def check(token: str) -> bool:\n    return bool(token)\n"
    out = _assert_parses(source)
    assert ") -> bool:" in out


def test_bare_value_scan_stops_before_fstring_interpolation_brace() -> None:
    # `f"api_key={value}"` used as a log message -- the bare-value scan
    # swallowed the interpolation's opening `{`, leaving a lone `}`.
    source = 'reason = f"api_key=sk-test-token-{idx}"\n'
    out = _assert_parses(source)
    assert "sk-test-token-" not in out


def test_bare_value_scan_preserves_trailing_newline_escape() -> None:
    # A value ending right before a literal `\n` escape (backslash + `n`,
    # two source characters) plus the enclosing quote had both consumed
    # and discarded on replacement.
    source = 'lines = ["Authorization: Bearer super-secret-token\\n"]\n'
    out = _assert_parses(source)
    assert "super-secret-token" not in out
    assert out.count("\\n") >= 1


def test_empty_value_adjacent_to_enclosing_quote_is_not_misdetected() -> None:
    # `assert b"token=" not in canonical` -- the `"` right after `=` is the
    # ENCLOSING b-string's own closing delimiter, not a new value opening.
    # A naive scanner searching for the "next" quote on the line found an
    # unrelated later one and swallowed everything in between.
    source = 'assert b"token=" not in canonical\nassert b"Bearer " not in canonical\n'
    out = _assert_parses(source)
    assert out == source


def test_key_match_inside_string_does_not_hunt_for_a_distant_unrelated_close() -> None:
    # A literal triple-quote-shaped run of characters occurring as DATA
    # inside an already-open, differently-quoted string must not make the
    # triple-quote closer search past this construct into an unrelated
    # later triple-quoted string (e.g. a following function's docstring)
    # and swallow everything in between into one `[REDACTED]`.
    marker = '"' * 3
    source = (
        "def f():\n"
        f"    text = 'tok' + 'en = ' + {marker!r} + \"\\nsecret-marker-abc123\\nnever closed\"\n"
        "    return text\n"
        "\n"
        "\n"
        "def g():\n"
        '    """A real docstring far enough away that an unbounded search\n'
        "    would otherwise reach it.\n"
        '    """\n'
        "    return 1\n"
    )
    out = _assert_parses(source)
    # `g`'s docstring and body must survive completely untouched.
    assert "def g():" in out
    assert "return 1" in out


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('self.api_key = settings.claude_api_key\n', id="dotted_reference"),
        pytest.param("token = tokens[index]\n", id="subscript_reference"),
        pytest.param("if token == expected_token:\n    pass\n", id="comparison_eq"),
        pytest.param(
            "class C:\n    def __init__(self, token: str, api_key: bytes) -> None:\n        self.token = token\n        self.api_key = api_key\n",
            id="constructor_signature_and_bare_attribute_assignment",
        ),
        pytest.param(
            "client = GitHubClient(token=token, repository=repository)\n",
            id="same_name_keyword_argument",
        ),
        pytest.param(
            "def f(*, api_key: str | None = None) -> None:\n    pass\n",
            id="pep604_union_annotation",
        ),
    ],
)
def test_ordinary_source_is_byte_identical_after_redaction(source: str) -> None:
    out = _assert_parses(source)
    assert out == source
