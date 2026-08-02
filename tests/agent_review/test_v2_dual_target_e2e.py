"""Dual-target conformance E2E for AgentReview v2 (issue #86).

Proves the v2 engine is genuinely generic across two independent targets --
"AgentEscala" and "InterLeitos" -- using real, on-disk `TargetProfileV2`
fixtures (`tests/agent_review/fixtures/v2/<target>/.aiops/target-profile.v2.yaml`,
loaded through the real `profile_loader_v2.load_target_profile_v2`, never
hand-constructed) and real on-disk artifact/contract files with real
computed sha256 hashes, driven through the full pipeline: profile load ->
semantic grouping -> manifest assembly -> payload building -> synthetic
provider response -> bind -> parse -> synthesis -> readiness decision ->
readiness emission -> PayloadSet emission.

Deliberately synthetic, per this slice's own scoping decision (see the
fixtures' own disclaiming comments): no real AgentEscala/InterLeitos product
logic, patient/institution/clinical data, or PHI of any kind is represented
anywhere in this suite. No git-based diff acquisition is exercised here --
`ParsedFileDiffV2`/`ParsedHunkV2` are constructed directly, mirroring #129's
own test pattern, since `assemble_manifest_from_diff_v2` accepts pre-parsed
diffs directly and this suite's job is to prove engine genericity, not to
re-prove diff parsing (already covered by #103's own suite). No new
redaction/PHI-detection infrastructure is built -- the DLP/PHI concern is
represented via the existing `manual_required`/`model_uncertainty` readiness
path, never a bespoke detector.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review.consumer_v2 import bind_chunk_response_v2
from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    DispositionEvidenceV2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    PullRequestStateV2,
    ReadinessReasonV2,
    ReadinessStateV2,
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    SemanticGroupV2,
    compute_response_sha256_v2,
    compute_run_id,
)
from app.agent_review.diff_acquisition_v2 import ParsedFileDiffV2, ParsedHunkV2
from app.agent_review.parser_v2 import parse_bound_chunk_response_v2
from app.agent_review.payload_builder_v2 import build_chunk_payloads_from_profile_v2
from app.agent_review.payload_set_emission_v2 import (
    PayloadSetBindingError,
    bind_payload_set_to_payloads_v2,
    emit_payload_set_v2,
)
from app.agent_review.payload_set_v2 import (
    PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2,
    PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2,
)
from app.agent_review.profile_loader_v2 import load_target_profile_v2
from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
from app.agent_review.review_readiness_emission_v2 import emit_review_readiness_v2
from app.agent_review.run_assembly_v2 import (
    RUN_ASSEMBLY_REQUIRED_PATH_UNREPRESENTABLE_REASON_V2,
    assemble_manifest_from_diff_v2,
)
from app.agent_review.semantic_grouping_policy_v2 import (
    SEMANTIC_GROUPING_UNKNOWN_GROUP_REASON_V2,
    SemanticGroupingError,
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    bind_semantic_grouping_policy_to_target_profile_v2,
    compute_semantic_grouping_policy_sha256_v2,
)
from app.agent_review.synthesis_v2 import synthesize_chunk_results_v2

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "v2"

_HEAD_SHA = "2" * 40
_BASE_SHA = "1" * 40
_TESTED_MERGE_SHA = "3" * 40
_TOOLREPO_SHA = "4" * 40
_EVIDENCE_HASH = "d" * 64


# -- shared fixture-building helpers ------------------------------------------


def _hunk(*, old_start: int, old_lines: int, new_start: int, new_lines: int, seed: str) -> ParsedHunkV2:
    return ParsedHunkV2(
        old_start=old_start,
        old_lines=old_lines,
        new_start=new_start,
        new_lines=new_lines,
        diff_sha256=hashlib.sha256(seed.encode()).hexdigest(),
        diff_chars=40,
    )


def _file_diff(*, path: str, hunks: tuple[ParsedHunkV2, ...], is_binary: bool = False) -> ParsedFileDiffV2:
    return ParsedFileDiffV2(
        old_path=path,
        new_path=path,
        change_type="modified",
        is_binary=is_binary,
        is_submodule=False,
        similarity_index=None,
        old_no_newline_at_eof=False,
        new_no_newline_at_eof=False,
        hunks=hunks,
        truncated=False,
    )


def _rule(*, rule_id: str, semantic_group: SemanticGroupV2, path_patterns: list[str]) -> SemanticGroupingRuleV2:
    return SemanticGroupingRuleV2(
        rule_id=rule_id,
        semantic_group=semantic_group,
        path_patterns=path_patterns,
        contract_ids=[],
        artifact_ids=[],
        priority=0,
    )


def _policy(*, rules: list[SemanticGroupingRuleV2]) -> SemanticGroupingPolicyV2:
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


def _agent_escala_policy() -> SemanticGroupingPolicyV2:
    return _policy(
        rules=[
            _rule(
                rule_id="backend",
                semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
                path_patterns=["backend/scheduling/*.py"],
            ),
            _rule(
                rule_id="tests",
                semantic_group=SemanticGroupV2.TESTS,
                path_patterns=["tests/scheduling/*.py"],
            ),
        ]
    )


def _interleitos_policy() -> SemanticGroupingPolicyV2:
    return _policy(
        rules=[
            _rule(
                rule_id="backend",
                semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
                path_patterns=["backend/tenancy/*.py"],
            ),
            _rule(
                rule_id="api",
                semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT,
                path_patterns=["backend/api/*.py"],
            ),
        ]
    )


def _agent_escala_diffs() -> list[ParsedFileDiffV2]:
    return [
        _file_diff(
            path="backend/scheduling/shift_rules.py",
            hunks=(_hunk(old_start=10, old_lines=6, new_start=10, new_lines=8, seed="shift-rules"),),
        ),
        _file_diff(
            path="tests/scheduling/test_shift_rules.py",
            hunks=(_hunk(old_start=1, old_lines=3, new_start=1, new_lines=6, seed="shift-rules-test"),),
        ),
    ]


def _interleitos_diffs() -> list[ParsedFileDiffV2]:
    return [
        _file_diff(
            path="backend/tenancy/access_scope.py",
            hunks=(_hunk(old_start=8, old_lines=6, new_start=8, new_lines=8, seed="access-scope"),),
        ),
        _file_diff(
            path="backend/api/schema_contract.py",
            hunks=(_hunk(old_start=1, old_lines=3, new_start=1, new_lines=6, seed="schema-contract"),),
        ),
    ]


def _load_profile(target: str):
    return load_target_profile_v2(FIXTURES_ROOT / target)


def _assemble(*, target: str, profile, policy, file_diffs, pr_number: int):
    outcome = assemble_manifest_from_diff_v2(
        file_diffs,
        profile=profile,
        grouping_policy=policy,
        repo=profile.identity.repo,
        pr_number=pr_number,
        base_sha=_BASE_SHA,
        head_sha=_HEAD_SHA,
        tested_merge_sha=_TESTED_MERGE_SHA,
        toolrepo_sha=_TOOLREPO_SHA,
        evidence_hash=_EVIDENCE_HASH,
        max_lines_per_chunk=200,
    )
    assert outcome.state == "assembled", (target, outcome.blocked_reason)
    assert outcome.manifest is not None
    return outcome.manifest


def _success_envelope(payload: ChunkPayloadV2, *, findings: list[dict], limitations: list[str] | None = None) -> dict:
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
        "request_id": f"req-{payload.chunk_id}",
        "finish_reason": "stop",
        "response_received": True,
        "response_sha256": "9" * 64,
        "result": {
            "schema_id": "agent-review.chunk-response.v2",
            "schema_version": 2,
            "summary": "review-complete",
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
            "limitations": limitations or [],
        },
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def _finding(*, file_path: str, finding_id: str = "finding-001", line_start: int = 12, line_end: int = 13) -> dict:
    return {
        "finding_id": finding_id,
        "severity": "P2",
        "title": "synthetic-illustrative-finding",
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "evidence": "synthetic, illustrative only",
        "impact": "synthetic, illustrative only",
        "confidence": "high",
        "contract_ids": [],
        "disposition": "new",
    }


def _run_chunk(payload: ChunkPayloadV2, *, findings: list[dict] | None = None, limitations: list[str] | None = None):
    envelope = _success_envelope(payload, findings=findings or [], limitations=limitations)
    bound = bind_chunk_response_v2(envelope=envelope, payload=payload)
    return parse_bound_chunk_response_v2(bound)


def _green_check(head_sha: str) -> RequiredCheckResultV2:
    return RequiredCheckResultV2(
        check_name="pytest",
        required=True,
        deterministic=True,
        conclusion=RequiredCheckConclusionV2.SUCCESS,
        head_sha=head_sha,
    )


# -- anti-branching proof ------------------------------------------------------


def test_engine_never_branches_on_target_name():
    """The v2 engine surface must never hardcode a target identity -- every
    target-specific decision comes from the profile/policy it is handed,
    never from a string literal compared against a repo/target name.

    A prose mention of a consuming issue tracker in a comment (e.g.
    ``telemetry.py``/``schemas.py`` documenting which real-world issue
    #111's fix closes) is not a branch and is deliberately not flagged --
    only a quoted STRING LITERAL naming a target, the shape an actual
    ``if repo == "..."`` branch would take, counts as a violation here.
    """

    literal_pattern = re.compile(r"""['"][^'"\n]*(agentescala|interleitos)[^'"\n]*['"]""", re.IGNORECASE)
    for path in (Path(__file__).parent.parent.parent / "app" / "agent_review").rglob("*.py"):
        for match in literal_pattern.finditer(path.read_text(encoding="utf-8")):
            pytest.fail(f"{path}: engine must not branch on target-name string literal {match.group(0)!r}")


# -- two profiles genuinely load, differ, and drive different grouping -------


def test_two_real_profiles_load_and_differ():
    escala = _load_profile("agent_escala")
    leitos = _load_profile("interleitos")

    assert escala.identity.repo == "mglpsw/AgentEscala"
    assert leitos.identity.repo == "mglpsw/interleitos"
    assert escala.policies.coverage_failure_state == "blocked_pipeline"
    assert leitos.policies.coverage_failure_state == "manual_required"
    assert set(escala.policies.allowed_semantic_groups) != set(leitos.policies.allowed_semantic_groups)


def test_two_targets_same_engine_different_groups_and_payloads():
    escala_profile = _load_profile("agent_escala")
    leitos_profile = _load_profile("interleitos")

    escala_manifest = _assemble(
        target="agent_escala",
        profile=escala_profile,
        policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(),
        pr_number=101,
    )
    leitos_manifest = _assemble(
        target="interleitos",
        profile=leitos_profile,
        policy=_interleitos_policy(),
        file_diffs=_interleitos_diffs(),
        pr_number=202,
    )

    escala_groups = {chunk.semantic_group for chunk in escala_manifest.chunks}
    leitos_groups = {chunk.semantic_group for chunk in leitos_manifest.chunks}
    assert escala_groups == {SemanticGroupV2.PRIMARY_BACKEND_LOGIC.value, SemanticGroupV2.TESTS.value}
    assert leitos_groups == {SemanticGroupV2.PRIMARY_BACKEND_LOGIC.value, SemanticGroupV2.API_SCHEMA_CONTRACT.value}

    escala_built = build_chunk_payloads_from_profile_v2(
        escala_manifest, profile=escala_profile, repo_root=FIXTURES_ROOT / "agent_escala"
    )
    leitos_built = build_chunk_payloads_from_profile_v2(
        leitos_manifest, profile=leitos_profile, repo_root=FIXTURES_ROOT / "interleitos"
    )
    escala_payload_hashes = {b.payload.payload_sha256 for b in escala_built}
    leitos_payload_hashes = {b.payload.payload_sha256 for b in leitos_built}
    assert escala_payload_hashes.isdisjoint(leitos_payload_hashes)

    escala_contract_ids = {ref.contract_id for b in escala_built for ref in b.payload.contract_references}
    leitos_contract_ids = {ref.contract_id for b in leitos_built for ref in b.payload.contract_references}
    assert escala_contract_ids == {"scheduling-rules"}
    assert leitos_contract_ids == {"clinical-domain-contract"}


# -- cross-target rejection ---------------------------------------------------


def test_cross_target_policy_binding_is_rejected():
    """AgentEscala's own grouping policy names ``docs_changelog``... no --
    it names ``tests``/``primary_backend_logic`` only, both allowed by
    InterLeitos too, so bind a policy that genuinely diverges: AgentEscala's
    policy never mentions ``api_schema_contract`` (not in its own allowed
    set), but InterLeitos's own profile requires it be resolvable -- prove
    the reverse divergence instead: InterLeitos's policy uses
    ``api_schema_contract``, which AgentEscala's profile does not allow."""

    leitos_policy = _interleitos_policy()
    escala_profile = _load_profile("agent_escala")

    with pytest.raises(SemanticGroupingError) as excinfo:
        bind_semantic_grouping_policy_to_target_profile_v2(leitos_policy, escala_profile)
    assert excinfo.value.reason_code == SEMANTIC_GROUPING_UNKNOWN_GROUP_REASON_V2

    # the same policy binds cleanly against its own, real target
    leitos_profile = _load_profile("interleitos")
    bind_semantic_grouping_policy_to_target_profile_v2(leitos_policy, leitos_profile)


def test_cross_target_payload_set_binding_is_rejected():
    escala_profile = _load_profile("agent_escala")
    leitos_profile = _load_profile("interleitos")

    escala_manifest = _assemble(
        target="agent_escala",
        profile=escala_profile,
        policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(),
        pr_number=101,
    )
    leitos_manifest = _assemble(
        target="interleitos",
        profile=leitos_profile,
        policy=_interleitos_policy(),
        file_diffs=_interleitos_diffs(),
        pr_number=202,
    )

    escala_built = build_chunk_payloads_from_profile_v2(
        escala_manifest, profile=escala_profile, repo_root=FIXTURES_ROOT / "agent_escala"
    )
    leitos_built = build_chunk_payloads_from_profile_v2(
        leitos_manifest, profile=leitos_profile, repo_root=FIXTURES_ROOT / "interleitos"
    )

    # a PayloadSet legitimately emitted for AgentEscala's own manifest+payloads
    escala_payload_set = emit_payload_set_v2(escala_manifest, [b.payload for b in escala_built])
    assert escala_payload_set.manifest_hash == escala_manifest.identity.manifest_hash

    # the same payload set cross-checked against InterLeitos's manifest/payloads must fail closed
    with pytest.raises(PayloadSetBindingError) as excinfo_1:
        emit_payload_set_v2(leitos_manifest, [b.payload for b in escala_built])
    assert excinfo_1.value.reason_code == PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2

    with pytest.raises(PayloadSetBindingError) as excinfo_2:
        bind_payload_set_to_payloads_v2(escala_payload_set, [b.payload for b in leitos_built], leitos_manifest)
    assert excinfo_2.value.reason_code == PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2


# -- cross-cutting regressions the issue names explicitly ---------------------


def test_identical_inputs_produce_identical_bytes_and_hashes():
    """mesmas entradas -> mesmos bytes e hashes: reassembling the identical
    profile+policy+diffs twice must yield byte-identical manifest_hash,
    run_id, and payload_sha256 values -- never incidentally
    nondeterministic (e.g. via dict/set iteration order)."""

    profile = _load_profile("agent_escala")

    manifest_a = _assemble(
        target="agent_escala", profile=profile, policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(), pr_number=101,
    )
    manifest_b = _assemble(
        target="agent_escala", profile=profile, policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(), pr_number=101,
    )
    assert manifest_a.identity.manifest_hash == manifest_b.identity.manifest_hash
    assert manifest_a.run_id == manifest_b.run_id

    built_a = build_chunk_payloads_from_profile_v2(manifest_a, profile=profile, repo_root=FIXTURES_ROOT / "agent_escala")
    built_b = build_chunk_payloads_from_profile_v2(manifest_b, profile=profile, repo_root=FIXTURES_ROOT / "agent_escala")
    assert sorted(b.payload.payload_sha256 for b in built_a) == sorted(b.payload.payload_sha256 for b in built_b)


def test_chunk_result_order_does_not_change_synthesis_outcome():
    """ordem de respostas/retries não muda resultado semântico: feeding the
    same set of chunk results in reversed order must produce the same
    coverage/findings/readiness outcome."""

    profile = _load_profile("agent_escala")
    manifest = _assemble(
        target="agent_escala", profile=profile, policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(), pr_number=101,
    )
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=FIXTURES_ROOT / "agent_escala")
    results = [_run_chunk(b.payload) for b in built]

    forward = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=_HEAD_SHA)
    backward = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=list(reversed(results)), evaluated_head_sha=_HEAD_SHA
    )
    assert forward.coverage_report.coverage_report_sha256 == backward.coverage_report.coverage_report_sha256
    assert forward.findings == backward.findings

    decision_forward = compute_readiness_decision_v2(synthesis=forward, manifest=manifest, policies=profile.policies)
    decision_backward = compute_readiness_decision_v2(synthesis=backward, manifest=manifest, policies=profile.policies)
    assert decision_forward.state is decision_backward.state is ReadinessStateV2.READY


