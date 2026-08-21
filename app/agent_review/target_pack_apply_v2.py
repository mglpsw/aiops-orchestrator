"""Canonical #203 target-pack apply orchestration under one private K epoch.

This module owns the *runtime* sequencing only.  It does not publish a
mutation-strength vocabulary, journal, recovery record, or persisted epoch
identity.  The operation-plan digest remains the caller's portable
authorization identity; K only makes the target-state observation and the
subsequent mutation one cooperative interval.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError

from app.agent_review.target_pack_epoch_v2 import acquire_target_pack_epoch_v2
from app.agent_review.target_pack_install_v2 import apply_install_plan_v2, write_receipt_v2
from app.agent_review.target_pack_manifest_v2 import TargetPackManifestV2
from app.agent_review.target_pack_operation_v2 import (
    compute_target_pack_operation_plan_v2,
    require_target_owned_reconciliation_acceptance_v2,
)
from app.agent_review.target_pack_plan_v2 import PlanError, resolve_within_target_root_v2
from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_RELATIVE_PATH_V2,
    TargetInstallReceiptV2,
    load_target_install_receipt_bytes_v2,
)

TARGET_PACK_PLAN_STALE_REASON_V2 = "target_pack_plan_stale"
TARGET_PACK_PREVIOUS_RECEIPT_INVALID_REASON_V2 = "target_pack_cli_previous_receipt_invalid"


class TargetPackAuthorizedApplyErrorV2(ValueError):
    """A refusal at the apply authorization boundary."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class TargetPackAuthorizedApplyResultV2:
    """The exact operation representation applied while the EX lease lived."""

    operation_plan_hash: str
    written_paths: tuple[str, ...]


def _load_previous_receipt_under_epoch_v2(*, target_root: Path) -> TargetInstallReceiptV2 | None:
    """Read the previous declaration only after K was acquired.

    ``resolve_within_target_root_v2`` remains the containment authority.  A
    missing target/receipt is normal; a malformed or inaccessible existing
    declaration is a typed pre-mutation refusal.
    """

    try:
        target_root_real = target_root.resolve(strict=False)
        receipt_path = resolve_within_target_root_v2(target_root_real, target_root_real / RECEIPT_RELATIVE_PATH_V2)
        if not receipt_path.is_file():
            if receipt_path.exists() or receipt_path.is_symlink():
                raise TargetPackAuthorizedApplyErrorV2(TARGET_PACK_PREVIOUS_RECEIPT_INVALID_REASON_V2)
            return None
        return load_target_install_receipt_bytes_v2(receipt_path.read_bytes())
    except TargetPackAuthorizedApplyErrorV2:
        raise
    except (OSError, RuntimeError, ValueError, ValidationError, PlanError) as exc:
        raise TargetPackAuthorizedApplyErrorV2(TARGET_PACK_PREVIOUS_RECEIPT_INVALID_REASON_V2) from exc


def apply_authorized_target_pack_init_v2(
    *,
    manifest: TargetPackManifestV2,
    target_root: Path,
    target_repo: str,
    rollout: str,
    seed_content_by_path: Mapping[str, bytes],
    expected_plan_sha256: str,
    accepted_target_owned_paths: tuple[str, ...],
) -> TargetPackAuthorizedApplyResultV2:
    """Apply exactly an authorized plan, or write no target-pack content.

    The operation is deliberately recomputed only once, after K EX is held.
    Equality of the freshly computed operation identity to the caller's
    authorized identity is checked before target-prefix materialization,
    binding, file writes, or receipt persistence.  On equality, that single
    locked representation is the one passed to both writer helpers.
    """

    with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True) as lease:
        previous_receipt = _load_previous_receipt_under_epoch_v2(target_root=target_root)
        operation = compute_target_pack_operation_plan_v2(
            manifest=manifest,
            target_root=target_root,
            target_repo=target_repo,
            rollout=rollout,
            seed_content_by_path=seed_content_by_path,
            previous_receipt=previous_receipt,
            accepted_target_owned_paths=accepted_target_owned_paths,
        )
        if operation.plan.operation_plan_hash != expected_plan_sha256:
            raise TargetPackAuthorizedApplyErrorV2(TARGET_PACK_PLAN_STALE_REASON_V2)
        require_target_owned_reconciliation_acceptance_v2(operation.plan)

        with lease.materialize_and_bind_target_root_v2(target_root=target_root) as binding:
            written = apply_install_plan_v2(
                plan=operation.install_plan,
                manifest=manifest,
                seed_content_by_path=dict(seed_content_by_path),
                lease=lease,
                target_binding=binding,
            )
            written_paths = list(written)
            if operation.should_write_receipt:
                write_receipt_v2(
                    receipt=operation.expected_receipt,
                    expected_target_root_real=operation.install_plan.target_root_real,
                    lease=lease,
                    target_binding=binding,
                )
                written_paths.append(RECEIPT_RELATIVE_PATH_V2)
        return TargetPackAuthorizedApplyResultV2(
            operation_plan_hash=operation.plan.operation_plan_hash,
            written_paths=tuple(written_paths),
        )
