"""`#200-F` §12/§13 -- composition invariants, in process.

The one-synthesis invariant is asserted by **object identity**, not equality.
An equal-but-distinct value is exactly what a second aggregation produces, so
equality would pass through the defect it is meant to catch. `#276` learned a
second lesson here too: its identity assertion was *vacuous*, because both
sides were empty tuples and CPython interns those. Every identity assertion
below therefore runs with at least one real finding present.
"""

from __future__ import annotations

import json

import pytest

from app.agent_review.contracts_v2 import (
    ReadinessStateV2,
    SemanticGroupV2,
    TargetProfileV2,
    compute_response_sha256_v2,
)
from app.agent_review.diff_acquisition_v2 import parse_unified_diff
from app.agent_review.operational_ingress_v2 import validate_public_inputs_v2
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2
from app.agent_review.operational_run_v2 import (
    OperationalRunError,
    execute_operational_run_v2,
)
from app.agent_review.payload_builder_v2 import build_chunk_payloads_v2
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

_BASE_SHA_V2 = "1" * 40
_HEAD_SHA_V2 = "2" * 40
_TESTED_MERGE_SHA_V2 = "3" * 40
_DIGEST_V2 = "4" * 64

_REVIEWABLE_DIFF_V2 = """diff --git a/app/service.py b/app/service.py
index 1111111..2222222 100644
--- a/app/service.py
+++ b/app/service.py
@@ -1,3 +1,4 @@
 import os
+VALUE = 2
 def handler():
     return 1
"""

_PURE_RENAME_DIFF_V2 = """diff --git a/app/old_name.py b/app/new_name.py
similarity index 100%
rename from app/old_name.py
rename to app/new_name.py
"""

