from __future__ import annotations

import copy
import hashlib
import http.client
import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
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
from app.agent_review.authoritative_diff_identity_v2 import (
    acquire_authoritative_diff_with_identity_v2,
    bind_manifest_to_diff_identity_v2,
)
from app.agent_review.diff_acquisition_v2 import acquire_authoritative_diff_v2
from app.agent_review.payload_builder_v2 import build_chunk_payload_v2
from app.agent_review.review_content_extraction_v2 import extract_review_content_v2
from app.agent_review.review_transport_contract_v2 import ChunkReviewTransportEnvelopeV1
from app.agent_review.review_transport_v2 import (
    CHUNK_TRANSPORT_FAILURE_REASON_V2,
    CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2,
    CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2,
    ROUTER_DISABLED_REASON_V2,
    ROUTER_MODEL_UNSUPPORTED_REASON_V2,
    ChunkTransportError,
    _NoRedirectHandlerV2,
    _open_agent_router_request_v2,
    agent_router_transport_v2,
    execute_chunk_review_v2,
    offline_file_transport_v2,
    run_synthetic_review_v2,
)
from app.agent_review._router_receipt_v2 import (
    ROUTER_CALLER_BINDING_MISMATCH_REASON_V2,
    _canonical_messages_bytes_v2,
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
from app.agent_review.schemas import ChunkResponse
from app.agent_review.versioning import (
    MIXED_CONTRACT_VERSIONS_REASON_V2,
    UNSUPPORTED_CONTRACT_VERSION_REASON_V2,
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
    file_diffs, _diff_text, acquired_identity = acquire_authoritative_diff_with_identity_v2(
        repo, base_sha=base_sha, head_sha=head_sha
    )
    outcome = assemble_manifest_from_diff_v2(
        file_diffs, profile=profile, grouping_policy=_grouping_policy(),
        repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
        tested_merge_sha=head_sha, toolrepo_sha="b" * 40, evidence_hash="c" * 64,
        max_lines_per_chunk=1000,
    )
    assert outcome.state == "assembled", outcome.blocked_reason
    manifest = outcome.manifest
    manifest_diff_binding = bind_manifest_to_diff_identity_v2(manifest, acquired_identity)
    payload_by_chunk_id = {c.chunk_id: build_chunk_payload_v2(manifest, c) for c in manifest.chunks}
    content = extract_review_content_v2(
        repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
        manifest_diff_binding=manifest_diff_binding,
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


def test_agent_router_redirect_handler_refuses_before_a_second_request() -> None:
    request = urllib.request.Request(
        "https://router.example/v1/chat/completions",
        data=b"{}",
        method="POST",
        headers={"Authorization": "Bearer test-token"},
    )

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _NoRedirectHandlerV2().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://other-origin.example/collect",
        )

    assert excinfo.value.code == 302
    assert excinfo.value.url == request.full_url


def test_agent_router_http_boundary_installs_the_no_redirect_handler() -> None:
    request = urllib.request.Request(
        "https://router.example/v1/chat/completions",
        data=b"{}",
        method="POST",
    )
    response = object()
    opener = mock.Mock()
    opener.open.return_value = response

    with mock.patch("urllib.request.build_opener", return_value=opener) as build:
        observed = _open_agent_router_request_v2(request, 3.0)

    assert observed is response
    assert isinstance(build.call_args.args[0], _NoRedirectHandlerV2)
    opener.open.assert_called_once_with(request, timeout=3.0)


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
    raw_response_mutator=None,
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
        raw_response = json.dumps(response_body)
        if raw_response_mutator is not None:
            raw_response = raw_response_mutator(raw_response)
        return _FakeResponse(raw_response.encode("utf-8"))

    transport = agent_router_transport_v2(
        base_url="https://router.example/",
        api_key="secret-token",
        model="review:code",
    )
    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        side_effect=_fake_urlopen,
    ):
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
        (
            lambda receipt: receipt["routing_execution"]["attempts"][0].update(
                {"finish_reason": "length"}
            ),
            ROUTER_FINISH_REASON_INCONCLUSIVE_REASON_V2,
        ),
        (
            lambda receipt: receipt["routing_execution"]["attempts"][0].pop(
                "finish_reason"
            ),
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


@pytest.mark.parametrize("invalid_index", [False, 0.0])
def test_agent_router_requires_an_actual_integer_zero_choice_index(
    tmp_path: Path,
    invalid_index: object,
) -> None:
    def replace_choice_index(response_body: dict) -> None:
        response_body["choices"][0]["index"] = invalid_index

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, response_mutator=replace_choice_index
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == ROUTER_RECEIPT_INVALID_REASON_V2


def test_agent_router_rejects_duplicate_keys_inside_receipt_identity(
    tmp_path: Path,
) -> None:
    def duplicate_received_input_sha256(raw_response: str) -> str:
        return raw_response.replace(
            '"sha256": "',
            f'"sha256": "{"0" * 64}", "sha256": "',
            1,
        )

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, raw_response_mutator=duplicate_received_input_sha256
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2


@pytest.mark.parametrize(
    ("existing_member", "duplicate_members"),
    [
        ('"findings":[]', '"findings":[],"findings":[]'),
        ('"status":"complete"', '"status":"complete","status":"complete"'),
    ],
)
def test_agent_router_rejects_duplicate_keys_inside_assistant_domain_json(
    tmp_path: Path,
    existing_member: str,
    duplicate_members: str,
) -> None:
    def duplicate_domain_member(raw_response: str) -> str:
        response_body = json.loads(raw_response)
        old_content = response_body["choices"][0]["message"]["content"]
        new_content = old_content.replace(
            existing_member, duplicate_members, 1
        )
        assert new_content != old_content
        response_body["choices"][0]["message"]["content"] = new_content
        response_body["inference_receipt"]["returned_output"]["sha256"] = (
            hashlib.sha256(new_content.encode("utf-8")).hexdigest()
        )
        return json.dumps(response_body)

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, raw_response_mutator=duplicate_domain_member
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == ROUTER_RESULT_INVALID_REASON_V2


def test_agent_router_converts_deep_assistant_json_to_typed_rejection(
    tmp_path: Path,
) -> None:
    def deeply_nest_assistant_json(raw_response: str) -> str:
        response_body = json.loads(raw_response)
        nesting = sys.getrecursionlimit() + 100
        assistant_content = "[" * nesting + "0" + "]" * nesting
        response_body["choices"][0]["message"]["content"] = assistant_content
        response_body["inference_receipt"]["returned_output"]["sha256"] = (
            hashlib.sha256(assistant_content.encode("utf-8")).hexdigest()
        )
        return json.dumps(response_body)

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, raw_response_mutator=deeply_nest_assistant_json
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == ROUTER_RESULT_INVALID_REASON_V2


def test_agent_router_converts_deep_outer_json_to_typed_rejection(
    tmp_path: Path,
) -> None:
    def deeply_nest_outer_json(raw_response: str) -> str:
        nesting = sys.getrecursionlimit() + 100
        deep_value = "[" * nesting + "0" + "]" * nesting
        return f'{{"deep":{deep_value},' + raw_response[1:]

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, raw_response_mutator=deeply_nest_outer_json
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2


@pytest.mark.parametrize("unpaired_surrogate", ["\ud800", "\udfff"])
def test_agent_router_converts_non_utf8_assistant_content_to_typed_rejection(
    tmp_path: Path,
    unpaired_surrogate: str,
) -> None:
    def replace_assistant_content(response_body: dict) -> None:
        response_body["choices"][0]["message"]["content"] = unpaired_surrogate

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, response_mutator=replace_assistant_content
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == ROUTER_OUTPUT_MISMATCH_REASON_V2


@pytest.mark.parametrize(
    "incomplete_declaration",
    ["truncated", "limitation", "both"],
)
def test_agent_router_rejects_explicit_incomplete_input_coverage(
    tmp_path: Path,
    incomplete_declaration: str,
) -> None:
    def declare_incomplete_coverage(receipt: dict) -> None:
        if incomplete_declaration in {"truncated", "both"}:
            receipt["coverage"] = {
                "schema": "agent-router.input-coverage.v1",
                "basis": "router-review-plan.v1",
                "mode": "single",
                "chunk_count": 1,
                "truncated": True,
            }
        if incomplete_declaration in {"limitation", "both"}:
            receipt["limitations"] = {
                "schema": "agent-router.limitations.v1",
                "codes": ["coverage_incomplete"],
            }

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, receipt_mutator=declare_incomplete_coverage
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

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        opener,
    ):
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

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        side_effect=_raise_503,
    ):
        outcome = execute_chunk_review_v2(
            content.chunks[0],
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )
    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2


