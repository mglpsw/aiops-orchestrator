"""`#203-S2` PR-B -- tests for the offline, read-only, target-only
`validate` command (`app.agent_review.target_pack_validate_v2`).

Fixture style deliberately mirrors `test_target_pack_doctor_v2.py`'s own
(`_sha256`, `_VALID_PROFILE_YAML`, `_receipt`) rather than importing it --
each `target_pack_*` test module owns its own fixtures, matching
`test_target_pack_operation_v2.py`'s existing precedent, so this file
stays independently collectible.

PR #235 (closed, unmerged) is treated here strictly as a forensic corpus
of properties and reproducers to re-derive against the LIVE authorities on
`master`, never as code to port. Two of its checks are deliberately NOT
reproduced -- see `test_generated_at_difference_does_not_change_
disposition` and `test_upgrade_shaped_lineage_is_not_refused` for the
corrected properties, and the module docstring of `target_pack_
validate_v2` for why.
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
    load_target_profile_v2,
)
from app.agent_review.target_pack_operation_v2 import SEED_PROFILE_IDENTITY_PLACEHOLDER_V2
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
    """`compute_profile_hash_v2` of `_VALID_PROFILE_YAML`, computed rather
    than hardcoded so it can never silently drift from what `profile_
    loader_v2` actually computes -- same discipline as `test_target_pack_
    doctor_v2.py`'s own `_real_profile_hash`."""

    return _profile_hash_of(_VALID_PROFILE_YAML)


def _receipt(**overrides: object) -> TargetInstallReceiptV2:
    """A self-consistent, real-shaped receipt. Defaults DECLARE the
    profile in the target-owned ledger (unlike `test_target_pack_doctor_
    v2.py`'s own `_receipt`, which defaults to an empty ledger) -- most
    tests here want a realistic happy-path receipt; tests that
    specifically exercise an INCOMPLETE ledger override it explicitly."""

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


def _decision_surface(report: ValidateReportV2) -> tuple[object, ...]:
    """The part of a report that should be IDENTICAL between two
    invocations that observed the same install state -- deliberately
    excludes `target_root_real`, which correctly differs whenever the two
    invocations were pointed at different roots (see the `.aiops` retarget
    tests below, where this matters directly)."""

    return (report.checks, report.is_valid, report.unvalidated_capabilities)


def _read_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text

    def spy_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        seen.append(("read_bytes", str(self.resolve())))
        return real_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    def spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
        seen.append(("read_text", str(self.resolve())))
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(Path, "read_text", spy_read_text)
    return seen


def _assert_no_read_escaped(seen: list[tuple[str, str]], target_root: Path) -> None:
    root_real = str(target_root.resolve())
    escaping = [entry for entry in seen if not entry[1].startswith(root_real + os.sep) and entry[1] != root_real]
    assert not escaping, f"a read resolved outside target_root: {escaping}"


def _make_non_directory(tmp_path_factory: pytest.TempPathFactory) -> Path:
    parent = tmp_path_factory.mktemp("parent")
    file_path = parent / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    return file_path


def _make_aiops_escape(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("aiops_escape")
    outside = tmp_path_factory.mktemp("outside")
    (root / ".aiops").symlink_to(outside, target_is_directory=True)
    return root


def _make_missing_receipt(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("missing_receipt")
    (root / ".aiops").mkdir()
    (root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    return root


def _make_healthy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("healthy")
    _install(root, receipt=_receipt())
    return root


def _make_symlink_loop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("symlink_loop")
    (root / ".aiops").mkdir()
    (root / ".aiops" / "target-profile.v2.yaml").symlink_to("target-profile.v2.yaml")
    (root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )
    return root


# --- Happy path / determinism ------------------------------------------------


def test_valid_installation_validates(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True
    for name in ("target_root", "aiops_snapshot", "receipt", "profile", "profile_hash", "profile_identity",
                 "root_identity", "target_owned_integrity"):
        assert _check(report, name).status == STATUS_PASS_V2, name
    assert set(report.unvalidated_capabilities) == {name for name, _ in UNVALIDATED_CAPABILITIES_V2}


def test_validate_is_deterministic_across_repeated_runs(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    first = run_validate_v2(target_root=tmp_path)
    second = run_validate_v2(target_root=tmp_path)

    assert first.checks == second.checks
    assert first.is_valid == second.is_valid
    assert first.unvalidated_capabilities == second.unvalidated_capabilities


# --- Honesty / `unavailable` --------------------------------------------------


def test_unavailable_dimensions_are_never_pass(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)

    for name, reason in UNVALIDATED_CAPABILITIES_V2:
        check = _check(report, name)
        assert check.status == STATUS_UNAVAILABLE_V2
        assert check.status != STATUS_PASS_V2
        assert check.reason_code == reason


def test_unavailable_capability_is_listed_and_does_not_count_as_validated(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True
    assert report.unvalidated_capabilities != ()


@pytest.mark.parametrize(
    "build_target_root",
    [
        pytest.param(_make_non_directory, id="non_directory_root"),
        pytest.param(_make_aiops_escape, id="aiops_escape"),
        pytest.param(_make_missing_receipt, id="missing_receipt"),
        pytest.param(_make_healthy, id="healthy"),
        pytest.param(lambda f: f.mktemp("empty"), id="empty_root"),
    ],
)
def test_every_return_path_emits_the_full_unavailable_block(
    tmp_path_factory: pytest.TempPathFactory, build_target_root
) -> None:
    root = build_target_root(tmp_path_factory)
    report = run_validate_v2(target_root=root)

    names = [c.name for c in report.checks]
    for capability_name, _ in UNVALIDATED_CAPABILITIES_V2:
        assert capability_name in names, f"{capability_name!r} missing on return path for {root}"


@pytest.mark.parametrize(
    "build_target_root",
    [
        pytest.param(_make_non_directory, id="non_directory_root"),
        pytest.param(_make_aiops_escape, id="aiops_escape"),
        pytest.param(_make_missing_receipt, id="missing_receipt"),
        pytest.param(_make_healthy, id="healthy"),
        pytest.param(lambda f: f.mktemp("empty"), id="empty_root"),
    ],
)
def test_emission_order_is_a_subsequence_with_no_duplicate_names(
    tmp_path_factory: pytest.TempPathFactory, build_target_root
) -> None:
    root = build_target_root(tmp_path_factory)
    report = run_validate_v2(target_root=root)

    names = [c.name for c in report.checks]
    assert len(names) == len(set(names)), f"duplicate check name(s): {names}"

    order_index = {name: i for i, name in enumerate(VALIDATE_CHECK_ORDER_V2)}
    indices = [order_index[name] for name in names]
    assert indices == sorted(indices), f"emitted names are not an ordered subsequence: {names}"


def test_every_emitted_reason_code_is_owned_by_validate(tmp_path_factory: pytest.TempPathFactory) -> None:
    roots = [
        _make_non_directory(tmp_path_factory),
        _make_aiops_escape(tmp_path_factory),
        _make_missing_receipt(tmp_path_factory),
        _make_healthy(tmp_path_factory),
        _make_symlink_loop(tmp_path_factory),
        tmp_path_factory.mktemp("empty"),
    ]
    for root in roots:
        report = run_validate_v2(target_root=root)
        for check in report.checks:
            if check.reason_code is not None:
                assert check.reason_code.startswith("target_pack_validate_"), (
                    f"reason code {check.reason_code!r} for {check.name!r} does not belong to validate"
                )


# --- Structural fail-closed ---------------------------------------------------


def test_target_root_not_a_directory_fails_closed(tmp_path_factory: pytest.TempPathFactory) -> None:
    root = _make_non_directory(tmp_path_factory)
    report = run_validate_v2(target_root=root)

    check = _check(report, "target_root")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2
    assert report.is_valid is False


def test_target_root_itself_a_symlink_loop_is_refused(tmp_path_factory: pytest.TempPathFactory) -> None:
    parent = tmp_path_factory.mktemp("parent")
    loop_path = parent / "loop"
    loop_path.symlink_to(loop_path)

    report = run_validate_v2(target_root=loop_path)

    check = _check(report, "target_root")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_ROOT_UNRESOLVABLE_REASON_V2
    assert report.target_root_real is None


def test_missing_receipt_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "receipt").status == STATUS_FAIL_V2
    assert report.is_valid is False


def test_missing_profile_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt(), profile_text=None)
    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile").status == STATUS_FAIL_V2
    assert report.is_valid is False


def test_unparseable_receipt_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text("{not json", encoding="utf-8")
    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_INVALID_REASON_V2


def test_unparseable_profile_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt(), profile_text="not: [a valid: profile")
    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile").status == STATUS_FAIL_V2


# --- Identity / tamper ---------------------------------------------------------


def test_tampered_receipt_fails_closed_at_parse(tmp_path: Path) -> None:
    good = _receipt()
    tampered = TargetInstallReceiptV2.model_construct(**{**good.model_dump(mode="json"), "pack_version": "9.9.9"})
    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(tampered.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_INVALID_REASON_V2
    # No separate `receipt_hash` check exists: the contract's own
    # validator already refuses a tampered self-hash at parse. Publishing
    # an unreachable second check would falsely imply this module
    # verifies something the contract already decided before it ran.
    assert "receipt_hash" not in [c.name for c in report.checks]


def test_profile_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(target_profile_hash="c" * 64)
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "profile_hash")
    assert check.status == STATUS_FAIL_V2
    assert report.is_valid is False


def test_root_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(portable_target_root_identity="f" * 64)
    _install(tmp_path, receipt=receipt)
    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "root_identity")
    assert check.status == STATUS_FAIL_V2
    assert report.is_valid is False


def test_foreign_profile_with_a_self_consistent_receipt_is_refused(tmp_path: Path) -> None:
    """Every self-referential check can be made to pass by a receipt that
    is internally consistent with a profile from a DIFFERENT repository --
    `root_identity` derives its expectation from the receipt's own
    `target_repo`, a closed loop over the receipt alone. Only `profile_
    identity`, which compares the profile (authored independently) against
    the receipt, breaks that loop."""

    foreign_profile = _VALID_PROFILE_YAML.replace("repo: owner/repo", "repo: attacker/other")
    foreign_hash = _profile_hash_of(foreign_profile)
    receipt = _receipt(
        target_profile_hash=foreign_hash,
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(foreign_profile.encode("utf-8"))},
    )
    _install(tmp_path, receipt=receipt, profile_text=foreign_profile)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile_hash").status == STATUS_PASS_V2
    assert _check(report, "root_identity").status == STATUS_PASS_V2
    assert _check(report, "target_owned_integrity").status == STATUS_PASS_V2
    identity_check = _check(report, "profile_identity")
    assert identity_check.status == STATUS_FAIL_V2
    assert identity_check.reason_code == PROFILE_IDENTITY_MISMATCH_REASON_V2
    assert report.is_valid is False


def test_uncustomized_seed_identity_placeholder_is_not_a_mismatch(tmp_path: Path) -> None:
    seed_profile = _VALID_PROFILE_YAML.replace("repo: owner/repo", f"repo: {SEED_PROFILE_IDENTITY_PLACEHOLDER_V2}")
    seed_hash = _profile_hash_of(seed_profile)
    receipt = _receipt(
        target_profile_hash=seed_hash,
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(seed_profile.encode("utf-8"))},
    )
    _install(tmp_path, receipt=receipt, profile_text=seed_profile)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile_identity").status == STATUS_PASS_V2


