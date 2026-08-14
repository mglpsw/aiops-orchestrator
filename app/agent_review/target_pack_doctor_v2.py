"""`#203` -- read-only target diagnostics (issue #203).

`run_doctor_v2` is READ-ONLY BY CONSTRUCTION: it accepts no manifest/plan to
apply, calls no write/mkdir/rename/remove primitive anywhere in its own call
graph, and returns a structured report instead of mutating anything.
`tests/agent_review/test_target_pack_arch_v2.py::test_doctor_call_graph_never_writes`
proves this mechanically by AST/call-graph inspection -- the same
"mechanical proof, not just docstring convention" discipline `#201-C`
established for its own single-construction-site/no-except invariants.

Every check reports PRESENT/MISSING/INVALID; `run_doctor_v2` itself never
raises for a diagnosable target state -- it only raises for a genuinely
unusable `target_root` (e.g. not a directory at all), which is an input
error, not a diagnosis.

## Target identity

`target_repo` is a REQUIRED caller-supplied parameter, mirroring `init
--target-repo`. It is the independent ground truth doctor checks a
receipt's own `target_repo` claim against -- never derived from anything
already on disk, which a receipt (or a whole `.aiops/` directory) copied
from a different install could otherwise assert about itself uncontested.

## Secret handling

`_check_secret_names_v2` checks whether an expected secret NAME exists as
an environment-variable KEY. It never reads, logs, returns, or otherwise
touches the VALUE bound to that key -- `SecretNameCheckV2.declared_present`
is a plain boolean, and no code path in this module ever calls
`os.environ[name]` for its value, only `name in os.environ` for its
presence. This mirrors `TargetInstallReceiptV2`'s own "names only, never
values" discipline from the receipt contract.
"""

from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.profile_loader_v2 import (
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_v2,
)
from app.agent_review.target_pack_manifest_v2 import (
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
    compute_target_pack_manifest_digest_v2,
)
from app.agent_review.target_pack_plan_v2 import rollout_mode_exceeds_pack_capability_v2
from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_RELATIVE_PATH_V2,
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
)
from pydantic import ValidationError

DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 = "target_pack_doctor_target_root_not_a_directory"
DOCTOR_RECEIPT_TARGET_REPO_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_target_repo_mismatch"
DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_target_owned_set_mismatch"
DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_pack_version_mismatch"
DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_toolrepo_sha_mismatch"
DOCTOR_RECEIPT_MANIFEST_DIGEST_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_manifest_digest_mismatch"
DOCTOR_RECEIPT_TARGET_ROOT_IDENTITY_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_target_root_identity_mismatch"
DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_profile_hash_mismatch"
DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2 = "target_owned_identity_unreconciled"
DOCTOR_RECEIPT_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2 = "target_pack_doctor_receipt_rollout_exceeds_pack_capability"


@dataclass(frozen=True)
class ProfileCheckV2:
    status: str  # "present" | "missing" | "invalid"
    profile_hash: str | None
    reason_code: str | None


@dataclass(frozen=True)
class ReceiptCheckV2:
    status: str  # "present" | "missing" | "invalid"
    receipt: TargetInstallReceiptV2 | None
    reason_code: str | None


@dataclass(frozen=True)
class SecretNameCheckV2:
    name: str
    declared_present: bool


@dataclass(frozen=True)
class DoctorReportV2:
    target_root: str
    profile: ProfileCheckV2
    receipt: ReceiptCheckV2
    secret_names: tuple[SecretNameCheckV2, ...]
    required_capabilities_declared: tuple[str, ...]

    @property
    def is_healthy(self) -> bool:
        return (
            self.profile.status == "present"
            and self.receipt.status == "present"
            and all(check.declared_present for check in self.secret_names)
        )