def test_agent_router_transport_maps_truncated_body_to_invalid_response(
    tmp_path: Path,
) -> None:
    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    payload = payload_by_chunk_id[content.chunks[0].chunk_id]
    transport = agent_router_transport_v2(
        base_url="https://router.example",
        api_key="secret",
        model="review:code",
    )

    class _TruncatedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise http.client.IncompleteRead(b'{"partial":', 10)

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        return_value=_TruncatedResponse(),
    ):
        outcome = execute_chunk_review_v2(
            content.chunks[0],
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2


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

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        return_value=_InvalidUtf8Response(),
    ):
        outcome = execute_chunk_review_v2(
            content.chunks[0],
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2


# -- #200-C-WIRE P1 (PR #270 comment 3860453638): the canonical version selector
# -- must gate the Router's semantic response before v2 domain parsing ------


def _v1_chunk_response_document() -> dict:
    """A *valid* v1 ``ChunkResponse`` (app/agent_review/schemas.py).

    v1 never declares ``schema_id``; ``schema_version == 1`` alone is its
    complete canonical marker. Its field values are deliberately independent
    of the v2 payload: the version gate must fire on the markers alone,
    before anything binds this document to a chunk.
    """

    return {
        "schema_version": 1,
        "chunk_id": "chunk-0001",
        "semantic_group": "primary_backend_logic",
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "coverage_notes": {
            "files_reviewed": [],
            "files_partial": [],
            "files_not_reviewed": [],
        },
    }


def test_agent_router_reports_a_valid_v1_semantic_response_as_mixed_versions(
    tmp_path: Path,
) -> None:
    """R-VERSION-1: a *known* v1 document under a v2 request/payload is a
    version disagreement, not an unparseable v2 document.

    Reporting ``router_result_invalid`` here destroys information the
    canonical selector already established.
    """

    v1_document = _v1_chunk_response_document()

    def replace_with_valid_v1(result: dict) -> None:
        result.clear()
        result.update(v1_document)

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, result_mutator=replace_with_valid_v1
    )

    # the document really is a valid v1 ChunkResponse, not merely v1-shaped
    ChunkResponse.model_validate(v1_document)

    assert outcome.state == "manual_required"
    assert outcome.reason_code == MIXED_CONTRACT_VERSIONS_REASON_V2


