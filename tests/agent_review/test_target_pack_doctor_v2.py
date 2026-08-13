from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2
from app.agent_review.target_pack_doctor_v2 import (
    DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2,
    DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2,
    run_doctor_v2,
)
from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
)
from app.agent_review.target_pack_receipt_v2 import TargetInstallReceiptV2, compute_target_install_receipt_hash_v2


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest() -> TargetPackManifestV2:
    return TargetPackManifestV2(
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
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )


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
    - pytest
  allowed_semantic_groups:
    - primary_backend_logic
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts: []
limitations: []
"""


def _real_profile_hash() -> str:
    """The actual `compute_profile_hash_v2` of `_VALID_PROFILE_YAML` --
    NOT the same value as `_manifest()`'s `content_sha256` (that is the
    raw seed-bytes digest `target_pack_plan_v2` uses for drift detection;
    this is the model-level digest `_check_receipt_v2` now cross-checks a
    receipt's `target_profile_hash` against). Computed here, not
    hardcoded, so it can never silently drift from what `profile_loader_
    v2` actually computes."""
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        (root / ".aiops").mkdir()
        (root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
        return compute_profile_hash_v2(load_target_profile_v2(str(root)))


def _receipt(required_secret_names: tuple[str, ...] = (), **overrides: object) -> TargetInstallReceiptV2:
    fields = dict(
        schema_id="agent-review.target-install-receipt.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        target_repo="owner/repo",
        target_profile_hash=_real_profile_hash(),
        target_policy_hash="b" * 64,
        review_pack_hashes={},
        generated_file_hashes={},
        target_owned_paths=(),
        required_capabilities=(),
        expected_runner_labels=(),
        required_secret_names=required_secret_names,
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


def test_doctor_reports_missing_profile_and_receipt_without_creating_anything(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())
    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest())

    assert report.profile.status == "missing"
    assert report.receipt.status == "missing"
    assert not report.is_healthy
    # Read-only: doctor must not have created ANYTHING.
    assert list(tmp_path.iterdir()) == before


def test_doctor_reports_invalid_profile_without_mutating(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text("not: valid: yaml: at: all: :::", encoding="utf-8")
    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest())
    assert report.profile.status in {"invalid", "missing"}
    assert not report.is_healthy


def test_doctor_reports_healthy_when_everything_present(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt_path = tmp_path / ".aiops" / "install-receipt.v2.json"
    receipt_path.write_text(json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8")

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest())

    assert report.profile.status == "present"
    assert report.profile.profile_hash is not None
    assert report.receipt.status == "present"
    assert report.is_healthy


def test_doctor_checks_secret_name_presence_never_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(required_secret_names=("AGENT_ROUTER_API_KEY", "MISSING_SECRET_NAME"))
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    monkeypatch.setenv("AGENT_ROUTER_API_KEY", "this-value-must-never-appear-in-the-report")
    monkeypatch.delenv("MISSING_SECRET_NAME", raising=False)

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest())

    by_name = {check.name: check.declared_present for check in report.secret_names}
    assert by_name == {"AGENT_ROUTER_API_KEY": True, "MISSING_SECRET_NAME": False}
    assert not report.is_healthy  # one secret missing
    # The VALUE never appears anywhere in the report's own repr.
    assert "this-value-must-never-appear-in-the-report" not in repr(report)


def test_doctor_refuses_a_target_root_that_is_not_a_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-dir.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError) as exc_info:
        run_doctor_v2(target_root=not_a_dir, manifest=_manifest())
    assert DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 in str(exc_info.value)


def test_doctor_reports_unhealthy_when_receipt_pack_version_does_not_match_the_manifest(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: a structurally
    valid, self-hash-consistent receipt claiming a DIFFERENT pack_version
    than the manifest being diagnosed against used to be reported
    `status="present"` / `is_healthy=True` -- doctor asserted a healthy
    install without ever checking it was looking at the install it thinks
    it is."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(pack_version="0.0.1-stale")
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest())

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_reports_unhealthy_when_receipt_toolrepo_sha_does_not_match_the_manifest(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(toolrepo_sha="9" * 40)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest())

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_reports_unhealthy_when_receipt_profile_hash_does_not_match_the_loaded_profile(
    tmp_path: Path,
) -> None:
    """The most severe of the three: a receipt can be internally
    consistent (matching pack_version/toolrepo_sha) while claiming
    provenance against a target-profile that is not the one actually on
    disk -- e.g. copied from a different target, or stale after the
    profile was hand-edited post-install."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(target_profile_hash="f" * 64)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest())

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_skips_profile_hash_comparison_when_profile_itself_is_not_loadable(tmp_path: Path) -> None:
    """When the profile is missing entirely, there is nothing meaningful
    to compare a receipt's `target_profile_hash` claim against -- doctor
    must not crash trying, and `is_healthy` is already false via
    `profile.status`, not via a spurious profile-hash reason code."""
    receipt = _receipt()
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest())

    assert report.profile.status == "missing"
    assert report.receipt.status == "present"
    assert not report.is_healthy