def _check_profile_v2(target_root: Path) -> ProfileCheckV2:
    try:
        profile = load_target_profile_v2(str(target_root))
    except TargetProfileLoadErrorV2 as exc:
        return ProfileCheckV2(status="missing", profile_hash=None, reason_code=exc.reason_code)
    except ValidationError:
        return ProfileCheckV2(status="invalid", profile_hash=None, reason_code="target_profile_invalid")
    return ProfileCheckV2(status="present", profile_hash=compute_profile_hash_v2(profile), reason_code=None)


def _check_receipt_v2(
    target_root: Path, *, manifest: TargetPackManifestV2, profile_check: ProfileCheckV2, target_repo: str
) -> ReceiptCheckV2:
    """A receipt that parses successfully is not yet a receipt that
    describes THIS install. Adversarial review finding, confirmed and
    fixed: the previous version only ever checked structural validity
    (does this JSON parse into a `TargetInstallReceiptV2`?), never whether
    the receipt's own claimed identity (`pack_version`, `toolrepo_sha`,
    `target_profile_hash`) actually matches the manifest being diagnosed
    against or the profile actually on disk. Reproduced: a receipt with a
    self-consistent `receipt_hash` but a completely unrelated
    `pack_version`/`toolrepo_sha`/`target_profile_hash` (as if copied from
    a different install, or stale from a previous pack version) reported
    `status="present"` and `is_healthy=True` -- doctor asserted an install
    was healthy while unable to say it was looking at the RIGHT install at
    all. Checked in this order (first mismatch wins, deterministic):
    target_repo, then pack_version, then toolrepo_sha, then manifest_digest,
    then portable_target_root_identity, then per-file target-owned
    reconciliation, then target_profile_hash (only when the profile itself
    loaded -- if it did not, `profile.status` already makes `is_healthy`
    false on its own, so there is nothing meaningful to compare the
    receipt's claim against), then rollout_mode against the CURRENT
    manifest's `max_supported_rollout_mode` -- a follow-on finding from the
    same review pass: a receipt can be internally consistent on the first
    three axes while still claiming an operational rollout state (e.g.
    `shadow_full`) the pack version being diagnosed against cannot deliver
    at all (e.g. stale from a since-downgraded or reverted pack version),
    then finally the target-owned SET reconciliation described below.

    Post-merge review debt (aiops-orchestrator#205, C2/C3), confirmed and
    fixed: two further axes were checked against nothing but the receipt's
    own self-reported fields. (1) `portable_target_root_identity` was
    compared against `compute_portable_target_root_identity_v2(target_repo
    =receipt.target_repo)` -- a hash of the receipt's OWN claimed
    `target_repo`, so a receipt copied wholesale from a different target
    (e.g. `.aiops/` copied between two checkouts) passed every check, since
    every field it claims is internally self-consistent by construction.
    Reproduced: copying an install's `.aiops/` directory into an unrelated
    target root reported `healthy: true`. `target_repo` is now REQUIRED
    from the caller (mirroring `init --target-repo`, the same CLI-supplied
    ground truth `init` already uses to establish what target it is
    writing to) and checked FIRST -- an independent source, not the
    receipt asserting its own identity to itself. (2) the per-file
    reconciliation loop below only ever iterated
    `receipt.target_owned_file_hashes`, so a receipt whose TARGET_OWNED set
    had been shrunk to `{}` (or to any subset smaller than the manifest's
    real TARGET_OWNED set) skipped reconciliation for every omitted path
    entirely -- a tampered TARGET_OWNED file with no corresponding receipt
    entry was never read, never hashed, never flagged. Reproduced: shrinking
    a receipt's target-owned set to empty while separately tampering the
    on-disk profile (and realigning `target_profile_hash` to the tampered
    bytes) reported `healthy: true`. The receipt's claimed set is now
    reconciled against the manifest's own TARGET_OWNED classification --
    the authority for what SHOULD be tracked, independent of anything the
    (possibly emptied) receipt claims -- so an emptied or narrowed claim
    fails closed. Checked last in the sequence (see the inline comment at
    the check site for why last does not mean weaker): the per-file loop
    is vacuously satisfied by an empty map regardless of where in the
    sequence it runs, so this check closes the gap no matter its position,
    and placing it last preserves every other check's own reason code as
    the reported cause when a receipt is ALSO wrong on an earlier axis."""

    receipt_path = target_root / RECEIPT_RELATIVE_PATH_V2
    if not receipt_path.is_file():
        return ReceiptCheckV2(status="missing", receipt=None, reason_code="target_pack_receipt_missing")
    try:
        raw = receipt_path.read_text(encoding="utf-8")
        receipt = TargetInstallReceiptV2.model_validate_json(raw)
    except (OSError, ValidationError, ValueError):
        return ReceiptCheckV2(status="invalid", receipt=None, reason_code="target_pack_receipt_invalid")

    if receipt.target_repo != target_repo:
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_TARGET_REPO_MISMATCH_REASON_V2
        )
    if receipt.pack_version != manifest.pack_version:
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2
        )
    if receipt.toolrepo_sha != manifest.toolrepo_sha:
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2
        )
    if receipt.manifest_digest != compute_target_pack_manifest_digest_v2(manifest):
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_MANIFEST_DIGEST_MISMATCH_REASON_V2
        )
    if receipt.portable_target_root_identity != compute_portable_target_root_identity_v2(target_repo=receipt.target_repo):
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_TARGET_ROOT_IDENTITY_MISMATCH_REASON_V2
        )
    for relative_path, recorded_hash in receipt.target_owned_file_hashes.items():
        observed_path = target_root / relative_path
        try:
            observed_hash = hashlib.sha256(observed_path.read_bytes()).hexdigest() if observed_path.is_file() else None
        except OSError:
            observed_hash = None
        if observed_hash != recorded_hash:
            return ReceiptCheckV2(
                status="invalid", receipt=receipt, reason_code=DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2
            )
    if profile_check.status == "present" and receipt.target_profile_hash != profile_check.profile_hash:
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2
        )
    if rollout_mode_exceeds_pack_capability_v2(
        mode=receipt.rollout_mode, max_supported=manifest.max_supported_rollout_mode
    ):
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2
        )
    # Checked last, deliberately, not because it is lower priority, but
    # because it is independent of every check above it: the per-file loop
    # above is vacuously satisfied by an EMPTY `target_owned_file_hashes`
    # (zero iterations is not zero problems), so this set-membership check
    # is what actually closes that gap, regardless of where in the sequence
    # it runs. Placing it last means a receipt that already fails an
    # earlier, more specific check (wrong pack_version, wrong toolrepo_sha,
    # ...) still reports THAT reason first, consistent with this function's
    # existing "first mismatch wins" contract.
    expected_target_owned_paths = frozenset(
        entry.path for entry in manifest.generated_files if entry.ownership is TargetPackFileOwnershipV2.TARGET_OWNED
    )
    if set(receipt.target_owned_paths) != expected_target_owned_paths:
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2
        )

    return ReceiptCheckV2(status="present", receipt=receipt, reason_code=None)


def _check_secret_names_v2(names: tuple[str, ...]) -> tuple[SecretNameCheckV2, ...]:
    # `name in os.environ` only -- never `os.environ[name]`. See module
    # docstring.
    return tuple(SecretNameCheckV2(name=name, declared_present=name in os.environ) for name in names)


def run_doctor_v2(*, target_root: Path, manifest: TargetPackManifestV2, target_repo: str) -> DoctorReportV2:
    if not target_root.is_dir():
        raise NotADirectoryError(DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2)

    profile_check = _check_profile_v2(target_root)
    receipt_check = _check_receipt_v2(
        target_root, manifest=manifest, profile_check=profile_check, target_repo=target_repo
    )
    expected_secret_names = receipt_check.receipt.required_secret_names if receipt_check.receipt else ()

    return DoctorReportV2(
        target_root=str(target_root),
        profile=profile_check,
        receipt=receipt_check,
        secret_names=_check_secret_names_v2(expected_secret_names),
        required_capabilities_declared=tuple(manifest.required_capabilities),
    )
