"""`#203-C2` -- tests for the successor `validate` command (`app.agent_
review.target_pack_validate_v2`), a structural rewrite of the forensic
prior attempt (PR #242, STOP/REDESIGN'd after three rounds converged on
the same ad-hoc-projection boundary). This file re-derives every
property against the NEW typed observation algebra; nothing is copied
from #242's test file, which was consulted only as a forensic corpus of
properties/reproducers.

Fixture style deliberately mirrors `test_target_pack_doctor_v2.py`'s own
(`_sha256`, `_VALID_PROFILE_YAML`, `_receipt`) and this repository's other
`target_pack_*` test modules for the same reasons those established: each
module owns its own fixtures so it stays independently collectible.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from app.agent_review.profile_loader_v2 import (
    DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
    compute_profile_hash_v2,
    load_target_profile_text_v2,
)
from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_RELATIVE_PATH_V2,
    ReceiptIdentityRefV2,
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
    compute_target_install_receipt_hash_v2,
    load_target_install_receipt_bytes_v2,
)
from app.agent_review.target_pack_validate_v2 import (
    AIOPS_MISSING_REASON_V2,
    AIOPS_NOT_A_DIRECTORY_REASON_V2,
    AIOPS_UNREADABLE_REASON_V2,
    GENERATED_FILE_DRIFT_REASON_V2,
    GENERATED_FILE_MISSING_REASON_V2,
    GENERATED_FILE_NOT_A_REGULAR_FILE_REASON_V2,
    GENERATED_FILE_UNREADABLE_REASON_V2,
    OBSERVATION_BUDGET_EXCEEDED_REASON_V2,
    PATH_ESCAPES_TARGET_ROOT_REASON_V2,
    PATH_RESOLUTION_FAILED_REASON_V2,
    PREVIOUS_INSTALL_LINEAGE_REASON_V2,
    PROFILE_IDENTITY_MISMATCH_REASON_V2,
    PROFILE_MISSING_REASON_V2,
    PROFILE_RESOURCE_LIMIT_EXCEEDED_REASON_V2,
    PROFILE_UNREADABLE_REASON_V2,
    RECEIPT_INVALID_REASON_V2,
    RECEIPT_MISSING_REASON_V2,
    RECEIPT_RESOURCE_LIMIT_EXCEEDED_REASON_V2,
    RECEIPT_UNREADABLE_REASON_V2,
    STATUS_FAIL_V2,
    STATUS_PASS_V2,
    STATUS_UNAVAILABLE_V2,
    TARGET_OWNED_DRIFT_REASON_V2,
    TARGET_OWNED_MISSING_REASON_V2,
    TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2,
    TARGET_OWNED_UNREADABLE_REASON_V2,
    TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2,
    TARGET_ROOT_UNREADABLE_REASON_V2,
    TARGET_ROOT_UNRESOLVABLE_REASON_V2,
    UNVALIDATED_CAPABILITIES_V2,
    VALIDATE_CHECK_ORDER_V2,
    ValidateCheckV2,
    ValidateReportV2,
    run_validate_v2,
)

# --- Fixture builders -------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_VALID_PROFILE_YAML = """
schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: owner/repo
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
    - some-check
  allowed_semantic_groups:
    - primary_backend_logic
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts: []
limitations: []
"""


def _profile_hash_of(raw_text: str) -> str:
    return compute_profile_hash_v2(load_target_profile_text_v2(raw_text))


def _real_profile_hash() -> str:
    return _profile_hash_of(_VALID_PROFILE_YAML)


def _receipt(**overrides: object) -> TargetInstallReceiptV2:
    fields: dict[str, object] = dict(
        schema_id="agent-review.target-install-receipt.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        manifest_digest="a" * 64,
        target_repo="owner/repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="owner/repo"),
        target_profile_hash=_real_profile_hash(),
        target_policy_hash=None,
        review_pack_hashes={},
        generated_file_hashes={},
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        required_capabilities=(),
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


def _install(
    root: Path, *, receipt: TargetInstallReceiptV2 | None, profile_text: str | None = _VALID_PROFILE_YAML
) -> None:
    (root / ".aiops").mkdir(parents=True, exist_ok=True)
    if profile_text is not None:
        (root / ".aiops" / "target-profile.v2.yaml").write_text(profile_text, encoding="utf-8")
    if receipt is not None:
        (root / ".aiops" / "install-receipt.v2.json").write_text(
            json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
        )


def _check(report: ValidateReportV2, name: str) -> ValidateCheckV2:
    for check in report.checks:
        if check.name == name:
            return check
    raise AssertionError(f"no check named {name!r} in report: {report.checks}")


def _check_or_none(report: ValidateReportV2, name: str) -> ValidateCheckV2 | None:
    for check in report.checks:
        if check.name == name:
            return check
    return None


def _decision_surface(report: ValidateReportV2) -> tuple[object, ...]:
    """Excludes `target_root_real`, which correctly differs whenever two
    invocations were pointed at different roots."""

    return (report.checks, report.is_valid, report.unvalidated_capabilities)


def _snapshot_tree(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = _sha256(path.read_bytes())
    return snapshot


# --- Happy path / determinism ------------------------------------------------


def test_valid_installation_validates(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)
    assert report.is_valid is True
    assert report.target_root_real == str(tmp_path.resolve())


def test_validate_is_deterministic_across_repeated_runs(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    a = run_validate_v2(target_root=tmp_path)
    b = run_validate_v2(target_root=tmp_path)
    assert a.checks == b.checks
    assert a.is_valid == b.is_valid


def test_emission_order_is_a_subsequence_with_no_duplicate_names(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)
    names = [c.name for c in report.checks]
    assert len(names) == len(set(names))
    indices = [VALIDATE_CHECK_ORDER_V2.index(n) for n in names]
    assert indices == sorted(indices)


def test_every_emitted_reason_code_is_owned_by_validate(tmp_path_factory: pytest.TempPathFactory) -> None:
    roots = [tmp_path_factory.mktemp(f"reason_{i}") for i in range(3)]
    _install(roots[0], receipt=_receipt())
    (roots[1] / ".aiops").mkdir()
    for root in roots:
        report = run_validate_v2(target_root=root)
        for check in report.checks:
            if check.reason_code is not None:
                assert check.reason_code.startswith("target_pack_validate_"), check.reason_code


def test_unavailable_capability_is_listed_and_does_not_count_as_validated(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)
    for name, reason in UNVALIDATED_CAPABILITIES_V2:
        capability_check = _check(report, name)
        assert capability_check.status == STATUS_UNAVAILABLE_V2
        assert capability_check.reason_code == reason
    assert report.is_valid is True
    assert set(report.unvalidated_capabilities) == {name for name, _ in UNVALIDATED_CAPABILITIES_V2}


def test_every_return_path_emits_the_full_unavailable_block(tmp_path_factory: pytest.TempPathFactory) -> None:
    roots = [
        tmp_path_factory.mktemp("missing_root_parent") / "never-created",
        tmp_path_factory.mktemp("empty"),
        tmp_path_factory.mktemp("healthy_root"),
    ]
    _install(roots[2], receipt=_receipt())
    for root in roots:
        report = run_validate_v2(target_root=root)
        for name, reason in UNVALIDATED_CAPABILITIES_V2:
            check = _check(report, name)
            assert check.status == STATUS_UNAVAILABLE_V2
            assert check.reason_code == reason


def test_validate_does_not_create_a_missing_target_root(tmp_path: Path) -> None:
    missing = tmp_path / "never-created"
    run_validate_v2(target_root=missing)
    assert not missing.exists()


def test_validate_never_mutates_the_target(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    before = _snapshot_tree(tmp_path)
    run_validate_v2(target_root=tmp_path)
    after = _snapshot_tree(tmp_path)
    assert before == after


# --- Structural fail-closed: target_root / .aiops ---------------------------


def test_target_root_not_a_directory_fails_closed(tmp_path_factory: pytest.TempPathFactory) -> None:
    parent = tmp_path_factory.mktemp("parent")
    not_a_dir = parent / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    report = run_validate_v2(target_root=not_a_dir)
    check = _check(report, "target_root")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2
    assert _check_or_none(report, "aiops_snapshot") is None


def test_target_root_itself_a_symlink_loop_is_refused(tmp_path_factory: pytest.TempPathFactory) -> None:
    parent = tmp_path_factory.mktemp("loop_parent")
    loop = parent / "loop"
    loop.symlink_to("loop")
    report = run_validate_v2(target_root=loop)
    check = _check(report, "target_root")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_ROOT_UNRESOLVABLE_REASON_V2


def test_target_root_metadata_permission_error_is_unreadable_not_a_traceback(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes a residual PR #242 never fixed: `target_root_real.is_dir()`
    used to sit outside any `OSError` boundary."""

    root = tmp_path_factory.mktemp("root_perm")
    root_real = root.resolve()
    real_is_dir = Path.is_dir

    def raising_is_dir(self: Path) -> bool:
        if self == root_real:
            raise PermissionError(13, "denied")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", raising_is_dir)
    report = run_validate_v2(target_root=root)  # must not raise
    check = _check(report, "target_root")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_ROOT_UNREADABLE_REASON_V2


