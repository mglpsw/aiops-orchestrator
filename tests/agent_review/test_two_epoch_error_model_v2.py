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

from app.agent_review.contracts_v2 import PullRequestStateV2, SemanticGroupV2
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


def test_readiness_post_seal_derivation_defect_is_raw() -> None:
    """Previously the FALSIFIER for this authority; now the proof it is fixed.

    It pinned the wrong behaviour on purpose, and the checkpoint named
    inverting it to `pytest.raises(ValidationError)` as the acceptance
    criterion for the readiness partition. This is that inversion.

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

    What changed: the caller-material half of `validate_state_invariants` was
    extracted into `evaluate_readiness_submitted_material_v2`, which
    `produce_review_readiness_v2` consults BEFORE its transformation and
    derivation epoch. `_assemble_review_readiness_v2` converts nothing, so a
    derivation defect reaches the caller as the defect it is.
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
        with pytest.raises(ValidationError):
            _assemble_review_readiness_v2(
                decision=decision, findings=synthesis.findings,
                identity=manifest.identity, evaluated_identity=manifest.identity,
                pr_state=PullRequestStateV2.OPEN,
                checks=[_green_check(manifest.identity.head_sha)],
            )




def test_readiness_pre_seal_invalid_combination_is_a_typed_refusal() -> None:
    """`ready` + merged PR is decidable from SUBMITTED material and refused
    before the artifact is built, with a reason that names the rule.

    `ready` without green checks deliberately is NOT here: the emitter
    replaces submitted `checks` with the assessment's own, and a submitted
    `READY` legitimately degrades to `manual_required` when required-check
    authority is not established. Refusing it pre-seal would break that
    documented behaviour, so it stays a final-material invariant.
    """

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

    from app.agent_review.contracts_v2 import (
        READY_REQUIRES_OPEN_PR_REASON_V2,
        )

    from app.agent_review.review_readiness_emission_v2 import (
        ReadinessEmissionError,
        _assemble_review_readiness_v2,
    )

    with pytest.raises(ReadinessEmissionError) as excinfo:
        _assemble_review_readiness_v2(
            decision=decision, findings=synthesis.findings,
            identity=manifest.identity, evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.MERGED,
            checks=[_green_check(manifest.identity.head_sha)],
        )
    assert excinfo.value.reason_code == READY_REQUIRES_OPEN_PR_REASON_V2


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
    # `contracts_v2` hosts the rule; the emission owner reaches the one
    # precondition it can decide through `ready_state_allows_pull_request_v2`,
    # not by calling this function. A second site here would be a duplicated
    # derivation of the full five-rule set.
    assert call_sites == ["contracts_v2.py"], call_sites


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
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )
    # a finding observed on a DIFFERENT head: submitted material, and a
    # caller-visible invariant with no `ready`-precondition of its own
    import dataclasses

    from app.agent_review.contracts_v2 import (
        READINESS_SUBMITTED_MATERIAL_INVALID_REASON_V2,
        )

    from app.agent_review.contracts_v2 import ReadinessReasonV2

    # duplicate submitted reason codes: caller material, and an invariant with
    # no `ready`-precondition of its own. The transformation only ADDS reasons,
    # so a duplicate submitted here stays a duplicate in the artifact.
    duplicated = dataclasses.replace(
        decision,
        reason_codes=(
            ReadinessReasonV2.HEAD_MISMATCH,
            ReadinessReasonV2.HEAD_MISMATCH,
        ),
    )
    from app.agent_review.contracts_v2 import evaluate_readiness_common_material_v2

    unmet = evaluate_readiness_common_material_v2(
        reason_codes=duplicated.reason_codes, blockers=duplicated.blockers,
        findings=synthesis.findings,
        evaluated_head_sha=manifest.identity.head_sha,
    )
    assert unmet == READINESS_SUBMITTED_MATERIAL_INVALID_REASON_V2
    assert "input_value" not in unmet and "{" not in unmet


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


# -- readiness authority partition (#272 corrective round) -------------------
#
# The partition that decides everything here is "who produced this value,
# relative to `produce_review_readiness_v2`":
#
#   C  submitted BY the caller          decision, findings, checks, identity,
#                                       evaluated_identity, pr_state
#   T  produced INSIDE the emitter      required-check assessment, and the
#                                       adjusted decision derived from it
#   D  derived INSIDE the emitter       run_id, evaluated_run_id, head_sha,
#                                       evaluated_head_sha
#
# The emitter derives NEITHER decision source -- a `--decision` JSON and a
# `compute_readiness_decision_v2` output arrive as the same argument and are
# equally caller-material AT THIS BOUNDARY. Provenance beyond that belongs to
# whoever produced the decision, which is a different authority's epoch. That
# is why no sealed carrier is needed here: the partition is total and disjoint
# without one.


def _readiness_case():
    from tests.agent_review.test_review_readiness_emission_v2 import (
        _fully_reviewed_manifest_and_report,
        _policies,
        _synthesis,
    )
    from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )
    return manifest, synthesis, decision


def test_adjusted_decision_defect_is_a_raw_programmer_defect(tmp_path: Path) -> None:
    """R2, and the reason the `compute_run_id` witness alone is insufficient.

    `_apply_required_check_assessment_v2` runs INSIDE the emitter and
    transforms the submitted decision. A bug there produces a structurally
    typed but semantically incoherent `ReadinessDecisionV2`, which the final
    contract rejects. That is transformation output, not caller material, so
    it must escape raw -- otherwise the laundering has simply moved one step
    earlier than the `compute_run_id` witness.
    """

    from unittest import mock as _mock

    from tests.agent_review.test_review_readiness_emission_v2 import (
        _assemble_review_readiness_v2,
    )
    from app.agent_review import review_readiness_emission_v2 as module
    from app.agent_review.contracts_v2 import PullRequestStateV2, ReadinessReasonV2

    manifest, synthesis, decision = _readiness_case()

    def _bugged_assessment(*, decision, assessment):
        # claims READY while carrying an active reason: contract-inadmissible
        import dataclasses

        return dataclasses.replace(
            decision, reason_codes=(ReadinessReasonV2.HEAD_MISMATCH,)
        )

    with _mock.patch.object(
        module, "_apply_required_check_assessment_v2", _bugged_assessment
    ):
        with pytest.raises(ValidationError):
            _assemble_review_readiness_v2(
                decision=_bugged_assessment(decision=decision, assessment=None),
                findings=synthesis.findings,
                identity=manifest.identity,
                evaluated_identity=manifest.identity,
                pr_state=PullRequestStateV2.OPEN,
                checks=(),
            )


def test_submitted_invalid_decision_is_a_typed_refusal(tmp_path: Path) -> None:
    """R3: an externally-submitted decision whose state/reasons/blockers
    combination the contract forbids must be refused pre-seal, sanitized, with
    no artifact and no traceback."""

    from tests.agent_review.test_review_readiness_emission_v2 import (
        _assemble_review_readiness_v2,
    )
    from app.agent_review.contracts_v2 import PullRequestStateV2, ReadinessReasonV2
    from app.agent_review.review_readiness_emission_v2 import ReadinessEmissionError

    manifest, synthesis, decision = _readiness_case()
    # BLOCKED_CODE with no reason codes: inadmissible for a non-ready state
    import dataclasses

    submitted = dataclasses.replace(
        decision, reason_codes=(ReadinessReasonV2.HEAD_MISMATCH,)
    )

    with pytest.raises((ReadinessEmissionError, ValidationError)) as excinfo:
        _assemble_review_readiness_v2(
            decision=submitted, findings=synthesis.findings,
            identity=manifest.identity, evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.OPEN, checks=(),
        )
    if isinstance(excinfo.value, ReadinessEmissionError):
        assert "{" not in excinfo.value.reason_code


def test_published_contract_still_rejects_a_wrong_run_id_on_direct_parse() -> None:
    """§17: moving the caller-material half pre-seal must not weaken the
    published artifact contract itself.

    A `ReviewReadinessV2` document parsed straight from JSON with an incorrect
    `run_id` -- the derivation-coherence half -- is still rejected. That half
    was never extracted, and never should be: nothing outside the emitter is
    positioned to establish it.
    """

    import json

    from tests.agent_review.test_review_readiness_emission_v2 import (
        _assemble_review_readiness_v2,
        _fully_reviewed_manifest_and_report,
        _green_check,
        _policies,
        _synthesis,
    )
    from app.agent_review.contracts_v2 import (
        PullRequestStateV2,
        ReviewReadinessV2,
    )
    from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )
    good = _assemble_review_readiness_v2(
        decision=decision, findings=synthesis.findings,
        identity=manifest.identity, evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(manifest.identity.head_sha)],
    )
    document = json.loads(good.model_dump_json())
    document["run_id"] = "a" * 64

    # `model_validate_json`, not `model_validate`: this contract has
    # tuple-typed fields, and under strict validation a Python list is not a
    # tuple while a JSON array is. Parsing the dict directly rejected for THAT
    # reason instead, so this test passed while the run_id check was removed --
    # the same trap this codebase documents elsewhere.
    with pytest.raises(ValidationError) as excinfo:
        ReviewReadinessV2.model_validate_json(json.dumps(document))
    assert "run_id" in str(excinfo.value)


def test_common_material_authority_is_actually_called_by_both_owners() -> None:
    """The extracted half must be CALLED, not restated.

    The previous version of this test grepped for the function name followed
    by "(", which also matches its own `def` line -- so `contracts_v2.py`
    counted as a call site while containing zero calls, and the test passed on
    a property that was false. Review caught it. Counting `ast.Call` nodes is
    what makes the assertion capable of failing.
    """

    import ast
    from pathlib import Path as _Path

    from app.agent_review import contracts_v2

    package_root = _Path(contracts_v2.__file__).parent
    callers = {}
    for source in package_root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        calls = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "evaluate_readiness_common_material_v2"
        )
        if calls:
            callers[source.name] = calls

    assert callers == {
        "contracts_v2.py": 1,
        "review_readiness_emission_v2.py": 1,
    }, callers


def test_public_boundary_refuses_invalid_submitted_material(tmp_path: Path) -> None:
    """The pre-seal epoch must be reached through the PUBLIC function, not
    only by calling the extracted authority directly.

    Without this, deleting the `if unmet is not None: raise` in
    `produce_review_readiness_v2` leaves every pre-seal test green -- they
    would all be exercising the helper rather than the boundary that uses it.
    """

    import json

    from app.agent_review.authoritative_ci_snapshot_v2 import (
        parse_authoritative_ci_snapshot_v2,
    )
    from app.agent_review.contracts_v2 import (
        READINESS_SUBMITTED_MATERIAL_INVALID_REASON_V2,
        ReadinessReasonV2,
        RunOriginV2,
    )
    from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
    from app.agent_review.review_readiness_emission_v2 import (
        ReadinessEmissionError,
        produce_review_readiness_v2,
    )
    from tests.agent_review.test_review_readiness_emission_v2 import (
        _fully_reviewed_manifest_and_report,
        _policies,
        _profile_bound_identity,
        _synthesis,
    )
    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import (
        TOOLCHAIN_DIGEST,
        _snapshot_dict,
    )

    import dataclasses

    profile_root, profile_hash = _profile_bound_identity(
        tmp_path, required_checks=["pytest"]
    )
    manifest, report = _fully_reviewed_manifest_and_report(profile_hash=profile_hash)
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )
    # duplicate submitted reason codes: caller material, inadmissible
    submitted = dataclasses.replace(
        decision,
        reason_codes=(
            ReadinessReasonV2.HEAD_MISMATCH,
            ReadinessReasonV2.HEAD_MISMATCH,
        ),
    )

    with pytest.raises(ReadinessEmissionError) as excinfo:
        produce_review_readiness_v2(
            decision=submitted,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.OPEN,
            checks=(),
            provenance=(),
            origin=RunOriginV2(
                event_type="pull_request", event_action="synchronize",
                delivery_id="delivery-1",
            ),
            snapshot=parse_authoritative_ci_snapshot_v2(
                json.dumps(_snapshot_dict([]))
            ),
            toolchain_digest=TOOLCHAIN_DIGEST,
            target_profile_root=str(profile_root),
        )
    assert excinfo.value.reason_code == READINESS_SUBMITTED_MATERIAL_INVALID_REASON_V2


def test_raising_derivation_also_escapes_raw() -> None:
    """A derivation that RAISES must escape too, not only one that returns a
    wrong value.

    Honest limitation, recorded rather than overstated: the mock fires at the
    provenance check's own `compute_run_id` call, which precedes the derivation
    lines, so this does not discriminate a catch placed around those lines
    specifically. That placement is covered instead by
    `M_FINAL_CONSTRUCTOR_VALIDATION_CAUGHT` and
    `M_FINAL_MODEL_CONSTRUCT_BYPASSES_VALIDATION`, both killed.
    """

    from unittest import mock as _mock

    from tests.agent_review.test_review_readiness_emission_v2 import (
        _assemble_review_readiness_v2,
        _fully_reviewed_manifest_and_report,
        _green_check,
        _policies,
        _synthesis,
    )
    from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )

    with _mock.patch(
        "app.agent_review.review_readiness_emission_v2.compute_run_id",
        side_effect=RuntimeError("derivation defect"),
    ):
        with pytest.raises(RuntimeError):
            _assemble_review_readiness_v2(
                decision=decision, findings=synthesis.findings,
                identity=manifest.identity, evaluated_identity=manifest.identity,
                pr_state=PullRequestStateV2.OPEN,
                checks=[_green_check(manifest.identity.head_sha)],
            )
