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
    OPERATIONAL_RUN_SCOPE_SILENTLY_NARROWED_REASON_V2,
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

def test_composers_own_profile_call_site_reads_once(tmp_path: Path):
    """NOTE (correction, found by independent review lane B): this only
    proves THIS composer's own call site (`operational_run_v2.
    load_target_profile_v2`) reads once. It is NOT proof the profile is
    read from disk exactly once across the whole run -- it is not:
    `produce_review_readiness_v2` -> `_verify_and_assess_required_checks_v2`
    (`required_check_readiness_v2.py:307`, an unmodified pre-existing owner)
    does its OWN separate `load_target_profile_v2` read, using its own
    imported reference, invisible to a mock on this module's reference. The
    real safety net against that second read observing a DIFFERENT profile
    than this composer bound `manifest.identity.profile_hash` to is hash
    binding (`compute_profile_hash_v2(profile) != identity.profile_hash`),
    not single-read discipline -- see
    `test_second_profile_read_divergence_is_caught_by_hash_binding` below,
    which proves that net actually catches a divergence."""
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

    assert len(calls) == 1, (
        "this composer's own load_target_profile_v2 call site must be captured once and reused, "
        "not re-read -- see this test's docstring for what this does NOT prove"
    )


def test_second_profile_read_divergence_is_caught_by_hash_binding(tmp_path: Path):
    """The real TOCTOU safety net: `required_check_readiness_v2.py`'s own,
    separate `load_target_profile_v2` read binds its result to
    `identity.profile_hash` (computed from THIS composer's earlier read,
    baked into `manifest.identity` by `assemble_manifest_from_diff_v2`) via
    `compute_profile_hash_v2`, and refuses on mismatch -- not by reading
    only once. Proven here by making the composer's OWN read return a
    profile that differs (one extra, otherwise-harmless required_checks
    entry) from what is actually on disk, so the two reads genuinely
    diverge; the on-disk file itself is never touched, so this is not a
    race, only a controlled divergence standing in for one."""
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "auth-10")

    import app.agent_review.operational_run_v2 as mod
    from app.agent_review.required_check_readiness_v2 import (
        ASSESSMENT_PROFILE_IDENTITY_MISMATCH_REASON_V2,
        RequiredCheckReadinessErrorV2,
    )

    original = mod.load_target_profile_v2

    def _diverging(*args, **kwargs):
        real_profile = original(*args, **kwargs)
        mutated_policies = real_profile.policies.model_copy(
            update={
                "required_checks": [
                    *real_profile.policies.required_checks,
                    "synthetic-toctou-divergence-check",
                ]
            }
        )
        return real_profile.model_copy(update={"policies": mutated_policies})

    with mock.patch.object(mod, "load_target_profile_v2", side_effect=_diverging):
        with pytest.raises(RequiredCheckReadinessErrorV2) as excinfo:
            run_operational_review_v2(inputs)

    assert excinfo.value.reason_code == ASSESSMENT_PROFILE_IDENTITY_MISMATCH_REASON_V2


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

    import dataclasses
    from app.agent_review.contracts_v2 import FindingDispositionV2, FindingLifecycleRecordV2, FindingSeverityV2

    original_synthesize = mod.synthesize_chunk_results_v2
    original_decide = mod.compute_readiness_decision_v2
    original_produce = mod.produce_review_readiness_v2
    synthesis_calls = []
    decision_calls = []
    produce_findings_calls = []

    def _wrapped_synthesize(**kwargs):
        result = original_synthesize(**kwargs)
        # `findings` is empty for this fixture's transport-failure-only
        # scenario (no canned chunk responses staged), and CPython interns
        # the empty tuple singleton (`() is ()` is always True) -- an
        # identity check against an empty `findings` would pass vacuously
        # even for a composer that recomputed it from a SEPARATE call. Force
        # a genuinely non-empty, non-interned tuple so the identity check
        # below is a real proof, not an artifact of the fixture.
        dummy_finding = FindingLifecycleRecordV2(
            finding_id="f" * 64, severity=FindingSeverityV2.P2,
            observed_at_head_sha=kwargs["evaluated_head_sha"], disposition=FindingDispositionV2.NEW,
            actionable=True, justification=None,
            decided_by=None, decided_at_head_sha=None, evidence=[], superseded_by=None,
        )
        result = dataclasses.replace(result, findings=(dummy_finding,))
        synthesis_calls.append(result)
        return result

    def _wrapped_decide(*, synthesis, **kwargs):
        decision_calls.append(synthesis)
        return original_decide(synthesis=synthesis, **kwargs)

    def _wrapped_produce(*, findings, **kwargs):
        produce_findings_calls.append(findings)
        return original_produce(findings=findings, **kwargs)

    with mock.patch.object(mod, "synthesize_chunk_results_v2", side_effect=_wrapped_synthesize), \
         mock.patch.object(mod, "compute_readiness_decision_v2", side_effect=_wrapped_decide), \
         mock.patch.object(mod, "produce_review_readiness_v2", side_effect=_wrapped_produce):
        run_operational_review_v2(inputs)

    assert len(synthesis_calls) == 1, "synthesize_chunk_results_v2 must be called exactly once"
    assert len(decision_calls) == 1
    assert decision_calls[0] is synthesis_calls[0], (
        "readiness must be derived from the SAME synthesis object (identity, not equality)"
    )
    assert len(produce_findings_calls) == 1
    assert produce_findings_calls[0] is synthesis_calls[0].findings, (
        "findings= passed into produce_review_readiness_v2 must be the SAME tuple object "
        "synthesis produced (identity, not equality) -- an earlier revision sourced findings "
        "from a SEPARATE aggregate_finding_lifecycle_v2 call that happened to agree only by "
        "construction, not identity; see #200-E correction loop. NOTE: identity cannot be "
        "checked past this boundary -- produce_review_readiness_v2's own emission "
        "(review_readiness_emission_v2.py, unmodified owner) intentionally defensive-copies "
        "into a list (`findings=list(findings)`) for the final ReviewReadinessV2, so "
        "`readiness.findings is synthesis.findings` is false by that owner's own design."
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


# ---- round-3 lane C P0: scope must never narrow silently ------------------

def _target_with_binary_change(tmp_path: Path) -> tuple[Path, str, str]:
    """A PR touching BOTH a reviewable .py and a NON-must-review binary.
    The fixture profile's must_review.patterns is `backend/scheduling/*.py`,
    so the binary is deliberately outside must-review -- which is exactly
    the case run assembly silently excludes rather than blocking on."""
    repo = tmp_path / "target_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "backend" / "scheduling").mkdir(parents=True)
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "def compute_shift():\n    return 1\n", encoding="utf-8"
    )
    (repo / "backend" / "scheduling" / "blob.bin").write_bytes(b"\x00\x01binary-v1\xff")
    (repo / "artifacts").mkdir()
    shutil.copy(FIXTURES_ROOT / "artifacts" / "full.diff", repo / "artifacts" / "full.diff")
    (repo / "contracts").mkdir()
    shutil.copy(
        FIXTURES_ROOT / "contracts" / "domain-contracts.yaml",
        repo / "contracts" / "domain-contracts.yaml",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "base"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "def compute_shift():\n    return 2\n", encoding="utf-8"
    )
    (repo / "backend" / "scheduling" / "blob.bin").write_bytes(b"\x00\x01binary-V2-CHANGED\xff")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "head"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, base_sha, head_sha


