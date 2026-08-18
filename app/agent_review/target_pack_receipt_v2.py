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

from pydantic import Field, ValidationError, model_validator

from app.common.strict_json import strict_json_loads
from app.agent_review.contracts_v2 import (
    ContractV2Model,
    GitSha,
    RelativePath,
    Repository,
    Rfc3339Timestamp,
    SafeIdentifier,
    SafeText,
    Sha256,
)

TARGET_INSTALL_RECEIPT_SCHEMA_ID_V2 = "agent-review.target-install-receipt.v2"

# The single source of truth for where a receipt lives in a target
# repository -- `target_pack_install_v2.write_receipt_v2` (the only
# sanctioned writer) and `target_pack_doctor_v2.run_doctor_v2` (read-only)
# both import this constant from here rather than each defining their own
# copy, so the two can never silently drift apart.
RECEIPT_RELATIVE_PATH_V2 = ".aiops/install-receipt.v2.json"

RECEIPT_SECRET_NAME_LOOKS_LIKE_VALUE_REASON_V2 = "target_install_receipt_secret_name_looks_like_value"
RECEIPT_HASH_MISMATCH_REASON_V2 = "target_install_receipt_hash_mismatch"
RECEIPT_TARGET_OWNED_PATHS_MISMATCH_REASON_V2 = "target_install_receipt_target_owned_paths_mismatch"
# PR-C1: `target_owned_file_hashes` and `generated_file_hashes` are two
# independent ownership ledgers over the SAME path space (`RelativePath`,
# both fields, since this PR). `TargetPackManifestV2.generated_files`
# permits each generated path exactly one ownership classification, so a
# receipt claiming the same path in both ledgers cannot represent any
# legitimate installation -- reproduced against PR #242 (Codex Round 3,
# R3-4): the same path with the same digest in both ledgers passed both
# independent integrity verifiers. The invariant belongs here, once, so
# every reader (not just one command) inherits it.
RECEIPT_OWNERSHIP_LEDGERS_OVERLAP_REASON_V2 = "target_install_receipt_ownership_ledgers_overlap"

# ONE reason code, deliberately. Publishing a finer taxonomy would require
# changing both readers, which is out of scope; a typed reason no caller
# reads is a refactor, not a finding closed. The receipt is refused either
# way, fail-closed.
RECEIPT_INVALID_REASON_V2 = "target_install_receipt_invalid"


