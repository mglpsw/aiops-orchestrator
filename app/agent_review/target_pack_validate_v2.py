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
- A SINGLE OBSERVATION FUNCTION per artifact kind, never a bare `bytes |
  None` projection: `_observe_artifact_bytes_v2` (bounded whole-file,
  for the two known-location `.aiops` artifacts) and `_observe_ledger_
  entry_hash_v2` (bounded-memory streaming, for arbitrary receipt-
  declared paths) each return an unambiguous status tag from a CLOSED
  vocabulary (`missing`/`not_a_regular_file`/`unreadable`/`resource_
  limit_exceeded`/`ok`), and every metadata probe (`.exists()`/
  `.is_file()`/`.is_dir()`) is inside the SAME `OSError` boundary as the
  read that follows it -- a permission-denied metadata probe is a
  diagnosable target state, never an escaping exception (closes a
  finding: an earlier version of this module wrapped only the read in
  `try/except OSError`, leaving `.exists()`/`.is_file()` able to raise
  `PermissionError` straight out of `run_validate_v2`).
- CONTAINMENT IS NOT EXISTENCE: `resolve_within_target_root_v2` proves
  only that a candidate path does not escape `target_root` -- it says
  nothing about whether that path exists or what type it is. An earlier
  version of this module treated a successful containment resolution of
  `.aiops` as sufficient for `aiops_snapshot: pass`, silently extending
  containment into a directory-existence claim it never proved (closes
  a finding: a target whose `.aiops` was absent, or a plain file, still
  reported `aiops_snapshot: pass`). `aiops_snapshot` now passes only when
  the resolved candidate is observed to actually be a directory.
- BOUNDED OBSERVATION, EXPLICITLY AN OPERATIONAL LIMIT, NOT A SCHEMA
  TRUTH: the two `.aiops` artifacts (receipt, profile) are both
  target-authored, so a whole-file `read_bytes()` on either would
  materialise an arbitrarily large file the target chose to put there
  (closes a finding: a multi-hundred-megabyte receipt or profile was
  fully buffered before any parse was attempted). `_ARTIFACT_BYTE_
  LIMIT_V2` bounds this; exceeding it is `resource_limit_exceeded`, a
  distinct disposition from `invalid` -- this command is not asserting
  the schema forbids a larger document, only that it will not observe
  one past this operational boundary. Ledger entries (`target_owned_
  file_hashes`, `generated_file_hashes`) already stream in bounded
  memory regardless of size (`_observe_ledger_entry_hash_v2`); no
  additional size cap is imposed there, which would be inventing a
  policy this command holds no authority for, exactly as it already
  declines to invent a path allowlist for the same reason.
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
- A RECEIPT FIELD IS NOT ONE PROPERTY: `target_owned_file_hashes` and
  `generated_file_hashes` each bundle at least three distinguishable
  claims -- declared-entry byte integrity (locally verifiable),
  declared-SET completeness (upstream-manifest-owned), and ownership
  classification (upstream-manifest-owned). Collapsing a field into a
  single "upstream-owned, therefore blind" classification silently
  drops the locally-verifiable claim it also carries (closes a finding:
  `generated_file_hashes` entries were neither verified nor disclosed
  `unavailable` -- a receipt could declare a byte claim about an
  `UPSTREAM_GENERATED` file, that file could be deleted or tampered,
  and `is_valid` stayed `true`). `target_owned_integrity` and
  `generated_file_integrity` below verify declared-entry integrity for
  their respective ledgers, through the SAME generic per-entry verifier
  (`_verify_declared_ledger_v2`); `target_owned_set`/`generated_file_
  set` remain `unavailable` for the completeness/ownership claims this
  command cannot establish.

## What this module deliberately does NOT check, and why

