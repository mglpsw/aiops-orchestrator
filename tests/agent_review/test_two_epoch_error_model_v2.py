"""Two-epoch owner validation (`#200-D` predecessor, model A*).

## The law

    G0 = caller-controlled / external / environment-derived material

    G0 -> owner validation / parsing / acquisition classification
       -> V = validated owner material
       -> SEAL
       -> internal derivation
       -> output

Only failures BEFORE the seal may be converted from generic parsing,
validation or I/O mechanics into an owner operational refusal. After the seal,
a `ValidationError` means code that had already accepted valid inputs derived
an invalid object -- a repository defect. Sanitizing it would report our bug to
an operator as their review outcome.

## Why the previous model was falsified

`#272`'s first architecture converted `ValidationError` at each authority's
OUTER boundary. That closed the positive direction and made the negative
direction impossible: the outer boundary cannot distinguish "the caller's
material violates this contract" from "our derivation built a malformed
object", because both arrive as the same type from the same call. Head
`56b4a874` is preserved as the falsified subject.

## What these tests must do that the previous controls did not

The earlier programmer-defect controls used `TypeError`/`AttributeError`/... and
omitted `ValidationError` -- the type that carries most internal defects here.
They also injected before any seal. Both directions are covered here, and every
defect injection happens with VALID caller material, strictly after that
material has crossed its seal.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import SemanticGroupV2
from app.agent_review.diff_acquisition_v2 import acquire_authoritative_diff_v2
from app.agent_review.payload_builder_v2 import (
    PayloadBuilderError,
    build_chunk_payloads_from_profile_v2,
)
from app.agent_review.payload_set_emission_v2 import emit_payload_set_v2
from app.agent_review.payload_set_v2 import PayloadSetBindingError
from app.agent_review.profile_loader_v2 import load_target_profile_v2
from app.agent_review.review_content_extraction_v2 import (
    ExtractionBlockedError,
    extract_review_content_v2,
)
from app.agent_review.run_assembly_v2 import (
    RunAssemblyError,
    assemble_manifest_from_diff_v2,
)
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)

from tests.agent_review.test_authority_error_surfaces_v2 import (
    _PROFILE,
    _assembled,
    _git,
    _grouping_policy,
    _repo,
)


def _defect() -> ValidationError:
    """A `ValidationError` standing in for an internal derivation bug."""

    return ValidationError.from_exception_data("InternalDerivation", [])


# -- assembly ---------------------------------------------------------------


def test_assembly_post_seal_validation_error_is_a_raw_defect(tmp_path: Path) -> None:
    """Caller identity and budget are valid and have crossed the seal; the
    planner then fails to derive a valid object. That is our bug."""

    repo, base_sha, head_sha = _repo(tmp_path)
    profile = load_target_profile_v2(repo)
    diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    with mock.patch(
        "app.agent_review.run_assembly_v2.plan_lossless_chunks_v2",
        side_effect=_defect(),
    ):
        with pytest.raises(ValidationError):
            assemble_manifest_from_diff_v2(
                diffs, profile=profile, grouping_policy=_grouping_policy(),
                repo="example/repo", pr_number=1, base_sha=base_sha,
                head_sha=head_sha, tested_merge_sha=head_sha,
                toolrepo_sha="b" * 40, evidence_hash="c" * 64,
                max_lines_per_chunk=1000,
            )


@pytest.mark.parametrize(
    ("override", "why"),
    [
        pytest.param({"toolrepo_sha": "z" * 40}, "sha not hex", id="toolrepo_sha"),
        pytest.param({"evidence_hash": "z" * 64}, "hash not hex", id="evidence_hash"),
        pytest.param({"max_lines_per_chunk": 0}, "budget < 1", id="budget"),
    ],
)
def test_assembly_pre_seal_caller_material_is_a_typed_refusal(
    tmp_path: Path, override: dict, why: str
) -> None:
    """The opposite direction, same module: invalid CALLER material is an
    operational refusal, established before the planner is reached."""

    repo, base_sha, head_sha = _repo(tmp_path)
    profile = load_target_profile_v2(repo)
    diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    kwargs = dict(
        profile=profile, grouping_policy=_grouping_policy(), repo="example/repo",
        pr_number=1, base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha,
        toolrepo_sha="b" * 40, evidence_hash="c" * 64, max_lines_per_chunk=1000,
    )
    kwargs.update(override)

    reached: list[str] = []
    with mock.patch(
        "app.agent_review.run_assembly_v2.plan_lossless_chunks_v2",
        side_effect=lambda *a, **k: reached.append("planner"),
    ):
        with pytest.raises(RunAssemblyError) as excinfo:
            assemble_manifest_from_diff_v2(diffs, **kwargs)
    assert excinfo.value.reason_code, why
    assert reached == [], "caller material must be refused before derivation"


# -- payload builder ---------------------------------------------------------


def test_payload_post_seal_validation_error_is_a_raw_defect(tmp_path: Path) -> None:
    """References acquired and validated; payload derivation then fails."""

    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)

    with mock.patch(
        "app.agent_review.payload_builder_v2.compute_payload_sha256_v2",
        side_effect=_defect(),
    ):
        with pytest.raises(ValidationError):
            build_chunk_payloads_from_profile_v2(
                manifest, profile=profile, repo_root=repo
            )


def test_payload_pre_seal_external_failure_is_a_typed_refusal(tmp_path: Path) -> None:
    """A declared required artifact that is not on disk -- external material,
    owned before the seal."""

    demanding = _PROFILE.replace(
        "artifacts: []",
        "artifacts:\n  - artifact_id: full-diff\n    path: artifacts/full.diff\n"
        "    kind: diff\n    required: true\n    max_bytes: 1000000",
    )
    repo, base_sha, head_sha = _repo(tmp_path, demanding)
    profile, manifest = _assembled(repo, base_sha, head_sha)

    with pytest.raises(PayloadBuilderError) as excinfo:
        build_chunk_payloads_from_profile_v2(manifest, profile=profile, repo_root=repo)
    assert excinfo.value.reason_code == "payload_required_artifact_missing"


# -- payload set -------------------------------------------------------------


def test_payload_set_post_seal_validation_error_is_a_raw_defect(
    tmp_path: Path,
) -> None:
    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )

    with mock.patch(
        "app.agent_review.payload_set_emission_v2.compute_payload_set_sha256_v2",
        side_effect=_defect(),
    ):
        with pytest.raises(ValidationError):
            emit_payload_set_v2(manifest, [item.payload for item in built])


def test_payload_set_pre_seal_invalid_input_is_a_typed_refusal(
    tmp_path: Path,
) -> None:
    repo, base_sha, head_sha = _repo(tmp_path)
    _, manifest = _assembled(repo, base_sha, head_sha)

    with pytest.raises(PayloadSetBindingError) as excinfo:
        emit_payload_set_v2(manifest, [])
    assert excinfo.value.reason_code


# -- content -----------------------------------------------------------------


def test_content_post_seal_validation_error_is_a_raw_defect(tmp_path: Path) -> None:
    """Diff acquired, redacted and DLP-checked; content derivation then fails."""

    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )

    with mock.patch(
        "app.agent_review.review_content_extraction_v2.compute_review_content_sha256_v2",
        side_effect=_defect(),
    ):
        with pytest.raises(ValidationError):
            extract_review_content_v2(
                repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
                payload_sha256_by_chunk_id={
                    item.chunk_id: item.payload.payload_sha256 for item in built
                },
                target_profile=profile,
            )


def test_content_pre_seal_unrepresentable_material_is_a_typed_refusal(
    tmp_path: Path,
) -> None:
    """The CRLF witness from review round 2 stays a permanent discriminator:
    external content the fragment contract cannot represent is an extraction
    refusal, and the reviewed bytes never appear in it."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".aiops").mkdir()
    (repo / ".aiops" / "target-profile.v2.yaml").write_text(_PROFILE, encoding="utf-8")
    (repo / ".gitattributes").write_text("* -text\n", encoding="utf-8")
    (repo / "app.py").write_bytes(b"a = 1\r\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_bytes(b"a = 2\r\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "head")
    head_sha = _git(repo, "rev-parse", "HEAD")

    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )

    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id={
                item.chunk_id: item.payload.payload_sha256 for item in built
            },
            target_profile=profile,
        )
    reason = excinfo.value.reason_code
    assert reason
    assert "a = 1" not in reason and "a = 2" not in reason and "\r" not in reason


