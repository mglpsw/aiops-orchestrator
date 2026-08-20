"""`#203-C2` -- offline, read-only, target-only validation of an installed
AgentReview v2 target pack (issue #203, child of tracker #199).

Structural successor to the forensic prior attempt at this command (PR
#242, closed via Structural Change Preflight STOP/REDESIGN after three
consecutive review rounds converged on the same boundary: a target's
filesystem state was projected through ad-hoc `tuple[str, X | None]`
pairs before any check ever consumed it, so a fix to one status branch
had no mechanism forcing every caller to handle every branch). This
module is a REWRITE from canonical master, not a port of #242's
production code -- see the module-adjacent qualification notes for the
full property inventory. Nothing here imports or depends on #242.

`run_validate_v2` answers a narrower question than `doctor`:

    of the relations for which THIS command holds local, independent
    evidence -- derived only from the state actually installed under
    `target_root` and from contracts this pack already owns -- is any
    violated?

It does NOT answer "does this target correspond to the upstream pack"
-- that is `doctor`'s charter. Absence of evidence is reported as
`unavailable`, never silently filled with a locally-invented rule.

That boundary is not merely documented in prose: it is a NAMED,
machine-readable row (`upstream_pack_identity`). `valid: true` means
only that no locally observable relation is violated, and a receipt can
be locally self-coherent while its `pack_version`, `toolrepo_sha` and
`manifest_digest` are stale or fabricated -- validate holds no manifest
or toolrepo to check them against. Emitting the verdict without that
row would let a consumer read `valid: true` as "provenance verified",
so the disclosure travels in the report itself, never only in docs.

## Design invariants

- READ-ONLY BY CONSTRUCTION, proven mechanically alongside `doctor` by
  `tests/agent_review/test_target_pack_arch_v2.py`.
- A REAL TYPED OBSERVATION ALGEBRA, not a string-tag/Optional-payload
  pair: every filesystem observation this module makes is one of a
  closed set of frozen discriminated variants (`Missing`, `NonRegular`,
  `MetadataUnreadable`, `DirectoryReady`, `PathResolutionFailure`,
  `ReadUnreadable`, `ResourceLimitExceeded`, `BufferedFile`,
  `DigestFile`), grouped into narrow per-purpose unions so a function
  typed to return one family cannot even attempt to return a variant
  from another. A state carrying bytes/a digest cannot simultaneously
  represent missing/unreadable/non-regular -- this is enforced by the
  type system, not by convention.
- STRICT LAYERING: filesystem observation -> semantic parsing -> claim
  evaluation -> report disposition. Reason codes are assigned only at
  the claim-evaluation/report layer; the observation functions never
  embed a report-shaped decision.
- ONE CAPTURED ROOT PER DECISION: `target_root` is resolved and
  classified exactly once (`_observe_root_v2`), including the metadata
  probe that decides whether it is a readable directory -- both inside
  the SAME typed observation boundary as the read that would follow,
  closing a residual PR #242 never fixed: `target_root_real.is_dir()`
  sat outside any error boundary, so a `PermissionError` from a
  search-restricted ancestor directory could escape `run_validate_v2`
  uncaught.
- ONE `.aiops` RESOLUTION PER DECISION: resolved once via the shared
  `resolve_within_target_root_v2`, then classified
  (`_observe_aiops_v2`); CONTAINMENT IS NOT EXISTENCE -- a candidate
  that does not escape `target_root` may still not exist, or may be a
  plain file. `aiops_snapshot` reports `pass` only when the resolved
  candidate is observed to actually be a directory; receipt/profile are
  never looked up otherwise.
- ONE OBSERVATION REGISTRY: receipt and profile are each observed
  exactly once; their outcome (success or failure) is seeded into a
  single registry keyed by RESOLVED path, so any ledger claim -- in
  EITHER `target_owned_file_hashes` or `generated_file_hashes` -- that
  happens to name the receipt's or profile's own canonical path reuses
  the already-captured observation instead of rereading it. Validate
  does not assume the profile can only be classified `TARGET_OWNED`;
  that classification is upstream-manifest authority this command does
  not have. Any OTHER declared path is resolved once and its
  observation cached against repeated aliases (distinct declared paths
  resolving to the same real file cost one read, not N).
- A CANONICAL OBSERVATION PLAN: every ledger claim (from both
  `target_owned_file_hashes` and `generated_file_hashes`) is compiled
  into one sorted, deterministic list BEFORE any ledger content I/O.
  Traversal order is `(relative_path, ledger_kind)`, independent of raw
  JSON member order -- `receipt_hash`'s canonical preimage sorts JSON
  object keys, so member order was never part of the receipt's
  identity (`app/agent_review/AGENTS.md`'s determinism section).
- NO CROSS-LEDGER OVERLAP CHECK: `TargetInstallReceiptV2` (`#203-C1`,
  merged to master) already enforces `keys(target_owned_file_hashes) &
  keys(generated_file_hashes) == set()` at the shared contract layer,
  using the SAME `RelativePath` authority both fields are keyed by.
  Every `TargetInstallReceiptV2` this module can ever hold already
  satisfies disjointness; re-deriving the check here would be a second,
  independently-maintained definition of the same invariant.
- ONE AGGREGATE OPERATIONAL BUDGET for ledger observation
  (`_LedgerObservationBudgetV2`): streaming already bounds MEMORY
  regardless of a single file's size, but bounds neither the number of
  claims, the number of distinct files touched, nor the total bytes
  read across every claim. A receipt-declared ledger may name any
  number of aliases to one large file, or many large distinct files --
  the budget is debited per claim, per newly-observed resolved path,
  and per byte read (mid-stream, so a single oversized file is aborted
  once the remaining byte budget is crossed, not after it is fully
  hashed). This is command-owned OPERATIONAL policy, never a schema
  truth or a claim about what the contract permits.
- BUDGET EXHAUSTION LOSES NO EVIDENCE: exhausting the aggregate budget
  is its own failure dimension (`observation_budget`), never silently
  relabelled as `target_owned_drift`/`generated_file_drift`/
  `unreadable`. A ledger already showed to contain a genuine
  counterexample (missing/non-regular/unreadable/drift) before the
  budget ran out KEEPS that failing disposition; a ledger the budget
  never finished evaluating, with no counterexample seen, is `absent`
  from the report (never fabricated as `pass`). An empty ledger is
  trivially complete and passes without any I/O.
- TOTAL over the enumerated target-state failure domain: every
  diagnosable target state becomes a reason-coded check, never an
  uncaught exception. Internal programmer errors remain exceptions --
  that is the entire point of never writing `except Exception`.
- HONEST about what it does not know: `unvalidated_capabilities` names
  every dimension this command cannot establish from the target alone,
  emitted on every return path, including the earliest refusals.
- DERIVES, NEVER RE-DERIVES: every parse/hash/containment/repository-
  shape/path-normalization/ledger-disjointness decision routes through
  the pack's existing shared authority (`load_target_profile_text_v2`,
  `load_target_install_receipt_bytes_v2`, `resolve_within_target_
  root_v2`, and -- since `#203-C1` -- `Repository`/`RelativePath` on
  `TargetInstallReceiptV2` itself). This module owns no second
  definition of any of these.

## What this module deliberately does NOT check, and why

- Rollout ceiling and target-owned/generated-file SET completeness or
  ownership classification: `manifest.max_supported_rollout_mode` and
  `TargetPackManifestV2.generated_files[].ownership` are upstream
  authorities this command has no manifest to read. `doctor` owns all
  of these, against the real manifest.
- `compatibility`: admits both `compatible` and `major_incompatible` as
  structurally valid declarations -- no contract validator makes either
  one invalid, and the field is tied to an upgrade/downgrade-protection
  authority this slice does not deliver.
- `previous_install_identity` compared to the CURRENT receipt: the
  field points at the PRIOR install, not a copy of the current one.
  This command has no independent history store to confirm the
  reference points at a receipt that ever existed.
- Repository shape of `target_repo`, `RelativePath` normalization of
  either ledger's keys, or ownership-ledger disjointness: all three are
  now `TargetInstallReceiptV2`'s own construction-time invariants
  (`#203-C1`). A receipt violating any of them never becomes a
  `TargetInstallReceiptV2` instance in the first place -- `receipt`
  reports `invalid` through the shared loader, and this module never
  sees an object where any of the three could be false.
- Whether a path declared in either ledger was actually supposed to
  carry that ownership classification: that lives solely in
  `TargetPackManifestV2.generated_files[].ownership` (spec `§8`), which
  this command does not have. `target_owned_integrity`/`generated_
  file_integrity` verify only the byte claims the receipt actually
  makes -- never "this path is proven to belong to this ownership
  class."

## Stated limitations

- `root_identity` proves intra-receipt coherence (`target_repo` agrees
  with `portable_target_root_identity`), not filesystem provenance: a
  whole `.aiops/` directory copied from one repository into another,
  with receipt and profile still coherent WITH EACH OTHER, is
  undetectable from this command alone.
- `root_identity` assumes `root_relative_path="."` -- no receipt field
  records an alternative.
- The `.aiops`/root snapshot is `Path`-based (`resolve(strict=False)`
  plus containment/metadata probes), not `openat`/directory-file-
  descriptor atomicity. It closes the inconsistent-pair class; it does
  not claim full filesystem-wide atomic snapshot semantics.
- Alias deduplication is by RESOLVED PATH STRING identity, not
  `st_dev`/`st_ino` -- two declared paths resolving to the same
  canonical filesystem path are recognised as the same file; true
  hardlinks reachable via two DIFFERENT resolved paths are not
  deduplicated. This is not required for the demonstrated property
  (symlink aliases to one file); it remains bounded regardless by the
  aggregate observation budget.
- `_ARTIFACT_BYTE_LIMIT_V2` (receipt/profile) and `_LedgerObservation
  BudgetV2` (ledger claims) are both operational ceilings this command
  enforces, chosen with a wide margin over observed real-world sizes.
  Neither claims to bound, or derives from, any schema-declared
  maximum.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.profile_loader_v2 import (
    DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
    TARGET_PROFILE_UNREADABLE_REASON_V2,
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_text_v2,
)
from app.agent_review.target_pack_build_v2 import SEED_PROFILE_IDENTITY_PLACEHOLDER_V2
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
from app.agent_review.target_pack_runtime_authority_v2 import (
    AIOPS_SNAPSHOT_CHECK_V2,
    CROSS_LEDGER_ALIAS_CHECK_V2,
    GENERATED_FILE_INTEGRITY_CHECK_V2,
    GENERATED_FILE_SET_CHECK_V2,
    GENERATED_FILE_SET_REASON_V2,
    OBSERVATION_BUDGET_CHECK_V2,
    PREVIOUS_INSTALL_LINEAGE_CHECK_V2,
    PREVIOUS_INSTALL_LINEAGE_REASON_V2,
    PROFILE_CHECK_V2,
    PROFILE_HASH_CHECK_V2,
    PROFILE_IDENTITY_CHECK_V2,
    RECEIPT_CHECK_V2,
    ROLLOUT_CAPABILITY_CHECK_V2,
    ROLLOUT_CAPABILITY_REASON_V2,
    ROOT_IDENTITY_CHECK_V2,
    TARGET_OWNED_INTEGRITY_CHECK_V2,
    TARGET_OWNED_SET_CHECK_V2,
    TARGET_OWNED_SET_REASON_V2,
    TARGET_ROOT_CHECK_V2,
    TRUSTED_CHECK_INVENTORY_CHECK_V2,
    TRUSTED_CHECK_INVENTORY_REASON_V2,
    UPSTREAM_PACK_IDENTITY_CHECK_V2,
    UPSTREAM_PACK_IDENTITY_REASON_V2,
    ValidateEvaluationClassV2,
    validate_check_domain_names_v2,
    validate_check_index_v2,
    validate_locally_evaluable_names_v2,
    validate_spec_by_name_v2,
    validate_unvalidated_specs_v2,
)

# --- Status vocabulary -------------------------------------------------
STATUS_PASS_V2 = "pass"
STATUS_FAIL_V2 = "fail"
STATUS_UNAVAILABLE_V2 = "unavailable"

# --- Check-name identities (re-exported, not owned here) -----------------
# The 17 check identities and the 6 structural `unavailable` reasons are
# declared by `target_pack_runtime_authority_v2`. They are imported above and
# re-exported here only so existing `from ...target_pack_validate_v2 import
# TARGET_ROOT_CHECK_V2` call sites keep working. There is deliberately no
# second complete enumeration in this module: a narrow partial binding such as
# `_CHECK_NAME_BY_LEDGER_V2` below is a different typed relation (ledger kind
# -> identity), not a rival domain authority.

# The determinism contract: every return path emits a duplicate-free
# SUBSEQUENCE of the authority's declared order. Both tuples below are DERIVED
# PROJECTIONS of that domain -- never independently maintained lists.
VALIDATE_CHECK_ORDER_V2: tuple[str, ...] = validate_check_domain_names_v2()

# --- Reason codes ----------------------------------------------------------
# Every reason code this module emits is defined here and begins
# `target_pack_validate_`.
TARGET_ROOT_UNRESOLVABLE_REASON_V2 = "target_pack_validate_target_root_unresolvable"
TARGET_ROOT_UNREADABLE_REASON_V2 = "target_pack_validate_target_root_unreadable"
TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 = "target_pack_validate_target_root_not_a_directory"
PATH_ESCAPES_TARGET_ROOT_REASON_V2 = "target_pack_validate_path_escapes_target_root"
PATH_RESOLUTION_FAILED_REASON_V2 = "target_pack_validate_path_resolution_failed"
# A THIRD, distinct contained-path resolution outcome. Deliberately not
# folded into either neighbour above: an escape is a containment verdict,
# a resolution failure is a malformed link structure, and this one is
# neither -- the filesystem refused to answer, so no verdict about
# containment or structure was ever reached. The existing
# `..._target_root_unreadable` cannot be reused: its subject is the root
# itself, not a descendant under it.
PATH_RESOLUTION_UNREADABLE_REASON_V2 = "target_pack_validate_path_resolution_unreadable"
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
OBSERVATION_BUDGET_EXCEEDED_REASON_V2 = "target_pack_validate_observation_budget_exceeded"
TARGET_OWNED_MISSING_REASON_V2 = "target_pack_validate_target_owned_missing"
TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2 = "target_pack_validate_target_owned_not_a_regular_file"
TARGET_OWNED_UNREADABLE_REASON_V2 = "target_pack_validate_target_owned_unreadable"
TARGET_OWNED_DRIFT_REASON_V2 = "target_pack_validate_target_owned_drift"
GENERATED_FILE_MISSING_REASON_V2 = "target_pack_validate_generated_file_missing"
GENERATED_FILE_NOT_A_REGULAR_FILE_REASON_V2 = "target_pack_validate_generated_file_not_a_regular_file"
GENERATED_FILE_UNREADABLE_REASON_V2 = "target_pack_validate_generated_file_unreadable"
GENERATED_FILE_DRIFT_REASON_V2 = "target_pack_validate_generated_file_drift"
CROSS_LEDGER_ALIAS_CONFLICT_REASON_V2 = "target_pack_validate_cross_ledger_alias_conflict"

UNVALIDATED_CAPABILITIES_V2: tuple[tuple[str, str], ...] = tuple(
    (spec.name, spec.unvalidated_reason_code or "") for spec in validate_unvalidated_specs_v2()
)

_AIOPS_DIR_RELATIVE_V2 = DEFAULT_TARGET_PROFILE_RELATIVE_PATH.parent
_PROFILE_FILENAME_V2 = DEFAULT_TARGET_PROFILE_RELATIVE_PATH.name
_RECEIPT_FILENAME_V2 = Path(RECEIPT_RELATIVE_PATH_V2).name

# Operational ceiling on whole-file reads of the two `.aiops` artifacts
# (receipt, profile) -- reconfirmed against every fixture/writer output on
# canonical master: the shipped template profile and every receipt fixture
# in this repository are low hundreds of bytes to a few kilobytes, so 8 MiB
# remains a wide margin, not a value chosen for this module alone.
_ARTIFACT_BYTE_LIMIT_V2 = 8 * 1024 * 1024

# Bounded chunk size for streaming ledger-entry hashing -- large enough to
# amortise syscall overhead, small enough that the aggregate byte budget
# below can abort within one chunk of the boundary rather than materialising
# an unbounded remainder.
_LEDGER_CHUNK_SIZE_V2 = 1024 * 1024

# `_LedgerObservationBudgetV2`'s defaults, as module-level names read
# explicitly at construction time (not baked into the dataclass field
# defaults, which Python resolves once at class-definition time and would
# make them unpatchable by tests).
_DEFAULT_MAX_LEDGER_CLAIMS_V2 = 2048
_DEFAULT_MAX_UNIQUE_LEDGER_PATHS_V2 = 2048
_DEFAULT_MAX_TOTAL_LEDGER_BYTES_V2 = 64 * 1024 * 1024


# --- 1. Observation algebra: frozen discriminated variants ---------------


@dataclass(frozen=True)
class PathResolutionFailure:
    """A candidate path could not even be classified: it either escapes
    `target_root` or is an unresolvable symlink loop. `reason` is one of
    THIS module's own already-translated reason codes."""

    reason: str


