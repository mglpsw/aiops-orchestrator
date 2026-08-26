from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    ChunkResponseSuccessEnvelopeV2,
    ChunkReviewResultV2,
    PullRequestStateV2,
    ReadinessReasonV2,
    ReadinessStateV2,
    SemanticGroupV2,
    TargetProfileV2,
    compute_response_sha256_v2,
)
from app.agent_review.diff_acquisition_v2 import acquire_authoritative_diff_v2
from app.agent_review.payload_builder_v2 import build_chunk_payload_v2
from app.agent_review.review_content_extraction_v2 import extract_review_content_v2
from app.agent_review.review_transport_contract_v2 import ChunkReviewTransportEnvelopeV1
from app.agent_review.review_transport_v2 import (
    CHUNK_TRANSPORT_FAILURE_REASON_V2,
    CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2,
    ROUTER_DISABLED_REASON_V2,
    ROUTER_MODEL_UNSUPPORTED_REASON_V2,
    ChunkTransportError,
    agent_router_transport_v2,
    execute_chunk_review_v2,
    offline_file_transport_v2,
    run_synthetic_review_v2,
)
from app.agent_review._router_receipt_v2 import (
    ROUTER_CALLER_BINDING_MISMATCH_REASON_V2,
    ROUTER_FINISH_REASON_INCONCLUSIVE_REASON_V2,
    ROUTER_INPUT_MISMATCH_REASON_V2,
    ROUTER_OUTPUT_MISMATCH_REASON_V2,
    ROUTER_RECEIPT_INVALID_REASON_V2,
    ROUTER_REQUESTED_MODEL_MISMATCH_REASON_V2,
    ROUTER_RESULT_INVALID_REASON_V2,
)
from app.agent_review.review_content_v2 import (
    CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2,
    compute_chunk_content_sha256_v2,
)
from app.agent_review.run_assembly_v2 import assemble_manifest_from_diff_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

# -- fixture helpers (file-local, matching this codebase's own per-file convention) --


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _profile() -> TargetProfileV2:
    return TargetProfileV2.model_validate(
        {
            "schema_id": "agent-review.target-profile.v2", "schema_version": 2, "source": "repo-profile",
            "identity": {"repo": "example/repo", "default_branch": "main"},
            "artifacts": [{"artifact_id": "full-diff", "path": "artifacts/full.diff", "kind": "diff", "required": True, "max_bytes": 1000000}],
            "budgets": {"max_chunks": 32, "total_prompt_chars": 250000, "max_chars_per_chunk": 24000, "max_files_per_chunk": 50, "max_contracts_per_chunk": 50},
            "must_review": {"paths": ["app.py"], "patterns": [], "artifact_ids": [], "minimum_coverage": "complete"},
            "policies": {
                "network_policy": "forbidden", "fail_closed": True, "redaction_required": True,
                "allow_partial_coverage": False, "required_checks": ["pytest"],
                "allowed_semantic_groups": ["primary_backend_logic"],
                "coverage_failure_state": "manual_required", "model_uncertainty_state": "manual_required",
            },
            "contracts": [{"contract_id": "contract.api", "contract_version": "1", "path": ".aiops/domain-contracts.yaml", "sha256": "f" * 64, "scope": "repository", "required": True}],
            "limitations": [],
        }
    )