def test_aiops_absent_is_reported_missing(tmp_path: Path) -> None:
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "aiops_snapshot")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == AIOPS_MISSING_REASON_V2
    assert report.is_valid is False


def test_aiops_regular_file_is_not_a_directory(tmp_path: Path) -> None:
    (tmp_path / ".aiops").write_text("not a directory", encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "aiops_snapshot")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == AIOPS_NOT_A_DIRECTORY_REASON_V2


def test_aiops_directory_permission_error_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".aiops").mkdir()
    aiops_real = (tmp_path / ".aiops").resolve()
    real_exists = Path.exists

    def raising_exists(self: Path) -> bool:
        if self == aiops_real:
            raise PermissionError(13, "denied")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", raising_exists)
    report = run_validate_v2(target_root=tmp_path)  # must not raise
    check = _check(report, "aiops_snapshot")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == AIOPS_UNREADABLE_REASON_V2


def test_aiops_symlink_escape_is_refused(tmp_path_factory: pytest.TempPathFactory) -> None:
    root = tmp_path_factory.mktemp("aiops_escape")
    outside = tmp_path_factory.mktemp("outside")
    (root / ".aiops").symlink_to(outside, target_is_directory=True)
    report = run_validate_v2(target_root=root)
    check = _check(report, "aiops_snapshot")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_aiops_directory_positive_control_passes(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "aiops_snapshot")
    assert check.status == STATUS_PASS_V2


@pytest.mark.parametrize(
    "make_bad_aiops",
    [
        pytest.param(lambda root: None, id="aiops_missing"),
        pytest.param(lambda root: (root / ".aiops").write_text("x", encoding="utf-8"), id="aiops_not_a_directory"),
    ],
)
def test_downstream_checks_are_absent_when_aiops_snapshot_fails(tmp_path: Path, make_bad_aiops) -> None:
    make_bad_aiops(tmp_path)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "aiops_snapshot").status == STATUS_FAIL_V2
    present = {c.name for c in report.checks}
    for absent in (
        "receipt", "profile", "profile_hash", "profile_identity", "root_identity",
        "observation_budget", "target_owned_integrity", "generated_file_integrity",
    ):
        assert absent not in present, absent


# --- Receipt/profile artifact taxonomy --------------------------------------


def test_missing_receipt_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_MISSING_REASON_V2


def test_missing_profile_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt(), profile_text=None)
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PROFILE_MISSING_REASON_V2


def test_unparseable_receipt_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text("not json", encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_INVALID_REASON_V2


def test_receipt_directory_reports_missing(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    (tmp_path / ".aiops" / "install-receipt.v2.json").unlink()
    (tmp_path / ".aiops" / "install-receipt.v2.json").mkdir()
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_MISSING_REASON_V2


def test_receipt_permission_error_is_unreadable_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, receipt=_receipt())
    receipt_real = (tmp_path / ".aiops" / "install-receipt.v2.json").resolve()
    real_exists = Path.exists

    def raising_exists(self: Path) -> bool:
        if self == receipt_real:
            raise PermissionError(13, "denied")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", raising_exists)
    report = run_validate_v2(target_root=tmp_path)  # must not raise
    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_UNREADABLE_REASON_V2


def test_receipt_above_budget_is_resource_limit_exceeded(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    import app.agent_review.target_pack_validate_v2 as validate_module

    oversized = b"{" + b"x" * (validate_module._ARTIFACT_BYTE_LIMIT_V2 + 1)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_bytes(oversized)
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_RESOURCE_LIMIT_EXCEEDED_REASON_V2


def test_profile_above_budget_is_resource_limit_exceeded(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt(), profile_text=None)
    import app.agent_review.target_pack_validate_v2 as validate_module

    oversized = "schema_id: " + "x" * (validate_module._ARTIFACT_BYTE_LIMIT_V2 + 1)
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(oversized, encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PROFILE_RESOURCE_LIMIT_EXCEEDED_REASON_V2


def test_artifact_byte_read_exact_limit_boundary_is_precise(tmp_path: Path) -> None:
    import app.agent_review.target_pack_validate_v2 as validate_module

    limit = validate_module._ARTIFACT_BYTE_LIMIT_V2
    at_limit = tmp_path / "at-limit"
    at_limit.write_bytes(b"x" * limit)
    observation = validate_module._observe_bounded_artifact_v2(at_limit)
    assert isinstance(observation, validate_module.BufferedFile)
    assert len(observation.content) == limit

    over_limit = tmp_path / "over-limit"
    over_limit.write_bytes(b"x" * (limit + 1))
    observation2 = validate_module._observe_bounded_artifact_v2(over_limit)
    assert isinstance(observation2, validate_module.ResourceLimitExceeded)


def test_receipt_parser_is_never_called_after_a_budget_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, receipt=None)
    import app.agent_review.target_pack_validate_v2 as validate_module

    oversized = b"{" + b"x" * (validate_module._ARTIFACT_BYTE_LIMIT_V2 + 1)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_bytes(oversized)

    calls: list[bytes] = []
    real_parser = validate_module.load_target_install_receipt_bytes_v2

    def spying_parser(raw: bytes):
        calls.append(raw)
        return real_parser(raw)

    monkeypatch.setattr(validate_module, "load_target_install_receipt_bytes_v2", spying_parser)
    report = run_validate_v2(target_root=tmp_path)
    assert calls == []
    assert _check(report, "receipt").reason_code == RECEIPT_RESOURCE_LIMIT_EXCEEDED_REASON_V2


def test_profile_byte_integrity_is_independent_of_semantic_validity(tmp_path: Path) -> None:
    malformed = "not: [a valid: profile"
    receipt = _receipt(target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(malformed.encode())})
    _install(tmp_path, receipt=receipt, profile_text=malformed)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "profile").status == STATUS_FAIL_V2
    assert _check(report, "target_owned_integrity").status == STATUS_PASS_V2


