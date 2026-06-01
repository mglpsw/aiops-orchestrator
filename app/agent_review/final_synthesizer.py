"""Deterministic final review synthesis for offline AgentReview results."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent_review.redaction import RedactionState, redact_text, redact_value
from app.agent_review.schemas import (
    CHUNK_RESULTS_SCHEMA,
    INTAKE_SCHEMA,
    REDACTION_REPORT_SCHEMA,
    SEMANTIC_CHUNK_PLAN_SCHEMA,
    ChunkResults,
    FinalReview,
    FinalReviewCounts,
    FinalReviewCoverage,
    FinalReviewFinding,
    FinalReviewRejectedSummary,
    FinalReviewRisk,
    NormalizedFinding,
    NormalizedRisk,
    RedactionReport,
    ReviewIntake,
    SemanticChunkPlan,
)


SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
RISK_SOURCE_ORDER = {"downgraded_finding": 0, "chunk_risk": 1}
SAMPLE_TITLE_LIMIT = 5
LIMITATION_MD_LIMIT = 10

CRITICAL_LIMITATIONS = {
    "chunk_results_status_failed",
    "coverage_missing",
    "coverage_expected_files_missing",
    "coverage_file_in_multiple_states",
    "redaction_report_not_safe_for_llm",
}

_UNIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.~-])/(?:[A-Za-z0-9._@+=:-]+/)+[A-Za-z0-9._@+=:-]+")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+")


class FinalSynthesizerError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def load_json_object(path: Path | str, *, error_class: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FinalSynthesizerError(error_class, "input file not found") from exc
    except json.JSONDecodeError as exc:
        raise FinalSynthesizerError(error_class, "input JSON is invalid") from exc
    if not isinstance(raw, dict):
        raise FinalSynthesizerError(error_class, "input JSON must be an object")
    return raw


def load_chunk_results(path: Path | str) -> ChunkResults:
    return validate_chunk_results(load_json_object(path, error_class="chunk_results_invalid"))


def validate_chunk_results(raw: dict[str, Any]) -> ChunkResults:
    if raw.get("schema_id") != CHUNK_RESULTS_SCHEMA or raw.get("schema_version") != 1:
        raise FinalSynthesizerError("chunk_results_invalid", "chunk results schema is invalid")
    try:
        return ChunkResults.model_validate(raw)
    except ValidationError as exc:
        raise FinalSynthesizerError("chunk_results_invalid", "chunk results structure is invalid") from exc


def load_intake(path: Path | str) -> ReviewIntake:
    raw = load_json_object(path, error_class="intake_invalid")
    if raw.get("schema_version") != INTAKE_SCHEMA and raw.get("schema_id") != INTAKE_SCHEMA:
        raise FinalSynthesizerError("intake_invalid", "intake schema is invalid")
    try:
        return ReviewIntake.model_validate(raw)
    except ValidationError as exc:
        raise FinalSynthesizerError("intake_invalid", "intake structure is invalid") from exc


def load_semantic_chunk_plan(path: Path | str) -> SemanticChunkPlan:
    raw = load_json_object(path, error_class="chunk_plan_invalid")
    if raw.get("schema_id") != SEMANTIC_CHUNK_PLAN_SCHEMA or raw.get("schema_version") != 1:
        raise FinalSynthesizerError("chunk_plan_invalid", "semantic chunk plan schema is invalid")
    try:
        return SemanticChunkPlan.model_validate(raw)
    except ValidationError as exc:
        raise FinalSynthesizerError("chunk_plan_invalid", "semantic chunk plan structure is invalid") from exc


def load_redaction_report(path: Path | str) -> RedactionReport:
    raw = load_json_object(path, error_class="redaction_report_invalid")
    if raw.get("schema_version") != REDACTION_REPORT_SCHEMA and raw.get("schema_id") != REDACTION_REPORT_SCHEMA:
        raise FinalSynthesizerError("redaction_report_invalid", "redaction report schema is invalid")
    try:
        return RedactionReport.model_validate(raw)
    except ValidationError as exc:
        raise FinalSynthesizerError("redaction_report_invalid", "redaction report structure is invalid") from exc


def synthesize_final_review(
    chunk_results: ChunkResults,
    *,
    intake: ReviewIntake | None = None,
    chunk_plan: SemanticChunkPlan | None = None,
    redaction_report: RedactionReport | None = None,
) -> FinalReview:
    findings = _dedupe_findings(chunk_results.confirmed_findings)
    risks = _dedupe_risks(chunk_results.risks)
    rejected_summary = _rejected_summary(chunk_results)
    coverage, coverage_limitations = _coverage(chunk_results, chunk_plan=chunk_plan)
    limitations = _limitations(chunk_results, coverage_limitations, redaction_report=redaction_report)
    status = _review_status(chunk_results, limitations)
    counts = _counts(
        findings=findings,
        risks=risks,
        rejected_summary=rejected_summary,
        limitations=limitations,
        chunk_results=chunk_results,
    )
    verdict = _verdict(
        chunk_results=chunk_results,
        findings=findings,
        risks=risks,
        limitations=limitations,
        coverage=coverage,
        rejected_summary=rejected_summary,
    )
    summary = _summary(verdict=verdict, status=status, counts=counts)

    review = FinalReview(
        target_repo=_target_repo(chunk_results, intake),
        status=status,
        verdict=verdict,
        summary=summary,
        confirmed_findings=findings,
        risks=risks,
        limitations=limitations,
        rejected_summary=rejected_summary,
        coverage=coverage,
        counts=counts,
        inputs=_inputs(chunk_results, intake=intake, chunk_plan=chunk_plan, redaction_report=redaction_report),
    )
    return _sanitize_review(review)


def render_final_review_markdown(
    review: FinalReview,
    *,
    max_findings: int = 10,
    max_risks: int = 10,
) -> str:
    lines = [
        "# Agent Review — Síntese Final",
        "",
        f"**Veredito:** `{review.verdict}`",
        f"**Status:** `{review.status}`",
        f"**Escopo:** `{review.target_repo}`",
        f"**Cobertura:** {_coverage_summary(review.coverage)}",
        "",
    ]

    if review.status in {"partial", "degraded", "failed"} or _has_critical_limitation(review.limitations):
        lines.extend(
            [
                f"> Atenção: síntese em status `{review.status}`. Use as limitações abaixo para avaliar confiança.",
                "",
            ]
        )

    lines.extend(["## Achados confirmados", ""])
    if review.confirmed_findings:
        for finding in review.confirmed_findings[:max_findings]:
            lines.append(_finding_line(finding))
        if len(review.confirmed_findings) > max_findings:
            lines.append(f"- Mais {len(review.confirmed_findings) - max_findings} achado(s) omitido(s) neste resumo.")
    else:
        lines.append("Nenhum achado confirmado.")

    lines.extend(["", "## Riscos e follow-ups", ""])
    if review.risks:
        for risk in review.risks[:max_risks]:
            lines.append(_risk_line(risk))
        if len(review.risks) > max_risks:
            lines.append(f"- Mais {len(review.risks) - max_risks} risco(s) omitido(s) neste resumo.")
    else:
        lines.append("Nenhum risco ou follow-up relevante.")

    lines.extend(["", "## Limitações", ""])
    if review.limitations:
        for limitation in review.limitations[:LIMITATION_MD_LIMIT]:
            lines.append(f"- `{limitation}`")
        if len(review.limitations) > LIMITATION_MD_LIMIT:
            lines.append(f"- Mais {len(review.limitations) - LIMITATION_MD_LIMIT} limitação(ões) omitida(s) neste resumo.")
    else:
        lines.append("Nenhuma limitação relevante registrada.")

    lines.extend(["", "## Observações rejeitadas/rebaixadas", ""])
    if review.rejected_summary.total:
        lines.append(f"Total rejeitado: {review.rejected_summary.total}.")
        if review.rejected_summary.by_reason:
            reason_counts = ", ".join(
                f"{reason}={count}" for reason, count in sorted(review.rejected_summary.by_reason.items())
            )
            lines.append(f"Por motivo: {reason_counts}.")
        if review.rejected_summary.sample_titles:
            lines.append("Amostras: " + "; ".join(review.rejected_summary.sample_titles) + ".")
    else:
        lines.append("Nenhuma observação rejeitada registrada.")

    lines.extend(["", "## Próxima ação", "", _next_action(review.verdict)])
    markdown = "\n".join(lines).rstrip() + "\n"
    return _sanitize_markdown(markdown)


def _dedupe_findings(findings: list[NormalizedFinding]) -> list[FinalReviewFinding]:
    by_key: dict[tuple[str, ...], FinalReviewFinding] = {}
    for finding in findings:
        key = _finding_key(finding)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = FinalReviewFinding(
                **finding.model_dump(mode="json"),
                source_chunks=[finding.chunk_id],
                semantic_groups=[finding.semantic_group],
            )
            continue
        _append_unique(existing.source_chunks, finding.chunk_id)
        _append_unique(existing.semantic_groups, finding.semantic_group)

    return sorted(
        by_key.values(),
        key=lambda item: (SEVERITY_ORDER.get(item.severity, 99), item.file_path, item.title),
    )


def _finding_key(finding: NormalizedFinding) -> tuple[str, ...]:
    if finding.dedupe_key:
        return ("dedupe", finding.dedupe_key)
    return ("struct", finding.severity, finding.file_path, finding.title, finding.evidence)


def _dedupe_risks(risks: list[NormalizedRisk]) -> list[FinalReviewRisk]:
    by_key: dict[tuple[str, ...], FinalReviewRisk] = {}
    for risk in risks:
        key = _risk_key(risk)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = FinalReviewRisk(
                **risk.model_dump(mode="json"),
                source_chunks=[risk.chunk_id],
                semantic_groups=[risk.semantic_group],
            )
            continue
        _append_unique(existing.source_chunks, risk.chunk_id)
        _append_unique(existing.semantic_groups, risk.semantic_group)

    return sorted(
        by_key.values(),
        key=lambda item: (
            RISK_SOURCE_ORDER.get(item.source, 99),
            item.file_path or "",
            item.title,
        ),
    )


def _risk_key(risk: NormalizedRisk) -> tuple[str, ...]:
    return (
        risk.source,
        risk.severity or "",
        risk.file_path or "",
        risk.title,
        risk.reason,
        risk.evidence or "",
    )


def _rejected_summary(chunk_results: ChunkResults) -> FinalReviewRejectedSummary:
    by_reason = Counter(finding.reason for finding in chunk_results.rejected_findings)
    sample_titles: list[str] = []
    for rejected in chunk_results.rejected_findings:
        title = _clean(rejected.title)
        if title and title not in sample_titles:
            sample_titles.append(title)
        if len(sample_titles) >= SAMPLE_TITLE_LIMIT:
            break
    return FinalReviewRejectedSummary(
        total=len(chunk_results.rejected_findings),
        by_reason=dict(sorted(by_reason.items())),
        sample_titles=sample_titles,
    )


def _coverage(
    chunk_results: ChunkResults,
    *,
    chunk_plan: SemanticChunkPlan | None,
) -> tuple[FinalReviewCoverage, list[str]]:
    files_reviewed = _dedupe(chunk_results.coverage.files_reviewed)
    files_partial = _dedupe(chunk_results.coverage.files_partial)
    files_not_reviewed = _dedupe(chunk_results.coverage.files_not_reviewed)
    limitations: list[str] = []

    overlaps = _coverage_overlaps(files_reviewed, files_partial, files_not_reviewed)
    if overlaps:
        limitations.append("coverage_file_in_multiple_states")

    expected_files: list[str] = []
    missing_expected_files: list[str] = []
    extra_reported_files: list[str] = []
    comparison_available = False

    if chunk_plan is not None:
        comparison_available = True
        if chunk_plan.status != "complete":
            limitations.append(f"chunk_plan_status_{chunk_plan.status}")
        expected_files = _expected_files(chunk_plan)
        reported = _dedupe([*files_reviewed, *files_partial, *files_not_reviewed])
        missing_expected_files = [file_path for file_path in expected_files if file_path not in reported]
        extra_reported_files = [file_path for file_path in reported if file_path not in expected_files]
        if missing_expected_files:
            limitations.append("coverage_expected_files_missing")
        if extra_reported_files:
            limitations.append("coverage_reported_files_not_in_plan")

    if not files_reviewed and not files_partial and not files_not_reviewed:
        limitations.append("coverage_missing")

    return (
        FinalReviewCoverage(
            files_reviewed=files_reviewed,
            files_partial=files_partial,
            files_not_reviewed=files_not_reviewed,
            expected_files=expected_files,
            missing_expected_files=missing_expected_files,
            extra_reported_files=extra_reported_files,
            comparison_available=comparison_available,
        ),
        limitations,
    )


def _expected_files(chunk_plan: SemanticChunkPlan) -> list[str]:
    files = [
        *chunk_plan.files_covered,
        *chunk_plan.files_partially_covered,
        *chunk_plan.files_not_covered,
    ]
    for chunk in chunk_plan.chunks:
        files.extend(chunk.files)
    return _dedupe(files)


def _limitations(
    chunk_results: ChunkResults,
    coverage_limitations: list[str],
    *,
    redaction_report: RedactionReport | None,
) -> list[str]:
    limitations = list(chunk_results.limitations)
    if chunk_results.status != "complete":
        limitations.append(f"chunk_results_status_{chunk_results.status}")
    if chunk_results.chunks_failed:
        limitations.append("chunks_failed_present")
    if redaction_report is not None and not redaction_report.output_safe_for_llm:
        limitations.append("redaction_report_not_safe_for_llm")
    return _dedupe([*limitations, *coverage_limitations])


def _review_status(chunk_results: ChunkResults, limitations: list[str]) -> str:
    if chunk_results.status in {"failed", "degraded"}:
        return "degraded"
    if _has_critical_limitation(limitations):
        return "degraded"
    if chunk_results.status == "partial":
        return "partial"
    return "complete"


def _counts(
    *,
    findings: list[FinalReviewFinding],
    risks: list[FinalReviewRisk],
    rejected_summary: FinalReviewRejectedSummary,
    limitations: list[str],
    chunk_results: ChunkResults,
) -> FinalReviewCounts:
    findings_by_severity = Counter(finding.severity for finding in findings)
    risks_by_source = Counter(risk.source for risk in risks)
    return FinalReviewCounts(
        confirmed_findings_total=len(findings),
        findings_by_severity=dict(sorted(findings_by_severity.items())),
        risks_total=len(risks),
        risks_by_source=dict(sorted(risks_by_source.items())),
        rejected_findings_total=rejected_summary.total,
        rejected_findings_by_reason=rejected_summary.by_reason,
        limitations_total=len(limitations),
        chunks_parsed=len(chunk_results.chunks_parsed),
        chunks_failed=len(chunk_results.chunks_failed),
    )


def _verdict(
    *,
    chunk_results: ChunkResults,
    findings: list[FinalReviewFinding],
    risks: list[FinalReviewRisk],
    limitations: list[str],
    coverage: FinalReviewCoverage,
    rejected_summary: FinalReviewRejectedSummary,
) -> str:
    if chunk_results.status == "failed" or not _has_minimum_material(chunk_results, findings, risks, limitations, coverage):
        return "review_unavailable"

    p0_p1 = [finding for finding in findings if finding.severity in {"P0", "P1"}]
    if p0_p1 and any(_finding_is_trustworthy(finding, chunk_results) for finding in p0_p1):
        return "changes_requested"
    if p0_p1:
        return "manual_review_required"

    if (
        chunk_results.status in {"partial", "degraded"}
        or chunk_results.chunks_failed
        or _has_critical_limitation(limitations)
    ):
        return "manual_review_required"

    if any(finding.severity == "P2" for finding in findings):
        return "approve_with_required_followup"
    if risks:
        return "approve_with_required_followup"
    if any(finding.severity == "P3" for finding in findings) or limitations or rejected_summary.total:
        return "approve_with_minor_notes"
    return "approved"


def _has_minimum_material(
    chunk_results: ChunkResults,
    findings: list[FinalReviewFinding],
    risks: list[FinalReviewRisk],
    limitations: list[str],
    coverage: FinalReviewCoverage,
) -> bool:
    return any(
        [
            chunk_results.chunks_parsed,
            findings,
            risks,
            limitations,
            coverage.files_reviewed,
            coverage.files_partial,
            coverage.files_not_reviewed,
            chunk_results.rejected_findings,
        ]
    )


def _finding_is_trustworthy(finding: FinalReviewFinding, chunk_results: ChunkResults) -> bool:
    parsed_chunks = set(chunk_results.chunks_parsed)
    return (
        bool(finding.file_path)
        and bool(finding.evidence)
        and bool(finding.source_artifact or finding.line_or_hunk)
        and any(chunk_id in parsed_chunks for chunk_id in finding.source_chunks)
    )


def _summary(*, verdict: str, status: str, counts: FinalReviewCounts) -> str:
    return (
        "Síntese final preliminar "
        f"com status {status} e veredito {verdict}: "
        f"{counts.confirmed_findings_total} achado(s) confirmado(s), "
        f"{counts.risks_total} risco(s), "
        f"{counts.rejected_findings_total} observação(ões) rejeitada(s) e "
        f"{counts.limitations_total} limitação(ões)."
    )


def _inputs(
    chunk_results: ChunkResults,
    *,
    intake: ReviewIntake | None,
    chunk_plan: SemanticChunkPlan | None,
    redaction_report: RedactionReport | None,
) -> dict[str, Any]:
    return {
        "chunk_results": {
            "schema_id": chunk_results.schema_id,
            "schema_version": chunk_results.schema_version,
            "source": chunk_results.source,
            "status": chunk_results.status,
            "created_at": chunk_results.created_at,
        },
        "intake": _optional_input_ref(intake),
        "chunk_plan": _optional_input_ref(chunk_plan),
        "redaction_report": _optional_input_ref(redaction_report),
    }


def _optional_input_ref(document: Any | None) -> dict[str, Any]:
    if document is None:
        return {"provided": False}
    payload = document.model_dump(mode="json")
    return {
        "provided": True,
        "schema_id": payload.get("schema_id"),
        "schema_version": payload.get("schema_version"),
        "source": payload.get("source"),
        "status": payload.get("status"),
        "created_at": payload.get("created_at"),
    }


def _target_repo(chunk_results: ChunkResults, intake: ReviewIntake | None) -> str:
    if intake is not None and intake.target_repo:
        return intake.target_repo
    return chunk_results.target_repo


def _coverage_summary(coverage: FinalReviewCoverage) -> str:
    return (
        f"{len(coverage.files_reviewed)} reviewed, "
        f"{len(coverage.files_partial)} partial, "
        f"{len(coverage.files_not_reviewed)} not reviewed"
    )


def _finding_line(finding: FinalReviewFinding) -> str:
    parts = [
        f"- **{finding.severity}** {finding.title}",
        f"`{finding.file_path}`",
        f"Evidência: {_shorten(finding.evidence)}",
    ]
    if finding.impact:
        parts.append(f"Impacto: {_shorten(finding.impact)}")
    return " — ".join(parts) + "."


def _risk_line(risk: FinalReviewRisk) -> str:
    parts = [f"- **{risk.source}** {risk.title}", f"Motivo: {_shorten(risk.reason)}"]
    if risk.file_path:
        parts.insert(1, f"`{risk.file_path}`")
    if risk.suggested_validation:
        parts.append(f"Validação: {_shorten(risk.suggested_validation)}")
    return " — ".join(parts) + "."


def _next_action(verdict: str) -> str:
    if verdict == "changes_requested":
        return "Corrigir os achados P0/P1 antes de merge; este veredito é preliminar e será refinado pela Phase 05 Quality Gate."
    if verdict == "manual_review_required":
        return "Executar revisão manual antes de usar este resultado como sinal de merge."
    if verdict == "approve_with_required_followup":
        return "Tratar os follow-ups indicados antes ou logo após o merge, conforme política do projeto."
    if verdict == "approve_with_minor_notes":
        return "Prosseguir somente com acompanhamento das notas menores registradas."
    if verdict == "approved":
        return "Nenhum blocker foi identificado pela síntese determinística; manter as validações normais do projeto."
    return "Não usar este resultado para decisão de merge; refazer os inputs ou revisar manualmente."


def _sanitize_review(review: FinalReview) -> FinalReview:
    redaction_state = RedactionState()
    redaction_state.record_file()
    redacted = redact_value(review.model_dump(mode="json"), redaction_state)
    redacted = _redact_local_paths(redacted)
    return FinalReview.model_validate(redacted)


def _sanitize_markdown(markdown: str) -> str:
    redaction_state = RedactionState()
    redaction_state.record_file()
    return _redact_local_paths_in_text(redact_text(markdown, redaction_state))


def _redact_local_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_local_paths(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_local_paths(item) for item in value]
    if isinstance(value, str):
        if _is_absolute_path(value):
            return "[LOCAL_PATH_REDACTED]"
        return _redact_local_paths_in_text(value)
    return value


def _redact_local_paths_in_text(value: str) -> str:
    redacted = _WINDOWS_ABSOLUTE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", value)
    return _UNIX_ABSOLUTE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", redacted)


def _is_absolute_path(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("/") or stripped.startswith("~/") or bool(re.match(r"^[A-Za-z]:\\", stripped))


def _coverage_overlaps(*groups: list[str]) -> set[str]:
    seen: set[str] = set()
    overlaps: set[str] = set()
    for group in groups:
        for file_path in group:
            if file_path in seen:
                overlaps.add(file_path)
            seen.add(file_path)
    return overlaps


def _has_critical_limitation(limitations: list[str]) -> bool:
    return any(limitation in CRITICAL_LIMITATIONS for limitation in limitations)


def _append_unique(values: list[Any], value: Any) -> None:
    if value not in values:
        values.append(value)


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _shorten(value: str, *, limit: int = 180) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."
