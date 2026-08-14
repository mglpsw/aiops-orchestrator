"""RED/lock tests for `agent-review target validate` (#203-S2).

`validate` is deliberately NOT `doctor`:

- `doctor` answers "does this install match THE PACK it claims to come
  from?" -- it needs `--toolrepo-root`/`--pack-version` to rebuild the
  upstream manifest and cross-check against it.
- `validate` answers "is this target's own installed state internally
  coherent, undrifted and safe?" -- it takes ONLY `--target-root`, so a
  consumer repository can run it in its own CI without a toolrepo
  checkout anywhere in sight.

Both are read-only. `validate`'s read-only-ness is proven mechanically in
`test_target_pack_arch_v2.py`, the same way `doctor`'s already is.

Capability honesty (#203-S2 grant): this slice ships no
`TrustedCheckInventoryV2`, no trusted-check seed and no workflow
installation, so the trusted-check dimension is reported as
`unavailable` with a reason code naming the slice that will deliver it --
never as a pass. Absence of a delivered capability is not successful
validation of that capability.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2
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
    VALIDATE_PATH_ESCAPES_TARGET_ROOT_REASON_V2,
    VALIDATE_PROFILE_HASH_MISMATCH_REASON_V2,
    VALIDATE_PROFILE_INVALID_REASON_V2,
    VALIDATE_PROFILE_MISSING_REASON_V2,
    VALIDATE_RECEIPT_INVALID_REASON_V2,
    VALIDATE_RECEIPT_MISSING_REASON_V2,
    VALIDATE_ROOT_IDENTITY_MISMATCH_REASON_V2,
    VALIDATE_TARGET_OWNED_DRIFT_REASON_V2,
    VALIDATE_TARGET_OWNED_MISSING_REASON_V2,
    VALIDATE_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2,
    VALIDATE_TRUSTED_CHECK_INVENTORY_DEFERRED_REASON_V2,
    STATUS_FAIL_V2,
    STATUS_PASS_V2,
    STATUS_UNAVAILABLE_V2,
    TRUSTED_CHECK_INVENTORY_CHECK_V2,
    run_validate_v2,
)

_PROFILE_RELATIVE_PATH = ".aiops/target-profile.v2.yaml"


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
    - pytest
  allowed_semantic_groups:
    - primary_backend_logic
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts: []
limitations: []
"""


def _manifest() -> TargetPackManifestV2:
    return TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=_PROFILE_RELATIVE_PATH,
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256=_sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="off",
    )


def _real_profile_hash() -> str:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        (root / ".aiops").mkdir()
        (root / _PROFILE_RELATIVE_PATH).write_text(_VALID_PROFILE_YAML, encoding="utf-8")
        return compute_profile_hash_v2(load_target_profile_v2(str(root)))


