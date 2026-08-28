"""`#200-D` successor: the operational composition authority (issue #200).

Covers: toolrepo identity gates the whole run before anything else; the
front half composes the existing, UNCHANGED authorities in order; the
payload family is `PayloadBuilderError` ONLY (`PayloadReferenceError` never
caught here -- structural oracle); the back-half error surface catches
exactly the families proven reachable through the CURRENT call graph
(including `LifecycleAggregationError`, reached directly, and
`TargetProfileLoadErrorV2`, reachable a SECOND time through the
required-check re-verification frontier); a programmer defect from the back
half still escapes raw; canonical synthesis is computed exactly once and is
the SAME object readiness was derived from; the target checkout is never
mutated; a real secret-shaped token in the target diff is redacted before it
reaches the outbound Router-format request bytes.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.agent_review import operational_run_v2, review_transport_v2
from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
from app.agent_review.contracts_v2 import PullRequestStateV2, RunOriginV2, compute_response_sha256_v2
from app.agent_review.contracts_v2 import (
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    FindingSeverityV2,
)
from app.agent_review.operational_run_v2 import (
    OperationalRunError,
    run_operational_review_v2,
)
from app.agent_review.review_transport_v2 import (
    _open_agent_router_request_v2,
    agent_router_transport_v2,
    offline_file_transport_v2,
)
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)
from app.agent_review.toolrepo_identity_v2 import TOOLREPO_IDENTITY_MISMATCH_REASON_V2

_BASE_SHA_PLACEHOLDER = None  # replaced per-test with real commit shas


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


_PROFILE_TEXT_TEMPLATE = """schema_id: agent-review.target-profile.v2
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
    sha256: "{contract_sha256}"
    scope: repository
    required: true
