"""Deterministic redaction for AgentReview intake artifacts.

#200-G2 note (see docs/checkpoints/AGENT_REVIEW_V2_200G2_SAFE_REVIEW_MATERIAL.md):
this module is the SAME shipping module master has carried since
9b2fcce/3c17ab1 -- it is unrelated to the #277/#200-F round-2 "suspect
unless benign" quoted-secret redesign (frozen forensic, DO_NOT_PORT,
branch `feat/200-f-derivable-operational-boundary`, never merged). This
slice extends it in place rather than porting that redesign: quoted secret
values, a handful of additional key names, and standalone credential-shape
detectors (JWT, common vendor token prefixes) are new; the underlying
strategy stays a literal key-alternation plus a hand-written linear value
scanner, not a single expanding regex.

The reason for the hand-written scanner instead of one bigger regex is the
specific failure mode the #277 lineage reproduced twice: an unbounded lazy
prefix (`X*?LITERAL`) retried at every character offset is O(n) per offset,
so a non-matching adversarial line becomes O(n^2) -- a 16,000-character line
took ~90s in that design. `_scan_and_redact_key_values` below matches key
literals by direct bounded slice comparison (no unanchored prefix search)
and advances its cursor past every value span it extracts, so each
character is visited a bounded number of times: linear, not quadratic. See
``tests/agent_review/test_redaction_200f_red_corpus.py`` for the ported
adversarial corpus and the performance envelope test.

The other recurring #277 defect -- a value spared because it merely
"looked like" a type name (a `(?i)`-flagged CapitalCase heuristic that
matched base64-ish secrets just as readily as real type names, the root
cause of the ~44% random-secret leak and the `Hunter2Value`/`Zm9vYmFyYmF6`
witnesses) -- is deliberately not reproduced here. Sparing an *unquoted*
value is only ever done on a STRUCTURAL basis (it contains `.`, `(`, `[` or
`{`, i.e. it can only be a reference/call/subscript/collection in valid
source, never a string literal) -- never on casing. A quoted value is never
spared on structural grounds at all, only via the placeholder list, because
a quoted string containing a `.` (e.g. a dotted secret, or a JWT if it were
ever quoted) is data, not code.
"""

from __future__ import annotations

import base64
import re
from collections import Counter
from typing import Any

from app.agent_review.schemas import RedactionReport


REDACTED = "[REDACTED]"

SENSITIVE_KEYS = {
    "authorization",
    "token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "pwd",
    "secret",
    "secret_key",
    "client_secret",
    "access_token",
    "refresh_token",
    "signing_key",
    "access_key",
    "encryption_key",
    "session_token",
    "auth_token",
    "private_key",
}

