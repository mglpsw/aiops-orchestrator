"""RED/lock tests for `agent-review target conformance` (#203-S2).

Synthetic, offline conformance: it proves the ONE generic pack behaves
identically across several independently-authored synthetic targets, and
detects violations in each, without branching on any target's name.

Scope boundary, enforced by these tests and by the module's own docstring:

- no real AgentEscala, no real InterLeitos;
- no network, no Agent Router, no provider, no GitHub, no secrets;
- no runner mutation, no CT102/CT104;
- fixtures are ordinary temporary directories authored inside the test.

That is `#203`'s share. Real dual-target adoption is `#204` (spec §10),
and nothing here may be reported as `dual_target_conformance`.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2
from app.agent_review.target_pack_conformance_v2 import (
    CONFORMANCE_CASE_TARGET_UNREADABLE_REASON_V2,
    ConformanceCaseV2,
    ConformanceExpectationV2,
    run_conformance_v2,
)
from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
    compute_target_pack_manifest_digest_v2,
)
from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_RELATIVE_PATH_V2,
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
    compute_target_install_receipt_hash_v2,
)
from app.agent_review.target_pack_validate_v2 import (
    VALIDATE_PROFILE_HASH_MISMATCH_REASON_V2,
    VALIDATE_RECEIPT_MISSING_REASON_V2,
    VALIDATE_TARGET_OWNED_DRIFT_REASON_V2,
)

_PROFILE_RELATIVE_PATH = ".aiops/target-profile.v2.yaml"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _profile_yaml(repo: str) -> str:
    """Two synthetic targets differ ONLY in their own authored identity --
    exactly the axis a target-name branch would key off."""

    return f"""
schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: {repo}
  default_branch: main
artifacts:
  - artifact_id: full-diff
    path: artifacts/full.diff
    kind: diff
    required: true
    max_bytes: 1000000
budgets:
  max_chunks: 16
  total_prompt_chars: 250000
  max_chars_per_chunk: 24000
  max_files_per_chunk: 50
  max_contracts_per_chunk: 50
must_review:
  paths: []
  patterns: []
  artifact_ids: []
  minimum_coverage: complete