@dataclass(frozen=True)
class Missing:
    pass


@dataclass(frozen=True)
class NonRegular:
    pass


@dataclass(frozen=True)
class MetadataUnreadable:
    """The EXISTENCE/TYPE probe itself (`.exists()`/`.is_file()`/
    `.is_dir()`) raised `OSError` -- distinct from `ReadUnreadable`,
    whose probe succeeded but the subsequent content read failed."""

    pass


@dataclass(frozen=True)
class DirectoryReady:
    pass


@dataclass(frozen=True)
class ReadUnreadable:
    pass


@dataclass(frozen=True)
class ResourceLimitExceeded:
    pass


@dataclass(frozen=True)
class BufferedFile:
    content: bytes


@dataclass(frozen=True)
class DigestFile:
    digest: str


# Narrow per-purpose unions -- a function typed to return one of these
# cannot construct or return a variant from a different family; the type
# checker rejects it at the call/return site, not merely at a runtime
# isinstance check.
RootObservationV2 = PathResolutionFailure | Missing | MetadataUnreadable | NonRegular | DirectoryReady
AiopsObservationV2 = PathResolutionFailure | Missing | MetadataUnreadable | NonRegular | DirectoryReady
BoundedArtifactV2 = Missing | MetadataUnreadable | NonRegular | ReadUnreadable | ResourceLimitExceeded | BufferedFile
StreamedLedgerEntryV2 = (
    PathResolutionFailure | Missing | MetadataUnreadable | NonRegular | ReadUnreadable | ResourceLimitExceeded | DigestFile
)


