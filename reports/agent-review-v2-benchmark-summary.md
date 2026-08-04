# AgentReview v2 benchmark report (#88)

- **benchmark_run_id:** `agentreview-v2-benchmark-ae758ab039de`
- **source_master_sha:** `ae758ab039def1e791e603efdddc0ad8e370a5be`
- **statistical_status:** descriptive_provisional_baseline (promotion_authority: False)

## aiops_pipeline (pipeline correctness, not detection)

- readiness_accuracy: 10/10
- finding_preservation: 6/6
- false_approval_count: 0
- coverage_behavior: complete_manifest_assembly=10/10 (no blocked_pipeline outcome)
- stale_rejection: not_applicable -- no identity_negative case is provider_review_applicable (stale is a Lane 1/2A-only pipeline_integrity question, see MANIFEST.yaml lane1_only_cases)

## codex_local_detection

- location_recall: 6/6
- severity_exact_match_rate: 3/6
- false_positive_count: 0 / 4 counterexamples
- canonical correlation (correlate_observation_v2): {'matched': 3, 'rejected': 3, 'inconclusive': 0, 'note': "correlate_observation_v2 requires EXACT severity match (never just file_path) -- a location-correct finding whose claimed severity differs from the case's ground truth severity is 'rejected' by this canonical tool, not silently loosened here. See location_recall above for the file-level detection signal independent of severity calibration."}

## codex_github_detection

- location_recall: 6/6
- severity_exact_match_rate: 6/6
- false_positive_count: 0 / 4 counterexamples
- canonical correlation (correlate_observation_v2): {'matched': 6, 'rejected': 0, 'inconclusive': 0, 'note': "correlate_observation_v2 requires EXACT severity match (never just file_path) -- a location-correct finding whose claimed severity differs from the case's ground truth severity is 'rejected' by this canonical tool, not silently loosened here. See location_recall above for the file-level detection signal independent of severity calibration."}

## cross_source_overlap

{'canonical_expected_vs_codex': {'both_lanes_location_match': '6/6', 'codex_local_only': 0, 'codex_github_only': 0, 'neither_lane': 0}}

## Disposition

- codex_operational_eligibility: eligible
- allowed_role: shadow
- advisory_eligibility: deferred_to_target_observation
- required_check_eligible: False
- readiness_authority: False

## human_lane

{'status': 'deferred', 'destination': 'RI-C/RI-D', 'completed': False, 'disposition_ref': 'https://github.com/mglpsw/aiops-orchestrator/issues/88#issuecomment-5183154624'}

## Limitations

- sample size is 6 semantic_positive / 4 semantic_safe_counterexample cases -- a descriptive baseline, not a statistically powered study
- correlate_observation_v2 requires exact severity match; codex_local's severity calibration diverged from ground truth on 3/6 positive cases in this run (all location-correct) -- see codex_local_detection.canonical_correlation... vs .location_recall for the split
- stale/pipeline_integrity/transport_or_dlp_stop cases are Lane 1/2A-only by design (see MANIFEST.yaml) and are excluded from these detection denominators
- Lane 4 (human) is deferred to RI-C/RI-D per the H2 disposition; no human precision/recall is reported
