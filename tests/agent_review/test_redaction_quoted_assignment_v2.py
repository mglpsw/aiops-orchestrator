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


# ---------------------------------------------------------------------------
# Round 2. Adversarial review found six shapes the first fix missed, three of
# which emitted [REDACTED] while the plaintext survived on the same line --
# so the artifact asserted a redaction that had not happened.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_line",
    [
        # String prefixes. The ordered alternation matched the bare prefix
        # character and let the literal through -- while recording success.
        f'password = f"{_SECRET_V2}"',
        f"password = f'{_SECRET_V2}'",
        f'api_key = b"{_SECRET_V2}"',
        f'token = rb"{_SECRET_V2}"',
        f'token = R"{_SECRET_V2}"',
        f'password = u"{_SECRET_V2}"',
        # Triple quotes: a two-quote alternative matches the empty string at
        # the start of `"""` and consumes two of the three.
        f'password = """{_SECRET_V2}"""',
        f"password = '''{_SECRET_V2}'''",
        # Unquoted YAML -- the dominant YAML form, and the one a comment
        # incorrectly claimed was covered.
        f"password: {_SECRET_V2}",
        f"api_key: {_SECRET_V2}",
        # Key names absent from the old hand-written list.
        f'secret_key = "{_SECRET_V2}"',
        f'apikey = "{_SECRET_V2}"',
        f'api-key: "{_SECRET_V2}"',
        f'db_password = "{_SECRET_V2}"',
        f'private_key = "{_SECRET_V2}"',
        f'access_key = "{_SECRET_V2}"',
        f'SECRET_KEY = "{_SECRET_V2}"',
        f'credentials = "{_SECRET_V2}"',
    ],
)
def test_round_two_shapes_do_not_leak(source_line: str) -> None:
    """Every shape adversarial review found still leaking."""
    redacted = redact_text(source_line, RedactionState())

    assert _SECRET_V2 not in redacted, f"raw secret survived: {redacted!r}"
    assert REDACTED in redacted


def test_a_redaction_is_never_claimed_over_material_that_survived() -> None:
    """The worst property of the round-1 defect, pinned directly.

    Three shapes emitted ``[REDACTED]`` *and* recorded a replacement while the
    plaintext sat on the same line. An artifact that misreports its own
    sanitisation is worse than one that admits it did nothing, because a
    reviewer downstream has no reason to look.
    """
    for source_line in (
        f'password = f"{_SECRET_V2}"',
        f'password = """{_SECRET_V2}"""',
        f'api_key = b"{_SECRET_V2}"',
    ):
        state = RedactionState()
        redacted = redact_text(source_line, state)

        claimed = state.secret_like_values_found > 0
        leaked = _SECRET_V2 in redacted

        assert not (claimed and leaked), (
            f"claimed a redaction that did not happen: {redacted!r}"
        )
        assert not leaked


@pytest.mark.parametrize(
    "benign_line",
    [
        "password: str",
        "token: Optional[str]",
        "api_key: bytes",
        "secret: None",
        "api_key: str | None = None",
        "token_count = 5",
        "MAX_TOKENS = 100",
        "retry_secret_delay = 30",
        'password = "fake-token"',
        "token = 'test-token'",
        "password = ${VAULT_PASSWORD}",
        "token = $GITHUB_TOKEN",
        'password = "{{ vault_password }}"',
    ],
)
def test_benign_values_are_left_intact(benign_line: str) -> None:
    """The cost side of "suspect by default".

    Sparing a *closed* set of benign shapes fails safe; enumerating secret
    shapes loses to the next shape. But over-redaction still damages the
    review, so the benign set has to actually work.
    """
    assert redact_text(benign_line, RedactionState()) == benign_line


def test_the_string_prefix_survives_so_the_line_stays_readable() -> None:
    """Context preservation, extended to prefixes.

    Dropping the prefix would emit syntax the source never had, and keeping it
    outside the replacement is what produced ``password = [REDACTED]"real"``.
    """
    redacted = redact_text(f'password = f"{_SECRET_V2}"', RedactionState())

    assert redacted == 'password = f"[REDACTED]"'


def test_the_new_patterns_are_linear_in_input_length() -> None:
    """Guards against introducing catastrophic backtracking.

    Review flagged a pre-existing quadratic pattern elsewhere in this module
    (``_PRIVATE_KEY_RE``); the assignment rules must not add another.
    """
    import time

    durations = []
    for multiplier in (1, 2, 4, 8):
        text = 'password = "' + "a" * (8000 * multiplier)
        started = time.perf_counter()
        redact_text(text, RedactionState())
        durations.append(time.perf_counter() - started)

    # 8x the input must not cost anything like 8^2 the time.
    assert durations[-1] < durations[0] * 24, durations


def test_redaction_does_not_damage_this_repository_s_own_source() -> None:
    """The control that would have caught round 2's over-correction.

    Fixing authority E's leaks by making every value after a sensitive key
    suspect swung too far: measured against this repository's 138 source
    files, it altered **71 real lines** and left several syntactically
    broken --

        "prompt_tokens": usage.get("input_tokens", 0)
          -> "prompt_tokens": [REDACTED], 0)

    -- because ``max_tokens`` and ``prompt_tokens`` are *counts*. A
    hand-written benign corpus could not have found that; only real code
    could. Over-redaction is the safe direction for a credential, but handing
    a reviewer invalid syntax damages exactly the material the product exists
    to review, so it is not free.

    The only lines this may alter are in ``redaction.py`` itself, whose
    comments quote example secret assignments verbatim. That is correct
    behaviour on a file that documents secret shapes, and it is bounded here
    so it cannot quietly grow.
    """
    import pathlib

    package_root = pathlib.Path(
        __import__("app.agent_review", fromlist=["__file__"]).__file__
    ).parent
    application_root = package_root.parent

    damaged: list[str] = []
    for source_path in sorted(application_root.rglob("*.py")):
        for line_number, line in enumerate(
            source_path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if redact_text(line, RedactionState()) != line:
                damaged.append(f"{source_path.name}:{line_number}")

    offenders = [entry for entry in damaged if not entry.startswith("redaction.py:")]

    assert offenders == [], (
        f"redaction altered {len(offenders)} lines of ordinary source: {offenders[:10]}"
    )
    assert len(damaged) < 20, (
        "even redaction.py's own example-bearing comments should not grow "
        f"without notice: {len(damaged)}"
    )


@pytest.mark.parametrize(
    "ordinary_line",
    [
        '"max_tokens": max_tokens,',
        '"prompt_tokens": usage.get("input_tokens", 0),',
        "self.api_key = settings.claude_api_key",
        "command_token: SafeIdentifier",
        "old_path_token = tokens[index]",
        "if token == expected_token:",
        "token = ['\"']",
        "usage: _TokenUsageV1 = Field(default_factory=lambda: None)",
        'monkey = "banana"',
        "max_tokens = 100",
    ],
)
def test_ordinary_code_shapes_survive_redaction(ordinary_line: str) -> None:
    """Each of these was mangled by the first round-2 attempt.

    ``token == expected`` is included because the comparison was being read as
    an assignment and its second ``=`` consumed as the value -- a defect that
    predates this slice and is closed here.
    """
    assert redact_text(ordinary_line, RedactionState()) == ordinary_line
