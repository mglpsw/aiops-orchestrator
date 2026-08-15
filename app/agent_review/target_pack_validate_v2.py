"""`agent-review target validate` -- target-only, read-only validation of an
installed AgentReview v2 target pack (#203-S2).

## Why this is not `doctor`

`doctor` (`target_pack_doctor_v2`) answers *"does this install match THE
PACK it claims to come from?"*. It needs `--toolrepo-root`/`--pack-version`
because it rebuilds the upstream `TargetPackManifestV2` and cross-checks the
receipt against it.

`validate` answers a different question: *"is this target's own installed
state internally coherent, undrifted and contained?"*. It takes ONLY
`--target-root`, deliberately, so a consumer repository can run it in its
own CI without a toolrepo checkout, a network call, or any upstream
artifact. Everything it checks is derivable from what the target already
has on disk:

```text
receipt parses strictly (which the contract's own validator makes
  equivalent to "receipt_hash still describes these bytes")
profile parses strictly
receipt.target_profile_hash          vs the profile actually on disk
receipt.portable_target_root_identity vs the receipt's own target_repo
receipt.target_owned_file_hashes      vs the bytes actually on disk
every declared path stays inside target_root
```

The two are complementary, not redundant: a target can pass `validate`
(internally coherent) while failing `doctor` (coherent, but not the pack
version it claims). Neither subsumes the other.

## Read-only by construction

This module writes nothing, creates nothing and removes nothing. It calls
no write/mkdir/rename/remove primitive anywhere in its call graph, proven
mechanically by the AST/call-graph test in `test_target_pack_arch_v2.py`
-- the same discipline already applied to `doctor`. A `target_root` that
does not exist stays nonexistent after a refusal.

## Capability honesty (#203-S2)

This slice ships no `TrustedCheckInventoryV2`, no trusted-check seed
template, and no workflow installation; `max_supported_rollout_mode`
remains `off`. The spec's §7 end-state has `validate` cross-checking a
target's trusted-check inventory against
`TargetProfileV2.policies.required_checks` -- but §14 of the same spec
defers that contract, and the pack currently generates exactly one file
(`.aiops/target-profile.v2.yaml`). Validating an inventory the pack never
installs, and for which no contract exists, would be validating nothing.

So that dimension is reported as `unavailable`, carrying a reason code
that names the deferral, and is listed in
`ValidateReportV2.unvalidated_capabilities`. It is never reported as a
pass and never silently omitted:

    absence of a delivered capability
      != successful validation of that capability

`is_valid` therefore means "no applicable check failed", NOT "everything
the final architecture will check has been checked". A consumer that
needs the stronger statement must read `unvalidated_capabilities`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.agent_review.contracts_v2 import TargetProfileV2
from app.common.strict_json import strict_json_loads
from app.agent_review.profile_loader_v2 import (
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_text_v2,
)
from app.agent_review.target_pack_operation_v2 import SEED_PROFILE_IDENTITY_PLACEHOLDER_V2
from app.agent_review.target_pack_plan_v2 import (
    PLAN_PATH_RESOLUTION_FAILED_REASON_V2,
    PlanError,
    resolve_within_target_root_v2,
)
from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_RELATIVE_PATH_V2,
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
)

# Status vocabulary. `unavailable` is the project's existing term for "this
# dimension could not be established" (`trusted_check_authority_v2.
# STATE_UNAVAILABLE_V2`); it is reused here rather than inventing a new
# word, and it is deliberately distinct from both pass and fail.
STATUS_PASS_V2 = "pass"
STATUS_FAIL_V2 = "fail"
STATUS_UNAVAILABLE_V2 = "unavailable"

VALIDATE_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 = "target_pack_validate_target_root_not_a_directory"
VALIDATE_RECEIPT_MISSING_REASON_V2 = "target_pack_validate_receipt_missing"
VALIDATE_RECEIPT_INVALID_REASON_V2 = "target_pack_validate_receipt_invalid"
VALIDATE_PROFILE_MISSING_REASON_V2 = "target_pack_validate_profile_missing"
VALIDATE_PROFILE_INVALID_REASON_V2 = "target_pack_validate_profile_invalid"
VALIDATE_PROFILE_HASH_MISMATCH_REASON_V2 = "target_pack_validate_profile_hash_mismatch"
VALIDATE_ROOT_IDENTITY_MISMATCH_REASON_V2 = "target_pack_validate_root_identity_mismatch"
VALIDATE_TARGET_OWNED_MISSING_REASON_V2 = "target_pack_validate_target_owned_missing"
VALIDATE_TARGET_OWNED_DRIFT_REASON_V2 = "target_pack_validate_target_owned_drift"
VALIDATE_PATH_ESCAPES_TARGET_ROOT_REASON_V2 = "target_pack_validate_path_escapes_target_root"
VALIDATE_PATH_RESOLUTION_FAILED_REASON_V2 = "target_pack_validate_path_resolution_failed"
VALIDATE_TRUSTED_CHECK_INVENTORY_DEFERRED_REASON_V2 = (
    "target_pack_validate_trusted_check_inventory_deferred_until_inventory_slice"
)
VALIDATE_ROLLOUT_ABOVE_CEILING_REASON_V2 = "target_pack_validate_rollout_above_pack_ceiling"
VALIDATE_PROFILE_IDENTITY_MISMATCH_REASON_V2 = "target_pack_validate_profile_identity_mismatch"
VALIDATE_PROFILE_NOT_TARGET_OWNED_REASON_V2 = "target_pack_validate_profile_not_in_target_owned_set"
VALIDATE_RECEIPT_MAJOR_INCOMPATIBLE_REASON_V2 = "target_pack_validate_receipt_major_incompatible"
VALIDATE_TARGET_OWNED_SET_UNEXPECTED_REASON_V2 = "target_pack_validate_target_owned_set_unexpected"
VALIDATE_GENERATED_FILES_UNSUPPORTED_REASON_V2 = "target_pack_validate_generated_files_unsupported"
VALIDATE_RECEIPT_NON_CANONICAL_CLAIM_REASON_V2 = "target_pack_validate_receipt_non_canonical_claim"
VALIDATE_TARGET_OWNED_PATHS_NON_CANONICAL_REASON_V2 = "target_pack_validate_target_owned_paths_non_canonical"

# The rollout ceiling THIS pack version can actually deliver. `validate` is
# target-only by design (no `--toolrepo-root`), so it cannot read a live
# manifest's `max_supported_rollout_mode`; it binds to the same constant the
# builder ships instead. Raising the real ceiling (S3, once workflows and
# trusted-check wiring exist) must raise this in the same change -- the
# capability and the validation of it move together, or one silently
# outruns the other.
VALIDATED_MAX_ROLLOUT_MODE_V2 = "off"

_PROFILE_RELATIVE_PATH_V2 = ".aiops/target-profile.v2.yaml"

# The complete TARGET_OWNED set this pack version delivers. A receipt may
# not declare anything else (PR #235 review round 4): the canonical
# operation planner already refuses a receipt whose target-owned set
# differs from the manifest's, and accepting extras here would both
# disagree with that writer and let receipt-authored input steer which
# files `validate` reads and hashes under the target root.
DELIVERED_TARGET_OWNED_PATHS_V2 = frozenset({_PROFILE_RELATIVE_PATH_V2})

# Check names, stable identifiers a consumer can match on.
TARGET_ROOT_CHECK_V2 = "target_root"
RECEIPT_CHECK_V2 = "receipt"
PROFILE_CHECK_V2 = "profile"
PROFILE_HASH_CHECK_V2 = "profile_hash"
PROFILE_IDENTITY_CHECK_V2 = "profile_identity"
ROOT_IDENTITY_CHECK_V2 = "root_identity"
TARGET_OWNED_CHECK_V2 = "target_owned"
ROLLOUT_CEILING_CHECK_V2 = "rollout_ceiling"
GENERATED_FILES_CHECK_V2 = "generated_files"
CANONICAL_CLAIMS_CHECK_V2 = "canonical_claims"
COMPATIBILITY_CHECK_V2 = "compatibility"
TRUSTED_CHECK_INVENTORY_CHECK_V2 = "trusted_check_inventory"


@dataclass(frozen=True)
class ValidateCheckV2:
    name: str
    status: str
    reason_code: str | None = None


@dataclass(frozen=True)
class ValidateReportV2:
    target_root: str
    checks: tuple[ValidateCheckV2, ...]

    # CLASS 4, WIDENED -- the authored identity is carried OUT of the same
    # snapshot the checks were computed from, rather than left for a
    # caller to go read for itself.
    #
    # Round 7 showed the earlier statement of the rule ("read both
    # installation artifacts through one snapshot") was scoped one level
    # too narrow: it governed reads INSIDE this function while the new
    # Class 3 distinctness property reopened the receipt afterwards. A
    # receipt swapped between the two reads yields checks describing the
    # old installation and an identity from its replacement. The rule is
    # therefore: ONE SNAPSHOT PER CASE, spanning every read that feeds one
    # decision -- so identity ships in the report and no consumer has a
    # second read path to get it wrong with.
    authored_target_identity: str | None = None

    @property
    def is_valid(self) -> bool:
        """No APPLICABLE check failed.

        Deliberately not "everything was validated" -- see this module's
        docstring. An `unavailable` check neither fails nor passes; it is
        surfaced through `unvalidated_capabilities` so a caller cannot
        read a bare `True` as coverage it never got.
        """
        return not any(check.status == STATUS_FAIL_V2 for check in self.checks)

    @property
    def unvalidated_capabilities(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if check.status == STATUS_UNAVAILABLE_V2)


# CLASS 2 -- TOTAL PARSE BOUNDARY.
#
# Target-authored input must never produce a traceback for malformed
# content. Four separate rounds of this PR each added one more exception
# type after a reviewer named it (duplicate JSON keys, YAML `TypeError`,
# path `RuntimeError`, then `RecursionError`), which is the wrong shape:
# the boundary should be TOTAL by construction, not incrementally widened.
#
# Deliberately NOT `except Exception`: an internal programmer bug must not
# be relabelled as invalid target input. Only failure classes that a
# parser can legitimately raise for malformed *input* are listed, and
# filesystem/containment failures stay separate so they keep their own
# distinguishable reason codes.
PARSE_INPUT_FAILURES_V2: tuple[type[BaseException], ...] = (
    ValidationError,
    ValueError,          # includes json.JSONDecodeError and yaml.YAMLError subclasses
    UnicodeDecodeError,  # (also a ValueError, listed for intent)
    RecursionError,      # deeply nested JSON/YAML
    TypeError,           # parser handed a shape the constructor cannot use
)


def _validate_reason_for_plan_error_v2(exc: PlanError) -> str:
    """Single translation site, mirroring `target_pack_doctor_v2`'s own --
    a symlink loop and a genuine containment escape must not collapse into
    one reason code (the exact defect H1-A round 5 fixed for doctor)."""

    if exc.reason_code == PLAN_PATH_RESOLUTION_FAILED_REASON_V2:
        return VALIDATE_PATH_RESOLUTION_FAILED_REASON_V2
    return VALIDATE_PATH_ESCAPES_TARGET_ROOT_REASON_V2


# CLASS 4 -- ONE SNAPSHOT, ONE DECISION.
#
# Every validation decision about one installation must be built from a
# SINGLE observed filesystem identity. Stated here as a module invariant
# rather than re-learned per bug: this rule was rediscovered four times in
# this PR (target_root resolve, profile resolve/read, profile semantic vs
# raw bytes, conformance resolved roots) and each time it was applied only
# to the site the reviewer named.
#
# Concretely, `run_validate_v2`:
#   1. resolves `target_root` once;
#   2. resolves the contained `.aiops` directory ONCE;
#   3. derives both artifact paths from that snapshot;
#   4. reads each artifact once and reuses those bytes for every decision;
#   5. never re-resolves, and never returns to the mutable original alias.
#
# Without step 2, `.aiops` was resolved independently per artifact, so an
# in-root symlink retargeted between the two reads let the receipt come
# from directory A and the profile from directory B -- a pair that no
# single installation ever contained.
_AIOPS_DIR_RELATIVE_PATH_V2 = ".aiops"


def _resolve_aiops_dir_v2(target_root_real: Path) -> Path:
    """The single contained `.aiops` snapshot both artifacts are read
    through. Raises `PlanError` if it escapes the root."""

    return resolve_within_target_root_v2(
        target_root_real, target_root_real / _AIOPS_DIR_RELATIVE_PATH_V2
    )


def _read_contained_bytes_v2(target_root_real: Path, relative_path: str, aiops_dir: Path | None = None) -> bytes:
    """Resolve INSIDE the root and read through the resolved path, so the
    containment decision is bound to the read rather than merely preceding
    it (H1A-R1).

    When `aiops_dir` is supplied, the path is derived from that already
    resolved snapshot instead of re-resolving from the root -- see the
    ONE SNAPSHOT, ONE DECISION note above.
    """

    if aiops_dir is not None and relative_path.startswith(_AIOPS_DIR_RELATIVE_PATH_V2 + "/"):
        leaf = relative_path[len(_AIOPS_DIR_RELATIVE_PATH_V2) + 1 :]
        resolved = resolve_within_target_root_v2(target_root_real, aiops_dir / leaf)
        return resolved.read_bytes()
    resolved = resolve_within_target_root_v2(target_root_real, target_root_real / relative_path)
    return resolved.read_bytes()


# NOTE: there is deliberately no `authored_target_identity_v2(target_root)`
# helper here. It existed, and it was the Class 3/4 recurrence found in
# round 7: routing a SECOND read through this module's contained loaders
# inherited containment and the parse boundary, but not the snapshot --
# the receipt could be replaced between `run_validate_v2` and the identity
# read. Identity now ships on `ValidateReportV2`, so there is no
# target-root-taking entry point for a caller to reintroduce that gap.


def run_validate_v2(*, target_root: Path) -> ValidateReportV2:
    """Validate an installed target pack using only what the target has.

    Never raises for an invalid installation -- every failure becomes a
    typed, reason-coded check in the returned report, so a caller always
    gets a total, inspectable result rather than an exception for some
    failures and a report for others.
    """

    checks: list[ValidateCheckV2] = []

    if not target_root.is_dir():
        return ValidateReportV2(
            target_root=str(target_root),
            checks=(
                ValidateCheckV2(
                    TARGET_ROOT_CHECK_V2, STATUS_FAIL_V2, VALIDATE_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2
                ),
                _trusted_check_inventory_check_v2(),
            ),
        )

    checks.append(ValidateCheckV2(TARGET_ROOT_CHECK_V2, STATUS_PASS_V2))
    target_root_real = target_root.resolve(strict=False)

    # ONE SNAPSHOT, ONE DECISION: resolve `.aiops` exactly once and read
    # BOTH installation artifacts through it.
    try:
        aiops_dir: Path | None = _resolve_aiops_dir_v2(target_root_real)
    except PlanError as exc:
        aiops_dir = None
        checks.append(
            ValidateCheckV2(TARGET_ROOT_CHECK_V2, STATUS_FAIL_V2, _validate_reason_for_plan_error_v2(exc))
        )

    receipt, receipt_check = _load_receipt_v2(target_root_real, aiops_dir)
    checks.append(receipt_check)

    profile, profile_bytes, profile_check = _load_profile_v2(target_root_real, aiops_dir)
    checks.append(profile_check)

    if receipt is not None and profile is not None:
        profile_hash = compute_profile_hash_v2(profile)
        checks.append(
            ValidateCheckV2(PROFILE_HASH_CHECK_V2, STATUS_PASS_V2)
            if receipt.target_profile_hash == profile_hash
            else ValidateCheckV2(
                PROFILE_HASH_CHECK_V2, STATUS_FAIL_V2, VALIDATE_PROFILE_HASH_MISMATCH_REASON_V2
            )
        )
        checks.append(_profile_identity_check_v2(profile, receipt))

    if receipt is not None:
        checks.append(_root_identity_check_v2(receipt))
        checks.append(_target_owned_check_v2(target_root_real, receipt, profile_bytes, aiops_dir))
        checks.append(_rollout_ceiling_check_v2(receipt))
        checks.append(_compatibility_check_v2(receipt))
        checks.append(_canonical_claims_check_v2(receipt))

    checks.append(_trusted_check_inventory_check_v2())
    return ValidateReportV2(
        target_root=str(target_root),
        checks=tuple(checks),
        # From the receipt THIS pass parsed -- never a re-read.
        authored_target_identity=None if receipt is None else receipt.target_repo,
    )


def _trusted_check_inventory_check_v2() -> ValidateCheckV2:
    return ValidateCheckV2(
        TRUSTED_CHECK_INVENTORY_CHECK_V2,
        STATUS_UNAVAILABLE_V2,
        VALIDATE_TRUSTED_CHECK_INVENTORY_DEFERRED_REASON_V2,
    )


def _load_receipt_v2(
    target_root_real: Path, aiops_dir: Path | None
) -> tuple[TargetInstallReceiptV2 | None, ValidateCheckV2]:
    """Parse the receipt, contained.

    Note there is deliberately NO separate "receipt_hash" check here.
    `TargetInstallReceiptV2` already enforces
    `receipt_hash == compute_target_install_receipt_hash_v2(self)` in its
    own `model_validator(mode="after")`, so a receipt edited after write
    cannot parse at all -- it is refused here as
    `..._receipt_invalid`. A second, independent hash check in this module
    would be structurally unreachable, and publishing an unreachable check
    would falsely imply `validate` verifies something the contract had
    already decided before this code ever ran.
    """

    try:
        raw = _read_contained_bytes_v2(target_root_real, RECEIPT_RELATIVE_PATH_V2, aiops_dir)
    except PlanError as exc:
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, _validate_reason_for_plan_error_v2(exc))
    except FileNotFoundError:
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, VALIDATE_RECEIPT_MISSING_REASON_V2)
    except OSError:
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, VALIDATE_RECEIPT_INVALID_REASON_V2)

    try:
        # strict_json_loads FIRST (PR #235 review round 2, confirmed):
        # `model_validate_json` silently accepts whichever value its parser
        # picked for a duplicated key, so a receipt carrying two
        # `pack_version` entries validated clean and kept a self-consistent
        # hash -- the documented "parses strictly" gate was bypassed by an
        # ambiguous document. The repository's own duplicate-key/non-finite
        # rejecting primitive runs before the model ever sees the data.
        text = raw.decode("utf-8")
        # Gate only: its return value is deliberately discarded. Parsing
        # the model FROM the dict would change existing semantics (this
        # contract is strict-mode, and `model_validate_json` is what
        # coerces JSON arrays into the declared tuples), so the strict
        # parser runs purely to REJECT an ambiguous document before the
        # permissive path ever sees it.
        strict_json_loads(text)
        receipt = TargetInstallReceiptV2.model_validate_json(text)
    except PARSE_INPUT_FAILURES_V2:
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, VALIDATE_RECEIPT_INVALID_REASON_V2)

    return receipt, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_PASS_V2)


def _load_profile_v2(
    target_root_real: Path, aiops_dir: Path | None
) -> tuple[TargetProfileV2 | None, bytes | None, ValidateCheckV2]:
    """Parse the profile from the EXACT bytes read through the contained
    resolved path.

    PR #235 review round 1, confirmed: the first cut resolved the path for
    containment and then handed `target_root_real` to
    `load_target_profile_v2`, which rebuilt and re-traversed the path
    itself. A profile symlink retargeted between those two traversals was
    read from outside `target_root` -- the exact check-then-rederive flaw
    already found and fixed once on the doctor path (H1A-R1). Reading the
    bytes here and parsing them with `load_target_profile_text_v2` keeps
    the containment decision bound to the read.
    """

    try:
        raw = _read_contained_bytes_v2(target_root_real, _PROFILE_RELATIVE_PATH_V2, aiops_dir)
    except PlanError as exc:
        return None, None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, _validate_reason_for_plan_error_v2(exc))
    except FileNotFoundError:
        return None, None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, VALIDATE_PROFILE_MISSING_REASON_V2)
    except OSError:
        return None, None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, VALIDATE_PROFILE_INVALID_REASON_V2)

    try:
        profile = load_target_profile_text_v2(raw.decode("utf-8"))
    except (TargetProfileLoadErrorV2, *PARSE_INPUT_FAILURES_V2):
        return None, None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, VALIDATE_PROFILE_INVALID_REASON_V2)

    # Return the BYTES too, so every later check about the profile is made
    # against this one snapshot (PR #235 review round 4). Re-reading the
    # path for the byte-level drift check opened a TOCTOU window: a
    # receipt carrying the semantic hash of version A and the raw hash of
    # version B passed both checks if the file was swapped between the two
    # reads, even though no single on-disk profile ever matched both
    # claims. Containment being bound to the read does not help when the
    # read happens twice.
    return profile, raw, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_PASS_V2)


def _profile_identity_check_v2(profile: TargetProfileV2, receipt: TargetInstallReceiptV2) -> ValidateCheckV2:
    """The profile's own authored identity must name the same repository
    the receipt does.

    PR #235 review round 1, confirmed: without this, the identity story is
    a closed loop -- `_root_identity_check_v2` derives its expectation from
    `receipt.target_repo`, so it can never disagree with the receipt, and
    nothing compared the receipt against the profile the target actually
    authored. A profile naming repository B installed under a receipt
    naming A passed every check.
    """

    # The un-customized seed marker is NOT a mismatch: a freshly
    # initialised target legitimately still carries it. Same allowance the
    # canonical operation writer already makes
    # (`target_pack_operation_v2._profile_hash_for_bytes_v2`), imported
    # rather than restated so the two cannot drift apart.
    if profile.identity.repo in {receipt.target_repo, SEED_PROFILE_IDENTITY_PLACEHOLDER_V2}:
        return ValidateCheckV2(PROFILE_IDENTITY_CHECK_V2, STATUS_PASS_V2)
    return ValidateCheckV2(
        PROFILE_IDENTITY_CHECK_V2, STATUS_FAIL_V2, VALIDATE_PROFILE_IDENTITY_MISMATCH_REASON_V2
    )


def _rollout_ceiling_check_v2(receipt: TargetInstallReceiptV2) -> ValidateCheckV2:
    """A receipt may not record a rollout mode this pack version cannot
    deliver.

    PR #235 review round 1, confirmed: a self-consistent receipt recording
    `shadow_minimal`/`shadow_full` validated clean, even though this slice
    ships ceiling `off`, installs no workflows and wires no trusted checks
    -- validation was accepting an operational state the installed
    capability cannot provide.
    """

    if receipt.rollout_mode == VALIDATED_MAX_ROLLOUT_MODE_V2:
        return ValidateCheckV2(ROLLOUT_CEILING_CHECK_V2, STATUS_PASS_V2)
    return ValidateCheckV2(
        ROLLOUT_CEILING_CHECK_V2, STATUS_FAIL_V2, VALIDATE_ROLLOUT_ABOVE_CEILING_REASON_V2
    )


def _root_identity_check_v2(receipt: TargetInstallReceiptV2) -> ValidateCheckV2:
    expected = compute_portable_target_root_identity_v2(target_repo=receipt.target_repo)
    if receipt.portable_target_root_identity == expected:
        return ValidateCheckV2(ROOT_IDENTITY_CHECK_V2, STATUS_PASS_V2)
    return ValidateCheckV2(ROOT_IDENTITY_CHECK_V2, STATUS_FAIL_V2, VALIDATE_ROOT_IDENTITY_MISMATCH_REASON_V2)


# CLASS 1 + CLASS 5 -- CANONICAL RECEIPT CLAIMS.
#
# Derived MECHANICALLY from `target_pack_operation_v2._build_receipt_v2`,
# the only canonical writer, rather than from a hand-kept list of
# "unsupported fields". Two earlier rounds each fixed one field of this
# family in isolation (`generated_file_hashes`, then
# `target_policy_hash`/`review_pack_hashes` came back), which is why this
# is now one rule over a declared table.
#
# The rule: a field the writer PINS to a constant may only ever hold that
# constant. A receipt outside the writer's own output domain describes an
# installation this pack version cannot produce, so validating it would
# attest state that no writer ever created.
#
# Fields deliberately NOT listed:
#   pack_version / toolrepo_sha / manifest_digest / required_capabilities
#       -- writer copies them from the manifest; `validate` is target-only
#          and has no manifest to compare against (that is `doctor`'s job).
#   target_repo / portable_target_root_identity / target_profile_hash
#       -- caller/content derived, already cross-checked elsewhere here.
#   rollout_mode -- has its own ceiling check.
#   previous_install_identity -- the writer legitimately emits EITHER a
#       ref (when a previous receipt existed) or None, so both forms are
#       canonical and neither may be refused. It is NOT unconstrained,
#       though: see the relations table below.
_WRITER_PINNED_RECEIPT_FIELDS_V2: tuple[tuple[str, object], ...] = (
    ("target_policy_hash", None),
    ("review_pack_hashes", {}),
    # zero UPSTREAM_GENERATED entries exist in this pack version's
    # manifest, so the writer's comprehension always yields {}.
    ("generated_file_hashes", {}),
    ("expected_runner_labels", ()),
    ("required_secret_names", ()),
    ("compatibility", "compatible"),
)


# CLASS 1, SECOND CATEGORY -- WRITER-DERIVED RELATIONS.
#
# Round 8, Class 1 recurrence. The constant table above answers "may this
# field hold this value?", but not every field the writer emits is a
# constant: some are FUNCTIONS of other fields in the same receipt.
#
# `previous_install_identity` was excluded from the constant table for a
# reason that was correct -- the writer emits a ref OR None, so neither
# form may be refused -- and was then accepted in ANY shape, which was
# wrong for a second reason the exclusion never stated: the writer cannot
# emit an ARBITRARY ref either.
#
# `compute_target_pack_operation_plan_v2` raises
# `OPERATION_FOREIGN_IDENTITY` unless the previous receipt's install
# identity EQUALS the destination identity, and `_build_receipt_v2` then
# copies `pack_version`/`toolrepo_sha` off that same previous receipt.
# So for any receipt this pack can emit, the reference's `pack_version`
# and `toolrepo_sha` necessarily equal the current receipt's own.
#
# The ref's `receipt_hash` is deliberately NOT constrained: it belongs to
# a prior receipt that `validate` -- target-only, with no install history
# -- cannot see, so any claim about it would be unfounded.
#
# CLASS 5 belongs to this same category (`target_owned_paths` is a
# function of `target_owned_file_hashes`) and is folded in here rather
# than kept as a separate special case.
_WRITER_DERIVED_RECEIPT_RELATIONS_V2: tuple[
    tuple[str, Callable[[TargetInstallReceiptV2], bool], str], ...
] = (
    (
        "target_owned_paths == sorted(target_owned_file_hashes)",
        lambda r: tuple(r.target_owned_paths) == tuple(sorted(r.target_owned_file_hashes)),
        VALIDATE_TARGET_OWNED_PATHS_NON_CANONICAL_REASON_V2,
    ),
    (
        "previous_install_identity.pack_version == pack_version",
        lambda r: r.previous_install_identity is None
        or r.previous_install_identity.pack_version == r.pack_version,
        VALIDATE_RECEIPT_NON_CANONICAL_CLAIM_REASON_V2,
    ),
    (
        "previous_install_identity.toolrepo_sha == toolrepo_sha",
        lambda r: r.previous_install_identity is None
        or r.previous_install_identity.toolrepo_sha == r.toolrepo_sha,
        VALIDATE_RECEIPT_NON_CANONICAL_CLAIM_REASON_V2,
    ),
)


def _canonical_claims_check_v2(receipt: TargetInstallReceiptV2) -> ValidateCheckV2:
    for field_name, canonical in _WRITER_PINNED_RECEIPT_FIELDS_V2:
        observed = getattr(receipt, field_name)
        # Normalise container types before comparing: the contract stores
        # mappings/sequences in its own types, so `{} != Mapping()` would
        # otherwise fire spuriously.
        if isinstance(canonical, dict):
            matches = dict(observed) == canonical
        elif isinstance(canonical, tuple):
            matches = tuple(observed) == canonical
        else:
            matches = observed == canonical
        if matches:
            continue
        # `generated_file_hashes` keeps its own established reason code --
        # it already shipped in this PR and consumers may match on it.
        reason = (
            VALIDATE_GENERATED_FILES_UNSUPPORTED_REASON_V2
            if field_name == "generated_file_hashes"
            else VALIDATE_RECEIPT_NON_CANONICAL_CLAIM_REASON_V2
        )
        return ValidateCheckV2(CANONICAL_CLAIMS_CHECK_V2, STATUS_FAIL_V2, reason)

    # Writer-derived relations, including CLASS 5's canonical FORM: the
    # writer emits `tuple(sorted(target_owned_file_hashes))`, so a tuple
    # that is unsorted, or repeats a path while the mapping holds one key,
    # is a representation the writer never produces even though its SET
    # matches.
    for _relation, holds, reason_code in _WRITER_DERIVED_RECEIPT_RELATIONS_V2:
        if not holds(receipt):
            return ValidateCheckV2(CANONICAL_CLAIMS_CHECK_V2, STATUS_FAIL_V2, reason_code)
    return ValidateCheckV2(CANONICAL_CLAIMS_CHECK_V2, STATUS_PASS_V2)


def _compatibility_check_v2(receipt: TargetInstallReceiptV2) -> ValidateCheckV2:
    """A receipt declaring itself major-incompatible must not validate.

    PR #235 review round 2, confirmed: `compatibility` exists precisely so
    an install can state it must not be used, and treating it as inert let
    `validate` exit 0 on an installation that says otherwise about itself.
    """

    if receipt.compatibility == "compatible":
        return ValidateCheckV2(COMPATIBILITY_CHECK_V2, STATUS_PASS_V2)
    return ValidateCheckV2(
        COMPATIBILITY_CHECK_V2, STATUS_FAIL_V2, VALIDATE_RECEIPT_MAJOR_INCOMPATIBLE_REASON_V2
    )


def _target_owned_check_v2(
    target_root_real: Path,
    receipt: TargetInstallReceiptV2,
    profile_bytes: bytes | None,
    aiops_dir: Path | None = None,
) -> ValidateCheckV2:
    """Every target-owned path the receipt recorded must still be present,
    contained, and byte-identical to the hash the receipt recorded.

    Deterministic order: paths are walked sorted, first failure wins, so
    two runs over the same tree always produce the same reason code.

    The profile MUST appear in the recorded set (PR #235 review round 1,
    confirmed). Without that requirement an empty or partial
    `target_owned_file_hashes` made this loop iterate over nothing and
    return `pass` -- and because `target_profile_hash` is computed from
    PARSED canonical content, a comment- or formatting-only edit preserves
    the semantic hash too, so a stale receipt plus a cosmetic edit
    validated completely clean. Byte-level drift detection is the only
    check that catches that class, so its input set cannot be optional.
    """

    if _PROFILE_RELATIVE_PATH_V2 not in receipt.target_owned_file_hashes:
        return ValidateCheckV2(
            TARGET_OWNED_CHECK_V2, STATUS_FAIL_V2, VALIDATE_PROFILE_NOT_TARGET_OWNED_REASON_V2
        )
    if set(receipt.target_owned_file_hashes) != DELIVERED_TARGET_OWNED_PATHS_V2:
        return ValidateCheckV2(
            TARGET_OWNED_CHECK_V2, STATUS_FAIL_V2, VALIDATE_TARGET_OWNED_SET_UNEXPECTED_REASON_V2
        )

    for relative_path in sorted(receipt.target_owned_file_hashes):
        if relative_path == _PROFILE_RELATIVE_PATH_V2 and profile_bytes is not None:
            # Reuse the snapshot the semantic checks were made against
            # rather than re-reading (see `_load_profile_v2`).
            if hashlib.sha256(profile_bytes).hexdigest() != receipt.target_owned_file_hashes[relative_path]:
                return ValidateCheckV2(
                    TARGET_OWNED_CHECK_V2, STATUS_FAIL_V2, VALIDATE_TARGET_OWNED_DRIFT_REASON_V2
                )
            continue
        expected = receipt.target_owned_file_hashes[relative_path]
        try:
            raw = _read_contained_bytes_v2(target_root_real, relative_path, aiops_dir)
        except PlanError as exc:
            return ValidateCheckV2(TARGET_OWNED_CHECK_V2, STATUS_FAIL_V2, _validate_reason_for_plan_error_v2(exc))
        except FileNotFoundError:
            return ValidateCheckV2(
                TARGET_OWNED_CHECK_V2, STATUS_FAIL_V2, VALIDATE_TARGET_OWNED_MISSING_REASON_V2
            )
        except OSError:
            return ValidateCheckV2(
                TARGET_OWNED_CHECK_V2, STATUS_FAIL_V2, VALIDATE_TARGET_OWNED_MISSING_REASON_V2
            )

        if hashlib.sha256(raw).hexdigest() != expected:
            return ValidateCheckV2(TARGET_OWNED_CHECK_V2, STATUS_FAIL_V2, VALIDATE_TARGET_OWNED_DRIFT_REASON_V2)

    return ValidateCheckV2(TARGET_OWNED_CHECK_V2, STATUS_PASS_V2)