def test_agent_router_reports_an_unknown_semantic_response_as_unsupported(
    tmp_path: Path,
) -> None:
    """R-VERSION-4: a foreign/future result marker is *unsupported*, never
    mixed -- mixed would claim knowledge of a contract we do not have."""

    def replace_with_unknown_version(result: dict) -> None:
        result["schema_id"] = "agent-review.chunk-response.v9"
        result["schema_version"] = 9

    outcome, _, _, _ = _run_mocked_router_chunk(
        tmp_path, result_mutator=replace_with_unknown_version
    )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == UNSUPPORTED_CONTRACT_VERSION_REASON_V2


def test_agent_router_version_gate_precedes_the_v2_domain_parser(
    tmp_path: Path,
) -> None:
    """R-VERSION-6: mechanical ordering proof.

    For a v1 response the ``ChunkReviewResultV2`` validator must never be
    reached -- version selection precedes domain interpretation.
    """

    v1_document = _v1_chunk_response_document()

    def replace_with_valid_v1(result: dict) -> None:
        result.clear()
        result.update(v1_document)

    domain_parse_calls: list[object] = []
    real_validate = ChunkReviewResultV2.model_validate_json

    def _recording_validate(*args: object, **kwargs: object):
        domain_parse_calls.append(args)
        return real_validate(*args, **kwargs)

    with mock.patch.object(
        ChunkReviewResultV2, "model_validate_json", _recording_validate
    ):
        outcome, _, _, _ = _run_mocked_router_chunk(
            tmp_path, result_mutator=replace_with_valid_v1
        )

    assert outcome.reason_code == MIXED_CONTRACT_VERSIONS_REASON_V2
    assert domain_parse_calls == []