def _receipt(**overrides: object) -> TargetInstallReceiptV2:
    fields = dict(
        schema_id="agent-review.target-install-receipt.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        manifest_digest=compute_target_pack_manifest_digest_v2(_manifest()),
        target_repo="owner/repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="owner/repo"),
        target_profile_hash=_real_profile_hash(),
        target_policy_hash=None,
        review_pack_hashes={},
        generated_file_hashes={},
        target_owned_file_hashes={_PROFILE_RELATIVE_PATH: _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
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


def _receipt_on_disk_bytes(receipt: TargetInstallReceiptV2) -> bytes:
    """Byte-for-byte what `target_pack_install_v2.write_receipt_v2`
    persists. Deliberately NOT `canonical_target_install_receipt_bytes_v2`
    -- that is the hash PRE-IMAGE and excludes `receipt_hash`, so a file
    written from it cannot be parsed back into a receipt at all."""

    return (
        json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _install(root: Path, *, receipt: TargetInstallReceiptV2 | None = None, profile: str | None = None) -> None:
    (root / ".aiops").mkdir(parents=True, exist_ok=True)
    if profile is not None:
        (root / _PROFILE_RELATIVE_PATH).write_text(profile, encoding="utf-8")
    if receipt is not None:
        (root / RECEIPT_RELATIVE_PATH_V2).write_bytes(_receipt_on_disk_bytes(receipt))


@pytest.fixture
def target_root():
    path = Path(tempfile.mkdtemp())
    try:
        yield path
    finally:
        import shutil

        shutil.rmtree(path, ignore_errors=True)


def _check(report, name: str):
    for item in report.checks:
        if item.name == name:
            return item
    raise AssertionError(f"no check named {name!r} in {[c.name for c in report.checks]}")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_installation_validates(target_root):
    _install(target_root, receipt=_receipt(), profile=_VALID_PROFILE_YAML)
    report = run_validate_v2(target_root=target_root)
    failures = [c for c in report.checks if c.status == STATUS_FAIL_V2]
    assert failures == [], failures
    assert report.is_valid is True


def test_validate_is_deterministic_across_repeated_runs(target_root):
    _install(target_root, receipt=_receipt(), profile=_VALID_PROFILE_YAML)
    first = run_validate_v2(target_root=target_root)
    second = run_validate_v2(target_root=target_root)
    assert [(c.name, c.status, c.reason_code) for c in first.checks] == [
        (c.name, c.status, c.reason_code) for c in second.checks
    ]


# ---------------------------------------------------------------------------
# Capability honesty: the trusted-check dimension is NEVER a pass in S2
# ---------------------------------------------------------------------------


def test_trusted_check_inventory_is_unavailable_never_pass(target_root):
    """#203-S2 grant, explicit: this slice ships no TrustedCheckInventoryV2,
    no seed and no workflow wiring, so this dimension must be reported as
    unavailable with a reason naming the deferral -- never as a pass, and
    never silently omitted."""
    _install(target_root, receipt=_receipt(), profile=_VALID_PROFILE_YAML)
    report = run_validate_v2(target_root=target_root)
    check = _check(report, TRUSTED_CHECK_INVENTORY_CHECK_V2)
    assert check.status == STATUS_UNAVAILABLE_V2
    assert check.status != STATUS_PASS_V2
    assert check.reason_code == VALIDATE_TRUSTED_CHECK_INVENTORY_DEFERRED_REASON_V2


def test_unavailable_capability_is_listed_and_does_not_count_as_validated(target_root):
    """`is_valid` must not be readable as "everything was validated". The
    report exposes explicitly which capabilities were NOT validated."""
    _install(target_root, receipt=_receipt(), profile=_VALID_PROFILE_YAML)
    report = run_validate_v2(target_root=target_root)
    assert TRUSTED_CHECK_INVENTORY_CHECK_V2 in report.unvalidated_capabilities
    assert report.is_valid is True  # no failures ...
    # ... but the unvalidated dimension is still visible, not folded away.
    assert report.unvalidated_capabilities != ()


# ---------------------------------------------------------------------------
# Fail-closed: structural
# ---------------------------------------------------------------------------


def test_target_root_not_a_directory_fails_closed(target_root):
    file_path = target_root / "a-file"
    file_path.write_text("x", encoding="utf-8")
    report = run_validate_v2(target_root=file_path)
    assert report.is_valid is False
    assert any(c.reason_code == VALIDATE_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 for c in report.checks)


def test_missing_receipt_fails_closed(target_root):
    _install(target_root, profile=_VALID_PROFILE_YAML)
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "receipt").reason_code == VALIDATE_RECEIPT_MISSING_REASON_V2


def test_missing_profile_fails_closed(target_root):
    _install(target_root, receipt=_receipt())
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "profile").reason_code == VALIDATE_PROFILE_MISSING_REASON_V2


def test_unparseable_receipt_fails_closed(target_root):
    _install(target_root, profile=_VALID_PROFILE_YAML)
    (target_root / RECEIPT_RELATIVE_PATH_V2).write_text("{not json", encoding="utf-8")
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "receipt").reason_code == VALIDATE_RECEIPT_INVALID_REASON_V2


def test_unparseable_profile_fails_closed(target_root):
    _install(target_root, receipt=_receipt())
    (target_root / _PROFILE_RELATIVE_PATH).write_text("not: [a valid: profile", encoding="utf-8")
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "profile").reason_code == VALIDATE_PROFILE_INVALID_REASON_V2


# ---------------------------------------------------------------------------
# Fail-closed: identity and tamper
# ---------------------------------------------------------------------------


def test_tampered_receipt_fails_closed_at_parse(target_root):
    """A receipt edited after write -- its recorded `receipt_hash` no
    longer describes its own bytes.

    This is refused as `receipt_invalid`, not as a separate hash check:
    `TargetInstallReceiptV2` already enforces the hash in its own
    `model_validator`, so such a file cannot parse at all. `validate`
    deliberately ships no independent receipt_hash check, because it
    would be structurally unreachable -- and an unreachable check would
    advertise verification this module never actually performs."""
    receipt = _receipt()
    # Edited after write: the bytes still parse as a receipt, but the
    # recorded receipt_hash no longer describes them. Built with
    # model_construct so the contract's own validator does not reject the
    # tampered object before it can be written to disk.
    tampered = TargetInstallReceiptV2.model_construct(
        **{**receipt.model_dump(), "pack_version": "9.9.9"}
    )
    _install(target_root, profile=_VALID_PROFILE_YAML)
    (target_root / RECEIPT_RELATIVE_PATH_V2).write_text(
        json.dumps(tampered.model_dump(mode="json"), sort_keys=True), encoding="utf-8"
    )
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "receipt").reason_code == VALIDATE_RECEIPT_INVALID_REASON_V2
    assert "receipt_hash" not in [c.name for c in report.checks]