- Rollout ceiling and target-owned/generated-file SET completeness or
  ownership classification: `manifest.max_supported_rollout_mode` and
  `TargetPackManifestV2.generated_files[].ownership` are upstream
  authorities this command has no manifest to read. `doctor` owns all
  of these, against the real manifest.
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
- Ordering of `target_owned_paths`/`generated_file_hashes` relative to
  their declared mappings: the contract enforces SET equality only for
  `target_owned_*` (`TargetInstallReceiptV2.validate_target_owned_
  hashes_match_declared_paths`) and imposes no ordering requirement on
  either mapping; this module does not strengthen an authority it only
  consumes with an ordering rule the contract deliberately does not
  have. This module's OWN traversal order over either mapping is sorted
  (see `_verify_declared_ledger_v2`), which is a LOCAL report-
  determinism property, not a constraint on the receipt.
- Whether a path declared in either ledger was actually supposed to
  carry that ownership classification: that classification lives solely
  in `TargetPackManifestV2.generated_files[].ownership` (spec `§8`),
  which this command does not have. `target_owned_integrity`/
  `generated_file_integrity` below verify only the byte claims the
  receipt actually makes -- they never mean "this path is proven to
  belong to this ownership class."

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
- `_ARTIFACT_BYTE_LIMIT_V2` is an operational ceiling this command
  enforces on the two `.aiops` artifacts, chosen with a wide margin over
  observed real-world receipt/profile sizes (low hundreds of bytes to a
  few kilobytes). It is not derived from, and does not claim to bound,
  any schema-declared maximum -- `TargetArtifactV2.max_bytes` and
  similar fields describe REVIEWED artifact content, a different
  concern from this command's own install-state documents.
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
GENERATED_FILE_INTEGRITY_CHECK_V2 = "generated_file_integrity"
TARGET_OWNED_SET_CHECK_V2 = "target_owned_set"
GENERATED_FILE_SET_CHECK_V2 = "generated_file_set"
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
    GENERATED_FILE_INTEGRITY_CHECK_V2,
    TARGET_OWNED_SET_CHECK_V2,
    GENERATED_FILE_SET_CHECK_V2,
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
AIOPS_MISSING_REASON_V2 = "target_pack_validate_aiops_missing"
AIOPS_NOT_A_DIRECTORY_REASON_V2 = "target_pack_validate_aiops_not_a_directory"
AIOPS_UNREADABLE_REASON_V2 = "target_pack_validate_aiops_unreadable"
RECEIPT_MISSING_REASON_V2 = "target_pack_validate_receipt_missing"
RECEIPT_UNREADABLE_REASON_V2 = "target_pack_validate_receipt_unreadable"
RECEIPT_INVALID_REASON_V2 = "target_pack_validate_receipt_invalid"
RECEIPT_RESOURCE_LIMIT_EXCEEDED_REASON_V2 = "target_pack_validate_receipt_resource_limit_exceeded"
PROFILE_MISSING_REASON_V2 = "target_pack_validate_profile_missing"
PROFILE_UNREADABLE_REASON_V2 = "target_pack_validate_profile_unreadable"
PROFILE_INVALID_REASON_V2 = "target_pack_validate_profile_invalid"
PROFILE_RESOURCE_LIMIT_EXCEEDED_REASON_V2 = "target_pack_validate_profile_resource_limit_exceeded"
PROFILE_HASH_MISMATCH_REASON_V2 = "target_pack_validate_profile_hash_mismatch"
PROFILE_IDENTITY_MISMATCH_REASON_V2 = "target_pack_validate_profile_identity_mismatch"
ROOT_IDENTITY_MISMATCH_REASON_V2 = "target_pack_validate_root_identity_mismatch"
TARGET_OWNED_MISSING_REASON_V2 = "target_pack_validate_target_owned_missing"
TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2 = "target_pack_validate_target_owned_not_a_regular_file"
TARGET_OWNED_UNREADABLE_REASON_V2 = "target_pack_validate_target_owned_unreadable"
TARGET_OWNED_RESOURCE_LIMIT_EXCEEDED_REASON_V2 = "target_pack_validate_target_owned_resource_limit_exceeded"
TARGET_OWNED_DRIFT_REASON_V2 = "target_pack_validate_target_owned_drift"
GENERATED_FILE_MISSING_REASON_V2 = "target_pack_validate_generated_file_missing"
GENERATED_FILE_NOT_A_REGULAR_FILE_REASON_V2 = "target_pack_validate_generated_file_not_a_regular_file"
GENERATED_FILE_UNREADABLE_REASON_V2 = "target_pack_validate_generated_file_unreadable"
GENERATED_FILE_DRIFT_REASON_V2 = "target_pack_validate_generated_file_drift"