def _path_reason_for_plan_error_v2(exc: PlanError) -> str:
    """Single translation site for every `PlanError` this module
    observes -- a genuine containment escape and an unresolvable symlink
    loop must not collapse into one reason code."""

    if exc.reason_code == PLAN_PATH_RESOLUTION_FAILED_REASON_V2:
        return PATH_RESOLUTION_FAILED_REASON_V2
    return PATH_ESCAPES_TARGET_ROOT_REASON_V2


def _observe_root_v2(target_root: Path) -> tuple[Path | None, RootObservationV2]:
    """Classify `target_root` itself, ONCE. EVERY leaf filesystem
    operation here -- the resolution step AND the metadata probe
    (`.exists()`/`.is_dir()`) -- sits inside this one typed boundary.

    Two distinct ways resolution can fail, deliberately NOT collapsed:

    - `RuntimeError` is how `pathlib` reports a symlink loop: the path's
      own link structure is malformed, and no filesystem access problem
      is implied -> `..._target_root_unresolvable`.
    - `OSError` means the resolution could not be carried out at all
      because the filesystem would not answer (`EACCES` on an ancestor
      without search permission, `EIO` on a failing or disconnected
      mount, or `ENOENT` from `getcwd()` when a relative root is
      resolved from a deleted working directory) -> the SAME
      `..._target_root_unreadable` code the metadata probe below emits,
      because it is the same operator-visible condition reached one
      operation earlier.

    Both are `PathResolutionFailure`: it is the only variant that can
    honestly report "there is no resolved path", which is exactly the
    state a failed `.resolve()` leaves behind. `MetadataUnreadable`
    cannot express it -- that variant's contract is that a resolved path
    exists and only its metadata was unreadable.

    Closes two residuals in sequence: PR #242 left `is_dir()` outside any
    `OSError` boundary, and this module's own first cut left `.resolve()`
    inside a narrower `RuntimeError`-only closure than the probe that
    follows it, despite claiming one shared boundary."""

    try:
        resolved = target_root.resolve(strict=False)
    except RuntimeError:
        return None, PathResolutionFailure(TARGET_ROOT_UNRESOLVABLE_REASON_V2)
    except OSError:
        return None, PathResolutionFailure(TARGET_ROOT_UNREADABLE_REASON_V2)
    try:
        exists = resolved.exists()
        is_dir = resolved.is_dir() if exists else False
    except OSError:
        return resolved, MetadataUnreadable()
    if not exists:
        return resolved, Missing()
    if not is_dir:
        return resolved, NonRegular()
    return resolved, DirectoryReady()