def _grouping_policy() -> SemanticGroupingPolicyV2:
    rule = SemanticGroupingRuleV2(rule_id="all", semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC, path_patterns=["*"], contract_ids=[], artifact_ids=[], priority=0)
    material = {"schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2, "source": "repo-semantic-grouping-policy", "rules": [rule], "fallback_group": None}
    digest = compute_semantic_grouping_policy_sha256_v2({**material, "rules": [rule.model_dump(mode="json")]})
    return SemanticGroupingPolicyV2(**material, policy_sha256=digest)


def _build_repo_manifest_and_content(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    base_sha = _commit_all(repo, "init")
    (repo / "app.py").write_text("a = 1\nb = CHANGED\nc = 3\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")

    profile = _profile()
    file_diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    outcome = assemble_manifest_from_diff_v2(
        file_diffs, profile=profile, grouping_policy=_grouping_policy(),
        repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
        tested_merge_sha=head_sha, toolrepo_sha="b" * 40, evidence_hash="c" * 64,
        max_lines_per_chunk=1000,
    )
    assert outcome.state == "assembled", outcome.blocked_reason
    manifest = outcome.manifest
    payload_by_chunk_id = {c.chunk_id: build_chunk_payload_v2(manifest, c) for c in manifest.chunks}
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        payload_sha256_by_chunk_id={cid: p.payload_sha256 for cid, p in payload_by_chunk_id.items()},
        target_profile=profile,
    )
    return manifest, content, payload_by_chunk_id


def _success_envelope_dict(*, run_id: str, chunk_id: str, head_sha: str, payload_sha256: str) -> dict:
    coverage = ChunkCoverageV2(
        status="complete", expected_files=("app.py",), reviewed_files=("app.py",),
        partially_reviewed_files=(), missing_files=(), must_review_files=("app.py",),
        missing_must_review_files=(), degradation_causes=(),
    )
    result = ChunkReviewResultV2(
        schema_id="agent-review.chunk-response.v2", schema_version=2,
        summary="looks fine", findings=[], coverage=coverage, limitations=[],
    )
    fields = dict(
        schema_id="agent-review.chunk-response-envelope.v2", schema_version=2,
        source="agent-review-provider-response", run_id=run_id, chunk_id=chunk_id,
        payload_sha256=payload_sha256, head_sha=head_sha, provider="test-provider",
        model="test-model", attempt=1, request_id="req-0001", finish_reason="stop",
        response_received=True, status="success", result=result,
    )
    dumped = {**fields, "result": result.model_dump(mode="json"), "response_sha256": None}
    response_sha256 = compute_response_sha256_v2(dumped)
    envelope = ChunkResponseSuccessEnvelopeV2(**fields, response_sha256=response_sha256)
    return envelope.model_dump(mode="json")


def _write_offline_responses(responses_dir: Path, *, content, manifest, tamper_chunk_id: str | None = None) -> None:
    responses_dir.mkdir(parents=True, exist_ok=True)
    for chunk in content.chunks:
        response = _success_envelope_dict(
            run_id=content.run_id, chunk_id=chunk.chunk_id, head_sha=manifest.identity.head_sha,
            payload_sha256=chunk.payload_sha256,
        )
        from app.agent_review.review_transport_contract_v2 import compute_request_sha256_v2

        request_sha256 = compute_request_sha256_v2(
            run_id=content.run_id, chunk_id=chunk.chunk_id, head_sha=manifest.identity.head_sha,
            payload_sha256=chunk.payload_sha256, content_sha256=chunk.content_sha256,
        )
        content_sha256 = chunk.content_sha256
        if chunk.chunk_id == tamper_chunk_id:
            content_sha256 = "9" * 64  # wrong echo
        envelope = {
            "schema_id": "agent-review.review-transport-envelope.v1", "schema_version": 1,
            "request_sha256": request_sha256, "content_sha256": content_sha256, "response": response,
        }
        (responses_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(envelope), encoding="utf-8")


def _policies():
    return _profile().policies


def _target_profile_root(tmp_path: Path) -> Path:
    """An on-disk trusted checkout carrying the SAME profile content as
    ``_profile()`` (so ``compute_profile_hash_v2`` agrees regardless of
    construction path) plus a matching authoritative-check policy for
    ``pytest`` -- required since `#201-C`'s choke point derives the
    required-check set and re-verifies claims from this root, never from a
    caller-supplied array."""

    profile_root = tmp_path / "target_profile"
    aiops_dir = profile_root / ".aiops"
    aiops_dir.mkdir(parents=True, exist_ok=True)
    (aiops_dir / "target-profile.v2.yaml").write_text(
        f"""schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: example/repo
  default_branch: main
artifacts:
  - artifact_id: full-diff
    path: artifacts/full.diff
    kind: diff
    required: true
    max_bytes: 1000000
budgets:
  max_chunks: 32
  total_prompt_chars: 250000
  max_chars_per_chunk: 24000
  max_files_per_chunk: 50
  max_contracts_per_chunk: 50
must_review:
  paths:
    - app.py
  patterns: []
  artifact_ids: []
  minimum_coverage: complete
policies:
  network_policy: forbidden
  fail_closed: true
  redaction_required: true
  allow_partial_coverage: false
  required_checks:
    - pytest
  allowed_semantic_groups:
    - primary_backend_logic
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts:
  - contract_id: contract.api
    contract_version: "1"
    path: .aiops/domain-contracts.yaml
    sha256: "{'f' * 64}"
    scope: repository
    required: true
limitations: []
""",
        encoding="utf-8",
    )
    (aiops_dir / "authoritative-checks.v2.yaml").write_text(
        """schema_id: agent-review.authoritative-check-policy.v2
schema_version: 2
source: repo-policy
identity:
  repo: example/repo
authoritative_checks:
  - check_name: pytest
    workflow_path: .github/workflows/authoritative-checks.yml
    job_name: authoritative pytest
    verifier_identity: github-actions
    producer_kind: base_owned_workflow_run
    producer_workflow:
      repository: example/repo
      path: .github/workflows/authoritative-checks.yml
      sha: "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"
    producer_workflow_ref: refs/heads/main
    permitted_conclusions:
      - success
      - failure
    origin_rules:
      pull_request: synthetic_merge_parentage
""",
        encoding="utf-8",
    )
    return profile_root


def _empty_authority(tmp_path: Path):
    """The Class A shape every test in this file uses: an empty, honestly
    unestablished required-check submission, verified against the real,
    unpatched `#201-C0` boundary -- never a hand-built claim asserting its
    own authority."""

    from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
    from app.agent_review.contracts_v2 import RunOriginV2
    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import TOOLCHAIN_DIGEST, _snapshot_dict

    # An empty submission never reaches `assemble_authoritative_ci_promotion_v2`
    # at all (`verify_required_check_provenance_set_v2`'s loop is vacuous for
    # an empty `checks`), so the snapshot's own content is irrelevant here --
    # it only needs to be a structurally valid, parseable snapshot.
    origin = RunOriginV2(event_type="pull_request", event_action="synchronize", delivery_id="delivery-1")
    snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))
    return {
        "origin": origin,
        "snapshot": snapshot,
        "toolchain_digest": TOOLCHAIN_DIGEST,
        "target_profile_root": str(_target_profile_root(tmp_path)),
    }


