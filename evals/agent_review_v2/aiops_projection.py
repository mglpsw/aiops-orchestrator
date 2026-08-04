"""AIOps-side projection onto a REAL PR/HEAD (#88 OP-BENCH step 2A, rev. 4.1).

Runs the real v2 pipeline (manifest assembly -> payload build -> synthesis
-> readiness decision) against the REAL diff of a real, already-open
synthetic OP-BENCH PR, bound to that PR's real (repo, pr_number, base_sha,
head_sha) -- never the Lane 1 harness's `_EVAL_HEAD_SHA` constant. This is
what makes correlation against Codex's own (repo, pr_number, head_sha)-bound
observations possible at all: `correlate_observation_v2` requires exact
identity equality first, and a synthetic identity would make every
correlation `rejected` by construction.

Explicitly a PROJECTOR, not a new authority: it reuses ONLY the same
already-merged engine functions Lane 1 (`evals/agent_review_v2/harness.py`)
reuses (`load_target_profile_v2`, `assemble_manifest_from_diff_v2`,
`build_chunk_payloads_from_profile_v2`, `bind_chunk_response_v2`/
`parse_bound_chunk_response_v2`, `synthesize_chunk_results_v2`,
`compute_readiness_decision_v2`), adds no review logic and no gate
authority of its own, and produces `AiopsFindingReferenceV2` records that
`evals/agent_review_v2/observation.py` already treats as strictly
non-authoritative (never written back into any `FindingLifecycleRecordV2`
or `ReviewReadinessV2`).

Still measures PIPELINE CORRECTNESS, not detection: exactly like Lane 1,
the "finding" a chunk response carries is INJECTED from the case's own
`expected.yaml` ground truth (representing "a reviewer claimed this"), not
independently detected by this module. A precision/recall number computed
from this projection alone would be vacuous by construction -- this module
intentionally does not compute one; see `report_v2.py` for the split
between `aiops_pipeline` (this projection's own metrics) and
`codex_local_detection`/`codex_github_detection` (the actual detection
lanes, computed against a REAL observer that can be wrong).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.agent_review.consumer_v2 import bind_chunk_response_v2
from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    SemanticGroupV2,
    compute_response_sha256_v2,
)
from app.agent_review.diff_acquisition_v2 import acquire_diff_v2, parse_unified_diff
from app.agent_review.payload_builder_v2 import build_chunk_payloads_from_profile_v2
from app.agent_review.parser_v2 import parse_bound_chunk_response_v2
from app.agent_review.profile_loader_v2 import load_target_profile_v2
from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)
from app.agent_review.synthesis_v2 import synthesize_chunk_results_v2

from evals.agent_review_v2.observation import AiopsFindingReferenceV2

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "tests" / "agent_review" / "fixtures" / "v2"

_TESTED_MERGE_SHA_PLACEHOLDER = "3" * 40  # no real merge has been tested for an unmerged synthetic PR
_TOOLREPO_SHA = None  # filled by caller with the real aiops-orchestrator HEAD at projection time
_EVIDENCE_HASH = "d" * 64  # no Validation Evidence artifact exists for these synthetic PRs (post-v2 #64)

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


def _policy_for_target(target: str) -> SemanticGroupingPolicyV2:
    rules = [
        SemanticGroupingRuleV2(
            rule_id=rule_id, semantic_group=group, path_patterns=[pattern], contract_ids=[], artifact_ids=[], priority=0
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


def _finding_dict(*, file_path: str, severity: str, line_start: int, line_end: int, finding_id: str) -> dict:
    return {
        "finding_id": finding_id,
        "severity": severity,
        "title": "synthetic: OP-BENCH AIOps-side projection finding, issue #88",
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "evidence": "synthetic, illustrative only -- issue #88 benchmark corpus",
        "impact": "synthetic, illustrative only -- issue #88 benchmark corpus",
        "confidence": "high",
        "contract_ids": [],
        "disposition": "new",
    }


def _envelope(payload: ChunkPayloadV2, *, findings: list[dict]) -> dict:
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
        "request_id": f"opbench-{payload.chunk_id}",
        "finish_reason": "stop",
        "response_received": True,
        "response_sha256": "9" * 64,
        "result": {
            "schema_id": "agent-review.chunk-response.v2",
            "schema_version": 2,
            "summary": "opbench-aiops-side-projection",
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
            "limitations": [],
        },
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


@dataclass(frozen=True)
class ProjectionResultV2:
    case_id: str
    repo: str
    pr_number: int
    base_sha: str
    head_sha: str
    actual_readiness: str
    expected_readiness: str
    readiness_matches: bool
    expected_finding_preserved: bool | None  # None for safe_counterexample cases (no finding expected)
    false_approval: bool  # MISMATCH that resolved `ready` despite ground truth expecting a block/manual_required
    manifest_hash: str
    finding_references: tuple[AiopsFindingReferenceV2, ...]


def run_projection_case_v2(
    *,
    case_id: str,
    target: str,
    relpath: str,
    expected: dict,
    repo: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    toolrepo_sha: str,
    diff_repo_root: Path,
) -> ProjectionResultV2:
    """Run one OP-BENCH case's REAL PR diff through the real v2 pipeline,
    bound to that PR's real identity. `diff_repo_root` is the git checkout
    (containing both `base_sha` and `head_sha` as real commits) `git diff`
    is run against -- never this repository's own working tree, and never
    a caller-supplied ref string (`acquire_diff_v2` itself rejects
    anything that is not a full 40-hex-character SHA)."""

    profile = load_target_profile_v2(FIXTURES_ROOT / target)
    policy = _policy_for_target(target)

    raw_diff_text = acquire_diff_v2(diff_repo_root, base_sha=base_sha, head_sha=head_sha)
    file_diffs = list(parse_unified_diff(raw_diff_text))

    outcome = assemble_manifest_from_diff_v2(
        file_diffs,
        profile=profile,
        grouping_policy=policy,
        repo=repo,
        pr_number=pr_number,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=_TESTED_MERGE_SHA_PLACEHOLDER,
        toolrepo_sha=toolrepo_sha,
        evidence_hash=_EVIDENCE_HASH,
        max_lines_per_chunk=200,
    )

    is_positive = expected["family"] == "semantic_positive"

    if outcome.state == "blocked_pipeline":
        return ProjectionResultV2(
            case_id=case_id,
            repo=repo,
            pr_number=pr_number,
            base_sha=base_sha,
            head_sha=head_sha,
            actual_readiness="blocked_pipeline",
            expected_readiness=expected["expected_readiness"],
            readiness_matches="blocked_pipeline" == expected["expected_readiness"],
            expected_finding_preserved=False if is_positive else None,
            false_approval=False,
            manifest_hash="",
            finding_references=(),
        )

    manifest = outcome.manifest
    assert manifest is not None
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=FIXTURES_ROOT / target)

    raw_finding_id = f"opbench-finding-{case_id}"
    findings_by_file: dict[str, list[dict]] = {}
    if is_positive:
        findings_by_file[relpath] = [
            _finding_dict(
                file_path=relpath,
                severity=expected["severity"],
                line_start=expected["line_start"],
                line_end=expected["line_end"],
                finding_id=raw_finding_id,
            )
        ]

    def _build_chunk_results():
        chunk_results = []
        for b in built:
            chunk_findings: list[dict] = []
            for file_path in b.payload.coverage.expected_files:
                chunk_findings.extend(findings_by_file.get(file_path, []))
            envelope = _envelope(b.payload, findings=chunk_findings)
            bound = bind_chunk_response_v2(envelope=envelope, payload=b.payload)
            chunk_results.append(parse_bound_chunk_response_v2(bound))
        return chunk_results

    results = _build_chunk_results()
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=head_sha)

    # blocked_code cases (a prior CONFIRMED disposition) need the same
    # two-pass round-trip the Lane 1 harness uses: first pass discovers the
    # engine-computed finding_id via provenance, second pass supplies a
    # matching prior_lifecycle CONFIRMED record.
    if expected.get("expected_readiness") == "blocked_code" and is_positive:
        target_key = (relpath, expected["line_start"], expected["line_end"], expected["severity"])
        confirmed_finding_ids = set()
        for record in synthesis.findings:
            for prov in synthesis.provenance.get(record.finding_id, ()):
                if prov.original_finding_id == raw_finding_id:
                    confirmed_finding_ids.add(record.finding_id)
        prior_lifecycle = [
            FindingLifecycleRecordV2(
                finding_id=fid,
                severity=expected["severity"],
                observed_at_head_sha=head_sha,
                disposition=FindingDispositionV2.CONFIRMED,
                actionable=True,
                justification="synthetic, illustrative confirmation -- issue #88 OP-BENCH projection",
                decided_by="aiops-projection",
                decided_at_head_sha=head_sha,
                evidence=[],
                superseded_by=None,
            )
            for fid in confirmed_finding_ids
        ]
        results = _build_chunk_results()
        synthesis = synthesize_chunk_results_v2(
            manifest=manifest, chunk_results=results, evaluated_head_sha=head_sha, prior_lifecycle=prior_lifecycle
        )
        _ = target_key  # documents intent; matching is via finding_id round-trip, not key re-derivation

    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=profile.policies, stale_reason_codes=frozenset()
    )

    finding_references: list[AiopsFindingReferenceV2] = []
    expected_finding_preserved: bool | None = False if is_positive else None
    false_approval = False
    for record in synthesis.findings:
        if record.disposition not in (FindingDispositionV2.NEW, FindingDispositionV2.CONFIRMED) or not record.actionable:
            continue
        is_our_finding = any(
            prov.original_finding_id == raw_finding_id for prov in synthesis.provenance.get(record.finding_id, ())
        )
        if not is_our_finding:
            continue
        finding_references.append(
            AiopsFindingReferenceV2(
                finding_id=record.finding_id,
                repo=repo,
                pr_number=pr_number,
                head_sha=head_sha,
                file_path=relpath,
                line_start=expected.get("line_start"),
                line_end=expected.get("line_end"),
                severity=record.severity,
            )
        )
        expected_finding_preserved = True

    # A false approval is a MISMATCH that resolves `ready` -- never a
    # blanket "any semantic_positive case reaching ready is suspicious".
    # P3-only findings are legitimately excluded from the blocking set
    # (readiness_decision_v2's own tested precedent, see
    # agentescala-contract-p3-finding-still-ready and the correction this
    # module's own driver caught for interleitos-domain-consent-scope-omission);
    # a positive case whose OWN ground truth expects `ready` is not a false
    # approval merely for reaching it.
    if is_positive and decision.state.value == "ready" and expected["expected_readiness"] != "ready":
        false_approval = True

    return ProjectionResultV2(
        case_id=case_id,
        repo=repo,
        pr_number=pr_number,
        base_sha=base_sha,
        head_sha=head_sha,
        actual_readiness=decision.state.value,
        expected_readiness=expected["expected_readiness"],
        readiness_matches=decision.state.value == expected["expected_readiness"],
        expected_finding_preserved=expected_finding_preserved,
        false_approval=false_approval,
        manifest_hash=manifest.identity.manifest_hash,
        finding_references=tuple(finding_references),
    )