PLACEHOLDER_VALUES = {
    REDACTED.lower(),
    "***masked***",
    "placeholder",
    "fake-token",
    "test-token",
    "dummy",
    "example",
    "local-only",
}

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
# #200-G2: the value class was `[^\s,;]+` -- it did NOT exclude a quote
# character, so applied to raw SOURCE TEXT (as opposed to an already-parsed
# runtime string) it consumed a literal closing `"`/`'` immediately after
# the token as part of "the value", then discarded it along with the token
# on replacement -- turning `"Authorization: Bearer abc"` into
# `"Authorization: Bearer [REDACTED]` with no closing quote, an unterminated
# string literal. Found by the real-source differential oracle (this
# module's own test file exercises exactly this shape). `_BEARER_RE` just
# below never had this defect -- its class already excludes quotes.
_AUTHORIZATION_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*)bearer\s+([^\s,;\"'{}()\[\]\\]+)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{8,})")
# #200-G2: the value class was `[^\r\n]+` -- same defect class as
# `_AUTHORIZATION_BEARER_RE` above (found by the real-source oracle): on
# raw source text it consumes through to end of PHYSICAL line, including a
# literal closing `"`/`'` and trailing `,`/`)` that belong to the
# surrounding Python source, not the cookie value, and discards them on
# replacement -- an unterminated string literal.
_COOKIE_RE = re.compile(r"(?i)\b(set-cookie|cookie)\s*:\s*([^\r\n\"'\\]+)")
_GITHUB_TOKEN_RE = re.compile(r"\b(ghp_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,})\b")
_OPENAI_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
# #200-G2: the scheme group was `[a-z][a-z0-9+.-]*` -- UNBOUNDED, and '.'
# and '-' are both in its own class, so on adversarial text that merely
# resembles a scheme (letters/dots/dashes with no `://` ever appearing) the
# greedy quantifier consumes the rest of the string and then backtracks one
# character at a time looking for the literal `://`, retried at every start
# offset: the exact O(n^2) shape #277 reproduced elsewhere, and reproduced
# empirically here pre-existing on current master (`'abcdefghij.' * 8000`
# measured ~5s in `_sub_credential_urls` alone before this bound was added).
# Real URL schemes are a handful of characters (`postgres`, `mysql+psycopg2`,
# `redis`, ...); bounding the repeat caps backtrack cost at a constant per
# offset, restoring O(n) total.
_DATABASE_URL_RE = re.compile(
    r"(?i)\b(DATABASE_URL\s*=\s*)([a-z][a-z0-9+.-]{0,20}://)([^:\s/@]+):([^@\s]+)@"
)
_CREDENTIAL_URL_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{0,20}://)([^:/@\s]+):([^/@\s]+)@")
_UNIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.~-])/(?:[A-Za-z0-9._@+=:-]+/)+[A-Za-z0-9._@+=:-]+")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+")
_HOME_RELATIVE_PATH_RE = re.compile(r"(?<![\w.~/-])~/(?:[A-Za-z0-9._@+=:-]+/)*[A-Za-z0-9._@+=:-]+")

# Known vendor credential shapes. Each quantifier is length-bounded (never a
# bare `+`/`*`/unbounded `{n,}` next to another quantified alternative) so a
# long non-matching run cannot drive backtracking cost above O(n) -- see the
# module docstring and the ReDoS regression test.
_AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,72}\b")
_GITLAB_TOKEN_RE = re.compile(r"\bglpat-[0-9A-Za-z_-]{20,50}\b")
_NPM_TOKEN_RE = re.compile(r"\bnpm_[0-9A-Za-z]{36}\b")
_GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")
_STRIPE_KEY_RE = re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{10,99}\b")

# JWT structure. Each of the three segments is length-BOUNDED ({10,500}) --
# unbounded segments next to two required literal '.' anchors is exactly the
# multi-quantifier shape that makes a non-matching long run quadratic
# (greedy segment 1 consumes to end of string, backtracks one char at a
# time looking for '.', retried at every start offset). Bounding each
# segment caps the backtrack cost per offset at a constant, so total cost
# stays O(n). The regex is a cheap SHAPE filter only; `_looks_like_jwt`
# below does the real structural validation (does the header segment
# base64url-decode to a JSON object) so an incidental "word.word.word" in
# ordinary prose or code doesn't get flagged.
_JWT_CANDIDATE_RE = re.compile(
    r"\b[A-Za-z0-9_-]{10,500}\.[A-Za-z0-9_-]{10,500}\.[A-Za-z0-9_-]{10,500}\b"
)

# Bound on how far a triple-quote CLOSE search is allowed to look ahead.
# See `_scan_value_span`'s inline comment: an "opening" triple-quote that
# is really just three literal quote characters inside an already-open,
# differently-quoted string must not let `str.find` walk arbitrarily far
# into the rest of the file looking for an unrelated triple-quoted string
# to treat as "the close". 20,000 characters is generous for any
# legitimate large multi-line secret block while bounding blast radius.
_MAX_TRIPLE_QUOTE_SEARCH_WINDOW = 20_000