# `unavailable` reason codes deliberately NAME THE OWNER of the question:
# if a `fail` code would need a suffix like this to stay honest, that
# check does not belong in this module.
TARGET_OWNED_SET_REASON_V2 = "target_pack_validate_target_owned_set_requires_the_upstream_manifest"
GENERATED_FILE_SET_REASON_V2 = "target_pack_validate_generated_file_set_requires_the_upstream_manifest"
ROLLOUT_CAPABILITY_REASON_V2 = "target_pack_validate_rollout_capability_requires_the_upstream_manifest"
PREVIOUS_INSTALL_LINEAGE_REASON_V2 = (
    "target_pack_validate_previous_install_lineage_not_verifiable_from_current_target_state"
)
TRUSTED_CHECK_INVENTORY_REASON_V2 = "target_pack_validate_trusted_check_inventory_requires_an_unshipped_contract"

UNVALIDATED_CAPABILITIES_V2: tuple[tuple[str, str], ...] = (
    (TARGET_OWNED_SET_CHECK_V2, TARGET_OWNED_SET_REASON_V2),
    (GENERATED_FILE_SET_CHECK_V2, GENERATED_FILE_SET_REASON_V2),
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
# resolution (see `_verify_declared_ledger_v2`). A resolved-PATH
# comparison would have to resolve the ledger entry first, through the
# live (mutable) `.aiops` alias -- reintroducing the exact independent-
# per-artifact resolution defect this module's snapshot design exists
# to close. A pure string comparison needs no filesystem access at all,
# so it cannot be influenced by a `.aiops` retarget.
_PROFILE_RELATIVE_PATH_STR_V2 = DEFAULT_TARGET_PROFILE_RELATIVE_PATH.as_posix()

# Operational ceiling on whole-file reads of the two `.aiops` artifacts --
# see the module docstring's "BOUNDED OBSERVATION" invariant and "Stated
# limitations". 8 MiB is chosen with a wide margin over observed
# real-world receipt/profile sizes (low hundreds of bytes to a few
# kilobytes for every fixture and template in this repository).
_ARTIFACT_BYTE_LIMIT_V2 = 8 * 1024 * 1024


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
    """The single CONTAINED `.aiops` candidate both artifacts are read
    through -- ONE SNAPSHOT PER DECISION. Resolving `.aiops` independently
    per artifact instead of once lets a symlink retargeted between two
    reads pair a receipt from one directory with a profile from another --
    a pair no single installation ever contained. Raises `PlanError` if
    `.aiops` itself escapes `target_root` or cannot be resolved (e.g. a
    symlink loop).

    Containment only -- proves the candidate does not escape `target_
    root`, nothing about whether it exists or is a directory. See
    `_aiops_directory_status_v2`, which every caller MUST consult before
    treating this candidate as ready."""

    return resolve_within_target_root_v2(target_root_real, target_root_real / _AIOPS_DIR_RELATIVE_V2)


def _aiops_directory_status_v2(aiops_dir: Path) -> str:
    """Classify the ALREADY-CONTAINED `.aiops` candidate's actual
    existence and type. Containment is not existence: a candidate that
    does not escape `target_root` may still not exist at all, or may be
    a regular file instead of a directory -- `resolve_within_target_
    root_v2` proves neither, and this module must not treat a
    successful containment resolution as though it had.

    Returns one of `{"missing", "not_a_directory", "unreadable",
    "ready"}`. The metadata probes are inside the SAME `OSError`
    boundary as everywhere else in this module -- a permission-denied
    probe here is `"unreadable"`, never an escaping exception."""

    try:
        exists = aiops_dir.exists()
        is_dir = aiops_dir.is_dir() if exists else False
    except OSError:
        return "unreadable"
    if not exists:
        return "missing"
    if not is_dir:
        return "not_a_directory"
    return "ready"


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
    """Classify and BOUNDED whole-file read ONE of the two known-location
    `.aiops` artifacts (receipt, profile) -- never used for an arbitrary
    receipt-declared ledger path, which streams instead with no size
    bound (see `_observe_ledger_entry_hash_v2`'s own docstring for why
    ledger entries are not bounded the same way).

    Every metadata probe (`.exists()`/`.is_file()`) is inside the SAME
    `OSError` boundary as the read that follows it: a `PermissionError`
    raised by either probe is `"unreadable"`, not an escaping exception
    -- a target-authored install that sits below a search-restricted
    directory is a diagnosable state, not a programmer bug.

    Returns a status tag from `{"missing", "not_a_regular_file",
    "unreadable", "resource_limit_exceeded", "ok"}` plus the raw bytes
    when readable and within `_ARTIFACT_BYTE_LIMIT_V2`."""

    try:
        exists = path.exists()
        is_file = path.is_file() if exists else False
        if not exists:
            return "missing", None
        if not is_file:
            return "not_a_regular_file", None
        # Read one byte past the limit: enough to detect overflow
        # without a second pass or trusting a possibly-stale `st_size`.
        with path.open("rb") as stream:
            content = stream.read(_ARTIFACT_BYTE_LIMIT_V2 + 1)
    except OSError:
        return "unreadable", None
    if len(content) > _ARTIFACT_BYTE_LIMIT_V2:
        return "resource_limit_exceeded", None
    return "ok", content


def _observe_ledger_entry_hash_v2(path: Path) -> tuple[str, str | None]:
    """Classify and hash ONE receipt-declared ledger path (target-owned
    OR generated-file) through a bounded-MEMORY streaming read, never
    whole-file `read_bytes()`.

    A receipt-controlled ledger may name any path contained in `target_
    root_real`: unlike `doctor`, this command has no manifest authority
    to reject a forged ownership set BEFORE reading it, so it cannot
    know a declared path is illegitimate before observing it.
    Materialising an arbitrarily large, receipt-named file whole into
    memory would be a local memory-exhaustion vector; this streaming
    read forecloses that without inventing a size cap or path allowlist
    -- either would be a policy this command holds no authority for.
    Unlike the two `.aiops` artifacts, no operational byte limit is
    imposed here either: streaming already keeps memory flat regardless
    of the declared file's size, so there is no resource-exhaustion
    vector left to bound with an additional cap.

    Every metadata probe is inside the SAME `OSError` boundary as the
    read, exactly as in `_observe_artifact_bytes_v2`.

    Returns a status tag from `{"missing", "not_a_regular_file",
    "unreadable", "ok"}` plus the hex digest when readable."""

    try:
        exists = path.exists()
        is_file = path.is_file() if exists else False
        if not exists:
            return "missing", None
        if not is_file:
            return "not_a_regular_file", None
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        return "unreadable", None
    return "ok", digest


def _load_receipt_v2(receipt_path: Path) -> tuple[TargetInstallReceiptV2 | None, ValidateCheckV2]:
    status, raw = _observe_artifact_bytes_v2(receipt_path)
    if status in ("missing", "not_a_regular_file"):
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_MISSING_REASON_V2)
    if status == "unreadable":
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_UNREADABLE_REASON_V2)
    if status == "resource_limit_exceeded":
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_RESOURCE_LIMIT_EXCEEDED_REASON_V2)
    if raw is None:  # unreachable given status == "ok"; no reliance on assert being enabled
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_UNREADABLE_REASON_V2)
    try:
        # THE shared authority -- never `model_validate_json` directly.
        receipt = load_target_install_receipt_bytes_v2(raw)
    except TargetInstallReceiptLoadErrorV2:
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_INVALID_REASON_V2)
    return receipt, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_PASS_V2, None)


