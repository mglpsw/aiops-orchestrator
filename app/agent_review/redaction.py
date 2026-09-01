"""Deterministic redaction for AgentReview intake artifacts."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.agent_review.schemas import RedactionReport


REDACTED = "[REDACTED]"

SENSITIVE_KEYS = {
    "authorization",
    "token",
    "api_key",
    "password",
    "secret",
    "client_secret",
    "access_token",
    "refresh_token",
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
_AUTHORIZATION_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*)bearer\s+([^\s,;]+)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{8,})")
# `#200-F` authority E, round 2. The first attempt fixed the *witness*
# (`password = "..."`) rather than the *class*, and adversarial review found
# six shapes still leaking -- three of which emitted `[REDACTED]` while the
# plaintext survived on the same line, so the artifact asserted a redaction
# that had not happened. An artifact that misreports its own sanitisation is
# worse than one that admits it did nothing.
#
# The mechanism of that failure generalises, and is worth stating: with an
# ordered alternation, when the character after `=` was not a quote (`f`, `b`,
# `(`), the bare-run alternative matched that single prefix character,
# replaced it, and let the quoted literal through untouched.
#
# Two inversions follow.
#
# 1. Keys are matched by PATTERN, not enumerated. `secret_key` and `apikey`
#    leaked purely because nobody had listed them. A component-wise pattern
#    cannot fall behind the same way -- the same reasoning as the operational
#    refusal family.
# 2. The value is SUSPECT to end of line and is spared only when demonstrably
#    benign. Enumerating secret shapes loses to the next shape; enumerating
#    benign shapes fails safe.
_SENSITIVE_KEY_V2 = (
    r"[A-Za-z0-9_.\-]*"
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|credential"
    r"|private[_-]?key|access[_-]?key)"
    r"[A-Za-z0-9_.\-]*"
)

# An optional Python string prefix (f, b, r, u and their pairs) followed by a
# triple- or single-quoted body, else a bare run. Triple quotes are tried
# FIRST: a two-quote alternative placed earlier matches the empty string at
# the start of `"""` and consumes two of the three, which is exactly how the
# triple-quoted case leaked while reporting success.
_STRING_PREFIX_V2 = r"(?:[fFbBrRuU]{1,2})?"
_TRIPLE_DOUBLE_V2 = _STRING_PREFIX_V2 + r'"""[^\n]*?"""'
_TRIPLE_SINGLE_V2 = _STRING_PREFIX_V2 + r"'''[^\n]*?'''"
_QUOTED_DOUBLE_V2 = _STRING_PREFIX_V2 + r'"(?:\\.|[^"\\\n])*"'
_QUOTED_SINGLE_V2 = _STRING_PREFIX_V2 + r"'(?:\\.|[^'\\\n])*'"
_UNTERMINATED_DOUBLE_V2 = _STRING_PREFIX_V2 + r'"[^\n]*'
_UNTERMINATED_SINGLE_V2 = _STRING_PREFIX_V2 + r"'[^\n]*"
_QUOTED_VALUE_V2 = "|".join(
    (
        _TRIPLE_DOUBLE_V2,
        _TRIPLE_SINGLE_V2,
        _QUOTED_DOUBLE_V2,
        _QUOTED_SINGLE_V2,
        _UNTERMINATED_DOUBLE_V2,
        _UNTERMINATED_SINGLE_V2,
    )
)

_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(" + _SENSITIVE_KEY_V2 + r")([\"']?\s*=\s*)(" + _QUOTED_VALUE_V2 + r"|[^\s&;,]+)"
)

# The colon form now accepts UNQUOTED values. Requiring quotes was justified
# in a comment claiming it "covers YAML and JSON, where the leak actually
# occurs" -- which was false: `password: hunter2` is the dominant YAML form
# and was never matched. Python type annotations are spared by recognising the
# small, closed set of shapes a type expression can take, rather than by
# demanding quotes of everybody.
_COLON_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(" + _SENSITIVE_KEY_V2 + r")([\"']?\s*:\s*)(" + _QUOTED_VALUE_V2 + r"|[^\s&;,]+)"
)

# Values that are not secrets and whose removal would damage the review:
# `token_count = 5`, `MAX_TOKENS = 100`, `password: str`, `secret: None`.
_BENIGN_VALUE_RE_V2 = re.compile(
    r"""(?xi)
    ^(?:
        [-+]?\d[\d_]*(?:\.\d+)?(?:[eE][-+]?\d+)?
      | true|false|none|null|nil|undefined
      | (?:typing\.|t\.)?
        (?:str|bytes|int|float|bool|text|any|object|dict|list|set|tuple
          |optional|union|secretstr|securestring|sequence|mapping|iterable)
        (?:\s*\[[^\n]*\])?
        (?:\s*\|\s*[A-Za-z_][A-Za-z0-9_.\[\]]*)*
      | \{\{[^\n]*\}\}
      | \$\{[^\n]*\}
      | \$[A-Za-z_][A-Za-z0-9_]*
    )$
    """
)

_COOKIE_RE = re.compile(r"(?i)\b(set-cookie|cookie)\s*:\s*([^\r\n]+)")
_GITHUB_TOKEN_RE = re.compile(r"\b(ghp_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,})\b")
_OPENAI_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_DATABASE_URL_RE = re.compile(
    r"(?i)\b(DATABASE_URL\s*=\s*)([a-z][a-z0-9+.-]*://)([^:\s/@]+):([^@\s]+)@"
)
_CREDENTIAL_URL_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^:/@\s]+):([^/@\s]+)@")
_UNIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.~-])/(?:[A-Za-z0-9._@+=:-]+/)+[A-Za-z0-9._@+=:-]+")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+")
_HOME_RELATIVE_PATH_RE = re.compile(r"(?<![\w.~/-])~/(?:[A-Za-z0-9._@+=:-]+/)*[A-Za-z0-9._@+=:-]+")


class RedactionState:
    def __init__(self) -> None:
        self.files_processed = 0
        self.replacements_by_type: Counter[str] = Counter()
        self.limitations: list[str] = []

    @property
    def secret_like_values_found(self) -> int:
        return sum(self.replacements_by_type.values())

    def record_file(self) -> None:
        self.files_processed += 1

    def record(self, replacement_type: str) -> None:
        self.replacements_by_type[replacement_type] += 1

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
    redacted = _sub_authorization_bearer(redacted, state)
    redacted = _sub_bearer(redacted, state)
    redacted = _sub_assignments(redacted, state)
    redacted = _sub_cookie_headers(redacted, state)
    redacted = _sub_simple_tokens(redacted, state)
    redacted = _sub_database_urls(redacted, state)
    redacted = _sub_credential_urls(redacted, state)
    return redacted


def _redact_sensitive_field(key: Any, value: Any, state: RedactionState) -> Any:
    if _normalize_key(key) in SENSITIVE_KEYS and isinstance(value, str) and not _is_placeholder(value):
        state.record("sensitive_json_field")
        return REDACTED
    return redact_value(value, state)


def _sub_private_keys(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        state.record("private_key")
        return REDACTED

    return _PRIVATE_KEY_RE.sub(replace, text)


def _sub_authorization_bearer(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(2)
        if _is_placeholder(token):
            return match.group(0)
        state.record("authorization_bearer")
        return f"{match.group(1)}Bearer {REDACTED}"

    return _AUTHORIZATION_BEARER_RE.sub(replace, text)


def _sub_bearer(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_placeholder(token) or token == REDACTED:
            return match.group(0)
        state.record("bearer_token")
        return f"Bearer {REDACTED}"

    return _BEARER_RE.sub(replace, text)


def _unwrap_assignment_value_v2(value: str) -> tuple[str, str, str]:
    """Split a matched value into (prefix, quote, inner text).

    ``prefix`` is a Python string prefix (``f``, ``b``, ``rb`` ...) when
    present. It is returned rather than discarded because leaving it attached
    to the redacted output is what made the f-string case emit
    ``password = [REDACTED]"the-real-secret"``.

    Returns ``("", "", value)`` for a bare value. An unterminated quote yields
    its opening character and the remainder, so the benign/placeholder checks
    still see the real text rather than a stray quote.
    """
    index = 0
    while index < len(value) and value[index] in "fFbBrRuU" and index < 2:
        index += 1
    prefix, remainder = value[:index], value[index:]
    if not remainder or remainder[0] not in "\"'":
        # A leading run of f/b/r/u letters that is not a string prefix is just
        # part of a bare value (`token = fallback`), so nothing is stripped.
        return "", "", value
    quote = remainder[0]
    triple = quote * 3
    if remainder.startswith(triple) and remainder.endswith(triple) and len(remainder) >= 6:
        return prefix, triple, remainder[3:-3]
    if len(remainder) >= 2 and remainder[-1] == quote:
        return prefix, quote, remainder[1:-1]
    return prefix, quote, remainder[1:]


def _sub_assignments(text: str, state: RedactionState) -> str:
    """Redact the value of a sensitive assignment, keeping the code readable.

    Only the value is removed. The key, the separator with its original
    spacing, the string prefix and the quoting style all survive, so a
    reviewer can still see that a credential is assigned, to which name, and
    in what syntax.

    Known and pre-existing exception, recorded rather than claimed away: a
    comparison such as ``if token == expected_token:`` has its second ``=``
    consumed as the value and becomes ``token =[REDACTED] expected_token``.
    That predates this slice (base ``f70af2e6`` does the same) and is not
    introduced here, but "the line stays readable" is not universally true
    and should not be written as if it were.

    A value is spared only when it is a placeholder or matches
    ``_BENIGN_VALUE_RE_V2`` -- a numeric literal, a sentinel, a type
    expression, or a template/environment interpolation. Everything else after
    a sensitive key is treated as a secret. That direction is deliberate: over
    -redacting `token_count = "many"` costs a reviewer one line of context,
    while under-redacting costs a credential.
    """

    def replace(match: re.Match[str]) -> str:
        key, separator, value = match.group(1), match.group(2), match.group(3)
        prefix, quote, inner = _unwrap_assignment_value_v2(value)
        if _is_placeholder(inner) or _BENIGN_VALUE_RE_V2.match(inner.strip()):
            return match.group(0)
        state.record(f"{key.lower()}_assignment")
        # A quote is reinstated only when the original was balanced; echoing an
        # unterminated quote back would emit syntax the source never had.
        if quote and value.endswith(quote):
            redacted_value = f"{prefix}{quote}{REDACTED}{quote}"
        else:
            redacted_value = REDACTED
        return f"{key}{separator}{redacted_value}"

    redacted = _ASSIGNMENT_RE.sub(replace, text)
    return _COLON_ASSIGNMENT_RE.sub(replace, redacted)


def _sub_cookie_headers(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(2)
        if _is_placeholder(value):
            return match.group(0)
        state.record("cookie")
        return f"{match.group(1)}: {REDACTED}"

    return _COOKIE_RE.sub(replace, text)


def _sub_simple_tokens(text: str, state: RedactionState) -> str:
    def replace_github(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_placeholder(token):
            return token
        state.record("github_token")
        return REDACTED

    def replace_openai(match: re.Match[str]) -> str:
        token = match.group(0)
        if _is_placeholder(token):
            return token
        state.record("openai_token")
        return REDACTED

    return _OPENAI_TOKEN_RE.sub(replace_openai, _GITHUB_TOKEN_RE.sub(replace_github, text))


def _sub_database_urls(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        state.record("database_url_credentials")
        return f"{match.group(1)}{match.group(2)}{REDACTED}:{REDACTED}@"

    return _DATABASE_URL_RE.sub(replace, text)


def _sub_credential_urls(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        username = match.group(2)
        password = match.group(3)
        if REDACTED in {username, password} or _is_placeholder(username) or _is_placeholder(password):
            return match.group(0)
        state.record("url_credentials")
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
