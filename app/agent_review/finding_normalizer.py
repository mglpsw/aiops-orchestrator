"""Deterministic normalization for structured AgentReview chunk findings."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent_review.schemas import (
    ChunkResponse,
    ChunkResponseFinding,
    NormalizedFinding,
    NormalizedRisk,
    RejectedFinding,
    SemanticChunk,
)


SEVERITIES = {"P0", "P1", "P2", "P3"}
CONFIDENCE = {"high", "medium", "low"}
PLACEHOLDER_EVIDENCE = {
    "redacted",
    "masked",
    "placeholder",
    "faketoken",
    "testtoken",
    "dummy",
    "example",
}
ALLOWED_TEST_FAILURE_SOURCES = {
    "checks",
    "test-intelligence",
    "local-code-intelligence",
}
SPECULATIVE_TERMS = (
    "might",
    "may",
    "could",
    "possibly",
    "seems",
    "appears",
    "likely",
    "suspect",
    "probably",
    "pode",
    "talvez",
    "parece",
)
TEST_FAILURE_TERMS = (
    "test failure",
    "failed test",
    "failing test",
    "test failed",
    "pytest",
    "assertionerror",
)


@dataclass
class DedupeState:
    seen: set[str] = field(default_factory=set)

    def duplicate_reason(self, finding: ChunkResponseFinding) -> str | None:
        key = _dedupe_lookup_key(finding)
        if not key:
            return None
        if key in self.seen:
            return "duplicate_dedupe_key"
        self.seen.add(key)
        return None


@dataclass
class NormalizedChunk:
    confirmed_findings: list[NormalizedFinding] = field(default_factory=list)
    risks: list[NormalizedRisk] = field(default_factory=list)
    rejected_findings: list[RejectedFinding] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def normalize_chunk_response(
    response: ChunkResponse,
    *,
    chunk: SemanticChunk,
    dedupe_state: DedupeState,
) -> NormalizedChunk:
    normalized = NormalizedChunk()

    for risk in response.risks:
        title = _clean(risk.title)
        reason = _clean(risk.reason)
        if not title or not reason:
            normalized.limitations.append(f"invalid_chunk_risk:{chunk.chunk_id}")
            continue
        normalized.risks.append(
            NormalizedRisk(
                chunk_id=chunk.chunk_id,
                semantic_group=chunk.semantic_group,
                source="chunk_risk",
                title=title,
                reason=reason,
                missing_evidence=_clean(risk.missing_evidence),
                suggested_validation=_clean(risk.suggested_validation),
            )
        )

    for finding in response.confirmed_findings:
        outcome = _normalize_finding(finding, chunk=chunk, dedupe_state=dedupe_state)
        if isinstance(outcome, NormalizedFinding):
            normalized.confirmed_findings.append(outcome)
        elif isinstance(outcome, NormalizedRisk):
            normalized.risks.append(outcome)
        else:
            normalized.rejected_findings.append(outcome)

    return normalized


def _normalize_finding(
    finding: ChunkResponseFinding,
    *,
    chunk: SemanticChunk,
    dedupe_state: DedupeState,
) -> NormalizedFinding | NormalizedRisk | RejectedFinding:
    title = _clean(finding.title)
    file_path = _clean(finding.file_path)
    severity = _clean(finding.severity)
    evidence = _clean(finding.evidence)
    impact = _clean(finding.impact)
    source_artifact = _clean(finding.source_artifact)
    line_or_hunk = _clean(finding.line_or_hunk)

    if not file_path:
        return _reject(finding, chunk, "missing_file_path")
    if file_path not in set(chunk.files):
        return _reject(finding, chunk, "file_not_in_chunk")
    if severity not in SEVERITIES:
        return _reject(finding, chunk, "invalid_finding")
    if not title:
        return _reject(finding, chunk, "invalid_finding")

    duplicate_reason = dedupe_state.duplicate_reason(finding)
    if duplicate_reason:
        return _reject(finding, chunk, duplicate_reason)

    missing = _missing_required_fields(
        title=title,
        impact=impact,
        evidence=evidence,
        source_artifact=source_artifact,
        line_or_hunk=line_or_hunk,
    )
    if missing:
        return _downgrade_or_reject(
            finding,
            chunk,
            reason="missing_required_evidence",
            missing_evidence=", ".join(missing),
        )

    if _has_speculative_language(title, evidence):
        return _downgrade_or_reject(
            finding,
            chunk,
            reason="speculative_language",
            missing_evidence="concrete non-speculative evidence",
        )

    if severity in {"P0", "P1"} and _is_placeholder_only_evidence(evidence):
        return _downgrade_or_reject(
            finding,
            chunk,
            reason="redacted_or_placeholder_only_evidence",
            missing_evidence="non-placeholder evidence",
        )

    if _is_test_failure_claim(title, evidence, impact) and not _has_allowed_test_failure_source(source_artifact):
        return _downgrade_or_reject(
            finding,
            chunk,
            reason="unsupported_test_failure_source",
            missing_evidence="checks/test-intelligence/local-code-intelligence source artifact",
        )

    return NormalizedFinding(
        chunk_id=chunk.chunk_id,
        semantic_group=chunk.semantic_group,
        severity=severity,  # type: ignore[arg-type]
        title=title,
        file_path=file_path,
        line_or_hunk=line_or_hunk,
        evidence=evidence,
        source_artifact=source_artifact,
        contract_id=_clean(finding.contract_id),
        impact=impact,
        confidence=_confidence(finding.confidence),
        dedupe_key=_clean(finding.dedupe_key),
    )


def _downgrade_or_reject(
    finding: ChunkResponseFinding,
    chunk: SemanticChunk,
    *,
    reason: str,
    missing_evidence: str,
) -> NormalizedRisk | RejectedFinding:
    title = _clean(finding.title)
    if not title:
        return _reject(finding, chunk, "invalid_finding")
    return NormalizedRisk(
        chunk_id=chunk.chunk_id,
        semantic_group=chunk.semantic_group,
        source="downgraded_finding",
        title=title,
        reason=reason,
        missing_evidence=missing_evidence,
        severity=_clean(finding.severity),
        file_path=_clean(finding.file_path),
        evidence=_clean(finding.evidence),
        impact=_clean(finding.impact),
        dedupe_key=_clean(finding.dedupe_key),
    )


def _reject(finding: ChunkResponseFinding, chunk: SemanticChunk, reason: str) -> RejectedFinding:
    return RejectedFinding(
        chunk_id=chunk.chunk_id,
        semantic_group=chunk.semantic_group,
        reason=reason,  # type: ignore[arg-type]
        title=_clean(finding.title),
        severity=_clean(finding.severity),
        file_path=_clean(finding.file_path),
        evidence=_clean(finding.evidence),
        dedupe_key=_clean(finding.dedupe_key),
    )


def _missing_required_fields(
    *,
    title: str | None,
    impact: str | None,
    evidence: str | None,
    source_artifact: str | None,
    line_or_hunk: str | None,
) -> list[str]:
    missing: list[str] = []
    if not title:
        missing.append("title")
    if not impact:
        missing.append("impact")
    if not evidence:
        missing.append("evidence")
    if not source_artifact and not line_or_hunk:
        missing.append("source_artifact_or_line_or_hunk")
    return missing


def _dedupe_lookup_key(finding: ChunkResponseFinding) -> str | None:
    explicit = _clean(finding.dedupe_key)
    if explicit:
        return f"dedupe:{explicit}"

    severity = _clean(finding.severity)
    file_path = _clean(finding.file_path)
    title = _clean(finding.title)
    evidence = _clean(finding.evidence)
    if not all([severity, file_path, title, evidence]):
        return None
    return "struct:" + "\x1f".join([severity, file_path, title, evidence])


def _has_speculative_language(*values: str | None) -> bool:
    haystack = " ".join(value.lower() for value in values if value)
    return any(re.search(rf"\b{re.escape(term)}\b", haystack) for term in SPECULATIVE_TERMS)


def _is_placeholder_only_evidence(evidence: str | None) -> bool:
    if not evidence:
        return True
    without_placeholders = re.sub(
        r"(?i)(\[redacted\]|\*\*\*masked\*\*\*|placeholder|fake-token|test-token|dummy|example)",
        "",
        evidence,
    )
    if not re.sub(r"[^a-z0-9]+", "", without_placeholders.lower()):
        return True
    compact = re.sub(r"[^a-z0-9]+", "", evidence.lower())
    return compact in PLACEHOLDER_EVIDENCE


def _is_test_failure_claim(*values: str | None) -> bool:
    haystack = " ".join(value.lower() for value in values if value)
    return any(term in haystack for term in TEST_FAILURE_TERMS)


def _has_allowed_test_failure_source(source_artifact: str | None) -> bool:
    if not source_artifact:
        return False
    normalized = source_artifact.lower()
    return any(allowed in normalized for allowed in ALLOWED_TEST_FAILURE_SOURCES)


def _confidence(value: str | None) -> str | None:
    cleaned = _clean(value)
    if cleaned in CONFIDENCE:
        return cleaned
    return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
