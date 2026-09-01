"""#200-G2 ported adversarial corpus.

Every case here is drawn from the #277/#200-F forensic record (frozen,
never-merged branch `feat/200-f-derivable-operational-boundary`,
`STOP_200F_ARCHITECTURE_NOT_CONVERGING`) or from #200-G2's own issue text
(#280), plus new forms this slice adds. These are PORTED AS RED TESTS per
the recovery checkpoint's port ledger -- carried forward as failing-first
tests against the new primitive, not as passing inheritance from that
lineage. All of them are expected GREEN against the current (fixed)
`app/agent_review/redaction.py`; that fix is this slice's actual change.

Sources:
* round 1 (commit `ed6692d`): quoted secret assignments leaked entirely --
  the value class excluded quote characters, so `password = "..."` never
  matched at all.
* round 2 (commit `e910dfb`): six shapes still leaked after the round-1
  fix, three of them emitting `[REDACTED]` while the real plaintext
  survived on the same line.
* PR #277 close comment (authoritative, reproduced independently twice):
  `export API_KEY=...` and unquoted `password=...` leaking, ~44% random
  secret leak rate, JWTs spared entirely, a multiline triple-quoted secret
  reproducing the "claims redacted while leaking" defect, and a
  16,000-character adversarial line taking ~90s (quadratic).
"""

from __future__ import annotations

import random
import string
import time

import pytest

from app.agent_review.redaction import REDACTED, RedactionState, redact_content, redact_text


# ---------------------------------------------------------------------------
# Round 1 (ed6692d): quoted secret assignments across separators/keys/casing.
# ---------------------------------------------------------------------------

ROUND_1_QUOTED_SHAPES = [
    pytest.param('password = "super-secret-value"', "super-secret-value", id="double_quoted_eq"),
    pytest.param("password = 'super-secret-value'", "super-secret-value", id="single_quoted_eq"),
    pytest.param('password: "super-secret-value"', "super-secret-value", id="colon_double_quoted"),
    pytest.param('"password": "super-secret-value"', "super-secret-value", id="json_quoted_key"),
    pytest.param('secret = "super-secret-value"', "super-secret-value", id="secret_key_enumerated"),
    pytest.param('PASSWORD = "super-secret-value"', "super-secret-value", id="upper_case_key"),
    pytest.param('self.password = "super-secret-value"', "super-secret-value", id="attribute_target"),
]


@pytest.mark.parametrize("text,witness", ROUND_1_QUOTED_SHAPES)
def test_round1_quoted_secret_is_redacted(text: str, witness: str) -> None:
    redacted, report = redact_content(text)

    assert witness not in redacted
    assert report.secret_like_values_found >= 1


# ---------------------------------------------------------------------------
# Round 2 (e910dfb): keys matched by enumeration gap, not by shape.
# ---------------------------------------------------------------------------

ROUND_2_KEY_GAP_SHAPES = [
    pytest.param('secret_key = "abc123def456secret"', "abc123def456secret", id="secret_key"),
    pytest.param('apikey = "abc123def456secret"', "abc123def456secret", id="apikey_no_underscore"),
    pytest.param('signing_key = "abc123def456secret"', "abc123def456secret", id="signing_key"),
    pytest.param('access_key = "abc123def456secret"', "abc123def456secret", id="access_key"),
    pytest.param('encryption_key = "abc123def456secret"', "abc123def456secret", id="encryption_key"),
    pytest.param('session_token = "abc123def456secret"', "abc123def456secret", id="session_token"),
]


@pytest.mark.parametrize("text,witness", ROUND_2_KEY_GAP_SHAPES)
def test_round2_key_pattern_gap_is_redacted(text: str, witness: str) -> None:
    redacted, report = redact_content(text)

    assert witness not in redacted
    assert report.secret_like_values_found >= 1