def test_evidence_hash_change_changes_run_id():
    """profile/policy/manifest/evidence alterado muda run_id -- evidence_hash
    is part of RunIdentityV2's own hash preimage (E1/#128); changing only it,
    with everything else held fixed, must change run_id and manifest_hash."""

    profile = _load_profile("agent_escala")
    policy = _agent_escala_policy()
    diffs = _agent_escala_diffs()

    outcome_a = assemble_manifest_from_diff_v2(
        diffs, profile=profile, grouping_policy=policy, repo=profile.identity.repo, pr_number=101,
        base_sha=_BASE_SHA, head_sha=_HEAD_SHA, tested_merge_sha=_TESTED_MERGE_SHA,
        toolrepo_sha=_TOOLREPO_SHA, evidence_hash=_EVIDENCE_HASH, max_lines_per_chunk=200,
    )
    outcome_b = assemble_manifest_from_diff_v2(
        diffs, profile=profile, grouping_policy=policy, repo=profile.identity.repo, pr_number=101,
        base_sha=_BASE_SHA, head_sha=_HEAD_SHA, tested_merge_sha=_TESTED_MERGE_SHA,
        toolrepo_sha=_TOOLREPO_SHA, evidence_hash="e" * 64, max_lines_per_chunk=200,
    )
    assert outcome_a.manifest.identity.manifest_hash == outcome_b.manifest.identity.manifest_hash
    assert outcome_a.manifest.run_id != outcome_b.manifest.run_id


