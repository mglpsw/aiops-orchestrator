"""AgentReview v2 evaluation harness (issue #88, Lane 1: AIOps v2
deterministic/offline).

Runs the REAL, already-merged v2 pipeline (`profile_loader_v2`,
`run_assembly_v2`, `payload_builder_v2`, `consumer_v2`/`parser_v2`,
`synthesis_v2`, `readiness_decision_v2`) against synthetic evaluation
cases, and measures whether the pipeline's own contract/readiness/coverage
behavior matches each case's declared expectation.

## What this lane measures, and what it deliberately does not

This lane does NOT test whether a real LLM/Codex would actually notice a
given defect -- that requires a real provider call, which is explicitly out
of scope for this offline slice (see the module docstring of
`docs/AGENT_REVIEW_V2_BENCHMARK.md`'s own "deliberately not executed"
section). Instead, each case's `injected_findings` are synthesized directly
into a chunk response (standing in for "a reviewer, human or model, claimed
this"), and this harness measures whether the DETERMINISTIC pipeline
downstream of that claim -- coverage bridging, lifecycle aggregation,
readiness precedence, stale/binary-block handling -- reaches the case's
declared `expected_readiness` and preserves every expected finding's
identity through to the final decision. A mismatch is a real, meaningful
signal: either the case's own expectation is wrong, or the pipeline has a
real bug -- never a measure of "how good is the reviewer at finding bugs",
which is out of scope here by design.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_review.consumer_v2 import bind_chunk_response_v2
from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    DispositionEvidenceV2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    ReadinessReasonV2,
    SemanticGroupV2,
    compute_response_sha256_v2,
)
from app.agent_review.diff_acquisition_v2 import ParsedFileDiffV2, ParsedHunkV2
from app.agent_review.parser_v2 import parse_bound_chunk_response_v2
from app.agent_review.payload_builder_v2 import build_chunk_payloads_from_profile_v2
from app.agent_review.profile_loader_v2 import load_target_profile_v2
from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)
from app.agent_review.synthesis_v2 import synthesize_chunk_results_v2

_BASE_SHA = "1" * 40
_TESTED_MERGE_SHA = "3" * 40
_TOOLREPO_SHA = "4" * 40
_EVIDENCE_HASH = "d" * 64

# One shared, canonical grouping policy per target, reused across every
# case -- never rebuilt per case, so a change in behavior across cases is
# never attributable to an accidentally different policy.
_TARGET_POLICIES: dict[str, list[tuple[str, SemanticGroupV2, str]]] = {
    "agent_escala": [
        ("backend", SemanticGroupV2.PRIMARY_BACKEND_LOGIC, "backend/scheduling/*.py"),
        ("tests", SemanticGroupV2.TESTS, "tests/scheduling/*.py"),
    ],
    "interleitos": [
        ("backend", SemanticGroupV2.PRIMARY_BACKEND_LOGIC, "backend/tenancy/*.py"),
        ("api", SemanticGroupV2.API_SCHEMA_CONTRACT, "backend/api/*.py"),
    ],
}


class ExpectedFindingV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    severity: Literal["P0", "P1", "P2", "P3"]
    file_path: str
    line_start: int
    line_end: int
    invariant: str
    root_cause: str


class CaseHunkV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    seed: str


class CaseFileV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    path: str
    hunks: list[CaseHunkV2] = Field(default_factory=list)
    is_binary: bool = False


class InjectedFindingV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    file_path: str
    severity: Literal["P0", "P1", "P2", "P3"]
    line_start: int
    line_end: int
    title: str = "synthetic-eval-finding"


class EvalCaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    case_id: str
    category: Literal["contract", "security", "coverage", "domain", "false-positive", "stale"]
    target: Literal["agent_escala", "interleitos"]
    files: list[CaseFileV2]
    injected_findings: list[InjectedFindingV2] = Field(default_factory=list)
    confirmed_findings: list[InjectedFindingV2] = Field(default_factory=list)
    injected_limitations: list[str] = Field(default_factory=list)
    stale_reason_codes: list[str] = Field(default_factory=list)
    expected_readiness: Literal["ready", "blocked_code", "blocked_pipeline", "manual_required", "stale"]
    expected_findings: list[ExpectedFindingV2] = Field(default_factory=list)
    forbidden_findings: list[ExpectedFindingV2] = Field(default_factory=list)
    must_review_fragments_complete: bool = True
    safe_counterexample: bool = False
    rationale: str


@dataclass(frozen=True)
class EvalCaseResultV2:
    """Plain, freely constructible data value -- like `SynthesisResultV2`,
    not a wire contract. One case's real, measured outcome."""

    case_id: str
    category: str
    actual_readiness: str
    expected_readiness: str
    readiness_matches: bool
    expected_findings_recovered: int
    expected_findings_total: int
    forbidden_findings_leaked: int
    manifest_hash: str | None
    payload_hashes: tuple[str, ...]
    duration_ms: float
    chunk_count: int
    fragment_count: int
    blocked_at_assembly: bool
    blocked_reason_code: str | None
    error: str | None = None