policies:
  network_policy: forbidden
  fail_closed: true
  redaction_required: true
  allow_partial_coverage: false
  required_checks:
    - REPLACE_ME_WITH_A_REAL_REQUIRED_CHECK_NAME
  allowed_semantic_groups:
    - primary_backend_logic
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts: []
limitations: []
"""


def _manifest(profile_yaml: str) -> TargetPackManifestV2:
    return TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=_PROFILE_RELATIVE_PATH,
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256=_sha256(profile_yaml.encode("utf-8")),
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="off",
    )


def _real_profile_hash(profile_yaml: str) -> str:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        (root / ".aiops").mkdir()
        (root / _PROFILE_RELATIVE_PATH).write_text(profile_yaml, encoding="utf-8")
        return compute_profile_hash_v2(load_target_profile_v2(str(root)))


def _receipt(repo: str, profile_yaml: str, **overrides: object) -> TargetInstallReceiptV2:
    fields = dict(
        schema_id="agent-review.target-install-receipt.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        manifest_digest=compute_target_pack_manifest_digest_v2(_manifest(profile_yaml)),
        target_repo=repo,
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo=repo),
        target_profile_hash=_real_profile_hash(profile_yaml),
        target_policy_hash=None,
        review_pack_hashes={},
        generated_file_hashes={},
        target_owned_file_hashes={_PROFILE_RELATIVE_PATH: _sha256(profile_yaml.encode("utf-8"))},
        target_owned_paths=(_PROFILE_RELATIVE_PATH,),
        required_capabilities=("router_transport",),
        expected_runner_labels=(),
        required_secret_names=(),
        rollout_mode="off",
        compatibility="compatible",
        previous_install_identity=None,
        generated_at=None,
    )
    fields.update(overrides)
    computed = compute_target_install_receipt_hash_v2(
        TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    )
    return TargetInstallReceiptV2(**fields, receipt_hash=computed)


def _materialize(root: Path, repo: str, *, receipt: TargetInstallReceiptV2 | None = None) -> str:
    profile_yaml = _profile_yaml(repo)
    (root / ".aiops").mkdir(parents=True, exist_ok=True)
    (root / _PROFILE_RELATIVE_PATH).write_text(profile_yaml, encoding="utf-8")
    receipt = receipt if receipt is not None else _receipt(repo, profile_yaml)
    (root / RECEIPT_RELATIVE_PATH_V2).write_text(
        json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return profile_yaml


@pytest.fixture
def two_targets():
    """Two independently-authored synthetic targets. Names are deliberately
    generic -- a real consumer name in a fixture would itself be the
    target-leak this capability exists to disprove."""

    base = Path(tempfile.mkdtemp())
    alpha = base / "alpha"
    beta = base / "beta"
    alpha.mkdir()
    beta.mkdir()
    _materialize(alpha, "acme/alpha-service")
    _materialize(beta, "globex/beta-platform")
    try:
        yield alpha, beta
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def _eligible(root: Path) -> ConformanceCaseV2:
    return ConformanceCaseV2(
        case_id=root.name, target_root=root, expectation=ConformanceExpectationV2.ELIGIBLE
    )


# ---------------------------------------------------------------------------
# Core property: one generic pack, several targets, same behaviour
# ---------------------------------------------------------------------------


def test_two_eligible_synthetic_targets_conform(two_targets):
    alpha, beta = two_targets
    report = run_conformance_v2(cases=(_eligible(alpha), _eligible(beta)))
    assert report.is_conformant is True
    assert len(report.cases) == 2
    assert all(case.matched_expectation for case in report.cases)


def test_conformance_requires_at_least_two_targets(two_targets):
    """A "the same pack works everywhere" claim proven against exactly one
    target is not a conformance claim at all."""
    alpha, _beta = two_targets
    report = run_conformance_v2(cases=(_eligible(alpha),))
    assert report.is_conformant is False


def test_identical_targets_differing_only_by_name_produce_identical_outcomes(two_targets):
    """The anti-target-branching property, stated observably: two targets
    whose ONLY difference is their authored repo identity must produce the
    same check names and the same statuses, in the same order."""
    alpha, beta = two_targets
    report = run_conformance_v2(cases=(_eligible(alpha), _eligible(beta)))
    shapes = [
        [(check.name, check.status) for check in case.validate_report.checks] for case in report.cases
    ]
    assert shapes[0] == shapes[1]


def test_conformance_is_deterministic_across_repeated_runs(two_targets):
    alpha, beta = two_targets
    cases = (_eligible(alpha), _eligible(beta))
    first = run_conformance_v2(cases=cases)
    second = run_conformance_v2(cases=cases)
    assert first.summary_tuple() == second.summary_tuple()


# ---------------------------------------------------------------------------
# It must DETECT violations, not merely pass clean targets
# ---------------------------------------------------------------------------


def test_missing_receipt_target_is_detected(two_targets):
    alpha, beta = two_targets
    (beta / RECEIPT_RELATIVE_PATH_V2).unlink()
    report = run_conformance_v2(cases=(_eligible(alpha), _eligible(beta)))
    assert report.is_conformant is False
    failing = [c for c in report.cases if not c.matched_expectation]
    assert [c.case_id for c in failing] == ["beta"]
    assert VALIDATE_RECEIPT_MISSING_REASON_V2 in failing[0].observed_reason_codes


def test_drifted_target_is_detected(two_targets):
    alpha, beta = two_targets
    (beta / _PROFILE_RELATIVE_PATH).write_text(
        _profile_yaml("globex/beta-platform") + "\n# drift\n", encoding="utf-8"
    )
    report = run_conformance_v2(cases=(_eligible(alpha), _eligible(beta)))
    assert report.is_conformant is False
    failing = [c for c in report.cases if not c.matched_expectation]
    assert VALIDATE_TARGET_OWNED_DRIFT_REASON_V2 in failing[0].observed_reason_codes


def test_expected_violation_is_conformant_when_it_actually_violates(tmp_path):
    """A case declared INELIGIBLE must actually fail validation. This is
    what stops conformance from degenerating into "everything passes".

    Uses THREE targets deliberately: after PR #235 review round 2, a
    matrix of one eligible plus one ineligible target is refused for a
    different reason (two singleton cohorts compare nothing), so proving
    the detection property needs a cohort that is genuinely comparable."""
    alpha, beta, gamma = tmp_path / "alpha", tmp_path / "beta", tmp_path / "gamma"
    for path, repo in ((alpha, "acme/alpha"), (beta, "globex/beta"), (gamma, "initech/gamma")):
        path.mkdir()
        _materialize(path, repo)
    (gamma / RECEIPT_RELATIVE_PATH_V2).unlink()

    report = run_conformance_v2(
        cases=(
            _eligible(alpha),
            _eligible(beta),
            ConformanceCaseV2(
                case_id="gamma", target_root=gamma, expectation=ConformanceExpectationV2.INELIGIBLE
            ),
        )
    )
    assert report.is_conformant is True


def test_case_declared_ineligible_that_actually_passes_is_a_conformance_failure(two_targets):
    """The mirror image, and the more dangerous direction: a target the
    matrix says must be refused, but which validates cleanly, means the
    pack failed to detect a violation."""
    alpha, beta = two_targets
    report = run_conformance_v2(
        cases=(
            _eligible(alpha),
            ConformanceCaseV2(
                case_id="beta", target_root=beta, expectation=ConformanceExpectationV2.INELIGIBLE
            ),
        )
    )
    assert report.is_conformant is False


def test_unreadable_target_root_is_a_case_failure_not_a_crash():
    missing = Path(tempfile.mkdtemp()) / "does-not-exist"
    other = Path(tempfile.mkdtemp())
    _materialize(other, "acme/alpha-service")
    try:
        report = run_conformance_v2(
            cases=(_eligible(missing), _eligible(other)),
        )
        assert report.is_conformant is False
        failing = [c for c in report.cases if not c.matched_expectation]
        assert CONFORMANCE_CASE_TARGET_UNREADABLE_REASON_V2 in failing[0].observed_reason_codes
    finally:
        import shutil

        shutil.rmtree(other, ignore_errors=True)


# ---------------------------------------------------------------------------
# Offline / no-mutation invariants
# ---------------------------------------------------------------------------


def test_conformance_never_mutates_any_target(two_targets):
    alpha, beta = two_targets

    def snapshot(root: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            rel = str(path.relative_to(root))
            out[rel] = "dir" if path.is_dir() else _sha256(path.read_bytes())
        return out

    before = (snapshot(alpha), snapshot(beta))
    run_conformance_v2(cases=(_eligible(alpha), _eligible(beta)))
    assert (snapshot(alpha), snapshot(beta)) == before


def test_conformance_makes_no_network_call(two_targets, monkeypatch):
    """Offline by construction: any socket creation during a conformance
    run is an immediate failure, not a slow test."""
    import socket

    def _forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("conformance attempted a network call")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)
    alpha, beta = two_targets
    report = run_conformance_v2(cases=(_eligible(alpha), _eligible(beta)))
    assert report.is_conformant is True


# ---------------------------------------------------------------------------
# PR #235 adversarial review round 1 -- all LIVE_CONFIRMED
# ---------------------------------------------------------------------------


def test_the_same_target_listed_twice_is_not_two_targets(two_targets):
    """F3: the minimum-cases gate counted matrix ENTRIES, not distinct
    target roots, so one directory under two case ids satisfied a claim
    that requires two independent targets."""
    from app.agent_review.target_pack_conformance_v2 import CONFORMANCE_DUPLICATE_TARGET_ROOT_REASON_V2

    alpha, _beta = two_targets
    report = run_conformance_v2(
        cases=(
            ConformanceCaseV2("one", alpha, ConformanceExpectationV2.ELIGIBLE),
            ConformanceCaseV2("two", alpha, ConformanceExpectationV2.ELIGIBLE),
        )
    )
    assert report.is_conformant is False
    assert CONFORMANCE_DUPLICATE_TARGET_ROOT_REASON_V2 in report.reason_codes


def test_a_matrix_of_only_unreadable_roots_is_never_conformant():
    """F4: an unreadable root produced `is_valid=False`, which happened to
    MATCH an `ineligible` expectation -- so a matrix of two nonexistent
    directories reported success having validated nothing at all."""
    missing_a = Path(tempfile.mkdtemp()) / "nope-a"
    missing_b = Path(tempfile.mkdtemp()) / "nope-b"
    report = run_conformance_v2(
        cases=(
            ConformanceCaseV2("a", missing_a, ConformanceExpectationV2.INELIGIBLE),
            ConformanceCaseV2("b", missing_b, ConformanceExpectationV2.INELIGIBLE),
        )
    )
    assert report.is_conformant is False


def test_unreadable_root_fails_even_when_declared_ineligible(two_targets):
    alpha, _beta = two_targets
    missing = Path(tempfile.mkdtemp()) / "nope"
    report = run_conformance_v2(
        cases=(
            _eligible(alpha),
            ConformanceCaseV2("missing", missing, ConformanceExpectationV2.INELIGIBLE),
        )
    )
    assert report.is_conformant is False
    failing = [c for c in report.cases if not c.matched_expectation]
    assert CONFORMANCE_CASE_TARGET_UNREADABLE_REASON_V2 in failing[0].observed_reason_codes


def test_uniformity_is_actually_compared_not_merely_claimed(two_targets):
    """F5, the most consequential: this module's docstring claims to prove
    that targets differing only in authored identity produce identical
    checks/order/statuses -- but the decision compared ONLY each case's
    validity boolean. Two ELIGIBLE-expectation targets could therefore
    produce different check shapes and still be called conformant, which
    is exactly the repository-name branch this is supposed to detect."""
    from app.agent_review.target_pack_conformance_v2 import CONFORMANCE_NON_UNIFORM_SHAPE_REASON_V2

    alpha, beta = two_targets
    # Same expectation, but make beta fail for a reason alpha does not, so
    # the check SHAPES diverge while both remain "as expected" booleans.
    (beta / RECEIPT_RELATIVE_PATH_V2).unlink()
    report = run_conformance_v2(
        cases=(
            ConformanceCaseV2("alpha", alpha, ConformanceExpectationV2.INELIGIBLE),
            ConformanceCaseV2("beta", beta, ConformanceExpectationV2.INELIGIBLE),
        )
    )
    # alpha is actually VALID so it already mismatches; the point is that
    # divergent shapes must be reported, not silently accepted.
    assert report.is_conformant is False


def test_two_ineligible_targets_failing_differently_are_not_uniform(two_targets):
    """The precise F5 scenario: both cases match their INELIGIBLE
    expectation, yet fail for entirely different reasons -- so the boolean
    agreed while the observable behaviour did not."""
    from app.agent_review.target_pack_conformance_v2 import CONFORMANCE_NON_UNIFORM_SHAPE_REASON_V2

    alpha, beta = two_targets
    (alpha / RECEIPT_RELATIVE_PATH_V2).unlink()
    (beta / _PROFILE_RELATIVE_PATH).write_text("not a valid profile", encoding="utf-8")
    report = run_conformance_v2(
        cases=(
            ConformanceCaseV2("alpha", alpha, ConformanceExpectationV2.INELIGIBLE),
            ConformanceCaseV2("beta", beta, ConformanceExpectationV2.INELIGIBLE),
        )
    )
    assert report.is_conformant is False
    assert CONFORMANCE_NON_UNIFORM_SHAPE_REASON_V2 in report.reason_codes


def test_uniform_eligible_targets_remain_conformant(two_targets):
    """Positive control for F5: the fix must not make every run
    non-uniform -- two genuinely equivalent targets still conform."""
    alpha, beta = two_targets
    report = run_conformance_v2(cases=(_eligible(alpha), _eligible(beta)))
    assert report.is_conformant is True


# ---------------------------------------------------------------------------
# PR #235 adversarial review round 2
# ---------------------------------------------------------------------------


def test_singleton_cohorts_cannot_claim_uniformity(two_targets):
    """R1, a gap in round 1's OWN fix: grouping by expectation means a
    minimal matrix of one eligible + one ineligible target produces two
    singleton cohorts, so no two shapes are ever compared and the
    uniformity claim is vacuous -- a target-name branch affecting either
    target escapes entirely."""
    from app.agent_review.target_pack_conformance_v2 import CONFORMANCE_NO_COMPARABLE_COHORT_REASON_V2

    alpha, beta = two_targets
    (beta / RECEIPT_RELATIVE_PATH_V2).unlink()
    report = run_conformance_v2(
        cases=(
            ConformanceCaseV2("alpha", alpha, ConformanceExpectationV2.ELIGIBLE),
            ConformanceCaseV2("beta", beta, ConformanceExpectationV2.INELIGIBLE),
        )
    )
    assert report.is_conformant is False
    assert CONFORMANCE_NO_COMPARABLE_COHORT_REASON_V2 in report.reason_codes


def test_a_cohort_of_two_eligible_targets_satisfies_the_comparison_requirement(two_targets):
    """Positive control for R1: two targets in the SAME cohort are
    genuinely compared, so the uniformity claim is earned."""
    alpha, beta = two_targets
    report = run_conformance_v2(cases=(_eligible(alpha), _eligible(beta)))
    assert report.is_conformant is True


def test_case_ids_must_be_unique_nonempty_strings():
    """R5 is enforced at the library boundary too, not only in the CLI."""
    from app.agent_review.target_pack_conformance_v2 import CONFORMANCE_DUPLICATE_CASE_ID_REASON_V2

    base = Path(tempfile.mkdtemp())
    a, b = base / "a", base / "b"
    a.mkdir()
    b.mkdir()
    _materialize(a, "acme/a")
    _materialize(b, "globex/b")
    try:
        report = run_conformance_v2(
            cases=(
                ConformanceCaseV2("dup", a, ConformanceExpectationV2.ELIGIBLE),
                ConformanceCaseV2("dup", b, ConformanceExpectationV2.ELIGIBLE),
            )
        )
        assert report.is_conformant is False
        assert CONFORMANCE_DUPLICATE_CASE_ID_REASON_V2 in report.reason_codes
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# PR #235 adversarial review round 3
# ---------------------------------------------------------------------------


def test_every_case_root_must_be_unique_not_merely_two_distinct(tmp_path):
    """T1, the third consecutive round to find a gap in the uniformity
    guarantee. Round 2 required "at least two distinct roots overall" and a
    cohort of >= 2 cases -- but a matrix of {A eligible, A eligible, B
    ineligible} satisfies both while the only comparable cohort compares A
    with ITSELF. Uniqueness must be per-case, not a set-size floor."""
    from app.agent_review.target_pack_conformance_v2 import CONFORMANCE_DUPLICATE_TARGET_ROOT_REASON_V2

    alpha, beta = tmp_path / "alpha", tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    _materialize(alpha, "acme/alpha")
    _materialize(beta, "globex/beta")
    (beta / RECEIPT_RELATIVE_PATH_V2).unlink()

    report = run_conformance_v2(
        cases=(
            ConformanceCaseV2("one", alpha, ConformanceExpectationV2.ELIGIBLE),
            ConformanceCaseV2("two", alpha, ConformanceExpectationV2.ELIGIBLE),
            ConformanceCaseV2("three", beta, ConformanceExpectationV2.INELIGIBLE),
        )
    )
    assert report.is_conformant is False
    assert CONFORMANCE_DUPLICATE_TARGET_ROOT_REASON_V2 in report.reason_codes


def test_symlink_cycle_root_is_a_reason_coded_failure_not_a_traceback(tmp_path):
    """T2: `Path.resolve(strict=False)` raises RuntimeError on a symlink
    cycle, and that ran in an eager comprehension before any per-case
    handling -- so a target-authored matrix produced a traceback instead of
    the promised reason-coded result."""
    from app.agent_review.target_pack_conformance_v2 import CONFORMANCE_TARGET_ROOT_UNRESOLVABLE_REASON_V2

    ok = tmp_path / "ok"
    ok.mkdir()
    _materialize(ok, "acme/ok")
    loop_a, loop_b = tmp_path / "loop-a", tmp_path / "loop-b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)

    report = run_conformance_v2(
        cases=(
            ConformanceCaseV2("loop", loop_a, ConformanceExpectationV2.INELIGIBLE),
            ConformanceCaseV2("ok", ok, ConformanceExpectationV2.ELIGIBLE),
        )
    )
    assert report.is_conformant is False
    assert CONFORMANCE_TARGET_ROOT_UNRESOLVABLE_REASON_V2 in report.reason_codes


def test_cases_are_validated_through_the_resolved_root_snapshot(tmp_path):
    """Round 5: uniqueness was gated on resolved snapshots but validation
    re-derived from the mutable `case.target_root`, so a symlink retargeted
    between the two phases could make both cases validate one directory --
    recreating the self-comparison the gate exists to prevent. Proven here
    by retargeting a symlink after resolution: the run must still validate
    the ORIGINAL directories."""
    real_a, real_b = tmp_path / "real-a", tmp_path / "real-b"
    real_a.mkdir()
    real_b.mkdir()
    _materialize(real_a, "acme/a")
    _materialize(real_b, "globex/b")

    link_a, link_b = tmp_path / "link-a", tmp_path / "link-b"
    link_a.symlink_to(real_a)
    link_b.symlink_to(real_b)

    cases = (
        ConformanceCaseV2("a", link_a, ConformanceExpectationV2.ELIGIBLE),
        ConformanceCaseV2("b", link_b, ConformanceExpectationV2.ELIGIBLE),
    )
    report = run_conformance_v2(cases=cases)
    assert report.is_conformant is True
    # Each case reports the resolved directory it actually validated, not
    # the symlink it was named by.
    validated = sorted(case.validate_report.target_root for case in report.cases)
    assert validated == sorted([str(real_a.resolve()), str(real_b.resolve())])


# ===========================================================================
# CLASS 3 FAMILY: the uniformity property, stated once and tested as a matrix
# ===========================================================================


def test_class3_same_authored_identity_in_two_dirs_does_not_prove_uniformity(tmp_path):
    """Two copies of ONE installation have distinct roots and identical
    authored identity, so comparing them proves only determinism -- a
    repository-name branch keyed on any other identity stays invisible."""
    from app.agent_review.target_pack_conformance_v2 import CONFORMANCE_SINGLE_AUTHORED_IDENTITY_REASON_V2

    a, b = tmp_path / "copy-a", tmp_path / "copy-b"
    a.mkdir()
    b.mkdir()
    _materialize(a, "acme/same-service")
    _materialize(b, "acme/same-service")

    report = run_conformance_v2(cases=(_eligible(a), _eligible(b)))
    assert report.is_conformant is False
    assert CONFORMANCE_SINGLE_AUTHORED_IDENTITY_REASON_V2 in report.reason_codes


def test_class3_distinct_authored_identities_do_prove_uniformity(two_targets):
    """Positive control: distinct roots AND distinct authored identities."""
    alpha, beta = two_targets
    assert run_conformance_v2(cases=(_eligible(alpha), _eligible(beta))).is_conformant is True


def test_class4_identity_cannot_come_from_a_receipt_swapped_after_validation(tmp_path, monkeypatch):
    """Round 7, Class 3/4 recurrence: the distinctness property used to
    RE-READ the receipt after `run_validate_v2` returned.

    Routing that second read through validate's own contained loaders
    inherited containment and the parse boundary but NOT the snapshot, so
    a receipt replaced in between produced checks describing installation
    A and an identity taken from installation B. Two copies of ONE target
    could then be made to look like two distinct identities and satisfy a
    uniformity claim that compared nothing.

    Simulates exactly that race: validation runs against `acme/same`, then
    the receipt is swapped before the identity would have been read.
    """
    from app.agent_review import target_pack_conformance_v2 as conformance_module
    from app.agent_review.target_pack_conformance_v2 import (
        CONFORMANCE_SINGLE_AUTHORED_IDENTITY_REASON_V2,
    )

    a, b = tmp_path / "copy-a", tmp_path / "copy-b"
    a.mkdir()
    b.mkdir()
    _materialize(a, "acme/same")
    _materialize(b, "acme/same")

    real_run_validate = conformance_module.run_validate_v2
    swapped_in = {str(a.resolve()): "attacker/alpha", str(b.resolve()): "attacker/beta"}

    def swap_receipt_after_validating(*, target_root):
        report = real_run_validate(target_root=target_root)
        replacement = swapped_in.get(str(Path(target_root).resolve()))
        if replacement is not None:  # the window between the two reads
            _materialize(Path(target_root), replacement)
        return report

    monkeypatch.setattr(conformance_module, "run_validate_v2", swap_receipt_after_validating)

    report = run_conformance_v2(cases=(_eligible(a), _eligible(b)))

    # Identity must describe what was VALIDATED, not what is on disk now.
    assert [case.authored_identity for case in report.cases] == ["acme/same", "acme/same"]
    assert report.is_conformant is False
    assert CONFORMANCE_SINGLE_AUTHORED_IDENTITY_REASON_V2 in report.reason_codes


def test_class4_no_target_root_taking_identity_entry_point_survives(tmp_path):
    """Guards against reintroducing the second read path itself.

    The round-7 defect was reachable only because an identity helper took
    a `target_root` and went to disk on its own. Identity now ships on the
    report, so no such entry point should exist to be called again."""
    from app.agent_review import target_pack_validate_v2

    assert not hasattr(target_pack_validate_v2, "authored_target_identity_v2")
