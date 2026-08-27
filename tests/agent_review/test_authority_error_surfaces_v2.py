"""Closed error surfaces for the v2 authorities (`#200-D` predecessor).

## Why this file exists

PR #271 built the operational composer and was reviewed six times. Every round
surfaced the same class: a stage's ``except`` list was narrower than the
exception surface beneath it. Two consumer-side structural attempts reduced but
never closed it, because each authority's surface is OPEN -- a sibling error
family, a pydantic ``ValidationError``, an unguarded file read -- and none of
those is documented, so no amount of consumer inspection enumerates them.

The decision recorded there was model **B**: close the surfaces at their
OWNERS. A caller may know THAT an authority refused; it must not know HOW that
authority's internals failed.

## The property, in both directions

Closure is not "catch more". Each authority must satisfy BOTH:

    expected operational failure  -> exactly that authority's documented family
    unexpected programmer defect  -> escapes raw, never sanitized

The second direction is what makes this different from a catch-all. A
``TypeError`` from a defect in this repository must never become a review
refusal, because that would let a bug be reported as a reviewed verdict.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import SemanticGroupV2
from app.agent_review.diff_acquisition_v2 import (
    DiffAcquisitionError,
    acquire_authoritative_diff_v2,
)
from app.agent_review.payload_builder_v2 import (
    PayloadBuilderError,
    build_chunk_payloads_from_profile_v2,
)
from app.agent_review.payload_set_emission_v2 import emit_payload_set_v2
from app.agent_review.payload_set_v2 import PayloadSetBindingError
from app.agent_review.profile_loader_v2 import (
    TargetProfileLoadErrorV2,
    load_target_profile_v2,
)
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

_PROFILE = """schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: example/repo
  default_branch: main
artifacts: []
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
contracts: []
limitations: []
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, profile_yaml: str = _PROFILE):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".aiops").mkdir()
    (repo / ".aiops" / "target-profile.v2.yaml").write_text(profile_yaml, encoding="utf-8")
    (repo / "app.py").write_text("a = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("a = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "head")
    return repo, base_sha, _git(repo, "rev-parse", "HEAD")


def _grouping_policy() -> SemanticGroupingPolicyV2:
    rule = SemanticGroupingRuleV2(
        rule_id="all", semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC,
        path_patterns=["*"], contract_ids=[], artifact_ids=[], priority=0,
    )
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2", "schema_version": 2,
        "source": "repo-semantic-grouping-policy", "rules": [rule], "fallback_group": None,
    }
    digest = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json")]}
    )
    return SemanticGroupingPolicyV2(**material, policy_sha256=digest)


def _assembled(repo: Path, base_sha: str, head_sha: str, profile=None):
    profile = profile or load_target_profile_v2(repo)
    diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    outcome = assemble_manifest_from_diff_v2(
        diffs, profile=profile, grouping_policy=_grouping_policy(), repo="example/repo",
        pr_number=1, base_sha=base_sha, head_sha=head_sha, tested_merge_sha=head_sha,
        toolrepo_sha="b" * 40, evidence_hash="c" * 64, max_lines_per_chunk=1000,
    )
    assert outcome.state == "assembled", outcome.blocked_reason
    return profile, outcome.manifest


# -- diff acquisition --------------------------------------------------------


def test_diff_missing_repo_root_is_a_diff_acquisition_error(tmp_path: Path) -> None:
    """WITNESS: `acquire_authoritative_diff_v2` shells out with
    ``cwd=repo_root``; a missing root raised a raw ``FileNotFoundError``."""

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_authoritative_diff_v2(
            tmp_path / "absent", base_sha="a" * 40, head_sha="b" * 40
        )
    assert excinfo.value.reason_code
    assert "absent" not in excinfo.value.reason_code