def _resolve_contained_path_v2(target_root_real: Path, candidate: Path) -> tuple[Path | None, str | None]:
    """THE single site in this module where a contained-path resolution
    attempt becomes a typed outcome. Every downstream consumer (`.aiops`,
    the two `.aiops` artifacts, and every ledger claim) goes through here;
    an architecture test in `test_target_pack_arch_v2.py` enforces that
    mechanically, so a future consumer cannot reintroduce a private
    resolution path.

    Containment itself is NOT re-derived here. `resolve_within_target_
    root_v2` remains the pack's single authority for what "contained in
    `target_root`" means, exactly as `doctor`/`init` use it. This function
    only decides how THIS module OBSERVES the ways that attempt can fail
    -- which is the observation plane's own charter.

    Three genuinely distinct outcomes, deliberately not collapsed:

    - `PlanError` carrying the escape code -> the candidate resolved, and
      resolved outside the root: a containment verdict.
    - `PlanError` carrying the resolution-failed code -> a symlink loop:
      the path's link structure is malformed and resolves nowhere.
    - `OSError` -> the filesystem refused to perform the resolution at all
      (`EACCES` on an ancestor without search permission, `EIO` on a
      failing or disconnected mount). No containment or structural verdict
      was ever reached, so reporting either of the above would assert
      something never observed.

    The third case is why this adapter exists. `resolve_within_target_
    root_v2` translates `RuntimeError` and `ValueError` into `PlanError`
    but lets `OSError` from its internal `Path.resolve(strict=False)`
    propagate untyped -- so before this round, `.aiops`, artifact and
    ledger resolution each caught only `PlanError` and an `EACCES`
    escaped `run_validate_v2` as a traceback instead of a reason-coded
    report. Round 2 closed exactly this class for the ROOT observer; that
    fix could not cover these sites because they resolve through the
    shared containment authority rather than calling `Path.resolve`
    directly.

    NOTE, deliberately not silently repaired here: the same `OSError` gap
    remains open in `resolve_within_target_root_v2` itself, and therefore
    for `doctor`/`init`/the operation planner, which this PR holds no
    authority to change. Closing it there is a separate predecessor
    change against a shared module."""

    try:
        return resolve_within_target_root_v2(target_root_real, candidate), None
    except PlanError as exc:
        return None, _path_reason_for_plan_error_v2(exc)
    except OSError:
        return None, PATH_RESOLUTION_UNREADABLE_REASON_V2


def _observe_aiops_v2(target_root_real: Path) -> tuple[Path | None, AiopsObservationV2]:
    """Resolve and classify the ONE `.aiops` candidate. Containment
    (`resolve_within_target_root_v2`, reached through the module's single
    contained-resolution adapter) proves only that the candidate does not
    escape `target_root_real` -- nothing about existence or type."""

    candidate, resolution_reason = _resolve_contained_path_v2(
        target_root_real, target_root_real / _AIOPS_DIR_RELATIVE_V2
    )
    if resolution_reason is not None:
        return None, PathResolutionFailure(resolution_reason)
    assert candidate is not None
    try:
        exists = candidate.exists()
        is_dir = candidate.is_dir() if exists else False
    except OSError:
        return candidate, MetadataUnreadable()
    if not exists:
        return candidate, Missing()
    if not is_dir:
        return candidate, NonRegular()
    return candidate, DirectoryReady()


def _resolve_ledger_path_v2(target_root_real: Path, relative_path: str) -> tuple[Path | None, str | None]:
    return _resolve_contained_path_v2(target_root_real, target_root_real / relative_path)


def _observe_bounded_artifact_v2(path: Path) -> BoundedArtifactV2:
    """Classify and BOUNDED whole-file read ONE of the two known-location
    `.aiops` artifacts (receipt, profile). The metadata probe and the
    content read are two SEPARATE `try/except OSError` boundaries so the
    two distinct failure classes stay distinguishable; neither can ever
    escape uncaught."""

    try:
        exists = path.exists()
        is_file = path.is_file() if exists else False
    except OSError:
        return MetadataUnreadable()
    if not exists:
        return Missing()
    if not is_file:
        return NonRegular()
    try:
        # Read one byte past the limit: enough to detect overflow without
        # a second pass or trusting a possibly-stale `st_size`.
        with path.open("rb") as stream:
            content = stream.read(_ARTIFACT_BYTE_LIMIT_V2 + 1)
    except OSError:
        return ReadUnreadable()
    if len(content) > _ARTIFACT_BYTE_LIMIT_V2:
        return ResourceLimitExceeded()
    return BufferedFile(content)


def _artifact_to_ledger_observation_v2(observation: BoundedArtifactV2) -> StreamedLedgerEntryV2:
    """Translate a `BoundedArtifactV2` outcome into the `StreamedLedger
    EntryV2` family, so a receipt/profile artifact's ALREADY-CAPTURED
    observation can seed the ledger observation registry directly --
    `BufferedFile` becomes a `DigestFile` of the same bytes; every other
    variant is shared between both families and passes through as-is."""

    if isinstance(observation, BufferedFile):
        return DigestFile(hashlib.sha256(observation.content).hexdigest())
    return observation


