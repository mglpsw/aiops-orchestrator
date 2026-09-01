"""`#200-F` authority E -- quoted secret assignments. HARD pre-canary blocker.

`redaction.py`'s assignment rule matched a value class of ``[^\\s&;,\\"']+``,
which **excludes quote characters**. The dominant shape of a hard-coded secret
in source code::

    password = "super-secret-value"

therefore never matched: immediately after ``=`` the next character is a
quote, which the class forbids, so the pattern failed at the first value
character. The bare form ``password = super-secret-value`` redacted correctly,
which is why the gap survived -- the rule looked exercised.

The `#276` note recorded this as narrower than it is. Measured on the merged
code, the leak covers single quotes, double quotes, colon separators, every
key in the sensitive set, upper case and attribute targets. Only the bare
``key = value`` form was ever redacted.

The escape guard cannot compensate: ``_redaction_escaped_v2`` asks
``sanitize_artifact_value`` whether anything is left to redact, and that runs
**the same pattern set**. A miss is invisible to it by construction. That is
why this is closed in the patterns and not behind another guard.

No live Router or provider call is made anywhere in this file.
"""

from __future__ import annotations

import pytest

from app.agent_review.redaction import (
    REDACTED,
    RedactionState,
    redact_text,
    sanitize_artifact_value,
)

_SECRET_V2 = "super-secret-value"


@pytest.mark.parametrize(
    "source_line",
    [
        f'password = "{_SECRET_V2}"',
        f"password = '{_SECRET_V2}'",
        f'password="{_SECRET_V2}"',
        f"password='{_SECRET_V2}'",
        f'PASSWORD = "{_SECRET_V2}"',
        f'self.password = "{_SECRET_V2}"',
        f'token = "{_SECRET_V2}"',
        f'secret = "{_SECRET_V2}"',
        f'api_key = "{_SECRET_V2}"',
        f'client_secret = "{_SECRET_V2}"',
        f'access_token = "{_SECRET_V2}"',
        f'refresh_token = "{_SECRET_V2}"',
        f'password: "{_SECRET_V2}"',
        f"password: '{_SECRET_V2}'",
        f'api_key: "{_SECRET_V2}"',
        f'"password": "{_SECRET_V2}"',
    ],
)
def test_a_quoted_secret_assignment_never_survives_redaction(source_line: str) -> None:
    """The witness, in every spelling that occurs in real source.

    Started RED for all sixteen: the merged rule matched none of them.
    """
    redacted = redact_text(source_line, RedactionState())

    assert _SECRET_V2 not in redacted, f"raw secret survived: {redacted!r}"
    assert REDACTED in redacted


@pytest.mark.parametrize(
    "source_line",
    [
        f"password = {_SECRET_V2}",
        f"token={_SECRET_V2}",
        f"api_key = {_SECRET_V2}",
    ],
)
def test_the_bare_assignment_form_still_redacts(source_line: str) -> None:
    """Regression guard on the one form that already worked.

    A fix that traded the bare form for the quoted form would be no fix.
    """
    redacted = redact_text(source_line, RedactionState())

    assert _SECRET_V2 not in redacted
    assert REDACTED in redacted


def test_the_sanitised_assignment_is_still_reviewable_code() -> None:
    """Redaction must not destroy the reviewability it exists to protect.

    A reviewer needs to see *that* a credential is assigned, to which name,
    with what syntax. Only the value is removed; the key, the separator and
    the quoting survive, so the line still reads as the assignment it is.
    """
    redacted = redact_text(
        f'    self.password = "{_SECRET_V2}"  # loaded at boot',
        RedactionState(),
    )

    assert _SECRET_V2 not in redacted
    assert "self.password" in redacted
    assert " = " in redacted, "the separator and its spacing are context"
    assert '"' in redacted, "quoting style is context a reviewer uses"
    assert "# loaded at boot" in redacted, "surrounding code must survive"
    assert redacted.startswith("    "), "indentation is context"


def test_a_placeholder_value_is_left_alone_even_when_quoted() -> None:
    """Non-vacuity control.

    A rule that redacted every quoted string after a sensitive key would pass
    every test above and ruin ordinary fixtures and documentation.
    """
    for placeholder in ('password = "fake-token"', "token = 'test-token'"):
        redacted = redact_text(placeholder, RedactionState())
        assert REDACTED not in redacted, redacted


def test_a_python_type_annotation_is_not_mistaken_for_an_assignment() -> None:
    """The colon form is restricted to quoted values, deliberately.

    ``password: str`` is a type annotation, not a secret. Redacting it would
    damage exactly the code a reviewer must read, so the colon separator only
    matches a quoted value -- which covers YAML and JSON, where the leak
    actually occurs, without touching annotations.
    """
    for annotation in ("password: str", "token: Optional[str]", "api_key: bytes"):
        redacted = redact_text(annotation, RedactionState())
        assert redacted == annotation, redacted


def test_the_state_records_the_redaction_so_it_is_countable() -> None:
    """A silent redaction cannot be audited.

    The report is what tells an operator a secret was found at all.
    """
    state = RedactionState()
    redact_text(f'password = "{_SECRET_V2}"', state)

    assert state.secret_like_values_found >= 1


def test_the_escape_guard_now_agrees_with_the_redactor() -> None:
    """``sanitize_artifact_value`` shares the pattern set, and must stay in step.

    Before this fix the guard reported "nothing left to redact" for a line
    that still contained a plaintext password. It could not have been
    otherwise: it consults the same patterns, so a pattern gap is invisible to
    it. Fixing the patterns fixes both, which is why the remedy belongs there
    rather than in another layer of guard.
    """
    line = f'password = "{_SECRET_V2}"'

    sanitised = sanitize_artifact_value(line)

    assert _SECRET_V2 not in sanitised
    assert sanitised != line


def test_multiple_secrets_on_one_line_are_all_removed() -> None:
    """Substitution must be global, not first-match."""
    line = f'password = "{_SECRET_V2}" and token = "{_SECRET_V2}-2"'

    redacted = redact_text(line, RedactionState())

    assert _SECRET_V2 not in redacted
    assert redacted.count(REDACTED) == 2


def test_an_unterminated_quote_does_not_swallow_the_rest_of_the_file() -> None:
    """Value patterns are line-bounded on purpose.

    A greedy quoted-value rule could match across newlines and redact an
    entire file body into one placeholder, destroying the review.
    """
    text = f'password = "{_SECRET_V2}\ndef unrelated_function():\n    return 1\n'

    redacted = redact_text(text, RedactionState())

    assert "def unrelated_function():" in redacted
    assert "return 1" in redacted