# -- offline transport, full synthetic E2E ------------------------------------


@pytest.mark.requires_network
def test_run_synthetic_review_produces_manual_required_when_authority_is_not_established(tmp_path: Path) -> None:
    """Was: proves a nominal invocation reaches `state: ready` via a
    hand-built green check. Under `#201-C` that check is no longer a
    trusted array -- it is a CLAIM `run_synthetic_review_v2` re-verifies
    against the real, unpatched `#201-C0` boundary itself. No positive
    required-check authority is reachable in production today (see
    `required_check_readiness_v2`'s own module docstring), so the
    genuinely reachable, honest outcome of a clean chunk bind with an
    empty (never fabricated) required-check submission is
    `manual_required` + `policy_failure` -- not `ready`. The composition-
    level proof that a SATISFIED assessment reaches `ready` lives in
    `test_readiness_decision_v2.py`/`test_review_readiness_emission_v2.py`,
    below the authority boundary, where it belongs."""

    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    responses_dir = tmp_path / "responses"
    _write_offline_responses(responses_dir, content=content, manifest=manifest)

    outcome = run_synthetic_review_v2(
        content=content, manifest=manifest, payload_by_chunk_id=payload_by_chunk_id,
        transport=offline_file_transport_v2(responses_dir), policies=_policies(),
        pr_state=PullRequestStateV2.OPEN, checks=(), provenance=(),
        **_empty_authority(tmp_path),
    )

    assert all(o.state == "bound" for o in outcome.chunk_outcomes)
    assert outcome.readiness.run_id == manifest.run_id
    assert outcome.readiness.state is ReadinessStateV2.MANUAL_REQUIRED, outcome.readiness.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in outcome.readiness.reason_codes