@dataclass
class _LedgerObservationBudgetV2:
    """Command-owned OPERATIONAL policy, never a schema truth. Streaming
    already bounds MEMORY regardless of one file's size; it bounds
    neither the number of claims, the number of distinct files touched,
    nor the total bytes read across every claim. Defaults (module-level
    `_DEFAULT_MAX_LEDGER_CLAIMS_V2` and friends, read explicitly at
    construction rather than baked into the dataclass field defaults so
    tests can override them) are chosen with a wide margin over the
    current canonical-master template/fixture corpus (a single shipped
    `TARGET_OWNED` file, low hundreds of bytes) while still bounding a
    receipt-declared ledger that could otherwise name unbounded aliases
    or unbounded distinct large files."""

    max_claims: int
    max_unique_resolved_paths: int
    max_total_bytes: int

    claims_seen: int = field(default=0, init=False)
    unique_paths_seen: int = field(default=0, init=False)
    total_bytes_read: int = field(default=0, init=False)
    exhausted: bool = field(default=False, init=False)

    def debit_claim(self) -> bool:
        self.claims_seen += 1
        if self.claims_seen > self.max_claims:
            self.exhausted = True
        return not self.exhausted

    def debit_unique_path(self) -> bool:
        self.unique_paths_seen += 1
        if self.unique_paths_seen > self.max_unique_resolved_paths:
            self.exhausted = True
        return not self.exhausted

    def debit_bytes(self, count: int) -> bool:
        self.total_bytes_read += count
        if self.total_bytes_read > self.max_total_bytes:
            self.exhausted = True
        return not self.exhausted


def _observe_streamed_ledger_entry_v2(path: Path, budget: _LedgerObservationBudgetV2) -> StreamedLedgerEntryV2:
    """Classify and hash ONE receipt-declared ledger path through a
    bounded-MEMORY streaming read, debiting the aggregate byte budget
    WHILE reading -- a single oversized file is aborted once the
    remaining budget is crossed, never fully hashed first and discovered
    over-budget only afterward. Deliberately does not call `hashlib.
    file_digest`: that reads in one C-level pass with no chunk-level
    accounting hook, which would prevent the mid-stream abort this
    property requires."""

    try:
        exists = path.exists()
        is_file = path.is_file() if exists else False
    except OSError:
        return MetadataUnreadable()
    if not exists:
        return Missing()
    if not is_file:
        return NonRegular()
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(_LEDGER_CHUNK_SIZE_V2)
                if not chunk:
                    break
                if not budget.debit_bytes(len(chunk)):
                    return ResourceLimitExceeded()
                hasher.update(chunk)
    except OSError:
        return ReadUnreadable()
    return DigestFile(hasher.hexdigest())


class _ObservationRegistryV2:
    """ONE registry for the whole decision, keyed by resolved-path
    string. Seeded with the receipt's and profile's own already-captured
    observations (translated via `_artifact_to_ledger_observation_v2`)
    so a ledger claim naming either canonical path -- in EITHER ledger,
    since validate does not assume the profile can only be `TARGET_
    OWNED` -- reuses it rather than rereading. Any other resolved path
    is observed once and cached, so N declared aliases to the same real
    file cost one read.

    Also retains, per resolved path, which ledger KINDS have claimed it
    (`register_claim_ledger`) -- `#203-C1` proves the two ledgers'
    DECLARED keys are disjoint; it says nothing about whether two
    distinct declared keys resolve to the SAME physical file (an
    in-root symlink). That is a runtime, only-locally-observable
    relation this registry is the natural single owner of, since it
    already owns "what resolved identity does this claim refer to"."""

    def __init__(self, budget: _LedgerObservationBudgetV2) -> None:
        self._budget = budget
        self._by_resolved_path: dict[str, StreamedLedgerEntryV2] = {}
        self._ledger_kinds_by_resolved_path: dict[str, set[str]] = {}

    def seed(self, resolved: Path, observation: StreamedLedgerEntryV2) -> None:
        self._by_resolved_path[str(resolved)] = observation

    def observe(self, resolved: Path) -> StreamedLedgerEntryV2 | None:
        """Returns `None` only when the unique-path budget is exhausted
        AND this resolved path was never seeded/observed before --
        signals the caller to stop treating this claim as evaluated."""

        key = str(resolved)
        if key in self._by_resolved_path:
            return self._by_resolved_path[key]
        if not self._budget.debit_unique_path():
            return None
        result = _observe_streamed_ledger_entry_v2(resolved, self._budget)
        self._by_resolved_path[key] = result
        return result

    def register_claim_ledger(self, resolved: Path, ledger: str) -> bool:
        """Associates `ledger` with this resolved path's claim set.
        Returns `True` iff, after this association, the resolved path
        has now been claimed by MORE THAN ONE distinct ledger kind --
        the fail-closed signal for a cross-ledger resolved-identity
        conflict. Independent of content observation/cache hits: the
        association is about declared-claim provenance, not about
        whether the underlying file was readable/matching."""

        kinds = self._ledger_kinds_by_resolved_path.setdefault(str(resolved), set())
        kinds.add(ledger)
        return len(kinds) > 1


# --- 2. Canonical observation plan ----------------------------------------


@dataclass(frozen=True)
class _LedgerClaimV2:
    ledger: str  # "target_owned" | "generated_file"
    relative_path: str
    declared_sha256: str


def _compile_observation_plan_v2(receipt: TargetInstallReceiptV2) -> tuple[_LedgerClaimV2, ...]:
    """Compile every declared claim from BOTH ledgers before any I/O.
    `TargetInstallReceiptV2` (`#203-C1`) already guarantees the two
    ledgers are disjoint by construction -- this traversal order exists
    for DETERMINISM (independent of raw JSON member order), not to
    detect an overlap that cannot exist on any receipt this module can
    hold."""

    claims = [
        _LedgerClaimV2("target_owned", path, digest) for path, digest in receipt.target_owned_file_hashes.items()
    ] + [_LedgerClaimV2("generated_file", path, digest) for path, digest in receipt.generated_file_hashes.items()]
    claims.sort(key=lambda claim: (claim.relative_path, claim.ledger))
    return tuple(claims)


@dataclass
class _LedgerDispositionV2:
    total_claims: int = 0
    evaluated_claims: int = 0
    failure_reason: str | None = None


_MISSING_REASON_BY_LEDGER_V2 = {
    "target_owned": TARGET_OWNED_MISSING_REASON_V2,
    "generated_file": GENERATED_FILE_MISSING_REASON_V2,
}
_NOT_A_REGULAR_FILE_REASON_BY_LEDGER_V2 = {
    "target_owned": TARGET_OWNED_NOT_A_REGULAR_FILE_REASON_V2,
    "generated_file": GENERATED_FILE_NOT_A_REGULAR_FILE_REASON_V2,
}
_UNREADABLE_REASON_BY_LEDGER_V2 = {
    "target_owned": TARGET_OWNED_UNREADABLE_REASON_V2,
    "generated_file": GENERATED_FILE_UNREADABLE_REASON_V2,
}
_DRIFT_REASON_BY_LEDGER_V2 = {
    "target_owned": TARGET_OWNED_DRIFT_REASON_V2,
    "generated_file": GENERATED_FILE_DRIFT_REASON_V2,
}
_CHECK_NAME_BY_LEDGER_V2 = {
    "target_owned": TARGET_OWNED_INTEGRITY_CHECK_V2,
    "generated_file": GENERATED_FILE_INTEGRITY_CHECK_V2,
}