def test_diff_absent_git_is_distinguishable_from_a_bad_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both used to surface as the SAME raw ``FileNotFoundError``, which is
    precisely why a consumer could not tell them apart -- and why #271 kept
    misreporting one as the other. The owner can, so it must."""

    repo, base_sha, head_sha = _repo(tmp_path)
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    from app.agent_review.diff_acquisition_v2 import (
        GIT_UNAVAILABLE_REASON_V2,
        REPO_ROOT_UNUSABLE_REASON_V2,
    )

    with pytest.raises(DiffAcquisitionError) as excinfo:
        acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
    # the EXACT code, not merely "different from the other one": a generic
    # I/O fallback would also differ, while telling the operator nothing.
    assert excinfo.value.reason_code == GIT_UNAVAILABLE_REASON_V2

    monkeypatch.undo()
    with pytest.raises(DiffAcquisitionError) as excinfo2:
        acquire_authoritative_diff_v2(
            tmp_path / "absent", base_sha=base_sha, head_sha=head_sha
        )
    assert excinfo2.value.reason_code == REPO_ROOT_UNUSABLE_REASON_V2


# -- assembly ---------------------------------------------------------------


def test_assembly_contract_invalid_identity_is_a_run_assembly_error(
    tmp_path: Path,
) -> None:
    """WITNESS: `RunIdentityV2` is constructed inside assembly, so
    contract-invalid material escaped as a pydantic ``ValidationError``."""

    repo, base_sha, head_sha = _repo(tmp_path)
    profile = load_target_profile_v2(repo)
    diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    with pytest.raises(RunAssemblyError) as excinfo:
        assemble_manifest_from_diff_v2(
            diffs, profile=profile, grouping_policy=_grouping_policy(),
            repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
            tested_merge_sha=head_sha, toolrepo_sha="z" * 40, evidence_hash="c" * 64,
            max_lines_per_chunk=1000,
        )
    assert excinfo.value.reason_code
    assert "zzz" not in excinfo.value.reason_code


def test_assembly_non_positive_budget_is_a_run_assembly_error(tmp_path: Path) -> None:
    """WITNESS: the chunk planner raised a BARE ``ValueError`` -- neither the
    owner family nor a pydantic error, so every consumer guard missed it."""

    repo, base_sha, head_sha = _repo(tmp_path)
    profile = load_target_profile_v2(repo)
    diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    with pytest.raises(RunAssemblyError) as excinfo:
        assemble_manifest_from_diff_v2(
            diffs, profile=profile, grouping_policy=_grouping_policy(),
            repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
            tested_merge_sha=head_sha, toolrepo_sha="b" * 40, evidence_hash="c" * 64,
            max_lines_per_chunk=0,
        )
    assert excinfo.value.reason_code


# -- payload builder ---------------------------------------------------------


def test_payload_missing_required_artifact_is_a_payload_builder_error(
    tmp_path: Path,
) -> None:
    """WITNESS: ``PayloadReferenceError`` is a SIBLING family, not a subclass,
    so a caller had to know two types for one boundary. The originating
    semantic reason is preserved through the conversion."""

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


def test_payload_unreadable_declared_contract_is_a_payload_builder_error(
    tmp_path: Path,
) -> None:
    """WITNESS: the contract-reference branch reads a file without an
    ``OSError`` guard (the artifact branch beside it has one), so an
    unreadable file leaked a raw ``PermissionError`` carrying the path."""

    repo, base_sha, head_sha = _repo(tmp_path)
    contract = repo / ".aiops" / "contracts.yaml"
    contract.write_text("k: v\n", encoding="utf-8")
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    (repo / ".aiops" / "target-profile.v2.yaml").write_text(
        _PROFILE.replace(
            "contracts: []",
            "contracts:\n  - contract_id: contract.api\n"
            '    contract_version: "1"\n    path: .aiops/contracts.yaml\n'
            f'    sha256: "{digest}"\n    scope: repository\n    required: true',
        ),
        encoding="utf-8",
    )
    profile, manifest = _assembled(repo, base_sha, head_sha)

    real_read_bytes = Path.read_bytes

    def _unreadable(self, *args, **kwargs):
        if self.name == "contracts.yaml":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self, *args, **kwargs)

    with mock.patch.object(Path, "read_bytes", _unreadable):
        with pytest.raises(PayloadBuilderError) as excinfo:
            build_chunk_payloads_from_profile_v2(
                manifest, profile=profile, repo_root=repo
            )
    assert excinfo.value.reason_code
    assert str(repo) not in excinfo.value.reason_code


# -- payload set -------------------------------------------------------------


def test_payload_set_empty_is_a_payload_set_binding_error(tmp_path: Path) -> None:
    """WITNESS: ``PayloadSetV2`` requires >= 1 entry, so an empty list escaped
    as a raw pydantic ``ValidationError`` from model construction."""

    repo, base_sha, head_sha = _repo(tmp_path)
    _, manifest = _assembled(repo, base_sha, head_sha)

    with pytest.raises(PayloadSetBindingError) as excinfo:
        emit_payload_set_v2(manifest, [])
    assert excinfo.value.reason_code


# -- review content ----------------------------------------------------------


def test_content_missing_repo_root_is_an_extraction_blocked_error(
    tmp_path: Path,
) -> None:
    """WITNESS: extraction reads the worktree, so a missing root escaped as a
    raw ``FileNotFoundError`` outside its declared family."""

    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )

    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=tmp_path / "absent", base_sha=base_sha, head_sha=head_sha,
            manifest=manifest,
            payload_sha256_by_chunk_id={
                item.chunk_id: item.payload.payload_sha256 for item in built
            },
            target_profile=profile,
        )
    assert excinfo.value.reason_code
    assert "absent" not in excinfo.value.reason_code


# -- readiness ---------------------------------------------------------------


def test_readiness_contract_violation_is_a_readiness_emission_error(
    tmp_path: Path,
) -> None:
    """The emission family exists and its reasons NAME rules.

    Under model B this authority produced one collapsed
    `readiness_emission_contract_invalid` for every contract failure. Under
    the two-epoch model the caller-visible `ready` preconditions are
    established before construction and each names the rule it violates, so an
    operator can again tell them apart.
    """

    from app.agent_review.contracts_v2 import (
        READY_REQUIRES_GREEN_CHECKS_REASON_V2,
        READY_REQUIRES_OPEN_PR_REASON_V2,
    )
    from app.agent_review.review_readiness_emission_v2 import ReadinessEmissionError

    assert READY_REQUIRES_OPEN_PR_REASON_V2 != READY_REQUIRES_GREEN_CHECKS_REASON_V2
    assert issubclass(ReadinessEmissionError, ValueError)


# -- the OTHER direction: programmer defects must never be sanitized ---------
#
# Closure is not "catch more". Without these controls, every test above could
# be satisfied by a blanket `except Exception`, and a genuine bug in this
# repository would be reported to an operator as a review refusal.


_PROGRAMMER_DEFECTS = [
    # `ValidationError` FIRST and deliberately: the previous control set
    # omitted it, and it is how internal defects most often manifest here.
    # That omission is exactly why these controls stayed green while the
    # property they exist to protect was false under model B. Every injection
    # below happens with valid caller material, strictly after its seal --
    # see `test_two_epoch_error_model_v2` for the seal-crossing witnesses.
    pytest.param(ValidationError.from_exception_data("Defect", []), id="ValidationError"),
    pytest.param(TypeError("programmer defect"), id="TypeError"),
    pytest.param(AttributeError("programmer defect"), id="AttributeError"),
    pytest.param(AssertionError("programmer defect"), id="AssertionError"),
    pytest.param(KeyError("programmer defect"), id="KeyError"),
    pytest.param(IndexError("programmer defect"), id="IndexError"),
]


@pytest.mark.parametrize("defect", _PROGRAMMER_DEFECTS)
def test_diff_does_not_launder_programmer_defects(
    tmp_path: Path, defect: BaseException
) -> None:
    repo, base_sha, head_sha = _repo(tmp_path)
    with mock.patch(
        "app.agent_review.diff_acquisition_v2.parse_unified_diff", side_effect=defect
    ):
        with pytest.raises(type(defect)):
            acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)


@pytest.mark.parametrize("defect", _PROGRAMMER_DEFECTS)
def test_assembly_does_not_launder_programmer_defects(
    tmp_path: Path, defect: BaseException
) -> None:
    repo, base_sha, head_sha = _repo(tmp_path)
    profile = load_target_profile_v2(repo)
    diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    with mock.patch(
        "app.agent_review.run_assembly_v2.validate_diff_completeness_v2",
        side_effect=defect,
    ):
        with pytest.raises(type(defect)):
            assemble_manifest_from_diff_v2(
                diffs, profile=profile, grouping_policy=_grouping_policy(),
                repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
                tested_merge_sha=head_sha, toolrepo_sha="b" * 40,
                evidence_hash="c" * 64, max_lines_per_chunk=1000,
            )


@pytest.mark.parametrize("defect", _PROGRAMMER_DEFECTS)
def test_payload_builder_does_not_launder_programmer_defects(
    tmp_path: Path, defect: BaseException
) -> None:
    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)

    with mock.patch(
        "app.agent_review.payload_builder_v2.build_payload_artifact_references_v2",
        side_effect=defect,
    ):
        with pytest.raises(type(defect)):
            build_chunk_payloads_from_profile_v2(
                manifest, profile=profile, repo_root=repo
            )


@pytest.mark.parametrize("defect", _PROGRAMMER_DEFECTS)
def test_payload_set_does_not_launder_programmer_defects(
    tmp_path: Path, defect: BaseException
) -> None:
    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )

    with mock.patch(
        "app.agent_review.payload_set_emission_v2.compute_payload_set_sha256_v2",
        side_effect=defect,
    ):
        with pytest.raises(type(defect)):
            emit_payload_set_v2(manifest, [item.payload for item in built])


@pytest.mark.parametrize("defect", _PROGRAMMER_DEFECTS)
def test_content_does_not_launder_programmer_defects(
    tmp_path: Path, defect: BaseException
) -> None:
    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )

    with mock.patch(
        "app.agent_review.review_content_extraction_v2.bind_review_content_to_manifest_v2",
        side_effect=defect,
    ):
        with pytest.raises(type(defect)):
            extract_review_content_v2(
                repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
                payload_sha256_by_chunk_id={
                    item.chunk_id: item.payload.payload_sha256 for item in built
                },
                target_profile=profile,
            )


# -- conformance matrix ------------------------------------------------------


# The authorities this PR closed, and therefore the exact module set the
# structural guard below must cover. Review round 3: this table was previously
# declared and never used -- a table nothing reads cannot keep anything
# honest, and it had already drifted (it named `produce_review_readiness_v2`,
# which deliberately lets other families through, and `profile_loader_v2`,
# which this PR does not touch). It now DRIVES the guard, so the two cannot
# disagree.
_CLOSED_AUTHORITY_MODULES = (
    "diff_acquisition_v2",
    "run_assembly_v2",
    "payload_builder_v2",
    "payload_references_v2",
    "payload_set_emission_v2",
    "review_content_extraction_v2",
    "review_readiness_emission_v2",
)


def test_no_closed_authority_catches_bare_exception() -> None:
    """The closure guard.

    Model B is "close at the owner", NOT "catch everything at the owner". If a
    future edit reached for `except Exception` or `except BaseException` inside
    one of these authorities, every positive test above would still pass while
    the programmer-defect controls silently became meaningless. This asserts
    the shape directly.
    """

    import ast
    import importlib

    offenders: list[str] = []
    modules = [
        importlib.import_module(f"app.agent_review.{name}")
        for name in _CLOSED_AUTHORITY_MODULES
    ]
    assert len(modules) == len(_CLOSED_AUTHORITY_MODULES)
    for module in modules:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names: list[str] = []
            if isinstance(node.type, ast.Name):
                names = [node.type.id]
            elif isinstance(node.type, ast.Tuple):
                names = [e.id for e in node.type.elts if isinstance(e, ast.Name)]
            elif node.type is None:
                names = ["<bare except>"]
            for name in names:
                if name in {"Exception", "BaseException", "<bare except>"}:
                    offenders.append(f"{Path(module.__file__).name}:{node.lineno} {name}")

    assert not offenders, offenders


def test_content_internal_digest_perturbation_is_now_a_raw_defect(
    tmp_path: Path,
) -> None:
    """Re-targeted by the two-epoch redesign, and worth keeping as history.

    This witness perturbs `compute_review_content_sha256_v2` -- an INTERNAL
    derivation step, reached only after acquisition, redaction and DLP have
    already accepted the external material. Under model B it produced
    `content_contract_invalid`; that was precisely the laundering that
    falsified model B, because the same code was also produced by genuine
    caller/external problems.

    Under model A* it is a repository defect and escapes raw. External
    unrepresentability keeps its own typed refusal -- see the CRLF witness.
    """

    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )

    from pydantic import ValidationError

    with mock.patch(
        "app.agent_review.review_content_extraction_v2.compute_review_content_sha256_v2",
        return_value="f" * 64,
    ):
        with pytest.raises(ValidationError):
            extract_review_content_v2(
                repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
                payload_sha256_by_chunk_id={
                    item.chunk_id: item.payload.payload_sha256 for item in built
                },
                target_profile=profile,
            )


def test_readiness_contract_failure_is_an_emission_error_through_the_public_path(
    tmp_path: Path,
) -> None:
    """WITNESS through the REAL public function.

    A ``ready`` decision combined with a merged PR violates
    ``ReviewReadinessV2``'s own state invariant, which used to surface as a
    raw pydantic ``ValidationError``.
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
    from app.agent_review.contracts_v2 import READY_REQUIRES_OPEN_PR_REASON_V2
    from app.agent_review.review_readiness_emission_v2 import ReadinessEmissionError

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies()
    )
    assert decision.state is ReadinessStateV2.READY

    # `_assemble_review_readiness_v2` is POST-seal after `#200-D`'s readiness
    # partition; the caller-visible refusal is established by the submitted-
    # material authority, which the public boundary consults.

    with pytest.raises(ReadinessEmissionError) as excinfo:
        _assemble_review_readiness_v2(
            decision=decision,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.MERGED,
            checks=[_green_check(manifest.identity.head_sha)],
        )
    # Under the two-epoch model this names the rule that was not met, rather
    # than collapsing every contract failure into one opaque code -- the
    # operator discrimination the outer-catch design had destroyed.
    assert excinfo.value.reason_code == READY_REQUIRES_OPEN_PR_REASON_V2