def test_round2_no_false_claim_of_redaction() -> None:
    """The round-1 fix's defect, restated as an invariant: three shapes
    emitted `[REDACTED]` on the line while the real secret also survived on
    it -- an artifact that MISREPORTS its own sanitisation. For every
    witness above, REDACTED being present must not coincide with the
    witness also being present.
    """
    for text, witness in [p.values for p in ROUND_1_QUOTED_SHAPES] + [p.values for p in ROUND_2_KEY_GAP_SHAPES]:
        redacted, _ = redact_content(text)
        if REDACTED in redacted:
            assert witness not in redacted, f"false claim of redaction: {text!r} -> {redacted!r}"


# ---------------------------------------------------------------------------
# PR #277 close-comment witnesses (authoritative, independently reproduced).
# ---------------------------------------------------------------------------


def test_export_bare_assignment_redacted() -> None:
    redacted, _ = redact_content("export API_KEY=Zm9vYmFyYmF6")

    assert "Zm9vYmFyYmF6" not in redacted


def test_bare_password_assignment_redacted() -> None:
    redacted, _ = redact_content("password=Hunter2Value")

    assert "Hunter2Value" not in redacted


def test_random_secret_leak_rate_is_zero() -> None:
    """~44% of random secrets leaked in the #277 round-2 design, because a
    `(?i)`-flagged CapitalCase-looks-like-a-type-name heuristic spared
    exactly the base64/mixed-case shapes real secrets take. Generate a
    reproducible batch of random secret-shaped values (base64-ish,
    hex-ish, and mixed alnum) assigned to a spread of sensitive keys in
    both quoted and unquoted form, and require a ZERO leak rate -- not an
    improved one.
    """
    rng = random.Random(20260828)
    keys = ["password", "token", "secret", "api_key", "secret_key", "apikey", "signing_key", "access_token"]
    alphabets = [
        string.ascii_letters + string.digits + "+/",  # base64-ish
        string.hexdigits,  # hex-ish
        string.ascii_letters + string.digits,  # generic mixed alnum
    ]

    leaked = 0
    total = 200
    cases: list[tuple[str, str]] = []
    for _ in range(total):
        key = rng.choice(keys)
        alphabet = rng.choice(alphabets)
        length = rng.randint(16, 40)
        secret = "".join(rng.choice(alphabet) for _ in range(length))
        # Force at least one uppercase-first mixed-case shape: this is
        # exactly the CapitalCase-looking family the round-2 design spared.
        if rng.random() < 0.5:
            secret = secret[0].upper() + secret[1:]
        quoted = rng.random() < 0.5
        sep = rng.choice(["=", ": "])
        if quoted:
            quote = rng.choice(['"', "'"])
            text = f"{key}{sep}{quote}{secret}{quote}"
        else:
            text = f"{key}{sep}{secret}"
        cases.append((text, secret))

    for text, secret in cases:
        redacted, _ = redact_content(text)
        if secret in redacted:
            leaked += 1

    assert leaked == 0, f"{leaked}/{total} random secrets leaked (expected 0)"


JWT_EXAMPLE = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"Authorization: Bearer {JWT_EXAMPLE}", id="bearer_header"),
        pytest.param(f"token = {JWT_EXAMPLE}", id="enumerated_key_bare"),
        pytest.param(f'token = "{JWT_EXAMPLE}"', id="enumerated_key_quoted"),
        pytest.param(f"id_token={JWT_EXAMPLE}", id="non_enumerated_key"),
        pytest.param(f"jwt_value = {JWT_EXAMPLE}", id="jwt_named_key"),
        pytest.param(f"here is a token: {JWT_EXAMPLE} embedded in prose", id="bare_in_prose_no_key_context"),
    ],
)
def test_jwt_is_never_spared(text: str) -> None:
    """`#277`: "JWTs are spared entirely." A JWT must be caught by a
    standalone structural detector, independent of whether it happens to
    sit after a recognised key.
    """
    redacted, report = redact_content(text)

    assert JWT_EXAMPLE not in redacted
    assert report.secret_like_values_found >= 1