def test_profile_invalid_utf8_bytes_is_reported_unreadable_not_a_traceback(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_bytes(b"\xff\xfe\x00\x01not utf-8")
    report = run_validate_v2(target_root=tmp_path)  # must not raise
    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PROFILE_UNREADABLE_REASON_V2


# --- profile_hash / profile_identity / root_identity ------------------------


def test_profile_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(target_profile_hash="f" * 64)
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "profile_hash").status == STATUS_FAIL_V2


def test_root_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(portable_target_root_identity="f" * 64)
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "root_identity").status == STATUS_FAIL_V2


def test_foreign_profile_with_a_self_consistent_receipt_is_refused(tmp_path: Path) -> None:
    foreign = _VALID_PROFILE_YAML.replace("repo: owner/repo", "repo: someone/else")
    receipt = _receipt(
        target_profile_hash=_profile_hash_of(foreign),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(foreign.encode())},
    )
    _install(tmp_path, receipt=receipt, profile_text=foreign)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "profile_identity").status == STATUS_FAIL_V2
    assert _check(report, "profile_identity").reason_code == PROFILE_IDENTITY_MISMATCH_REASON_V2
    assert _check(report, "profile_hash").status == STATUS_PASS_V2
    assert _check(report, "root_identity").status == STATUS_PASS_V2


def test_uncustomized_seed_identity_placeholder_is_not_a_mismatch(tmp_path: Path) -> None:
    placeholder = _VALID_PROFILE_YAML.replace("repo: owner/repo", "repo: OWNER/REPO")
    receipt = _receipt(
        target_profile_hash=_profile_hash_of(placeholder),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(placeholder.encode())},
    )
    _install(tmp_path, receipt=receipt, profile_text=placeholder)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "profile_identity").status == STATUS_PASS_V2


def test_seed_placeholder_matches_operation_planning_acceptance() -> None:
    """`#203-C1.amend`: the placeholder is now a SHARED authority
    (`target_pack_build_v2.SEED_PROFILE_IDENTITY_PLACEHOLDER_V2`) imported
    by both the writer (`operation`) and this reader -- this is a
    BEHAVIORAL proof that the shared value is genuinely accepted by real
    operation planning, complementing (not replacing) the static
    single-authority proof in `test_target_pack_arch_v2.py`."""

    from app.agent_review.target_pack_build_v2 import SEED_PROFILE_IDENTITY_PLACEHOLDER_V2 as build_placeholder
    from app.agent_review.target_pack_manifest_v2 import GeneratedFileEntryV2, TargetPackFileOwnershipV2, TargetPackManifestV2
    from app.agent_review.target_pack_operation_v2 import compute_target_pack_operation_plan_v2
    from app.agent_review.target_pack_validate_v2 import SEED_PROFILE_IDENTITY_PLACEHOLDER_V2 as validate_placeholder

    assert build_placeholder == validate_placeholder == "OWNER/REPO"

    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/target-profile.v2.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=(),
        min_engine_contract_version=2,
        max_supported_rollout_mode="off",
    )
    placeholder_content = _VALID_PROFILE_YAML.replace("repo: owner/repo", f"repo: {build_placeholder}").encode()
    result = compute_target_pack_operation_plan_v2(
        manifest=manifest,
        target_root=Path("/nonexistent-for-preview-only"),
        target_repo="owner/repo",
        rollout="off",
        seed_content_by_path={".aiops/target-profile.v2.yaml": placeholder_content},
        previous_receipt=None,
    )
    assert result is not None  # accepted without raising OPERATION_FOREIGN_IDENTITY_REASON_V2


# --- Safe counterexamples: identity/epistemic boundary ----------------------


def test_generated_at_difference_does_not_change_disposition(tmp_path_factory: pytest.TempPathFactory) -> None:
    root_a = tmp_path_factory.mktemp("gen_at_a")
    root_b = tmp_path_factory.mktemp("gen_at_b")
    a = _receipt(generated_at="2026-01-01T00:00:00Z")
    b = _receipt(generated_at="2026-06-01T00:00:00Z")
    assert a.receipt_hash == b.receipt_hash
    _install(root_a, receipt=a)
    _install(root_b, receipt=b)
    report_a = run_validate_v2(target_root=root_a)
    report_b = run_validate_v2(target_root=root_b)
    assert _decision_surface(report_a) == _decision_surface(report_b)


def test_upgrade_shaped_lineage_is_not_refused(tmp_path: Path) -> None:
    ref = ReceiptIdentityRefV2(receipt_hash="e" * 64, pack_version="0.0.9", toolrepo_sha="2" * 40)
    receipt = _receipt(previous_install_identity=ref)
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    assert report.is_valid is True


def test_previous_install_lineage_never_claims_verified(tmp_path: Path) -> None:
    ref = ReceiptIdentityRefV2(receipt_hash="e" * 64, pack_version="0.1.0", toolrepo_sha="2" * 40)
    receipt = _receipt(previous_install_identity=ref)
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "previous_install_lineage")
    assert check.status == STATUS_UNAVAILABLE_V2
    assert check.reason_code == PREVIOUS_INSTALL_LINEAGE_REASON_V2