def _load_profile_v2(
    profile_path: Path,
) -> tuple[TargetProfileV2 | None, bytes | None, str, ValidateCheckV2]:
    """Returns `(profile, raw_bytes, artifact_status, profile_check)`.

    `artifact_status` is the RAW, full-fidelity status from `_observe_
    artifact_bytes_v2` (`"missing"`/`"not_a_regular_file"`/`"unreadable"`/
    `"resource_limit_exceeded"`/`"ok"`) -- preserved and returned even
    though `profile_check` itself deliberately collapses `"missing"`/
    `"not_a_regular_file"` into one disposition (`PROFILE_MISSING_
    REASON_V2`). Collapsing is the right call for the `profile` check's
    OWN vocabulary; it must not also destroy the distinction for a
    DIFFERENT downstream consumer (`target_owned_integrity`'s profile-
    ledger-entry branch) that needs the fuller picture.
    `artifact_status == "ok"` whenever bytes were actually read within
    budget, independent of whether they later decode/parse -- the
    filesystem observation and the semantic parse result are different
    questions; this return value answers only the first one."""

    status, raw = _observe_artifact_bytes_v2(profile_path)
    if status in ("missing", "not_a_regular_file"):
        return None, None, status, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_MISSING_REASON_V2)
    if status == "unreadable":
        return None, None, "unreadable", ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_UNREADABLE_REASON_V2)
    if status == "resource_limit_exceeded":
        return (
            None,
            None,
            "resource_limit_exceeded",
            ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_RESOURCE_LIMIT_EXCEEDED_REASON_V2),
        )
    if raw is None:  # unreachable given status == "ok"; no reliance on assert being enabled
        return None, None, "unreadable", ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_UNREADABLE_REASON_V2)
    try:
        raw_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, raw, "ok", ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_UNREADABLE_REASON_V2)
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
        return None, raw, "ok", ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, reason)
    return profile, raw, "ok", ValidateCheckV2(PROFILE_CHECK_V2, STATUS_PASS_V2, None)