@pytest.mark.requires_network
def test_run_synthetic_review_degrades_a_tampered_echo_chunk_to_manual_required(tmp_path: Path) -> None:
    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    responses_dir = tmp_path / "responses"
    tamper_chunk_id = content.chunks[0].chunk_id
    _write_offline_responses(responses_dir, content=content, manifest=manifest, tamper_chunk_id=tamper_chunk_id)

    outcome = run_synthetic_review_v2(
        content=content, manifest=manifest, payload_by_chunk_id=payload_by_chunk_id,
        transport=offline_file_transport_v2(responses_dir), policies=_policies(),
        pr_state=PullRequestStateV2.OPEN, checks=(), provenance=(),
        **_empty_authority(tmp_path),
    )

    tampered = next(o for o in outcome.chunk_outcomes if o.chunk_id == tamper_chunk_id)
    assert tampered.state == "manual_required"
    assert tampered.reason_code == "content_echo_mismatch"
    assert tampered.result is None
    # Never approved: readiness must not be READY when a chunk failed to bind.
    assert outcome.readiness.state is not ReadinessStateV2.READY


@pytest.mark.requires_network
def test_run_synthetic_review_degrades_a_missing_response_file_to_manual_required(tmp_path: Path) -> None:
    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)  # no files written at all

    outcome = run_synthetic_review_v2(
        content=content, manifest=manifest, payload_by_chunk_id=payload_by_chunk_id,
        transport=offline_file_transport_v2(responses_dir), policies=_policies(),
        pr_state=PullRequestStateV2.OPEN, checks=(), provenance=(),
        **_empty_authority(tmp_path),
    )

    assert all(o.state == "manual_required" for o in outcome.chunk_outcomes)
    assert all(o.reason_code == CHUNK_TRANSPORT_FAILURE_REASON_V2 for o in outcome.chunk_outcomes)
    assert outcome.readiness.state is not ReadinessStateV2.READY


@pytest.mark.requires_network
def test_run_synthetic_review_degrades_malformed_json_to_manual_required(tmp_path: Path) -> None:
    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    for chunk in content.chunks:
        (responses_dir / f"{chunk.chunk_id}.json").write_text("{not json", encoding="utf-8")

    outcome = run_synthetic_review_v2(
        content=content, manifest=manifest, payload_by_chunk_id=payload_by_chunk_id,
        transport=offline_file_transport_v2(responses_dir), policies=_policies(),
        pr_state=PullRequestStateV2.OPEN, checks=(), provenance=(),
        **_empty_authority(tmp_path),
    )
    assert all(o.reason_code == CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2 for o in outcome.chunk_outcomes)


def test_offline_file_transport_raises_typed_error_for_a_missing_file(tmp_path: Path) -> None:
    from app.agent_review.review_transport_contract_v2 import ChunkReviewRequestV2, compute_request_sha256_v2

    transport = offline_file_transport_v2(tmp_path / "does-not-exist")
    request = ChunkReviewRequestV2(
        run_id="1" * 64, chunk_id="chunk-0", head_sha="2" * 40, payload_sha256="3" * 64,
        content_sha256="4" * 64,
        request_sha256=compute_request_sha256_v2(
            run_id="1" * 64, chunk_id="chunk-0", head_sha="2" * 40,
            payload_sha256="3" * 64, content_sha256="4" * 64,
        ),
    )
    with pytest.raises(ChunkTransportError) as excinfo:
        transport(request, mock.Mock(), mock.Mock())
    assert excinfo.value.reason_code == CHUNK_TRANSPORT_FAILURE_REASON_V2


# -- agent_router_transport_v2: gating + endpoint lock-down, mocked HTTP only --


def test_agent_router_transport_refuses_with_no_api_key() -> None:
    with pytest.raises(ChunkTransportError) as excinfo:
        agent_router_transport_v2(base_url="https://router.example", api_key="", model="test-model")
    assert excinfo.value.reason_code == ROUTER_DISABLED_REASON_V2


def test_agent_router_transport_refuses_an_unstructured_review_preset() -> None:
    with pytest.raises(ChunkTransportError) as excinfo:
        agent_router_transport_v2(
            base_url="https://router.example",
            api_key="present",
            model="review:critical",
        )
    assert excinfo.value.reason_code == ROUTER_MODEL_UNSUPPORTED_REASON_V2


_ROUTER_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "router_receipt_v2"


def _router_result_document(payload, *, findings: list[dict] | None = None) -> dict:
    return {
        "schema_id": "agent-review.chunk-response.v2",
        "schema_version": 2,
        "summary": "router review complete",
        "findings": findings or [],
        "coverage": payload.coverage.model_dump(mode="json"),
        "limitations": [],
    }