def _evaluate_observation_plan_v2(
    *, target_root_real: Path, claims: tuple[_LedgerClaimV2, ...], registry: _ObservationRegistryV2, budget: _LedgerObservationBudgetV2
) -> tuple[bool, dict[str, _LedgerDispositionV2], bool]:
    """Evaluate the compiled claims IN ORDER, debiting the aggregate
    claim budget per claim. Returns `(budget_exhausted, dispositions,
    cross_ledger_conflict)`. Per-ledger completion/first-failure is
    tracked independently so a ledger that finished before the other
    exhausts the budget keeps its own verdict -- see the module
    docstring's "BUDGET EXHAUSTION LOSES NO EVIDENCE".

    `cross_ledger_conflict` is set as soon as ANY resolved path is found
    claimed by more than one distinct ledger kind (`#203-C2` Codex Round
    1, P2-A) -- registered for EVERY successfully-resolved claim,
    independent of that claim's own content observation, so the
    relation is detected purely from declared-claim provenance."""

    dispositions: dict[str, _LedgerDispositionV2] = {
        "target_owned": _LedgerDispositionV2(),
        "generated_file": _LedgerDispositionV2(),
    }
    for claim in claims:
        dispositions[claim.ledger].total_claims += 1

    cross_ledger_conflict = False

    for claim in claims:
        if not budget.debit_claim():
            return True, dispositions, cross_ledger_conflict

        disposition = dispositions[claim.ledger]
        resolved, resolution_reason = _resolve_ledger_path_v2(target_root_real, claim.relative_path)
        if resolved is None:
            if disposition.failure_reason is None:
                disposition.failure_reason = resolution_reason
            disposition.evaluated_claims += 1
            continue

        if registry.register_claim_ledger(resolved, claim.ledger):
            cross_ledger_conflict = True

        observation = registry.observe(resolved)
        if observation is None:
            # Unique-path budget exhausted on a genuinely new path --
            # this claim was never evaluated; stop the whole plan.
            return True, dispositions, cross_ledger_conflict

        if isinstance(observation, (PathResolutionFailure, Missing, NonRegular, MetadataUnreadable, ReadUnreadable)):
            if disposition.failure_reason is None:
                if isinstance(observation, PathResolutionFailure):
                    disposition.failure_reason = observation.reason
                elif isinstance(observation, Missing):
                    disposition.failure_reason = _MISSING_REASON_BY_LEDGER_V2[claim.ledger]
                elif isinstance(observation, NonRegular):
                    disposition.failure_reason = _NOT_A_REGULAR_FILE_REASON_BY_LEDGER_V2[claim.ledger]
                else:
                    disposition.failure_reason = _UNREADABLE_REASON_BY_LEDGER_V2[claim.ledger]
            disposition.evaluated_claims += 1
        elif isinstance(observation, ResourceLimitExceeded):
            # Not a counterexample -- we simply do not know. Does not set
            # failure_reason; this claim (and the whole plan) stops here.
            return True, dispositions, cross_ledger_conflict
        else:
            assert isinstance(observation, DigestFile)
            if observation.digest != claim.declared_sha256 and disposition.failure_reason is None:
                disposition.failure_reason = _DRIFT_REASON_BY_LEDGER_V2[claim.ledger]
            disposition.evaluated_claims += 1

        if budget.exhausted:
            return True, dispositions, cross_ledger_conflict

    return False, dispositions, cross_ledger_conflict


# --- 3. Artifact/parse layer ------------------------------------------------


def _load_receipt_v2(receipt_path: Path) -> tuple[TargetInstallReceiptV2 | None, ValidateCheckV2, BoundedArtifactV2]:
    observation = _observe_bounded_artifact_v2(receipt_path)
    if isinstance(observation, (Missing, NonRegular)):
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_MISSING_REASON_V2), observation
    if isinstance(observation, (MetadataUnreadable, ReadUnreadable)):
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_UNREADABLE_REASON_V2), observation
    if isinstance(observation, ResourceLimitExceeded):
        return (
            None,
            ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_RESOURCE_LIMIT_EXCEEDED_REASON_V2),
            observation,
        )
    assert isinstance(observation, BufferedFile)
    try:
        receipt = load_target_install_receipt_bytes_v2(observation.content)
    except TargetInstallReceiptLoadErrorV2:
        return None, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, RECEIPT_INVALID_REASON_V2), observation
    return receipt, ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_PASS_V2, None), observation


def _load_profile_v2(profile_path: Path) -> tuple[TargetProfileV2 | None, ValidateCheckV2, BoundedArtifactV2]:
    observation = _observe_bounded_artifact_v2(profile_path)
    if isinstance(observation, (Missing, NonRegular)):
        return None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_MISSING_REASON_V2), observation
    if isinstance(observation, (MetadataUnreadable, ReadUnreadable)):
        return None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_UNREADABLE_REASON_V2), observation
    if isinstance(observation, ResourceLimitExceeded):
        return (
            None,
            ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_RESOURCE_LIMIT_EXCEEDED_REASON_V2),
            observation,
        )
    assert isinstance(observation, BufferedFile)
    try:
        raw_text = observation.content.decode("utf-8")
    except UnicodeDecodeError:
        return None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, PROFILE_UNREADABLE_REASON_V2), observation
    try:
        # Bytes already captured under containment; hand TEXT to the
        # loader -- never a root path it would re-traverse itself.
        profile = load_target_profile_text_v2(raw_text)
    except TargetProfileLoadErrorV2 as exc:
        reason = (
            PROFILE_UNREADABLE_REASON_V2
            if exc.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2
            else PROFILE_INVALID_REASON_V2
        )
        return None, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, reason), observation
    return profile, ValidateCheckV2(PROFILE_CHECK_V2, STATUS_PASS_V2, None), observation


def _profile_hash_check_v2(*, receipt: TargetInstallReceiptV2, profile: TargetProfileV2) -> ValidateCheckV2:
    if receipt.target_profile_hash != compute_profile_hash_v2(profile):
        return ValidateCheckV2(PROFILE_HASH_CHECK_V2, STATUS_FAIL_V2, PROFILE_HASH_MISMATCH_REASON_V2)
    return ValidateCheckV2(PROFILE_HASH_CHECK_V2, STATUS_PASS_V2, None)


def _profile_identity_check_v2(*, receipt: TargetInstallReceiptV2, profile: TargetProfileV2) -> ValidateCheckV2:
    if profile.identity.repo not in {receipt.target_repo, SEED_PROFILE_IDENTITY_PLACEHOLDER_V2}:
        return ValidateCheckV2(PROFILE_IDENTITY_CHECK_V2, STATUS_FAIL_V2, PROFILE_IDENTITY_MISMATCH_REASON_V2)
    return ValidateCheckV2(PROFILE_IDENTITY_CHECK_V2, STATUS_PASS_V2, None)