def _profile_ledger_unavailable_reason_v2(
    *, profile_path_reason: str | None, artifact_status: str | None
) -> str | None:
    """Translate the ALREADY-CAPTURED profile artifact observation into
    the reason `target_owned_integrity` reports for the canonical profile
    ledger entry when its bytes are unavailable -- never re-derived from
    `profile_bytes is None`, which collapses `not_a_regular_file`/
    `unreadable`/`resource_limit_exceeded`/`missing`/a path-resolution
    failure into one indistinguishable state. Every value returned is
    already one of `target_pack_validate_v2`'s own constants; no new
    vocabulary."""

    if profile_path_reason is not None:
        return profile_path_reason
    if artifact_status == "missing":
        return TARGET_OWNED_MISSING_REASON_V2
    if artifact_status == "not_a_regular_file":
        return TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2
    if artifact_status == "unreadable":
        return TARGET_OWNED_UNREADABLE_REASON_V2
    if artifact_status == "resource_limit_exceeded":
        return TARGET_OWNED_RESOURCE_LIMIT_EXCEEDED_REASON_V2
    return None


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


def _verify_declared_ledger_v2(
    *,
    check_name: str,
    target_root_real: Path,
    entries: dict[str, str],
    missing_reason: str,
    not_a_regular_file_reason: str,
    unreadable_reason: str,
    drift_reason: str,
    profile_reuse: tuple[bytes | None, str | None] | None,
) -> ValidateCheckV2:
    """THE generic per-entry ledger verifier, shared by `target_owned_
    integrity` and `generated_file_integrity` -- one traversal, one
    resolution primitive, one streaming-observation primitive, for
    both ledgers. Validates only the byte claims the receipt actually
    makes for the entries it declares -- see the module docstring's "A
    RECEIPT FIELD IS NOT ONE PROPERTY". `pass` means "for each local
    byte-integrity claim this receipt makes here, the contained regular
    file currently matches it"; it never means "this path is proven to
    belong to this ownership class." A receipt may therefore cause this
    check to inspect any contained regular file it declares -- bounded
    by containment only, never by an invented size cap or allowlist
    (see `_observe_ledger_entry_hash_v2`'s own docstring), and the
    report exposes only `(name, status, reason_code)`, never content or
    paths.

    `profile_reuse`, when not `None`, is `(profile_bytes, profile_
    unavailable_reason)` -- the ALREADY-CAPTURED `.aiops` snapshot's
    profile artifact bytes, consulted instead of resolving/reading
    AGAIN whenever a declared entry's relative-path STRING equals the
    profile's own canonical relative path (`_PROFILE_RELATIVE_PATH_
    STR_V2`). The string comparison happens BEFORE any resolution is
    attempted for that entry -- resolving first and comparing RESOLVED
    paths would resolve the entry through the live, mutable `.aiops`
    alias, independently of the snapshot already captured for the
    profile artifact, reintroducing exactly the per-artifact
    independent-resolution defect this module's snapshot design exists
    to close. `generated_file_hashes` passes `profile_reuse=None`: the
    profile is never classified `UPSTREAM_GENERATED`, so no generated-
    file entry can legitimately name it.

    Traversal order is sorted, not raw mapping-insertion order:
    `receipt_hash`'s canonical preimage sorts JSON object keys, so raw
    member order is NOT part of the receipt's identity -- two receipts
    with identical semantic ledger claims and the same valid `receipt_
    hash` can differ only in JSON member order, and the first-failure-
    wins rule below must not let that non-identity-bearing difference
    change which reason code is reported first (`app/agent_review/
    AGENTS.md`'s determinism section: every emitted artifact byte must
    be independent of arrival order)."""

    profile_hash: str | None = None
    profile_unavailable_reason: str | None = None
    if profile_reuse is not None:
        profile_bytes, profile_unavailable_reason = profile_reuse
        if profile_bytes is not None:
            profile_hash = hashlib.sha256(profile_bytes).hexdigest()

    for relative_path in sorted(entries):
        declared_hash = entries[relative_path]
        if profile_reuse is not None and relative_path == _PROFILE_RELATIVE_PATH_STR_V2:
            # Reuse the hash of the bytes already captured for the
            # `.aiops` snapshot's profile artifact -- never a second,
            # independently-timed resolution/read of this same entry.
            # Without reuse, a receipt could carry the SEMANTIC hash
            # (`target_profile_hash`) of one profile content and the BYTE
            # hash of a DIFFERENT one here, each individually satisfied by
            # two reads timed to observe different bytes.
            if profile_hash is None:
                # `profile_unavailable_reason` is guaranteed non-None here
                # by construction: `profile_bytes is None` and `profile_
                # unavailable_reason is None` never occur together (see
                # `_profile_ledger_unavailable_reason_v2`'s callers).
                return ValidateCheckV2(check_name, STATUS_FAIL_V2, profile_unavailable_reason)
            observed_hash = profile_hash
        else:
            try:
                resolved = resolve_within_target_root_v2(target_root_real, target_root_real / relative_path)
            except PlanError as exc:
                return ValidateCheckV2(check_name, STATUS_FAIL_V2, _path_reason_for_plan_error_v2(exc))
            status, observed = _observe_ledger_entry_hash_v2(resolved)
            if status == "missing":
                return ValidateCheckV2(check_name, STATUS_FAIL_V2, missing_reason)
            if status == "not_a_regular_file":
                return ValidateCheckV2(check_name, STATUS_FAIL_V2, not_a_regular_file_reason)
            if status == "unreadable" or observed is None:
                return ValidateCheckV2(check_name, STATUS_FAIL_V2, unreadable_reason)
            observed_hash = observed

        if observed_hash != declared_hash:
            return ValidateCheckV2(check_name, STATUS_FAIL_V2, drift_reason)

    return ValidateCheckV2(check_name, STATUS_PASS_V2, None)