_ASSIGNMENT_KEY_FORMS: tuple[str, ...] = tuple(
    sorted(
        (SENSITIVE_KEYS | {"credential", "credentials", "client_id_secret"}) - {"authorization"},
        key=len,
        reverse=True,
    )
)
# `authorization` is deliberately excluded from the generic key=value/key:
# value scanner above: an HTTP header value is often two tokens
# ("Bearer <opaque>"), which the bare-value scanner (stops at whitespace)
# would truncate and mis-redact only the "Bearer" word. It stays in
# SENSITIVE_KEYS for JSON-field redaction, and `_sub_authorization_bearer`/
# `_sub_bearer` already cover the header-value text form specifically.
_WORD_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)
# Bare-word sentinels, plus a small, explicitly enumerated set of builtin/
# typing names. The latter is a deliberate, named CLOSED-LIST trade-off --
# the same shape as the `*_key` enumeration trade-off elsewhere in this
# module -- not a "looks like a type" shape heuristic: enumerating type
# NAMES misses project-defined ones (`token: SafeIdentifier` isn't covered
# here; the trailing-`=`-lookahead further down covers the "has a default
# value" case, and an annotation-only project-defined type on a sensitive
# parameter name remains a known limitation, not a false claim). This list
# exists because the real-source differential oracle (see checkpoint)
# measured it as the dominant real damage source: ordinary function
# signatures like `def f(token: str, api_key: bytes) -> None` are far more
# common in this repository than any real colon-form secret in a .py file.
_BENIGN_UNQUOTED_LITERALS = {
    "true", "false", "none", "null", "nil", "undefined",
    "str", "int", "float", "bool", "bytes", "bytearray", "complex",
    "list", "dict", "set", "tuple", "frozenset", "object", "type",
    "any", "optional", "union", "sequence", "mapping", "iterable",
    "iterator", "callable", "awaitable", "coroutine", "generator", "self",
}
_NUMERIC_RE = re.compile(r"^[-+]?\d[\d_]*(\.\d+)?([eE][-+]?\d+)?$")
_TEMPLATE_RE = re.compile(r"^(\$\{[^\n]*\}|\{\{[^\n]*\}\}|\$[A-Za-z_][A-Za-z0-9_]*)$")


class RedactionState:
    def __init__(self) -> None:
        self.files_processed = 0
        self.replacements_by_type: Counter[str] = Counter()
        self.limitations: list[str] = []
        # #200-G2 additions, additive-only: existing callers that only read
        # files_processed/replacements_by_type/limitations are unaffected.
        self.redacted_witnesses: list[str] = []
        self.unbounded_construct_present: bool = False

    @property
    def secret_like_values_found(self) -> int:
        return sum(self.replacements_by_type.values())

    def record_file(self) -> None:
        self.files_processed += 1

    def record(self, replacement_type: str) -> None:
        self.replacements_by_type[replacement_type] += 1

    def record_witness(self, witness: str) -> None:
        if witness:
            self.redacted_witnesses.append(witness[:2000])

    def to_report(self, *, source: str = "aiops-review-intake", output_safe_for_llm: bool = True) -> RedactionReport:
        redacted = self.secret_like_values_found > 0
        return RedactionReport(
            source=source,
            files_processed=self.files_processed,
            replacements_by_type=dict(sorted(self.replacements_by_type.items())),
            secret_like_values_found=self.secret_like_values_found,
            redacted_lines_present=redacted,
            redaction_is_sanitizer_artifact=redacted,
            hardcoded_secret_confirmed=False,
            output_safe_for_llm=output_safe_for_llm,
            limitations=list(self.limitations),
        )


def redact_content(value: Any, *, source: str = "redaction-test") -> tuple[Any, RedactionReport]:
    state = RedactionState()
    state.record_file()
    redacted = redact_value(value, state)
    return redacted, state.to_report(source=source)


def redact_value(value: Any, state: RedactionState) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_sensitive_field(key, child, state)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, state) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, state) for item in value]
    if isinstance(value, str):
        return redact_text(value, state)
    return value


def sanitize_artifact_value(value: Any) -> Any:
    """Redact secrets and local paths before emitting an uploadable artifact."""
    state = RedactionState()
    state.record_file()
    return _redact_local_paths(redact_value(value, state))