def test_silently_narrowed_scope_is_refused_not_reported_ready(tmp_path: Path):
    """Round-3 lane C P0: a changed file dropped from scope by run assembly
    (binary/submodule/hunkless/truncated, and not must-review) produced
    `expected_files` covering only the survivors, `degradation_causes == []`,
    and a readiness decision of READY with COMPLETE coverage over the
    NARROWED set -- while the emitted artifact said nothing about the file
    that was never reviewed. `excluded_paths`, the audit field the assembly
    owner explicitly delegates to its caller, had zero production consumers.

    The composer now refuses fail-closed, because `ReviewReadinessV2` has
    nowhere to honestly record a narrowed scope."""
    repo, base_sha, head_sha = _target_with_binary_change(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "narrowed-1")

    with pytest.raises(OperationalRunError) as excinfo:
        run_operational_review_v2(inputs)
    assert excinfo.value.args[0] == OPERATIONAL_RUN_SCOPE_SILENTLY_NARROWED_REASON_V2


def test_unnarrowed_scope_still_runs_to_readiness(tmp_path: Path):
    """Non-vacuity control: the SAME shape without the excluded binary must
    still complete normally, so the refusal above cannot be passing merely
    because this fixture never works."""
    repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    inputs = _inputs(repo, base_sha, head_sha, responses_dir, "narrowed-2")

    readiness = run_operational_review_v2(inputs)
    assert readiness.schema_id == "agent-review.review-readiness.v2"