def test_agent_router_v2_semantic_result_still_passes_the_version_gate(
    tmp_path: Path,
) -> None:
    """R-VERSION-2: the gate admits the Router's own v2 result artifact.

    ``agent-review.chunk-response.v2`` is a second legitimate v2 response
    artifact alongside the historical envelope; recognising it must not
    require a second, Router-local selector.
    """

    outcome, _, _, payload = _run_mocked_router_chunk(tmp_path)

    assert outcome.state == "bound"
    assert outcome.reason_code is None
    assert outcome.result is not None


# -- C5-F1: the Router body-acquisition boundary must be total ------------


@pytest.mark.parametrize(
    "read_error",
    [
        pytest.param(ConnectionResetError(104, "Connection reset by peer"), id="connection_reset"),
        pytest.param(ssl.SSLError(1, "decryption failed or bad record mac"), id="ssl_error"),
        pytest.param(ConnectionAbortedError(103, "Software caused connection abort"), id="connection_aborted"),
        pytest.param(OSError(5, "Input/output error"), id="generic_oserror"),
    ],
)
def test_agent_router_transport_types_socket_read_failures(
    tmp_path: Path, read_error: OSError
) -> None:
    """R-C5-1/R-C5-2: a socket/SSL failure raised by ``response.read()``.

    ``ConnectionResetError`` and ``ssl.SSLError`` are ``OSError`` subclasses
    but neither ``URLError`` nor ``TimeoutError``, so they matched none of the
    existing handlers and escaped ``execute_chunk_review_v2`` untyped --
    aborting a whole multi-chunk review instead of degrading one chunk.
    """

    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    payload = payload_by_chunk_id[content.chunks[0].chunk_id]
    transport = agent_router_transport_v2(
        base_url="https://router.example",
        api_key="secret",
        model="review:code",
    )

    class _FailingReadResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise read_error

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        return_value=_FailingReadResponse(),
    ):
        outcome = execute_chunk_review_v2(
            content.chunks[0],
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2


def test_agent_router_transport_types_http_protocol_read_failures(
    tmp_path: Path,
) -> None:
    """The same boundary, protocol side: a non-``IncompleteRead``
    ``HTTPException`` is a defective *response*, keeping ``IncompleteRead``'s
    established ``transport_invalid_response`` semantics rather than the
    connection-level ``transport_unavailable``."""

    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    payload = payload_by_chunk_id[content.chunks[0].chunk_id]
    transport = agent_router_transport_v2(
        base_url="https://router.example",
        api_key="secret",
        model="review:code",
    )

    class _BadStatusLineResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise http.client.LineTooLong("header line")

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        return_value=_BadStatusLineResponse(),
    ):
        outcome = execute_chunk_review_v2(
            content.chunks[0],
            run_id=content.run_id,
            head_sha=manifest.identity.head_sha,
            payload=payload,
            transport=transport,
        )

    assert outcome.state == "manual_required"
    assert outcome.reason_code == CHUNK_TRANSPORT_INVALID_RESPONSE_REASON_V2


@pytest.mark.parametrize(
    "programmer_error",
    [
        pytest.param(TypeError("not an operational failure"), id="type_error"),
        pytest.param(AttributeError("not an operational failure"), id="attribute_error"),
        pytest.param(MemoryError(), id="memory_error"),
    ],
)
def test_agent_router_transport_does_not_swallow_programmer_errors(
    tmp_path: Path, programmer_error: BaseException
) -> None:
    """Totalizing the *operational* boundary must not become a blanket
    ``except Exception``: a programmer error stays a crash, not a sanitized
    ``manual_required`` that would hide a defect behind a review verdict."""

    manifest, content, payload_by_chunk_id = _build_repo_manifest_and_content(tmp_path)
    payload = payload_by_chunk_id[content.chunks[0].chunk_id]
    transport = agent_router_transport_v2(
        base_url="https://router.example",
        api_key="secret",
        model="review:code",
    )

    class _ProgrammerErrorResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise programmer_error

    with mock.patch(
        "app.agent_review.review_transport_v2._open_agent_router_request_v2",
        return_value=_ProgrammerErrorResponse(),
    ):
        with pytest.raises(type(programmer_error)):
            execute_chunk_review_v2(
                content.chunks[0],
                run_id=content.run_id,
                head_sha=manifest.identity.head_sha,
                payload=payload,
                transport=transport,
            )