def _target_owned_integrity_check_v2(
    *,
    target_root_real: Path,
    receipt: TargetInstallReceiptV2,
    profile_bytes: bytes | None,
    profile_unavailable_reason: str | None,
) -> ValidateCheckV2:
    return _verify_declared_ledger_v2(
        check_name=TARGET_OWNED_INTEGRITY_CHECK_V2,
        target_root_real=target_root_real,
        entries=dict(receipt.target_owned_file_hashes),
        missing_reason=TARGET_OWNED_MISSING_REASON_V2,
        not_a_regular_file_reason=TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2,
        unreadable_reason=TARGET_OWNED_UNREADABLE_REASON_V2,
        drift_reason=TARGET_OWNED_DRIFT_REASON_V2,
        profile_reuse=(profile_bytes, profile_unavailable_reason),
    )


def _generated_file_integrity_check_v2(*, target_root_real: Path, receipt: TargetInstallReceiptV2) -> ValidateCheckV2:
    """`generated_file_hashes` keys are `SafeText`, not `RelativePath`
    (unlike `target_owned_file_hashes`) -- the receipt contract does not
    lexically forbid `../` traversal or an absolute path in this
    mapping's keys. Safety here does not depend on the lexical shape of
    the declared string: `resolve_within_target_root_v2` checks
    containment on the RESOLVED path regardless of how the candidate was
    spelled, so a traversal or absolute-path entry is refused exactly
    like any other escape, through the same primitive `_verify_
    declared_ledger_v2` already uses for `target_owned_file_hashes`."""

    return _verify_declared_ledger_v2(
        check_name=GENERATED_FILE_INTEGRITY_CHECK_V2,
        target_root_real=target_root_real,
        entries=dict(receipt.generated_file_hashes),
        missing_reason=GENERATED_FILE_MISSING_REASON_V2,
        not_a_regular_file_reason=GENERATED_FILE_NOT_A_REGULAR_FILE_REASON_V2,
        unreadable_reason=GENERATED_FILE_UNREADABLE_REASON_V2,
        drift_reason=GENERATED_FILE_DRIFT_REASON_V2,
        profile_reuse=None,  # the profile is never classified UPSTREAM_GENERATED
    )


