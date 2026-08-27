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


def test_readiness_post_seal_validation_error_is_a_raw_defect() -> None:
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

    # a VALID combination -- so any failure is derivation, not caller material
    with mock.patch(
        "app.agent_review.review_readiness_emission_v2.compute_run_id",
        side_effect=_defect(),
    ):
        with pytest.raises(ValidationError):
            _assemble_review_readiness_v2(
                decision=decision, findings=synthesis.findings,
                identity=manifest.identity, evaluated_identity=manifest.identity,
                pr_state=PullRequestStateV2.OPEN,
                checks=[_green_check(manifest.identity.head_sha)],
            )


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

    from app.agent_review import contracts_v2, review_readiness_emission_v2

    call_sites = [
        module
        for module in (contracts_v2, review_readiness_emission_v2)
        if "evaluate_ready_preconditions_v2(" in _Path(module.__file__).read_text(encoding="utf-8")
    ]
    assert len(call_sites) == 2


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
