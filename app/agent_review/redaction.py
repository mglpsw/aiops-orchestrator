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

# #200-G2 round 2: an earlier version of this module matched sensitive keys
# by EXACT identifier equality against an enumerated set (`password`,
# `api_key`, ...). Independent review found that requires an underscore-
# bounded WHOLE word, so `DB_PASSWORD`, `STRIPE_API_KEY`, `JWT_SECRET`,
# `ADMIN_PASSWORD` -- the single most common real-world compound secret-
# naming convention -- never matched at all (kebab-case like `db-password`
# DID match, purely because `-` isn't a word character and so accidentally
# acted as a boundary; that asymmetry was itself the tell). Replaced with
# WORD-based matching: an identifier is split into words on `_` and
# camelCase boundaries, and is a sensitive key if any single word, or any
# adjacent word pair, is in the sets below. This still costs O(1) per
# identifier occurrence (bounded by identifier length) and is applied via
# maximal-munch identifier extraction (`_IDENTIFIER_RE`), not an unanchored
# search -- see `_scan_and_redact_key_values`, which advances its cursor
# past a whole identifier in ONE step whether or not it turns out
# sensitive, so a very long non-matching identifier still costs O(its own
# length) once, not O(remaining text) at every one of its offsets.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_SIMPLE_SENSITIVE_WORDS = frozenset(
    {
        "password", "passwd", "pwd", "secret", "apikey", "authorization",
    }
)
# #200-G2 round 2: `token` was originally in `_SIMPLE_SENSITIVE_WORDS`
# too (matching as a compound COMPONENT, same as `password`/`secret`), but
# independent review's own required negative corpus uses
# `command_token: SafeIdentifier` as the exemplar for "project-defined
# CapitalCase type names must not be damaged" -- and `token` as a bare
# SUFFIX is genuinely overloaded: `command_token`/`expected_token`/
# `parse_token` are ordinary non-credential code, while `session_token`/
# `auth_token`/`access_token`/`refresh_token` clearly are credentials.
# There is no structural (non-casing) signal that tells them apart. So
# `token` is treated as sensitive ONLY when it is the WHOLE identifier on
# its own (`token = ...`, unambiguous) or as part of one of the explicit
# compound forms below -- the same "ambiguous word, enumerate the compound
# forms instead of matching generically" trade-off this module already
# applies to `key`.
_SINGLE_WORD_ONLY_SENSITIVE = frozenset({"token", "credential", "credentials"})
# `credential(s)` moved here from `_SIMPLE_SENSITIVE_WORDS` for the same
# reason as `token`: `allow_credentials` (a FastAPI/Django CORS boolean
# config flag, `app/main.py`) was flagged as a credential-holding key by
# compound matching -- it is a toggle, not a secret. No Lane-A witness
# needed `credentials` to match compound-wise.
# Compound terms where the sensitive meaning only exists as the PAIR
# (`key` alone is the genuinely ambiguous case named in the historical
# commit history -- `dedupe_key`, `namespace_key` are ordinary code; `api`
# immediately followed by `key` is not ambiguous).
_COMPOUND_SENSITIVE_BIGRAMS = frozenset(
    {
        ("api", "key"), ("secret", "key"), ("signing", "key"),
        ("access", "key"), ("encryption", "key"), ("private", "key"),
        ("client", "secret"), ("access", "token"), ("refresh", "token"),
        ("session", "token"), ("auth", "token"),
    }
)


def _split_identifier_words(identifier: str) -> list[str]:
    """Split ``identifier`` into lowercase words on ``_`` and camelCase
    boundaries. ``DB_PASSWORD`` -> ``["db", "password"]``,
    ``stripeApiKey`` -> ``["stripe", "api", "key"]``. O(len(identifier)).
    """
    words: list[str] = []
    for chunk in identifier.split("_"):
        if not chunk:
            continue
        start = 0
        for idx in range(1, len(chunk)):
            if chunk[idx].isupper() and (chunk[idx - 1].islower() or chunk[idx - 1].isdigit()):
                words.append(chunk[start:idx])
                start = idx
        words.append(chunk[start:])
    return [w.lower() for w in words if w]