def test_major_incompatible_receipt_does_not_change_disposition(tmp_path: Path) -> None:
    receipt = _receipt(compatibility="major_incompatible")
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    assert report.is_valid is True
    assert {c.name for c in report.checks} == {c.name for c in run_validate_v2(target_root=tmp_path).checks}


def test_target_owned_paths_in_non_sorted_order_is_accepted(tmp_path: Path) -> None:
    extra = tmp_path / "z-extra.txt"
    receipt = _receipt(
        target_owned_paths=("z-extra.txt", ".aiops/target-profile.v2.yaml"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            "z-extra.txt": _sha256(b"extra"),
        },
    )
    _install(tmp_path, receipt=receipt)
    extra.write_bytes(b"extra")
    report = run_validate_v2(target_root=tmp_path)
    assert report.is_valid is True


def test_receipt_omitting_the_profile_from_the_ledger_still_validates(tmp_path: Path) -> None:
    receipt = _receipt(target_owned_file_hashes={}, target_owned_paths=())
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "profile").status == STATUS_PASS_V2
    assert _check(report, "profile_hash").status == STATUS_PASS_V2
    assert "target_owned_set" in report.unvalidated_capabilities
    assert report.is_valid is True


# --- Ledger integrity: target_owned ------------------------------------------


def test_target_owned_file_deleted_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    (tmp_path / ".aiops" / "target-profile.v2.yaml").unlink()
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_MISSING_REASON_V2


def test_target_owned_not_a_regular_file_is_reported_distinctly(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "not-a-file").mkdir()
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/not-a-file"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/not-a-file": "a" * 64,
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2