def test_content_binding_family_is_converted_at_the_extraction_boundary(
    tmp_path: Path,
) -> None:
    """WITNESS for the binder clause specifically.

    ``bind_review_content_to_manifest_v2`` is extraction's terminal step and
    raises ``ReviewContentBindingError`` -- a SIBLING family, so it escaped the
    declared surface. Content built FROM a manifest necessarily agrees with it,
    so the divergence the binder exists to catch cannot be produced from
    outside the call; the binder seam is injected narrowly while the function
    under test remains the real public authority. The binder's own precise
    reason must survive the conversion, not be flattened.
    """

    from app.agent_review.review_content_v2 import ReviewContentBindingError

    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )

    with mock.patch(
        "app.agent_review.review_content_extraction_v2.bind_review_content_to_manifest_v2",
        side_effect=ReviewContentBindingError("content_run_identity_mismatch"),
    ):
        with pytest.raises(ExtractionBlockedError) as excinfo:
            extract_review_content_v2(
                repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
                payload_sha256_by_chunk_id={
                    item.chunk_id: item.payload.payload_sha256 for item in built
                },
                target_profile=profile,
            )
    assert excinfo.value.reason_code == "content_run_identity_mismatch"


# -- the acceptance oracle (§13): what a future composer must NOT need ------