def _identifier_is_sensitive_key(identifier: str, *, exclude_authorization: bool = True) -> bool:
    """Is ``identifier`` a sensitive key, by word-based (not exact-string)
    matching?

    ``exclude_authorization`` defaults to True for the TEXT key=value
    scanner, where an HTTP header value is often two tokens
    ("Bearer <opaque>"), which the bare-value scanner (stops at
    whitespace) would truncate and mis-redact only the word "Bearer" --
    `_sub_authorization_bearer`/`_sub_bearer` cover that form specifically.
    The JSON/dict-field path (`_redact_sensitive_field`) passes
    ``exclude_authorization=False``: a dict value is never embedded prose
    subject to that truncation risk, so `{"authorization": "Bearer xyz"}`
    should redact the whole value.
    """
    if exclude_authorization and identifier.lower() == "authorization":
        return False
    words = _split_identifier_words(identifier)
    if len(words) == 1 and words[0] in _SINGLE_WORD_ONLY_SENSITIVE:
        return True
    # #200-G2 round 2: the single-word simple-word check is capped at 2
    # words, found necessary by the real-source oracle.
    # `hardcoded_secret_confirmed` (3 words) and `secret_like_values_found`
    # (4 words) both contain "secret" as a component and were flagged as
    # credential-holding keys -- both are COUNTS/FLAGS *describing*
    # secret-shaped material, the same "descriptive metadata name, not a
    # credential" shape as `command_token`. Every real Lane-A witness
    # (`db_password`, `jwt_secret`, `admin_password`) is exactly 2 words;
    # longer compounds are measured, in this repository's own source, to
    # be far more often descriptive/metric identifiers than actual secret
    # holders. The BIGRAM check (below) is intentionally NOT length-capped
    # the same way: `stripe_api_key` (3 words) is a real Lane-A witness,
    # caught via the ("api", "key") bigram regardless of the leading
    # "stripe" -- a bigram is a much more specific signal (two co-occurring
    # exact words) than a single word, so its false-positive rate is
    # structurally lower and a length cap is less needed to control it.
    if len(words) <= 2 and any(word in _SIMPLE_SENSITIVE_WORDS for word in words):
        return True
    return any((a, b) in _COMPOUND_SENSITIVE_BIGRAMS for a, b in zip(words, words[1:]))


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
        # #200-G2 round 2: target-owned DLP "known safe" values (see
        # `safe_review_material.DLPOverrideConfig.additional_safe_
        # substrings`). Consulted at the REDACTION DECISION itself (via
        # `_is_placeholder`), not just at postcondition-verification time --
        # independent review found the previous design excused a value from
        # the GLOBAL witness list once ANY occurrence of it was
        # "known safe", which silently stopped checking for OTHER,
        # never-redacted occurrences of the same literal elsewhere in the
        # material (e.g. a second mention in a comment) -- a real leak the
        # postcondition check exists specifically to catch. Wiring it in
        # here instead means: a value on this list is never treated as
        # suspect in the first place, so it is simply never a witness, and
        # every value that IS a witness is still verified with no
        # exemptions.
        self.extra_safe_values: frozenset[str] = frozenset()

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
    # #200-G2 round 2: word-based, not exact-set membership -- same
    # `DB_PASSWORD`/`STRIPE_API_KEY` blind spot independent review found in
    # the text scanner applied here too, since a JSON/dict key is exactly
    # as likely to be a compound identifier as an assignment's LHS.
    if (
        _identifier_is_sensitive_key(_normalize_key(key), exclude_authorization=False)
        and isinstance(value, str)
        and not _is_placeholder(value, state)
    ):
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
        if not _looks_like_jwt(candidate) or _is_placeholder(candidate, state):
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
            if _is_placeholder(candidate, state):
                return candidate
            state.record(_label)
            state.record_witness(candidate)
            return REDACTED

        redacted = pattern.sub(replace, redacted)
    return redacted