def test_multiline_triple_quoted_secret_is_not_falsely_claimed_safe() -> None:
    """`#277`: a multiline triple-quoted secret reproduced, byte-for-byte,
    the "claims [REDACTED] while leaking" defect a prior round had declared
    closed. Both lines' secret content must actually be gone if REDACTED
    is present; if the engine cannot bound the construct at all, it must
    say so via the flag, not silently leak.
    """
    text = 'token = """\nline-one-secret-abcdef123456\nline-two-secret-ghijkl789012\n"""\n'

    redacted, report = redact_content(text)

    assert "abcdef123456" not in redacted
    assert "ghijkl789012" not in redacted
    if REDACTED in redacted:
        assert "abcdef123456" not in redacted and "ghijkl789012" not in redacted


def test_unterminated_triple_quote_does_not_leak_and_is_flagged_unbounded() -> None:
    """The multiline construct with NO closing marker at all: the legacy
    best-effort API (`redact_text`) must still never leak the plaintext,
    and must set `unbounded_construct_present` so the new disposition layer
    can fail closed instead of claiming success.
    """
    # Built via concatenation, splitting "token" itself and never writing
    # a literal triple double-quote in this file's own source: a key match
    # immediately followed by three raw quote characters here in THIS
    # file would itself be a real-source-oracle witness (that shape
    # followed by the containing string's own closing quote, which a naive
    # whole-file scan could misdetect as the START of a new quoted value
    # and then search for an unrelated closing marker elsewhere in the
    # file -- exactly the class of bug this module's bounded-search-window
    # fix defends against). Constructing it this way avoids that
    # self-referential collision while testing the identical runtime
    # string value.
    text = "tok" + "en = " + '"' * 3 + "\nunterminated-secret-marker-abc123\nmore content, never closed"

    state = RedactionState()
    out = redact_text(text, state)

    assert "unterminated-secret-marker-abc123" not in out
    assert state.unbounded_construct_present is True


def test_redos_witness_stays_linear() -> None:
    """`#277`: a 16,000-character adversarial line took ~90s (quadratic:
    an unbounded lazy prefix retried at every offset). The same size input
    here must complete in well under a second, and doubling the input size
    must not roughly quadruple the time (the actual complexity claim, not
    just a single-point timing coincidence).
    """
    adversarial_16k = "a" * 16_000

    start = time.monotonic()
    redact_text(adversarial_16k, RedactionState())
    duration_16k = time.monotonic() - start

    assert duration_16k < 2.0, f"16k adversarial line took {duration_16k:.3f}s (must be << 90s)"

    durations: list[float] = []
    for size in (4_000, 8_000, 16_000, 32_000):
        text = "a" * size
        start = time.monotonic()
        redact_text(text, RedactionState())
        durations.append(time.monotonic() - start)

    # Quadratic growth would show each doubling costing ~4x; a generous
    # bound of 3x per doubling still clearly rejects O(n^2) while tolerating
    # ordinary measurement noise on a shared CI/dev box.
    for earlier, later in zip(durations, durations[1:]):
        ratio = later / earlier if earlier > 0 else 0.0
        assert ratio < 3.0, f"growth ratio {ratio:.2f} between {durations} looks superlinear"


def test_redos_witness_url_scheme_backtrack() -> None:
    """A second, independently-discovered quadratic shape in the SAME
    module (`_CREDENTIAL_URL_RE`'s unbounded scheme class backtracking
    against text that merely resembles a URL scheme): pre-existing on
    current master, found while proving the linear-envelope claim for the
    module as a whole, not part of the #277 lineage's known corpus but the
    same defect class and fixed in the same slice.
    """
    adversarial = "abcdefghij." * 8_000  # ~88k chars, looks scheme-ish, never matches

    start = time.monotonic()
    redact_text(adversarial, RedactionState())
    duration = time.monotonic() - start

    assert duration < 2.0, f"url-scheme-shaped adversarial input took {duration:.3f}s"