def test_a_composer_needs_only_owner_families(tmp_path: Path) -> None:
    """The central acceptance criterion for this predecessor.

    A future successor of PR #271 must be able to write its authority calls
    catching ONE documented family each -- with no ``ValidationError``, no
    ``OSError``, no ``PayloadReferenceError``, no ``ReviewContentBindingError``,
    no ``except Exception`` and no dynamic ``getattr(exc, "reason_code")`` at
    the composition layer.

    This drives the whole front half with only those families, against inputs
    chosen so that EVERY stage refuses in turn. If any stage regressed to an
    open surface, the raw exception would escape this function's narrow
    handlers and fail the test rather than be reported.
    """

    refusals: dict[str, str] = {}

    # 1. profile
    try:
        load_target_profile_v2(tmp_path / "no_such_root")
    except TargetProfileLoadErrorV2 as exc:
        refusals["profile"] = exc.reason_code

    # 2. diff
    try:
        acquire_authoritative_diff_v2(
            tmp_path / "no_such_root", base_sha="a" * 40, head_sha="b" * 40
        )
    except DiffAcquisitionError as exc:
        refusals["diff"] = exc.reason_code

    repo, base_sha, head_sha = _repo(tmp_path)
    profile = load_target_profile_v2(repo)
    diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    # 3. assembly
    try:
        assemble_manifest_from_diff_v2(
            diffs, profile=profile, grouping_policy=_grouping_policy(),
            repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
            tested_merge_sha=head_sha, toolrepo_sha="z" * 40, evidence_hash="c" * 64,
            max_lines_per_chunk=1000,
        )
    except RunAssemblyError as exc:
        refusals["assembly"] = exc.reason_code

    _, manifest = _assembled(repo, base_sha, head_sha)

    # 4. payload
    demanding_repo, d_base, d_head = _repo(
        tmp_path / "demanding",
        _PROFILE.replace(
            "artifacts: []",
            "artifacts:\n  - artifact_id: full-diff\n    path: artifacts/full.diff\n"
            "    kind: diff\n    required: true\n    max_bytes: 1000000",
        ),
    )
    d_profile, d_manifest = _assembled(demanding_repo, d_base, d_head)
    try:
        build_chunk_payloads_from_profile_v2(
            d_manifest, profile=d_profile, repo_root=demanding_repo
        )
    except PayloadBuilderError as exc:
        refusals["payload"] = exc.reason_code

    # 5. payload set
    try:
        emit_payload_set_v2(manifest, [])
    except PayloadSetBindingError as exc:
        refusals["payload_set"] = exc.reason_code

    # 6. content -- the refusal must come from INSIDE the body. A nonexistent
    # repo_root would be refused at the first statement, leaving the body
    # unexecuted and the stage unable to prove anything about it.
    try:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id={}, target_profile=profile,
        )
    except ExtractionBlockedError as exc:
        refusals["content"] = exc.reason_code

    assert set(refusals) == {
        "profile", "diff", "assembly", "payload", "payload_set", "content"
    }, refusals
    # every refusal is a stable, content-free, path-free code
    for stage, reason in refusals.items():
        assert reason and reason.replace("_", "").isalnum(), (stage, reason)
        assert str(tmp_path) not in reason
        assert "/" not in reason and "\\" not in reason