def _root_identity_check_v2(*, receipt: TargetInstallReceiptV2) -> ValidateCheckV2:
    expected = compute_portable_target_root_identity_v2(target_repo=receipt.target_repo)
    if receipt.portable_target_root_identity != expected:
        return ValidateCheckV2(ROOT_IDENTITY_CHECK_V2, STATUS_FAIL_V2, ROOT_IDENTITY_MISMATCH_REASON_V2)
    return ValidateCheckV2(ROOT_IDENTITY_CHECK_V2, STATUS_PASS_V2, None)


def _ledger_checks_v2(
    *, target_root_real: Path, receipt: TargetInstallReceiptV2, receipt_path: Path, receipt_observation: BoundedArtifactV2,
    profile_path: Path | None, profile_observation: BoundedArtifactV2 | None,
) -> tuple[ValidateCheckV2, ValidateCheckV2 | None, ValidateCheckV2 | None, ValidateCheckV2 | None]:
    """Returns `(observation_budget, target_owned_integrity, generated_
    file_integrity, cross_ledger_alias_separation)`. Seeds the registry
    with the receipt's own and (if resolved) the profile's own artifact
    observation before compiling/evaluating the plan, so a ledger claim
    naming either path never rereads it.

    A ledger's check is OMITTED (`None`), not emitted with `unavailable`
    status, when the aggregate budget ran out before that ledger
    finished and no counterexample was found in it -- ABSENT, mirroring
    exactly how `receipt`/`profile`/etc. are already omitted (never
    emitted `unavailable`) when `aiops_snapshot` fails. `unavailable`
    status stays reserved for the 6 permanently upstream-owned
    dimensions; a budget-limited ledger is a DIFFERENT, situational kind
    of "not decided", disclosed instead through `observation_budget`
    itself failing. `cross_ledger_alias_separation` follows the SAME
    ABSENT-under-incompleteness discipline, applied to the cross-ledger
    relation rather than either individual ledger."""

    budget = _LedgerObservationBudgetV2(
        max_claims=_DEFAULT_MAX_LEDGER_CLAIMS_V2,
        max_unique_resolved_paths=_DEFAULT_MAX_UNIQUE_LEDGER_PATHS_V2,
        max_total_bytes=_DEFAULT_MAX_TOTAL_LEDGER_BYTES_V2,
    )

    # `#203-C2` Codex Round 1, P2-B: the combined claim COUNT is already
    # available from the parsed receipt's own maps -- admission against
    # `max_claims` happens on that count BEFORE `_LedgerClaimV2` objects
    # are constructed and sorted for the whole combined set. A receipt
    # may legitimately declare far more than `max_claims` short claims
    # while staying well under the receipt artifact's own byte ceiling;
    # materializing/sorting the full claim set first would do work
    # proportional to every declared claim before the budget ever gets a
    # chance to refuse.
    total_claim_count = len(receipt.target_owned_file_hashes) + len(receipt.generated_file_hashes)
    if total_claim_count > budget.max_claims:
        budget.exhausted = True
        return (
            ValidateCheckV2(OBSERVATION_BUDGET_CHECK_V2, STATUS_FAIL_V2, OBSERVATION_BUDGET_EXCEEDED_REASON_V2),
            None,
            None,
            None,
        )

    registry = _ObservationRegistryV2(budget)
    # Seed with the paths the containment authority ALREADY returned.
    # These used to call `.resolve()` again here, which was both a second
    # filesystem operation on an already-resolved path and -- because it
    # sat outside every typed boundary -- a third way an `OSError` could
    # escape `run_validate_v2` as a traceback. Re-deriving a path the
    # authority already resolved is the precise anti-pattern
    # `resolve_within_target_root_v2`'s own contract warns about: read
    # through the exact `Path` it returned, never a separately re-derived
    # one, or the check and the use are bound only by timing coincidence.
    registry.seed(receipt_path, _artifact_to_ledger_observation_v2(receipt_observation))
    if profile_path is not None and profile_observation is not None:
        registry.seed(profile_path, _artifact_to_ledger_observation_v2(profile_observation))

    claims = _compile_observation_plan_v2(receipt)
    exhausted, dispositions, cross_ledger_conflict = _evaluate_observation_plan_v2(
        target_root_real=target_root_real, claims=claims, registry=registry, budget=budget
    )

    def _ledger_check(ledger: str) -> ValidateCheckV2 | None:
        disposition = dispositions[ledger]
        name = _CHECK_NAME_BY_LEDGER_V2[ledger]
        if disposition.failure_reason is not None:
            return ValidateCheckV2(name, STATUS_FAIL_V2, disposition.failure_reason)
        if disposition.evaluated_claims >= disposition.total_claims:
            return ValidateCheckV2(name, STATUS_PASS_V2, None)
        return None

    budget_check = (
        ValidateCheckV2(OBSERVATION_BUDGET_CHECK_V2, STATUS_FAIL_V2, OBSERVATION_BUDGET_EXCEEDED_REASON_V2)
        if exhausted
        else ValidateCheckV2(OBSERVATION_BUDGET_CHECK_V2, STATUS_PASS_V2, None)
    )

    if cross_ledger_conflict:
        cross_ledger_check = ValidateCheckV2(
            CROSS_LEDGER_ALIAS_CHECK_V2, STATUS_FAIL_V2, CROSS_LEDGER_ALIAS_CONFLICT_REASON_V2
        )
    elif len(claims) == 0 or not exhausted:
        cross_ledger_check = ValidateCheckV2(CROSS_LEDGER_ALIAS_CHECK_V2, STATUS_PASS_V2, None)
    else:
        cross_ledger_check = None

    return budget_check, _ledger_check("target_owned"), _ledger_check("generated_file"), cross_ledger_check


# --- 4. Report shape and top-level orchestration --------------------------


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
        """No EMITTED/APPLICABLE check has status `fail`. Deliberately
        not "everything was proven": `unavailable` neither fails nor
        passes, and an ABSENT check (never emitted) is not read as
        passing either."""

        return not any(check.status == STATUS_FAIL_V2 for check in self.checks)

    @property
    def unvalidated_capabilities(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if check.status == STATUS_UNAVAILABLE_V2)


class ValidateReportConstructionErrorV2(Exception):
    """The finalizer was handed observed checks that the declared check domain
    does not sanction. An internal programmer error in THIS module, never a
    diagnosable target state -- so it is an exception, not a reason-coded
    check, exactly like every other bug-class failure here."""