def test_profile_hash_mismatch_fails_closed(target_root):
    """Receipt claims a profile digest that the on-disk profile does not
    have -- the profile changed semantically after install."""
    _install(target_root, receipt=_receipt(target_profile_hash="c" * 64), profile=_VALID_PROFILE_YAML)
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "profile_hash").reason_code == VALIDATE_PROFILE_HASH_MISMATCH_REASON_V2


def test_root_identity_mismatch_fails_closed(target_root):
    """portable_target_root_identity must be the digest of the receipt's
    own target_repo -- a receipt copied from another repository fails."""
    _install(
        target_root,
        receipt=_receipt(portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="other/repo")),
        profile=_VALID_PROFILE_YAML,
    )
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "root_identity").reason_code == VALIDATE_ROOT_IDENTITY_MISMATCH_REASON_V2


# ---------------------------------------------------------------------------
# Fail-closed: drift on target-owned content
# ---------------------------------------------------------------------------


def test_target_owned_content_drift_fails_closed(target_root):
    _install(target_root, receipt=_receipt(), profile=_VALID_PROFILE_YAML)
    (target_root / _PROFILE_RELATIVE_PATH).write_text(
        _VALID_PROFILE_YAML + "\n# an edit the receipt never recorded\n", encoding="utf-8"
    )
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "target_owned").reason_code == VALIDATE_TARGET_OWNED_DRIFT_REASON_V2


def test_target_owned_file_deleted_fails_closed(target_root):
    _install(target_root, receipt=_receipt(), profile=_VALID_PROFILE_YAML)
    (target_root / _PROFILE_RELATIVE_PATH).unlink()
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "target_owned").reason_code == VALIDATE_TARGET_OWNED_MISSING_REASON_V2


@pytest.mark.skipif(os.name != "posix", reason="symlink containment is a POSIX-path concern here")
def test_target_owned_path_escaping_via_symlink_fails_closed(target_root):
    """Containment is bound to the read, not merely checked before it --
    the same invariant H1-A established for init/doctor."""
    outside = Path(tempfile.mkdtemp())
    try:
        (outside / "elsewhere.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
        (target_root / ".aiops").mkdir(parents=True, exist_ok=True)
        (target_root / RECEIPT_RELATIVE_PATH_V2).write_bytes(_receipt_on_disk_bytes(_receipt()))
        (target_root / _PROFILE_RELATIVE_PATH).symlink_to(outside / "elsewhere.yaml")
        report = run_validate_v2(target_root=target_root)
        assert report.is_valid is False
        assert any(
            c.reason_code == VALIDATE_PATH_ESCAPES_TARGET_ROOT_REASON_V2 for c in report.checks
        ), [(c.name, c.reason_code) for c in report.checks]
    finally:
        import shutil

        shutil.rmtree(outside, ignore_errors=True)


# ---------------------------------------------------------------------------
# Read-only: proven behaviourally here, mechanically in test_target_pack_arch_v2
# ---------------------------------------------------------------------------


def test_validate_never_mutates_the_target(target_root):
    _install(target_root, receipt=_receipt(), profile=_VALID_PROFILE_YAML)

    def snapshot() -> dict[str, str]:
        out: dict[str, str] = {}
        for path in sorted(target_root.rglob("*")):
            rel = str(path.relative_to(target_root))
            out[rel] = "dir" if path.is_dir() else _sha256(path.read_bytes())
        return out

    before = snapshot()
    run_validate_v2(target_root=target_root)
    assert snapshot() == before


def test_validate_does_not_create_a_missing_target_root():
    root = Path(tempfile.mkdtemp()) / "never-created"
    report = run_validate_v2(target_root=root)
    assert report.is_valid is False
    assert not root.exists()


# ---------------------------------------------------------------------------
# PR #235 adversarial review round 1 -- all LIVE_CONFIRMED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["shadow_minimal", "shadow_full"])
def test_receipt_rollout_above_this_packs_ceiling_fails_closed(target_root, mode):
    """F1: a self-consistent receipt recording a rollout this pack cannot
    deliver describes an operational state that does not exist. This slice
    ships ceiling `off`, no workflows and no trusted-check wiring."""
    from app.agent_review.target_pack_validate_v2 import VALIDATE_ROLLOUT_ABOVE_CEILING_REASON_V2

    _install(target_root, receipt=_receipt(rollout_mode=mode), profile=_VALID_PROFILE_YAML)
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "rollout_ceiling").reason_code == VALIDATE_ROLLOUT_ABOVE_CEILING_REASON_V2