limitations: []
"""

_AUTHORITATIVE_CHECKS_TEXT = """schema_id: agent-review.authoritative-check-policy.v2
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
"""


def _grouping_policy() -> SemanticGroupingPolicyV2:
    rule = SemanticGroupingRuleV2(
        rule_id="all", semantic_group="primary_backend_logic", path_patterns=["*"],
        contract_ids=[], artifact_ids=[], priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2,
        "source": "repo-semantic-grouping-policy", "rules": [rule], "fallback_group": None,
    }
    digest = compute_semantic_grouping_policy_sha256_v2({**material, "rules": [rule.model_dump(mode="json")]})
    return SemanticGroupingPolicyV2(**material, policy_sha256=digest)


def _make_target_repo(tmp_path: Path, *, extra_lines: str = "b = CHANGED") -> tuple[Path, str, str]:
    """A real target repo OUTSIDE any toolrepo tree, with `.aiops/target-
    profile.v2.yaml`, `.aiops/domain-contracts.yaml` and `artifacts/full.diff`
    ALL COMMITTED at the returned `head_sha` (so reference_source_v2 can
    materialize them from Git objects)."""

    repo = tmp_path / "target-repo"
    _init_repo(repo)
    (repo / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    (repo / ".aiops").mkdir()
    (repo / ".aiops" / "domain-contracts.yaml").write_bytes(b"rules: []\n")
    contract_sha256 = hashlib.sha256((repo / ".aiops" / "domain-contracts.yaml").read_bytes()).hexdigest()
    (repo / ".aiops" / "target-profile.v2.yaml").write_text(
        _PROFILE_TEXT_TEMPLATE.format(contract_sha256=contract_sha256), encoding="utf-8"
    )
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "full.diff").write_bytes(b"placeholder\n")
    base_sha = _commit_all(repo, "init")

    (repo / "app.py").write_text(f"a = 1\n{extra_lines}\nc = 3\n", encoding="utf-8")
    head_sha = _commit_all(repo, "update")
    return repo, base_sha, head_sha


def _make_trusted_profile_root(tmp_path: Path, *, name: str = "trusted") -> Path:
    """A SEPARATE trusted checkout (never the target's own tree) carrying the
    SAME profile content plus the authoritative-check policy `#201-C0`
    re-derives the required-check set from."""

    profile_root = tmp_path / name
    aiops_dir = profile_root / ".aiops"
    aiops_dir.mkdir(parents=True)
    (aiops_dir / "domain-contracts.yaml").write_bytes(b"rules: []\n")
    contract_sha256 = hashlib.sha256((aiops_dir / "domain-contracts.yaml").read_bytes()).hexdigest()
    (aiops_dir / "target-profile.v2.yaml").write_text(
        _PROFILE_TEXT_TEMPLATE.format(contract_sha256=contract_sha256), encoding="utf-8"
    )
    (aiops_dir / "authoritative-checks.v2.yaml").write_text(_AUTHORITATIVE_CHECKS_TEXT, encoding="utf-8")
    return profile_root


def _empty_snapshot():
    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import _snapshot_dict

    return parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))


def _run_origin() -> RunOriginV2:
    return RunOriginV2(event_type="pull_request", event_action="synchronize", delivery_id="delivery-1")


def _success_envelope_dict(*, run_id: str, chunk_id: str, head_sha: str, payload_sha256: str) -> dict:
    result = {
        "schema_id": "agent-review.chunk-response.v2", "schema_version": 2,
        "summary": "looks fine", "findings": [],
        "coverage": {
            "status": "complete", "expected_files": ["app.py"], "reviewed_files": ["app.py"],
            "partially_reviewed_files": [], "missing_files": [], "must_review_files": ["app.py"],
            "missing_must_review_files": [], "degradation_causes": [],
        },
        "limitations": [],
    }
    envelope = {
        "schema_id": "agent-review.chunk-response-envelope.v2", "schema_version": 2,
        "source": "agent-review-provider-response", "status": "success",
        "run_id": run_id, "chunk_id": chunk_id, "payload_sha256": payload_sha256, "head_sha": head_sha,
        "provider": "test-provider", "model": "test-model", "attempt": 1, "request_id": f"req-{chunk_id}",
        "finish_reason": "stop", "response_received": True, "result": result, "response_sha256": None,
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def _write_offline_responses(responses_dir: Path, *, content, manifest) -> None:
    from app.agent_review.review_transport_contract_v2 import compute_request_sha256_v2

    responses_dir.mkdir(parents=True, exist_ok=True)
    for chunk in content.chunks:
        response = _success_envelope_dict(
            run_id=content.run_id, chunk_id=chunk.chunk_id,
            head_sha=manifest.identity.head_sha, payload_sha256=chunk.payload_sha256,
        )
        request_sha256 = compute_request_sha256_v2(
            run_id=content.run_id, chunk_id=chunk.chunk_id, head_sha=manifest.identity.head_sha,
            payload_sha256=chunk.payload_sha256, content_sha256=chunk.content_sha256,
        )
        envelope = {
            "schema_id": "agent-review.review-transport-envelope.v1", "schema_version": 1,
            "request_sha256": request_sha256, "content_sha256": chunk.content_sha256, "response": response,
        }
        (responses_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(envelope), encoding="utf-8")


def _run_kwargs(*, repo_root: Path, profile_root: Path, base_sha: str, head_sha: str, transport, toolrepo_sha: str):
    return dict(
        repo_root=repo_root,
        target_profile_root=profile_root,
        grouping_policy=_grouping_policy(),
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=head_sha,
        pr_number=1,
        declared_toolrepo_sha=toolrepo_sha,
        evidence_hash="d" * 64,
        transport=transport,
        pr_state=PullRequestStateV2.OPEN,
        origin=_run_origin(),
        snapshot=_empty_snapshot(),
        toolchain_digest="toolchain-digest-0001",
        max_lines_per_chunk=1000,
    )


@pytest.fixture()
def real_toolrepo_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# -- toolrepo identity gates the whole run FIRST ------------------------------


def test_toolrepo_identity_is_checked_before_target_profile_load(tmp_path, real_toolrepo_sha):
    """A wrong `declared_toolrepo_sha` refuses with `toolrepo_identity_
    mismatch` even when `repo_root`/`target_profile_root` point at nothing
    that could otherwise be loaded -- proving toolrepo identity gates
    before the target-side authorities ever run."""

    with pytest.raises(OperationalRunError) as excinfo:
        run_operational_review_v2(
            **_run_kwargs(
                repo_root=tmp_path / "does-not-exist",
                profile_root=tmp_path / "also-does-not-exist",
                base_sha="1" * 40, head_sha="2" * 40,
                transport=offline_file_transport_v2(tmp_path / "responses"),
                toolrepo_sha="a" * 40,
            )
        )
    assert excinfo.value.reason_code == TOOLREPO_IDENTITY_MISMATCH_REASON_V2


# -- full front-to-back composition -------------------------------------------


def test_full_operational_run_reaches_honest_readiness(tmp_path, real_toolrepo_sha):
    """No fabricated required-check authority is reachable in production
    today (see `required_check_readiness_v2`'s own module docstring), so the
    genuinely reachable, honest outcome of a real target checkout, real
    diff, real payload/content, and a clean chunk bind with an EMPTY (never
    fabricated) required-check submission is `manual_required` +
    `policy_failure` -- never a forced `ready`. This is the acceptance
    oracle's honesty control, not a weaker test."""

    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    profile_root = _make_trusted_profile_root(tmp_path)

    # Pass 1: discover chunk ids by running the front half directly, so we
    # can pre-place offline responses before the real composed run.
    from app.agent_review.operational_run_v2 import prepare_operational_review_v2

    prepared = prepare_operational_review_v2(
        repo_root=repo, target_profile_root=profile_root, grouping_policy=_grouping_policy(),
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha=real_toolrepo_sha, evidence_hash="d" * 64, max_lines_per_chunk=1000,
    )
    responses_dir = tmp_path / "responses"
    _write_offline_responses(responses_dir, content=prepared.content, manifest=prepared.manifest)

    outcome = run_operational_review_v2(
        **_run_kwargs(
            repo_root=repo, profile_root=profile_root, base_sha=base_sha, head_sha=head_sha,
            transport=offline_file_transport_v2(responses_dir), toolrepo_sha=real_toolrepo_sha,
        )
    )

    assert outcome.toolrepo_identity.toolrepo_sha == real_toolrepo_sha
    assert all(o.state == "bound" for o in outcome.review.chunk_outcomes)
    assert outcome.review.readiness.state.value == "manual_required"
    assert "policy_failure" in [rc.value for rc in outcome.review.readiness.reason_codes]


def test_target_checkout_is_never_mutated(tmp_path, real_toolrepo_sha):
    """`#200-D` §17: the target's git tree must be byte-identical before and
    after a full operational run."""

    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    profile_root = _make_trusted_profile_root(tmp_path)

    tree_before = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    status_before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout

    from app.agent_review.operational_run_v2 import prepare_operational_review_v2

    prepared = prepare_operational_review_v2(
        repo_root=repo, target_profile_root=profile_root, grouping_policy=_grouping_policy(),
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha=real_toolrepo_sha, evidence_hash="d" * 64, max_lines_per_chunk=1000,
    )
    responses_dir = tmp_path / "responses"
    _write_offline_responses(responses_dir, content=prepared.content, manifest=prepared.manifest)
    run_operational_review_v2(
        **_run_kwargs(
            repo_root=repo, profile_root=profile_root, base_sha=base_sha, head_sha=head_sha,
            transport=offline_file_transport_v2(responses_dir), toolrepo_sha=real_toolrepo_sha,
        )
    )

    tree_after = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    status_after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert tree_after == tree_before
    assert status_after == status_before


# -- canonical synthesis: computed once, same object readiness derives from --


def test_synthesis_is_computed_exactly_once_and_is_the_object_readiness_used(
    tmp_path, real_toolrepo_sha, monkeypatch
):
    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    profile_root = _make_trusted_profile_root(tmp_path)

    from app.agent_review.operational_run_v2 import prepare_operational_review_v2

    prepared = prepare_operational_review_v2(
        repo_root=repo, target_profile_root=profile_root, grouping_policy=_grouping_policy(),
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha=real_toolrepo_sha, evidence_hash="d" * 64, max_lines_per_chunk=1000,
    )
    responses_dir = tmp_path / "responses"
    _write_offline_responses(responses_dir, content=prepared.content, manifest=prepared.manifest)

    call_count = {"synthesize": 0}
    captured = {}
    real_synthesize = review_transport_v2.synthesize_chunk_results_v2
    real_decision = review_transport_v2.compute_readiness_decision_v2

    def counting_synthesize(*args, **kwargs):
        call_count["synthesize"] += 1
        result = real_synthesize(*args, **kwargs)
        captured["synthesis_from_call"] = result
        return result

    def capturing_decision(*, synthesis, **kwargs):
        captured["synthesis_seen_by_decision"] = synthesis
        return real_decision(synthesis=synthesis, **kwargs)

    monkeypatch.setattr(review_transport_v2, "synthesize_chunk_results_v2", counting_synthesize)
    monkeypatch.setattr(review_transport_v2, "compute_readiness_decision_v2", capturing_decision)

    outcome = run_operational_review_v2(
        **_run_kwargs(
            repo_root=repo, profile_root=profile_root, base_sha=base_sha, head_sha=head_sha,
            transport=offline_file_transport_v2(responses_dir), toolrepo_sha=real_toolrepo_sha,
        )
    )

    assert call_count["synthesize"] == 1
    assert outcome.review.synthesis is captured["synthesis_from_call"]
    assert outcome.review.synthesis is captured["synthesis_seen_by_decision"]
    assert outcome.review.synthesis.run_id == outcome.review.readiness.run_id


# -- back-half error surface: measured families, not inherited ---------------


def test_lifecycle_aggregation_error_is_converted_not_raw(tmp_path, real_toolrepo_sha):
    """`LifecycleAggregationError` is reached DIRECTLY by `synthesize_chunk_
    results_v2` (it calls the PRIVATE `_aggregate_finding_lifecycle_core_v2`
    and does not convert it) -- proven here through the REAL composed run,
    not asserted from reading the source alone."""

    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    profile_root = _make_trusted_profile_root(tmp_path)

    from app.agent_review.operational_run_v2 import prepare_operational_review_v2

    prepared = prepare_operational_review_v2(
        repo_root=repo, target_profile_root=profile_root, grouping_policy=_grouping_policy(),
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha=real_toolrepo_sha, evidence_hash="d" * 64, max_lines_per_chunk=1000,
    )
    responses_dir = tmp_path / "responses"
    _write_offline_responses(responses_dir, content=prepared.content, manifest=prepared.manifest)

    def _new_record() -> FindingLifecycleRecordV2:
        return FindingLifecycleRecordV2(
            finding_id="dup-1", severity=FindingSeverityV2.P2,
            observed_at_head_sha=head_sha, disposition=FindingDispositionV2.NEW,
            actionable=True, justification=None, decided_by=None,
            decided_at_head_sha=None, evidence=[], superseded_by=None,
        )

    duplicate_prior = [_new_record(), _new_record()]

    with pytest.raises(OperationalRunError) as excinfo:
        run_operational_review_v2(
            **_run_kwargs(
                repo_root=repo, profile_root=profile_root, base_sha=base_sha, head_sha=head_sha,
                transport=offline_file_transport_v2(responses_dir), toolrepo_sha=real_toolrepo_sha,
            ),
            prior_lifecycle=duplicate_prior,
        )
    assert excinfo.value.reason_code == "duplicate_prior_lifecycle_finding"


def test_back_half_programmer_defect_escapes_raw(tmp_path, real_toolrepo_sha, monkeypatch):
    """A defect that is NOT one of the documented back-half families (here:
    simulated by making the back half itself raise a bare `TypeError`) must
    escape `run_operational_review_v2` uncaught -- never become an
    `OperationalRunError`."""

    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    profile_root = _make_trusted_profile_root(tmp_path)

    def _boom(**kwargs):
        raise TypeError("simulated programmer defect")

    monkeypatch.setattr(operational_run_v2, "run_synthetic_review_v2", _boom)

    with pytest.raises(TypeError):
        run_operational_review_v2(
            **_run_kwargs(
                repo_root=repo, profile_root=profile_root, base_sha=base_sha, head_sha=head_sha,
                transport=offline_file_transport_v2(tmp_path / "responses"), toolrepo_sha=real_toolrepo_sha,
            )
        )


def test_missing_required_artifact_is_a_typed_refusal_not_raw(tmp_path, real_toolrepo_sha):
    """A required artifact absent from head_sha's Git tree must surface as
    the payload owner's own typed refusal through this composer -- never as
    a raw `PayloadReferenceError` (the sibling family the owner already
    converts) and never as a traceback. This exercises the REAL composed
    path, not just the structural (AST-level) oracle."""

    repo, base_sha, head_sha = _make_target_repo(tmp_path)
    # Remove the required artifact from the tree entirely at a new head.
    (repo / "artifacts" / "full.diff").unlink()
    head_sha_missing = _commit_all(repo, "remove required artifact")
    profile_root = _make_trusted_profile_root(tmp_path)

    with pytest.raises(OperationalRunError) as excinfo:
        run_operational_review_v2(
            **_run_kwargs(
                repo_root=repo, profile_root=profile_root, base_sha=base_sha, head_sha=head_sha_missing,
                transport=offline_file_transport_v2(tmp_path / "responses"), toolrepo_sha=real_toolrepo_sha,
            )
        )
    assert excinfo.value.reason_code == "payload_required_artifact_missing"


# -- structural oracles --------------------------------------------------------


def test_operational_run_v2_never_catches_forbidden_families():
    """AST-level structural oracle: `operational_run_v2.py` must never
    catch `pydantic.ValidationError`, a raw `OSError`, the sibling
    `PayloadReferenceError` (already converted by its owner), `Exception`,
    or `BaseException`."""

    source = Path(operational_run_v2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"ValidationError", "OSError", "PayloadReferenceError", "Exception", "BaseException"}
    caught_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            for t in types:
                if isinstance(t, ast.Name):
                    caught_names.add(t.id)
                elif isinstance(t, ast.Attribute):
                    caught_names.add(t.attr)
    assert not (caught_names & forbidden), caught_names & forbidden


def test_operational_run_v2_never_inspects_dynamic_reason_code():
    source = Path(operational_run_v2.__file__).read_text(encoding="utf-8")
    assert "getattr(exc" not in source
    assert "getattr(exc," not in source.replace(" ", "")


# -- router-format redaction proof (offline seam, never a live call) ---------


def test_secret_in_the_real_target_diff_never_reaches_the_outbound_request(tmp_path, monkeypatch):
    """A token-shaped literal in the REAL target diff must be redacted
    before it reaches the ACTUAL outbound Router-format request bytes --
    non-vacuously: the sanitized line must still be present (not deleted
    entirely), proving redaction, not blanket omission."""

    repo, base_sha, head_sha = _make_target_repo(
        tmp_path, extra_lines='token = "sk-live-should-never-leak-1234567890"'
    )
    profile_root = _make_trusted_profile_root(tmp_path)

    from app.agent_review.operational_run_v2 import prepare_operational_review_v2

    prepared = prepare_operational_review_v2(
        repo_root=repo, target_profile_root=profile_root, grouping_policy=_grouping_policy(),
        base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha, pr_number=1,
        toolrepo_sha="a" * 40, evidence_hash="d" * 64, max_lines_per_chunk=1000,
    )

    captured_bytes: list[bytes] = []

    class _FakeResponse:
        status = 200

        def read(self):
            return b'{"choices": [{"message": {"content": "{}"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_open(http_request, timeout_seconds):
        captured_bytes.append(http_request.data)
        return _FakeResponse()

    monkeypatch.setattr(review_transport_v2, "_open_agent_router_request_v2", _fake_open)

    transport = agent_router_transport_v2(
        base_url="https://router.example.invalid", api_key="test-key", model="review:code"
    )
    for chunk in prepared.content.chunks:
        payload = prepared.payload_by_chunk_id[chunk.chunk_id]
        from app.agent_review.review_transport_v2 import build_chunk_review_request_v2

        request = build_chunk_review_request_v2(chunk, run_id=prepared.content.run_id, head_sha=head_sha)
        try:
            transport(request, chunk, payload)
        except Exception:
            pass  # the fake response is not a valid parse target; only the outbound bytes matter here

    assert captured_bytes, "the transport must have attempted at least one outbound request"
    outbound_text = b"".join(captured_bytes).decode("utf-8", errors="replace")
    assert "sk-live-should-never-leak-1234567890" not in outbound_text
    assert "token" in outbound_text  # the sanitized line survives -- redaction, not deletion