def _finalize_validate_checks_v2(observed: tuple[ValidateCheckV2, ...]) -> tuple[ValidateCheckV2, ...]:
    """The ONLY sanctioned construction of a report's final checks tuple.

    Every report must pass through here, so the declared domain is a causal
    authority over the runtime rather than a description of it:

        O(x) subset of L
        R(x) = Canonicalize(O(x) union U)
        U subset of R(x)     and     R(x) subset of D

    `Canonicalize` orders by the authority's own declaration order, which is
    why check ordering is decided in exactly one place. Fails closed rather
    than emitting a report the domain does not sanction -- an unknown or
    duplicated identity, a caller fabricating an `unvalidated` row the
    authority alone may emit, or a locally evaluable check smuggling
    `unavailable` status (which would silently widen the disclosed
    authority boundary)."""

    spec_by_name = validate_spec_by_name_v2()
    locally_evaluable = set(validate_locally_evaluable_names_v2())

    seen: set[str] = set()
    for check in observed:
        spec = spec_by_name.get(check.name)
        if spec is None:
            raise ValidateReportConstructionErrorV2(
                f"observed check {check.name!r} is not in the declared validate check domain"
            )
        if check.name in seen:
            raise ValidateReportConstructionErrorV2(f"observed check {check.name!r} emitted more than once")
        seen.add(check.name)
        if spec.evaluation_class is ValidateEvaluationClassV2.UNVALIDATED:
            raise ValidateReportConstructionErrorV2(
                f"observed check {check.name!r} is structurally UNVALIDATED; only the authority may emit it"
            )
        if check.name in locally_evaluable and check.status == STATUS_UNAVAILABLE_V2:
            raise ValidateReportConstructionErrorV2(
                f"locally evaluable check {check.name!r} was emitted with status {STATUS_UNAVAILABLE_V2!r}"
            )

    unvalidated = tuple(
        ValidateCheckV2(spec.name, STATUS_UNAVAILABLE_V2, spec.unvalidated_reason_code)
        for spec in validate_unvalidated_specs_v2()
    )
    index = validate_check_index_v2()
    return tuple(sorted((*observed, *unvalidated), key=lambda check: index[check.name]))


def _resolve_artifact_path_v2(aiops_dir: Path, target_root_real: Path, filename: str) -> tuple[Path | None, str | None]:
    return _resolve_contained_path_v2(target_root_real, aiops_dir / filename)


_ROOT_REASON_BY_OBSERVATION_V2 = {
    Missing: TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2,
    NonRegular: TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2,
    MetadataUnreadable: TARGET_ROOT_UNREADABLE_REASON_V2,
}
_AIOPS_REASON_BY_OBSERVATION_V2 = {
    Missing: AIOPS_MISSING_REASON_V2,
    NonRegular: AIOPS_NOT_A_DIRECTORY_REASON_V2,
    MetadataUnreadable: AIOPS_UNREADABLE_REASON_V2,
}


def _collect_checks_v2(target_root: Path) -> tuple[list[ValidateCheckV2], str | None]:
    root_resolved, root_observation = _observe_root_v2(target_root)
    if isinstance(root_observation, PathResolutionFailure):
        # Honour the reason the variant CARRIES rather than re-deriving a
        # constant here: the observer distinguishes a malformed link
        # structure from an inaccessible filesystem, and substituting one
        # code for both at the consumer would silently collapse them --
        # the exact class of information loss the typed algebra exists to
        # prevent.
        return [ValidateCheckV2(TARGET_ROOT_CHECK_V2, STATUS_FAIL_V2, root_observation.reason)], None
    assert root_resolved is not None
    target_root_real_str = str(root_resolved)
    if not isinstance(root_observation, DirectoryReady):
        reason = _ROOT_REASON_BY_OBSERVATION_V2[type(root_observation)]
        return [ValidateCheckV2(TARGET_ROOT_CHECK_V2, STATUS_FAIL_V2, reason)], target_root_real_str

    checks: list[ValidateCheckV2] = [ValidateCheckV2(TARGET_ROOT_CHECK_V2, STATUS_PASS_V2, None)]
    target_root_real = root_resolved

    aiops_dir, aiops_observation = _observe_aiops_v2(target_root_real)
    if isinstance(aiops_observation, PathResolutionFailure):
        checks.append(ValidateCheckV2(AIOPS_SNAPSHOT_CHECK_V2, STATUS_FAIL_V2, aiops_observation.reason))
        return checks, target_root_real_str
    if not isinstance(aiops_observation, DirectoryReady):
        reason = _AIOPS_REASON_BY_OBSERVATION_V2[type(aiops_observation)]
        checks.append(ValidateCheckV2(AIOPS_SNAPSHOT_CHECK_V2, STATUS_FAIL_V2, reason))
        return checks, target_root_real_str
    checks.append(ValidateCheckV2(AIOPS_SNAPSHOT_CHECK_V2, STATUS_PASS_V2, None))
    assert aiops_dir is not None

    receipt_path, receipt_path_reason = _resolve_artifact_path_v2(aiops_dir, target_root_real, _RECEIPT_FILENAME_V2)
    profile_path, profile_path_reason = _resolve_artifact_path_v2(aiops_dir, target_root_real, _PROFILE_FILENAME_V2)

    receipt: TargetInstallReceiptV2 | None = None
    receipt_observation: BoundedArtifactV2 | None = None
    if receipt_path is not None:
        receipt, receipt_check, receipt_observation = _load_receipt_v2(receipt_path)
    else:
        receipt_check = ValidateCheckV2(RECEIPT_CHECK_V2, STATUS_FAIL_V2, receipt_path_reason)
    checks.append(receipt_check)

    profile: TargetProfileV2 | None = None
    profile_observation: BoundedArtifactV2 | None = None
    if profile_path is not None:
        profile, profile_check, profile_observation = _load_profile_v2(profile_path)
    else:
        profile_check = ValidateCheckV2(PROFILE_CHECK_V2, STATUS_FAIL_V2, profile_path_reason)
    checks.append(profile_check)

    if receipt is not None and profile is not None:
        checks.append(_profile_hash_check_v2(receipt=receipt, profile=profile))
        checks.append(_profile_identity_check_v2(receipt=receipt, profile=profile))

    if receipt is not None:
        checks.append(_root_identity_check_v2(receipt=receipt))
        assert receipt_path is not None and receipt_observation is not None
        budget_check, target_owned_check, generated_file_check, cross_ledger_check = _ledger_checks_v2(
            target_root_real=target_root_real,
            receipt=receipt,
            receipt_path=receipt_path,
            receipt_observation=receipt_observation,
            profile_path=profile_path,
            profile_observation=profile_observation,
        )
        checks.append(budget_check)
        if target_owned_check is not None:
            checks.append(target_owned_check)
        if generated_file_check is not None:
            checks.append(generated_file_check)
        if cross_ledger_check is not None:
            checks.append(cross_ledger_check)

    return checks, target_root_real_str


def run_validate_v2(*, target_root: Path) -> ValidateReportV2:
    """Validate an installed target pack using only what the target has.

    Total over the enumerated target-state failure domain: every
    diagnosable target state becomes a reason-coded check in the
    returned report, never an uncaught exception. Internal programmer
    errors (a bug in this module itself) remain exceptions.
    """

    observed, target_root_real = _collect_checks_v2(target_root)
    return ValidateReportV2(
        target_root_real=target_root_real,
        checks=_finalize_validate_checks_v2(tuple(observed)),
    )