# -- 5 readiness states across the two targets --------------------------------


def test_agent_escala_ready():
    profile = _load_profile("agent_escala")
    manifest = _assemble(
        target="agent_escala",
        profile=profile,
        policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(),
        pr_number=101,
    )
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=FIXTURES_ROOT / "agent_escala")
    results = [_run_chunk(b.payload) for b in built]

    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=_HEAD_SHA)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=profile.policies)
    assert decision.state is ReadinessStateV2.READY

    readiness = emit_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(_HEAD_SHA)],
    )
    assert readiness.state == ReadinessStateV2.READY.value


def test_agent_escala_blocked_code_on_confirmed_finding():
    profile = _load_profile("agent_escala")
    manifest = _assemble(
        target="agent_escala",
        profile=profile,
        policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(),
        pr_number=101,
    )
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=FIXTURES_ROOT / "agent_escala")
    backend_built = next(b for b in built if b.payload.semantic_group == SemanticGroupV2.PRIMARY_BACKEND_LOGIC.value)
    other_built = [b for b in built if b is not backend_built]

    findings = [_finding(file_path="backend/scheduling/shift_rules.py")]
    results = [_run_chunk(backend_built.payload, findings=findings)] + [_run_chunk(b.payload) for b in other_built]

    # round 1: the finding is fresh ("new") -- establishes its real, engine-computed finding_id
    synthesis_1 = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=_HEAD_SHA)
    new_record = next(f for f in synthesis_1.findings if f.disposition is FindingDispositionV2.NEW)

    # round 2: a prior human decision confirmed that exact finding_id -- proves confirmation persists
    prior = FindingLifecycleRecordV2(
        finding_id=new_record.finding_id,
        severity=new_record.severity,
        observed_at_head_sha=_HEAD_SHA,
        disposition=FindingDispositionV2.CONFIRMED,
        actionable=True,
        justification="synthetic, illustrative confirmation for the conformance suite",
        decided_by="synthetic-reviewer",
        decided_at_head_sha=_HEAD_SHA,
        evidence=[DispositionEvidenceV2(kind="test", reference="synthetic-repro-test", head_sha=_HEAD_SHA)],
        superseded_by=None,
    )
    synthesis_2 = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=results, evaluated_head_sha=_HEAD_SHA, prior_lifecycle=[prior]
    )
    confirmed_record = next(f for f in synthesis_2.findings if f.finding_id == new_record.finding_id)
    assert confirmed_record.disposition is FindingDispositionV2.CONFIRMED

    decision = compute_readiness_decision_v2(synthesis=synthesis_2, manifest=manifest, policies=profile.policies)
    assert decision.state is ReadinessStateV2.BLOCKED_CODE
    assert ReadinessReasonV2.CONFIRMED_CODE_FINDING in decision.reason_codes

    readiness = emit_review_readiness_v2(
        decision=decision,
        findings=synthesis_2.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(_HEAD_SHA)],
    )
    assert readiness.state == ReadinessStateV2.BLOCKED_CODE.value


