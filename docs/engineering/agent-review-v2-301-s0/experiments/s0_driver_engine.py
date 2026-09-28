"""EXPERIMENTAL ONLY -- the engine percurso exercised by exp_functional.py.

Mirrors the stage sequence of the real v2 caller (AgentEscala `scripts/aiops/agent_review_v2_run.py`
@ develop 8537eb18): profile load (YAML) -> semantic grouping policy -> parse_unified_diff ->
assemble_manifest_from_diff_v2 -> build_chunk_payloads_from_profile_v2 -> emit_payload_set_v2 ->
bind_chunk_response_v2 -> parse_bound_chunk_response_v2 -> synthesize_chunk_results_v2 ->
compute_readiness_decision_v2 -> redaction/sanitization of the emitted bundle.

Differences from the real caller, stated rather than hidden: the Router call is replaced by a
synthetic, schema-valid provider response (the engine consumes it as data); authoritative PR
reacquisition and the rollout ceiling are target-side and are not engine code. Inputs are
synthetic (the dual-target conformance fixture of this repository).
"""


def run(inputs):
    import hashlib
    import json
    import resource
    from pathlib import Path

    from app.agent_review.consumer_v2 import bind_chunk_response_v2
    from app.agent_review.contracts_v2 import SemanticGroupV2, compute_response_sha256_v2
    from app.agent_review.diff_acquisition_v2 import parse_unified_diff
    from app.agent_review.parser_v2 import parse_bound_chunk_response_v2
    from app.agent_review.payload_builder_v2 import build_chunk_payloads_from_profile_v2
    from app.agent_review.payload_set_emission_v2 import emit_payload_set_v2
    from app.agent_review.profile_loader_v2 import load_target_profile_v2
    from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
    from app.agent_review.redaction import redact_content, sanitize_artifact_value
    from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
    from app.agent_review.semantic_grouping_policy_v2 import (
        SemanticGroupingPolicyV2,
        SemanticGroupingRuleV2,
        compute_semantic_grouping_policy_sha256_v2,
    )
    from app.agent_review.synthesis_v2 import synthesize_chunk_results_v2

    target_root = Path(inputs["target_root"])
    profile = load_target_profile_v2(target_root)
    rules = [
        SemanticGroupingRuleV2(rule_id=r["rule_id"], semantic_group=SemanticGroupV2(r["group"]),
                               path_patterns=r["patterns"], contract_ids=[], artifact_ids=[], priority=0)
        for r in inputs["rules"]
    ]
    material = {"schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2,
                "source": "repo-semantic-grouping-policy", "rules": rules, "fallback_group": None}
    policy = SemanticGroupingPolicyV2(**material, policy_sha256=compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [r.model_dump(mode="json") for r in rules]}))

    diff_text = inputs["diff_text"]
    file_diffs = parse_unified_diff(diff_text)
    outcome = assemble_manifest_from_diff_v2(
        file_diffs, profile=profile, grouping_policy=policy, repo=profile.identity.repo,
        pr_number=inputs["pr_number"], base_sha=inputs["base_sha"], head_sha=inputs["head_sha"],
        tested_merge_sha=inputs["head_sha"], toolrepo_sha=inputs["toolrepo_sha"],
        evidence_hash=hashlib.sha256(diff_text.encode()).hexdigest(), max_lines_per_chunk=200,
        expected_paths=frozenset(d.new_path for d in file_diffs),
    )
    if outcome.state != "assembled":
        return {"manifest_state": outcome.state, "blocked": str(outcome.blocked_reason)}
    manifest = outcome.manifest
    built = build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=target_root)
    payload_set = emit_payload_set_v2(manifest, [b.payload for b in built])

    results = []
    for b in built:
        p = b.payload
        envelope = {
            "schema_id": "agent-review.chunk-response-envelope.v2", "schema_version": 2,
            "source": "agent-review-provider-response", "status": "success", "run_id": p.run_id,
            "chunk_id": p.chunk_id, "payload_sha256": p.payload_sha256, "head_sha": p.identity.head_sha,
            "provider": "synthetic", "model": "synthetic", "attempt": 1, "request_id": "req-" + p.chunk_id,
            "finish_reason": "stop", "response_received": True, "response_sha256": "0" * 64,
            "result": {
                "schema_id": "agent-review.chunk-response.v2", "schema_version": 2, "summary": "review-complete",
                "findings": [], "limitations": [],
                "coverage": {"status": "complete", "expected_files": p.coverage.expected_files,
                             "reviewed_files": p.coverage.expected_files, "partially_reviewed_files": [],
                             "missing_files": [], "must_review_files": p.coverage.must_review_files,
                             "missing_must_review_files": [], "degradation_causes": []},
            },
        }
        envelope["response_sha256"] = compute_response_sha256_v2(envelope)
        results.append(parse_bound_chunk_response_v2(bind_chunk_response_v2(envelope=envelope, payload=p)))

    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results,
                                            evaluated_head_sha=inputs["head_sha"])
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=profile.policies)
    bundle = {"run_id": manifest.run_id, "manifest_hash": manifest.identity.manifest_hash,
              "profile_hash": manifest.identity.profile_hash, "policy_hash": manifest.identity.policy_hash,
              "payload_set_sha256": payload_set.payload_set_sha256, "readiness_state": decision.state.value,
              "readiness_reason_codes": [r.value for r in decision.reason_codes], "chunks": len(built)}
    sanitized, _report = redact_content(sanitize_artifact_value(bundle), source="s0-experiment")
    return {
        "manifest_state": outcome.state,
        "bundle": sanitized,
        "bundle_sha256": hashlib.sha256(json.dumps(sanitized, sort_keys=True).encode()).hexdigest(),
        "maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
