"""Authoritative diff-byte identity and manifest binding for #200-G3B.

G3 was refuted because a path-set agreement check could prove only internal
consistency: a caller could supply a truncated ``file_diffs`` view and both
sides of the check would agree about the same incomplete material.

This module separates the two trust roots instead of asking Git acquisition to
invent run context it does not receive:

* ``AcquiredDiffIdentityV2`` is owned by diff acquisition and proves the exact
  UTF-8 unified-diff bytes for ``base_sha...head_sha``.
* ``AuthoritativeDiffIdentityV2`` adds repository/tested-merge context only
  when those values are bound to a real ``ManifestV2``/``RunIdentityV2``.
* ``ManifestDiffBindingV2`` is the additive sidecar linking ``run_id`` and
  ``manifest_hash`` to that exact diff identity. ``ManifestV2`` remains
  byte-for-byte unchanged.

Authenticity is therefore byte identity, never path-set agreement.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from app.agent_review.contracts_v2 import ContractV2Model, GitSha, Repository, Sha256
from app.agent_review.diff_acquisition_v2 import (
    ParsedFileDiffV2,
    acquire_diff_v2,
    acquire_raw_diff_v2,
    correlate_raw_and_unified_v2,
    parse_raw_diff_z,
    parse_unified_diff,
)
from app.agent_review.manifest_v2 import ManifestV2

MANIFEST_DIFF_BINDING_SCHEMA_V2 = "agent-review.manifest-diff-binding.v2"

DIFF_BINDING_MANIFEST_IDENTITY_MISMATCH_REASON_V2 = "diff_binding_manifest_identity_mismatch"
DIFF_BINDING_DIFF_IDENTITY_MISMATCH_REASON_V2 = "diff_binding_diff_identity_mismatch"


class ManifestDiffBindingError(ValueError):
    """Content-free refusal for a manifest/diff authenticity mismatch."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class AcquiredDiffIdentityV2(ContractV2Model):
    """Facts that the Git acquisition authority itself can truthfully prove."""

    base_sha: GitSha
    head_sha: GitSha
    diff_sha256: Sha256


class AuthoritativeDiffIdentityV2(ContractV2Model):
    """Acquired byte identity contextualized by the manifest's run identity."""

    repository: Repository
    base_sha: GitSha
    head_sha: GitSha
    tested_merge_sha: GitSha | None
    diff_sha256: Sha256


class ManifestDiffBindingV2(ContractV2Model):
    """Additive sidecar binding one ManifestV2 to exact authoritative diff bytes."""

    schema_id: Literal["agent-review.manifest-diff-binding.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-bind-manifest-diff-v2"]
    run_id: Sha256
    manifest_hash: Sha256
    repository: Repository
    base_sha: GitSha
    head_sha: GitSha
    tested_merge_sha: GitSha | None
    authoritative_diff_sha256: Sha256


def compute_authoritative_diff_sha256_v2(diff_text: str) -> str:
    """Hash the exact UTF-8 bytes returned by the unified diff acquisition."""

    return hashlib.sha256(diff_text.encode("utf-8")).hexdigest()


def acquire_authoritative_diff_with_identity_v2(
    repo_root: Path,
    *,
    base_sha: str,
    head_sha: str,
) -> tuple[tuple[ParsedFileDiffV2, ...], AcquiredDiffIdentityV2]:
    """Acquire and correlate both Git diff views while retaining byte identity.

    This is the new G3B path. The legacy ``acquire_authoritative_diff_v2`` is
    intentionally not modified in this first additive commit; both paths use
    the same lower-level acquisition/parsing/correlation authorities. A later
    migration may make the legacy wrapper delegate once this primitive is
    independently qualified, but no caller needs to trust a second parser or
    a path-set projection to establish the digest.
    """

    diff_text = acquire_diff_v2(repo_root, base_sha=base_sha, head_sha=head_sha)
    file_diffs = parse_unified_diff(diff_text)
    raw_text = acquire_raw_diff_v2(repo_root, base_sha=base_sha, head_sha=head_sha)
    raw_records = parse_raw_diff_z(raw_text)
    correlate_raw_and_unified_v2(raw_records, file_diffs)
    identity = AcquiredDiffIdentityV2(
        base_sha=base_sha,
        head_sha=head_sha,
        diff_sha256=compute_authoritative_diff_sha256_v2(diff_text),
    )
    return file_diffs, identity


def bind_manifest_to_diff_identity_v2(
    manifest: ManifestV2,
    acquired_identity: AcquiredDiffIdentityV2,
) -> ManifestDiffBindingV2:
    """Bind a manifest's run identity to the separately acquired byte identity."""

    identity = manifest.identity
    if (
        acquired_identity.base_sha != identity.base_sha
        or acquired_identity.head_sha != identity.head_sha
    ):
        raise ManifestDiffBindingError(DIFF_BINDING_MANIFEST_IDENTITY_MISMATCH_REASON_V2)

    contextual = AuthoritativeDiffIdentityV2(
        repository=identity.repo,
        base_sha=acquired_identity.base_sha,
        head_sha=acquired_identity.head_sha,
        tested_merge_sha=identity.tested_merge_sha,
        diff_sha256=acquired_identity.diff_sha256,
    )
    return ManifestDiffBindingV2(
        schema_id=MANIFEST_DIFF_BINDING_SCHEMA_V2,
        schema_version=2,
        source="aiops-review-bind-manifest-diff-v2",
        run_id=manifest.run_id,
        manifest_hash=identity.manifest_hash,
        repository=contextual.repository,
        base_sha=contextual.base_sha,
        head_sha=contextual.head_sha,
        tested_merge_sha=contextual.tested_merge_sha,
        authoritative_diff_sha256=contextual.diff_sha256,
    )


def verify_manifest_diff_binding_v2(
    binding: ManifestDiffBindingV2,
    *,
    manifest: ManifestV2,
    diff_text: str,
) -> AuthoritativeDiffIdentityV2:
    """Verify exact diff bytes and run identity before any scope classification."""

    identity = manifest.identity
    if (
        binding.run_id != manifest.run_id
        or binding.manifest_hash != identity.manifest_hash
        or binding.repository != identity.repo
        or binding.base_sha != identity.base_sha
        or binding.head_sha != identity.head_sha
        or binding.tested_merge_sha != identity.tested_merge_sha
    ):
        raise ManifestDiffBindingError(DIFF_BINDING_MANIFEST_IDENTITY_MISMATCH_REASON_V2)

    observed = compute_authoritative_diff_sha256_v2(diff_text)
    if observed != binding.authoritative_diff_sha256:
        raise ManifestDiffBindingError(DIFF_BINDING_DIFF_IDENTITY_MISMATCH_REASON_V2)

    return AuthoritativeDiffIdentityV2(
        repository=binding.repository,
        base_sha=binding.base_sha,
        head_sha=binding.head_sha,
        tested_merge_sha=binding.tested_merge_sha,
        diff_sha256=binding.authoritative_diff_sha256,
    )