def test_rollout_off_is_accepted(target_root):
    _install(target_root, receipt=_receipt(rollout_mode="off"), profile=_VALID_PROFILE_YAML)
    assert _check(run_validate_v2(target_root=target_root), "rollout_ceiling").status == STATUS_PASS_V2


def test_profile_identity_must_agree_with_receipt_target_repo(target_root):
    """F2: profile identifies repository B while a self-consistent receipt
    identifies A. Every prior check passed because `_root_identity_check_v2`
    derives its expectation from the receipt's OWN target_repo -- a closed
    loop that can never disagree with itself."""
    from app.agent_review.target_pack_validate_v2 import VALIDATE_PROFILE_IDENTITY_MISMATCH_REASON_V2

    foreign = _VALID_PROFILE_YAML.replace("repo: owner/repo", "repo: attacker/other")
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw)
        (p / ".aiops").mkdir()
        (p / _PROFILE_RELATIVE_PATH).write_text(foreign, encoding="utf-8")
        foreign_hash = compute_profile_hash_v2(load_target_profile_v2(str(p)))

    _install(
        target_root,
        receipt=_receipt(
            target_profile_hash=foreign_hash,
            target_owned_file_hashes={_PROFILE_RELATIVE_PATH: _sha256(foreign.encode("utf-8"))},
        ),
        profile=foreign,
    )
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "profile_identity").reason_code == VALIDATE_PROFILE_IDENTITY_MISMATCH_REASON_V2


def test_receipt_omitting_the_profile_from_target_owned_set_fails_closed(target_root):
    """F6: an empty/партial target-owned set means the drift loop iterates
    over nothing and returns pass. Because `target_profile_hash` is computed
    from PARSED canonical content, a comment-only edit preserves it -- so a
    stale receipt plus a formatting edit validates clean."""
    from app.agent_review.target_pack_validate_v2 import VALIDATE_PROFILE_NOT_TARGET_OWNED_REASON_V2

    _install(
        target_root,
        receipt=_receipt(target_owned_file_hashes={}, target_owned_paths=()),
        profile=_VALID_PROFILE_YAML,
    )
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "target_owned").reason_code == VALIDATE_PROFILE_NOT_TARGET_OWNED_REASON_V2


def test_comment_only_profile_edit_is_still_caught_as_drift(target_root):
    """The concrete consequence of F6: a comment-only edit preserves the
    semantic profile hash, so byte-level drift detection is the only thing
    that can catch it."""
    _install(target_root, receipt=_receipt(), profile=_VALID_PROFILE_YAML)
    (target_root / _PROFILE_RELATIVE_PATH).write_text(
        _VALID_PROFILE_YAML + "\n# comment-only edit\n", encoding="utf-8"
    )
    report = run_validate_v2(target_root=target_root)
    assert report.is_valid is False
    assert _check(report, "profile_hash").status == STATUS_PASS_V2  # semantic hash unchanged ...
    assert _check(report, "target_owned").reason_code == VALIDATE_TARGET_OWNED_DRIFT_REASON_V2  # ... bytes caught it


def test_uncustomized_seed_identity_placeholder_is_not_a_mismatch(target_root):
    """The shipped seed carries a visible `OWNER/REPO` marker instead of a
    real identity, so no target-specific material is baked into the generic
    artifact. A freshly initialised target legitimately still has it, and
    that must not be reported as a foreign identity -- the same allowance
    the canonical operation writer makes, imported rather than restated."""
    from app.agent_review.target_pack_operation_v2 import SEED_PROFILE_IDENTITY_PLACEHOLDER_V2

    seed = _VALID_PROFILE_YAML.replace("repo: owner/repo", f"repo: {SEED_PROFILE_IDENTITY_PLACEHOLDER_V2}")
    with tempfile.TemporaryDirectory() as raw:
        p = Path(raw)
        (p / ".aiops").mkdir()
        (p / _PROFILE_RELATIVE_PATH).write_text(seed, encoding="utf-8")
        seed_hash = compute_profile_hash_v2(load_target_profile_v2(str(p)))

    _install(
        target_root,
        receipt=_receipt(
            target_profile_hash=seed_hash,
            target_owned_file_hashes={_PROFILE_RELATIVE_PATH: _sha256(seed.encode("utf-8"))},
        ),
        profile=seed,
    )
    report = run_validate_v2(target_root=target_root)
    assert _check(report, "profile_identity").status == STATUS_PASS_V2
    assert report.is_valid is True