def test_closure_composes_across_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 1 on this PR: closure must COMPOSE, not just terminate.

    Extraction converts `DiffAcquisitionError` into its own family. Flattening
    every acquisition cause to a single ``content_diff_acquisition_failed``
    would undo, on this path, the exact distinction this change exists to
    create -- "no such checkout" and "no git on PATH" would once again be
    indistinguishable to an operator, this time one layer up.
    """

    from app.agent_review.diff_acquisition_v2 import (
        GIT_UNAVAILABLE_REASON_V2,
        REPO_ROOT_UNUSABLE_REASON_V2,
    )

    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)
    built = build_chunk_payloads_from_profile_v2(
        manifest, profile=profile, repo_root=repo
    )
    payload_map = {item.chunk_id: item.payload.payload_sha256 for item in built}

    with pytest.raises(ExtractionBlockedError) as missing_root:
        extract_review_content_v2(
            repo_root=tmp_path / "absent", base_sha=base_sha, head_sha=head_sha,
            manifest=manifest, payload_sha256_by_chunk_id=payload_map,
            target_profile=profile,
        )

    # Simulate git absence the way it actually happens -- an empty PATH --
    # rather than by patching `subprocess.run` while git is still installed.
    # The authority no longer INFERS which file was missing, it asks, so a
    # simulation that leaves git on PATH is simply not the condition under
    # test.
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty_bin))

    with pytest.raises(ExtractionBlockedError) as absent_git:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha,
            manifest=manifest, payload_sha256_by_chunk_id=payload_map,
            target_profile=profile,
        )

    assert missing_root.value.reason_code == REPO_ROOT_UNUSABLE_REASON_V2
    assert absent_git.value.reason_code == GIT_UNAVAILABLE_REASON_V2


def test_fragment_contract_failure_does_not_leak_diff_content(tmp_path: Path) -> None:
    """Review round 2 on this PR, REPRODUCED end-to-end.

    The first closure attempt guarded only the terminal ``ReviewContentV2``
    construction. ``FragmentContentV2`` and ``ChunkContentV2`` are built
    EARLIER in the same body, so a raw pydantic ``ValidationError`` still
    escaped -- and its message embeds the fragment's own diff content in
    ``input_value``. That made the gap a content leak, not only an open
    surface.

    A CRLF file is the natural witness: the carriage return is a forbidden
    control character for the fragment contract. No mocking -- the real public
    function, real git, real content.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".aiops").mkdir()
    (repo / ".aiops" / "target-profile.v2.yaml").write_text(_PROFILE, encoding="utf-8")
    # deliberately CRLF, and marked binary=false so git keeps the bytes as-is
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
    # the refusal must not carry the reviewed bytes
    assert "a = 1" not in reason and "a = 2" not in reason
    assert "\r" not in reason and str(repo) not in reason


