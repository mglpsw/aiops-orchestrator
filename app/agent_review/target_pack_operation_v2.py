"""Canonical, preview-safe operation planning for the v2 target pack.

This is the S2A identity boundary.  It deliberately decides only the small
initial-install surface already shipped by #223: an initial TARGET_OWNED seed
and the receipt that describes it.  It does not implement S2B's transaction
journal or general multi-file writer.

TARGET_OWNED bytes are never overwritten.  When valid observed bytes no
longer match the receipt, the only available action is the explicit
``RECONCILE_TARGET_OWNED_IDENTITY`` receipt transition.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, Mapping

from pydantic import Field, model_validator

from app.agent_review.contracts_v2 import ContractV2Model, GitSha, RelativePath, SafeText, Sha256
from app.agent_review.profile_loader_v2 import (
    DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_text_v2,
)
from app.agent_review.target_pack_manifest_v2 import (
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
    compute_target_pack_manifest_digest_v2,
)
from app.agent_review.target_pack_plan_v2 import InstallPlanV2, PlanError, compute_install_plan_v2
from app.agent_review.target_pack_receipt_v2 import (
    ReceiptIdentityRefV2,
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
    compute_target_install_receipt_hash_v2,
)

TARGET_PACK_OPERATION_PLAN_SCHEMA_ID_V2 = "agent-review.target-pack-operation-plan.v2"

OPERATION_FOREIGN_IDENTITY_REASON_V2 = "target_pack_operation_foreign_identity"
OPERATION_TARGET_OWNED_CHANGED_INVALID_REASON_V2 = "target_owned_changed_invalid"
OPERATION_TARGET_OWNED_MISSING_INSTALLED_REASON_V2 = "target_owned_missing_installed"
OPERATION_TARGET_OWNED_ACCEPTANCE_REQUIRED_REASON_V2 = "target_owned_identity_acceptance_required"
OPERATION_UNKNOWN_ACCEPTED_TARGET_OWNED_PATH_REASON_V2 = "target_pack_operation_unknown_accepted_target_owned_path"
OPERATION_DUPLICATE_ACCEPTED_TARGET_OWNED_PATH_REASON_V2 = "target_pack_operation_duplicate_accepted_target_owned_path"
OPERATION_PLAN_HASH_MISMATCH_REASON_V2 = "target_pack_operation_plan_hash_mismatch"


class TargetPackOperationActionTypeV2(str, Enum):
    WRITE_NEW = "WRITE_NEW"
    NOOP_UNCHANGED = "NOOP_UNCHANGED"
    RECONCILE_TARGET_OWNED_IDENTITY = "RECONCILE_TARGET_OWNED_IDENTITY"


class TargetPackInstallIdentityV2(ContractV2Model):
    """Public, portable identity used to bind a plan to one install."""

    pack_version: SafeText
    toolrepo_sha: GitSha
    manifest_digest: Sha256
    target_repo: SafeText
    portable_target_root_identity: Sha256


class TargetPackOperationActionV2(ContractV2Model):
    path: RelativePath
    ownership: Literal["target_owned"]
    action: TargetPackOperationActionTypeV2
    before_sha256: Sha256 | None = None
    after_sha256: Sha256


class TargetPackOperationPlanV2(ContractV2Model):
    """The serialisable preview contract accepted by ``--apply``.

    ``operation_plan_hash`` excludes itself and is recomputed on validation,
    so a plan copied from preview cannot be modified or replayed against a
    different source/destination identity without a typed refusal.
    """

    schema_id: Literal["agent-review.target-pack-operation-plan.v2"]
    schema_version: Literal[2]
    operation: Literal["init"]
    source_identity: TargetPackInstallIdentityV2 | None = None
    destination_identity: TargetPackInstallIdentityV2
    target_root_identity: Sha256
    actions: tuple[TargetPackOperationActionV2, ...]
    before_hashes: Mapping[SafeText, Sha256] = Field(
        default_factory=dict, json_schema_extra={"additionalProperties": False}
    )
    after_hashes: Mapping[SafeText, Sha256] = Field(
        default_factory=dict, json_schema_extra={"additionalProperties": False}
    )
    accepted_target_owned_paths: tuple[RelativePath, ...] = ()
    expected_receipt_hash: Sha256
    operation_plan_hash: Sha256

    @model_validator(mode="after")
    def validate_operation_plan_hash(self) -> TargetPackOperationPlanV2:
        if self.operation_plan_hash != compute_target_pack_operation_plan_hash_v2(self):
            raise ValueError(OPERATION_PLAN_HASH_MISMATCH_REASON_V2)
        return self


@dataclass(frozen=True)
class TargetPackOperationPlanningResultV2:
    plan: TargetPackOperationPlanV2
    install_plan: InstallPlanV2
    expected_receipt: TargetInstallReceiptV2
    should_write_receipt: bool


def _canonical_json_bytes_v2(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def compute_target_pack_operation_plan_hash_v2(plan: TargetPackOperationPlanV2) -> str:
    payload = plan.model_dump(mode="json", exclude={"operation_plan_hash"})
    return hashlib.sha256(_canonical_json_bytes_v2(payload)).hexdigest()


def _identity_from_manifest_v2(*, manifest: TargetPackManifestV2, target_repo: str) -> TargetPackInstallIdentityV2:
    root_identity = compute_portable_target_root_identity_v2(target_repo=target_repo)
    return TargetPackInstallIdentityV2(
        pack_version=manifest.pack_version,
        toolrepo_sha=manifest.toolrepo_sha,
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_repo=target_repo,
        portable_target_root_identity=root_identity,
    )


def _identity_from_receipt_v2(receipt: TargetInstallReceiptV2) -> TargetPackInstallIdentityV2:
    return TargetPackInstallIdentityV2(
        pack_version=receipt.pack_version,
        toolrepo_sha=receipt.toolrepo_sha,
        manifest_digest=receipt.manifest_digest,
        target_repo=receipt.target_repo,
        portable_target_root_identity=receipt.portable_target_root_identity,
    )


def _read_target_owned_bytes_v2(*, target_root: Path, path: str) -> bytes | None:
    candidate = target_root / path
    if candidate.is_file():
        return candidate.read_bytes()
    if candidate.exists() or candidate.is_symlink():
        raise PlanError(OPERATION_TARGET_OWNED_CHANGED_INVALID_REASON_V2)
    return None


def _profile_hash_for_bytes_v2(*, content: bytes, target_repo: str) -> str:
    try:
        profile = load_target_profile_text_v2(content.decode("utf-8"))
    except (UnicodeDecodeError, TargetProfileLoadErrorV2) as exc:
        raise PlanError(OPERATION_TARGET_OWNED_CHANGED_INVALID_REASON_V2) from exc
    # The shipped first-init seed intentionally carries this visible marker:
    # no target-specific material is baked into the generic artifact. Once a
    # target replaces it, its profile identity must bind to the CLI target
    # repository before it can be reconciled into a receipt.
    if profile.identity.repo not in {target_repo, "OWNER/REPO"}:
        raise PlanError(OPERATION_FOREIGN_IDENTITY_REASON_V2)
    return compute_profile_hash_v2(profile)


def _build_receipt_v2(
    *,
    manifest: TargetPackManifestV2,
    identity: TargetPackInstallIdentityV2,
    rollout: str,
    target_owned_file_hashes: Mapping[str, str],
    target_profile_hash: str,
    previous_receipt: TargetInstallReceiptV2 | None,
) -> TargetInstallReceiptV2:
    generated_file_hashes = {
        entry.path: entry.content_sha256
        for entry in manifest.generated_files
        if entry.ownership is TargetPackFileOwnershipV2.UPSTREAM_GENERATED
    }
    fields = {
        "schema_id": "agent-review.target-install-receipt.v2",
        "schema_version": 2,
        "pack_version": manifest.pack_version,
        "toolrepo_sha": manifest.toolrepo_sha,
        "manifest_digest": identity.manifest_digest,
        "target_repo": identity.target_repo,
        "portable_target_root_identity": identity.portable_target_root_identity,
        "target_profile_hash": target_profile_hash,
        "target_policy_hash": None,
        "review_pack_hashes": {},
        "generated_file_hashes": generated_file_hashes,
        "target_owned_file_hashes": dict(target_owned_file_hashes),
        "target_owned_paths": tuple(sorted(target_owned_file_hashes)),
        "required_capabilities": manifest.required_capabilities,
        "expected_runner_labels": (),
        "required_secret_names": (),
        "rollout_mode": rollout,
        "compatibility": "compatible",
        "previous_install_identity": (
            ReceiptIdentityRefV2(
                receipt_hash=previous_receipt.receipt_hash,
                pack_version=previous_receipt.pack_version,
                toolrepo_sha=previous_receipt.toolrepo_sha,
            )
            if previous_receipt is not None
            else None
        ),
    }
    receipt_hash = compute_target_install_receipt_hash_v2(
        TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    )
    return TargetInstallReceiptV2(**fields, receipt_hash=receipt_hash)


def compute_target_pack_operation_plan_v2(
    *,
    manifest: TargetPackManifestV2,
    target_root: Path,
    target_repo: str,
    rollout: str,
    seed_content_by_path: Mapping[str, bytes],
    previous_receipt: TargetInstallReceiptV2 | None,
    accepted_target_owned_paths: tuple[str, ...] = (),
) -> TargetPackOperationPlanningResultV2:
    """Build an ``init`` preview without writing a target file or receipt."""

    destination = _identity_from_manifest_v2(manifest=manifest, target_repo=target_repo)
    target_owned_entries = tuple(
        entry for entry in manifest.generated_files if entry.ownership is TargetPackFileOwnershipV2.TARGET_OWNED
    )
    target_owned_paths = {entry.path for entry in target_owned_entries}
    if len(accepted_target_owned_paths) != len(set(accepted_target_owned_paths)):
        raise PlanError(OPERATION_DUPLICATE_ACCEPTED_TARGET_OWNED_PATH_REASON_V2)
    accepted = tuple(sorted(set(accepted_target_owned_paths)))
    if any(path not in target_owned_paths for path in accepted):
        raise PlanError(OPERATION_UNKNOWN_ACCEPTED_TARGET_OWNED_PATH_REASON_V2)

    if previous_receipt is not None:
        source = _identity_from_receipt_v2(previous_receipt)
        if source != destination or set(previous_receipt.target_owned_paths) != target_owned_paths:
            raise PlanError(OPERATION_FOREIGN_IDENTITY_REASON_V2)
    else:
        source = None

    install_plan = compute_install_plan_v2(
        manifest=manifest, target_root=target_root, previous_receipt=previous_receipt
    )
    actions: list[TargetPackOperationActionV2] = []
    before_hashes: dict[str, str] = {}
    after_hashes: dict[str, str] = {}
    target_owned_hashes: dict[str, str] = {}
    target_profile_hash: str | None = None

    for entry in target_owned_entries:
        observed = _read_target_owned_bytes_v2(target_root=target_root, path=entry.path)
        if observed is None:
            if previous_receipt is not None:
                raise PlanError(OPERATION_TARGET_OWNED_MISSING_INSTALLED_REASON_V2)
            expected = seed_content_by_path[entry.path]
            expected_hash = hashlib.sha256(expected).hexdigest()
            actions.append(
                TargetPackOperationActionV2(
                    path=entry.path,
                    ownership="target_owned",
                    action=TargetPackOperationActionTypeV2.WRITE_NEW,
                    before_sha256=None,
                    after_sha256=expected_hash,
                )
            )
        else:
            expected = observed
            expected_hash = hashlib.sha256(observed).hexdigest()
            before_hashes[entry.path] = expected_hash
            recorded_hash = previous_receipt.target_owned_file_hashes.get(entry.path) if previous_receipt else None
            if recorded_hash != expected_hash:
                actions.append(
                    TargetPackOperationActionV2(
                        path=entry.path,
                        ownership="target_owned",
                        action=TargetPackOperationActionTypeV2.RECONCILE_TARGET_OWNED_IDENTITY,
                        before_sha256=recorded_hash,
                        after_sha256=expected_hash,
                    )
                )
            else:
                actions.append(
                    TargetPackOperationActionV2(
                        path=entry.path,
                        ownership="target_owned",
                        action=TargetPackOperationActionTypeV2.NOOP_UNCHANGED,
                        before_sha256=expected_hash,
                        after_sha256=expected_hash,
                    )
                )
        after_hashes[entry.path] = expected_hash
        target_owned_hashes[entry.path] = expected_hash
        if entry.path == str(DEFAULT_TARGET_PROFILE_RELATIVE_PATH):
            target_profile_hash = _profile_hash_for_bytes_v2(content=expected, target_repo=target_repo)

    if target_profile_hash is None:
        raise PlanError(OPERATION_TARGET_OWNED_CHANGED_INVALID_REASON_V2)

    reconciliation_paths = {
        action.path
        for action in actions
        if action.action is TargetPackOperationActionTypeV2.RECONCILE_TARGET_OWNED_IDENTITY
    }
    expected_receipt = _build_receipt_v2(
        manifest=manifest,
        identity=destination,
        rollout=rollout,
        target_owned_file_hashes=target_owned_hashes,
        target_profile_hash=target_profile_hash,
        previous_receipt=previous_receipt if reconciliation_paths or previous_receipt is None else None,
    )
    # A clean repeat-init is a true noop: it neither replaces TARGET_OWNED
    # bytes nor churns lineage by writing a structurally different receipt.
    should_write_receipt = previous_receipt is None or bool(reconciliation_paths)
    if previous_receipt is not None and not should_write_receipt:
        expected_receipt = previous_receipt

    material = {
        "schema_id": TARGET_PACK_OPERATION_PLAN_SCHEMA_ID_V2,
        "schema_version": 2,
        "operation": "init",
        "source_identity": source,
        "destination_identity": destination,
        "target_root_identity": destination.portable_target_root_identity,
        "actions": tuple(actions),
        "before_hashes": before_hashes,
        "after_hashes": after_hashes,
        "accepted_target_owned_paths": accepted,
        "expected_receipt_hash": expected_receipt.receipt_hash,
    }
    plan_hash = compute_target_pack_operation_plan_hash_v2(
        TargetPackOperationPlanV2.model_construct(**material, operation_plan_hash="0" * 64)
    )
    plan = TargetPackOperationPlanV2(**material, operation_plan_hash=plan_hash)
    return TargetPackOperationPlanningResultV2(
        plan=plan,
        install_plan=install_plan,
        expected_receipt=expected_receipt,
        should_write_receipt=should_write_receipt,
    )


def require_target_owned_reconciliation_acceptance_v2(plan: TargetPackOperationPlanV2) -> None:
    """Fail closed unless every changed TARGET_OWNED path was named."""

    accepted = set(plan.accepted_target_owned_paths)
    required = {
        action.path
        for action in plan.actions
        if action.action is TargetPackOperationActionTypeV2.RECONCILE_TARGET_OWNED_IDENTITY
    }
    if not required.issubset(accepted):
        raise PlanError(OPERATION_TARGET_OWNED_ACCEPTANCE_REQUIRED_REASON_V2)