class TargetInstallReceiptLoadErrorV2(ValueError):
    """Every failure of the shared receipt-loading authority.

    A `ValueError` because that is what a typed target-authored
    parse-input failure IS -- not because the existing call sites happen to
    catch `ValueError`. Designing the hierarchy around existing `except`
    clauses is how an improvement lands invisible. Carries a stable
    `reason_code` only, never raw receipt content or a local path."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


_RECEIPT_PARSE_FAILURES_V2: tuple[type[BaseException], ...] = (
    ValidationError,
    ValueError,
    UnicodeDecodeError,
    RecursionError,
    TypeError,
)

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
    # Digest of the canonical manifest bytes, not merely the toolrepo SHA.
    # A tree can legitimately contain more than one manifest builder, so the
    # receipt binds the exact install description consumed during planning.
    manifest_digest: Sha256
    # PR-C1: was `SafeText` -- any non-empty, control-character-free,
    # non-secret-shaped string, with no `owner/name` structure required at
    # all. Reproduced against PR #242 (Codex Round 3, R3-3):
    # `target_repo="not-a-repository"` paired with the shipped seed
    # profile's `OWNER/REPO` placeholder validated end to end, because the
    # placeholder short-circuits the only independent comparison
    # (`profile_identity`) and every other identity-shaped check compares
    # the receipt against itself. `Repository` is the same `owner/name`
    # authority `doctor`'s `--target-repo` and this receipt's own
    # `compute_portable_target_root_identity_v2` conceptually already
    # assume; it was never actually enforced at the type level.
    target_repo: Repository
    # Portable identity only: repository + relative install root, never an
    # absolute workstation path. Local inode/device identity is deliberately
    # kept out of this published contract and belongs to the apply boundary.
    portable_target_root_identity: Sha256
    target_profile_hash: Sha256
    # `None` means exactly "no policy artifact exists for this install yet"
    # -- never a digest. Adversarial review finding, confirmed and fixed:
    # this field used to be a plain `Sha256`, and its only writer
    # (`_cmd_init`) filled it with `"0" * 64` because no policy artifact
    # ships in this slice at all. A syntactically valid, self-hash-
    # consistent all-zero digest is structurally indistinguishable from a
    # real policy hash to any consumer of this schema -- exactly the
    # "fabricated-but-syntactically-valid identity" class already fixed
    # for `toolrepo_sha` and `target_profile_hash` in the same review.
    # Because this contract is new and unreleased (issue #203, this PR),
    # the field itself is corrected now rather than deferred: absence is
    # represented as `None`, and a future slice that ships a real policy
    # artifact computes a genuine digest the same way `target_profile_
    # hash` already does.
    target_policy_hash: Sha256 | None = Field(
        default=None, description="sha256 of the target policy artifact, or null if none exists yet"
    )
    review_pack_hashes: Mapping[SafeIdentifier, Sha256] = Field(
        default_factory=dict, json_schema_extra={"additionalProperties": False}
    )
    # PR-C1: was `Mapping[SafeText, Sha256]` -- unlike `target_owned_file_
    # hashes` below (already `RelativePath` since aiops-orchestrator#205,
    # C1/C4), this ledger's keys were never lexically constrained to a
    # normalized, contained relative path at all. Writer-produced values
    # were always `RelativePath`-shaped already (`entry.path` from
    # `GeneratedFileEntryV2.path: RelativePath`, `target_pack_manifest_v2`)
    # -- tightening the FIELD refuses nothing any writer produces. It also
    # makes the disjointness invariant below SOUND rather than merely
    # convenient: both ledgers sharing one canonical path authority means a
    # raw-string-set intersection cannot miss two different spellings of
    # the same path (a noncanonical spelling is refused at the FIELD level
    # before the invariant ever runs).
    generated_file_hashes: Mapping[RelativePath, Sha256] = Field(
        default_factory=dict, json_schema_extra={"additionalProperties": False}
    )
    # Raw byte hashes are separate from the semantic profile/policy hashes
    # above. A formatting-only edit may preserve semantic identity but still
    # requires explicit reconciliation before a receipt can claim those bytes.
    #
    # Post-merge review debt (aiops-orchestrator#205, C1/C4), confirmed and
    # fixed: these keys/elements used to be `SafeText`, which has no
    # `Field(pattern=...)` Pydantic can turn into a JSON Schema
    # `patternProperties`/array-`items` constraint -- combined with this
    # field's own `additionalProperties: False` override, the EXPORTED
    # schema had no `properties`/`patternProperties` at all, so only `{}`
    # ever validated. A real receipt (every successful `init` records at
    # least the profile seed's hash here) could never validate against its
    # own published schema. `RelativePath` is the correct type regardless of
    # the schema defect -- these values are always `entry.path` from
    # `GeneratedFileEntryV2.path: RelativePath` (`target_pack_manifest_v2`)
    # -- and it doubles as path confinement: `_validate_relative_path`
    # rejects `../` traversal, absolute POSIX/Windows-drive paths, and
    # non-normalized spellings at parse time, before doctor's reconciliation
    # loop ever reads a byte from disk.
    target_owned_file_hashes: Mapping[RelativePath, Sha256] = Field(
        default_factory=dict, json_schema_extra={"additionalProperties": False}
    )
    target_owned_paths: tuple[RelativePath, ...] = ()
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
    def validate_target_owned_hashes_match_declared_paths(self) -> TargetInstallReceiptV2:
        if set(self.target_owned_file_hashes) != set(self.target_owned_paths):
            raise ValueError(RECEIPT_TARGET_OWNED_PATHS_MISMATCH_REASON_V2)
        return self

    @model_validator(mode="after")
    def validate_ownership_ledgers_are_disjoint(self) -> TargetInstallReceiptV2:
        """PR-C1: `TargetPackManifestV2.generated_files` classifies each
        generated path with exactly one `TargetPackFileOwnershipV2` value
        -- a receipt claiming the same path in both `target_owned_file_
        hashes` and `generated_file_hashes` cannot represent any
        legitimate installation this pack could have produced, regardless
        of whether the two declared digests happen to agree. Sound because
        both fields are `RelativePath`-keyed: there is no pair of distinct
        key spellings that denote the same path semantics past this
        validator's own field-level normalization."""

        overlap = set(self.target_owned_file_hashes) & set(self.generated_file_hashes)
        if overlap:
            raise ValueError(RECEIPT_OWNERSHIP_LEDGERS_OVERLAP_REASON_V2)
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


def load_target_install_receipt_bytes_v2(raw: bytes | str) -> TargetInstallReceiptV2:
    """THE authority for turning target-authored receipt bytes into a
    receipt. `init`, `doctor` and any future reader go through this.

    Each reader previously called `model_validate_json` directly, which
    delegates duplicate-key handling to the JSON parser and silently keeps
    the LAST occurrence. Because `receipt_hash` is computed from the PARSED
    model, a duplicated key leaves the self-hash valid -- so the same bytes
    could be refused by one reader and trusted by another while a human
    auditing the file saw a third thing.

    `strict_json_loads` is a GATE whose result is discarded: the contract
    is strict-mode and will not coerce JSON arrays into the declared
    tuples, so `model_validate_json` remains the authoritative parse.

    Known residual: those are two different JSON parsers and they do not
    accept exactly the same language (`NaN`, lone surrogates), so
    well-formed receipt bytes are currently their INTERSECTION, which is
    stated nowhere as a contract.
    """

    try:
        strict_json_loads(raw)
    except _RECEIPT_PARSE_FAILURES_V2 as exc:
        raise TargetInstallReceiptLoadErrorV2(RECEIPT_INVALID_REASON_V2) from exc
    try:
        return TargetInstallReceiptV2.model_validate_json(raw)
    except _RECEIPT_PARSE_FAILURES_V2 as exc:
        raise TargetInstallReceiptLoadErrorV2(RECEIPT_INVALID_REASON_V2) from exc


def compute_portable_target_root_identity_v2(*, target_repo: str, root_relative_path: str = ".") -> str:
    """Return the public, path-free identity of a target install root.

    The local realpath/device/inode tuple is intentionally not serialised in
    a receipt or operation plan because it would disclose a target host. The
    writer revalidates that local identity separately at apply time.
    """

    return hashlib.sha256(
        _canonical_json_bytes_v2({"target_repo": target_repo, "root_relative_path": root_relative_path})
    ).hexdigest()