# -- readiness ---------------------------------------------------------------


def test_readiness_post_seal_defect_is_STILL_LAUNDERED_falsifier() -> None:
    """FALSIFIER for the two-epoch model at this authority. Read the assertion
    carefully: it pins the CURRENT, WRONG behaviour on purpose.

    An earlier version of this test mocked `compute_run_id` to raise, which
    fires at the pre-seal provenance check three statements before the `try`.
    It therefore never crossed the seal and stayed green even when the
    constructor handler was widened to `except Exception` -- the second time
    in this branch that a control failed to test what it claimed.

    Driven properly, `compute_run_id` returns a wrong-but-well-formed sha so
    the provenance check passes and the CONTRACT's derivation-coherence check
    (`run_id` vs `identity`) fails inside the constructor. That is a
    repository defect, and it comes back as
    `ReadinessEmissionError(readiness_material_invalid)` -- delivered to an
    operator as a gate refusal.

    WHY THIS IS NOT FIXABLE BY ANOTHER ROUND HERE.
    `ReviewReadinessV2.validate_state_invariants` checks CALLER material
    (pr_state, checks, blocker/finding linkage) and DERIVATION coherence
    (`run_id`, `evaluated_run_id`, `head_sha`, `evaluated_head_sha`, all
    computed by this function) in one validator. A single construction-site
    conversion cannot separate them, and enumerating the caller-material
    invariants pre-seal is the recurrence this design exists to end.
    Separating them means splitting that validator -- a contract change, and a
    decision this grant did not scope.
    """

    from unittest import mock as _mock

    from tests.agent_review.test_review_readiness_emission_v2 import (
        _assemble_review_readiness_v2,
        _fully_reviewed_manifest_and_report,
        _green_check,
        _policies,
        _synthesis,
    )
    from app.agent_review.contracts_v2 import PullRequestStateV2, compute_run_id
    from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
    from app.agent_review.review_readiness_emission_v2 import (
        READINESS_MATERIAL_INVALID_REASON_V2,
        ReadinessEmissionError,
    )

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )

    real = compute_run_id
    calls = {"n": 0}

    def _buggy_derivation(identity):
        calls["n"] += 1
        return real(identity) if calls["n"] <= 1 else "a" * 64

    with _mock.patch(
        "app.agent_review.review_readiness_emission_v2.compute_run_id",
        side_effect=_buggy_derivation,
    ):
        with pytest.raises(ReadinessEmissionError) as excinfo:
            _assemble_review_readiness_v2(
                decision=decision, findings=synthesis.findings,
                identity=manifest.identity, evaluated_identity=manifest.identity,
                pr_state=PullRequestStateV2.OPEN,
                checks=[_green_check(manifest.identity.head_sha)],
            )

    # Pinned deliberately: a defect surfacing as an operational refusal. When
    # the next grant splits the validator, this assertion must be inverted to
    # `pytest.raises(ValidationError)` -- and that inversion is the acceptance
    # criterion for the fix.
    assert excinfo.value.reason_code == READINESS_MATERIAL_INVALID_REASON_V2


