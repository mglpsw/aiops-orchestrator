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

## Path containment on every read

Every filesystem read this module performs -- the profile, the receipt, and
each target-owned file -- goes through `target_pack_plan_v2.resolve_within_
target_root_v2` FIRST, and reads only the resolved `Path` it returned, never
a separately re-derived one. That is the same containment primitive
(`resolve(strict=False)` on the candidate, then `relative_to` a
once-resolved root) `target_pack_install_v2` has applied to the WRITE path
since the round-4 review, so the pack has one definition of "inside
`target_root`", not two. `run_doctor_v2` resolves `target_root` exactly
once per invocation and threads that single value through every check
below it -- never re-resolved per file -- matching `apply_install_plan_v2`'s
own `target_root_real` threading. A symlink resolving back inside
`target_root` is allowed; only escape is refused, reported as
`target_pack_doctor_path_escapes_target_root`.

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
    DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
    TARGET_PROFILE_MISSING_REASON_V2,
    TARGET_PROFILE_UNREADABLE_REASON_V2,
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_text_v2,
)
from app.agent_review.target_pack_manifest_v2 import (
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
    compute_target_pack_manifest_digest_v2,
)
from app.agent_review.target_pack_plan_v2 import (
    PlanError,
    resolve_within_target_root_v2,
    rollout_mode_exceeds_pack_capability_v2,
)
from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_RELATIVE_PATH_V2,
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
)
from pydantic import ValidationError

DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 = "target_pack_doctor_target_root_not_a_directory"
DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2 = "target_pack_doctor_path_escapes_target_root"
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


def _check_profile_v2(target_root_real: Path) -> ProfileCheckV2:
    # Containment BOUND to the read, not merely checked before it
    # (aiops-orchestrator#205, H1A-R1, round 2). The first cut of this fix
    # verified containment and then discarded the result, letting
    # `load_target_profile_v2` independently re-derive `target_root /
    # relative_path` and re-traverse it -- the check and the read observed
    # the same symlink only by coincidence of timing, not by construction.
    # Confirmed exploitable deterministically (no real race needed):
    # swapping the symlink to point outside `target_root` immediately after
    # the passing containment check, but before the read, made this
    # function report `status="present"` with a hash computed from content
    # OUTSIDE `target_root`. Reading through `resolved` directly, and
    # parsing via `load_target_profile_text_v2` (the same "I already have
    # the bytes, do not re-derive a path" entry point `target_pack_
    # operation_v2._profile_hash_for_bytes_v2` already uses) closes that:
    # there is no second path composition left to race. `TargetProfile
    # LoadErrorV2`'s three reason codes (MISSING/UNREADABLE/INVALID) are
    # reproduced here explicitly since the loader that used to distinguish
    # them from a bare `Path` is bypassed.
    #
    # Round 5 (Codex shadow review of #230 at fbc67db), confirmed and fixed:
    # composed from `target_root / DEFAULT_TARGET_PROFILE_RELATIVE_PATH` --
    # the CALLER's mutable alias -- rather than from `target_root_real`, the
    # already-resolved root every other part of round 3/4's fix is bound to.
    # If `target_root` were retargeted to a DESCENDANT of the resolved root
    # between `run_doctor_v2` capturing `target_root_real` and this call,
    # `relative_to(target_root_real)` still passes (the new destination is
    # still contained), but the bytes read come from the descendant, not the
    # location the operation is bound to -- the identical class of defect
    # round 3 closed in `compute_target_pack_operation_plan_v2`, reachable
    # here through a different alias. Composing from `target_root_real`
    # itself removes the second, divergeable base entirely; `target_root`
    # is no longer a parameter of this function because nothing in it needs
    # the mutable alias once the read is bound to the resolved root.
    try:
        resolved = resolve_within_target_root_v2(target_root_real, target_root_real / DEFAULT_TARGET_PROFILE_RELATIVE_PATH)
    except PlanError:
        return ProfileCheckV2(
            status="invalid", profile_hash=None, reason_code=DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
        )
    if not resolved.is_file():
        return ProfileCheckV2(status="missing", profile_hash=None, reason_code=TARGET_PROFILE_MISSING_REASON_V2)
    try:
        raw_text = resolved.read_text(encoding="utf-8")
    except OSError:
        return ProfileCheckV2(status="missing", profile_hash=None, reason_code=TARGET_PROFILE_UNREADABLE_REASON_V2)
    try:
        profile = load_target_profile_text_v2(raw_text)
    except TargetProfileLoadErrorV2 as exc:
        return ProfileCheckV2(status="missing", profile_hash=None, reason_code=exc.reason_code)
    except ValidationError:
        return ProfileCheckV2(status="invalid", profile_hash=None, reason_code="target_profile_invalid")
    return ProfileCheckV2(status="present", profile_hash=compute_profile_hash_v2(profile), reason_code=None)