def test_target_owned_unreadable_is_reported_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, receipt=None)
    extra_path = tmp_path / ".aiops" / "extra-owned.txt"
    extra_path.write_bytes(b"extra content")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/extra-owned.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/extra-owned.txt": _sha256(b"extra content"),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")
    extra_real = extra_path.resolve()
    real_open = Path.open

    def selectively_raising_open(self: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode")
        if self.resolve() == extra_real and mode == "rb":
            raise OSError("simulated permission denied")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", selectively_raising_open)
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_UNREADABLE_REASON_V2


def test_declared_target_owned_path_escaping_via_symlink_is_refused(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    (target_root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    outside_owned = outside / "owned.txt"
    outside_owned.write_text("outside content", encoding="utf-8")
    (target_root / ".aiops" / "owned.txt").symlink_to(outside_owned)
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/owned.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/owned.txt": _sha256(b"outside content"),
        },
    )
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")
    report = run_validate_v2(target_root=target_root)
    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_target_owned_symlink_loop_is_resolution_failed_not_escape(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "loopy.txt").symlink_to("loopy.txt")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/loopy.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/loopy.txt": "c" * 64,
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_RESOLUTION_FAILED_REASON_V2


def test_target_owned_disposition_is_independent_of_json_member_order(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root_a = tmp_path_factory.mktemp("order_a")
    root_b = tmp_path_factory.mktemp("order_b")
    ledger = {
        ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
        ".aiops/a-missing.txt": "a" * 64,
        ".aiops/b-drifted.txt": "b" * 64,
        ".aiops/c-intact.txt": _sha256(b"intact content"),
    }
    receipt = _receipt(target_owned_paths=tuple(sorted(ledger)), target_owned_file_hashes=ledger)
    raw_dump = receipt.model_dump(mode="json")

    def raw_json_with_order(order: list[str]) -> str:
        d = dict(raw_dump)
        d["target_owned_file_hashes"] = {k: ledger[k] for k in order}
        return json.dumps(d)

    order_a = list(ledger)
    order_b = list(reversed(order_a))
    raw_a, raw_b = raw_json_with_order(order_a), raw_json_with_order(order_b)
    assert raw_a != raw_b
    receipt_a = load_target_install_receipt_bytes_v2(raw_a)
    receipt_b = load_target_install_receipt_bytes_v2(raw_b)
    assert receipt_a.receipt_hash == receipt_b.receipt_hash

    for root, raw in ((root_a, raw_a), (root_b, raw_b)):
        (root / ".aiops").mkdir()
        (root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
        (root / ".aiops" / "install-receipt.v2.json").write_text(raw, encoding="utf-8")
        (root / ".aiops" / "b-drifted.txt").write_bytes(b"WRONG content")
        (root / ".aiops" / "c-intact.txt").write_bytes(b"intact content")

    report_a = run_validate_v2(target_root=root_a)
    report_b = run_validate_v2(target_root=root_b)
    assert _decision_surface(report_a) == _decision_surface(report_b)


# --- Ledger integrity: generated_file ----------------------------------------


def test_generated_file_declared_and_deleted_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(generated_file_hashes={"generated-workflow.yml": "d" * 64})
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == GENERATED_FILE_MISSING_REASON_V2


def test_generated_file_declared_and_drifted_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(generated_file_hashes={"generated-workflow.yml": "d" * 64})
    _install(tmp_path, receipt=receipt)
    (tmp_path / "generated-workflow.yml").write_bytes(b"actual content")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == GENERATED_FILE_DRIFT_REASON_V2


def test_generated_file_not_a_regular_file_is_reported_distinctly(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / "generated-dir").mkdir()
    receipt = _receipt(generated_file_hashes={"generated-dir": "a" * 64})
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == GENERATED_FILE_NOT_A_REGULAR_FILE_REASON_V2


def test_generated_file_unreadable_is_reported_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, receipt=None)
    generated_path = tmp_path / "generated-workflow.yml"
    generated_path.write_bytes(b"generated content")
    receipt = _receipt(generated_file_hashes={"generated-workflow.yml": _sha256(b"generated content")})
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")
    generated_real = generated_path.resolve()
    real_open = Path.open

    def selectively_raising_open(self: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode")
        if self.resolve() == generated_real and mode == "rb":
            raise OSError("simulated permission denied")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", selectively_raising_open)
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == GENERATED_FILE_UNREADABLE_REASON_V2


def test_generated_file_symlink_escape_is_refused(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    (target_root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    outside_generated = outside / "generated.yml"
    outside_generated.write_text("outside content", encoding="utf-8")
    (target_root / "generated.yml").symlink_to(outside_generated)
    receipt = _receipt(generated_file_hashes={"generated.yml": _sha256(b"outside content")})
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")
    report = run_validate_v2(target_root=target_root)
    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_generated_file_symlink_loop_is_resolution_failed_not_escape(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / "loopy.yml").symlink_to("loopy.yml")
    receipt = _receipt(generated_file_hashes={"loopy.yml": "c" * 64})
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_RESOLUTION_FAILED_REASON_V2


def test_generated_file_empty_mapping_passes_while_generated_file_set_stays_unavailable(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt(generated_file_hashes={}))
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_PASS_V2
    assert "generated_file_set" in report.unvalidated_capabilities
    assert report.is_valid is True


def test_generated_file_disposition_is_independent_of_json_member_order(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    root_a = tmp_path_factory.mktemp("gen_order_a")
    root_b = tmp_path_factory.mktemp("gen_order_b")
    ledger = {"a-missing.yml": "a" * 64, "b-drifted.yml": "b" * 64, "c-intact.yml": _sha256(b"intact content")}
    receipt = _receipt(generated_file_hashes=ledger)
    raw_dump = receipt.model_dump(mode="json")

    def raw_json_with_order(order: list[str]) -> str:
        d = dict(raw_dump)
        d["generated_file_hashes"] = {k: ledger[k] for k in order}
        return json.dumps(d)

    order_a = list(ledger)
    order_b = list(reversed(order_a))
    raw_a, raw_b = raw_json_with_order(order_a), raw_json_with_order(order_b)
    receipt_a = load_target_install_receipt_bytes_v2(raw_a)
    receipt_b = load_target_install_receipt_bytes_v2(raw_b)
    assert receipt_a.receipt_hash == receipt_b.receipt_hash

    for root, raw in ((root_a, raw_a), (root_b, raw_b)):
        (root / ".aiops").mkdir()
        (root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
        (root / ".aiops" / "install-receipt.v2.json").write_text(raw, encoding="utf-8")
        (root / "c-intact.yml").write_bytes(b"intact content")

    report_a = run_validate_v2(target_root=root_a)
    report_b = run_validate_v2(target_root=root_b)
    assert _decision_surface(report_a) == _decision_surface(report_b)


def test_distinct_ledger_paths_across_both_ledgers_remain_legal(tmp_path: Path) -> None:
    """Safe counterexample: two distinct files under budget, one in each
    ledger, both validate -- the ordinary case."""

    receipt = _receipt(generated_file_hashes={"workflow.yml": _sha256(b"wf content")})
    _install(tmp_path, receipt=receipt)
    (tmp_path / "workflow.yml").write_bytes(b"wf content")
    report = run_validate_v2(target_root=tmp_path)
    assert report.is_valid is True


# --- Observation budget ------------------------------------------------------


def test_observation_budget_claims_exceeded_fails_before_any_content_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_validate_v2 as validate_module

    monkeypatch.setattr(validate_module, "_DEFAULT_MAX_LEDGER_CLAIMS_V2", 1)
    receipt = _receipt(generated_file_hashes={"never-read.yml": "d" * 64})
    _install(tmp_path, receipt=receipt)
    # target_owned already declares 1 claim (the profile); this pushes to 2.

    read_calls: list[str] = []
    real_stream = validate_module._observe_streamed_ledger_entry_v2

    def spying_stream(path, budget):
        read_calls.append(str(path))
        return real_stream(path, budget)

    monkeypatch.setattr(validate_module, "_observe_streamed_ledger_entry_v2", spying_stream)
    report = run_validate_v2(target_root=tmp_path)
    ob = _check(report, "observation_budget")
    assert ob.status == STATUS_FAIL_V2
    assert ob.reason_code == OBSERVATION_BUDGET_EXCEEDED_REASON_V2
    assert not any("never-read.yml" in c for c in read_calls)


def test_claim_ceiling_admission_precedes_plan_materialization(tmp_path: Path) -> None:
    """`#203-C2` Codex Round 1, P2-B: reproduced against PR #244 exact
    HEAD b76bb806 with a structural witness (not timing-based) --
    `_LedgerClaimV2` objects were constructed and sorted for the WHOLE
    combined claim set before `debit_claim()` ever ran, so a receipt
    declaring far more than `max_claims` short claims (well within the
    receipt artifact's own byte ceiling) did work proportional to every
    declared claim before the budget could refuse any of it. Fixed by
    admitting on `len(target_owned_file_hashes) + len(generated_file_
    hashes)` BEFORE compiling the plan at all."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    n_over = validate_module._DEFAULT_MAX_LEDGER_CLAIMS_V2 + 500
    generated_hashes = {f"f{i}.txt": "a" * 64 for i in range(n_over)}
    receipt = _receipt(generated_file_hashes=generated_hashes)
    _install(tmp_path, receipt=receipt)

    construction_count = {"n": 0}
    real_init = validate_module._LedgerClaimV2.__init__

    def counting_init(self, *args, **kwargs):
        construction_count["n"] += 1
        return real_init(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(validate_module._LedgerClaimV2, "__init__", counting_init)
        report = run_validate_v2(target_root=tmp_path)

    assert construction_count["n"] == 0, (
        f"{construction_count['n']} _LedgerClaimV2 objects were constructed before the "
        "claim-count refusal -- admission must happen strictly before plan materialization"
    )
    ob = _check(report, "observation_budget")
    assert ob.status == STATUS_FAIL_V2
    assert ob.reason_code == OBSERVATION_BUDGET_EXCEEDED_REASON_V2
    assert report.is_valid is False


def test_claim_ceiling_boundary_is_exact(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Safe counterexamples: exactly `max_ledger_claims` is admitted;
    `max_ledger_claims + 1` is refused before plan materialization; 0
    claims preserves existing semantics."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    max_claims = validate_module._DEFAULT_MAX_LEDGER_CLAIMS_V2

    root_at = tmp_path_factory.mktemp("at_ceiling")
    # target_owned already declares 1 claim (the profile); (max_claims - 1)
    # generated claims brings the COMBINED total to exactly max_claims.
    receipt_at = _receipt(generated_file_hashes={f"f{i}.txt": "a" * 64 for i in range(max_claims - 1)})
    _install(root_at, receipt=receipt_at)
    report_at = run_validate_v2(target_root=root_at)
    assert _check(report_at, "observation_budget").status == STATUS_PASS_V2

    root_over = tmp_path_factory.mktemp("over_ceiling")
    receipt_over = _receipt(generated_file_hashes={f"f{i}.txt": "a" * 64 for i in range(max_claims)})
    _install(root_over, receipt=receipt_over)
    report_over = run_validate_v2(target_root=root_over)
    assert _check(report_over, "observation_budget").status == STATUS_FAIL_V2


def test_observation_budget_unique_paths_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_review.target_pack_validate_v2 as validate_module

    monkeypatch.setattr(validate_module, "_DEFAULT_MAX_UNIQUE_LEDGER_PATHS_V2", 1)
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"b")
    receipt = _receipt(generated_file_hashes={"a.txt": _sha256(b"a"), "b.txt": _sha256(b"b")})
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    ob = _check(report, "observation_budget")
    assert ob.status == STATUS_FAIL_V2


def test_observation_budget_aborts_a_single_huge_ledger_file_mid_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_review.target_pack_validate_v2 as validate_module

    monkeypatch.setattr(validate_module, "_DEFAULT_MAX_TOTAL_LEDGER_BYTES_V2", 4 * 1024 * 1024)
    huge = tmp_path / "huge.bin"
    with huge.open("wb") as f:
        f.seek(64 * 1024 * 1024 - 1)
        f.write(b"\0")
    receipt = _receipt(generated_file_hashes={"huge.bin": "e" * 64})
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    ob = _check(report, "observation_budget")
    assert ob.status == STATUS_FAIL_V2
    assert _check_or_none(report, "generated_file_integrity") is None


def test_budget_exhaustion_preserves_an_already_observed_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PASSO 10's central property: a ledger that already produced a
    genuine counterexample keeps `fail`, even though the aggregate
    budget later ran out evaluating the OTHER ledger."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    monkeypatch.setattr(validate_module, "_DEFAULT_MAX_TOTAL_LEDGER_BYTES_V2", 4 * 1024 * 1024)
    (tmp_path / "a-drift.bin").write_bytes(b"actual content")
    huge = tmp_path / "z-huge.bin"
    with huge.open("wb") as f:
        f.seek(64 * 1024 * 1024 - 1)
        f.write(b"\0")
    receipt = _receipt(
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            "a-drift.bin": "f" * 64,
        },
        target_owned_paths=(".aiops/target-profile.v2.yaml", "a-drift.bin"),
        generated_file_hashes={"z-huge.bin": "e" * 64},
    )
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    toi = _check(report, "target_owned_integrity")
    assert toi.status == STATUS_FAIL_V2
    assert toi.reason_code == TARGET_OWNED_DRIFT_REASON_V2
    assert _check(report, "observation_budget").status == STATUS_FAIL_V2
    assert _check_or_none(report, "generated_file_integrity") is None
    assert report.is_valid is False


def test_incomplete_ledger_without_a_counterexample_never_reports_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uses the BYTE budget, not the claim-count budget: since the P2-B
    fix, a claim-count shortfall is now refused BEFORE `_ledger_check`
    ever runs (see `test_claim_ceiling_admission_precedes_plan_
    materialization`), which would make this test pass for the wrong
    reason regardless of whether the ABSENT-not-PASS fallback logic
    this test actually targets still works. The byte budget still
    exhausts a claim mid-evaluation, genuinely reaching that fallback."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    monkeypatch.setattr(validate_module, "_DEFAULT_MAX_TOTAL_LEDGER_BYTES_V2", 4 * 1024 * 1024)
    huge = tmp_path / "never-reached.bin"
    with huge.open("wb") as f:
        f.seek(64 * 1024 * 1024 - 1)
        f.write(b"\0")
    receipt = _receipt(generated_file_hashes={"never-reached.bin": "d" * 64})
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "observation_budget").status == STATUS_FAIL_V2
    assert _check_or_none(report, "generated_file_integrity") is None


def test_completed_ledger_result_is_retained_when_the_other_ledger_exhausts_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uses the BYTE budget, not the claim-count budget: since the P2-B
    fix, claim-count admission happens on the COMBINED total before any
    ledger is touched, so a claim-count shortfall now refuses BOTH
    ledgers upfront (see `test_claim_ceiling_admission_precedes_plan_
    materialization`) rather than letting one complete first. The byte
    budget is still exhausted DURING evaluation, mid-plan, which is what
    this property is actually about: target_owned's only claim (the
    profile, reused from the seed, zero extra bytes) completes and
    PASSES before generated_file's one huge claim exhausts the shared
    byte budget."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    monkeypatch.setattr(validate_module, "_DEFAULT_MAX_TOTAL_LEDGER_BYTES_V2", 4 * 1024 * 1024)
    huge = tmp_path / "z-huge.bin"
    with huge.open("wb") as f:
        f.seek(64 * 1024 * 1024 - 1)
        f.write(b"\0")
    receipt = _receipt(generated_file_hashes={"z-huge.bin": "d" * 64})
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)
    toi = _check(report, "target_owned_integrity")
    assert toi.status == STATUS_PASS_V2
    assert _check_or_none(report, "generated_file_integrity") is None


# --- Codex Round 1, P2-A: cross-ledger resolved-alias separation ------------
#
# `#203-C1` proves the two ledgers' DECLARED keys are disjoint; it says
# nothing about whether two distinct declared keys resolve to the SAME
# physical file (an in-root symlink). Reproduced against PR #244 exact
# HEAD b76bb806: a target-owned entry and a generated-file entry naming
# the same resolved file, same declared digest, both passed silently.


def test_cross_ledger_resolved_alias_is_rejected(tmp_path: Path) -> None:
    content = b"shared physical content"
    (tmp_path / "generated.txt").write_bytes(content)
    (tmp_path / "target-owned.txt").symlink_to(tmp_path / "generated.txt")
    receipt = _receipt(
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            "target-owned.txt": _sha256(content),
        },
        target_owned_paths=(".aiops/target-profile.v2.yaml", "target-owned.txt"),
        generated_file_hashes={"generated.txt": _sha256(content)},
    )
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    cross_ledger = _check(report, "cross_ledger_alias_separation")
    assert cross_ledger.status == STATUS_FAIL_V2
    assert report.is_valid is False
    # The existing integrity checks are NOT redefined -- the declared
    # bytes genuinely match, so they keep reporting pass; ownership-alias
    # separation is a DIFFERENT, dedicated relation.
    assert _check(report, "target_owned_integrity").status == STATUS_PASS_V2
    assert _check(report, "generated_file_integrity").status == STATUS_PASS_V2


def test_cross_ledger_resolved_alias_is_rejected_reverse_orientation(tmp_path: Path) -> None:
    """Symmetric to the above -- the alias may sit on either ledger's
    side; the relation is about the PAIR of ledgers, not about which one
    happens to hold the symlink."""

    content = b"shared physical content"
    (tmp_path / "target-owned.txt").write_bytes(content)
    (tmp_path / "generated.txt").symlink_to(tmp_path / "target-owned.txt")
    receipt = _receipt(
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            "target-owned.txt": _sha256(content),
        },
        target_owned_paths=(".aiops/target-profile.v2.yaml", "target-owned.txt"),
        generated_file_hashes={"generated.txt": _sha256(content)},
    )
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "cross_ledger_alias_separation").status == STATUS_FAIL_V2
    assert report.is_valid is False


def test_distinct_resolved_files_across_ledgers_remain_valid(tmp_path: Path) -> None:
    """Safe counterexample A: two DIFFERENT resolved files, one per
    ledger, with matching declared hashes -- no conflict."""

    (tmp_path / "target-owned.txt").write_bytes(b"owned content")
    (tmp_path / "generated.txt").write_bytes(b"generated content")
    receipt = _receipt(
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            "target-owned.txt": _sha256(b"owned content"),
        },
        target_owned_paths=(".aiops/target-profile.v2.yaml", "target-owned.txt"),
        generated_file_hashes={"generated.txt": _sha256(b"generated content")},
    )
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "cross_ledger_alias_separation").status == STATUS_PASS_V2
    assert report.is_valid is True


def test_same_ledger_aliases_remain_deduplicated_and_valid(tmp_path: Path) -> None:
    """Safe counterexample B: two aliases in the SAME ledger resolving
    to the same file -- must remain accepted/deduplicated; the new check
    must not regress the alias-dedup property fixing this finding could
    have accidentally broken."""

    content = b"same-ledger shared content"
    (tmp_path / "real.txt").write_bytes(content)
    (tmp_path / "alias1.txt").symlink_to(tmp_path / "real.txt")
    (tmp_path / "alias2.txt").symlink_to(tmp_path / "real.txt")
    receipt = _receipt(
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            "alias1.txt": _sha256(content),
            "alias2.txt": _sha256(content),
        },
        target_owned_paths=(".aiops/target-profile.v2.yaml", "alias1.txt", "alias2.txt"),
    )
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "cross_ledger_alias_separation").status == STATUS_PASS_V2
    assert _check(report, "target_owned_integrity").status == STATUS_PASS_V2
    assert report.is_valid is True


def test_cross_ledger_alias_separation_passes_trivially_for_empty_ledgers(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "cross_ledger_alias_separation").status == STATUS_PASS_V2


def test_cross_ledger_alias_separation_absent_when_claim_budget_exhausted_before_any_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_validate_v2 as validate_module

    monkeypatch.setattr(validate_module, "_DEFAULT_MAX_LEDGER_CLAIMS_V2", 0)
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)
    assert _check_or_none(report, "cross_ledger_alias_separation") is None
    assert _check(report, "observation_budget").status == STATUS_FAIL_V2


# --- Alias dedup + registry reuse --------------------------------------------


def test_many_aliases_to_one_file_reuse_a_single_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_review.target_pack_validate_v2 as validate_module

    big = tmp_path / "big.bin"
    content = b"x" * (1024 * 1024)
    big.write_bytes(content)
    n = 20
    hashes = {".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())}
    paths = [".aiops/target-profile.v2.yaml"]
    for i in range(n):
        (tmp_path / f"alias-{i}.bin").symlink_to(big)
        hashes[f"alias-{i}.bin"] = _sha256(content)
        paths.append(f"alias-{i}.bin")
    receipt = _receipt(target_owned_file_hashes=hashes, target_owned_paths=tuple(paths))
    _install(tmp_path, receipt=receipt)

    calls: list[str] = []
    real_stream = validate_module._observe_streamed_ledger_entry_v2

    def spying_stream(path, budget):
        calls.append(str(path))
        return real_stream(path, budget)

    monkeypatch.setattr(validate_module, "_observe_streamed_ledger_entry_v2", spying_stream)
    report = run_validate_v2(target_root=tmp_path)
    assert report.is_valid is True
    big_reads = [c for c in calls if c == str(big.resolve())]
    assert len(big_reads) == 1, f"expected exactly one real read of the aliased file, got {len(big_reads)}"
    # every claim was still independently evaluated (all pass)
    assert _check(report, "target_owned_integrity").status == STATUS_PASS_V2


def test_profile_declared_in_generated_file_ledger_reuses_seeded_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PASSO 6's correction over #242: validate does not assume the
    profile can only be `TARGET_OWNED`."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    receipt = _receipt(
        target_owned_file_hashes={},
        target_owned_paths=(),
        generated_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())},
    )
    _install(tmp_path, receipt=receipt)

    calls: list[str] = []
    real_stream = validate_module._observe_streamed_ledger_entry_v2

    def spying_stream(path, budget):
        calls.append(str(path))
        return real_stream(path, budget)

    monkeypatch.setattr(validate_module, "_observe_streamed_ledger_entry_v2", spying_stream)
    report = run_validate_v2(target_root=tmp_path)
    assert _check(report, "generated_file_integrity").status == STATUS_PASS_V2
    assert report.is_valid is True
    profile_real = str((tmp_path / ".aiops" / "target-profile.v2.yaml").resolve())
    assert profile_real not in calls, "profile was reread via the ledger path instead of reusing the seeded observation"


def test_receipt_declared_in_target_owned_ledger_reuses_seeded_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_validate_v2 as validate_module

    # A self-referential digest match is circular (the file's content
    # includes its own hash), so this proves REUSE (no second read) via a
    # deliberately WRONG declared hash for the receipt's own ledger
    # entry -- would otherwise attempt a read and observe the receipt's
    # real digest, not "0"*64.
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/install-receipt.v2.json"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/install-receipt.v2.json": "0" * 64,  # deliberately wrong -- proves reuse, not a lucky match
        },
    )
    _install(tmp_path, receipt=receipt)

    calls: list[str] = []
    real_stream = validate_module._observe_streamed_ledger_entry_v2

    def spying_stream(path, budget):
        calls.append(str(path))
        return real_stream(path, budget)

    monkeypatch.setattr(validate_module, "_observe_streamed_ledger_entry_v2", spying_stream)
    report = run_validate_v2(target_root=tmp_path)
    receipt_real = str((tmp_path / ".aiops" / "install-receipt.v2.json").resolve())
    assert receipt_real not in calls, "receipt was reread via the ledger path instead of reusing the seeded observation"
    # The declared hash ("0"*64) does not match the receipt's OWN real
    # hash (reused from the seed) -- so this is legitimately drift.
    toi = _check(report, "target_owned_integrity")
    assert toi.status == STATUS_FAIL_V2
    assert toi.reason_code == TARGET_OWNED_DRIFT_REASON_V2


# --- Real read instrumentation (PASSO 14) ------------------------------------


def test_no_read_handle_is_ever_opened_outside_captured_target_root_real(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Instruments the ACTUAL primitive production uses (`Path.open`),
    not an obsolete `read_bytes`/`read_text` spy -- #242's own `_read_
    spy` went stale after production moved to `Path.open` and this test
    would not have caught a mutant reading via `open()`."""

    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    outside_profile = outside / "outside-profile.yaml"
    outside_profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(outside_profile)
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )

    seen: list[str] = []
    real_open = Path.open

    def spy_open(self: Path, *args: object, **kwargs: object):
        seen.append(str(self.resolve()))
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", spy_open)
    report = run_validate_v2(target_root=target_root)
    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_ESCAPES_TARGET_ROOT_REASON_V2

    root_real = str(target_root.resolve())
    escaping = [p for p in seen if not (p == root_real or p.startswith(root_real + os.sep))]
    assert not escaping, f"a read was opened outside target_root_real: {escaping}"


def test_no_read_bytes_whole_file_fallback_exists_anywhere_in_the_validate_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated_content = b"generated content" * 1000
    receipt = _receipt(generated_file_hashes={"generated.yml": _sha256(generated_content)})
    _install(tmp_path, receipt=receipt)
    (tmp_path / "generated.yml").write_bytes(generated_content)

    seen: list[str] = []
    real_read_bytes = Path.read_bytes

    def spy_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        seen.append(str(self))
        return real_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    report = run_validate_v2(target_root=tmp_path)
    assert report.is_valid is True
    assert seen == [], f"validate called whole-file Path.read_bytes(): {seen}"


# --- C1 integration: shared authority refuses malformed receipts ------------


def test_a_receipt_with_a_malformed_target_repo_is_refused_by_the_shared_loader(tmp_path: Path) -> None:
    """`#203-C1` (merged to master): `TargetInstallReceiptV2.target_repo`
    is `Repository`-typed. This is an INTEGRATION proof that the shared
    loader refuses such a receipt -- validate never re-derives the
    Repository regex itself."""

    _install(tmp_path, receipt=None)
    raw = json.dumps(
        {
            "schema_id": "agent-review.target-install-receipt.v2",
            "schema_version": 2,
            "pack_version": "0.1.0",
            "toolrepo_sha": "1" * 40,
            "manifest_digest": "a" * 64,
            "target_repo": "not-a-repository",
            "portable_target_root_identity": "e" * 64,
            "target_profile_hash": "a" * 64,
            "target_policy_hash": None,
            "review_pack_hashes": {},
            "generated_file_hashes": {},
            "target_owned_file_hashes": {},
            "target_owned_paths": [],
            "required_capabilities": [],
            "expected_runner_labels": [],
            "required_secret_names": [],
            "rollout_mode": "off",
            "compatibility": "compatible",
            "previous_install_identity": None,
            "generated_at": None,
            "receipt_hash": "0" * 64,
        }
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(raw, encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)
    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_INVALID_REASON_V2


def test_a_receipt_with_overlapping_ownership_ledgers_is_refused_by_the_shared_loader(tmp_path: Path) -> None:
    """`#203-C1`: the disjointness invariant lives on `TargetInstallReceiptV2`
    itself -- validate never re-implements the overlap check."""

    _install(tmp_path, receipt=None)
    shared_hash = "c" * 64
    fields = dict(
        schema_id="agent-review.target-install-receipt.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        manifest_digest="a" * 64,
        target_repo="owner/repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="owner/repo"),
        target_profile_hash=_real_profile_hash(),
        target_policy_hash=None,
        review_pack_hashes={},
        generated_file_hashes={"shared.txt": shared_hash},
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            "shared.txt": shared_hash,
        },
        target_owned_paths=(".aiops/target-profile.v2.yaml", "shared.txt"),
        required_capabilities=(),
        expected_runner_labels=(),
        required_secret_names=(),
        rollout_mode="off",
        compatibility="compatible",
        previous_install_identity=None,
        generated_at=None,
    )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
        TargetInstallReceiptV2(**fields, receipt_hash="0" * 64)
    # Confirms the CONTRACT itself refuses construction; validate would
    # therefore only ever observe this as `receipt: invalid` if such
    # bytes were on disk (the receipt could never be legitimately built).


# --- Finite-model decision-signature test (PASSO 13) -------------------------


def _decision_signature(report: ValidateReportV2) -> tuple[tuple[str, str, object], ...]:
    names = (
        "target_root", "aiops_snapshot", "receipt", "profile", "observation_budget",
        "target_owned_integrity", "generated_file_integrity",
    )
    signature = []
    for name in names:
        check = _check_or_none(report, name)
        if check is None:
            signature.append((name, "ABSENT", None))
        else:
            signature.append((name, check.status, check.reason_code))
    return tuple(signature)


def test_finite_model_authorized_equivalence_invariance(tmp_path_factory: pytest.TempPathFactory) -> None:
    """A. For concrete realizations explicitly declared equivalent
    (never-created missing vs written-then-deleted missing), the
    decision signature must be identical -- finite-model discrimination
    over the enumerated corpus below, NOT a universal proof over all
    filesystems."""

    root_never_created = tmp_path_factory.mktemp("never_created")
    (root_never_created / ".aiops").mkdir()
    (root_never_created / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    # receipt deliberately never created

    root_deleted = tmp_path_factory.mktemp("deleted")
    _install(root_deleted, receipt=_receipt())
    (root_deleted / ".aiops" / "install-receipt.v2.json").unlink()

    sig_a = _decision_signature(run_validate_v2(target_root=root_never_created))
    sig_b = _decision_signature(run_validate_v2(target_root=root_deleted))
    assert sig_a == sig_b


def test_finite_model_material_distinction_preservation(tmp_path_factory: pytest.TempPathFactory) -> None:
    """B. Cases that produce DIFFERENT authorized consumer decisions
    (missing vs drifted target-owned entry) must remain distinguishable
    in the decision signature."""

    root_missing = tmp_path_factory.mktemp("missing_case")
    _install(root_missing, receipt=_receipt())
    (root_missing / ".aiops" / "target-profile.v2.yaml").unlink()

    root_drifted = tmp_path_factory.mktemp("drifted_case")
    receipt = _receipt(
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": "f" * 64},
    )
    _install(root_drifted, receipt=receipt)

    sig_missing = _decision_signature(run_validate_v2(target_root=root_missing))
    sig_drifted = _decision_signature(run_validate_v2(target_root=root_drifted))
    assert sig_missing != sig_drifted


def test_finite_model_direct_vs_symlink_alias_same_content_same_signature(tmp_path_factory: pytest.TempPathFactory) -> None:
    """A second authorized-equivalence pair: a direct file and a symlink
    alias to identical content must decide identically -- content
    identity, not path mechanism, drives the decision."""

    root_direct = tmp_path_factory.mktemp("direct")
    content = b"identical content"
    receipt = _receipt(generated_file_hashes={"f.bin": _sha256(content)})
    _install(root_direct, receipt=receipt)
    (root_direct / "f.bin").write_bytes(content)

    root_alias = tmp_path_factory.mktemp("alias")
    _install(root_alias, receipt=receipt)
    real_target = root_alias / "real.bin"
    real_target.write_bytes(content)
    (root_alias / "f.bin").symlink_to(real_target)

    sig_direct = _decision_signature(run_validate_v2(target_root=root_direct))
    sig_alias = _decision_signature(run_validate_v2(target_root=root_alias))
    assert sig_direct == sig_alias
