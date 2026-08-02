from __future__ import annotations

import pytest
from pydantic import ValidationError

from evals.agent_review_v2.observation import (
    AiopsFindingReferenceV2,
    ExternalObservationV2,
    correlate_observation_v2,
)

_REPO = "mglpsw/aiops-orchestrator"
_PR_NUMBER = 1
_HEAD_SHA = "2" * 40


def _observation(**overrides: object) -> dict:
    base: dict = {
        "source": "codex_local",
        "repo": _REPO,
        "pr_number": _PR_NUMBER,
        "head_sha": _HEAD_SHA,
        "file_path": "app/agent_review/foo.py",
        "severity_claimed": "P2",
        "normalized_cause": "synthetic cause",
        "evidence_summary": "synthetic evidence",
    }
    base.update(overrides)
    return base


def _finding(**overrides: object) -> AiopsFindingReferenceV2:
    base: dict = {
        "finding_id": "f1",
        "repo": _REPO,
        "pr_number": _PR_NUMBER,
        "head_sha": _HEAD_SHA,
        "file_path": "app/agent_review/foo.py",
        "severity": "P2",
    }
    base.update(overrides)
    return AiopsFindingReferenceV2.model_validate(base)


def test_observation_rejects_unknown_field():
    with pytest.raises(ValidationError):
        ExternalObservationV2.model_validate({**_observation(), "unexpected": True})


def test_observation_rejects_unknown_source():
    with pytest.raises(ValidationError):
        ExternalObservationV2.model_validate(_observation(source="chatgpt_workspace"))


def test_observation_default_disposition_is_unverified():
    obs = ExternalObservationV2.model_validate(_observation())
    assert obs.disposition == "unverified"


def test_correlate_matches_single_candidate():
    obs = ExternalObservationV2.model_validate(_observation())
    findings = [
        _finding(finding_id="f1", file_path="app/agent_review/foo.py", severity="P2"),
        _finding(finding_id="f2", file_path="app/agent_review/bar.py", severity="P1"),
    ]
    result = correlate_observation_v2(obs, aiops_findings=findings)
    assert result.disposition == "matched"
    assert result.matched_finding_id == "f1"


def test_correlate_rejects_when_no_candidate():
    obs = ExternalObservationV2.model_validate(_observation())
    findings = [_finding(finding_id="f2", file_path="app/agent_review/bar.py", severity="P1")]
    result = correlate_observation_v2(obs, aiops_findings=findings)
    assert result.disposition == "rejected"
    assert result.matched_finding_id is None


def test_correlate_is_inconclusive_when_ambiguous():
    """Non-vacuity proof: two AIOps findings at the identical file+severity
    (for the SAME run) make the match genuinely ambiguous -- this must
    resolve to `inconclusive`, never arbitrarily pick one, and never
    silently `matched`."""

    obs = ExternalObservationV2.model_validate(_observation())
    findings = [
        _finding(finding_id="f1", file_path="app/agent_review/foo.py", severity="P2"),
        _finding(finding_id="f3", file_path="app/agent_review/foo.py", severity="P2"),
    ]
    result = correlate_observation_v2(obs, aiops_findings=findings)
    assert result.disposition == "inconclusive"
    assert result.matched_finding_id is None


def test_correlate_never_produces_confirmed_disposition():
    """The issue's own rule: a correlation `matched` must never be
    confused with the AIOps lifecycle's `confirmed` disposition -- the
    correlation result type does not even have that value available."""

    obs = ExternalObservationV2.model_validate(_observation())
    findings = [_finding(finding_id="f1", file_path="app/agent_review/foo.py", severity="P2")]
    result = correlate_observation_v2(obs, aiops_findings=findings)
    assert result.disposition in ("matched", "rejected", "inconclusive")
    assert result.disposition != "confirmed"


def test_correlate_rejects_cross_run_identity_mismatch():
    """Regression (Codex review of #149): an AiopsFindingReferenceV2 from a
    DIFFERENT repo/PR/HEAD must never be treated as a candidate, even if
    its file_path/severity happen to coincide -- a coincidental cross-run
    match is exactly the false-positive class this guards against."""

    obs = ExternalObservationV2.model_validate(_observation())

    different_repo = _finding(repo="mglpsw/AgentEscala")
    different_pr = _finding(pr_number=999)
    different_head = _finding(head_sha="9" * 40)

    for mismatched in (different_repo, different_pr, different_head):
        result = correlate_observation_v2(obs, aiops_findings=[mismatched])
        assert result.disposition == "rejected", mismatched
        assert result.matched_finding_id is None

    # Sanity: the identical identity on all three fields IS a real match.
    same_identity = _finding()
    result = correlate_observation_v2(obs, aiops_findings=[same_identity])
    assert result.disposition == "matched"


def test_construction_rejects_malformed_placeholder_identities():
    """Codex review of PR #154: repo/pr_number/head_sha were plain str/int
    fields with no format validation, so two unrelated runs that both
    happened to carry a degenerate placeholder identity (an empty repo,
    PR 0, head_sha="unknown") would coincidentally compare equal in
    correlate_observation_v2's equality check and report a false shared-run
    match. The canonical contracts already require owner/name repos,
    positive PR numbers, and 40-character git SHAs (Repository/PositiveInt/
    GitSha in contracts_v2.py) -- both ExternalObservationV2 and
    AiopsFindingReferenceV2 must fail closed at construction time instead,
    never reaching the correlation comparison at all."""

    for repo, pr_number, head_sha in (
        ("", _PR_NUMBER, _HEAD_SHA),
        (_REPO, 0, _HEAD_SHA),
        (_REPO, _PR_NUMBER, "unknown"),
        (_REPO, -1, _HEAD_SHA),
    ):
        with pytest.raises(ValidationError):
            ExternalObservationV2.model_validate(_observation(repo=repo, pr_number=pr_number, head_sha=head_sha))
        with pytest.raises(ValidationError):
            AiopsFindingReferenceV2.model_validate(
                {
                    "finding_id": "f1",
                    "repo": repo,
                    "pr_number": pr_number,
                    "head_sha": head_sha,
                    "file_path": "app/agent_review/foo.py",
                    "severity": "P2",
                }
            )
