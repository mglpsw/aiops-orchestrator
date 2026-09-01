"""#200-G2B executable spike: an independent negative oracle for outbound bytes.

This module is deliberately under ``evals/`` rather than ``app/``. It is not
production authority and does not wire AgentReview's Router transport. Its one
job is to test the architectural proposition that was missing from G2:

    forward detector misses a secret
        -> exact final pre-HTTP bytes still look unsafe to an independent oracle
        -> outbound call is refused before the delegate can run

Independence is structural: this module imports neither ``redaction.py`` nor
``review_content_extraction_v2.py`` and consumes no witness set produced by
those modules. The input is the exact ``bytes`` object that would otherwise be
handed to ``urllib.request.Request(..., data=body)`` / the HTTP opener.

The oracle is conservative. It does not transform source and therefore cannot
claim that a secret was safely redacted; it only returns OUTBOUND_SAFE or
OUTBOUND_NOT_PROVEN_SAFE. False positives are tested separately against real
repository source before this spike can graduate into a production design.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Literal, TypeVar

OutboundVerdictV2 = Literal["OUTBOUND_SAFE", "OUTBOUND_NOT_PROVEN_SAFE"]


@dataclass(frozen=True)
class OutboundSafetyFindingV2:
    detector: str
    location: str
    evidence_class: str


@dataclass(frozen=True)
class OutboundSafetyReportV2:
    verdict: OutboundVerdictV2
    findings: tuple[OutboundSafetyFindingV2, ...]

    @property
    def safe(self) -> bool:
        return self.verdict == "OUTBOUND_SAFE"


class OutboundSafetyBlockedV2(RuntimeError):
    """Spike-only refusal. Carries classification, never matched secret text."""

    def __init__(self, report: OutboundSafetyReportV2) -> None:
        super().__init__("outbound_not_proven_safe")
        self.report = report


_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}(?![A-Za-z0-9_-])"
)
_AWS_ACCESS_KEY_RE = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
_SLACK_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9])")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
)

# --- Round-2 correction --------------------------------------------------
#
# Round 1 (this file's original shape) committed a closed-world fallacy:
# ``_LINE_ASSIGNMENT_RE`` only matched a bare identifier anchored to the
# START of a line, so it never saw kwarg-call assignments
# (``connect(password="x")``) or quoted-key dict-literal lines
# (``"password": "x"``). Because "no regex matched" silently fell through to
# OUTBOUND_SAFE, those unrecognised shapes were treated as proof of safety
# instead of absence of evidence.
#
# ``_ASSIGNMENT_RE`` below is deliberately *not* anchored to line start and
# recognises both a bare/dotted identifier key and a quoted dict-literal
# key, so the same sensitive-key branch below now actually sees these
# shapes instead of silently missing them.

# Every alternative below is length-bounded. Without a cap, the quoted-string
# alternative can match an entire JSON-escaped source blob as a single
# "value" — the Router body wraps reviewed source as a JSON string, so a
# quoted dict-style key like ``"content"`` sitting in front of that whole
# blob would otherwise capture thousands of characters as one candidate
# value and feed the *entire file* through the entropy/opaque check. That is
# exactly the "generic entropy over an arbitrary JSON string" anti-pattern
# ``_opaque_value_is_suspicious`` already documents as rejected — it just
# used to be prevented structurally by the old line-start anchor (which
# never matched a quoted key at all), and reintroducing quoted-key support
# reopened the door unless a bound is applied. 512 characters comfortably
# covers realistic credential shapes (JWTs included) while refusing to treat
# a multi-KB blob as a single atomic value; nested content inside that blob
# is still reachable, either through this same regex re-matching a smaller
# inner span, or through the whole-text JSON-parse recursion in
# ``_scan_text``.
_MAX_VALUE_LEN = 512
_VALUE_ALT = (
    rf'"(?:[^"\\\n]|\\.){{0,{_MAX_VALUE_LEN}}}"'  # double-quoted value
    rf"|'(?:[^'\\\n]|\\.){{0,{_MAX_VALUE_LEN}}}'"  # single-quoted value
    # One-level object/array literal. The trailing `[^\s,)\]}\n]*` matters:
    # without it, ``[REDACTED]Hunter2`` would match only the short balanced
    # ``[REDACTED]`` span and silently orphan ``Hunter2`` as unconsumed,
    # unscanned text after the match — the exact kind of "value the grammar
    # doesn't quite cover, so it goes unseen" gap this round is fixing.
    # Requiring the container span to swallow any immediately-adjacent
    # non-delimiter text means a real container is still captured whole,
    # while a bracket-prefixed placeholder-with-trailing-secret is captured
    # whole too and therefore fails `_is_container_literal`'s strict
    # first/last-character check, falling through to be judged as an
    # ordinary opaque value instead of being silently split.
    rf"|\{{[^{{}}\n]{{0,{_MAX_VALUE_LEN * 4}}}\}}[^\s,)\]}}\n]{{0,{_MAX_VALUE_LEN}}}"
    rf"|\[[^\[\]\n]{{0,{_MAX_VALUE_LEN * 4}}}\][^\s,)\]}}\n]{{0,{_MAX_VALUE_LEN}}}"
    # Bare token up to a delimiter — but never starting with a JSON-escaped
    # whitespace pair (``\n``/``\r``/``\t`` as the two literal characters
    # backslash+letter, not an actual newline byte). Reviewed source travels
    # JSON-escaped, so a genuine end-of-statement colon with nothing real
    # after it (``if not api_key:`` followed by a real line break) leaves
    # exactly that two-character escape sequence sitting right after the
    # colon. Without this guard, ``api_key`` reads as a "key" and the escape
    # sequence reads as its "value" — a Python control-flow colon, not an
    # assignment, misparsed as one because nothing distinguishes "nothing
    # meaningful follows" from "a value follows" once you stop requiring the
    # key to be the first token on its line.
    rf"|(?!\\[nrt])[^\s,)\]}}\n]{{1,{_MAX_VALUE_LEN}}}"
)
_ASSIGNMENT_RE = re.compile(
    r'(?:"(?P<qkey1>[^"\n]{1,80})"|\'(?P<qkey2>[^\'\n]{1,80})\')\s*:\s*(?P<qval>'
    + _VALUE_ALT
    + r")"
    r"|(?P<bkey>[A-Za-z_][A-Za-z0-9_.]{0,80})[ \t]*(?P<bsep>[:=])(?!=)[ \t]*(?P<bval>"
    + _VALUE_ALT
    + r")"
)
_OPAQUE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9_+/=-]{24,160}(?![A-Za-z0-9_])")
_MAX_ASSIGNMENT_RECURSION_DEPTH = 6

_SAFE_VALUE_WORDS = {
    "", "none", "null", "false", "true", "example", "placeholder", "redacted",
    "sample", "dummy", "changeme", "secret", "token", "value", "test", "str",
    "string", "int", "integer", "bool", "boolean", "float", "bytes", "path",
    "optional[str]", "optional[int]", "secretstr", "safetext",
}
# Exact, bounded set of bracket/brace-wrapped placeholder *shapes*. This is
# intentionally a closed enumeration checked with fullmatch (never a prefix
# check) — round 1's ``value.startswith("[redacted")`` accepted
# ``[REDACTED]Hunter2`` because a prefix match proves nothing about what
# follows the prefix.
_SAFE_BRACKET_PLACEHOLDER_RE = re.compile(
    r"^[<\[](redacted|placeholder|hidden|masked|omitted|example|your[a-z0-9_-]*here)[>\]]$",
    re.IGNORECASE,
)
# Bounded environment-variable-reference grammar. Round 1's
# ``value.startswith("$")`` accepted ``$ecret123`` because any string
# beginning with "$" passed, regardless of whether the rest of the value
# was actually a well-formed reference. An env var name is conventionally
# all-uppercase; this grammar requires the *entire* value to be exactly
# ``$NAME`` or ``${NAME}`` with nothing else attached.
_ENV_VAR_REF_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$|^\$[A-Z_][A-Z0-9_]*$")
_SAFE_TOKEN_KEYS = {
    "prompttokens", "completiontokens", "inputtokens", "outputtokens", "totaltokens",
    "maxtokens", "tokenusage", "tokencount",
}
_SENSITIVE_EXACT = {
    "password", "passwd", "pwd", "apikey", "secret", "clientsecret", "secretkey",
    "signingkey", "masterkey", "privatekey", "accesskey", "token", "authtoken",
    "accesstoken", "refreshtoken", "bearertoken", "csrftoken", "idtoken", "pin",
    "pincode", "passcode", "otp", "accesscode", "verificationcode",
}
# Key-name context in which a 40/64-hex-char value is a provable non-secret
# identifier (a git SHA, a content digest, ...) rather than an opaque
# credential. Round 1 exempted *any* 40/64-hex value globally regardless of
# key context ("probably a SHA"), which is exactly the closed-world guess
# that let ``API_TOKEN=<64-hex secret>`` through — a hex-encoded token is
# indistinguishable in shape from a hex-encoded digest; only the key name
# can tell them apart, and even then only affirmatively, never by default.
_HEX_IDENTIFIER_KEY_SUFFIXES = (
    "sha", "sha1", "sha256", "hash", "digest", "commit", "revision",
    "checksum", "etag", "fingerprint",
)


def _normalise_key(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _sensitive_key(value: str) -> bool:
    """Round 1 missed ``API_TOKEN`` (normalised ``apitoken``): the suffix
    list enumerated specific compound suffixes (``csrftoken``, ``idtoken``,
    ``authtoken``, ...) but never ``apitoken``, so it was silently treated
    as non-sensitive.

    The first fix attempt for this widened the suffix to bare ``token``,
    but real-source calibration (task step 6, scanning ``app/``/``tests/``)
    showed that is too coarse: this repository's own diff-parsing code has
    legitimate, non-secret identifiers like ``old_path_token`` and
    ``command_token`` (a lexer token, not a credential), which a bare
    ``token`` suffix flags just as eagerly as ``API_TOKEN``. The suffix
    list instead stays enumerated (specific compounds only), with
    ``apitoken`` added as the one addition this round actually needed.
    """

    key = _normalise_key(value)
    if key in _SAFE_TOKEN_KEYS:
        return False
    if key in _SENSITIVE_EXACT:
        return True
    if key.endswith((
        "password", "passwd", "apikey", "clientsecret", "secretkey",
        "signingkey", "masterkey", "privatekey", "accesskey", "csrftoken",
        "idtoken", "authtoken", "accesstoken", "refreshtoken", "bearertoken",
        "apitoken",
    )):
        return True
    if key.endswith("secret") and len(key) > len("secret"):
        return True
    return False


def _strip_assignment_value(value: str) -> str:
    result = value.strip().rstrip(",;")
    if len(result) >= 2 and result[0] == result[-1] and result[0] in {'\"', "'"}:
        result = result[1:-1]
    return result.strip()


def _safe_placeholder(value: str) -> bool:
    """Only explicit, *fully matched* placeholders are safe under a
    proven-sensitive key.

    G2B's CI caught a dangerous cross-context exemption: a CapitalCase-like
    value such as ``Ab9Cd...`` was treated as a type name even after the key
    had already established that the line was a password. Type-shape reasoning
    is valid only in non-sensitive contexts; under a sensitive key, anything
    not explicitly a placeholder/environment reference remains suspect.

    Round 1 implemented "explicit placeholder" as a *prefix* check
    (``value.startswith("$")`` / ``startswith("[redacted")``). A prefix
    check proves nothing about what follows the prefix, so
    ``$ecret123``/``[REDACTED]Hunter2`` both satisfied it while carrying an
    exact secret. Every check below is either an exact set-membership test
    or a ``fullmatch`` against a bounded grammar — the entire value must be
    the placeholder, not merely start like one.
    """

    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in _SAFE_VALUE_WORDS:
        return True
    if _ENV_VAR_REF_RE.fullmatch(stripped):
        return True
    if _SAFE_BRACKET_PLACEHOLDER_RE.fullmatch(stripped):
        return True
    return False


def _placeholder_or_type(value: str) -> bool:
    if _safe_placeholder(value):
        return True
    lowered = value.strip().lower()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\]| ]*", value) and (
        value[:1].isupper() or lowered in _SAFE_VALUE_WORDS
    ):
        return True
    return False


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _looks_like_sha_or_identifier(value: str, *, key: str | None = None) -> bool:
    """A hex-looking value is a provable non-secret identifier only in an
    affirmative key context (``head_sha``, ``content_digest``, ...).

    Round 1 exempted *any* 40/64-hex-char value from entropy suspicion
    unconditionally — "probably a SHA". That is a closed-world guess about
    shape, not proof: a hex-encoded API token is byte-for-byte
    indistinguishable from a hex-encoded digest. This version only accepts
    the exemption when the key itself affirmatively names an
    identifier/digest role; without a key, or under an unrelated key, a
    hex-looking value gets no exemption and falls through to the ordinary
    entropy check like any other opaque token.
    """

    if value.startswith(("agent-review.", "review:", "chunk-", "run-")):
        return True
    if key is not None and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        normalised_key = _normalise_key(key)
        if normalised_key.endswith(_HEX_IDENTIFIER_KEY_SUFFIXES):
            return True
    return False


def _looks_token_like_for_entropy(value: str) -> bool:
    if any(character in value for character in "+/="):
        return True
    return (
        any(character.islower() for character in value)
        and any(character.isupper() for character in value)
        and sum(character.isdigit() for character in value) >= 3
    )


def _opaque_value_is_suspicious(value: str, *, key: str | None = None) -> bool:
    """Entropy is meaningful only for an explicitly parsed value context.

    Structural JWT/AWS/Slack/PEM detectors remain global because their shape
    itself is evidence. Generic entropy is intentionally *not* applied to
    arbitrary JSON string values: reviewed source is itself transported as a
    JSON string, so doing so merely renames a global source scan and recreates
    the false-positive class this spike is designed to expose.
    """

    for match in _OPAQUE_TOKEN_RE.finditer(value):
        token = match.group(0)
        if _looks_like_sha_or_identifier(token, key=key) or not _looks_token_like_for_entropy(token):
            continue
        if _entropy(token) >= 4.25:
            return True
    return False


def _is_container_literal(value: str) -> bool:
    stripped = value.strip()
    return (
        len(stripped) >= 2
        and (
            (stripped[0] == "{" and stripped[-1] == "}")
            or (stripped[0] == "[" and stripped[-1] == "]")
        )
    )


def _scan_assignments(
    text: str, *, location: str, findings: list[OutboundSafetyFindingV2], depth: int = 0
) -> None:
    """Find every assignment-shaped construct in *text* and require a
    positive safety proof for each one instead of defaulting to safe.

    This is the load-bearing polarity inversion for the round-2 fix. Round 1
    only recognised a bare identifier anchored to the start of a line
    (``_LINE_ASSIGNMENT_RE``); a kwarg call (``connect(password="x")``) or a
    quoted dict-literal key (``"password": "x"``) never matched anything at
    all, and "nothing matched" silently became OUTBOUND_SAFE. ``_ASSIGNMENT_RE``
    is unanchored and recognises both shapes, so the *same* sensitive-key
    logic below — which was already allowlist-only, never match-list-only —
    now actually gets a chance to run against them. A dict/array-literal
    value under a non-sensitive key is recursed into (bounded depth) rather
    than being judged as a single opaque blob, so a nested sensitive key
    (``config = {"password": "Hunter2"}``) is still found.
    """

    if depth > _MAX_ASSIGNMENT_RECURSION_DEPTH:
        return

    for match in _ASSIGNMENT_RE.finditer(text):
        key = match.group("qkey1") or match.group("qkey2") or match.group("bkey")
        raw_value = match.group("qval") if match.group("qval") is not None else match.group("bval")
        value = _strip_assignment_value(raw_value)

        if _sensitive_key(key):
            if not _safe_placeholder(value):
                findings.append(
                    OutboundSafetyFindingV2("sensitive_assignment", location, "sensitive_key_value")
                )
            continue

        if _is_container_literal(raw_value):
            # A container value is not itself a single opaque token; its
            # safety is fully determined by recursing into its contents. An
            # empty recursive result means "no assignment-shaped construct
            # found inside" — the same provably-out-of-domain condition that
            # makes construct-free prose safe.
            _scan_assignments(
                raw_value[1:-1], location=f"{location}::<nested>", findings=findings, depth=depth + 1
            )
            continue

        if not _placeholder_or_type(value) and _opaque_value_is_suspicious(value, key=key):
            findings.append(
                OutboundSafetyFindingV2("high_entropy_assignment", location, "opaque_candidate")
            )


def _scan_text(text: str, *, location: str, findings: list[OutboundSafetyFindingV2]) -> None:
    for detector, regex in (
        ("jwt", _JWT_RE),
        ("aws_access_key", _AWS_ACCESS_KEY_RE),
        ("slack_token", _SLACK_TOKEN_RE),
        ("private_key", _PRIVATE_KEY_RE),
    ):
        if regex.search(text):
            findings.append(OutboundSafetyFindingV2(detector, location, "structural_credential"))

    _scan_assignments(text, location=location, findings=findings)

    # The Router user message contains canonical JSON as a string. Parse and
    # recurse so the oracle reasons about that final material independently of
    # however the forward extractor represented it.
    stripped = text.strip()
    if stripped[:1] in {"{", "["}:
        try:
            nested = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return
        _scan_value(nested, location=f"{location}::<nested-json>", findings=findings)


def _scan_value(value: Any, *, location: str, findings: list[OutboundSafetyFindingV2]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if _sensitive_key(str(key)) and isinstance(child, (str, int, float)):
                candidate = str(child).strip()
                if not _safe_placeholder(candidate):
                    findings.append(
                        OutboundSafetyFindingV2("sensitive_json_key", child_location, "sensitive_key_value")
                    )
            _scan_value(child, location=child_location, findings=findings)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_value(child, location=f"{location}[{index}]", findings=findings)
        return
    if isinstance(value, str):
        _scan_text(value, location=location, findings=findings)


def inspect_outbound_body_v2(body: bytes) -> OutboundSafetyReportV2:
    """Inspect exact pre-HTTP request bytes without consulting forward witnesses."""

    findings: list[OutboundSafetyFindingV2] = []
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        findings.append(OutboundSafetyFindingV2("invalid_utf8", "$", "uninspectable_outbound"))
        return OutboundSafetyReportV2("OUTBOUND_NOT_PROVEN_SAFE", tuple(findings))

    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        findings.append(OutboundSafetyFindingV2("invalid_json", "$", "uninspectable_outbound"))
        return OutboundSafetyReportV2("OUTBOUND_NOT_PROVEN_SAFE", tuple(findings))

    _scan_value(document, location="$", findings=findings)
    verdict: OutboundVerdictV2 = "OUTBOUND_SAFE" if not findings else "OUTBOUND_NOT_PROVEN_SAFE"
    return OutboundSafetyReportV2(verdict, tuple(findings))


T = TypeVar("T")


def guard_exact_outbound_body_v2(body: bytes, delegate: Callable[[], T]) -> T:
    """Spike harness: call *delegate* only when exact outbound bytes are proven safe."""

    report = inspect_outbound_body_v2(body)
    if not report.safe:
        raise OutboundSafetyBlockedV2(report)
    return delegate()