# -- C5-F2: Router-authored conformance oracle for openai-messages-json.v1 --


def _router_authored_vector() -> dict:
    """The committed cross-repository conformance vector.

    Its ``expected_sha256`` was produced by executing the Router authority's
    own ``canonicalize_openai_messages`` at
    ``mglpsw/agent-router-api@80e921df`` -- NOT by this repository's
    ``_canonical_messages_bytes_v2``. Deriving the expectation from the
    implementation under test is exactly the defect this closes.
    """

    return json.loads(
        (_ROUTER_FIXTURE_ROOT / "openai_messages_json_v1_vector.json").read_text(
            encoding="utf-8"
        )
    )


def test_router_vector_declares_its_authority_and_is_provider_free() -> None:
    vector = _router_authored_vector()
    assert vector["authority"] == {
        "repository": "mglpsw/agent-router-api",
        "sha": "80e921dfc28436bd4fed8a4e1fa72ffaa168d10c",
        "function": "canonicalize_openai_messages",
        "source_path": "app/agent_router/inference_receipt.py",
        "source_blob_oid": "70071b049640998e1dad0dd0c24aa3dfd0f3e9bb",
        "canonicalization": "openai-messages-json.v1",
    }
    assert vector["generation"]["live_router_call"] is False
    assert vector["generation"]["provider_call"] is False


def test_aiops_messages_canonicalization_matches_the_router_authored_vector() -> None:
    """R-C5-3: interoperability, not self-consistency.

    ``_canonical_messages_bytes_v2`` must reproduce the Router's frozen
    ``openai-messages-json.v1`` bytes exactly; otherwise every live chunk
    fails ``router_input_mismatch`` while this repository's own suite stays
    green.
    """

    vector = _router_authored_vector()
    expected_text: str = vector["expected_canonical_text"]
    expected_sha256: str = vector["expected_sha256"]

    produced = _canonical_messages_bytes_v2(vector["messages"])

    assert produced == expected_text.encode("utf-8")
    assert hashlib.sha256(produced).hexdigest() == expected_sha256


def test_router_vector_discriminates_the_plausible_canonicalization_variants() -> None:
    """R-C5-4/5/6 as an oracle-quality proof.

    A vector that hashed identically under ``ensure_ascii=True``,
    ``sort_keys=False`` or default separators could not detect a regression
    in the implementation it is supposed to police.
    """

    vector = _router_authored_vector()
    messages = vector["messages"]
    expected_sha256 = vector["expected_sha256"]

    variants = {
        "ensure_ascii=True": json.dumps(
            messages, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ),
        "sort_keys=False": json.dumps(
            messages, ensure_ascii=False, sort_keys=False,
            separators=(",", ":"), allow_nan=False,
        ),
        "default separators": json.dumps(
            messages, ensure_ascii=False, sort_keys=True, allow_nan=False,
        ),
        "reversed message order": json.dumps(
            list(reversed(messages)), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ),
    }
    for label, serialized in variants.items():
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        assert digest != expected_sha256, f"vector cannot discriminate {label}"

    # a non-UTF-8 codec must also change the digest
    assert (
        hashlib.sha256(vector["expected_canonical_text"].encode("utf-16")).hexdigest()
        != expected_sha256
    )


def test_router_vector_expectation_is_not_derived_from_the_implementation() -> None:
    """M_C5_TEST_RECOMPUTES_EXPECTED_WITH_AIOPS_IMPLEMENTATION.

    Structural guard: the fixture must carry a literal expected digest. If a
    future edit replaced it with a value computed from
    ``_canonical_messages_bytes_v2``, the oracle would silently become a
    tautology and this file would still pass every other assertion.
    """

    vector = _router_authored_vector()

    # the expectation is a literal, not a computed value
    digest = vector["expected_sha256"]
    assert isinstance(digest, str)
    assert len(digest) == 64
    assert all(character in "0123456789abcdef" for character in digest)

    # and it is attributed to the Router, not to this repository. Only the
    # explanatory `_comment` may name the AgentReview implementation; no
    # data field may, because that would mean the vector was derived from it.
    data_fields = {key: value for key, value in vector.items() if key != "_comment"}
    assert "_canonical_messages_bytes_v2" not in json.dumps(data_fields)
    assert vector["authority"]["repository"] == "mglpsw/agent-router-api"
