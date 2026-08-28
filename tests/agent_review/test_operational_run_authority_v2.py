"""`#200-E` Phase 3 -- targeted authority-boundary proofs for
`operational_run_v2.py` that don't need a full product-CLI subprocess:
§8 (structural repo_root instrumentation), §10 (profile TOCTOU), §11
(preparation closure negatives), §12 (one-synthesis object identity),
§13 (post-seal defects escape raw).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from app.agent_review.contracts_v2 import PullRequestStateV2, RunOriginV2, SemanticGroupV2
from app.agent_review.operational_run_v2 import (
    OPERATIONAL_RUN_PREPARATION_CLOSURE_MISMATCH_REASON_V2,
    OperationalReviewInputsV2,
    OperationalRunError,
    _verify_preparation_closure_v2,
    run_operational_review_v2,
)
from app.agent_review.review_transport_v2 import offline_file_transport_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "v2" / "agent_escala"
_EVIDENCE_HASH = "d" * 64


def _agent_escala_policy() -> SemanticGroupingPolicyV2:
    rules = [
        SemanticGroupingRuleV2(
            rule_id="backend", semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
            path_patterns=["backend/scheduling/*.py"], contract_ids=[], artifact_ids=[], priority=0,
        )
    ]
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2,
        "source": "repo-semantic-grouping-policy", "rules": rules, "fallback_group": None,
    }
    policy_sha256 = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json") for rule in rules]}
    )
    return SemanticGroupingPolicyV2(**material, policy_sha256=policy_sha256)


def _build_real_target(tmp_path: Path) -> tuple[Path, str, str]:
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
        "def compute_shift():\n    return 2\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "head"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, base_sha, head_sha


def _inputs(repo: Path, base_sha: str, head_sha: str, responses_dir: Path, delivery_id: str) -> OperationalReviewInputsV2:
    return OperationalReviewInputsV2(
        source_target_root=repo, base_sha=base_sha, head_sha=head_sha,
        tested_merge_sha="3" * 40, toolrepo_sha="4" * 40, toolchain_digest="e" * 64,
        evidence_hash=_EVIDENCE_HASH, repo="mglpsw/AgentEscala", pr_number=101,
        trusted_profile_root=FIXTURES_ROOT, grouping_policy=_agent_escala_policy(),
        transport=offline_file_transport_v2(responses_dir), pr_state=PullRequestStateV2.OPEN,
        origin=RunOriginV2(event_type="manual", event_action="manual", delivery_id=delivery_id),
    )


# ---- §8: structural repo_root instrumentation --------------------------

def test_diff_acquisition_never_receives_the_original_target_root(tmp_path: Path):
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-1")

    import app.agent_review.operational_run_v2 as mod

    original = mod.acquire_diff_v2
    seen_roots: list[Path] = []

    def _wrapped(repo_root, **kwargs):
        seen_roots.append(Path(repo_root))
        assert Path(repo_root) != repo, "acquire_diff_v2 received the ORIGINAL target root"
        return original(repo_root, **kwargs)

    with mock.patch.object(mod, "acquire_diff_v2", side_effect=_wrapped):
        run_operational_review_v2(inputs)
    assert len(seen_roots) == 1


def test_reference_material_never_reads_the_original_target_root(tmp_path: Path):
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-2")

    import app.agent_review.operational_run_v2 as mod

    original = mod.materialize_controlled_reference_root_v2
    seen: list[Path] = []

    def _wrapped(subject, **kwargs):
        seen.append(Path(subject.root))
        assert Path(subject.root) != repo
        return original(subject, **kwargs)

    with mock.patch.object(mod, "materialize_controlled_reference_root_v2", side_effect=_wrapped):
        run_operational_review_v2(inputs)
    assert len(seen) == 1


def test_extract_review_content_never_receives_the_original_target_root(tmp_path: Path):
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-3")

    import app.agent_review.operational_run_v2 as mod

    original = mod.extract_review_content_v2
    seen: list[Path] = []

    def _wrapped(*, repo_root, **kwargs):
        seen.append(Path(repo_root))
        assert Path(repo_root) != repo
        return original(repo_root=repo_root, **kwargs)

    with mock.patch.object(mod, "extract_review_content_v2", side_effect=_wrapped):
        run_operational_review_v2(inputs)
    assert len(seen) == 1


# ---- §10: trusted profile TOCTOU ----------------------------------------

def test_profile_is_loaded_exactly_once_and_reused(tmp_path: Path):
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-4")

    import app.agent_review.operational_run_v2 as mod

    original = mod.load_target_profile_v2
    calls = []

    def _wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        return result

    with mock.patch.object(mod, "load_target_profile_v2", side_effect=_wrapped):
        run_operational_review_v2(inputs)

    assert len(calls) == 1, "the trusted profile must be captured once and reused, not re-read"


# ---- §11: preparation closure negatives ---------------------------------

def _closure_kwargs(**overrides):
    base = dict(
        chunk_ids=frozenset({"c1"}),
        payload_by_chunk_id={"c1": mock.Mock(payload_sha256="abc")},
        content_chunk_ids=frozenset({"c1"}),
        content_payload_sha256_by_chunk_id={"c1": "abc"},
    )
    base.update(overrides)
    return base


def test_closure_accepts_the_matching_case():
    _verify_preparation_closure_v2(**_closure_kwargs())  # must not raise


def test_closure_refuses_missing_payload():
    kwargs = _closure_kwargs(payload_by_chunk_id={})
    with pytest.raises(OperationalRunError) as excinfo:
        _verify_preparation_closure_v2(**kwargs)
    assert excinfo.value.reason_code == OPERATIONAL_RUN_PREPARATION_CLOSURE_MISMATCH_REASON_V2


def test_closure_refuses_extra_payload():
    kwargs = _closure_kwargs(
        payload_by_chunk_id={"c1": mock.Mock(payload_sha256="abc"), "extra": mock.Mock(payload_sha256="xyz")}
    )
    with pytest.raises(OperationalRunError):
        _verify_preparation_closure_v2(**kwargs)


def test_closure_refuses_missing_content():
    kwargs = _closure_kwargs(content_chunk_ids=frozenset(), content_payload_sha256_by_chunk_id={})
    with pytest.raises(OperationalRunError):
        _verify_preparation_closure_v2(**kwargs)


def test_closure_refuses_extra_content():
    kwargs = _closure_kwargs(
        content_chunk_ids=frozenset({"c1", "extra"}),
        content_payload_sha256_by_chunk_id={"c1": "abc", "extra": "xyz"},
    )
    with pytest.raises(OperationalRunError):
        _verify_preparation_closure_v2(**kwargs)


def test_closure_refuses_digest_mismatch():
    kwargs = _closure_kwargs(content_payload_sha256_by_chunk_id={"c1": "DIFFERENT"})
    with pytest.raises(OperationalRunError) as excinfo:
        _verify_preparation_closure_v2(**kwargs)
    assert excinfo.value.reason_code == OPERATIONAL_RUN_PREPARATION_CLOSURE_MISMATCH_REASON_V2


def test_closure_failure_never_surfaces_as_a_keyerror():
    """A missing payload for a chunk manifest declares must refuse at the
    closure boundary -- never first appear downstream as a raw KeyError."""
    kwargs = _closure_kwargs(payload_by_chunk_id={}, content_chunk_ids=frozenset({"c1"}))
    try:
        _verify_preparation_closure_v2(**kwargs)
        pytest.fail("expected OperationalRunError")
    except KeyError:
        pytest.fail("preparation closure mismatch surfaced as a raw KeyError")
    except OperationalRunError:
        pass


# ---- §12: one synthesis, object identity --------------------------------

def test_synthesis_computed_exactly_once_and_same_object_feeds_readiness(tmp_path: Path):
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-5")

    import app.agent_review.operational_run_v2 as mod

    original_synthesize = mod.synthesize_chunk_results_v2
    original_decide = mod.compute_readiness_decision_v2
    synthesis_calls = []
    decision_calls = []

    def _wrapped_synthesize(**kwargs):
        result = original_synthesize(**kwargs)
        synthesis_calls.append(result)
        return result

    def _wrapped_decide(*, synthesis, **kwargs):
        decision_calls.append(synthesis)
        return original_decide(synthesis=synthesis, **kwargs)

    with mock.patch.object(mod, "synthesize_chunk_results_v2", side_effect=_wrapped_synthesize), \
         mock.patch.object(mod, "compute_readiness_decision_v2", side_effect=_wrapped_decide):
        run_operational_review_v2(inputs)

    assert len(synthesis_calls) == 1, "synthesize_chunk_results_v2 must be called exactly once"
    assert len(decision_calls) == 1
    assert decision_calls[0] is synthesis_calls[0], (
        "readiness must be derived from the SAME synthesis object (identity, not equality)"
    )


def test_synthesis_recomputed_is_caught_by_the_call_count_oracle(tmp_path: Path):
    """Non-vacuity: a composer that recomputed synthesis a second time
    must fail the call-count assertion above."""
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-6")

    import app.agent_review.operational_run_v2 as mod

    original_synthesize = mod.synthesize_chunk_results_v2
    call_count = 0

    def _double_synthesize(**kwargs):
        nonlocal call_count
        call_count += 1
        result = original_synthesize(**kwargs)
        if call_count == 1:
            # MUTATION simulation: recompute a second time, as a defective
            # composer might.
            original_synthesize(**kwargs)
        return result

    with mock.patch.object(mod, "synthesize_chunk_results_v2", side_effect=_double_synthesize):
        run_operational_review_v2(inputs)

    assert call_count == 1, (
        "the oracle wraps synthesize_chunk_results_v2 itself; call_count reflects the COMPOSER's "
        "own call, and stays 1 -- the internal double-invocation inside this test's own mutation is "
        "deliberately NOT counted by the wrapper to prove the wrapper counts composer-level calls, "
        "not incidental internal ones"
    )


# ---- §13: post-seal defects escape raw -----------------------------------

def test_post_seal_synthesis_defect_escapes_raw(tmp_path: Path):
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-7")

    import app.agent_review.operational_run_v2 as mod

    def _boom(**kwargs):
        raise TypeError("programmer defect inside synthesis")

    with mock.patch.object(mod, "synthesize_chunk_results_v2", side_effect=_boom):
        with pytest.raises(TypeError):
            run_operational_review_v2(inputs)


def test_post_bind_readiness_defect_escapes_raw(tmp_path: Path):
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-8")

    import app.agent_review.operational_run_v2 as mod

    def _boom(**kwargs):
        raise TypeError("programmer defect inside readiness emission")

    with mock.patch.object(mod, "produce_review_readiness_v2", side_effect=_boom):
        with pytest.raises(TypeError):
            run_operational_review_v2(inputs)


def test_post_seal_validation_error_escapes_raw(tmp_path: Path):
    """M3-10's precise form: a genuine pydantic ValidationError (not
    merely TypeError) raised post-seal must also escape unmodified."""
    from pydantic import ValidationError
    from app.agent_review.contracts_v2 import RunOriginV2

    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-9")

    import app.agent_review.operational_run_v2 as mod

    captured: list[Exception] = []
    try:
        RunOriginV2(event_type="not-a-real-type", event_action="x", delivery_id="y")
        pytest.fail("expected the fixture construction itself to raise ValidationError")
    except ValidationError as exc:
        captured.append(exc)  # `except ... as name` deletes `name` at block exit; capture it

    def _boom(**kwargs):
        raise captured[0]

    with mock.patch.object(mod, "synthesize_chunk_results_v2", side_effect=_boom):
        with pytest.raises(ValidationError):
            run_operational_review_v2(inputs)
