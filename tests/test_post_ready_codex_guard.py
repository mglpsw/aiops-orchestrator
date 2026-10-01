from __future__ import annotations

from copy import deepcopy

from scripts.github_codex_post_ready_guard import evaluate_evidence


HEAD = "661422837a3099bb8d989907d42314e26e410aa7"
BASE = "6bbd2f949989da3e90e1d9c527e37059c0b628ff"
REPO = "mglpsw/aiops-orchestrator"


def _evidence(*, draft: bool = False, merged: bool = False, review_head: str = HEAD, findings=None, ready=True):
    return {
        "pr": {
            "repo": REPO,
            "number": 369,
            "state": "open",
            "draft": draft,
            "merged": merged,
            "head_sha": HEAD,
            "base_sha": BASE,
        },
        "ready_events": ([{"event": "ready_for_review", "created_at": "2026-10-01T16:16:00Z"}] if ready else []),
        "reviews": [{
            "id": 5382257522,
            "author_login": "chatgpt-codex-connector",
            "state": "COMMENTED",
            "commit_id": review_head,
            "submitted_at": "2026-10-01T16:21:19Z",
        }],
        "summaries": [{
            "id": 5935569021,
            "author_login": "chatgpt-codex-connector",
            "body": "Codex Review Summary\n✅ Completed\nCommit `6614228`\nReview trigger: Draft marked ready",
            "created_at": "2026-10-01T16:16:41Z",
        }],
        "findings": findings or [],
        "checks": [
            {"name": "Validate repository", "status": "completed", "conclusion": "success"},
            {"name": "AgentReview release gates", "status": "completed", "conclusion": "success"},
        ],
        "collection_errors": [],
    }


def _evaluate(evidence):
    return evaluate_evidence(
        evidence,
        expected_repo=REPO,
        expected_pr=369,
        expected_head=HEAD,
        expected_base=BASE,
        required_checks=("Validate repository", "AgentReview release gates"),
    )


def test_ci_green_and_ready_without_terminal_codex_review_is_blocked():
    evidence = _evidence()
    evidence["reviews"] = []
    evidence["summaries"] = []

    assert _evaluate(evidence).state == "HELD_PENDING_CODEX"


def test_terminal_review_with_material_finding_is_blocked_until_disposition():
    evidence = _evidence(findings=[{"id": 4157801069, "review_id": 5382257522, "material": True}])

    assert _evaluate(evidence).state == "HELD_WITH_MATERIAL_FINDINGS"


def test_older_review_cannot_qualify_new_head():
    evidence = _evidence(review_head="d3f5946c4d0513def9f7c2018b63703a53df1cc")

    assert _evaluate(evidence).state == "HELD_PENDING_CODEX"


def test_post_merge_review_does_not_satisfy_pre_merge_gate():
    evidence = _evidence(merged=True)

    assert _evaluate(evidence).state == "HELD_STALE"


def test_incomplete_api_collection_is_not_success():
    evidence = _evidence()
    evidence["collection_errors"] = ["review-thread evidence is unavailable"]

    assert _evaluate(evidence).state == "HELD_CODEX_UNAVAILABLE"


def test_positive_path_is_observational_only():
    result = _evaluate(_evidence())

    assert result.state == "READY_FOR_HUMAN_INTEGRATION_DECISION"
    assert result.merge_authorized is False


def test_adjudicated_material_finding_can_satisfy_observation():
    evidence = _evidence(findings=[{
        "id": 4157801069,
        "review_id": 5382257522,
        "material": True,
        "disposition": "FIXED",
    }])

    assert _evaluate(evidence).state == "READY_FOR_HUMAN_INTEGRATION_DECISION"


def test_resolved_thread_without_correction_is_still_material():
    evidence = _evidence(findings=[{
        "id": 4157801069,
        "review_id": 5382257522,
        "material": True,
        "thread_resolved": True,
    }])

    assert _evaluate(evidence).state == "HELD_WITH_MATERIAL_FINDINGS"


def test_causal_mutation_removing_terminal_review_check_is_detected():
    evidence = _evidence()
    mutant = deepcopy(evidence)
    mutant["reviews"] = []

    assert _evaluate(evidence).state == "READY_FOR_HUMAN_INTEGRATION_DECISION"
    assert _evaluate(mutant).state == "HELD_PENDING_CODEX"


def test_ready_event_is_required_for_review_binding():
    assert _evaluate(_evidence(ready=False)).state == "HELD_STALE"


def test_latest_ready_cycle_invalidates_an_older_terminal_review():
    evidence = _evidence()
    evidence["ready_events"].append({"event": "ready_for_review", "created_at": "2026-10-01T16:25:00Z"})

    assert _evaluate(evidence).state == "HELD_STALE"


def test_draft_state_is_pending_even_with_a_review_shape():
    assert _evaluate(_evidence(draft=True)).state == "HELD_PENDING_CODEX"