def test_acceptance_oracle_content_stage_executes_the_real_body(
    tmp_path: Path,
) -> None:
    """Review round 2: the oracle's content stage passed a nonexistent
    ``repo_root``, so extraction refused at its FIRST statement and the body
    never ran -- the oracle structurally could not have caught the leak above.

    This drives a refusal from INSIDE the body instead, so the stage proves
    what it claims to.
    """

    repo, base_sha, head_sha = _repo(tmp_path)
    profile, manifest = _assembled(repo, base_sha, head_sha)

    # a payload map missing the manifest's chunk: reached well inside the body,
    # after acquisition and fragment planning have really run
    with pytest.raises(ExtractionBlockedError) as excinfo:
        extract_review_content_v2(
            repo_root=repo, base_sha=base_sha, head_sha=head_sha, manifest=manifest,
            payload_sha256_by_chunk_id={}, target_profile=profile,
        )
    assert excinfo.value.reason_code == "chunk_payload_sha256_unavailable"


# -- review round 3 on this PR ----------------------------------------------


def test_singular_payload_builder_is_closed_like_its_plural_sibling(
    tmp_path: Path,
) -> None:
    """R3: closing ONE entry point of an authority is not closing the
    authority. ``build_chunk_payload_from_profile_v2`` -- the singular public
    sibling -- was still open after the plural form was closed, and the very
    same witness escaped it raw, carrying the checkout path."""

    from app.agent_review.payload_builder_v2 import (
        build_chunk_payload_from_profile_v2,
    )

    repo, base_sha, head_sha = _repo(tmp_path)
    contract = repo / ".aiops" / "contracts.yaml"
    contract.write_text("k: v\n", encoding="utf-8")
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    (repo / ".aiops" / "target-profile.v2.yaml").write_text(
        _PROFILE.replace(
            "contracts: []",
            "contracts:\n  - contract_id: contract.api\n"
            '    contract_version: "1"\n    path: .aiops/contracts.yaml\n'
            f'    sha256: "{digest}"\n    scope: repository\n    required: true',
        ),
        encoding="utf-8",
    )
    profile, manifest = _assembled(repo, base_sha, head_sha)

    real_read_bytes = Path.read_bytes

    def _unreadable(self, *args, **kwargs):
        if self.name == "contracts.yaml":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self, *args, **kwargs)

    with mock.patch.object(Path, "read_bytes", _unreadable):
        with pytest.raises(PayloadBuilderError) as excinfo:
            build_chunk_payload_from_profile_v2(
                manifest, manifest.chunks[0], profile=profile, repo_root=repo
            )
    # the TRUE owner now names it precisely, rather than the generic fallback
    assert excinfo.value.reason_code == "payload_contract_unreadable"
    assert str(repo) not in excinfo.value.reason_code


