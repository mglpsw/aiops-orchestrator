from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2
from app.agent_review.target_pack_doctor_v2 import (
    DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2,
    DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2,
    DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_TARGET_REPO_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2,
    DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2,
    DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2,
    run_doctor_v2,
)
from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
    compute_target_pack_manifest_digest_v2,
)
from app.agent_review.target_pack_receipt_v2 import (
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
    compute_target_install_receipt_hash_v2,
)


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
        manifest_digest=compute_target_pack_manifest_digest_v2(_manifest()),
        target_repo="owner/repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="owner/repo"),
        target_profile_hash=_real_profile_hash(),
        target_policy_hash="b" * 64,
        review_pack_hashes={},
        generated_file_hashes={},
        target_owned_file_hashes={},
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
    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert report.profile.status == "missing"
    assert report.receipt.status == "missing"
    assert not report.is_healthy
    # Read-only: doctor must not have created ANYTHING.
    assert list(tmp_path.iterdir()) == before


def test_doctor_reports_invalid_profile_without_mutating(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text("not: valid: yaml: at: all: :::", encoding="utf-8")
    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    assert report.profile.status in {"invalid", "missing"}
    assert not report.is_healthy


def test_doctor_reports_healthy_when_everything_present(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt_path = tmp_path / ".aiops" / "install-receipt.v2.json"
    # A genuinely healthy install's receipt DOES record its target-owned
    # set -- every real `init` populates it (aiops-orchestrator#205, C3).
    # An empty default here would (correctly, post-fix) no longer count as
    # healthy, since it would not match the manifest's own TARGET_OWNED
    # classification.
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    receipt_path.write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

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

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    by_name = {check.name: check.declared_present for check in report.secret_names}
    assert by_name == {"AGENT_ROUTER_API_KEY": True, "MISSING_SECRET_NAME": False}
    assert not report.is_healthy  # one secret missing
    # The VALUE never appears anywhere in the report's own repr.
    assert "this-value-must-never-appear-in-the-report" not in repr(report)


def test_doctor_refuses_a_target_root_that_is_not_a_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-dir.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError) as exc_info:
        run_doctor_v2(target_root=not_a_dir, manifest=_manifest(), target_repo="owner/repo")
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

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

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

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

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

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2


def test_doctor_reports_unreconciled_target_owned_bytes_even_when_semantics_match(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    profile_path = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile_path.write_text(_VALID_PROFILE_YAML + "\n# formatting only\n", encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert report.profile.status == "present"
    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2
    assert not report.is_healthy


def test_doctor_reports_unhealthy_when_receipt_rollout_mode_exceeds_pack_capability(tmp_path: Path) -> None:
    """Follow-on adversarial finding from the same review pass, confirmed
    and fixed: a receipt can be internally consistent on pack_version/
    toolrepo_sha/target_profile_hash while still claiming a rollout_mode
    (e.g. shadow_full) the manifest being diagnosed against cannot
    deliver -- e.g. stale from a since-downgraded or reverted pack
    version. The same class of defect P2-4 fixed for `init`, reachable
    through `doctor` instead."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(rollout_mode="shadow_full")
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2
    assert not report.is_healthy


def test_doctor_skips_profile_hash_comparison_when_profile_itself_is_not_loadable(tmp_path: Path) -> None:
    """When the profile is missing entirely, there is nothing meaningful
    to compare a receipt's `target_profile_hash` claim against -- doctor
    must not crash trying, and `is_healthy` is already false via
    `profile.status`, not via a spurious profile-hash reason code.

    Uses a manifest with no TARGET_OWNED entries at all (an
    UPSTREAM_GENERATED-only pack version) so an empty
    `target_owned_paths` legitimately matches it -- this test's premise
    (the profile file is entirely absent) is otherwise incompatible with
    the target-owned reconciliation `#205`/C3 added: the profile IS the
    only TARGET_OWNED path the shared `_manifest()` fixture declares, so a
    receipt cannot claim to have reconciled it while the file is absent."""
    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path="templates/workflow.yml",
                ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED,
                content_sha256="a" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    receipt = _receipt(manifest_digest=compute_target_pack_manifest_digest_v2(manifest))
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo")

    assert report.profile.status == "missing"
    assert report.receipt.status == "present"
    assert not report.is_healthy


# --- Post-merge review debt (aiops-orchestrator#205, C2/C3) -----------------


def test_doctor_reports_healthy_for_correct_target_repo(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert report.is_healthy


def test_doctor_refuses_a_receipt_transplanted_from_a_different_target(tmp_path: Path) -> None:
    """RED for C2, library level (see the CLI-level end-to-end reproduction
    in `test_agent_review_target_pack_v2_cli.py`). Previously,
    `run_doctor_v2` had no `target_repo` parameter at all -- the only
    identity source was `receipt.portable_target_root_identity`, which is
    itself derived from `receipt.target_repo`, so a receipt is always
    internally self-consistent no matter which target it actually came
    from. Confirmed by reproduction before this fix: copying a healthy
    install's `.aiops/` into an unrelated `tmp_path` reported
    `healthy: true` regardless of which repository was actually being
    diagnosed."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_repo="acme/original-repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="acme/original-repo"),
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="acme/a-completely-different-repo")

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TARGET_REPO_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_refuses_a_receipt_whose_target_owned_set_was_shrunk_to_empty(tmp_path: Path) -> None:
    """RED for C3. Reproduced before this fix: shrinking a receipt's
    `target_owned_paths`/`target_owned_file_hashes` to `{}` makes the
    per-file reconciliation loop iterate zero times -- trivially
    "successful" -- while a SEPARATE tampered on-disk profile (with
    `target_profile_hash` realigned to the tampered bytes, so the
    unrelated profile-hash check also passes) went completely
    unreconciled. `healthy: true` was reported for a target-owned file
    that was never read, hashed, or compared against anything."""
    (tmp_path / ".aiops").mkdir()
    tampered_profile = _VALID_PROFILE_YAML + "\n# attacker-injected line\n"
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(tampered_profile, encoding="utf-8")
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        (root / ".aiops").mkdir()
        (root / ".aiops" / "target-profile.v2.yaml").write_text(tampered_profile, encoding="utf-8")
        tampered_profile_hash = compute_profile_hash_v2(load_target_profile_v2(str(root)))

    receipt = _receipt(target_owned_paths=(), target_owned_file_hashes={}, target_profile_hash=tampered_profile_hash)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_refuses_a_receipt_whose_target_owned_set_is_a_strict_superset(tmp_path: Path) -> None:
    """Adversarial matrix item: the reconciliation must be a SET equality,
    not merely "does the receipt cover at least what the manifest
    requires" -- a receipt that also claims an extra, manifest-unknown
    target-owned path is equally not describing this pack version's real
    TARGET_OWNED set."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    # The extra path is a REAL file on disk with a hash that matches the
    # receipt's own claim -- the per-file loop alone would pass it (there
    # is nothing "unreconciled" about it byte-for-byte). Only the set
    # comparison against the manifest's actual TARGET_OWNED classification
    # can catch a claim of ownership over a path the manifest never
    # declared as TARGET_OWNED at all.
    (tmp_path / ".aiops" / "extra-unknown-file.txt").write_text("attacker content", encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/extra-unknown-file.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/extra-unknown-file.txt": _sha256(b"attacker content"),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2


def test_doctor_target_owned_set_reconciliation_is_order_independent(tmp_path: Path) -> None:
    """PASSO 3 item 11: the manifest's TARGET_OWNED set and the receipt's
    claimed set are compared as sets, so declaration order never affects
    the outcome. Uses a local two-entry manifest (the shared `_manifest()`
    fixture only has one TARGET_OWNED path) so there is a real permutation
    to prove order-independence with."""
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
            GeneratedFileEntryV2(
                path=".aiops/second-target-owned.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="b" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (tmp_path / ".aiops" / "second-target-owned.yaml").write_text("x", encoding="utf-8")
    # Receipt's tuple order is the REVERSE of the manifest's declaration
    # order above -- the comparison must not care.
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/second-target-owned.yaml", ".aiops/target-profile.v2.yaml"),
        target_owned_file_hashes={
            ".aiops/second-target-owned.yaml": _sha256(b"x"),
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo")

    assert report.receipt.status == "present"
    assert report.receipt.reason_code is None


# --- H1A-R1: symlink-mediated read escape (independent review finding) -----
#
# `RelativePath` (the C1/C4 retype above) proves a path STRING is well-formed
# -- no `..`, no absolute/drive form. It says nothing about what an EXISTING
# component on disk resolves to. Reproduced against PR #230's own head
# (dd6d72b) before this fix: with `.aiops/target-profile.v2.yaml` symlinked
# outside `target_root`, `run_doctor_v2` returned `is_healthy=True` while a
# `Path.read_text`/`read_bytes` spy recorded both the profile read and the
# target-owned read resolving outside `target_root`.


def _read_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Records (kind, resolved_path) for every `Path` content read."""
    seen: list[tuple[str, str]] = []
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def spy_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        seen.append(("read_bytes", str(self.resolve())))
        return original_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    def spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
        seen.append(("read_text", str(self.resolve())))
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(Path, "read_text", spy_read_text)
    return seen


def _assert_no_read_escaped(seen: list[tuple[str, str]], target_root: Path) -> None:
    root_real = str(target_root.resolve())
    escaping = [entry for entry in seen if not entry[1].startswith(root_real + os.sep) and entry[1] != root_real]
    assert not escaping, f"a read resolved outside target_root: {escaping}"


def test_doctor_refuses_a_profile_symlinked_outside_target_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED for H1A-R1, narrowest form: only `target-profile.v2.yaml` is a
    symlink pointing outside `target_root`."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    outside_profile = outside / "outside-profile.yaml"
    outside_profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(outside_profile)

    seen = _read_spy(monkeypatch)
    report = run_doctor_v2(target_root=target_root, manifest=_manifest(), target_repo="owner/repo")

    assert report.profile.status == "invalid"
    assert report.profile.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert not report.is_healthy
    _assert_no_read_escaped(seen, target_root)


def test_doctor_refuses_when_the_whole_aiops_directory_is_symlinked_outside(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED for H1A-R1, broadest form: an intermediate path COMPONENT
    (`.aiops` itself) is the symlink, so both the profile and the receipt
    resolve outside `target_root` even though every path string involved is
    a perfectly valid `RelativePath`."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (outside / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (outside / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )
    (target_root / ".aiops").symlink_to(outside, target_is_directory=True)

    seen = _read_spy(monkeypatch)
    report = run_doctor_v2(target_root=target_root, manifest=_manifest(), target_repo="owner/repo")

    assert report.profile.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert report.receipt.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert not report.is_healthy
    _assert_no_read_escaped(seen, target_root)


def test_doctor_refuses_a_target_owned_file_symlinked_outside_target_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED for H1A-R1 on the target-owned reconciliation loop specifically:
    a real, in-root profile and receipt, but the receipt's target-owned
    entry points at a path whose on-disk form escapes."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    (target_root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    outside_owned = outside / "owned.txt"
    outside_owned.write_text("outside content", encoding="utf-8")
    (target_root / ".aiops" / "owned.txt").symlink_to(outside_owned)

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
            GeneratedFileEntryV2(
                path=".aiops/owned.txt",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="b" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/owned.txt", ".aiops/target-profile.v2.yaml"),
        target_owned_file_hashes={
            ".aiops/owned.txt": _sha256(b"outside content"),
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
        },
    )
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    seen = _read_spy(monkeypatch)
    report = run_doctor_v2(target_root=target_root, manifest=manifest, target_repo="owner/repo")

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert not report.is_healthy
    _assert_no_read_escaped(seen, target_root)


def test_doctor_allows_a_symlink_that_resolves_back_inside_target_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The policy is CONTAINMENT, not symlink prohibition -- identical in
    meaning to `target_pack_install_v2`'s write-side check. Without this
    test the fix could silently become "no symlinks at all", a second,
    stricter policy the writer does not share."""
    target_root = tmp_path_factory.mktemp("target")
    (target_root / ".aiops").mkdir()
    real_profile = target_root / ".aiops" / "real-profile.yaml"
    real_profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(real_profile)
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = run_doctor_v2(target_root=target_root, manifest=_manifest(), target_repo="owner/repo")

    assert report.profile.status == "present"
    assert report.receipt.status == "present"
    assert report.is_healthy
