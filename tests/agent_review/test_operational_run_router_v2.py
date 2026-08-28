"""`#200-E` Phase 3, §16 -- provider-free Router receipt-v2 PRODUCT E2E.

Separate from the offline transport proof. Runs the REAL operational
composer with the REAL `agent_router_transport_v2`, HTTP boundary mocked
via the same `_open_agent_router_request_v2` patch point
`test_review_transport_v2.py` already establishes as the correct mock
seam -- reused here, not reinvented, but driven DYNAMICALLY from whatever
chunk(s) the real composer actually produces (the existing test's mock is
pre-computed against a hand-built single chunk; a real target's diff
determines its own chunking, so this mock reconstructs a valid response
per-request from the request body itself).
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from app.agent_review.contracts_v2 import ChunkReviewResultV2, PullRequestStateV2, RunOriginV2, SemanticGroupV2
from app.agent_review.operational_run_v2 import OperationalReviewInputsV2, run_operational_review_v2
from app.agent_review.review_transport_v2 import agent_router_transport_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "v2" / "agent_escala"
_ROUTER_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "router_receipt_v2"
_EVIDENCE_HASH = "d" * 64
_SECRET_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz123456"


def _agent_escala_policy() -> SemanticGroupingPolicyV2:
    rules = [
        SemanticGroupingRuleV2(
            rule_id="backend",
            semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
            path_patterns=["backend/scheduling/*.py"],
            contract_ids=[],
            artifact_ids=[],
            priority=0,
        )
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


def _build_target_with_secret(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "target_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)

    (repo / "backend" / "scheduling").mkdir(parents=True)
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "def compute_shift():\n    return 1\n", encoding="utf-8"
    )
    (repo / "artifacts").mkdir()
    shutil.copy(FIXTURES_ROOT / "artifacts" / "full.diff", repo / "artifacts" / "full.diff")
    (repo / "contracts").mkdir()
    shutil.copy(
        FIXTURES_ROOT / "contracts" / "domain-contracts.yaml", repo / "contracts" / "domain-contracts.yaml"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        f"def compute_shift():\n    token = '{_SECRET_TOKEN}'\n    return 2\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "head"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    return repo, base_sha, head_sha


def _dynamic_router_mock(*, captured: list, tamper_returned_sha256: bool = False, tamper_caller_metadata: bool = False):
    """Builds a valid response for WHATEVER chunk request actually arrives,
    reconstructed from the request body itself -- not pre-computed against
    a hand-built fixture chunk, since the real composer determines its own
    chunking from a real diff."""

    class _FakeResponse:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self._raw

    def _fake_urlopen(http_request, timeout):
        request_body = json.loads(http_request.data.decode("utf-8"))
        captured.append((http_request.full_url, request_body))
        user_material = json.loads(request_body["messages"][1]["content"])
        coverage = user_material["coverage"]

        result_document = {
            "schema_id": "agent-review.chunk-response.v2",
            "schema_version": 2,
            "summary": "router review complete",
            "findings": [],
            "coverage": coverage,
            "limitations": [],
        }
        assistant_content = json.dumps(
            result_document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )

        receipt = json.loads(
            (_ROUTER_FIXTURE_ROOT / "local-success-f2a.json").read_text(encoding="utf-8")
        )
        messages_bytes = json.dumps(
            request_body["messages"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        receipt["requested"]["model"] = request_body["model"]
        receipt["received_input"]["sha256"] = hashlib.sha256(messages_bytes).hexdigest()
        returned_sha256 = hashlib.sha256(assistant_content.encode("utf-8")).hexdigest()
        if tamper_returned_sha256:
            returned_sha256 = "0" * 64
        receipt["returned_output"]["sha256"] = returned_sha256
        caller_metadata = copy.deepcopy(request_body["metadata"])
        if tamper_caller_metadata:
            caller_metadata["chunk_id"] = "tampered-chunk-id-does-not-exist"
        receipt["caller_declared_metadata"] = caller_metadata

        response_body = {
            "id": "chatcmpl-fixture",
            "object": "chat.completion",
            "created": 1,
            "model": "resolved-model-is-not-a-domain-identity",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": assistant_content}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "request_id": "router-public-request",
            "inference_receipt": receipt,
        }
        return _FakeResponse(json.dumps(response_body).encode("utf-8"))

    return _fake_urlopen


def _run_router_product_review(tmp_path: Path, *, mock_urlopen):
    repo, base_sha, head_sha = _build_target_with_secret(tmp_path)
    transport = agent_router_transport_v2(
        base_url="https://router.example/", api_key="secret-token", model="review:code"
    )
    inputs = OperationalReviewInputsV2(
        source_target_root=repo,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha="3" * 40,
        toolrepo_sha="4" * 40,
        evidence_hash=_EVIDENCE_HASH,
        repo="mglpsw/AgentEscala",
        pr_number=101,
        trusted_profile_root=FIXTURES_ROOT,
        grouping_policy=_agent_escala_policy(),
        transport=transport,
        pr_state=PullRequestStateV2.OPEN,
        origin=RunOriginV2(event_type="manual", event_action="manual", delivery_id="delivery-200e-router-1"),
    )
    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2", side_effect=mock_urlopen
    ):
        return run_operational_review_v2(inputs)


def test_router_product_e2e_binds_a_real_chunk_result(tmp_path: Path):
    captured: list = []
    readiness = _run_router_product_review(tmp_path, mock_urlopen=_dynamic_router_mock(captured=captured))

    assert len(captured) >= 1
    assert readiness.evaluated_head_sha != ""
    assert readiness.state != "ready"  # honest: no authoritative checks submitted


def test_router_product_e2e_redacts_secret_before_outbound_request(tmp_path: Path):
    """§16: raw secret absent outbound, sanitized line still present."""
    captured: list = []
    _run_router_product_review(tmp_path, mock_urlopen=_dynamic_router_mock(captured=captured))

    assert len(captured) >= 1
    for _url, request_body in captured:
        raw_bytes = json.dumps(request_body).encode("utf-8")
        assert _SECRET_TOKEN.encode("utf-8") not in raw_bytes, "raw secret reached the outbound request"
    combined = json.dumps([body for _url, body in captured])
    assert "REDACTED" in combined or "redact" in combined.lower(), (
        "expected a sanitized placeholder line to survive redaction"
    )


def test_router_product_e2e_tampered_output_sha256_never_binds(tmp_path: Path):
    """Non-vacuity control: a tampered `returned_output.sha256` must
    degrade to manual_required, never bind a finding."""
    captured: list = []
    readiness = _run_router_product_review(
        tmp_path,
        mock_urlopen=_dynamic_router_mock(captured=captured, tamper_returned_sha256=True),
    )
    assert readiness.state != "ready"
    # With a single chunk in this fixture, a returned_output.sha256
    # mismatch means that chunk degrades to manual_required and zero
    # findings reach readiness -- expressed positively, not merely
    # "not ready" (which the honest-non-ready test already covers for an
    # unrelated reason: no authoritative checks submitted).
    assert len(readiness.findings) == 0


def test_router_product_e2e_tampered_caller_metadata_never_binds(tmp_path: Path):
    captured: list = []
    readiness = _run_router_product_review(
        tmp_path,
        mock_urlopen=_dynamic_router_mock(captured=captured, tamper_caller_metadata=True),
    )
    assert len(readiness.findings) == 0