def test_unreadable_contract_is_named_by_the_reference_authority(
    tmp_path: Path,
) -> None:
    """R3: the fix belongs in `payload_references_v2`, whose artifact branch
    already guarded its read while the contract branch beside it did not.
    Closing it there gives a precise reason instead of the payload builder's
    generic `payload_reference_unreadable` fallback -- and fixes every caller
    at once."""

    from app.agent_review.payload_references_v2 import (
        PAYLOAD_CONTRACT_UNREADABLE_REASON_V2,
        PayloadReferenceError,
        build_payload_contract_references_v2,
    )

    repo, base_sha, head_sha = _repo(tmp_path)
    contract = repo / ".aiops" / "contracts.yaml"
    contract.write_text("k: v\n", encoding="utf-8")
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    (repo / ".aiops" / "target-profile.v2.yaml").write_text(
        _PROFILE.replace(
            "contracts: []",
            "contracts:\n  - contract_id: contract.api\n"
            '    contract_version: "1"\n    path: .aiops/contracts.yaml\n'
            f'    sha256: "{digest}"\n    scope: repository\n    required: true',
        ),
        encoding="utf-8",
    )
    profile = load_target_profile_v2(repo)

    real_read_bytes = Path.read_bytes

    def _unreadable(self, *args, **kwargs):
        if self.name == "contracts.yaml":
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_bytes(self, *args, **kwargs)

    with mock.patch.object(Path, "read_bytes", _unreadable):
        with pytest.raises(PayloadReferenceError) as excinfo:
            build_payload_contract_references_v2(profile, repo)
    assert excinfo.value.reason_code == PAYLOAD_CONTRACT_UNREADABLE_REASON_V2


@pytest.mark.parametrize(
    "bad_budget", [pytest.param("500", id="str"), pytest.param(2.0, id="float"),
                   pytest.param(True, id="bool")],
)
def test_wrong_budget_type_is_a_defect_not_an_operational_refusal(
    tmp_path: Path, bad_budget: object
) -> None:
    """R3, the laundering direction.

    `isinstance(x, int)` accepted `True` (bool subclasses int) and assembled
    with an effective 1-line budget, while `"500"` and `2.0` were reported to
    the OPERATOR as a bad budget when they are a CALLER type defect. A wrong
    type is a bug and must crash; a well-typed budget that cannot describe a
    chunk is the operational refusal.
    """

    repo, base_sha, head_sha = _repo(tmp_path)
    profile = load_target_profile_v2(repo)
    diffs = acquire_authoritative_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)

    with pytest.raises(TypeError):
        assemble_manifest_from_diff_v2(
            diffs, profile=profile, grouping_policy=_grouping_policy(),
            repo="example/repo", pr_number=1, base_sha=base_sha, head_sha=head_sha,
            tested_merge_sha=head_sha, toolrepo_sha="b" * 40, evidence_hash="c" * 64,
            max_lines_per_chunk=bad_budget,
        )