def _unavailable_checks_v2() -> tuple[ValidateCheckV2, ...]:
    """The capabilities this command always discloses it cannot
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

    # Containment is not existence (see the module docstring): a
    # contained candidate may still not exist, or may not be a
    # directory. Receipt/profile must never be looked up unless `.aiops`
    # itself is observed to be an actual, readable directory.
    aiops_status = _aiops_directory_status_v2(aiops_dir)
    if aiops_status != "ready":
        aiops_reason = {
            "missing": AIOPS_MISSING_REASON_V2,
            "not_a_directory": AIOPS_NOT_A_DIRECTORY_REASON_V2,
            "unreadable": AIOPS_UNREADABLE_REASON_V2,
        }[aiops_status]
        checks.append(ValidateCheckV2(AIOPS_SNAPSHOT_CHECK_V2, STATUS_FAIL_V2, aiops_reason))
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
        profile, profile_bytes, profile_artifact_status, profile_check = _load_profile_v2(profile_path)
    else:
        profile, profile_bytes, profile_artifact_status = None, None, None
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
                profile_unavailable_reason=_profile_ledger_unavailable_reason_v2(
                    profile_path_reason=profile_path_reason, artifact_status=profile_artifact_status
                ),
            )
        )
        checks.append(_generated_file_integrity_check_v2(target_root_real=target_root_real, receipt=receipt))

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