def _fixture_receipt(
    fixture_name: str,
    *,
    request_body: dict,
    assistant_content: str,
) -> dict:
    receipt = json.loads((_ROUTER_FIXTURE_ROOT / fixture_name).read_text(encoding="utf-8"))
    messages_bytes = json.dumps(
        request_body["messages"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    receipt["requested"]["model"] = request_body["model"]
    receipt["received_input"]["sha256"] = hashlib.sha256(messages_bytes).hexdigest()
    receipt["returned_output"]["sha256"] = hashlib.sha256(
        assistant_content.encode("utf-8")
    ).hexdigest()
    receipt["caller_declared_metadata"] = copy.deepcopy(request_body["metadata"])
    return receipt


def _run_mocked_router_chunk(
    tmp_path: Path,
    *,
    fixture_name: str = "local-success-f2a.json",
    receipt_mutator=None,
    response_mutator=None,
    result_mutator=None,
    chunk_content_override=None,
):
    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    chunk_content = chunk_content_override or content.chunks[0]
    payload = payload_by_chunk_id[content.chunks[0].chunk_id]
    result_document = _router_result_document(payload)
    if result_mutator is not None:
        result_mutator(result_document)
    assistant_content = json.dumps(
        result_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    captured: list[tuple[str, dict]] = []

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
        receipt = _fixture_receipt(
            fixture_name,
            request_body=request_body,
            assistant_content=assistant_content,
        )
        if receipt_mutator is not None:
            receipt_mutator(receipt)
        response_body = {
            "id": "chatcmpl-fixture",
            "object": "chat.completion",
            "created": 1,
            "model": "resolved-model-is-not-a-domain-identity",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": assistant_content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "request_id": "router-public-request",
            "inference_receipt": receipt,
        }
        if response_mutator is not None:
            response_mutator(response_body)
        return _FakeResponse(json.dumps(response_body).encode("utf-8"))

    transport = agent_router_transport_v2(
        base_url="https://router.example/",
        api_key="secret-token",
        model="review:code",
    )
    with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        outcome = execute_chunk_review_v2(
            chunk_content,
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )
    return outcome, captured, chunk_content, payload


@pytest.mark.parametrize(
    "fixture_name",
    ["local-success-f2a.json", "provider-fallback-success-f2a.json"],
)
def test_agent_router_receipt_v2_binds_local_and_fallback_f2a_fixtures(
    tmp_path: Path, fixture_name: str
) -> None:
    outcome, captured, _, _ = _run_mocked_router_chunk(
        tmp_path, fixture_name=fixture_name
    )

    assert outcome.state == "bound"
    assert outcome.result is not None
    assert outcome.result.summary == "router review complete"
    assert len(captured) == 1


def test_agent_router_receipt_v2_accepts_the_frozen_optional_f2b_grammar(
    tmp_path: Path,
) -> None:
    def add_f2b(receipt: dict) -> None:
        receipt.update(
            {
                "routing_policy": {
                    "schema": "agent-router.routing-policy-binding.v1",
                    "canonicalization": "agent-router-routing-policy-json.v1",
                    "version": 1,
                    "sha256": "6" * 64,
                    "loader_result": {
                        "schema": "agent-router.routing-policy-loader-result.v1",
                        "disposition": "loaded_repository",
                        "source_kind": "repository_file",
                        "source_id": "review-policy",
                        "selected_policy_sha256": "6" * 64,
                    },
                    "nominal_provider_order": ["local", "openai"],
                },
                "producer": {
                    "schema": "agent-router.producer.v1",
                    "service": "agent-router-api",
                    "version": "1.2.3",
                    "revision": "7" * 40,
                },
                "timing": {
                    "schema": "agent-router.execution-timing.v1",
                    "started_at": "2026-08-26T01:02:03.000Z",
                    "completed_at": "2026-08-26T01:02:03.010Z",
                    "duration_ms": 10,
                    "duration_basis": "monotonic.v1",
                },
                "usage": {
                    "schema": "agent-router.token-usage.v1",
                    "scope": "selected_attempt",
                    "source": "provider_reported",
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "coverage": {"adapter_invocations": 1, "observations": 1},
                },
                "budget": {
                    "schema": "agent-router.token-budget.v1",
                    "scope": "each_adapter_invocation",
                    "source": "router_config",
                    "max_input_tokens": 100,
                    "max_output_tokens": 100,
                },
                "coverage": {
                    "schema": "agent-router.input-coverage.v1",
                    "basis": "router-review-plan.v1",
                    "mode": "single",
                    "chunk_count": 1,
                    "truncated": False,
                },
                "limitations": {
                    "schema": "agent-router.limitations.v1",
                    "codes": ["routing_policy_unobserved"],
                },
            }
        )

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, receipt_mutator=add_f2b
    )

    assert outcome.state == "bound"


def test_agent_router_receipt_v2_rejects_a_fallback_transition_that_lies(
    tmp_path: Path,
) -> None:
    def corrupt_transition(receipt: dict) -> None:
        receipt["routing_execution"]["transitions"][0]["kind"] = "model_fallback"

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path,
        fixture_name="provider-fallback-success-f2a.json",
        receipt_mutator=corrupt_transition,
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == ROUTER_RECEIPT_INVALID_REASON_V2


def test_agent_router_transport_uses_exact_endpoint_messages_metadata_and_json_contract(
    tmp_path: Path,
) -> None:
    outcome, captured, chunk_content, payload = _run_mocked_router_chunk(tmp_path)

    assert outcome.state == "bound"
    assert [item[0] for item in captured] == [
        "https://router.example/v1/chat/completions"
    ]
    body = captured[0][1]
    assert set(body) == {"model", "messages", "metadata", "response_format"}
    assert body["model"] == "review:code"
    assert body["response_format"] == {"type": "json_object"}
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert set(body["metadata"]) == {
        "chunk_id",
        "run_id",
        "payload_sha256",
        "head_sha",
        "content_sha256",
        "request_sha256",
    }
    assert body["metadata"]["payload_sha256"] == payload.payload_sha256
    assert body["metadata"]["content_sha256"] == chunk_content.content_sha256
    user_material = json.loads(body["messages"][1]["content"])
    assert set(user_material) == {
        "semantic_group",
        "coverage",
        "artifact_references",
        "contract_references",
        "chunk_content",
        "output_contract",
    }
    assert "aiops_review_request" not in body
    assert "aiops_review_content" not in body


def test_agent_router_exact_semantic_finding_reaches_the_agentreview_domain(
    tmp_path: Path,
) -> None:
    def add_in_scope_finding(result: dict) -> None:
        result["findings"] = [
            {
                "finding_id": "router-finding-accepted",
                "severity": "P2",
                "title": "accepted semantic finding",
                "file_path": "app.py",
                "line_start": 1,
                "line_end": 1,
                "evidence": "exact assistant content",
                "impact": "domain result reached",
                "confidence": "high",
                "contract_ids": [],
                "disposition": "new",
            }
        ]

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, result_mutator=add_in_scope_finding
    )

    assert outcome.state == "bound"
    assert outcome.result is not None
    assert [finding.finding_id for finding in outcome.result.findings] == [
        "router-finding-accepted"
    ]


@pytest.mark.parametrize(
    ("mutator", "reason_code"),
    [
        (
            lambda receipt: receipt["received_input"].update({"sha256": "0" * 64}),
            ROUTER_INPUT_MISMATCH_REASON_V2,
        ),
        (
            lambda receipt: receipt["returned_output"].update({"sha256": "0" * 64}),
            ROUTER_OUTPUT_MISMATCH_REASON_V2,
        ),
        (
            lambda receipt: receipt["caller_declared_metadata"].update(
                {"request_sha256": "0" * 64}
            ),
            ROUTER_CALLER_BINDING_MISMATCH_REASON_V2,
        ),
        (
            lambda receipt: receipt["requested"].update({"model": "review:deep"}),
            ROUTER_REQUESTED_MODEL_MISMATCH_REASON_V2,
        ),
        (
            lambda receipt: receipt.update(
                {"schema": "agent-router.inference-receipt.v1"}
            ),
            ROUTER_RECEIPT_INVALID_REASON_V2,
        ),
        (
            lambda receipt: receipt.update({"unexpected": True}),
            ROUTER_RECEIPT_INVALID_REASON_V2,
        ),
        (
            lambda receipt: receipt.update({"producer": None}),
            ROUTER_RECEIPT_INVALID_REASON_V2,
        ),
        (
            lambda receipt: receipt["execution"].update({"finish_reason": "length"}),
            ROUTER_FINISH_REASON_INCONCLUSIVE_REASON_V2,
        ),
    ],
)
def test_agent_router_receipt_v2_mutations_fail_closed(
    tmp_path: Path, mutator, reason_code: str
) -> None:
    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, receipt_mutator=mutator
    )

    assert outcome.state == "manual_required"
    assert outcome.result is None
    assert outcome.reason_code == reason_code