def _policy_for_target(target: str) -> SemanticGroupingPolicyV2:
    rules = [
        SemanticGroupingRuleV2(
            rule_id=rule_id,
            semantic_group=group,
            path_patterns=[pattern],
            contract_ids=[],
            artifact_ids=[],
            priority=0,
        )
        for rule_id, group, pattern in _TARGET_POLICIES[target]
    ]
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": rules,
        "fallback_group": None,
    }
    policy_sha256 = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json") for rule in rules]}
    )
    return SemanticGroupingPolicyV2(**material, policy_sha256=policy_sha256)


def _file_diff(case_file: CaseFileV2) -> ParsedFileDiffV2:
    hunks = tuple(
        ParsedHunkV2(
            old_start=h.old_start,
            old_lines=h.old_lines,
            new_start=h.new_start,
            new_lines=h.new_lines,
            diff_sha256=hashlib.sha256(h.seed.encode()).hexdigest(),
            diff_chars=40,
        )
        for h in case_file.hunks
    )
    return ParsedFileDiffV2(
        old_path=case_file.path,
        new_path=case_file.path,
        change_type="modified",
        is_binary=case_file.is_binary,
        is_submodule=False,
        similarity_index=None,
        old_no_newline_at_eof=False,
        new_no_newline_at_eof=False,
        hunks=hunks,
        truncated=False,
    )