def _check_receipt_v2(
    *,
    target_root_real: Path,
    manifest: TargetPackManifestV2,
    profile_check: ProfileCheckV2,
    target_repo: str,
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
    then portable_target_root_identity, then the target-owned SET
    reconciliation described below, then per-file target-owned
    reconciliation, then target_profile_hash (only when the profile itself
    loaded -- if it did not, `profile.status` already makes `is_healthy`
    false on its own, so there is nothing meaningful to compare the
    receipt's claim against), then rollout_mode against the CURRENT
    manifest's `max_supported_rollout_mode` -- a follow-on finding from the
    same review pass: a receipt can be internally consistent on the first
    three axes while still claiming an operational rollout state (e.g.
    `shadow_full`) the pack version being diagnosed against cannot deliver
    at all (e.g. stale from a since-downgraded or reverted pack version).

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
    fails closed.

    Round 5 (Codex shadow review of #230 at fbc67db), confirmed and fixed:
    the SET check used to run LAST, after the per-file loop -- deliberately,
    so a receipt already wrong on an earlier axis kept reporting that axis's
    reason code. But the per-file loop reads and hashes every path the
    receipt claims BEFORE the set check ever runs, including a path the set
    check will go on to reject as not `TARGET_OWNED` at all. A self-hash-
    consistent receipt could therefore make this loop `read_bytes()` an
    arbitrary regular file inside `target_root` -- and pay the cost of
    hashing it, unbounded by size -- for a path already knowable as invalid
    by a single O(1) set comparison. Moved the SET check to run BEFORE the
    per-file loop instead: it still follows every check above it (target_
    repo, pack_version, toolrepo_sha, manifest_digest, portable_target_root
    _identity), so a receipt wrong on one of THOSE axes still reports that
    axis first, and every existing test that reaches deeper into this
    function already uses a receipt whose target-owned set matches the
    manifest -- moving the check earlier changes nothing about what those
    tests observe, only how early a receipt-declared file the set check
    would reject anyway stops being read at all."""

    try:
        receipt_path = resolve_within_target_root_v2(target_root_real, target_root_real / RECEIPT_RELATIVE_PATH_V2)
    except PlanError:
        return ReceiptCheckV2(status="invalid", receipt=None, reason_code=DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2)
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
    # Checked BEFORE the per-file loop below, not after (round 5, see this
    # function's own docstring): the loop reads and hashes every path the
    # receipt claims, so a set mismatch caught only afterward means an
    # already-known-invalid, receipt-declared path was read regardless.
    expected_target_owned_paths = frozenset(
        entry.path for entry in manifest.generated_files if entry.ownership is TargetPackFileOwnershipV2.TARGET_OWNED
    )
    if set(receipt.target_owned_paths) != expected_target_owned_paths:
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2
        )
    for relative_path, recorded_hash in receipt.target_owned_file_hashes.items():
        # Containment before the read (aiops-orchestrator#205, H1A-R1). The
        # `RelativePath` retype earlier in this PR closed the LEXICAL escape
        # (`../`, absolute, drive-absolute); this closes the SYMLINK escape,
        # which no string-level validation can reach. Composed from
        # `target_root_real`, not the caller's mutable `target_root` alias
        # (round 5) -- see `_check_profile_v2`'s docstring for why the
        # distinction matters.
        try:
            observed_path = resolve_within_target_root_v2(target_root_real, target_root_real / relative_path)
        except PlanError:
            return ReceiptCheckV2(
                status="invalid", receipt=receipt, reason_code=DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
            )
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

    return ReceiptCheckV2(status="present", receipt=receipt, reason_code=None)


def _check_secret_names_v2(names: tuple[str, ...]) -> tuple[SecretNameCheckV2, ...]:
    # `name in os.environ` only -- never `os.environ[name]`. See module
    # docstring.
    return tuple(SecretNameCheckV2(name=name, declared_present=name in os.environ) for name in names)


def run_doctor_v2(*, target_root: Path, manifest: TargetPackManifestV2, target_repo: str) -> DoctorReportV2:
    if not target_root.is_dir():
        raise NotADirectoryError(DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2)

    # Resolved exactly once per invocation and threaded through every
    # containment check below -- not re-resolved per file (round 2, see
    # `resolve_within_target_root_v2`'s own docstring).
    target_root_real = target_root.resolve(strict=False)
    profile_check = _check_profile_v2(target_root_real)
    receipt_check = _check_receipt_v2(
        target_root_real=target_root_real,
        manifest=manifest,
        profile_check=profile_check,
        target_repo=target_repo,
    )
    expected_secret_names = receipt_check.receipt.required_secret_names if receipt_check.receipt else ()

    return DoctorReportV2(
        target_root=str(target_root),
        profile=profile_check,
        receipt=receipt_check,
        secret_names=_check_secret_names_v2(expected_secret_names),
        required_capabilities_declared=tuple(manifest.required_capabilities),
    )