def test_agent_router_rejects_old_f1_style_transport_envelope_on_the_http_path(
    tmp_path: Path,
) -> None:
    def replace_with_old_f1_envelope(response_body: dict) -> None:
        response_body.pop("inference_receipt")
        response_body["request_sha256"] = "1" * 64
        response_body["content_sha256"] = "2" * 64

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, response_mutator=replace_with_old_f1_envelope
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == ROUTER_RECEIPT_INVALID_REASON_V2


def test_agent_router_verifies_exact_output_before_parsing_the_domain(
    tmp_path: Path,
) -> None:
    def make_domain_invalid(result: dict) -> None:
        result.pop("coverage")

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, result_mutator=make_domain_invalid
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == ROUTER_RESULT_INVALID_REASON_V2


def test_agent_router_output_identity_precedes_domain_parsing_when_both_fail(
    tmp_path: Path,
) -> None:
    def make_domain_invalid(result: dict) -> None:
        result.pop("coverage")

    def make_output_identity_invalid(receipt: dict) -> None:
        receipt["returned_output"]["sha256"] = "0" * 64

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path,
        result_mutator=make_domain_invalid,
        receipt_mutator=make_output_identity_invalid,
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == ROUTER_OUTPUT_MISMATCH_REASON_V2


def test_agent_router_result_uses_the_common_contract_scope_authority(
    tmp_path: Path,
) -> None:
    def add_out_of_scope_contract_finding(result: dict) -> None:
        result["findings"] = [
            {
                "finding_id": "router-finding-1",
                "severity": "P2",
                "title": "contract scope violation",
                "file_path": "app.py",
                "line_start": 1,
                "line_end": 1,
                "evidence": "fixture evidence",
                "impact": "scope escape",
                "confidence": "high",
                "contract_ids": ["contract.not-supplied"],
                "disposition": "new",
            }
        ]

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, result_mutator=add_out_of_scope_contract_finding
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == "response_scope_mismatch"