def _synthetic_envelope(
    payload: ChunkPayloadV2, *, findings: list[dict], limitations: list[str]
) -> dict:
    envelope: dict = {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "source": "agent-review-provider-response",
        "status": "success",
        "run_id": payload.run_id,
        "chunk_id": payload.chunk_id,
        "payload_sha256": payload.payload_sha256,
        "head_sha": payload.identity.head_sha,
        "provider": "openai",
        "model": "gpt-5.4",
        "attempt": 1,
        "request_id": f"eval-{payload.chunk_id}",
        "finish_reason": "stop",
        "response_received": True,
        "response_sha256": "9" * 64,
        "result": {
            "schema_id": "agent-review.chunk-response.v2",
            "schema_version": 2,
            "summary": "synthetic-eval-response",
            "findings": findings,
            "coverage": {
                "status": "complete",
                "expected_files": payload.coverage.expected_files,
                "reviewed_files": payload.coverage.expected_files,
                "partially_reviewed_files": [],
                "missing_files": [],
                "must_review_files": payload.coverage.must_review_files,
                "missing_must_review_files": [],
                "degradation_causes": [],
            },
            "limitations": limitations,
        },
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def _finding_dict(finding: InjectedFindingV2, index: int) -> dict:
    return {
        "finding_id": f"eval-finding-{index:03d}",
        "severity": finding.severity,
        "title": finding.title,
        "file_path": finding.file_path,
        "line_start": finding.line_start,
        "line_end": finding.line_end,
        "evidence": "synthetic, illustrative only -- issue #88 benchmark corpus",
        "impact": "synthetic, illustrative only -- issue #88 benchmark corpus",
        "confidence": "high",
        "contract_ids": [],
        "disposition": "new",
    }


class EvalCaseError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def run_eval_case_v2(case: EvalCaseV2, *, fixtures_root: Path, head_sha: str) -> EvalCaseResultV2:
    """Run one case through the real v2 pipeline exactly once. Deterministic
    given the case content and `fixtures_root` -- never reads real network,
    real provider, or real GitHub state."""

    started = time.perf_counter()
    profile = load_target_profile_v2(fixtures_root / case.target)
    policy = _policy_for_target(case.target)
    file_diffs = [_file_diff(f) for f in case.files]

    outcome = assemble_manifest_from_diff_v2(
        file_diffs,
        profile=profile,
        grouping_policy=policy,
        repo=profile.identity.repo,
        pr_number=1,
        base_sha=_BASE_SHA,
        head_sha=head_sha,
        tested_merge_sha=_TESTED_MERGE_SHA,
        toolrepo_sha=_TOOLREPO_SHA,
        evidence_hash=_EVIDENCE_HASH,
        max_lines_per_chunk=200,
    )

    if outcome.state == "blocked_pipeline":
        duration_ms = (time.perf_counter() - started) * 1000
        actual_readiness = "blocked_pipeline"
        return EvalCaseResultV2(
            case_id=case.case_id,
            category=case.category,
            actual_readiness=actual_readiness,
            expected_readiness=case.expected_readiness,
            readiness_matches=actual_readiness == case.expected_readiness,
            expected_findings_recovered=0,
            expected_findings_total=len(case.expected_findings),
            forbidden_findings_leaked=0,
            manifest_hash=None,
            payload_hashes=(),
            duration_ms=duration_ms,
            chunk_count=0,
            fragment_count=0,
            blocked_at_assembly=True,
            blocked_reason_code=outcome.blocked_reason.reason_code if outcome.blocked_reason else None,
        )

    manifest = outcome.manifest
    assert manifest is not None

    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=fixtures_root / case.target)

    # `confirmed_findings` are injected exactly like `injected_findings` (a
    # confirmation can only ever apply to something that was actually
    # observed this run) -- the difference is entirely in what happens
    # AFTER the first synthesis pass, below.
    all_injected = list(case.injected_findings) + list(case.confirmed_findings)
    raw_id_to_key: dict[str, tuple[str, int, int, str]] = {}
    findings_by_file: dict[str, list[dict]] = {}
    for index, injected in enumerate(all_injected):
        raw_id = f"eval-finding-{index:03d}"
        raw_id_to_key[raw_id] = (injected.file_path, injected.line_start, injected.line_end, injected.severity)
        findings_by_file.setdefault(injected.file_path, []).append(_finding_dict(injected, index))

    def _build_chunk_results() -> list:
        chunk_results = []
        for position, b in enumerate(built):
            chunk_findings: list[dict] = []
            for file_path in b.payload.coverage.expected_files:
                chunk_findings.extend(findings_by_file.get(file_path, []))
            limitations = case.injected_limitations if position == 0 else []
            envelope = _synthetic_envelope(b.payload, findings=chunk_findings, limitations=limitations)
            bound = bind_chunk_response_v2(envelope=envelope, payload=b.payload)
            chunk_results.append(parse_bound_chunk_response_v2(bound))
        return chunk_results

    results = _build_chunk_results()
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=head_sha)

    if case.confirmed_findings:
        # First pass (above) establishes the real, engine-computed
        # finding_id for each confirmed_findings entry via its provenance's
        # original_finding_id. A second synthesis pass, with a matching
        # prior_lifecycle CONFIRMED record for exactly those finding_ids,
        # is what actually promotes them -- mirroring the real
        # new-then-confirmed round-trip #86's own test suite established.
        confirmed_keys = {
            (f.file_path, f.line_start, f.line_end, f.severity) for f in case.confirmed_findings
        }
        finding_id_by_key: dict[tuple[str, int, int, str], str] = {}
        for record in synthesis.findings:
            for prov in synthesis.provenance.get(record.finding_id, ()):
                key = raw_id_to_key.get(prov.original_finding_id)
                if key is not None:
                    finding_id_by_key[key] = record.finding_id

        prior_lifecycle = []
        for record in synthesis.findings:
            matching_key = next((k for k, fid in finding_id_by_key.items() if fid == record.finding_id), None)
            if matching_key is not None and matching_key in confirmed_keys:
                prior_lifecycle.append(
                    FindingLifecycleRecordV2(
                        finding_id=record.finding_id,
                        severity=record.severity,
                        observed_at_head_sha=head_sha,
                        disposition=FindingDispositionV2.CONFIRMED,
                        actionable=True,
                        justification="synthetic, illustrative confirmation -- issue #88 benchmark corpus",
                        decided_by="eval-harness",
                        decided_at_head_sha=head_sha,
                        evidence=[
                            DispositionEvidenceV2(kind="test", reference="eval-repro-test", head_sha=head_sha)
                        ],
                        superseded_by=None,
                    )
                )

        results = _build_chunk_results()
        synthesis = synthesize_chunk_results_v2(
            manifest=manifest, chunk_results=results, evaluated_head_sha=head_sha, prior_lifecycle=prior_lifecycle
        )

    stale_codes = frozenset(ReadinessReasonV2(code) for code in case.stale_reason_codes)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=profile.policies, stale_reason_codes=stale_codes
    )

    # Map each surviving, actionable NEW/CONFIRMED finding back to its
    # ORIGINAL (file_path, line_start, line_end, severity) via real
    # provenance -- never a count-based approximation. This is what makes
    # the recall/forbidden-leak check a genuine identity-preservation proof
    # rather than a coincidental size comparison.
    surviving_keys: set[tuple[str, int, int, str]] = set()
    for record in synthesis.findings:
        if record.disposition not in (FindingDispositionV2.NEW, FindingDispositionV2.CONFIRMED) or not record.actionable:
            continue
        for prov in synthesis.provenance.get(record.finding_id, ()):
            key = raw_id_to_key.get(prov.original_finding_id)
            if key is not None:
                surviving_keys.add(key)

    survived_count = sum(
        1
        for f in case.expected_findings
        if (f.file_path, f.line_start, f.line_end, f.severity) in surviving_keys
    )
    forbidden_leaked = sum(
        1
        for f in case.forbidden_findings
        if (f.file_path, f.line_start, f.line_end, f.severity) in surviving_keys
    )

    duration_ms = (time.perf_counter() - started) * 1000
    return EvalCaseResultV2(
        case_id=case.case_id,
        category=case.category,
        actual_readiness=decision.state.value,
        expected_readiness=case.expected_readiness,
        readiness_matches=decision.state.value == case.expected_readiness,
        expected_findings_recovered=survived_count,
        expected_findings_total=len(case.expected_findings),
        forbidden_findings_leaked=forbidden_leaked,
        manifest_hash=manifest.identity.manifest_hash,
        payload_hashes=tuple(sorted(b.payload.payload_sha256 for b in built)),
        duration_ms=duration_ms,
        chunk_count=len(manifest.chunks),
        fragment_count=len(manifest.fragments),
        blocked_at_assembly=False,
        blocked_reason_code=None,
    )