@pytest.mark.parametrize(
    ("pr_state_name", "with_checks"),
    [("MERGED", True), ("OPEN", False)],
)
def test_readiness_pre_seal_invalid_combination_is_a_typed_refusal(
    pr_state_name: str, with_checks: bool
) -> None:
    """`ready` + merged PR, and `ready` without green checks, are CALLER-visible
    invalid combinations. They must be refused before the artifact is built --
    and with reasons an operator can tell apart."""

    from tests.agent_review.test_review_readiness_emission_v2 import (
        _assemble_review_readiness_v2,
        _fully_reviewed_manifest_and_report,
        _green_check,
        _policies,
        _synthesis,
    )
    from app.agent_review.contracts_v2 import PullRequestStateV2, ReadinessStateV2
    from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
    from app.agent_review.review_readiness_emission_v2 import ReadinessEmissionError

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )
    assert decision.state is ReadinessStateV2.READY

    with pytest.raises(ReadinessEmissionError) as excinfo:
        _assemble_review_readiness_v2(
            decision=decision, findings=synthesis.findings,
            identity=manifest.identity, evaluated_identity=manifest.identity,
            pr_state=getattr(PullRequestStateV2, pr_state_name),
            checks=[_green_check(manifest.identity.head_sha)] if with_checks else [],
        )
    assert excinfo.value.reason_code