def test_payload_content_mismatch_stops_before_message_builder_and_http(
    tmp_path: Path,
) -> None:
    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    payload = payload_by_chunk_id[content.chunks[0].chunk_id]
    mismatched = content.chunks[0].model_copy(
        update={"payload_sha256": "f" * 64}
    )
    mismatched = mismatched.model_copy(
        update={"content_sha256": compute_chunk_content_sha256_v2(mismatched)}
    )
    transport = agent_router_transport_v2(
        base_url="https://router.example",
        api_key="secret-token",
        model="review:code",
    )
    opener = mock.Mock(side_effect=AssertionError("HTTP must not be called"))

    with mock.patch("urllib.request.urlopen", opener):
        outcome = execute_chunk_review_v2(
            mismatched,
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2
    assert opener.call_count == 0


def test_agent_router_transport_maps_5xx_to_unavailable_never_approval(
    tmp_path: Path,
) -> None:
    import urllib.error

    from app.agent_review.review_transport_v2 import CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2

    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    payload = payload_by_chunk_id[content.chunks[0].chunk_id]
    transport = agent_router_transport_v2(
        base_url="https://router.example", api_key="secret", model="review:code"
    )

    def _raise_503(http_request, timeout):
        raise urllib.error.HTTPError("https://router.example/v1/chat/completions", 503, "unavailable", {}, None)

    with mock.patch("urllib.request.urlopen", side_effect=_raise_503):
        outcome = execute_chunk_review_v2(
            content.chunks[0],
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )
    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2


def test_agent_router_transport_maps_invalid_utf8_to_invalid_response(
    tmp_path: Path,
) -> None:
    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    payload = payload_by_chunk_id[content.chunks[0].chunk_id]
    transport = agent_router_transport_v2(
        base_url="https://router.example",
        api_key="secret",
        model="review:code",
    )

    class _InvalidUtf8Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"\xff\xfe"

    with mock.patch("urllib.request.urlopen", return_value=_InvalidUtf8Response()):
        outcome = execute_chunk_review_v2(
            content.chunks[0],
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2