def test_agent_escala_blocked_pipeline_on_incomplete_coverage():
    """Path fragmentado (aqui: uma chunk nunca respondeu) produz EXATAMENTE
    ``policies.coverage_failure_state`` -- para AgentEscala,
    ``blocked_pipeline`` -- nunca ``ready``."""

    profile = _load_profile("agent_escala")
    assert profile.policies.coverage_failure_state == "blocked_pipeline"
    manifest = _assemble(
        target="agent_escala",
        profile=profile,
        policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(),
        pr_number=101,
    )
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=FIXTURES_ROOT / "agent_escala")
    assert len(built) >= 2

    # deliberately omit one chunk's result entirely -- simulates a real
    # provider/transport failure for that chunk, never fabricated coverage
    results = [_run_chunk(built[0].payload)]

    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=_HEAD_SHA)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=profile.policies)
    assert decision.state is ReadinessStateV2.BLOCKED_PIPELINE
    assert decision.state.value == profile.policies.coverage_failure_state
    assert ReadinessReasonV2.COVERAGE_FAILURE in decision.reason_codes


def test_interleitos_manual_required_on_model_uncertainty():
    """Stands in for the DLP/PHI-suspicion concern via the EXISTING
    ``manual_required``/``model_uncertainty`` readiness path -- no new
    detection infrastructure is built for this suite."""

    profile = _load_profile("interleitos")
    assert profile.policies.coverage_failure_state == "manual_required"
    manifest = _assemble(
        target="interleitos",
        profile=profile,
        policy=_interleitos_policy(),
        file_diffs=_interleitos_diffs(),
        pr_number=202,
    )
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=FIXTURES_ROOT / "interleitos")
    results = [
        _run_chunk(built[0].payload, limitations=["model_uncertainty"]),
        *[_run_chunk(b.payload) for b in built[1:]],
    ]

    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=_HEAD_SHA)
    assert synthesis.limitations == ("model_uncertainty",)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=profile.policies)
    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.MODEL_UNCERTAINTY in decision.reason_codes

    readiness = emit_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(_HEAD_SHA)],
    )
    assert readiness.state == ReadinessStateV2.MANUAL_REQUIRED.value