@dataclass(frozen=True)
class EvalSummaryV2:
    total_cases: int
    readiness_matches: int
    readiness_mismatches: tuple[str, ...]
    false_approvals: tuple[str, ...]
    stale_cases_total: int
    stale_cases_correct: int
    expected_findings_total: int
    expected_findings_recovered: int
    forbidden_findings_leaked_total: int
    total_duration_ms: float


def compute_eval_summary_v2(results: list[EvalCaseResultV2]) -> EvalSummaryV2:
    readiness_mismatches = tuple(r.case_id for r in results if not r.readiness_matches)
    # A false approval is the single most critical pipeline KPI the issue
    # names explicitly: a case that should NOT have been ready, but was.
    false_approvals = tuple(
        r.case_id for r in results if r.expected_readiness != "ready" and r.actual_readiness == "ready"
    )
    stale_cases = [r for r in results if r.expected_readiness == "stale"]
    return EvalSummaryV2(
        total_cases=len(results),
        readiness_matches=sum(1 for r in results if r.readiness_matches),
        readiness_mismatches=readiness_mismatches,
        false_approvals=false_approvals,
        stale_cases_total=len(stale_cases),
        stale_cases_correct=sum(1 for r in stale_cases if r.actual_readiness == "stale"),
        expected_findings_total=sum(r.expected_findings_total for r in results),
        expected_findings_recovered=sum(r.expected_findings_recovered for r in results),
        forbidden_findings_leaked_total=sum(r.forbidden_findings_leaked for r in results),
        total_duration_ms=sum(r.duration_ms for r in results),
    )