def test_seed_placeholder_has_exactly_one_definition() -> None:
    template_path = (
        Path(__file__).resolve().parents[2] / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml"
    )
    template_text = template_path.read_text(encoding="utf-8")
    assert f"repo: {SEED_PROFILE_IDENTITY_PLACEHOLDER_V2}" in template_text


# --- generated_at: NOT identity (correction 1) --------------------------------


def test_generated_at_difference_does_not_change_disposition(tmp_path_factory: pytest.TempPathFactory) -> None:
    """T1. Two receipts, identical identity fields, DIFFERENT valid
    `generated_at`, same canonical receipt hash (the field is excluded
    from the preimage) -- `validate` reaches the same disposition for
    both. The safe counterexample against #235's own `generated_at`
    check, which this command's design deliberately does not port."""

    root_a = tmp_path_factory.mktemp("a")
    root_b = tmp_path_factory.mktemp("b")
    receipt_a = _receipt(generated_at="2024-01-01T00:00:00Z")
    receipt_b = _receipt(generated_at="2025-06-15T12:30:45Z")

    assert receipt_a.receipt_hash == receipt_b.receipt_hash
    assert receipt_a.generated_at != receipt_b.generated_at

    _install(root_a, receipt=receipt_a)
    _install(root_b, receipt=receipt_b)

    report_a = run_validate_v2(target_root=root_a)
    report_b = run_validate_v2(target_root=root_b)

    assert _decision_surface(report_a) == _decision_surface(report_b)
    assert report_a.is_valid is True


def test_malformed_generated_at_is_refused_by_the_shared_authority(tmp_path: Path) -> None:
    """T2. `generated_at` outside `Rfc3339Timestamp` is refused by the
    RECEIPT contract's own validator, not by anything this module adds."""

    good = _receipt()
    tampered_dump = good.model_dump(mode="json")
    # Regex-shaped (matches Rfc3339Timestamp's pattern) but calendar-
    # invalid -- `_validate_timestamp`'s `datetime.strptime` call refuses
    # it. `receipt_hash` stays untouched and self-consistent, since
    # `generated_at` is excluded from its preimage -- the ONLY thing that
    # can refuse this receipt is the timestamp's own validator.
    tampered_dump["generated_at"] = "2020-13-45T99:99:99Z"
    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(json.dumps(tampered_dump), encoding="utf-8")

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_INVALID_REASON_V2


def test_a_tampered_generated_at_is_deliberately_not_a_validate_failure(tmp_path: Path) -> None:
    """Inverted from PR #235's own (incorrect) test: a well-formed but
    DIFFERENT `generated_at` from what a real writer would have emitted is
    not identity, not drift, and this command must not refuse it."""

    receipt = _receipt(generated_at="2099-12-31T23:59:59Z")
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True


# --- previous_install_identity: PRIOR state, not current (correction 2) ------


def test_upgrade_shaped_lineage_is_not_refused(tmp_path: Path) -> None:
    """T3. `previous_install_identity` describes the PRIOR receipt, not a
    copy of the current one -- `pack_version`/`toolrepo_sha` legitimately
    DIFFERING from the current receipt's own is exactly what a real
    `upgrade` would produce. No real `upgrade` is implemented here; the
    only property proven is that `validate` never refuses SOLELY because
    previous != current."""

    previous = ReceiptIdentityRefV2(receipt_hash="c" * 64, pack_version="1.0", toolrepo_sha="a" * 40)
    receipt = _receipt(pack_version="2.0", toolrepo_sha="b" * 40, previous_install_identity=previous)
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True
    assert "previous_install_lineage" in report.unvalidated_capabilities


def test_previous_install_lineage_never_claims_verified(tmp_path: Path) -> None:
    """T4. With `previous_install_identity` present and no independent
    history store, lineage is never reported verified."""

    previous = ReceiptIdentityRefV2(receipt_hash="c" * 64, pack_version="1.0", toolrepo_sha="a" * 40)
    receipt = _receipt(previous_install_identity=previous)
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    lineage_check = _check(report, "previous_install_lineage")
    assert lineage_check.status == STATUS_UNAVAILABLE_V2
    assert lineage_check.reason_code == PREVIOUS_INSTALL_LINEAGE_REASON_V2


# --- Drift detection -----------------------------------------------------------