def test_interleitos_dlp_block_prior_to_any_synthetic_response():
    """InterLeitos's own required minimum case: 'um bloqueio DLP anterior a
    qualquer response sintética'. Per this suite's own scoping decision, no
    new PHI/DLP detector is built -- instead, a required must-review path
    the engine cannot represent AT ALL (binary content) is refused at
    ASSEMBLY time, before any ChunkPayloadV2 is built and before any
    synthetic provider response is ever constructed. This stands in for
    "a suspected-PHI artifact must never even reach a provider" using the
    engine's own real, existing fail-closed gate, not an invented one."""

    profile = _load_profile("interleitos")
    binary_diffs = [
        _file_diff(path="backend/tenancy/access_scope.py", hunks=(), is_binary=True),
        *_interleitos_diffs()[1:],
    ]

    outcome = assemble_manifest_from_diff_v2(
        binary_diffs,
        profile=profile,
        grouping_policy=_interleitos_policy(),
        repo=profile.identity.repo,
        pr_number=202,
        base_sha=_BASE_SHA,
        head_sha=_HEAD_SHA,
        tested_merge_sha=_TESTED_MERGE_SHA,
        toolrepo_sha=_TOOLREPO_SHA,
        evidence_hash=_EVIDENCE_HASH,
        max_lines_per_chunk=200,
    )

    # blocked before a manifest, any chunk payload, or any provider response
    # ever existed -- there is nothing downstream left to build or send.
    assert outcome.state == "blocked_pipeline"
    assert outcome.manifest is None
    assert outcome.blocked_reason.reason_code == RUN_ASSEMBLY_REQUIRED_PATH_UNREPRESENTABLE_REASON_V2
    assert "backend/tenancy/access_scope.py" in outcome.blocked_reason.affected_paths


