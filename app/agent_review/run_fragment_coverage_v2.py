"""Run-level fragment coverage proof and fail-closed policy (issue #104).

Two grandezas that this module never lets collapse into each other:

    escopo estrutural esperado    = the union of a path's fragments in the manifest
    cobertura semântica provada   = only the fragments explicitly reported as reviewed

``ChunkCoverageV2`` (frozen in #81) is file-granular: a path split across more
than one chunk is, at best, ``partially_reviewed`` in every chunk that carries
it, and ``validate_response_binding_v2`` already forbids a response from
promoting that to ``reviewed``. But nothing upstream of this module could
*represent* fragment-level coverage at all -- ``PipelineDegradationCauseV2``
carries only ``reason_code``/``component``/``detail``, and dedups on
``(reason_code, component)``, so it cannot even distinguish two divided paths
from each other, let alone which of their fragments were touched.

``RunFragmentCoverageReportV2`` is that missing evidence: one entry per path,
naming exactly which of its fragments were assigned, reviewed, partially
reviewed, or missing, and which chunks carried them. The policy this module
enforces -- structurally, in the contract itself, not as documentation a
caller could ignore -- is the one already decided for the first v2 release:

    a path split across more than one chunk can never be "reviewed",
    regardless of what any caller-supplied fragment set claims.

Building a fragment-aware response protocol (payload + envelope + binder +
parser all versioned together) is deliberately out of scope here and left to
a future issue, gated on the calibration evidence #86/#88 produce -- see the
issue body for the full rationale. This module's job is narrower and already
complete without it: publish the contract, and make the fail-closed shape the
only shape a caller can construct.

``PipelineDegradationCauseV2`` remains exactly what it already was: a
summarized projection for readiness (#108), never the evidence of record.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.agent_review.contracts_v2 import ContractV2Model, RelativePath, SafeIdentifier, Sha256
from app.agent_review.manifest_v2 import ManifestV2

RUN_FRAGMENT_COVERAGE_SCHEMA_V2 = "agent-review.run-fragment-coverage.v2"


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Deliberately a local, unmodified copy of the same canonical-JSON rules
    # every other v2 hash in this codebase uses (contracts_v2._canonical_json_bytes,
    # manifest_v2._canonical_json_bytes_v2) -- not a new discipline. No separate
    # sensitive-value scan is applied here, matching manifest_v2's own precedent:
    # every field below is already a validated type (RelativePath, SafeIdentifier,
    # Sha256) whose own AfterValidator already rejects secret-like or local-path
    # content at construction time, so a whole-document re-scan at hash time
    # would be redundant, not a gap.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class FragmentCoverageStatusV2(str, Enum):
    REVIEWED = "reviewed"
    PARTIAL = "partial"
    MISSING = "missing"


class FragmentCoverageReasonV2(str, Enum):
    STRUCTURAL_SPLIT = "structural_split"
    FRAGMENT_DEGRADED = "fragment_degraded"
    CHUNK_UNAVAILABLE = "chunk_unavailable"
    NOT_YET_PROCESSED = "not_yet_processed"


FragmentCoverageStatusValueV2 = Annotated[FragmentCoverageStatusV2, Field(strict=False)]
FragmentCoverageReasonValueV2 = Annotated[FragmentCoverageReasonV2, Field(strict=False)]


class RunFragmentCoverageEntryV2(ContractV2Model):
    """Per-path fragment coverage, independently verifiable from its own
    fields -- no external manifest reference is needed to prove this entry
    is *internally* consistent. Cross-checking it against the manifest it
    actually claims to describe is ``bind_coverage_report_to_manifest_v2``'s
    job, not this model's: a bare Pydantic model has no way to reach out to
    another object at construction time, and conflating "shape is coherent"
    with "shape matches this specific manifest" would blur exactly the
    run/manifest-identity discipline the rest of v2 already relies on.
    """

    path: RelativePath
    expected_fragment_ids: list[Sha256]
    assigned_fragment_ids: list[Sha256]
    reviewed_fragment_ids: list[Sha256]
    partially_reviewed_fragment_ids: list[Sha256]
    missing_fragment_ids: list[Sha256]
    affected_chunk_ids: list[SafeIdentifier]
    status: FragmentCoverageStatusValueV2
    reason_codes: list[FragmentCoverageReasonValueV2]

    @model_validator(mode="after")
    def validate_entry(self) -> RunFragmentCoverageEntryV2:
        for name, values in (
            ("expected_fragment_ids", self.expected_fragment_ids),
            ("assigned_fragment_ids", self.assigned_fragment_ids),
            ("reviewed_fragment_ids", self.reviewed_fragment_ids),
            ("partially_reviewed_fragment_ids", self.partially_reviewed_fragment_ids),
            ("missing_fragment_ids", self.missing_fragment_ids),
            ("affected_chunk_ids", self.affected_chunk_ids),
            ("reason_codes", self.reason_codes),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")

        expected = set(self.expected_fragment_ids)
        assigned = set(self.assigned_fragment_ids)
        reviewed = set(self.reviewed_fragment_ids)
        partial = set(self.partially_reviewed_fragment_ids)
        missing = set(self.missing_fragment_ids)

        if not expected:
            raise ValueError("a path must declare at least one expected fragment")
        if not assigned <= expected:
            raise ValueError("assigned_fragment_ids must be a subset of expected_fragment_ids")
        if not reviewed <= assigned or not partial <= assigned:
            raise ValueError(
                "reviewed_fragment_ids and partially_reviewed_fragment_ids must be assigned fragments"
            )
        if reviewed & partial:
            raise ValueError("reviewed_fragment_ids and partially_reviewed_fragment_ids must be disjoint")
        if (reviewed | partial) & missing:
            raise ValueError("missing_fragment_ids must be disjoint from reviewed/partially_reviewed")
        # A fragment never assigned to any chunk at all (dropped auxiliary
        # content, or simply never planned) must be reported missing here --
        # checked before, and in addition to, the exact-partition invariant
        # below, so an omission of exactly this kind gets its own clear error
        # rather than the generic "does not partition" message.
        if not (expected - assigned) <= missing:
            raise ValueError("every unassigned fragment must be reported missing")
        if (reviewed | partial | missing) != expected:
            raise ValueError(
                "reviewed_fragment_ids, partially_reviewed_fragment_ids and missing_fragment_ids "
                "must exactly partition expected_fragment_ids"
            )

        # The core fail-closed policy: a path whose fragments are spread
        # across more than one chunk can never reach "reviewed", regardless
        # of what reviewed_fragment_ids claims -- there is no fragment-level
        # proof channel through the response protocol today (see module
        # docstring), so a per-fragment "reviewed" claim spanning multiple
        # chunks is not verifiable and must not be trusted structurally.
        divided = len(self.affected_chunk_ids) > 1
        fully_reviewed = reviewed == expected and not partial and not missing
        if fully_reviewed and not divided:
            expected_status = FragmentCoverageStatusV2.REVIEWED
        elif not reviewed and not partial:
            expected_status = FragmentCoverageStatusV2.MISSING
        else:
            expected_status = FragmentCoverageStatusV2.PARTIAL
        if self.status is not expected_status:
            raise ValueError(f"status must be {expected_status.value!r} for this fragment partition")

        if divided and FragmentCoverageReasonV2.STRUCTURAL_SPLIT not in self.reason_codes:
            raise ValueError("a structurally divided path must carry the structural_split reason code")
        if not divided and FragmentCoverageReasonV2.STRUCTURAL_SPLIT in self.reason_codes:
            raise ValueError("structural_split is only valid for a structurally divided path")

        if self.status is FragmentCoverageStatusV2.REVIEWED and self.reason_codes:
            raise ValueError("a fully reviewed path must not carry any reason codes")
        if self.status is not FragmentCoverageStatusV2.REVIEWED and not self.reason_codes:
            raise ValueError("a non-reviewed path must carry at least one reason code")

        return self


class RunFragmentCoverageReportMaterialV2(ContractV2Model):
    schema_id: Literal["agent-review.run-fragment-coverage.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-fragment-coverage"]
    run_id: Sha256
    manifest_hash: Sha256
    paths: list[RunFragmentCoverageEntryV2]

    @model_validator(mode="after")
    def validate_material(self) -> RunFragmentCoverageReportMaterialV2:
        entry_paths = [entry.path for entry in self.paths]
        if len(entry_paths) != len(set(entry_paths)):
            raise ValueError("paths must not contain duplicate path entries")
        return self


def canonical_coverage_report_bytes_v2(
    material: RunFragmentCoverageReportMaterialV2 | Mapping[str, object],
) -> bytes:
    """Hash preimage: every validated field except ``coverage_report_sha256``,
    with ``paths`` sorted by ``path`` regardless of construction order --
    reordering the entries passed to the constructor must never change this
    function's output. Mirrors ``contracts_v2``'s ``*Material*``/hash-carrier
    split (``ChunkPayloadMaterialV2``/``ChunkPayloadV2``) exactly, reused
    here for a new artifact rather than re-derived.
    """

    if isinstance(material, RunFragmentCoverageReportMaterialV2):
        dumped = material.model_dump(mode="json", exclude={"coverage_report_sha256"})
    elif isinstance(material, Mapping):
        raw = dict(material)
        raw.pop("coverage_report_sha256", None)
        dumped = RunFragmentCoverageReportMaterialV2.model_validate(raw).model_dump(mode="json")
    else:
        raise TypeError("coverage report material must be a model or mapping")
    dumped["paths"] = sorted(dumped["paths"], key=lambda entry: entry["path"])
    return _canonical_json_bytes_v2(dumped)


def compute_coverage_report_sha256_v2(
    material: RunFragmentCoverageReportMaterialV2 | Mapping[str, object],
) -> str:
    return hashlib.sha256(canonical_coverage_report_bytes_v2(material)).hexdigest()


class RunFragmentCoverageReportV2(RunFragmentCoverageReportMaterialV2):
    coverage_report_sha256: Sha256

    @model_validator(mode="after")
    def validate_hash(self) -> RunFragmentCoverageReportV2:
        expected = compute_coverage_report_sha256_v2(self)
        if self.coverage_report_sha256 != expected:
            raise ValueError(
                "coverage_report_sha256 does not match the canonical report material"
            )
        return self


class FragmentCoverageBindingError(ValueError):
    """Raised by ``bind_coverage_report_to_manifest_v2``. Carries a stable
    ``reason_code`` only -- never manifest/report content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


