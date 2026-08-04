"""Safety gate for the AgentReview v2 benchmark corpus (#88, rev. 4.1 §9).

This module protects the SYNTHETIC benchmark corpus committed under
`evals/agent_review_v2/case_sources/` and
`evals/agent_review_v2/reviewable_corpus/`. It is explicitly NOT the
DLP/PHI gate for InterLeitos production traffic, nor the RI-B0 #160 gate --
scope is limited to proving that #88's own corpus contains no secret,
credential, local path, or real-world personal identifier.

Two independent checks, run as DETECTORS, never as silent sanitizers:

1. Secrets/credentials/local paths -- reuses
   `app.agent_review.redaction.sanitize_artifact_value`, the same
   canonical sanitizer every publish-facing module in this codebase uses.
   If `sanitize_artifact_value(original) != original`, the artifact FAILS
   outright. A "cleaned" version is never silently substituted and
   versioned in its place -- that would hide that the original acquisition
   contained material that should never have been captured at all.

2. Real-world personal identifiers -- CPF, CNS (Cartao Nacional de
   Saude), phone numbers, and e-mail addresses. `redaction.py` has no
   detector for any of these (verified: it covers only tokens,
   credentials, cookies, database/credential URLs, and local paths), so
   this module adds them independently.

This gate does NOT ban clinical vocabulary generically -- a safe
counterexample case may legitimately contain fabricated, clearly
fictional clinical-sounding terms (e.g. "condition_code_x"), and banning
"anything that sounds clinical" would destroy exactly that kind of
counterexample. It bans PERSONAL IDENTIFIERS and unredacted
secrets/paths, and nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.agent_review.redaction import sanitize_artifact_value

# Directories that are legitimately excluded from the corpus safety walk
# (never text content of the corpus itself).
_EXCLUDED_DIR_NAMES = {"__pycache__", ".git"}

# CPF: either the canonical dotted/dashed format, or 11 bare digits that
# also satisfy the real CPF check-digit algorithm (to avoid flagging every
# unrelated 11-digit number, e.g. a phone number or a large integer
# literal, as a false positive).
_CPF_FORMATTED_RE = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_CPF_BARE_RE = re.compile(r"(?<!\d)\d{11}(?!\d)")

# CNS (Cartao Nacional de Saude): 15 digits, first digit in {1,2,7,8,9}
# per the real CNS numbering scheme.
_CNS_RE = re.compile(r"(?<!\d)[127-9]\d{14}(?!\d)")

# Brazilian phone numbers: optional area code in parens, optional 9-digit
# mobile prefix, hyphen-separated body.
_PHONE_RE = re.compile(r"\(?\b\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}\b")

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _cpf_check_digits_valid(digits: str) -> bool:
    """The real CPF check-digit algorithm -- used only to decide whether a
    bare 11-digit run is plausibly a CPF (and therefore must be treated as
    a real-world identifier) rather than an unrelated numeric literal."""
    if len(set(digits)) == 1:
        return False  # all-same-digit CPFs are never valid, and common as noise
    nums = [int(d) for d in digits]

    def _check(nums_slice: list[int], weight_start: int) -> int:
        total = sum(n * w for n, w in zip(nums_slice, range(weight_start, 1, -1)))
        remainder = (total * 10) % 11
        return 0 if remainder == 10 else remainder

    d1 = _check(nums[:9], 10)
    d2 = _check(nums[:9] + [d1], 11)
    return nums[9] == d1 and nums[10] == d2


@dataclass(frozen=True)
class SafetyFinding:
    path: str
    category: str  # "secret_or_path" | "cpf" | "cns" | "phone" | "email"
    detail: str


@dataclass
class SafetyReport:
    findings: list[SafetyFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, path: str, category: str, detail: str) -> None:
        self.findings.append(SafetyFinding(path=path, category=category, detail=detail))


def detect_identifiers(text: str) -> list[tuple[str, str]]:
    """Return (category, matched_text) for every real-world personal
    identifier found. Never flags fabricated technical vocabulary (e.g.
    "condition_code_x") -- these patterns match only identifier SHAPES,
    not clinical terminology."""
    findings: list[tuple[str, str]] = []
    for match in _CPF_FORMATTED_RE.finditer(text):
        findings.append(("cpf", match.group(0)))
    for match in _CPF_BARE_RE.finditer(text):
        if _cpf_check_digits_valid(match.group(0)):
            findings.append(("cpf", match.group(0)))
    for match in _CNS_RE.finditer(text):
        findings.append(("cns", match.group(0)))
    for match in _PHONE_RE.finditer(text):
        findings.append(("phone", match.group(0)))
    for match in _EMAIL_RE.finditer(text):
        findings.append(("email", match.group(0)))
    return findings


def validate_text(relpath: str, text: str, report: SafetyReport) -> None:
    """Run both checks against one file's content, recording findings
    (never silently repairing them) into `report`."""
    sanitized = sanitize_artifact_value(text)
    if sanitized != text:
        report.add(
            relpath,
            "secret_or_path",
            "sanitize_artifact_value(original) != original -- unredacted "
            "secret/credential/local-path-shaped content detected; the "
            "artifact is REJECTED, not silently replaced with the "
            "sanitized version",
        )
    for category, matched in detect_identifiers(text):
        report.add(relpath, category, f"real-world identifier shape detected: {matched!r}")


def validate_tree(root: Path) -> SafetyReport:
    """Walk every text file under `root` (skipping VCS/build noise) and
    run `validate_text` against each. Binary files are read as UTF-8 and
    skipped (not silently ignored as safe) if they fail to decode, since
    this corpus is exclusively text/YAML/Python by construction."""
    report = SafetyReport()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        relpath = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report.add(relpath, "secret_or_path", "file is not valid UTF-8 text -- rejected, not skipped")
            continue
        validate_text(relpath, text, report)
    return report
