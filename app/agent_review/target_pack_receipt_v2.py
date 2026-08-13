"""`#203` -- the target install receipt (issue #203, child of tracker #199).

`TargetInstallReceiptV2` is written to `.aiops/install-receipt.v2.json` in a
TARGET repository after every successful `init`/`install`/`upgrade`. It is
the pack's own identity contract -- deliberately NOT `RunIdentityV2`
(`contracts_v2.py`), which identifies a single *review run*, never an
*installation*. Collapsing the two would let a review-run's identity fields
(head_sha, tested_merge_sha, ...) stand in for install-state questions they
were never designed to answer, and vice versa -- exactly the "duplicated
concepts" trap `#203`'s own specification self-audit (`§11`) checked for.

## What this receipt proves, and what it does not

It proves: which pack version generated which files, at which content
hashes, against which target profile/policy, at which rollout ceiling, with
which previous-install identity to roll back to. It does NOT carry any
review/readiness authority -- `ReviewReadinessV2`/`compute_readiness_
decision_v2` remain the sole authority on review outcomes, exactly as
`#201-C`'s own emission module states for itself. A receipt is install-state
provenance, not review evidence.

## Secret discipline

`required_secret_names` carries NAMES ONLY -- `validate_no_secret_values`
below fails closed on anything value-shaped, mirroring the redaction
discipline `app.agent_review.redaction` already applies to published
artifacts elsewhere in this codebase.

`generation timestamp` is deliberately EXCLUDED from `receipt_hash`'s
preimage (see `canonical_target_install_receipt_bytes_v2`) so that two
installs of the byte-identical pack+profile+policy produce byte-identical
receipts -- the property `target_pack_plan_v2`'s idempotence tests depend
on.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, Mapping

from pydantic import Field, model_validator

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    GitSha,
    Rfc3339Timestamp,
    SafeIdentifier,
    SafeText,
    Sha256,
)

TARGET_INSTALL_RECEIPT_SCHEMA_ID_V2 = "agent-review.target-install-receipt.v2"

RECEIPT_SECRET_NAME_LOOKS_LIKE_VALUE_REASON_V2 = "target_install_receipt_secret_name_looks_like_value"
RECEIPT_HASH_MISMATCH_REASON_V2 = "target_install_receipt_hash_mismatch"

# A NAME is short, identifier-shaped. Anything long, high-entropy, or
# containing characters a real secret VALUE would (base64/hex runs well
# past identifier length, "=", "/", whitespace) is refused -- fail-closed,
# not a best-effort heuristic promoted to a security boundary on its own:
# this is defense in depth alongside "the pack never reads or writes an
# environment variable's VALUE anywhere in its own code", which is the
# real invariant, verified by the architecture test in
# `test_target_pack_receipt_arch_v2.py`.
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class ReceiptIdentityRefV2(ContractV2Model):
    """A pointer to a PRIOR `TargetInstallReceiptV2`, used by `rollback`
    (spec `§4.3`). Carries only what `rollback` needs to verify it is
    rolling back to -- and away FROM -- the state it thinks it is: the
    prior receipt's own hash, pack version, and toolrepo SHA. Never embeds
    the full prior receipt (that would make `TargetInstallReceiptV2`
    self-referentially unbounded in size across repeated upgrades)."""

    receipt_hash: Sha256
    pack_version: SafeText
    toolrepo_sha: GitSha


class TargetInstallReceiptV2(ContractV2Model):
    schema_id: Literal["agent-review.target-install-receipt.v2"]
    schema_version: Literal[2]
    pack_version: SafeText
    toolrepo_sha: GitSha
    target_repo: SafeText
    target_profile_hash: Sha256
    target_policy_hash: Sha256
    review_pack_hashes: Mapping[SafeIdentifier, Sha256] = Field(
        default_factory=dict, json_schema_extra={"additionalProperties": False}
    )
    generated_file_hashes: Mapping[SafeText, Sha256] = Field(
        default_factory=dict, json_schema_extra={"additionalProperties": False}
    )
    target_owned_paths: tuple[SafeText, ...] = ()
    required_capabilities: tuple[SafeIdentifier, ...] = ()
    expected_runner_labels: tuple[SafeIdentifier, ...] = ()
    required_secret_names: tuple[SafeIdentifier, ...] = ()
    rollout_mode: Literal["off", "shadow_minimal", "shadow_full"]
    compatibility: Literal["compatible", "major_incompatible"]
    previous_install_identity: ReceiptIdentityRefV2 | None = None
    generated_at: Rfc3339Timestamp | None = None
    receipt_hash: Sha256

    @model_validator(mode="after")
    def validate_no_secret_values(self) -> TargetInstallReceiptV2:
        for name in self.required_secret_names:
            if not _SECRET_NAME_RE.fullmatch(name):
                raise ValueError(RECEIPT_SECRET_NAME_LOOKS_LIKE_VALUE_REASON_V2)
        return self

    @model_validator(mode="after")
    def validate_receipt_hash_matches_content(self) -> TargetInstallReceiptV2:
        expected = compute_target_install_receipt_hash_v2(self)
        if self.receipt_hash != expected:
            raise ValueError(RECEIPT_HASH_MISMATCH_REASON_V2)
        return self


def _canonical_json_bytes_v2(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


_RECEIPT_HASH_EXCLUDED_FIELDS_V2 = frozenset({"receipt_hash", "generated_at"})


def canonical_target_install_receipt_bytes_v2(receipt: TargetInstallReceiptV2) -> bytes:
    """Canonical bytes for `compute_target_install_receipt_hash_v2`. Excludes
    `receipt_hash` itself (self-referential) and `generated_at` (a plain
    informational timestamp, deliberately never part of canonical identity
    -- see the module docstring's "secret discipline" section, which is
    really a broader "canonical identity discipline" also covering this
    field)."""

    payload = receipt.model_dump(mode="json", exclude=_RECEIPT_HASH_EXCLUDED_FIELDS_V2)
    return _canonical_json_bytes_v2(payload)


def compute_target_install_receipt_hash_v2(receipt: TargetInstallReceiptV2) -> str:
    return hashlib.sha256(canonical_target_install_receipt_bytes_v2(receipt)).hexdigest()