# -- review round 1 on the two-epoch model -----------------------------------


def test_readiness_pre_seal_sees_blocking_findings_not_just_blockers() -> None:
    """R1: the pre-seal predicate was NARROWER than the validator's.

    It read `decision.reason_codes or decision.blockers` while the contract
    counts reason codes, ACTIVE blockers and blocking FINDINGS -- and findings
    arrive by a separate argument. A blocking finding therefore slipped past
    the seal and surfaced as a raw `ValidationError` from construction.

    Root cause was the helper taking a pre-computed boolean, which let its two
    callers derive it differently. The helper now owns the derivation, so this
    asserts they cannot diverge again.
    """

    import inspect

    from app.agent_review.contracts_v2 import evaluate_ready_preconditions_v2

    params = set(inspect.signature(evaluate_ready_preconditions_v2).parameters)
    assert {"reason_codes", "blockers", "findings"} <= params
    assert "has_reasons_or_blockers" not in params, (
        "a pre-computed predicate lets the two callers diverge -- that is the bug"
    )


def test_ready_precondition_authority_has_exactly_two_callers() -> None:
    """One authority, both consumers: the artifact's own validator and the
    emission owner. A third derivation of these rules anywhere would be the
    duplication this design exists to prevent."""

    from pathlib import Path as _Path

    from app.agent_review import contracts_v2

    # Scan the whole package. Filtering a hard-coded two-module tuple could
    # never have found the third call site this test claims to guard against.
    package_root = _Path(contracts_v2.__file__).parent
    call_sites = sorted(
        source.name
        for source in package_root.glob("*.py")
        if "evaluate_ready_preconditions_v2(" in source.read_text(encoding="utf-8")
    )
    assert call_sites == [
        "contracts_v2.py",
        "review_readiness_emission_v2.py",
    ], call_sites


def test_optional_unrepresentable_fragment_degrades_instead_of_aborting(
    tmp_path: Path,
) -> None:
    """R1: the pre-seal representability check raised unconditionally, unlike
    the five neighbouring branches, so one CRLF byte in an OPTIONAL context
    fragment killed the whole extraction. Required fragments still block."""

    import inspect

    from app.agent_review import review_content_extraction_v2 as module

    source = inspect.getsource(module._build_fragment_content_v2)
    marker = source.index("_is_fragment_content_representable_v2")
    tail = source[marker:]
    assert "if fragment.coverage_required:" in tail
    assert "ReviewContentPolicyV2.UNREPRESENTABLE" in tail