def test_interleitos_stale_on_identity_divergence():
    profile = _load_profile("interleitos")
    manifest = _assemble(
        target="interleitos",
        profile=profile,
        policy=_interleitos_policy(),
        file_diffs=_interleitos_diffs(),
        pr_number=202,
    )
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=FIXTURES_ROOT / "interleitos")
    results = [_run_chunk(b.payload) for b in built]
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=_HEAD_SHA)

    decision = compute_readiness_decision_v2(
        synthesis=synthesis,
        manifest=manifest,
        policies=profile.policies,
        stale_reason_codes=frozenset({ReadinessReasonV2.HEAD_MISMATCH}),
    )
    assert decision.state is ReadinessStateV2.STALE

    new_head_sha = "5" * 40
    current_identity = manifest.identity.model_copy(update={"head_sha": new_head_sha})
    readiness = emit_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=current_identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(_HEAD_SHA)],
    )
    assert readiness.state == ReadinessStateV2.STALE.value
    assert compute_run_id(readiness.identity) == readiness.run_id


# -- conformance matrix artifact -----------------------------------------------

CONFORMANCE_SCHEMA_ID_V2 = "agent-review.v2-conformance-matrix"
_VERIFY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify-agent-review-v2-conformance.py"


def _conformance_entry(*, target_id: str, profile, manifest, payload_set, readiness, decision) -> dict:
    canonical_output = {
        "manifest_hash": manifest.identity.manifest_hash,
        "payload_set_sha256": payload_set.payload_set_sha256,
        "readiness_state": readiness.state.value,
    }
    canonical_digest = hashlib.sha256(
        json.dumps(
            canonical_output, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    return {
        "target_id": target_id,
        "repo": profile.identity.repo,
        "profile_hash": manifest.identity.profile_hash,
        "policy_hash": manifest.identity.policy_hash,
        "manifest_hash": manifest.identity.manifest_hash,
        "evidence_hash": manifest.identity.evidence_hash,
        "expected_head_sha": manifest.identity.head_sha,
        "evaluated_head_sha": readiness.evaluated_head_sha,
        "expected_chunks": len(manifest.chunks),
        "processed_chunks": len(payload_set.payloads),
        "expected_fragments": len(manifest.fragments),
        "processed_fragments": sum(len(chunk.fragment_ids) for chunk in manifest.chunks),
        "coverage_status": decision.coverage.status.value,
        "must_review_coverage_complete": not decision.coverage.missing_must_review_files,
        "binding_result": "ok",
        "readiness_state": readiness.state.value,
        "reason_codes": [code.value for code in readiness.reason_codes],
        "findings": [
            {"finding_id": f.finding_id, "severity": f.severity.value, "disposition": f.disposition.value}
            for f in readiness.findings
        ],
        "evidence_no_network": True,
        "evidence_no_provider": True,
        "evidence_no_github_write": True,
        "evidence_no_ct102": True,
        "canonical_output_digest": canonical_digest,
    }


def _build_full_run(*, target_id: str, profile, policy, file_diffs, pr_number: int, repo_root: Path, limitations=None):
    manifest = _assemble(target=target_id, profile=profile, policy=policy, file_diffs=file_diffs, pr_number=pr_number)
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=repo_root)
    results = [
        _run_chunk(built[0].payload, limitations=limitations),
        *[_run_chunk(b.payload) for b in built[1:]],
    ]
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=_HEAD_SHA)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=profile.policies)
    readiness = emit_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(_HEAD_SHA)],
    )
    payload_set = emit_payload_set_v2(manifest, [b.payload for b in built])
    return manifest, payload_set, decision, readiness


