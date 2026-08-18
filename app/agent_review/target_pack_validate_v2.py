"""`#203-S2` PR-B -- offline, read-only, target-only validation of an
installed AgentReview v2 target pack (issue #203, child of tracker #199).

`run_validate_v2` answers a narrower question than `doctor`:

    of the relations for which THIS command holds local, independent
    evidence -- derived only from the state actually installed under
    `target_root` and from contracts this pack already owns -- is any
    violated?

It does NOT answer "does this target correspond to the upstream pack" --
that is `doctor`'s charter (`doctor` takes `--toolrepo-root`/`--target-repo`
and rebuilds the manifest; `validate` takes neither, and needs no toolrepo
checkout at all). Absence of evidence is reported as `unavailable`, never
silently filled with a locally-invented rule -- the defect an earlier,
closed-unmerged attempt at this command (PR #235) repeatedly reintroduced
by hardcoding local constants that answered upstream questions from a
target-only tool.

## Design invariants

- READ-ONLY BY CONSTRUCTION, proven mechanically alongside `target_pack_
  doctor_v2` by `tests/agent_review/test_target_pack_arch_v2.py`.
- ONE CAPTURED ROOT, ONE `.aiops` ARTIFACT SNAPSHOT PER DECISION:
  `target_root` is resolved exactly once; `.aiops` is resolved exactly
  once, contained, via `resolve_within_target_root_v2`; the receipt and
  profile are each read once from that snapshot and their bytes reused
  for every decision that needs them, including the target-owned ledger
  entry that happens to name the profile itself (see `_target_owned_
  integrity_check_v2`). Ledger entries for any OTHER declared path are
  individually contained local observations anchored to the same
  `target_root_real` -- they are NOT part of one filesystem-wide
  snapshot, and this module claims no `openat`/dirfd atomicity anywhere.
- TOTAL over the enumerated target-state failure domain: every
  diagnosable target state becomes a reason-coded check, never an
  uncaught exception. Internal programmer errors remain exceptions --
  that is the entire point of never writing `except Exception`.
- HONEST about what it does not know: `unvalidated_capabilities` names
  every dimension this command cannot establish from the target alone.
  `unavailable` is never reported as `pass`, and is emitted on every
  return path, including the earliest refusals.
- DERIVES, NEVER RE-DERIVES: every parse/hash/containment decision routes
  through the pack's existing shared authority (`load_target_profile_
  text_v2`, `load_target_install_receipt_bytes_v2`, `resolve_within_
  target_root_v2`); this module owns no second definition of receipt,
  profile, or containment semantics.

## What this module deliberately does NOT check, and why

- Rollout ceiling and target-owned SET completeness: `manifest.max_
  supported_rollout_mode` and `TargetPackManifestV2.generated_files[].
  ownership` are upstream authorities this command has no manifest to
  read. `doctor` owns both, against the real manifest.
- `compatibility`: `TargetInstallReceiptV2.compatibility` admits both
  `compatible` and `major_incompatible` as structurally valid
  declarations -- no contract validator makes either one invalid, and
  the field is tied to an upgrade/downgrade-protection authority this
  slice does not deliver. Converting either value into a semantic
  verdict would be the same class of unfounded inference as the rollout
  ceiling; this command holds no authority over it.
- `previous_install_identity` compared to the CURRENT receipt: the field
  points at the PRIOR install, not a copy of the current one
  (`ReceiptIdentityRefV2`'s own docstring: what `rollback` verifies it is
  moving "away FROM"). Comparing it to the current receipt is true only
  until `upgrade` exists, at which point pack_version/toolrepo_sha
  legitimately differing IS the correct state. This command has no
  independent history store to confirm the reference points at a receipt
  that ever existed, so the dimension is reported `unavailable` rather
  than fabricating a comparison against the wrong state.
- Ordering of `target_owned_paths` relative to `target_owned_file_
  hashes`: the contract enforces SET equality only (`TargetInstallReceipt
  V2.validate_target_owned_hashes_match_declared_paths`); this module
  does not strengthen an authority it only consumes with an ordering
  rule the contract deliberately does not have.
- Whether a path declared in the receipt's target-owned ledger was
  actually supposed to be TARGET_OWNED: that classification lives solely
  in `TargetPackManifestV2.generated_files[].ownership` (spec `§8`),
  which this command does not have. `target_owned_integrity` below
  verifies only the byte claims the receipt actually makes -- it never
  means "this path is proven TARGET_OWNED."

## Stated limitations

- `root_identity` proves intra-receipt coherence (`target_repo` agrees
  with `portable_target_root_identity`), not filesystem provenance: a
  whole `.aiops/` directory copied from one repository into another,
  with receipt and profile still coherent WITH EACH OTHER, is
  undetectable from this command alone. `doctor` takes `--target-repo`
  as independent ground truth precisely because `validate` has none.
- `root_identity` assumes `root_relative_path="."` -- no receipt field
  records an alternative; a future non-root-install slice must decide
  this explicitly rather than silently keep assuming it.
- The `.aiops` snapshot is `Path`-based (`resolve(strict=False)` plus
  containment), not `openat`/directory-file-descriptor atomicity. It
  closes the inconsistent-pair class this module's tests target; it does
  not claim full filesystem-wide atomic snapshot semantics.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.profile_loader_v2 import (
    DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
    TARGET_PROFILE_UNREADABLE_REASON_V2,
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
    TargetInstallReceiptLoadErrorV2,
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
    load_target_install_receipt_bytes_v2,
)

# --- Status vocabulary ----------------------------------------------------
#
# `unavailable` is the project's existing term for "this dimension could
# not be established" (mirrors `trusted_check_authority_v2`'s own
# `STATE_UNAVAILABLE_V2` vocabulary), reused here rather than a new word,
# and deliberately distinct from both `pass` and `fail`.
STATUS_PASS_V2 = "pass"
STATUS_FAIL_V2 = "fail"
STATUS_UNAVAILABLE_V2 = "unavailable"

# --- Check-name constants: stable identifiers a consumer can match on ----
TARGET_ROOT_CHECK_V2 = "target_root"
AIOPS_SNAPSHOT_CHECK_V2 = "aiops_snapshot"
RECEIPT_CHECK_V2 = "receipt"
PROFILE_CHECK_V2 = "profile"
PROFILE_HASH_CHECK_V2 = "profile_hash"
PROFILE_IDENTITY_CHECK_V2 = "profile_identity"
ROOT_IDENTITY_CHECK_V2 = "root_identity"
TARGET_OWNED_INTEGRITY_CHECK_V2 = "target_owned_integrity"
TARGET_OWNED_SET_CHECK_V2 = "target_owned_set"
ROLLOUT_CAPABILITY_CHECK_V2 = "rollout_capability"
PREVIOUS_INSTALL_LINEAGE_CHECK_V2 = "previous_install_lineage"
TRUSTED_CHECK_INVENTORY_CHECK_V2 = "trusted_check_inventory"

# The determinism contract: every return path emits a duplicate-free
# SUBSEQUENCE of this tuple, never a permutation of it and never a
# duplicate name. See `_collect_checks_v2`'s own skip semantics.
VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = (
    TARGET_ROOT_CHECK_V2,
    AIOPS_SNAPSHOT_CHECK_V2,
    RECEIPT_CHECK_V2,
    PROFILE_CHECK_V2,
    PROFILE_HASH_CHECK_V2,
    PROFILE_IDENTITY_CHECK_V2,
    ROOT_IDENTITY_CHECK_V2,
    TARGET_OWNED_INTEGRITY_CHECK_V2,
    TARGET_OWNED_SET_CHECK_V2,
    ROLLOUT_CAPABILITY_CHECK_V2,
    PREVIOUS_INSTALL_LINEAGE_CHECK_V2,
    TRUSTED_CHECK_INVENTORY_CHECK_V2,
)

# --- Reason codes ----------------------------------------------------------
#
# Every reason code THIS module emits is defined here and begins
# `target_pack_validate_`, with no exception -- an earlier defect (in the
# closed-unmerged PR #235 this command's design was re-derived from)
# leaked `target_pack_plan_path_resolution_failed` on one return path,
# giving one conceptual failure two different names depending on where it
# was detected. `_path_reason_for_plan_error_v2` is the single translation
# site that keeps this true.
TARGET_ROOT_UNRESOLVABLE_REASON_V2 = "target_pack_validate_target_root_unresolvable"
TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 = "target_pack_validate_target_root_not_a_directory"
PATH_ESCAPES_TARGET_ROOT_REASON_V2 = "target_pack_validate_path_escapes_target_root"
PATH_RESOLUTION_FAILED_REASON_V2 = "target_pack_validate_path_resolution_failed"
RECEIPT_MISSING_REASON_V2 = "target_pack_validate_receipt_missing"
RECEIPT_UNREADABLE_REASON_V2 = "target_pack_validate_receipt_unreadable"
RECEIPT_INVALID_REASON_V2 = "target_pack_validate_receipt_invalid"
PROFILE_MISSING_REASON_V2 = "target_pack_validate_profile_missing"
PROFILE_UNREADABLE_REASON_V2 = "target_pack_validate_profile_unreadable"
PROFILE_INVALID_REASON_V2 = "target_pack_validate_profile_invalid"
PROFILE_HASH_MISMATCH_REASON_V2 = "target_pack_validate_profile_hash_mismatch"
PROFILE_IDENTITY_MISMATCH_REASON_V2 = "target_pack_validate_profile_identity_mismatch"
ROOT_IDENTITY_MISMATCH_REASON_V2 = "target_pack_validate_root_identity_mismatch"
TARGET_OWNED_MISSING_REASON_V2 = "target_pack_validate_target_owned_missing"
TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2 = "target_pack_validate_target_owned_not_a_regular_file"
TARGET_OWNED_UNREADABLE_REASON_V2 = "target_pack_validate_target_owned_unreadable"
TARGET_OWNED_DRIFT_REASON_V2 = "target_pack_validate_target_owned_drift"

# `unavailable` reason codes deliberately NAME THE OWNER of the question:
# if a `fail` code would need a suffix like this to stay honest, that
# check does not belong in this module.
TARGET_OWNED_SET_REASON_V2 = "target_pack_validate_target_owned_set_requires_the_upstream_manifest"
ROLLOUT_CAPABILITY_REASON_V2 = "target_pack_validate_rollout_capability_requires_the_upstream_manifest"
PREVIOUS_INSTALL_LINEAGE_REASON_V2 = (
    "target_pack_validate_previous_install_lineage_not_verifiable_from_current_target_state"
)
TRUSTED_CHECK_INVENTORY_REASON_V2 = "target_pack_validate_trusted_check_inventory_requires_an_unshipped_contract"

UNVALIDATED_CAPABILITIES_V2: tuple[tuple[str, str], ...] = (
    (TARGET_OWNED_SET_CHECK_V2, TARGET_OWNED_SET_REASON_V2),
    (ROLLOUT_CAPABILITY_CHECK_V2, ROLLOUT_CAPABILITY_REASON_V2),
    (PREVIOUS_INSTALL_LINEAGE_CHECK_V2, PREVIOUS_INSTALL_LINEAGE_REASON_V2),
    (TRUSTED_CHECK_INVENTORY_CHECK_V2, TRUSTED_CHECK_INVENTORY_REASON_V2),
)

# The two `.aiops` artifacts, derived from the shared authorities' own
# path constants -- never a second, independently-spelled copy of
# ".aiops/target-profile.v2.yaml" or ".aiops/install-receipt.v2.json".
_AIOPS_DIR_RELATIVE_V2 = DEFAULT_TARGET_PROFILE_RELATIVE_PATH.parent
_PROFILE_FILENAME_V2 = DEFAULT_TARGET_PROFILE_RELATIVE_PATH.name
_RECEIPT_FILENAME_V2 = Path(RECEIPT_RELATIVE_PATH_V2).name
# The profile artifact's canonical RELATIVE PATH, as a plain string --
# compared against a receipt-declared ledger key BEFORE any filesystem
# resolution (see `_target_owned_integrity_check_v2`). A resolved-PATH
# comparison would have to resolve the ledger entry first, through the
# live (mutable) `.aiops` alias -- reintroducing the exact independent-
# per-artifact resolution defect this module's snapshot design exists
# to close. A pure string comparison needs no filesystem access at all,
# so it cannot be influenced by a `.aiops` retarget.
_PROFILE_RELATIVE_PATH_STR_V2 = DEFAULT_TARGET_PROFILE_RELATIVE_PATH.as_posix()


@dataclass(frozen=True)
class ValidateCheckV2:
    name: str
    status: str
    reason_code: str | None = None


@dataclass(frozen=True)
class ValidateReportV2:
    target_root_real: str | None
    checks: tuple[ValidateCheckV2, ...]

    @property
    def is_valid(self) -> bool:
        """No APPLICABLE check FAILED.

        Deliberately not "everything was validated": an `unavailable`
        check neither fails nor passes; it is surfaced through
        `unvalidated_capabilities` so a caller can never read a bare
        `True` here as coverage it never received.
        """

        return not any(check.status == STATUS_FAIL_V2 for check in self.checks)

    @property
    def unvalidated_capabilities(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if check.status == STATUS_UNAVAILABLE_V2)


def _path_reason_for_plan_error_v2(exc: PlanError) -> str:
    """Single translation site for every `PlanError` this module observes
    -- mirrors `target_pack_doctor_v2`'s own `_doctor_reason_for_plan_
    error_v2`. A genuine containment escape and an unresolvable symlink
    loop must not collapse into one reason code; every value returned
    here is one of THIS module's own `target_pack_validate_*` constants,
    never `target_pack_plan_v2`'s."""

    if exc.reason_code == PLAN_PATH_RESOLUTION_FAILED_REASON_V2:
        return PATH_RESOLUTION_FAILED_REASON_V2
    return PATH_ESCAPES_TARGET_ROOT_REASON_V2


def _resolve_aiops_dir_v2(target_root_real: Path) -> Path:
    """The single contained `.aiops` snapshot both artifacts are read
    through -- ONE SNAPSHOT PER DECISION. Resolving `.aiops` independently
    per artifact instead of once lets a symlink retargeted between two
    reads pair a receipt from one directory with a profile from another --
    a pair no single installation ever contained. Raises `PlanError` if
    `.aiops` itself escapes `target_root` or cannot be resolved (e.g. a
    symlink loop)."""

    return resolve_within_target_root_v2(target_root_real, target_root_real / _AIOPS_DIR_RELATIVE_V2)


def _resolve_artifact_path_v2(
    target_root_real: Path, aiops_dir: Path, filename: str
) -> tuple[Path | None, str | None]:
    """Derive and contain one artifact path from the ALREADY-RESOLVED
    `.aiops` snapshot -- never re-resolves `.aiops` itself, only the one
    filename component under it. Returns the resolved path, or `None`
    plus this module's own already-translated reason code."""

    try:
        return resolve_within_target_root_v2(target_root_real, aiops_dir / filename), None
    except PlanError as exc:
        return None, _path_reason_for_plan_error_v2(exc)


def _observe_artifact_bytes_v2(path: Path) -> tuple[str, bytes | None]:
    """Classify and whole-file read ONE of the two known-location `.aiops`
    artifacts (receipt, profile) -- never used for an arbitrary
    receipt-declared ledger path, which streams instead (see
    `_observe_ledger_entry_hash_v2`). Returns a status tag from
    `{"missing", "not_a_regular_file", "unreadable", "ok"}` plus the raw
    bytes when readable."""

    if not path.exists():
        return "missing", None
    if not path.is_file():
        return "not_a_regular_file", None
    try:
        return "ok", path.read_bytes()
    except OSError:
        return "unreadable", None


def _observe_ledger_entry_hash_v2(path: Path) -> tuple[str, str | None]:
    """Classify and hash ONE receipt-declared target-owned path through a
    bounded-memory streaming read, never whole-file `read_bytes()`.

    A receipt-controlled ledger may name any `RelativePath` contained in
    `target_root_real`: unlike `doctor`, this command has no manifest
    authority to reject a forged ownership set BEFORE reading it, so it
    cannot know a declared path is illegitimate before observing it.
    Materialising an arbitrarily large, receipt-named file whole into
    memory would be a local memory-exhaustion vector; this streaming read
    forecloses that without inventing a size cap or path allowlist --
    either would be a policy this command holds no authority for."""

    if not path.exists():
        return "missing", None
    if not path.is_file():
        return "not_a_regular_file", None
    try:
        with path.open("rb") as stream:
            return "ok", hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        return "unreadable", None


def _load_receipt_v2(receipt_path: Path) -> tuple[TargetInstallReceiptV2 | None, ValidateCheckV2]:
    status, raw = _observe_artifact_bytes_v2(receipt_path)
    if status in ("missing", "not_a_regular_file"):
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_MISSING_REASON_V2)
    if status == "unreadable" or raw is None:
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_UNREADABLE_REASON_V2)
    try:
        # THE shared authority -- never `model_validate_json` directly.
        receipt = load_target_install_receipt_bytes_v2(raw)
    except TargetInstallReceiptLoadErrorV2:
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_INVALID_REASON_V2)
    return receipt, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_PASS_V2, None)


def _load_profile_v2(profile_path: Path) -> tuple[TargetProfileV2 | None, bytes | None, ValidateCheckV2]:
    status, raw = _observe_artifact_bytes_v2(profile_path)
    if status in ("missing", "not_a_regular_file"):
        return None, None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_MISSING_REASON_V2)
    if status == "unreadable" or raw is None:
        return None, None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_UNREADABLE_REASON_V2)
    try:
        raw_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, raw, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_UNREADABLE_REASON_V2)
    try:
        # Read the bytes ourselves under containment, then hand TEXT to
        # the `_text` loader -- never a root path to a loader that would
        # re-traverse (a real symlink-escape bug in an earlier draft of
        # this command).
        profile = load_target_profile_text_v2(raw_text)
    except TargetProfileLoadErrorV2 as exc:
        reason = (
            PROFILE_UNREADABLE_REASON_V2
            if exc.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2
            else PROFILE_INVALID_REASON_V2
        )
        return None, raw, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, reason)
    return profile, raw, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_PASS_V2, None)


def _profile_hash_check_v2(*, receipt: TargetInstallReceiptV2, profile: TargetProfileV2) -> ValidateCheckV2:
    if receipt.target_profile_hash != compute_profile_hash_v2(profile):
        return ValidateCheckV2(PROFILE_HASH_CHECK_V2, STATUS_FAIL_V2, PROFILE_HASH_MISMATCH_REASON_V2)
    return ValidateCheckV2(PROFILE_HASH_CHECK_V2, STATUS_PASS_V2, None)


def _profile_identity_check_v2(*, receipt: TargetInstallReceiptV2, profile: TargetProfileV2) -> ValidateCheckV2:
    """The only check that breaks the receipt's identity self-loop: every
    other identity-shaped check compares the receipt against itself (see
    `_root_identity_check_v2`'s docstring). This one compares the profile
    -- an artifact authored independently of the receipt -- against the
    receipt's own `target_repo` claim."""

    if profile.identity.repo not in {receipt.target_repo, SEED_PROFILE_IDENTITY_PLACEHOLDER_V2}:
        return ValidateCheckV2(PROFILE_IDENTITY_CHECK_V2, STATUS_FAIL_V2, PROFILE_IDENTITY_MISMATCH_REASON_V2)
    return ValidateCheckV2(PROFILE_IDENTITY_CHECK_V2, STATUS_PASS_V2, None)


def _root_identity_check_v2(*, receipt: TargetInstallReceiptV2) -> ValidateCheckV2:
    """Proves intra-receipt coherence: the contract has no validator
    deriving `portable_target_root_identity` from `target_repo`, so an
    incoherent pair with a recomputed `receipt_hash` parses cleanly, and
    this check genuinely catches it. It does NOT prove this filesystem
    belongs to that `target_repo` -- see the module docstring's stated
    limitation."""

    expected = compute_portable_target_root_identity_v2(target_repo=receipt.target_repo)
    if receipt.portable_target_root_identity != expected:
        return ValidateCheckV2(ROOT_IDENTITY_CHECK_V2, STATUS_FAIL_V2, ROOT_IDENTITY_MISMATCH_REASON_V2)
    return ValidateCheckV2(ROOT_IDENTITY_CHECK_V2, STATUS_PASS_V2, None)


def _target_owned_integrity_check_v2(
    *,
    target_root_real: Path,
    receipt: TargetInstallReceiptV2,
    profile_bytes: bytes | None,
) -> ValidateCheckV2:
    """Validates only the byte claims the receipt actually makes -- see
    the module docstring's "ledger claim != ownership proof". `pass` here
    means "for each local byte-integrity claim this receipt makes, the
    contained regular file currently matches it"; it never means "this
    path is proven TARGET_OWNED". A receipt may therefore cause this
    check to inspect any contained regular file it declares -- bounded by
    containment and `RelativePath`, never by an invented size cap or
    allowlist, and the report exposes only `(name, status, reason_code)`,
    never content or paths.

    The declared relative-path STRING is compared against the profile
    artifact's own canonical relative path BEFORE any resolution is
    attempted for that entry -- resolving first and comparing RESOLVED
    paths would resolve the ledger entry through the live, mutable
    `.aiops` alias, independently of the snapshot already captured for
    the profile artifact, reintroducing exactly the per-artifact
    independent-resolution defect this module's snapshot design exists
    to close."""

    profile_hash = hashlib.sha256(profile_bytes).hexdigest() if profile_bytes is not None else None

    for relative_path, declared_hash in receipt.target_owned_file_hashes.items():
        if relative_path == _PROFILE_RELATIVE_PATH_STR_V2:
            # Reuse the hash of the bytes already captured for the
            # `.aiops` snapshot's profile artifact -- never a second,
            # independently-timed resolution/read of this same entry.
            # Without reuse, a receipt could carry the SEMANTIC hash
            # (`target_profile_hash`) of one profile content and the BYTE
            # hash of a DIFFERENT one here, each individually satisfied by
            # two reads timed to observe different bytes.
            if profile_hash is None:
                return ValidateCheckV2(
                    TARGET_OWNED_INTEGRITY_CHECK_V2, STATUS_FAIL_V2, TARGET_OWNED_MISSING_REASON_V2
                )
            observed_hash = profile_hash
        else:
            try:
                resolved = resolve_within_target_root_v2(target_root_real, target_root_real / relative_path)
            except PlanError as exc:
                return ValidateCheckV2(
                    TARGET_OWNED_INTEGRITY_CHECK_V2, STATUS_FAIL_V2, _path_reason_for_plan_error_v2(exc)
                )
            status, observed = _observe_ledger_entry_hash_v2(resolved)
            if status == "missing":
                return ValidateCheckV2(
                    TARGET_OWNED_INTEGRITY_CHECK_V2, STATUS_FAIL_V2, TARGET_OWNED_MISSING_REASON_V2
                )
            if status == "not_a_regular_file":
                return ValidateCheckV2(
                    TARGET_OWNED_INTEGRITY_CHECK_V2, STATUS_FAIL_V2, TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2
                )
            if status == "unreadable" or observed is None:
                return ValidateCheckV2(
                    TARGET_OWNED_INTEGRITY_CHECK_V2, STATUS_FAIL_V2, TARGET_OWNED_UNREADABLE_REASON_V2
                )
            observed_hash = observed

        if observed_hash != declared_hash:
            return ValidateCheckV2(TARGET_OWNED_INTEGRITY_CHECK_V2, STATUS_FAIL_V2, TARGET_OWNED_DRIFT_REASON_V2)

    return ValidateCheckV2(TARGET_OWNED_INTEGRITY_CHECK_V2, STATUS_PASS_V2, None)


def _unavailable_checks_v2() -> tuple[ValidateCheckV2, ...]:
    """The four capabilities this command always discloses it cannot
    establish -- emitted on EVERY return path, including the earliest
    refusals, so an early exit never silently drops the "we did not check
    this" disclosure."""

    return tuple(ValidateCheckV2(name, STATUS_UNAVAILABLE_V2, reason) for name, reason in UNVALIDATED_CAPABILITIES_V2)


def _collect_checks_v2(target_root: Path) -> tuple[list[ValidateCheckV2], str | None]:
    try:
        target_root_real = target_root.resolve(strict=False)
    except RuntimeError:
        # No `target_root_real` was ever established, so nothing can be
        # reported to have been evaluated against it.
        return [ValidateCheckV2(TARGET_ROOT_CHECK_V2, STATUS_FAIL_V2, TARGET_ROOT_UNRESOLVABLE_REASON_V2)], None

    # Checked AFTER resolution, not before: resolving first and checking
    # the identity actually used closes the TOCTOU window a check-then-
    # resolve order would leave between "the alias looked like a
    # directory" and "the resolved identity is what every check below is
    # bound to" -- the same class of gap `target_pack_plan_v2`'s own
    # `target_root_real` threading exists to close on the write side.
    if not target_root_real.is_dir():
        return (
            [ValidateCheckV2(TARGET_ROOT_CHECK_V2, STATUS_FAIL_V2, TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2)],
            str(target_root_real),
        )

    checks: list[ValidateCheckV2] = [ValidateCheckV2(TARGET_ROOT_CHECK_V2, STATUS_PASS_V2, None)]
    target_root_real_str = str(target_root_real)

    try:
        aiops_dir = _resolve_aiops_dir_v2(target_root_real)
    except PlanError as exc:
        checks.append(ValidateCheckV2(AIOPS_SNAPSHOT_CHECK_V2, STATUS_FAIL_V2, _path_reason_for_plan_error_v2(exc)))
        return checks, target_root_real_str
    checks.append(ValidateCheckV2(AIOPS_SNAPSHOT_CHECK_V2, STATUS_PASS_V2, None))

    receipt_path, receipt_path_reason = _resolve_artifact_path_v2(target_root_real, aiops_dir, _RECEIPT_FILENAME_V2)
    profile_path, profile_path_reason = _resolve_artifact_path_v2(target_root_real, aiops_dir, _PROFILE_FILENAME_V2)

    if receipt_path is not None:
        receipt, receipt_check = _load_receipt_v2(receipt_path)
    else:
        receipt = None
        receipt_check = ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, receipt_path_reason)
    checks.append(receipt_check)

    if profile_path is not None:
        profile, profile_bytes, profile_check = _load_profile_v2(profile_path)
    else:
        profile, profile_bytes = None, None
        profile_check = ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, profile_path_reason)
    checks.append(profile_check)

    if receipt is not None and profile is not None:
        checks.append(_profile_hash_check_v2(receipt=receipt, profile=profile))
        checks.append(_profile_identity_check_v2(receipt=receipt, profile=profile))

    if receipt is not None:
        checks.append(_root_identity_check_v2(receipt=receipt))
        checks.append(
            _target_owned_integrity_check_v2(
                target_root_real=target_root_real,
                receipt=receipt,
                profile_bytes=profile_bytes,
            )
        )

    return checks, target_root_real_str


def run_validate_v2(*, target_root: Path) -> ValidateReportV2:
    """Validate an installed target pack using only what the target has.

    Total over the enumerated target-state failure domain: every
    diagnosable target state becomes a reason-coded check in the returned
    report, never an uncaught exception -- a caller always gets a total,
    inspectable result. Internal programmer errors (a bug in this module
    itself) remain exceptions; only failures a target-authored install
    can legitimately produce are ever caught here.
    """

    checks, target_root_real = _collect_checks_v2(target_root)
    return ValidateReportV2(
        target_root_real=target_root_real,
        checks=(*checks, *_unavailable_checks_v2()),
    )