def test_repo_root_that_is_a_file_is_named_precisely(tmp_path: Path) -> None:
    """The `is_dir()` pre-probe earns its place here, not by inference.

    Once `shutil.which` decides "git missing" vs "checkout missing" directly,
    the probe is no longer needed for THAT distinction. It still is for a root
    that exists but is not a directory: `subprocess` raises
    `NotADirectoryError`, which is an `OSError` but not a `FileNotFoundError`,
    so without the probe it would degrade to the generic acquisition-I/O
    reason instead of naming the checkout.
    """

    from app.agent_review.diff_acquisition_v2 import (
        REPO_ROOT_UNUSABLE_REASON_V2,
        DiffAcquisitionError,
    )

    not_a_dir = tmp_path / "not_a_dir"
    not_a_dir.write_text("i am a file", encoding="utf-8")

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_authoritative_diff_v2(
            not_a_dir, base_sha="a" * 40, head_sha="b" * 40
        )
    assert excinfo.value.reason_code == REPO_ROOT_UNUSABLE_REASON_V2


def test_readiness_non_ready_invariant_is_typed_and_leaks_nothing() -> None:
    """R3: only the five `ready` preconditions were pre-sealed, so the
    contract's OTHER caller-visible invariants escaped raw -- carrying
    `input_value`, i.e. the whole readiness dict including finding text.

    Enumerating those invariants one at a time would have repeated the
    recurrence. Scoping the conversion to the construction site closes them
    together, while `compute_run_id` stays outside so derivation defects
    still escape.
    """

    from tests.agent_review.test_review_readiness_emission_v2 import (
        _assemble_review_readiness_v2,
        _fully_reviewed_manifest_and_report,
        _green_check,
        _policies,
        _synthesis,
    )
    from app.agent_review.contracts_v2 import PullRequestStateV2
    from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
    from app.agent_review.review_readiness_emission_v2 import (
        READINESS_MATERIAL_INVALID_REASON_V2,
        ReadinessEmissionError,
    )

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )
    # a check bound to a DIFFERENT head: a caller-visible invariant with no
    # `ready`-precondition of its own
    with pytest.raises(ReadinessEmissionError) as excinfo:
        _assemble_review_readiness_v2(
            decision=decision, findings=synthesis.findings,
            identity=manifest.identity, evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.OPEN,
            checks=[_green_check("f" * 40)],
        )
    reason = excinfo.value.reason_code
    assert reason in {
        READINESS_MATERIAL_INVALID_REASON_V2,
        "ready_requires_green_checks",
    }
    assert "input_value" not in reason and "{" not in reason


def test_caller_payload_digest_values_are_validated_pre_seal(tmp_path: Path) -> None:
    """R3: only the map's KEYS were checked, so a malformed VALUE reached
    `ChunkContentV2` and escaped past this authority's own epoch-1 boundary."""

    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)

    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id={
                chunk.chunk_id: "NOT-A-SHA" for chunk in manifest.chunks
            },
            target_profile=profile,
        )
    assert excinfo.value.reason_code == "chunk_payload_sha256_invalid"


def test_over_budget_content_points_at_replan_not_unrepresentability(
    tmp_path: Path,
) -> None:
    """R3: the representability check also trips on the contract's 256 KiB
    cap, so an over-budget fragment was labelled `content_unrepresentable` --
    pointing the operator at the target's bytes instead of the replan remedy.

    Reachable because a profile's `max_chars_per_chunk` is unbounded in both
    the contract and the published schema, so it can exceed that cap.
    """

    import inspect

    from app.agent_review import review_content_extraction_v2 as module

    source = inspect.getsource(module._build_fragment_content_v2)
    over_budget = source.index("_MAX_FRAGMENT_CONTENT_CHARS_V2")
    unrepresentable = source.index("_is_fragment_content_representable_v2")
    assert over_budget < unrepresentable, (
        "the length check must run first, or over-budget content is "
        "misreported as unrepresentable"
    )
    assert "CONTENT_REASON_OVER_BUDGET_REQUIRES_REPLAN_V2" in source[over_budget:unrepresentable]