def _build_conformance_matrix() -> dict:
    escala_profile = _load_profile("agent_escala")
    escala_manifest, escala_payload_set, escala_decision, escala_readiness = _build_full_run(
        target_id="agent_escala",
        profile=escala_profile,
        policy=_agent_escala_policy(),
        file_diffs=_agent_escala_diffs(),
        pr_number=101,
        repo_root=FIXTURES_ROOT / "agent_escala",
    )
    assert escala_decision.state is ReadinessStateV2.READY

    leitos_profile = _load_profile("interleitos")
    leitos_manifest, leitos_payload_set, leitos_decision, leitos_readiness = _build_full_run(
        target_id="interleitos",
        profile=leitos_profile,
        policy=_interleitos_policy(),
        file_diffs=_interleitos_diffs(),
        pr_number=202,
        repo_root=FIXTURES_ROOT / "interleitos",
        limitations=["model_uncertainty"],
    )
    assert leitos_decision.state is ReadinessStateV2.MANUAL_REQUIRED

    return {
        "schema_id": CONFORMANCE_SCHEMA_ID_V2,
        "targets": [
            _conformance_entry(
                target_id="agent_escala",
                profile=escala_profile,
                manifest=escala_manifest,
                payload_set=escala_payload_set,
                readiness=escala_readiness,
                decision=escala_decision,
            ),
            _conformance_entry(
                target_id="interleitos",
                profile=leitos_profile,
                manifest=leitos_manifest,
                payload_set=leitos_payload_set,
                readiness=leitos_readiness,
                decision=leitos_decision,
            ),
        ],
    }