_BINARY_DIFF_V2 = """diff --git a/assets/logo.png b/assets/logo.png
index 3333333..4444444 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""


def _profile_v2(*, must_review_paths: tuple[str, ...] = ()) -> TargetProfileV2:
    return TargetProfileV2.model_validate(
        {
            "schema_id": "agent-review.target-profile.v2",
            "schema_version": 2,
            "source": "repo-profile",
            "identity": {"repo": "mglpsw/aiops-orchestrator", "default_branch": "master"},
            "artifacts": [
                {
                    "artifact_id": "full-diff",
                    "path": "artifacts/full.diff",
                    "kind": "diff",
                    "required": True,
                    "max_bytes": 1000000,
                }
            ],
            "budgets": {
                "max_chunks": 32,
                "total_prompt_chars": 250000,
                "max_chars_per_chunk": 24000,
                "max_files_per_chunk": 50,
                "max_contracts_per_chunk": 50,
            },
            "must_review": {
                "paths": list(must_review_paths),
                "patterns": [],
                "artifact_ids": [],
                "minimum_coverage": "complete",
            },
            "policies": {
                "network_policy": "forbidden",
                "fail_closed": True,
                "redaction_required": True,
                "allow_partial_coverage": False,
                "required_checks": ["pytest"],
                "allowed_semantic_groups": ["primary_backend_logic", "tests"],
                "coverage_failure_state": "blocked_pipeline",
                "model_uncertainty_state": "manual_required",
            },
            "contracts": [],
            "limitations": [],
        }
    )


def _grouping_policy_v2() -> SemanticGroupingPolicyV2:
    rule = SemanticGroupingRuleV2(
        rule_id="all",
        semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
        path_patterns=["*"],
        contract_ids=[],
        artifact_ids=[],
        priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": [rule],
        "fallback_group": None,
    }
    return SemanticGroupingPolicyV2(
        **material,
        policy_sha256=compute_semantic_grouping_policy_sha256_v2(
            {**material, "rules": [rule.model_dump(mode="json")]}
        ),
    )


def _inputs_v2():
    return validate_public_inputs_v2(
        {
            "repo": "mglpsw/aiops-orchestrator",
            "pr_number": 200,
            "base_sha": _BASE_SHA_V2,
            "head_sha": _HEAD_SHA_V2,
            "tested_merge_sha": _TESTED_MERGE_SHA_V2,
            "toolchain_digest": _DIGEST_V2,
            "event_type": "pull_request",
            "event_action": "synchronize",
            "delivery_id": "delivery-0001",
        }
    )


def _transport_for_v2(*diff_texts: str, findings_per_chunk: int = 0):
    """Prepare responses for whatever chunks the assembly produces.

    Built by driving the same assembly the composer will drive, so the
    responses bind to the real payload identities rather than to guesses.
    """
    profile = _profile_v2()
    outcome = assemble_manifest_from_diff_v2(
        [fd for text in diff_texts for fd in parse_unified_diff(text)],
        profile=profile,
        grouping_policy=_grouping_policy_v2(),
        repo="mglpsw/aiops-orchestrator",
        pr_number=200,
        base_sha=_BASE_SHA_V2,
        head_sha=_HEAD_SHA_V2,
        tested_merge_sha=_TESTED_MERGE_SHA_V2,
        toolrepo_sha=_DIGEST_V2[:40],
        evidence_hash=_DIGEST_V2,
        max_lines_per_chunk=400,
    )
    assert outcome.manifest is not None

    prepared: dict[str, str] = {}
    for built in build_chunk_payloads_v2(outcome.manifest):
        payload = built.payload
        coverage = json.loads(payload.coverage.model_dump_json())
        reviewed_path = coverage["expected_files"][0]
        findings = [
            {
                "finding_id": f"finding-{index:03d}",
                "severity": "P2",
                "title": "observed",
                "file_path": reviewed_path,
                "line_start": None,
                "line_end": None,
                "evidence": "offline",
                "impact": "offline",
                "confidence": "medium",
                "contract_ids": [],
                "disposition": "new",
            }
            for index in range(findings_per_chunk)
        ]
        envelope: dict[str, object] = {
            "schema_id": "agent-review.chunk-response-envelope.v2",
            "schema_version": 2,
            "source": "agent-review-provider-response",
            "status": "success",
            "run_id": payload.run_id,
            "chunk_id": payload.chunk_id,
            "payload_sha256": payload.payload_sha256,
            "head_sha": payload.identity.head_sha,
            "provider": "offline",
            "model": "offline-fixture",
            "attempt": 1,
            "request_id": f"req-{payload.chunk_id}",
            "finish_reason": "stop",
            "response_received": True,
            "response_sha256": "9" * 64,
            "result": {
                "schema_id": "agent-review.chunk-response.v2",
                "schema_version": 2,
                "summary": "offline-review",
                "findings": findings,
                "coverage": coverage,
                "limitations": [],
            },
        }
        envelope["response_sha256"] = compute_response_sha256_v2(envelope)
        prepared[payload.chunk_id] = json.dumps(envelope)

    def _transport(payload):
        return prepared.get(payload.chunk_id)

    return _transport


def _run_v2(*diff_texts: str, profile: TargetProfileV2 | None = None, findings_per_chunk: int = 0):
    return execute_operational_run_v2(
        inputs=_inputs_v2(),
        profile=profile or _profile_v2(),
        grouping_policy=_grouping_policy_v2(),
        file_diffs=[fd for text in diff_texts for fd in parse_unified_diff(text)],
        transport=_transport_for_v2(*diff_texts, findings_per_chunk=findings_per_chunk),
        evidence_hash=_DIGEST_V2,
    )


def test_a_run_composes_and_returns_readiness() -> None:
    """Non-vacuity control for every assertion below."""
    result = _run_v2(_REVIEWABLE_DIFF_V2)

    assert result.manifest is not None
    assert isinstance(result.readiness_state, ReadinessStateV2)
    assert result.scope.reviewable_paths == ("app/service.py",)


def test_one_synthesis_feeds_readiness_and_the_returned_findings() -> None:
    """Identity, with a real finding present so it cannot be vacuous.

    `#276`'s equivalent assertion compared two empty tuples, which CPython
    interns -- it passed for a run that had synthesised twice. A finding is
    injected here so both sides are non-interned values.
    """
    result = _run_v2(_REVIEWABLE_DIFF_V2, findings_per_chunk=1)

    assert len(result.findings) == 1, "the identity check must not be vacuous"
    assert result.findings is result.synthesis.findings
    assert result.findings == result.synthesis.findings


def test_metadata_only_changes_do_not_block_a_run() -> None:
    """The `#276` regression, at composer level.

    The predecessor raised for any non-empty ``excluded_paths``, so this exact
    combination denied the whole review.
    """
    result = _run_v2(_REVIEWABLE_DIFF_V2, _PURE_RENAME_DIFF_V2)

    assert result.scope.metadata_only_paths == ("app/new_name.py",)
    assert result.scope.scope_complete is True
    assert result.readiness_state is not ReadinessStateV2.BLOCKED_PIPELINE


def test_unsupported_material_makes_ready_impossible() -> None:
    """Scope completeness gates ``ready`` without any published vocabulary."""
    result = _run_v2(_REVIEWABLE_DIFF_V2, _BINARY_DIFF_V2)

    assert result.scope.unsupported_paths == ("assets/logo.png",)
    assert result.scope.scope_complete is False
    assert result.ready is False


def test_a_must_review_path_that_is_unreviewable_fails_closed() -> None:
    """Strictly stronger than incomplete scope, and diagnosed accurately.

    The assembly would also refuse this run, since a must-review path with no
    fragments blocks it -- but under a generic
    ``operational_run_assembly_blocked``, which sends an operator to look at
    chunking when the real cause is that a path the target declared
    must-review carried nothing reviewable. The scope authority is the
    component that actually knows, so it decides, and the reason code says so.
    """
    with pytest.raises(OperationalRunError) as caught:
        _run_v2(
            _REVIEWABLE_DIFF_V2,
            _BINARY_DIFF_V2,
            profile=_profile_v2(must_review_paths=("assets/logo.png",)),
        )

    assert caught.value.reason_code == "operational_run_must_review_unreviewable"
    assert isinstance(caught.value, ExpectedOperationalRefusalV2)


def test_a_missing_chunk_response_is_a_typed_refusal() -> None:
    """A transport that returns nothing must not yield a partial run."""
    with pytest.raises(OperationalRunError) as caught:
        execute_operational_run_v2(
            inputs=_inputs_v2(),
            profile=_profile_v2(),
            grouping_policy=_grouping_policy_v2(),
            file_diffs=list(parse_unified_diff(_REVIEWABLE_DIFF_V2)),
            transport=lambda payload: None,
            evidence_hash=_DIGEST_V2,
        )

    assert caught.value.reason_code == "operational_run_missing_chunk_response"
    assert isinstance(caught.value, ExpectedOperationalRefusalV2)


def test_the_composer_accepts_no_unvalidated_input() -> None:
    """Ingress cannot be skipped by passing a look-alike.

    The parameter type is produced only by the ingress authority, so a plain
    object carrying the same attribute names is not interchangeable with one.
    """
    import inspect

    signature = inspect.signature(execute_operational_run_v2)
    annotation = signature.parameters["inputs"].annotation

    assert annotation is not str
    assert "ValidatedPublicInputsV2" in str(annotation)


def test_the_composer_uses_only_the_range_aware_binder() -> None:
    """Structural: the un-ranged binder must not be reachable from here.

    Authority D is only worth anything if the composition cannot bypass it, so
    the import is asserted rather than trusted.
    """
    import app.agent_review.operational_run_v2 as composer

    source = inspect_source_v2(composer)

    assert "bind_offline_response_with_range_authority_v2" in source
    assert "bind_chunk_response_v2" not in source, (
        "the composer must not reach the binder that skips range validation"
    )


def inspect_source_v2(module) -> str:
    import inspect as _inspect

    return _inspect.getsource(module)
