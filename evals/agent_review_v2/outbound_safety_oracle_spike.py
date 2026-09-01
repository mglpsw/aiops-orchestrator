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
# ReviewContent carries unified-hunk body lines, so the real outbound form of
# an added/deleted assignment begins with '+'/'-'.  Ignoring that marker made
# the first spike green only for synthetic plain-text shapes and blind on the
# actual transport material -- exactly the correlated-fixture error this spike
# exists to prevent.
_LINE_ASSIGNMENT_RE = re.compile(
    r"(?mi)^[ \t]*[+-]?(?:export[ \t]+)?(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)[ \t]*(?P<sep>[:=])[ \t]*(?P<value>[^\r\n]*)$"
)
_OPAQUE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9_+/=-]{24,160}(?![A-Za-z0-9_])")

_SAFE_VALUE_WORDS = {
    "", "none", "null", "false", "true", "example", "placeholder", "redacted",
    "sample", "dummy", "changeme", "secret", "token", "value", "test", "str",
    "string", "int", "integer", "bool", "boolean", "float", "bytes", "path",
    "optional[str]", "optional[int]", "secretstr", "safetext",
}
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


def _normalise_key(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _sensitive_key(value: str) -> bool:
    key = _normalise_key(value)
    if key in _SAFE_TOKEN_KEYS:
        return False
    if key in _SENSITIVE_EXACT:
        return True
    if key.endswith(("password", "passwd", "apikey", "clientsecret", "secretkey", "signingkey", "masterkey", "privatekey", "accesskey", "csrftoken", "idtoken", "authtoken", "accesstoken", "refreshtoken", "bearertoken")):
        return True
    if key.endswith("secret") and len(key) > len("secret"):
        return True
    return False


def _strip_assignment_value(value: str) -> str:
    result = value.strip().rstrip(",;")
    if len(result) >= 2 and result[0] == result[-1] and result[0] in {'\"', "'"}:
        result = result[1:-1]
    return result.strip()


def _placeholder_or_type(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _SAFE_VALUE_WORDS:
        return True
    if lowered.startswith(("${", "$", "<", "[redacted", "[placeholder")):
        return True
    # Python annotations/default-shape examples are not credentials.
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


def _looks_like_sha_or_identifier(value: str) -> bool:
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        return True
    if value.startswith(("agent-review.", "review:", "chunk-", "run-")):
        return True
    return False


def _looks_token_like_for_entropy(value: str) -> bool:
    """Keep entropy as an independent detector without flagging code names.

    A broad first attempt treated a long mixed identifier in real
    ``run_assembly_v2.py`` as a secret.  Token-shaped opaque material needs a
    stronger surface signal than entropy alone: mixed case plus several digits,
    or base64 punctuation.  This still catches the spike's deliberately opaque
    candidate while excluding ordinary ``LongPythonIdentifierV2``-style names.
    """

    if any(character in value for character in "+/="):
        return True
    return (
        any(character.islower() for character in value)
        and any(character.isupper() for character in value)
        and sum(character.isdigit() for character in value) >= 3
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

    for match in _LINE_ASSIGNMENT_RE.finditer(text):
        key = match.group("key")
        value = _strip_assignment_value(match.group("value"))
        if _sensitive_key(key) and not _placeholder_or_type(value):
            findings.append(
                OutboundSafetyFindingV2("sensitive_assignment", location, "sensitive_key_value")
            )

    # Independent broad negative oracle: opaque high-entropy strings. It is
    # intentionally subordinate to structural detectors and excludes canonical
    # SHA-sized values because AgentReview's request legitimately contains many.
    for match in _OPAQUE_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _looks_like_sha_or_identifier(token) or not _looks_token_like_for_entropy(token):
            continue
        if _entropy(token) >= 4.25:
            findings.append(
                OutboundSafetyFindingV2("high_entropy_opaque", location, "opaque_candidate")
            )
            break

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
                if not _placeholder_or_type(candidate):
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