def test_conformance_matrix_is_complete_and_deterministic(tmp_path):
    matrix_a = _build_conformance_matrix()
    matrix_b = _build_conformance_matrix()
    assert matrix_a == matrix_b

    bytes_a = json.dumps(
        matrix_a, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    bytes_b = json.dumps(
        matrix_b, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert bytes_a == bytes_b

    target_ids = {entry["target_id"] for entry in matrix_a["targets"]}
    assert target_ids == {"agent_escala", "interleitos"}

    output_path = tmp_path / "agent-review-v2-conformance.json"
    output_path.write_text(
        json.dumps(matrix_a, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(_VERIFY_SCRIPT), "--conformance", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok:" in result.stdout


def test_conformance_verify_script_fails_closed_on_missing_field(tmp_path):
    matrix = _build_conformance_matrix()
    del matrix["targets"][0]["evidence_hash"]
    output_path = tmp_path / "broken-conformance.json"
    output_path.write_text(json.dumps(matrix), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(_VERIFY_SCRIPT), "--conformance", str(output_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "conformance_target_field_missing" in result.stderr


def test_conformance_verify_script_fails_closed_on_missing_evidence_flag():
    matrix = _build_conformance_matrix()
    matrix["targets"][0]["evidence_no_network"] = False

    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_agent_review_v2_conformance", _VERIFY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with pytest.raises(module.ConformanceVerificationError) as excinfo:
        module.verify_conformance_matrix(matrix)
    assert excinfo.value.reason_code == "conformance_network_or_provider_evidence_missing"


def _load_verify_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("verify_agent_review_v2_conformance", _VERIFY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conformance_verify_script_fails_closed_on_invalid_coverage_status():
    """A bogus coverage_status (not a real CoverageStateV2 value) must be
    rejected, not silently accepted just because the field is present."""

    module = _load_verify_module()
    matrix = _build_conformance_matrix()
    matrix["targets"][0]["coverage_status"] = "totally-bogus-not-a-real-enum-value"

    with pytest.raises(module.ConformanceVerificationError) as excinfo:
        module.verify_conformance_matrix(matrix)
    assert excinfo.value.reason_code == "conformance_target_field_invalid"


def test_conformance_verify_script_fails_closed_on_non_string_target_id():
    """A type-confused target_id (e.g. a list, unhashable) must fail closed
    with a stable reason code -- never an unhandled TypeError/traceback."""

    module = _load_verify_module()
    matrix = _build_conformance_matrix()
    matrix["targets"][0]["target_id"] = ["not", "a", "string"]

    with pytest.raises(module.ConformanceVerificationError) as excinfo:
        module.verify_conformance_matrix(matrix)
    assert excinfo.value.reason_code == "conformance_target_field_invalid"


def test_conformance_verify_script_fails_closed_on_local_path_leak():
    """Proves the sanitization check genuinely catches a local filesystem
    path (which `redact_content` alone does NOT redact -- only
    `sanitize_artifact_value` does), not merely a secret-shaped value."""

    module = _load_verify_module()
    matrix = _build_conformance_matrix()
    matrix["targets"][0]["repo"] = "/home/mglpsw/.ssh/id_rsa leaked into this field"

    with pytest.raises(module.ConformanceVerificationError) as excinfo:
        module.verify_conformance_matrix(matrix)
    assert excinfo.value.reason_code == "conformance_sanitization_leak_detected"