RUN_ID_MISMATCH_REASON_V2 = "run_id_mismatch"
MANIFEST_HASH_MISMATCH_REASON_V2 = "manifest_hash_mismatch"
PATH_SET_MISMATCH_REASON_V2 = "path_set_mismatch"
FRAGMENT_SET_MISMATCH_REASON_V2 = "fragment_set_mismatch"
UNKNOWN_CHUNK_REFERENCE_REASON_V2 = "unknown_chunk_reference"
AFFECTED_CHUNK_SET_MISMATCH_REASON_V2 = "affected_chunk_set_mismatch"
DEGRADED_FRAGMENT_CANNOT_BE_REVIEWED_REASON_V2 = "degraded_fragment_cannot_be_reviewed"
DEGRADED_FRAGMENT_NOT_ACCOUNTED_REASON_V2 = "degraded_fragment_not_accounted"


def bind_coverage_report_to_manifest_v2(
    report: RunFragmentCoverageReportV2, manifest: ManifestV2
) -> None:
    """Cross-check a coverage report against the manifest it claims to
    describe. Raises ``FragmentCoverageBindingError`` fail-closed on any
    divergence; returns ``None`` on success.

    This proves the report is not just internally coherent (already
    guaranteed by ``RunFragmentCoverageEntryV2``'s own validator) but
    actually *about* this manifest and this run: identity, the complete set
    of paths, each path's real fragment set, and each path's EXACT chunk
    set -- not merely a subset of chunks that exist somewhere in the
    manifest. This last check closes the gap a bare entry cannot close on
    its own: ``RunFragmentCoverageEntryV2`` computes "is this path
    divided?" purely from the caller-supplied ``affected_chunk_ids`` list
    (it has no manifest to consult), so a caller that under-reports that
    list -- naming only one of a path's two real chunks -- could otherwise
    make a genuinely multi-chunk-split path look undivided, and therefore
    eligible for ``status="reviewed"``, at the entry level alone. Requiring
    exact equality with the manifest's real per-path chunk set here is what
    makes the fail-closed policy hold at the system level, not just inside
    one object considered in isolation. Replay against a different run or
    a different manifest that happens to reuse fragment IDs is rejected
    here too, not assumed safe elsewhere.

    What this function does NOT verify: whether a chunk that carries a
    fragment was actually usable (its response bound and parsed
    successfully). That fact only exists once real chunk results exist
    (issue #107); the manifest alone only proves structural assignment, not
    processing outcome. This function proves the report matches the
    manifest's structure and its degradation causes -- not that every
    reviewed/missing claim was truthfully derived from real responses.
    """

    if report.run_id != manifest.run_id:
        raise FragmentCoverageBindingError(RUN_ID_MISMATCH_REASON_V2)
    if report.manifest_hash != manifest.identity.manifest_hash:
        raise FragmentCoverageBindingError(MANIFEST_HASH_MISMATCH_REASON_V2)

    report_paths = {entry.path for entry in report.paths}
    if report_paths != set(manifest.expected_files):
        raise FragmentCoverageBindingError(PATH_SET_MISMATCH_REASON_V2)

    fragments_by_path: dict[str, set[str]] = {}
    for fragment in manifest.fragments:
        fragments_by_path.setdefault(fragment.path, set()).add(fragment.fragment_id)
    chunk_ids = {chunk.chunk_id for chunk in manifest.chunks}
    # A fragment is assigned to at most one chunk -- manifest_v2's own
    # validator already guarantees this ("a fragment must not be assigned
    # to more than one chunk") -- so this mapping is safe to build as a
    # plain fragment_id -> chunk_id dict, never overwritten ambiguously.
    chunk_by_fragment: dict[str, str] = {}
    for chunk in manifest.chunks:
        for fragment_id in chunk.fragment_ids:
            chunk_by_fragment[fragment_id] = chunk.chunk_id
    degraded_fragment_ids: set[str] = set()
    for cause in manifest.degradation_causes:
        degraded_fragment_ids.update(cause.affected_fragment_ids)

    for entry in report.paths:
        expected = fragments_by_path.get(entry.path, set())
        if set(entry.expected_fragment_ids) != expected:
            raise FragmentCoverageBindingError(FRAGMENT_SET_MISMATCH_REASON_V2)
        if not set(entry.affected_chunk_ids) <= chunk_ids:
            raise FragmentCoverageBindingError(UNKNOWN_CHUNK_REFERENCE_REASON_V2)
        # Not just "these chunk IDs exist somewhere in the manifest" --
        # they must be EXACTLY the chunks that actually carry this path's
        # fragments. A subset-only check would let a caller under-report
        # affected_chunk_ids (omit a real chunk) to make a genuinely
        # multi-chunk-split path look undivided to
        # RunFragmentCoverageEntryV2's own len(affected_chunk_ids) > 1
        # test -- defeating the fail-closed policy at the system level
        # even though the entry's own validator is airtight in isolation.
        real_chunks_for_path = {
            chunk_by_fragment[fragment_id]
            for fragment_id in expected
            if fragment_id in chunk_by_fragment
        }
        if set(entry.affected_chunk_ids) != real_chunks_for_path:
            raise FragmentCoverageBindingError(AFFECTED_CHUNK_SET_MISMATCH_REASON_V2)

        path_degraded = expected & degraded_fragment_ids
        if path_degraded:
            if entry.status is FragmentCoverageStatusV2.REVIEWED:
                raise FragmentCoverageBindingError(DEGRADED_FRAGMENT_CANNOT_BE_REVIEWED_REASON_V2)
            if not path_degraded <= set(entry.missing_fragment_ids):
                raise FragmentCoverageBindingError(DEGRADED_FRAGMENT_NOT_ACCOUNTED_REASON_V2)
