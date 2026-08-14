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


def test_expected_violation_is_conformant_when_it_actually_violates(two_targets):
    """A case declared INELIGIBLE must actually fail validation. This is
    what stops conformance from degenerating into "everything passes"."""
    alpha, beta = two_targets
    (beta / RECEIPT_RELATIVE_PATH_V2).unlink()
    report = run_conformance_v2(
        cases=(
            _eligible(alpha),
            ConformanceCaseV2(
                case_id="beta", target_root=beta, expectation=ConformanceExpectationV2.INELIGIBLE
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