def redact_text(text: str, state: RedactionState) -> str:
    redacted = _sub_private_keys(text, state)
    redacted = _sub_jwt_tokens(redacted, state)
    redacted = _sub_known_credential_prefixes(redacted, state)
    redacted = _sub_authorization_bearer(redacted, state)
    redacted = _sub_bearer(redacted, state)
    redacted, unbounded = _scan_and_redact_key_values(redacted, state)
    if unbounded:
        state.unbounded_construct_present = True
    redacted = _sub_cookie_headers(redacted, state)
    redacted = _sub_simple_tokens(redacted, state)
    redacted = _sub_database_urls(redacted, state)
    redacted = _sub_credential_urls(redacted, state)
    return redacted


def _redact_sensitive_field(key: Any, value: Any, state: RedactionState) -> Any:
    if _normalize_key(key) in SENSITIVE_KEYS and isinstance(value, str) and not _is_placeholder(value):
        state.record("sensitive_json_field")
        state.record_witness(value)
        return REDACTED
    return redact_value(value, state)


def _sub_private_keys(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        state.record("private_key")
        state.record_witness(match.group(0))
        return REDACTED

    return _PRIVATE_KEY_RE.sub(replace, text)


def _looks_like_jwt(token: str) -> bool:
    parts = token.split(".")
    if len(parts) != 3 or any(len(part) < 5 for part in parts):
        return False
    header = parts[0]
    padded = header + "=" * (-len(header) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception:
        return False
    return decoded.startswith(b'{"') or decoded.startswith(b'{ "') or decoded.startswith(b'{\n')


def _sub_jwt_tokens(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if not _looks_like_jwt(candidate) or _is_placeholder(candidate):
            return candidate
        state.record("jwt")
        state.record_witness(candidate)
        return REDACTED

    return _JWT_CANDIDATE_RE.sub(replace, text)


def _sub_known_credential_prefixes(text: str, state: RedactionState) -> str:
    patterns = (
        ("aws_access_key", _AWS_ACCESS_KEY_RE),
        ("slack_token", _SLACK_TOKEN_RE),
        ("gitlab_token", _GITLAB_TOKEN_RE),
        ("npm_token", _NPM_TOKEN_RE),
        ("google_api_key", _GOOGLE_API_KEY_RE),
        ("stripe_key", _STRIPE_KEY_RE),
    )
    redacted = text
    for label, pattern in patterns:
        def replace(match: re.Match[str], _label: str = label) -> str:
            candidate = match.group(0)
            if _is_placeholder(candidate):
                return candidate
            state.record(_label)
            state.record_witness(candidate)
            return REDACTED

        redacted = pattern.sub(replace, redacted)
    return redacted


def _sub_authorization_bearer(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(2)
        if _is_placeholder(token):
            return match.group(0)
        state.record("authorization_bearer")
        state.record_witness(token)
        return f"{match.group(1)}Bearer {REDACTED}"

    return _AUTHORIZATION_BEARER_RE.sub(replace, text)


def _sub_bearer(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_placeholder(token) or token == REDACTED:
            return match.group(0)
        state.record("bearer_token")
        state.record_witness(token)
        return f"Bearer {REDACTED}"

    return _BEARER_RE.sub(replace, text)


def _is_word_char(ch: str) -> bool:
    return ch in _WORD_CHARS


def _match_key_at(text: str, lowered: str, i: int) -> str | None:
    """Return the sensitive key literal matched at position ``i``, if any.

    Direct bounded slice comparison against a fixed, small alternative set --
    no unanchored `.*?`/`X*?` prefix search retried at every offset. That is
    the mechanism difference from the #277 lineage's quadratic defect (see
    module docstring): a failed attempt here costs O(len(longest key)),
    a small constant, never O(remaining text).
    """
    if i > 0 and _is_word_char(text[i - 1]):
        return None
    for key in _ASSIGNMENT_KEY_FORMS:
        end = i + len(key)
        if lowered[i:end] == key and not (end < len(text) and _is_word_char(text[end])):
            return text[i:end]
    return None


def _scan_value_span(text: str, start: int) -> tuple[str, int, bool]:
    """Return ``(value_text, end_index, bounded)`` for the value starting at
    ``start``.

    ``bounded=False`` only for an opened triple-quoted string whose closing
    marker does not appear anywhere later in ``text`` -- its true extent is
    unknowable from this text alone (it may continue past a chunk boundary
    this function cannot see). Every other case, including an unterminated
    single/double-quoted string, is bounded to end-of-line: a truncated
    secret is still a secret and "end of line" is a hard limit, not a guess.
    """
    n = len(text)
    i = start
    prefix_start = i
    while i < n and text[i] in "fFbBrRuU" and (i - prefix_start) < 2:
        i += 1
    if i >= n or text[i] not in "\"'":
        # A bare (unquoted) value stops at whitespace, at a small set of
        # separators no legitimate token spans (`,`, `;`, `&` -- carried
        # over from the pre-#200-G2 value class), at `)`, `]`, `}` (real-
        # source differential-oracle evidence, see checkpoint: found
        # `def f(token: str) -> bool:` losing its own closing paren --
        # `str)` was consumed whole as "the value" and the paren discarded
        # with it, breaking the signature's syntax), and at `"`/`'` (same
        # oracle: a bare value scan running into the CLOSING quote of the
        # enclosing Python string literal -- e.g. `redact_content("...
        # client_secret=another-secret")` -- swallowed that quote as if it
        # were part of the token and discarded it on replacement, producing
        # an unterminated string literal). None of these eight characters
        # can appear inside a legitimate bare identifier/number/base64-ish
        # token either, so this narrows correctly in both directions. `{`
        # is included too: an f-string like `f"api_key={value}"` used as a
        # log/prose message (the literal text "api_key=" followed by an
        # interpolation, not a real assignment) otherwise had the `{idx}`
        # placeholder's opening brace consumed as "part of the value",
        # leaving a lone unmatched `}` -- a SyntaxError, not just an
        # over-redaction (found by the real-source oracle). `\` is
        # excluded too: on raw source text a trailing `\n` escape (two
        # literal characters, backslash then `n`) right before a value's
        # enclosing quote was consumed as "part of the value" and
        # discarded on replacement, silently dropping the escape.
        j = start
        while j < n and text[j] not in " \t\r\n,;&)]}{\"'\\":
            j += 1
        return text[start:j], j, True
    quote = text[i]
    triple = quote * 3
    if text[i : i + 3] == triple:
        # Bounded search window, not an unbounded `text.find` to end of
        # text: the real-source oracle found a case where the "opening"
        # triple-quote wasn't a genuine Python delimiter at all -- three
        # literal quote characters occurring as DATA inside an already-open
        # (differently-quoted) string -- and the unbounded search walked
        # past the intended nearby content to the next UNRELATED
        # triple-quoted string much later in the same file (another
        # function's docstring), swallowing everything between the two
        # into one `[REDACTED]`. A generous but finite window bounds the
        # blast radius of that misdetection AND keeps the worst-case find
        # cost bounded rather than proportional to the rest of the file.
        window_end = min(n, i + 3 + _MAX_TRIPLE_QUOTE_SEARCH_WINDOW)
        close = text.find(triple, i + 3, window_end)
        if close == -1:
            return text[start:n], n, False
        end = close + 3
        return text[start:end], end, True
    # Single/double (non-triple) quote. If a matching close is found before
    # end-of-line, this is a genuine short quoted value. If NOT found, this
    # is deliberately NOT treated as "an unterminated secret, redact to
    # EOL" (an earlier version of this scanner did that, and the real-
    # source oracle found it misfiring on `assert b"token=" not in ...`:
    # the `"` immediately after `token=` is the ENCLOSING b-string's own
    # CLOSING delimiter, not the start of a new value, so "no close found
    # on this line" here is much more often a false nesting detection than
    # a real truncated secret. `bounded=False` lets the caller spare this
    # occurrence entirely rather than guess-redact it -- see
    # `_scan_and_redact_key_values`.
    j = i + 1
    while j < n and text[j] != "\n":
        if text[j] == "\\" and j + 1 < n:
            j += 2
            continue
        if text[j] == quote:
            j += 1
            return text[start:j], j, True
        j += 1
    return text[start:j], j, False


def _unwrap_value(value: str) -> tuple[str, str, str]:
    """Split a scanned value into ``(string_prefix, quote_marker, inner)``.

    ``quote_marker`` is ``""`` for a bare value. Keeping the prefix (``f``,
    ``b``, ``r``, ``u`` and 2-letter combinations) and quote style separate
    from the redacted payload is what lets the caller reinstate them, so
    ``f"secret"`` becomes ``f"[REDACTED]"`` rather than losing its syntax.
    """
    index = 0
    while index < len(value) and value[index] in "fFbBrRuU" and index < 2:
        index += 1
    prefix, remainder = value[:index], value[index:]
    if not remainder or remainder[0] not in "\"'":
        return "", "", value
    quote = remainder[0]
    triple = quote * 3
    if remainder.startswith(triple):
        if remainder.endswith(triple) and len(remainder) >= 6:
            return prefix, triple, remainder[3:-3]
        return prefix, triple, remainder[3:]
    if len(remainder) >= 2 and remainder[-1] == quote:
        return prefix, quote, remainder[1:-1]
    return prefix, quote, remainder[1:]


# A genuine Python dotted reference/attribute chain (`settings.claude_api_key`,
# `self.api_key`). The FIRST segment is required to start lowercase or `_`
# -- deliberately, not incidentally: a real-source regression run (see
# checkpoint differential-oracle results) found this carve-out, when it
# allowed an uppercase-starting first segment, spared values like
# `AGENTESCALA_PHASE3_FIXTURE_SECRET.py` (a secret embedded in a bare
# filename-shaped string -- the dot is a file extension, not a reference
# operator). Python attribute/variable access overwhelmingly starts
# lowercase by convention (`self`, `settings`, module names); a value
# starting uppercase is far more likely to be DATA that happens to contain
# a literal '.' than a live reference, so it is not spared this way.
_DOTTED_REFERENCE_RE = re.compile(r"^[a-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
# An identifier (optionally dotted) immediately followed by `(` or `[` --
# a call or subscript (`get_secret()`, `tokens[index]`), never a data
# literal. Anchored at the start, unlike the retired "contains '(' or '['
# anywhere" rule, so a value that merely happens to CONTAIN a bracket
# somewhere in the middle (not as its own opening token) is not spared.
_CALL_OR_SUBSCRIPT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*[(\[]")
# A bare value that itself opens with a collection/tuple literal marker.
_COLLECTION_LITERAL_RE = re.compile(r"^[\[{(]")


def _is_benign_literal(value: str, *, quoted: bool) -> bool:
    """Is ``value`` structurally incapable of being a hard-coded secret?

    A QUOTED value is never spared here (only the placeholder list spares
    one) -- a quoted string containing '.', '(' or '[' is still data, not
    code (this is the JWT-in-an-assignment case: don't let a value's dots
    be mistaken for a code reference just because it's unquoted-adjacent
    logic elsewhere spares dotted names).

    An UNQUOTED value can only be a secret if the source is malformed
    (Python requires quotes for a string literal), so a numeric literal, a
    recognised sentinel, a template/env interpolation, a dotted reference,
    or a call/subscript/collection-literal OPENING is spared on STRUCTURAL
    grounds -- never on casing. This is the deliberate inversion from the
    #277 round-2 CapitalCase heuristic, which spared values by shape-of-text
    rather than by what the shape structurally rules out, and so spared
    base64-ish secrets as often as real type names. See
    ``_DOTTED_REFERENCE_RE``'s docstring above for why the structural check
    itself still needs a narrow anchor (lowercase-first segment / bracket at
    the front) rather than "contains this character anywhere" -- the wider
    form was measured, against this repository's own source, to spare a
    secret-plus-file-extension string like `SECRET_VALUE.py`.
    """
    if not value:
        return True
    if quoted:
        return False
    if value.lower() in _BENIGN_UNQUOTED_LITERALS:
        return True
    if _NUMERIC_RE.match(value):
        return True
    if _TEMPLATE_RE.match(value):
        return True
    if _DOTTED_REFERENCE_RE.match(value):
        return True
    if _CALL_OR_SUBSCRIPT_RE.match(value):
        return True
    if _COLLECTION_LITERAL_RE.match(value):
        return True
    return False


def _scan_and_redact_key_values(text: str, state: RedactionState) -> tuple[str, bool]:
    """Single linear pass over ``text``: find ``<sensitive-key><sep><value>``
    occurrences (``=`` or ``:``) and redact the value only, preserving key,
    separator spacing, quote style and string prefix.

    Runs in O(len(text)). See the module docstring and
    ``test_redaction_200f_red_corpus.py::test_redos_witness_stays_linear``
    for the complexity argument and its empirical proof.
    """
    n = len(text)
    lowered = text.lower()
    out: list[str] = []
    i = 0
    unbounded = False
    while i < n:
        key = _match_key_at(text, lowered, i)
        if key is None:
            out.append(text[i])
            i += 1
            continue
        key_end = i + len(key)
        j = key_end
        while j < n and text[j] in " \t":
            j += 1
        if j < n and text[j] in "\"'":
            j += 1
            while j < n and text[j] in " \t":
                j += 1
        if j >= n or text[j] not in "=:":
            out.append(text[i:key_end])
            i = key_end
            continue
        sep_char = text[j]
        sep_end = j + 1
        if sep_char == "=" and sep_end < n and text[sep_end] == "=":
            # `==` comparison, not assignment -- leave untouched.
            out.append(text[i:key_end])
            i = key_end
            continue
        v_start = sep_end
        while v_start < n and text[v_start] in " \t":
            v_start += 1
        value_text, value_end, bounded = _scan_value_span(text, v_start)
        if not bounded and v_start < n and text[v_start] in "\"'" and text[v_start : v_start + 3] != text[v_start] * 3:
            # A single/double (non-triple) quote whose close was never found
            # on this line: per `_scan_value_span`'s docstring, treat as "no
            # real value here" (very likely a misdetected enclosing-string
            # delimiter, e.g. `assert b"token=" not in ...` where the `"`
            # right after `=` is the ENCLOSING b-string's own closing
            # delimiter) rather than guess-redacting to end of line. Spare
            # this occurrence entirely and resume scanning right after the
            # key -- the quote character and everything after it is left
            # completely untouched, exactly as if this key had no value.
            out.append(text[i:key_end])
            i = key_end
            continue
        if not bounded:
            unbounded = True
        prefix, quote, inner = _unwrap_value(value_text)
        stripped_inner = inner.strip()
        # `name: Type = default` -- a Python variable annotation with a
        # default value, not a YAML/env `key: value` data pair. Scoped
        # deliberately narrow: only the colon separator (never `=`, where a
        # bare value is far more likely to be an actual shell/env-style
        # leak -- see `Zm9vYmFyYmF6`/`Hunter2Value` in the RED corpus), only
        # an unquoted value (a quoted string can't be a type expression),
        # and only when a bare `=` (not `==`) follows shortly after -- a
        # data value is not usually itself followed by another assignment
        # on the same logical line. This does NOT cover a bare annotation
        # with no default (`secret_key: SecretKeyConfig` alone); that
        # remains a named, deliberate limitation rather than a casing
        # heuristic, because casing alone was measured (in the random-secret
        # corpus) to reopen the colon-form leak class.
        looks_like_annotation_with_default = False
        if sep_char == ":" and not quote and value_text:
            lookahead = value_end
            while lookahead < n and text[lookahead] in " \t":
                lookahead += 1
            if (
                lookahead < n
                and text[lookahead] == "="
                and not (lookahead + 1 < n and text[lookahead + 1] == "=")
            ):
                looks_like_annotation_with_default = True
        # A bare (unquoted) value that is EXACTLY the key's own name
        # (`token=token`, `self.token = token`, `GitHubClient(token=token,
        # ...)`) is a variable being passed under a same-named parameter --
        # ordinary Python, not a hard-coded secret. Real-source oracle
        # evidence (see checkpoint): this shape is common in this
        # repository's own `scripts/*.py`.
        #
        # This is EXACT match only, deliberately -- an earlier version of
        # this rule also spared a bare value merely CONTAINING the key's
        # name as a substring (`api_key=router_api_key`), and the oracle
        # found that reopens a real leak: fixture/constant secrets are
        # routinely NAMED with the sensitive word inside them
        # (`AGENTESCALA_FIXTURE_TOKEN_SECRET` contains "TOKEN" as a whole
        # word and is not a reference to anything) -- so it leaked in
        # `tests/agent_review/test_aiops_review_intake_cli.py`'s own
        # fixture. `api_key=router_api_key` is accepted as a known,
        # narrower remaining limitation (over-redaction, not a leak) rather
        # than risk that class again.
        looks_like_same_name_reference = (
            not quote
            and bool(stripped_inner)
            and stripped_inner.lower().replace("_", "") == key.lower().replace("_", "")
        )
        if (
            not value_text
            or _is_placeholder(stripped_inner)
            or _is_benign_literal(stripped_inner, quoted=bool(quote))
            or looks_like_annotation_with_default
            or looks_like_same_name_reference
        ):
            out.append(text[i:value_end])
        else:
            state.record(f"{key.lower()}_assignment")
            state.record_witness(stripped_inner)
            if quote and value_text.endswith(quote):
                redacted_value = f"{prefix}{quote}{REDACTED}{quote}"
            else:
                redacted_value = REDACTED
            out.append(f"{text[i:key_end]}{text[key_end:v_start]}{redacted_value}")
        i = value_end
    return "".join(out), unbounded


def _sub_cookie_headers(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(2)
        if _is_placeholder(value):
            return match.group(0)
        state.record("cookie")
        state.record_witness(value)
        return f"{match.group(1)}: {REDACTED}"

    return _COOKIE_RE.sub(replace, text)


def _sub_simple_tokens(text: str, state: RedactionState) -> str:
    def replace_github(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_placeholder(token):
            return token
        state.record("github_token")
        state.record_witness(token)
        return REDACTED

    def replace_openai(match: re.Match[str]) -> str:
        token = match.group(0)
        if _is_placeholder(token):
            return token
        state.record("openai_token")
        state.record_witness(token)
        return REDACTED

    return _OPENAI_TOKEN_RE.sub(replace_openai, _GITHUB_TOKEN_RE.sub(replace_github, text))


def _sub_database_urls(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        state.record("database_url_credentials")
        state.record_witness(match.group(3))
        state.record_witness(match.group(4))
        return f"{match.group(1)}{match.group(2)}{REDACTED}:{REDACTED}@"

    return _DATABASE_URL_RE.sub(replace, text)


def _sub_credential_urls(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        username = match.group(2)
        password = match.group(3)
        if REDACTED in {username, password} or _is_placeholder(username) or _is_placeholder(password):
            return match.group(0)
        state.record("url_credentials")
        state.record_witness(username)
        state.record_witness(password)
        return f"{match.group(1)}{REDACTED}:{REDACTED}@"

    return _CREDENTIAL_URL_RE.sub(replace, text)


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower().strip("\"'")
    if normalized in PLACEHOLDER_VALUES:
        return True
    if normalized.startswith("example") or normalized.endswith("-example"):
        return True
    return False


def _redact_local_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_local_paths(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_local_paths(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_local_paths(item) for item in value]
    if not isinstance(value, str):
        return value
    if _is_absolute_path(value):
        return "[LOCAL_PATH_REDACTED]"
    redacted = _WINDOWS_ABSOLUTE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", value)
    redacted = _UNIX_ABSOLUTE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", redacted)
    return _HOME_RELATIVE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", redacted)


def _is_absolute_path(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("/") or stripped.startswith("~/") or bool(re.match(r"^[A-Za-z]:\\", stripped))
