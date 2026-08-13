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
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.profile_loader_v2 import (
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_v2,
)
from app.agent_review.target_pack_manifest_v2 import TargetPackManifestV2
from app.agent_review.target_pack_plan_v2 import rollout_mode_exceeds_pack_capability_v2
from app.agent_review.target_pack_receipt_v2 import RECEIPT_RELATIVE_PATH_V2, TargetInstallReceiptV2
from pydantic import ValidationError

DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 = "target_pack_doctor_target_root_not_a_directory"
DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_pack_version_mismatch"
DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_toolrepo_sha_mismatch"
DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_profile_hash_mismatch"
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
    target_root: Path, *, manifest: TargetPackManifestV2, profile_check: ProfileCheckV2
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
    pack_version, then toolrepo_sha, then target_profile_hash (only when
    the profile itself loaded -- if it did not, `profile.status` already
    makes `is_healthy` false on its own, so there is nothing meaningful to
    compare the receipt's claim against), then rollout_mode against the
    CURRENT manifest's `max_supported_rollout_mode` -- a follow-on finding
    from the same review pass: a receipt can be internally consistent on
    the first three axes while still claiming an operational rollout state
    (e.g. `shadow_full`) the pack version being diagnosed against cannot
    deliver at all (e.g. stale from a since-downgraded or reverted pack
    version)."""

    receipt_path = target_root / RECEIPT_RELATIVE_PATH_V2
    if not receipt_path.is_file():
        return ReceiptCheckV2(status="missing", receipt=None, reason_code="target_pack_receipt_missing")
    try:
        raw = receipt_path.read_text(encoding="utf-8")
        receipt = TargetInstallReceiptV2.model_validate_json(raw)
    except (OSError, ValidationError, ValueError):
        return ReceiptCheckV2(status="invalid", receipt=None, reason_code="target_pack_receipt_invalid")

    if receipt.pack_version != manifest.pack_version:
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2
        )
    if receipt.toolrepo_sha != manifest.toolrepo_sha:
        return ReceiptCheckV2(
            status="invalid", receipt=receipt, reason_code=DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2
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


def run_doctor_v2(*, target_root: Path, manifest: TargetPackManifestV2) -> DoctorReportV2:
    if not target_root.is_dir():
        raise NotADirectoryError(DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2)

    profile_check = _check_profile_v2(target_root)
    receipt_check = _check_receipt_v2(target_root, manifest=manifest, profile_check=profile_check)
    expected_secret_names = receipt_check.receipt.required_secret_names if receipt_check.receipt else ()

    return DoctorReportV2(
        target_root=str(target_root),
        profile=profile_check,
        receipt=receipt_check,
        secret_names=_check_secret_names_v2(expected_secret_names),
        required_capabilities_declared=tuple(manifest.required_capabilities),
    )
