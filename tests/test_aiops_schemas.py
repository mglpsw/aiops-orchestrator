from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent_router.schemas import (
    AIOpsDiagnoseRequest,
    AIOpsDiagnoseResponse,
    AIOpsFinding,
    AIOpsRecommendedAction,
    AIOpsSignal,
)


def test_aiops_diagnose_request_valid_with_dry_run_true() -> None:
    request = AIOpsDiagnoseRequest(
        checks=["readiness", "error_rate"],
        metadata={"source": "test"},
    )

    assert request.target == "agent-router"
    assert request.scope == "self"
    assert request.dry_run is True
    assert request.checks == ["readiness", "error_rate"]
    assert request.metadata == {"source": "test"}


def test_aiops_diagnose_request_rejects_dry_run_false() -> None:
    with pytest.raises(ValidationError, match="dry_run must be True"):
        AIOpsDiagnoseRequest(dry_run=False)


def test_aiops_diagnose_request_rejects_unknown_check() -> None:
    with pytest.raises(ValidationError, match="Unknown check"):
        AIOpsDiagnoseRequest(checks=["readiness", "unknown_check"])


def test_aiops_recommended_action_rejects_command() -> None:
    with pytest.raises(ValidationError, match="command must be None"):
        AIOpsRecommendedAction(
            title="Inspect logs",
            description="Review logs in dry-run",
            command="tail -n 20 /var/log/app.log",
        )


def test_aiops_recommended_action_rejects_non_dry_run_action_type() -> None:
    with pytest.raises(ValidationError, match="action_type must be dry_run"):
        AIOpsRecommendedAction(
            title="Restart service",
            description="This should not be allowed in v1",
            action_type="execute",
        )


def test_aiops_diagnose_response_serializes_correctly() -> None:
    response = AIOpsDiagnoseResponse(
        status="warning",
        severity="medium",
        summary="Backend latency is elevated.",
        signals=[
            AIOpsSignal(
                name="latency_p95",
                status="degraded",
                value=212.5,
                unit="ms",
                source="metrics",
                description="P95 latency above threshold.",
            )
        ],
        findings=[
            AIOpsFinding(
                title="Latency elevated",
                severity="medium",
                status="degraded",
                description="Observed higher-than-normal p95 latency.",
                evidence=[
                    AIOpsSignal(
                        name="latency_p95",
                        status="degraded",
                        value=212.5,
                        unit="ms",
                        source="metrics",
                    )
                ],
            )
        ],
        recommended_actions=[
            AIOpsRecommendedAction(
                title="Review backend health",
                description="Check backend availability and latency trends in dry-run.",
            )
        ],
    )

    data = response.model_dump()

    assert data["dry_run"] is True
    assert data["status"] == "warning"
    assert data["severity"] == "medium"
    assert data["signals"][0]["name"] == "latency_p95"
    assert data["findings"][0]["evidence"][0]["source"] == "metrics"
    assert data["recommended_actions"][0]["action_type"] == "dry_run"
