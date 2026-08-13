from __future__ import annotations

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
    ChunkTransportError,
    agent_router_transport_v2,
    offline_file_transport_v2,
    run_synthetic_review_v2,
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
        transport(request, mock.Mock())
    assert excinfo.value.reason_code == CHUNK_TRANSPORT_FAILURE_REASON_V2


# -- agent_router_transport_v2: gating + endpoint lock-down, mocked HTTP only --


def test_agent_router_transport_refuses_with_no_api_key() -> None:
    with pytest.raises(ChunkTransportError) as excinfo:
        agent_router_transport_v2(base_url="https://router.example", api_key="", model="test-model")
    assert excinfo.value.reason_code == ROUTER_DISABLED_REASON_V2


def test_agent_router_transport_calls_exactly_the_chat_completions_endpoint() -> None:
    from app.agent_review.review_transport_contract_v2 import ChunkReviewRequestV2, compute_request_sha256_v2

    request = ChunkReviewRequestV2(
        run_id="1" * 64, chunk_id="chunk-0", head_sha="2" * 40, payload_sha256="3" * 64,
        content_sha256="4" * 64,
        request_sha256=compute_request_sha256_v2(
            run_id="1" * 64, chunk_id="chunk-0", head_sha="2" * 40,
            payload_sha256="3" * 64, content_sha256="4" * 64,
        ),
    )
    response_body = _success_envelope_dict(
        run_id="1" * 64, chunk_id="chunk-0", head_sha="2" * 40, payload_sha256="3" * 64,
    )
    envelope_json = json.dumps(
        {
            "schema_id": "agent-review.review-transport-envelope.v1", "schema_version": 1,
            "request_sha256": request.request_sha256, "content_sha256": "4" * 64,
            "response": response_body,
        }
    ).encode("utf-8")

    captured_requests = []

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return envelope_json

    def _fake_urlopen(http_request, timeout):
        captured_requests.append(http_request.full_url)
        return _FakeResponse()

    transport = agent_router_transport_v2(
        base_url="https://router.example/", api_key="secret-token", model="test-model"
    )
    with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        envelope = transport(request, mock.Mock(model_dump=lambda mode: {}))

    assert captured_requests == ["https://router.example/v1/chat/completions"]
    assert isinstance(envelope, ChunkReviewTransportEnvelopeV1)


def test_agent_router_transport_maps_5xx_to_unavailable_never_approval() -> None:
    import urllib.error

    from app.agent_review.review_transport_contract_v2 import ChunkReviewRequestV2, compute_request_sha256_v2
    from app.agent_review.review_transport_v2 import CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2

    request = ChunkReviewRequestV2(
        run_id="1" * 64, chunk_id="chunk-0", head_sha="2" * 40, payload_sha256="3" * 64,
        content_sha256="4" * 64,
        request_sha256=compute_request_sha256_v2(
            run_id="1" * 64, chunk_id="chunk-0", head_sha="2" * 40,
            payload_sha256="3" * 64, content_sha256="4" * 64,
        ),
    )
    transport = agent_router_transport_v2(base_url="https://router.example", api_key="secret", model="m")

    def _raise_503(http_request, timeout):
        raise urllib.error.HTTPError("https://router.example/v1/chat/completions", 503, "unavailable", {}, None)

    with mock.patch("urllib.request.urlopen", side_effect=_raise_503):
        with pytest.raises(ChunkTransportError) as excinfo:
            transport(request, mock.Mock(model_dump=lambda mode: {}))
    assert excinfo.value.reason_code == CHUNK_TRANSPORT_UNAVAILABLE_REASON_V2
