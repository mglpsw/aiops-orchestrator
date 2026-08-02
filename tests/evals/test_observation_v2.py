from __future__ import annotations

import pytest
from pydantic import ValidationError

from evals.agent_review_v2.observation import (
    AiopsFindingReferenceV2,
    ExternalObservationV2,
    correlate_observation_v2,
)


def _observation(**overrides: object) -> dict:
    base: dict = {
        "source": "codex_local",
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 1,
        "head_sha": "2" * 40,
        "file_path": "app/agent_review/foo.py",
        "severity_claimed": "P2",
        "normalized_cause": "synthetic cause",
        "evidence_summary": "synthetic evidence",
    }
    base.update(overrides)
    return base


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
        AiopsFindingReferenceV2(finding_id="f1", file_path="app/agent_review/foo.py", severity="P2"),
        AiopsFindingReferenceV2(finding_id="f2", file_path="app/agent_review/bar.py", severity="P1"),
    ]
    result = correlate_observation_v2(obs, aiops_findings=findings)
    assert result.disposition == "matched"
    assert result.matched_finding_id == "f1"


def test_correlate_rejects_when_no_candidate():
    obs = ExternalObservationV2.model_validate(_observation())
    findings = [AiopsFindingReferenceV2(finding_id="f2", file_path="app/agent_review/bar.py", severity="P1")]
    result = correlate_observation_v2(obs, aiops_findings=findings)
    assert result.disposition == "rejected"
    assert result.matched_finding_id is None


def test_correlate_is_inconclusive_when_ambiguous():
    """Non-vacuity proof: two AIOps findings at the identical file+severity
    make the match genuinely ambiguous -- this must resolve to
    `inconclusive`, never arbitrarily pick one, and never silently `matched`."""

    obs = ExternalObservationV2.model_validate(_observation())
    findings = [
        AiopsFindingReferenceV2(finding_id="f1", file_path="app/agent_review/foo.py", severity="P2"),
        AiopsFindingReferenceV2(finding_id="f3", file_path="app/agent_review/foo.py", severity="P2"),
    ]
    result = correlate_observation_v2(obs, aiops_findings=findings)
    assert result.disposition == "inconclusive"
    assert result.matched_finding_id is None


def test_correlate_never_produces_confirmed_disposition():
    """The issue's own rule: a correlation `matched` must never be
    confused with the AIOps lifecycle's `confirmed` disposition -- the
    correlation result type does not even have that value available."""

    obs = ExternalObservationV2.model_validate(_observation())
    findings = [AiopsFindingReferenceV2(finding_id="f1", file_path="app/agent_review/foo.py", severity="P2")]
    result = correlate_observation_v2(obs, aiops_findings=findings)
    assert result.disposition in ("matched", "rejected", "inconclusive")
    assert result.disposition != "confirmed"