def _sub_authorization_bearer(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(2)
        if _is_placeholder(token, state):
            return match.group(0)
        state.record("authorization_bearer")
        state.record_witness(token)
        return f"{match.group(1)}Bearer {REDACTED}"

    return _AUTHORIZATION_BEARER_RE.sub(replace, text)


def _sub_bearer(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_placeholder(token, state) or token == REDACTED:
            return match.group(0)
        state.record("bearer_token")
        state.record_witness(token)
        return f"Bearer {REDACTED}"

    return _BEARER_RE.sub(replace, text)


def _is_word_char(ch: str) -> bool:
    return ch in _WORD_CHARS


def _identifier_match_at(text: str, i: int) -> re.Match[str] | None:
    """Return the maximal-munch identifier match starting at ``i``, if any
    identifier starts there at all (i.e. ``i`` is a word boundary AND
    ``text[i]`` is itself a word-start character).

    This is the ONLY unanchored-ish regex scan in the key-matching path,
    and it is safe: `[A-Za-z_][A-Za-z0-9_]*` has no internal ambiguity (no
    nested/alternated quantifiers that could match the same span multiple
    ways), so matching it at a single fixed offset via ``re.match`` costs
    O(len(the identifier)), never more. The caller (`_scan_and_redact_key_
    values`) uses the match's end position to skip a whole identifier in
    one step regardless of whether it turns out sensitive -- that is what
    keeps the overall scan O(n): a single very long non-matching
    "identifier" is charged once for its own length, not re-attempted at
    every one of its internal offsets.
    """
    if i > 0 and _is_word_char(text[i - 1]):
        return None
    return _IDENTIFIER_RE.match(text, i)


def _looks_like_new_assignment_after(text: str, sep_index: int) -> bool:
    """Peek past a `,`/`;`/`&` separator at ``sep_index``: does what
    follows look like the START of a new ``identifier = value`` /
    ``identifier: value`` pair, OR a new QUOTED JSON/dict-literal key
    (``"key": value``) -- a real second keyword argument or field -- as
    opposed to more DATA under the same key (a delimited list)? Used only
    to decide whether a bare-value scan should stop at the separator or
    absorb it; see `_scan_value_span`.

    #200-G2 round 2: the bare-identifier-only version of this check found
    a real defect via the real-source oracle -- `{"secret_like_values_
    found": 1, "redacted_lines_present": True, ...}` has QUOTED keys, so
    the lookahead never recognised `"redacted_lines_present"` as a new
    field and absorbed the whole rest of the dict literal into the first
    value. Bounded to a fixed small window (200 chars) for the quoted-key
    case -- a peek, not a search, so this stays O(1) per call regardless
    of file size.

    A second, related defect the oracle found: real multi-line dict/call
    literals put the next field on its OWN LINE after the comma (`1,\n
    "next_field": ...`), and the original whitespace-skip here only
    skipped spaces/tabs, never a newline -- so it never even reached the
    quoted-key or identifier check for the extremely common "one field per
    line" formatting style. The skip below is bounded (200 chars) for the
    same linear-time reason as the quote scan above.
    """
    n = len(text)
    j = sep_index + 1
    skip_start = j
    while j < n and text[j] in " \t\r\n" and (j - skip_start) < 200:
        j += 1
    if j < n and text[j] in "\"'":
        quote = text[j]
        k = j + 1
        found_close = False
        while k < n and text[k] != "\n" and (k - j) < 200:
            if text[k] == "\\" and k + 1 < n:
                k += 2
                continue
            if text[k] == quote:
                k += 1
                found_close = True
                break
            k += 1
        if not found_close:
            return False
        j = k
    else:
        match = _IDENTIFIER_RE.match(text, j)
        if match is None:
            return False
        j = match.end()
    while j < n and text[j] in " \t":
        j += 1
    return j < n and text[j] in "=:"


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
        #
        # #200-G2 round 2: `,`/`;`/`&` stopping UNCONDITIONALLY left a
        # real gap independent review found -- `api_key=abcd1234,efgh5678`
        # (a realistic comma-joined credential pair, e.g. a rotation pair
        # or a scope list) only had the pre-comma half captured, so only
        # half was redacted, and because the tail was never even examined
        # it was never recorded as a witness either -- postcondition
        # verification had nothing to check it against. `)`, `]`, `}` stay
        # unconditional hard stops (real container/call closers, and
        # extending past them risks exactly the syntax damage the
        # exclusion above exists to prevent). `,`/`;`/`&` are different:
        # they are BOTH a plausible argument separator (`foo(a=1, b=2)`,
        # which must not be swallowed) AND a plausible data separator
        # (`KEY=val1,val2`), and the two are only distinguishable by what
        # comes after. So: peek past the separator: if it is followed by
        # `identifier =` or `identifier :` (a new key=value pair, however
        # is spelled), stop here, exactly as before -- but if not, the
        # separator is almost certainly part of the DATA (a delimited list
        # under a single key), so it is consumed and the scan continues.
        j = start
        while j < n and text[j] not in " \t\r\n,;&)]}{\"'\\":
            j += 1
        while j < n and text[j] in ",;&" and not _looks_like_new_assignment_after(text, j):
            j += 1
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
# #200-G2 round 2: lowercase-first was NOT sufficient. Round-1 independent
# review found `token=deadbeef.cafebabe1234` and
# `password=admin.hunter2value` -- two lowercase, dot-joined runs that are
# .env/shell-style secrets, not Python references -- spared outright by the
# rule above with ZERO witness recorded (a silent false negative, worse
# than a leak that at least gets flagged). Lowercase-vs-uppercase distinguishes
# a secret from a TYPE reference, but does not distinguish a secret from a
# VARIABLE/ATTRIBUTE reference, because both are lowercase by convention.
# The additional signal used here is a closed, named enumeration of
# FIRST-SEGMENT names that are actually common as the head of a Python
# reference chain in this kind of code (`self`, `settings`, `config`, a
# request/context/session object, ...) -- the same trade-off shape as the
# `*_key` enumeration elsewhere in this module: a real project-specific
# object name not on this list (`payload.access_token`) will still be
# over-redacted, accepted deliberately because the alternative (guessing
# any lowercase word is a reference) is exactly what reopened the leak.
_KNOWN_REFERENCE_PREFIXES = frozenset(
    {
        "self", "cls", "settings", "config", "cfg", "options", "opts",
        "app", "request", "req", "response", "resp", "ctx", "context",
        "obj", "instance", "client", "session", "env", "environ", "os",
        "sys", "this", "args", "kwargs", "params", "payload", "state",
        "conn", "connection", "db", "store",
    }
)
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
    dotted_match = _DOTTED_REFERENCE_RE.match(value)
    if dotted_match is not None and value.split(".", 1)[0].lower() in _KNOWN_REFERENCE_PREFIXES:
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
    out: list[str] = []
    i = 0
    unbounded = False
    while i < n:
        ident_match = _identifier_match_at(text, i)
        if ident_match is None:
            out.append(text[i])
            i += 1
            continue
        identifier = ident_match.group(0)
        if not _identifier_is_sensitive_key(identifier):
            # Skip the WHOLE identifier in one step (not char by char) --
            # see `_identifier_match_at`'s docstring for why this is what
            # keeps the scan linear.
            out.append(identifier)
            i = ident_match.end()
            continue
        key = identifier
        key_end = ident_match.end()
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
            or _is_placeholder(stripped_inner, state)
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
        if _is_placeholder(value, state):
            return match.group(0)
        state.record("cookie")
        state.record_witness(value)
        return f"{match.group(1)}: {REDACTED}"

    return _COOKIE_RE.sub(replace, text)


def _sub_simple_tokens(text: str, state: RedactionState) -> str:
    def replace_github(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_placeholder(token, state):
            return token
        state.record("github_token")
        state.record_witness(token)
        return REDACTED

    def replace_openai(match: re.Match[str]) -> str:
        token = match.group(0)
        if _is_placeholder(token, state):
            return token
        state.record("openai_token")
        state.record_witness(token)
        return REDACTED

    return _OPENAI_TOKEN_RE.sub(replace_openai, _GITHUB_TOKEN_RE.sub(replace_github, text))


def _sub_database_urls(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        username = match.group(3)
        password = match.group(4)
        # #200-G2 round 2: this sibling was missing the placeholder check
        # `_sub_credential_urls` (just below) has -- found in review.
        if REDACTED in {username, password} or _is_placeholder(username, state) or _is_placeholder(password, state):
            return match.group(0)
        state.record("database_url_credentials")
        state.record_witness(username)
        state.record_witness(password)
        return f"{match.group(1)}{match.group(2)}{REDACTED}:{REDACTED}@"

    return _DATABASE_URL_RE.sub(replace, text)


def _sub_credential_urls(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        username = match.group(2)
        password = match.group(3)
        if REDACTED in {username, password} or _is_placeholder(username, state) or _is_placeholder(password, state):
            return match.group(0)
        state.record("url_credentials")
        state.record_witness(username)
        state.record_witness(password)
        return f"{match.group(1)}{REDACTED}:{REDACTED}@"

    return _CREDENTIAL_URL_RE.sub(replace, text)


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _is_placeholder(value: str, state: RedactionState | None = None) -> bool:
    normalized = value.strip().lower().strip("\"'")
    if normalized in PLACEHOLDER_VALUES:
        return True
    if normalized.startswith("example") or normalized.endswith("-example"):
        return True
    if state is not None and state.extra_safe_values:
        stripped = value.strip().strip("\"'")
        if value in state.extra_safe_values or stripped in state.extra_safe_values:
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