def test_comment_only_profile_edit_is_caught_as_byte_drift(tmp_path: Path) -> None:
    """The sharpest test in the file. Appending a comment preserves the
    profile's SEMANTIC hash (comments don't survive YAML parsing) but
    changes its BYTES -- proves byte-level hashing is not redundant with
    semantic hashing; it is the only thing that catches cosmetic
    tampering after install."""

    _install(tmp_path, receipt=_receipt())
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(
        _VALID_PROFILE_YAML + "\n# comment-only edit\n", encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile_hash").status == STATUS_PASS_V2
    target_owned = _check(report, "target_owned_integrity")
    assert target_owned.status == STATUS_FAIL_V2
    assert target_owned.reason_code == TARGET_OWNED_DRIFT_REASON_V2
    assert report.is_valid is False


def test_target_owned_file_deleted_fails_closed(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    (tmp_path / ".aiops" / "target-profile.v2.yaml").unlink()

    report = run_validate_v2(target_root=tmp_path)

    target_owned = _check(report, "target_owned_integrity")
    assert target_owned.status == STATUS_FAIL_V2
    assert target_owned.reason_code == TARGET_OWNED_MISSING_REASON_V2


def test_target_owned_not_a_regular_file_is_reported_distinctly(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "not-a-file").mkdir()
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/not-a-file"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/not-a-file": "a" * 64,
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2


def test_target_owned_unreadable_is_reported_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fourth state of the total taxonomy: a regular, existing, contained
    file whose read raises `OSError` is neither `missing` nor `not_a_
    regular_file` nor silently treated as drift -- it gets its own reason
    code. Simulated via monkeypatch (portable across CI/root/non-root)
    rather than relying on real filesystem permission semantics, and
    scoped to exactly the ONE extra path so the receipt/profile artifact
    reads (unaffected) still succeed."""

    _install(tmp_path, receipt=None)
    extra_path = tmp_path / ".aiops" / "extra-owned.txt"
    extra_path.write_bytes(b"extra content")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/extra-owned.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/extra-owned.txt": _sha256(b"extra content"),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    extra_path_real = extra_path.resolve()
    real_open = Path.open

    def selectively_raising_open(self: Path, *args: object, **kwargs: object):
        mode = args[0] if args else kwargs.get("mode")
        if self.resolve() == extra_path_real and mode == "rb":
            raise OSError("simulated permission denied")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", selectively_raising_open)
    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_UNREADABLE_REASON_V2


def test_receipt_declaring_an_extra_intact_target_owned_path_still_validates(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    extra_path = tmp_path / ".aiops" / "extra-owned.txt"
    extra_path.write_bytes(b"extra content")
    receipt = _receipt(
        target_owned_paths=(".aiops/extra-owned.txt", ".aiops/target-profile.v2.yaml"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/extra-owned.txt": _sha256(b"extra content"),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True
    assert "target_owned_set" in report.unvalidated_capabilities


def test_receipt_declaring_an_extra_drifted_target_owned_path_is_refused(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    extra_path = tmp_path / ".aiops" / "extra-owned.txt"
    extra_path.write_bytes(b"DIFFERENT actual content")
    receipt = _receipt(
        target_owned_paths=(".aiops/extra-owned.txt", ".aiops/target-profile.v2.yaml"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/extra-owned.txt": _sha256(b"the declared content"),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is False
    assert _check(report, "target_owned_integrity").reason_code == TARGET_OWNED_DRIFT_REASON_V2


# --- Safe counterexamples (kills the #235 patterns permanently) --------------


def test_target_owned_paths_in_non_sorted_order_is_accepted(tmp_path: Path) -> None:
    """Safe Counterexample A. The contract enforces SET equality between
    `target_owned_paths` and `target_owned_file_hashes` only -- #235
    additionally required canonical (sorted) ORDER, strengthening an
    authority it only consumes. This receipt's `target_owned_paths` is
    deliberately in the REVERSE of what `sorted()` would produce."""

    _install(tmp_path, receipt=None)
    extra_path = tmp_path / ".aiops" / "extra-owned.txt"
    extra_path.write_bytes(b"extra content")
    unsorted_order = (".aiops/target-profile.v2.yaml", ".aiops/extra-owned.txt")
    assert unsorted_order != tuple(sorted(unsorted_order))
    receipt = _receipt(
        target_owned_paths=unsorted_order,
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/extra-owned.txt": _sha256(b"extra content"),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True


def test_receipt_omitting_the_profile_from_the_ledger_still_validates(tmp_path: Path) -> None:
    """Safe Counterexample B, the core proof of correction 4. Without the
    upstream manifest, `validate` cannot establish that the ledger
    declared every entry it should have. A self-consistent receipt that
    OMITS the profile from `target_owned_paths`/`target_owned_file_
    hashes` -- profile present on disk, `target_profile_hash` correct,
    `receipt_hash` recomputed -- must not be refused by a fabricated
    completeness rule. `target_owned_set` stays `unavailable`; that
    dimension is `doctor`'s charter against the real manifest."""

    receipt = _receipt(target_owned_paths=(), target_owned_file_hashes={})
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile").status == STATUS_PASS_V2
    assert _check(report, "profile_hash").status == STATUS_PASS_V2
    assert _check(report, "profile_identity").status == STATUS_PASS_V2
    assert _check(report, "target_owned_integrity").status == STATUS_PASS_V2
    assert "target_owned_set" in report.unvalidated_capabilities
    assert report.is_valid is True


def test_receipt_omitting_the_profile_but_declaring_something_else_still_validates(tmp_path: Path) -> None:
    """A stronger form of Safe Counterexample B: the ledger is non-empty
    (so the loop actually runs) but still never mentions the profile."""

    _install(tmp_path, receipt=None)
    extra_path = tmp_path / ".aiops" / "extra-owned.txt"
    extra_path.write_bytes(b"extra content")
    receipt = _receipt(
        target_owned_paths=(".aiops/extra-owned.txt",),
        target_owned_file_hashes={".aiops/extra-owned.txt": _sha256(b"extra content")},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True
    assert "target_owned_set" in report.unvalidated_capabilities


# --- Containment / symlink (adversarial) --------------------------------------


def test_profile_symlink_escape_is_refused_and_the_outside_file_is_never_read(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    outside_profile = outside / "outside-profile.yaml"
    outside_profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(outside_profile)
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )

    seen = _read_spy(monkeypatch)
    report = run_validate_v2(target_root=target_root)

    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert report.is_valid is False
    _assert_no_read_escaped(seen, target_root)


def test_declared_target_owned_path_escaping_via_symlink_is_refused(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
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
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/owned.txt": _sha256(b"outside content"),
        },
    )
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    seen = _read_spy(monkeypatch)
    report = run_validate_v2(target_root=target_root)

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_ESCAPES_TARGET_ROOT_REASON_V2
    _assert_no_read_escaped(seen, target_root)


def test_symlink_loop_is_distinguished_from_containment_escape(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").symlink_to("target-profile.v2.yaml")
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_RESOLUTION_FAILED_REASON_V2
    assert check.reason_code != PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_target_root_removed_between_check_and_resolution_never_reports_pass(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_root = tmp_path_factory.mktemp("target")
    (target_root / ".aiops").mkdir()
    (target_root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")

    real_resolve = Path.resolve
    removed = {"done": False}

    def racing_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        result = real_resolve(self, *args, **kwargs)  # type: ignore[arg-type]
        if not removed["done"] and self == target_root:
            removed["done"] = True
            shutil.rmtree(target_root)
        return result

    monkeypatch.setattr(Path, "resolve", racing_resolve)
    report = run_validate_v2(target_root=target_root)

    check = _check(report, "target_root")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2


def test_validate_does_not_create_a_missing_target_root(tmp_path: Path) -> None:
    missing = tmp_path / "never-created"
    run_validate_v2(target_root=missing)
    assert not missing.exists()


# --- Read-only -----------------------------------------------------------------


def _snapshot_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            result[rel] = f"symlink:{os.readlink(path)}"
        elif path.is_dir():
            result[rel] = "dir"
        else:
            result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def test_validate_never_mutates_the_target(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())
    before = _snapshot_tree(tmp_path)

    run_validate_v2(target_root=tmp_path)
    run_validate_v2(target_root=tmp_path)

    after = _snapshot_tree(tmp_path)
    assert before == after


# --- TOCTOU / snapshot atomicity ------------------------------------------------


def test_aiops_retarget_between_the_two_reads_is_invisible(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ONE SNAPSHOT PER DECISION, proven directly. `dir_a` and `dir_b`
    each hold an INDIVIDUALLY VALID install whose profiles differ in
    bytes and semantic hash. `root/.aiops` starts symlinked to `dir_a`;
    the very first `resolve_within_target_root_v2` call -- which is
    `.aiops` itself being resolved into the one snapshot every artifact
    path derives from -- retargets the symlink to `dir_b` as a side
    effect, immediately AFTER that one resolution returns but BEFORE the
    receipt/profile paths are derived from it. A naive implementation
    that re-resolves `.aiops` independently per artifact would pair a
    receipt read from `dir_a` with a profile read from `dir_b`; this
    implementation cannot, because both derive from the SAME already-
    captured `Path` value, which does not change when the live symlink
    does."""

    root = tmp_path_factory.mktemp("root")
    dir_a = root / "dir_a"
    dir_b = root / "dir_b"
    (dir_a / ".aiops").mkdir(parents=True)
    (dir_b / ".aiops").mkdir(parents=True)

    profile_a = _VALID_PROFILE_YAML
    profile_b = _VALID_PROFILE_YAML.replace("repo: owner/repo", "repo: owner/other-repo")
    assert _sha256(profile_a.encode()) != _sha256(profile_b.encode())
    assert _profile_hash_of(profile_a) != _profile_hash_of(profile_b)

    receipt_a = _receipt()
    receipt_b = _receipt(
        target_repo="owner/other-repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="owner/other-repo"),
        target_profile_hash=_profile_hash_of(profile_b),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(profile_b.encode("utf-8"))},
    )
    _install(dir_a, receipt=receipt_a, profile_text=profile_a)
    _install(dir_b, receipt=receipt_b, profile_text=profile_b)

    (root / ".aiops").symlink_to(dir_a / ".aiops", target_is_directory=True)

    import app.agent_review.target_pack_validate_v2 as validate_module

    real_resolve_within = validate_module.resolve_within_target_root_v2
    call_count = {"n": 0}

    def racing_resolve_within(target_root_real: Path, path: Path) -> Path:
        result = real_resolve_within(target_root_real, path)
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Exactly the `.aiops` snapshot call. Retarget the symlink now
            # -- as late as possible while still being BEFORE the receipt/
            # profile artifact paths are derived from `aiops_dir`.
            (root / ".aiops").unlink()
            (root / ".aiops").symlink_to(dir_b / ".aiops", target_is_directory=True)
        return result

    monkeypatch.setattr(validate_module, "resolve_within_target_root_v2", racing_resolve_within)
    attacked = run_validate_v2(target_root=root)
    monkeypatch.undo()

    control_a = run_validate_v2(target_root=dir_a)

    assert attacked.target_root_real == str(root.resolve())
    assert control_a.target_root_real == str(dir_a.resolve())
    # Full report equality is NOT asserted: `target_root_real` correctly
    # differs between `root` and `dir_a`. Only the decision surface --
    # what was actually validated -- must agree.
    assert _decision_surface(attacked) == _decision_surface(control_a)
    assert attacked.is_valid is True


def test_aiops_retarget_before_run_matches_the_new_target(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Symmetric case: a retarget completed BEFORE `run_validate_v2` is
    even called is not an attack on the snapshot -- it simply means a
    different install is the one being validated, wholly."""

    root = tmp_path_factory.mktemp("root")
    dir_a = root / "dir_a"
    dir_b = root / "dir_b"
    (dir_a / ".aiops").mkdir(parents=True)
    (dir_b / ".aiops").mkdir(parents=True)

    profile_a = _VALID_PROFILE_YAML
    profile_b = _VALID_PROFILE_YAML.replace("repo: owner/repo", "repo: owner/other-repo")
    receipt_a = _receipt()
    receipt_b = _receipt(
        target_repo="owner/other-repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="owner/other-repo"),
        target_profile_hash=_profile_hash_of(profile_b),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(profile_b.encode("utf-8"))},
    )
    _install(dir_a, receipt=receipt_a, profile_text=profile_a)
    _install(dir_b, receipt=receipt_b, profile_text=profile_b)

    (root / ".aiops").symlink_to(dir_a / ".aiops", target_is_directory=True)
    (root / ".aiops").unlink()
    (root / ".aiops").symlink_to(dir_b / ".aiops", target_is_directory=True)

    result = run_validate_v2(target_root=root)
    control_b = run_validate_v2(target_root=dir_b)

    assert _decision_surface(result) == _decision_surface(control_b)
    assert result.is_valid is True


def test_profile_bytes_are_read_once_and_reused_for_the_ledger_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The profile artifact's bytes, captured once during the `.aiops`
    snapshot, are REUSED for `target_owned_integrity`'s byte-hash check
    rather than read a second time. Without reuse, a receipt could carry
    the SEMANTIC hash of one profile content and the BYTE hash of a
    DIFFERENT one, each individually satisfied by two reads timed to
    observe different bytes; proven here by counting reads of the
    resolved profile path rather than by trying to literally win a race."""

    _install(tmp_path, receipt=_receipt())
    profile_path_real = (tmp_path / ".aiops" / "target-profile.v2.yaml").resolve()

    # Round 2 (Codex P2-2): the profile artifact is now observed through
    # a bounded `.open("rb")` + `stream.read(limit)` read, not whole-file
    # `read_bytes()` -- count opens of the resolved profile path instead.
    open_counts: dict[str, int] = {}
    real_open = Path.open

    def counting_open(self: Path, *args: object, **kwargs: object):
        if self.resolve() == profile_path_real:
            open_counts[str(self)] = open_counts.get(str(self), 0) + 1
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", counting_open)
    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True
    total_opens = sum(open_counts.values())
    assert total_opens == 1, f"profile path opened {total_opens} times, expected exactly 1"


# --- Parser authority (strictness / ambiguity) ---------------------------------


def test_receipt_with_a_prepended_duplicate_json_key_is_refused(tmp_path: Path) -> None:
    good = _receipt()
    original_json = json.dumps(good.model_dump(mode="json"))
    # Prepend a duplicate `pack_version` key; JSON last-wins parsing keeps
    # the ORIGINAL (valid) value, so `receipt_hash` -- computed from the
    # PARSED model -- stays self-consistent. Only `strict_json_loads`'s
    # duplicate-key gate refuses this.
    tampered_json = '{"pack_version": "9.9.9", ' + original_json[1:]

    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(tampered_json, encoding="utf-8")

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_INVALID_REASON_V2


def test_profile_with_duplicate_yaml_keys_is_refused(tmp_path: Path) -> None:
    tampered_profile = _VALID_PROFILE_YAML.replace("  repo: owner/repo", "  repo: attacker/other\n  repo: owner/repo")
    receipt = _receipt(
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(tampered_profile.encode("utf-8"))},
    )
    _install(tmp_path, receipt=receipt, profile_text=tampered_profile)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile").status == STATUS_FAIL_V2


def test_profile_with_unhashable_yaml_key_is_reason_coded_not_a_traceback(tmp_path: Path) -> None:
    tampered_profile = "? [a, b]\n: value\n"
    _install(tmp_path, receipt=_receipt(), profile_text=tampered_profile)

    report = run_validate_v2(target_root=tmp_path)  # must not raise

    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code is not None


def test_profile_duplicate_key_rejection_survives_the_merge_key_fix(tmp_path: Path) -> None:
    tampered_profile = "a: 1\na: 2\n"
    _install(tmp_path, receipt=_receipt(), profile_text=tampered_profile)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile").status == STATUS_FAIL_V2


# --- Total parse-boundary families --------------------------------------------

_MALFORMED_RECEIPT_BYTES_CORPUS: tuple[bytes, ...] = (
    b"",
    b"not json at all",
    b'{"a": ',
    b"\xff\xfe\x00\x01",
    b"42",
    b"[1, 2, 3]",
    b"{}",
    ("[" * 2000).encode("utf-8"),
)


@pytest.mark.parametrize("raw_bytes", _MALFORMED_RECEIPT_BYTES_CORPUS)
def test_family_malformed_receipt_never_produces_a_traceback(tmp_path: Path, raw_bytes: bytes) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_bytes(raw_bytes)

    report = run_validate_v2(target_root=tmp_path)  # must not raise

    assert report.is_valid is False
    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code is not None
    assert check.reason_code.startswith("target_pack_validate_")


_MALFORMED_PROFILE_TEXT_CORPUS: tuple[str, ...] = (
    "",
    "not: valid: yaml: at: all: :::",
    "? [a, b]\n: value\n",
    "a: 1\na: 2\n",
    "[1, 2, 3]",
    "42",
    "not_a_mapping",
)


@pytest.mark.parametrize("raw_text", _MALFORMED_PROFILE_TEXT_CORPUS)
def test_family_malformed_profile_never_produces_a_traceback(tmp_path: Path, raw_text: str) -> None:
    _install(tmp_path, receipt=_receipt())
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(raw_text, encoding="utf-8")

    report = run_validate_v2(target_root=tmp_path)  # must not raise

    assert report.is_valid is False
    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code is not None
    assert check.reason_code.startswith("target_pack_validate_")


# --- Compatibility: correction 3 -- no independent authority ------------------


def test_major_incompatible_receipt_does_not_change_disposition(tmp_path: Path) -> None:
    """`compatibility` admits both values as structurally valid
    declarations; converting either into a semantic verdict is an
    inference `validate` holds no authority for (the same class as the
    rollout ceiling). Covered directly by the blindness family below;
    this test pins the specific value the PR-B contract names."""

    receipt = _receipt(compatibility="major_incompatible")
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True


# --- Completeness: every receipt field is explicitly classified --------------

_RECEIPT_MODEL_FIELDS_V2 = frozenset(TargetInstallReceiptV2.model_fields)

_BLIND_TO_UPSTREAM_AUTHORITY_FIELDS_V2 = frozenset(
    {
        "pack_version",
        "toolrepo_sha",
        "manifest_digest",
        "required_capabilities",
        "rollout_mode",
        "target_policy_hash",
        "review_pack_hashes",
        "expected_runner_labels",
        "required_secret_names",
        "generated_at",
        "previous_install_identity",
        "compatibility",
    }
)

_REACTS_TO_LOCAL_EVIDENCE_FIELDS_V2 = frozenset(
    {
        "target_repo",
        "portable_target_root_identity",
        "target_profile_hash",
        "target_owned_file_hashes",
        "target_owned_paths",
        # Codex Round 2 P2-1: `generated_file_hashes` DECLARED-ENTRY
        # integrity is now locally verified (`generated_file_integrity`),
        # symmetric with `target_owned_file_hashes` -- only SET
        # completeness/ownership classification remain upstream-blind
        # (`generated_file_set: unavailable`).
        "generated_file_hashes",
    }
)

# Fields excluded from the blind/reacts classification, each with the
# reason it does not fit either bucket -- so a NEW field on the contract
# is forced into an explicit decision via `test_every_receipt_field_is_
# explicitly_classified`, rather than silently defaulting to unclassified.
_EXCLUDED_FROM_CLASSIFICATION_V2: dict[str, str] = {
    "schema_id": "single-value Literal; no legal alternative value exists to mutate to",
    "schema_version": "single-value Literal; no legal alternative value exists to mutate to",
    "receipt_hash": "the self-hash; tampering it is covered by test_tampered_receipt_fails_closed_at_parse, "
    "not a per-field blind/reacts classification",
}


def test_every_receipt_field_is_explicitly_classified() -> None:
    classified = (
        _BLIND_TO_UPSTREAM_AUTHORITY_FIELDS_V2
        | _REACTS_TO_LOCAL_EVIDENCE_FIELDS_V2
        | set(_EXCLUDED_FROM_CLASSIFICATION_V2)
    )
    assert classified == _RECEIPT_MODEL_FIELDS_V2, (
        f"unclassified: {_RECEIPT_MODEL_FIELDS_V2 - classified}; "
        f"stale (no longer on the contract): {classified - _RECEIPT_MODEL_FIELDS_V2}"
    )
    assert not (_BLIND_TO_UPSTREAM_AUTHORITY_FIELDS_V2 & _REACTS_TO_LOCAL_EVIDENCE_FIELDS_V2)


def _blind_mutation_value_v2(field: str) -> object:
    return {
        "pack_version": "9.9.9",
        "toolrepo_sha": "f" * 40,
        "manifest_digest": "b" * 64,
        "required_capabilities": ("router_transport",),
        "rollout_mode": "shadow_full",
        "target_policy_hash": "c" * 64,
        "review_pack_hashes": {"some-pack": "d" * 64},
        "expected_runner_labels": ("self-hosted",),
        "required_secret_names": ("SOME_TOKEN",),
        "generated_at": "2030-01-01T00:00:00Z",
        "previous_install_identity": ReceiptIdentityRefV2(
            receipt_hash="c" * 64, pack_version="1.0", toolrepo_sha="a" * 40
        ),
        "compatibility": "major_incompatible",
    }[field]


@pytest.mark.parametrize("field", sorted(_BLIND_TO_UPSTREAM_AUTHORITY_FIELDS_V2))
def test_validate_is_blind_to_receipt_fields_requiring_upstream_authority(tmp_path: Path, field: str) -> None:
    receipt = _receipt(**{field: _blind_mutation_value_v2(field)})
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True, f"mutating {field!r} alone made validate refuse a structurally valid receipt"


def _reacts_mutation_value_v2(field: str) -> object:
    return {
        "target_repo": "someone/else",
        "portable_target_root_identity": "f" * 64,
        "target_profile_hash": "a" * 64,
        "target_owned_file_hashes": {".aiops/target-profile.v2.yaml": "b" * 64},
        # Declares a generated file that is never written to disk --
        # generated_file_integrity must react with "missing".
        "generated_file_hashes": {"never-written-generated.yml": "c" * 64},
    }[field]


@pytest.mark.parametrize("field", sorted(_REACTS_TO_LOCAL_EVIDENCE_FIELDS_V2))
def test_validate_reacts_to_receipt_fields_with_local_independent_evidence(tmp_path: Path, field: str) -> None:
    if field == "target_owned_paths":
        pytest.skip(
            "cannot be mutated independently of target_owned_file_hashes -- the contract's own set-"
            "equality validator refuses any receipt where they disagree; reactivity for this pair is "
            "proven via the target_owned_file_hashes case"
        )
    receipt = _receipt(**{field: _reacts_mutation_value_v2(field)})
    _install(tmp_path, receipt=receipt)

    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is False, f"mutating {field!r} did not make validate refuse a locally-checkable claim"


# --- Single-.aiops-parent guard (validates the snapshot design itself) -------


def test_receipt_and_profile_share_one_aiops_parent() -> None:
    assert Path(RECEIPT_RELATIVE_PATH_V2).parent == DEFAULT_TARGET_PROFILE_RELATIVE_PATH.parent


# --- Pre-push qualification: four evidence gaps closed --------------------
#
# All four production behaviors below were independently reproduced as
# correct during pre-push adversarial review before these tests existed.
# These tests add committed regression evidence only -- no production
# behavior changes in this corrective pass.


def test_profile_invalid_utf8_bytes_is_reported_unreadable_not_a_traceback(tmp_path: Path) -> None:
    """Gap 1. `_load_profile_v2`'s `UnicodeDecodeError` branch has no
    other test: the malformed-profile corpus is all `str`, which cannot
    hold invalid UTF-8 -- only writing raw bytes directly exercises this
    branch."""

    _install(tmp_path, receipt=_receipt())
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_bytes(b"\xff\xfe\x00\x01not valid utf-8 at all")
    before = _snapshot_tree(tmp_path)

    report = run_validate_v2(target_root=tmp_path)  # must not raise

    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PROFILE_UNREADABLE_REASON_V2
    assert report.is_valid is False
    for name, reason in UNVALIDATED_CAPABILITIES_V2:
        capability_check = _check(report, name)
        assert capability_check.status == STATUS_UNAVAILABLE_V2
        assert capability_check.reason_code == reason

    after = _snapshot_tree(tmp_path)
    assert before == after


@pytest.mark.parametrize(
    "artifact_filename,check_name,missing_reason",
    [
        pytest.param("install-receipt.v2.json", "receipt", RECEIPT_MISSING_REASON_V2, id="receipt"),
        pytest.param("target-profile.v2.yaml", "profile", PROFILE_MISSING_REASON_V2, id="profile"),
    ],
)
def test_artifact_directory_reports_the_existing_missing_disposition(
    tmp_path: Path, artifact_filename: str, check_name: str, missing_reason: str
) -> None:
    """Gap 2. A directory at the expected receipt/profile path is NOT a
    separate `not_a_regular_file` artifact state -- unlike target-owned
    ledger entries (`target_owned_integrity`, which DOES split that
    state out), the `.aiops` artifact checks (`receipt`/`profile`)
    deliberately collapse "does not exist" and "exists but is not a
    regular file" into the SAME `_missing` disposition
    (`_observe_artifact_bytes_v2`'s `"missing"`/`"not_a_regular_file"`
    statuses both map to one reason code in `_load_receipt_v2`/`_load_
    profile_v2`). This pins that CURRENT, intentional collapse -- it
    does not argue for a finer split. Parametrized rather than two
    separate tests: receipt and profile genuinely share this one
    observable contract."""

    _install(tmp_path, receipt=_receipt())
    target_path = tmp_path / ".aiops" / artifact_filename
    target_path.unlink()
    target_path.mkdir()

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, check_name)
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == missing_reason


def test_non_profile_ledger_entry_missing_takes_the_general_observation_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap 3. Proves BRANCH IDENTITY, not merely the same final reason
    code reachable through profile reuse: `.aiops/extra-owned.txt` is
    NOT the profile's own canonical path, so `_target_owned_integrity_
    check_v2` must take the general per-entry `_observe_ledger_entry_
    hash_v2` branch -- the captured-profile-bytes reuse branch never
    calls that function at all for the profile's own entry (see
    `test_target_owned_file_deleted_fails_closed`, which exercises
    reuse, not this)."""

    _install(tmp_path, receipt=None)
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/extra-owned.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/extra-owned.txt": "b" * 64,
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    # `.aiops/extra-owned.txt` is deliberately never written.

    import app.agent_review.target_pack_validate_v2 as validate_module

    real_observe = validate_module._observe_ledger_entry_hash_v2
    observed_paths: list[Path] = []

    def spying_observe(path: Path):
        observed_paths.append(path)
        return real_observe(path)

    monkeypatch.setattr(validate_module, "_observe_ledger_entry_hash_v2", spying_observe)
    report = run_validate_v2(target_root=tmp_path)

    assert any(p.name == "extra-owned.txt" for p in observed_paths), (
        "the general ledger-observation branch was never invoked for the non-profile entry"
    )

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_MISSING_REASON_V2


def test_non_profile_ledger_entry_symlink_loop_is_resolution_failed_not_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap 4. `.aiops/loopy.txt` is NOT the profile's own path, so this
    exercises `_target_owned_integrity_check_v2`'s own `except PlanError`
    branch (line-local to that function) rather than the profile
    artifact's containment check. Pins the resolution-failure-vs-
    containment-escape distinction specifically on THIS branch, not just
    on the already-covered profile/`.aiops` paths (`test_symlink_loop_
    is_distinguished_from_containment_escape`)."""

    _install(tmp_path, receipt=None)
    (tmp_path / ".aiops" / "loopy.txt").symlink_to("loopy.txt")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/loopy.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/loopy.txt": "c" * 64,
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    import app.agent_review.target_pack_validate_v2 as validate_module

    real_resolve = validate_module.resolve_within_target_root_v2
    resolved_calls: list[Path] = []

    def counting_resolve(target_root_real: Path, path: Path) -> Path:
        resolved_calls.append(path)
        return real_resolve(target_root_real, path)

    monkeypatch.setattr(validate_module, "resolve_within_target_root_v2", counting_resolve)
    report = run_validate_v2(target_root=tmp_path)

    assert any(p.name == "loopy.txt" for p in resolved_calls), (
        "the ledger branch's own resolve_within_target_root_v2 call was never made for the non-profile entry"
    )

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_RESOLUTION_FAILED_REASON_V2
    assert check.reason_code != PATH_ESCAPES_TARGET_ROOT_REASON_V2


# --- Codex round 1: two P2 findings closed ----------------------------------
#
# Both production behaviors below were independently reproduced as
# defects (not merely Codex's claim taken on trust) before any production
# code changed. See the corresponding PR thread replies for the exact
# reproduction transcripts.


def test_target_owned_disposition_is_independent_of_json_member_order(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Codex P2 finding 1. `receipt_hash`'s canonical preimage sorts JSON
    object keys, so raw member order of `target_owned_file_hashes` is NOT
    part of the receipt's identity: two receipt JSON documents with
    identical semantic ledger claims and the SAME valid `receipt_hash`
    must produce the SAME validate report regardless of which order
    their JSON members were written in (`app/agent_review/AGENTS.md`'s
    determinism section: every emitted artifact byte must be independent
    of arrival order). Uses THREE non-profile entries, two of which fail
    for DIFFERENT reasons, so the property is genuinely about ordering,
    not a two-element coincidence."""

    root_a = tmp_path_factory.mktemp("order_a")
    root_b = tmp_path_factory.mktemp("order_b")

    ledger = {
        ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
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
    assert order_a != order_b

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
        # a-missing.txt deliberately never written.

    report_a = run_validate_v2(target_root=root_a)
    report_b = run_validate_v2(target_root=root_b)

    assert _decision_surface(report_a) == _decision_surface(report_b)


def test_profile_ledger_entry_directory_is_not_a_regular_file(tmp_path: Path) -> None:
    """Codex P2 finding 2, scenario A. Before the fix, `profile_bytes is
    None` alone could not distinguish this from `missing`; the already-
    captured profile artifact observation is now threaded through
    instead of re-derived from that lossy projection."""

    _install(tmp_path, receipt=None, profile_text=None)
    (tmp_path / ".aiops" / "target-profile.v2.yaml").mkdir()
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2


def test_profile_ledger_entry_unreadable_is_distinguished_from_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario B."""

    _install(tmp_path, receipt=_receipt())

    import app.agent_review.target_pack_validate_v2 as validate_module

    real_observe = validate_module._observe_artifact_bytes_v2

    def raising_observe(path: Path):
        if path.name == "target-profile.v2.yaml":
            return "unreadable", None
        return real_observe(path)

    monkeypatch.setattr(validate_module, "_observe_artifact_bytes_v2", raising_observe)
    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_UNREADABLE_REASON_V2


def test_profile_ledger_entry_symlink_loop_is_resolution_failed(tmp_path: Path) -> None:
    """Scenario D."""

    _install(tmp_path, receipt=None, profile_text=None)
    (tmp_path / ".aiops" / "target-profile.v2.yaml").symlink_to("target-profile.v2.yaml")
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_RESOLUTION_FAILED_REASON_V2
    assert check.reason_code != PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_profile_ledger_entry_containment_escape_is_path_escapes(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Scenario E."""

    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    outside_profile = outside / "outside-profile.yaml"
    outside_profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(outside_profile)
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=target_root)

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_profile_byte_integrity_is_independent_of_semantic_validity(tmp_path: Path) -> None:
    """Completes the required test matrix for finding 2: the profile's
    SEMANTIC parse result and its filesystem/byte observation are
    different questions. A receipt whose declared byte hash matches the
    ACTUAL (semantically invalid) bytes on disk passes `target_owned_
    integrity` even though the `profile` check itself fails parsing."""

    malformed = "not: [a valid: profile"
    receipt = _receipt(
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(malformed.encode("utf-8"))},
    )
    _install(tmp_path, receipt=receipt, profile_text=malformed)

    report = run_validate_v2(target_root=tmp_path)

    assert _check(report, "profile").status == STATUS_FAIL_V2
    assert _check(report, "target_owned_integrity").status == STATUS_PASS_V2


# --- Codex round 2: four P2 findings closed ---------------------------------
#
# All four production behaviors below were independently reproduced as
# defects against the exact pre-fix module (backed up before this round's
# rewrite) before any production code changed. The fix replaced four
# independent patches with a single typed observation carrier (`_observe_
# artifact_bytes_v2` / `_observe_ledger_entry_hash_v2` / `_aiops_directory_
# status_v2`) shared by both ledgers, per this round's disposable spike and
# the module docstring's "A SINGLE OBSERVATION FUNCTION per artifact kind"
# invariant. See the corresponding PR thread replies for the exact
# reproduction transcripts against the pre-fix HEAD.


# P2-A: `generated_file_hashes` were accepted into `receipt_hash` but never
# locally verified -- a receipt could declare a byte claim about a
# generated file, that file could be deleted or tampered, and `is_valid`
# stayed `True`. ------------------------------------------------------------


def test_generated_file_declared_and_deleted_fails_closed(tmp_path: Path) -> None:
    content = b"generated content"
    receipt = _receipt(generated_file_hashes={"generated-workflow.yml": _sha256(content)})
    _install(tmp_path, receipt=receipt)
    # generated-workflow.yml deliberately never written: declared, absent.

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == GENERATED_FILE_MISSING_REASON_V2
    assert report.is_valid is False


def test_generated_file_declared_and_drifted_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(generated_file_hashes={"generated-workflow.yml": "d" * 64})
    _install(tmp_path, receipt=receipt)
    (tmp_path / "generated-workflow.yml").write_bytes(b"actual different content")

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == GENERATED_FILE_DRIFT_REASON_V2
    assert report.is_valid is False


def test_generated_file_empty_mapping_passes_while_generated_file_set_stays_unavailable(tmp_path: Path) -> None:
    """An empty `generated_file_hashes` makes no byte-integrity claim at
    all, so `generated_file_integrity` trivially passes -- it never means
    "the manifest's generated-file set is complete", which stays
    `unavailable` regardless of how many entries the receipt declares."""

    _install(tmp_path, receipt=_receipt(generated_file_hashes={}))

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_PASS_V2
    assert check.reason_code is None
    assert report.is_valid is True
    assert "generated_file_set" in report.unvalidated_capabilities
    assert _check(report, "generated_file_set").status == STATUS_UNAVAILABLE_V2


def test_generated_file_disposition_is_independent_of_json_member_order(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Mirrors `test_target_owned_disposition_is_independent_of_json_
    member_order` for `generated_file_hashes`: `receipt_hash`'s canonical
    preimage sorts JSON object keys, so raw member order is not part of
    the receipt's identity, and `_verify_declared_ledger_v2`'s own
    traversal is `sorted(entries)` -- the same generic verifier both
    ledgers share (see `test_target_owned_and_generated_file_integrity_
    share_the_same_verifier` below)."""

    root_a = tmp_path_factory.mktemp("gen_order_a")
    root_b = tmp_path_factory.mktemp("gen_order_b")

    ledger = {
        "a-missing.yml": "a" * 64,
        "b-drifted.yml": "b" * 64,
        "c-intact.yml": _sha256(b"intact content"),
    }
    receipt = _receipt(generated_file_hashes=ledger)
    raw_dump = receipt.model_dump(mode="json")

    def raw_json_with_order(order: list[str]) -> str:
        d = dict(raw_dump)
        d["generated_file_hashes"] = {k: ledger[k] for k in order}
        return json.dumps(d)

    order_a = list(ledger)
    order_b = list(reversed(order_a))
    assert order_a != order_b

    raw_a, raw_b = raw_json_with_order(order_a), raw_json_with_order(order_b)
    assert raw_a != raw_b

    receipt_a = load_target_install_receipt_bytes_v2(raw_a)
    receipt_b = load_target_install_receipt_bytes_v2(raw_b)
    assert receipt_a.receipt_hash == receipt_b.receipt_hash

    for root, raw in ((root_a, raw_a), (root_b, raw_b)):
        (root / ".aiops").mkdir()
        (root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
        (root / ".aiops" / "install-receipt.v2.json").write_text(raw, encoding="utf-8")
        (root / "b-drifted.yml").write_bytes(b"WRONG content")
        (root / "c-intact.yml").write_bytes(b"intact content")
        # a-missing.yml deliberately never written.

    report_a = run_validate_v2(target_root=root_a)
    report_b = run_validate_v2(target_root=root_b)

    assert _decision_surface(report_a) == _decision_surface(report_b)


def test_generated_file_declared_path_escaping_via_symlink_is_refused(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    (target_root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    outside_generated = outside / "generated.yml"
    outside_generated.write_text("outside content", encoding="utf-8")
    (target_root / "generated.yml").symlink_to(outside_generated)

    receipt = _receipt(generated_file_hashes={"generated.yml": _sha256(b"outside content")})
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    seen = _read_spy(monkeypatch)
    report = run_validate_v2(target_root=target_root)

    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_ESCAPES_TARGET_ROOT_REASON_V2
    _assert_no_read_escaped(seen, target_root)


def test_generated_file_symlink_loop_is_resolution_failed_not_escape(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (tmp_path / "loopy.yml").symlink_to("loopy.yml")
    receipt = _receipt(generated_file_hashes={"loopy.yml": "c" * 64})
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PATH_RESOLUTION_FAILED_REASON_V2
    assert check.reason_code != PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_generated_file_not_a_regular_file_is_reported_distinctly(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    (tmp_path / "generated-dir").mkdir()
    receipt = _receipt(generated_file_hashes={"generated-dir": "a" * 64})
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == GENERATED_FILE_NOT_A_REGULAR_FILE_REASON_V2


def test_generated_file_unreadable_is_reported_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, receipt=None)
    generated_path = tmp_path / "generated-workflow.yml"
    generated_path.write_bytes(b"generated content")
    receipt = _receipt(generated_file_hashes={"generated-workflow.yml": _sha256(b"generated content")})
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

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


# P2-B: whole-file reads of the two `.aiops` artifacts were unbounded -- a
# multi-hundred-megabyte receipt or profile was fully buffered before any
# parse was attempted. --------------------------------------------------------


def test_receipt_above_budget_is_resource_limit_exceeded(tmp_path: Path) -> None:
    _install(tmp_path, receipt=None)
    import app.agent_review.target_pack_validate_v2 as validate_module

    oversized = b"{" + b"x" * (validate_module._ARTIFACT_BYTE_LIMIT_V2 + 1)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_bytes(oversized)

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_RESOURCE_LIMIT_EXCEEDED_REASON_V2
    assert report.is_valid is False


def test_profile_above_budget_is_resource_limit_exceeded(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt(), profile_text=None)
    import app.agent_review.target_pack_validate_v2 as validate_module

    oversized = "schema_id: " + "x" * (validate_module._ARTIFACT_BYTE_LIMIT_V2 + 1)
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(oversized, encoding="utf-8")

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PROFILE_RESOURCE_LIMIT_EXCEEDED_REASON_V2
    assert report.is_valid is False


def test_artifact_byte_read_exact_limit_boundary_is_precise(tmp_path: Path) -> None:
    """Exact-limit control: content of PRECISELY `_ARTIFACT_BYTE_LIMIT_V2`
    bytes is within budget (`"ok"`); one byte more is `"resource_limit_
    exceeded"`. Exercises `_observe_artifact_bytes_v2` directly -- the
    private function IS the off-by-one boundary; testing only through a
    full receipt/profile parse would conflate this boundary with the
    parser's own separate pass/fail behaviour."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    limit = validate_module._ARTIFACT_BYTE_LIMIT_V2
    at_limit = tmp_path / "at-limit"
    at_limit.write_bytes(b"x" * limit)
    status, content = validate_module._observe_artifact_bytes_v2(at_limit)
    assert status == "ok"
    assert content is not None
    assert len(content) == limit

    over_limit = tmp_path / "over-limit"
    over_limit.write_bytes(b"x" * (limit + 1))
    status2, content2 = validate_module._observe_artifact_bytes_v2(over_limit)
    assert status2 == "resource_limit_exceeded"
    assert content2 is None


def test_receipt_parser_is_never_called_after_a_budget_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refused content must never reach the shared receipt-parsing
    authority at all -- proves the budget refusal happens strictly before
    parsing, not merely that the FINAL disposition is correct."""

    _install(tmp_path, receipt=None)
    import app.agent_review.target_pack_validate_v2 as validate_module

    oversized = b"{" + b"x" * (validate_module._ARTIFACT_BYTE_LIMIT_V2 + 1)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_bytes(oversized)

    parser_calls: list[bytes] = []
    real_parser = validate_module.load_target_install_receipt_bytes_v2

    def spying_parser(raw: bytes):
        parser_calls.append(raw)
        return real_parser(raw)

    monkeypatch.setattr(validate_module, "load_target_install_receipt_bytes_v2", spying_parser)
    report = run_validate_v2(target_root=tmp_path)

    assert parser_calls == [], "the shared receipt parser was invoked on content already refused for exceeding budget"
    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_RESOURCE_LIMIT_EXCEEDED_REASON_V2


def test_validate_never_calls_whole_file_read_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Architecture proof: neither the two bounded `.aiops` artifacts nor
    any receipt-declared ledger entry is ever materialised via whole-file
    `Path.read_bytes()` -- the fix replaced `_observe_artifact_bytes_v2`'s
    prior `read_bytes()` call with bounded `path.open('rb')` +
    `stream.read(limit + 1)`; ledger entries were already streaming via
    `hashlib.file_digest`. A declared generated file is included so the
    ledger side is exercised too, not only the two known-location
    artifacts."""

    generated_content = b"generated content" * 1000
    receipt = _receipt(generated_file_hashes={"generated.yml": _sha256(generated_content)})
    _install(tmp_path, receipt=receipt)
    (tmp_path / "generated.yml").write_bytes(generated_content)

    seen: list[tuple[str, str]] = []
    real_read_bytes = Path.read_bytes

    def spy_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        seen.append(("read_bytes", str(self)))
        return real_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    report = run_validate_v2(target_root=tmp_path)

    assert report.is_valid is True
    assert seen == [], f"validate called whole-file Path.read_bytes(): {seen}"


# P2-C: `.exists()`/`.is_file()` metadata probes sat OUTSIDE the `try/
# except OSError` boundary that wrapped only the read -- a `PermissionError`
# raised by the metadata probe itself escaped `run_validate_v2` as an
# uncaught exception rather than a diagnosed `"unreadable"` disposition. ----


def test_receipt_permission_error_from_metadata_probe_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, receipt=_receipt())
    target_real = (tmp_path / ".aiops" / "install-receipt.v2.json").resolve()
    real_exists = Path.exists

    def raising_exists(self: Path) -> bool:
        if self.resolve() == target_real:
            raise PermissionError(13, "Permission denied")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", raising_exists)
    report = run_validate_v2(target_root=tmp_path)  # must not raise -- no traceback

    check = _check(report, "receipt")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == RECEIPT_UNREADABLE_REASON_V2


def test_profile_permission_error_from_metadata_probe_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, receipt=_receipt())
    target_real = (tmp_path / ".aiops" / "target-profile.v2.yaml").resolve()
    real_exists = Path.exists

    def raising_exists(self: Path) -> bool:
        if self.resolve() == target_real:
            raise PermissionError(13, "Permission denied")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", raising_exists)
    report = run_validate_v2(target_root=tmp_path)  # must not raise -- no traceback

    check = _check(report, "profile")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == PROFILE_UNREADABLE_REASON_V2


def test_target_owned_ledger_permission_error_from_metadata_probe_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Targets `.is_file()` rather than `.exists()` (unlike the receipt/
    profile artifact tests above) so both metadata probes this module
    relies on are exercised at least once across the suite."""

    _install(tmp_path, receipt=None)
    extra_path = tmp_path / ".aiops" / "extra-owned.txt"
    extra_path.write_bytes(b"extra content")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/extra-owned.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/extra-owned.txt": _sha256(b"extra content"),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    extra_real = extra_path.resolve()
    real_is_file = Path.is_file

    def raising_is_file(self: Path) -> bool:
        if self.resolve() == extra_real:
            raise PermissionError(13, "Permission denied")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", raising_is_file)
    report = run_validate_v2(target_root=tmp_path)  # must not raise -- no traceback

    check = _check(report, "target_owned_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == TARGET_OWNED_UNREADABLE_REASON_V2


def test_generated_file_ledger_permission_error_from_metadata_probe_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(tmp_path, receipt=None)
    generated_path = tmp_path / "generated-workflow.yml"
    generated_path.write_bytes(b"generated content")
    receipt = _receipt(generated_file_hashes={"generated-workflow.yml": _sha256(b"generated content")})
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    generated_real = generated_path.resolve()
    real_is_file = Path.is_file

    def raising_is_file(self: Path) -> bool:
        if self.resolve() == generated_real:
            raise PermissionError(13, "Permission denied")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", raising_is_file)
    report = run_validate_v2(target_root=tmp_path)  # must not raise -- no traceback

    check = _check(report, "generated_file_integrity")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == GENERATED_FILE_UNREADABLE_REASON_V2


# P2-D: a `.aiops` candidate that resolved as CONTAINED but did not actually
# exist (or was a plain file, or raised `OSError` on probe) was treated as
# `aiops_snapshot: pass` -- containment was silently extended into a
# directory-existence claim it never proved. -------------------------------


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


def test_aiops_directory_permission_error_from_metadata_probe_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".aiops").mkdir()
    aiops_real = (tmp_path / ".aiops").resolve()
    real_exists = Path.exists

    def raising_exists(self: Path) -> bool:
        if self.resolve() == aiops_real:
            raise PermissionError(13, "Permission denied")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", raising_exists)
    report = run_validate_v2(target_root=tmp_path)  # must not raise -- no traceback

    check = _check(report, "aiops_snapshot")
    assert check.status == STATUS_FAIL_V2
    assert check.reason_code == AIOPS_UNREADABLE_REASON_V2


def test_aiops_directory_positive_control_passes(tmp_path: Path) -> None:
    _install(tmp_path, receipt=_receipt())

    report = run_validate_v2(target_root=tmp_path)

    check = _check(report, "aiops_snapshot")
    assert check.status == STATUS_PASS_V2
    assert check.reason_code is None


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param("missing", id="aiops_missing"),
        pytest.param("not_a_directory", id="aiops_not_a_directory"),
    ],
)
def test_artifact_checks_are_absent_when_aiops_snapshot_fails(tmp_path: Path, mode: str) -> None:
    """Containment is not existence: receipt/profile (and everything
    downstream of them) must never be looked up unless `.aiops` itself is
    observed to actually be a readable directory -- an early refusal must
    be an ABSENCE of these check names, not merely a `fail` status for
    them."""

    if mode == "not_a_directory":
        (tmp_path / ".aiops").write_text("not a directory", encoding="utf-8")
    # "missing": .aiops deliberately never created at all.

    report = run_validate_v2(target_root=tmp_path)

    present_names = {c.name for c in report.checks}
    for absent_name in (
        "receipt",
        "profile",
        "profile_hash",
        "profile_identity",
        "root_identity",
        "target_owned_integrity",
        "generated_file_integrity",
    ):
        assert absent_name not in present_names, f"{absent_name!r} was emitted despite aiops_snapshot failing"


# --- Round 2 structural completeness (spike properties, pinned in production) -


def test_observe_artifact_bytes_status_vocabulary_is_closed_and_pairwise_reachable(tmp_path: Path) -> None:
    """The disposable observation-algebra spike's completeness property
    (`ker(alpha) subseteq intersection ker(d_i)`), pinned directly against
    the production function: a collapse back to a `bytes | None` /
    parallel-Optional-fields projection would make several of these
    states indistinguishable from each other. Enumerates every
    filesystem-observable state `_observe_artifact_bytes_v2` can report
    (excluding `"unreadable"`, which needs a monkeypatch and is already
    pinned distinctly by the P2-C tests above) and asserts all four are
    pairwise distinct status tags."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    missing = tmp_path / "missing"
    not_a_file = tmp_path / "a-directory"
    not_a_file.mkdir()
    ok = tmp_path / "ok"
    ok.write_bytes(b"small content")
    over_limit = tmp_path / "over-limit"
    over_limit.write_bytes(b"x" * (validate_module._ARTIFACT_BYTE_LIMIT_V2 + 1))

    observe = validate_module._observe_artifact_bytes_v2
    statuses = {
        "missing": observe(missing)[0],
        "not_a_regular_file": observe(not_a_file)[0],
        "resource_limit_exceeded": observe(over_limit)[0],
        "ok": observe(ok)[0],
    }
    assert statuses == {
        "missing": "missing",
        "not_a_regular_file": "not_a_regular_file",
        "resource_limit_exceeded": "resource_limit_exceeded",
        "ok": "ok",
    }
    assert len(set(statuses.values())) == len(statuses), "two observably-different states collapsed to one status tag"


def test_target_owned_and_generated_file_integrity_share_the_same_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a future change duplicated target-owned/generated-file
    verification into two independently maintained loops, a taxonomy fix
    applied to one could silently fail to apply to the other -- exactly
    the class of drift this round's spike closed by giving both ledgers
    ONE shared generic verifier. Proven mechanically: both `_target_
    owned_integrity_check_v2` and `_generated_file_integrity_check_v2`
    must route through the SAME `_verify_declared_ledger_v2` call, once
    each, never two separately maintained implementations."""

    import app.agent_review.target_pack_validate_v2 as validate_module

    call_names: list[str] = []
    real_verify = validate_module._verify_declared_ledger_v2

    def spying_verify(*, check_name: str, **kwargs: object):
        call_names.append(check_name)
        return real_verify(check_name=check_name, **kwargs)

    monkeypatch.setattr(validate_module, "_verify_declared_ledger_v2", spying_verify)

    receipt = _receipt(generated_file_hashes={})
    _install(tmp_path, receipt=receipt)
    run_validate_v2(target_root=tmp_path)

    assert call_names == ["target_owned_integrity", "generated_file_integrity"], (
        f"expected both ledgers to route through the shared verifier exactly once each, got: {call_names}"
    )
