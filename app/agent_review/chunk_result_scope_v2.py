"""Shared, neutral revalidation of ``ParsedChunkResultV2`` against the
``ManifestV2`` that planned it (issue #107 hardening, round 3).

``ParsedChunkResultV2`` (parser_v2.py) is, by its own module's docstring,
"a plain data value, freely constructible" -- unlike ``BoundChunkResponseV2``,
it carries no seal proving it actually came from
``parse_bound_chunk_response_v2``. ``isinstance`` and a matching
``run_id``/``chunk_id`` prove nothing about whether a result's HEAD,
coverage, or findings are consistent with what the manifest's own chunk
actually describes.

Two independent public v2 entry points consume a
``Sequence[ParsedChunkResultV2]``: ``synthesis_v2.synthesize_chunk_results_v2``
and ``lifecycle_v2.aggregate_finding_lifecycle_v2``. Both must revalidate
every result against their own manifest before trusting any of its claims,
regardless of whether a caller already invoked the other -- an earlier
revision of this hardening lived only inside ``synthesis_v2``, leaving
``aggregate_finding_lifecycle_v2`` safe only by call-site convention, not by
its own contract, while its docstring incorrectly claimed the opposite.

``validate_chunk_results_scope_v2`` is the single, neutral authority both
modules call directly. There is deliberately no "trusted"/"already
validated" flag to skip it -- a caller cannot opt out of revalidation by
claiming to have done it already. Where the canonical composed path
(``synthesize_chunk_results_v2`` calling into lifecycle aggregation) needs
to avoid re-running this same check twice, that is solved by lifecycle_v2
exposing a private core that accepts an already-validated mapping, never by
weakening or bypassing this function.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.agent_review.contracts_v2 import ChunkFindingV2
from app.agent_review.manifest_v2 import FragmentV2, ManifestV2
from app.agent_review.parser_v2 import ParsedChunkResultV2
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

CROSS_RUN_CHUNK_RESULT_REASON_V2 = "cross_run_chunk_result"
INVALID_CHUNK_RESULT_TYPE_REASON_V2 = "invalid_chunk_result_type"
DUPLICATE_CHUNK_RESULT_REASON_V2 = "duplicate_chunk_result"
UNKNOWN_CHUNK_RESULT_REASON_V2 = "unknown_chunk_result"
CHUNK_RESULT_HEAD_MISMATCH_REASON_V2 = "chunk_result_head_mismatch"
SYNTHESIS_EVALUATED_HEAD_MISMATCH_REASON_V2 = "synthesis_evaluated_head_mismatch"
CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2 = "chunk_result_coverage_scope_mismatch"
FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2 = "finding_outside_chunk_scope"


class ChunkResultScopeError(ExpectedOperationalRefusalV2, ValueError):
    """Raised when a ``ParsedChunkResultV2`` fails scope revalidation
    against its manifest. Carries a stable ``reason_code`` only -- never
    chunk content, findings, or manifest data."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _finding_within_chunk_fragments_v2(
    *,
    fragments_by_id: Mapping[str, FragmentV2],
    chunk_fragment_ids: Sequence[str],
    finding: ChunkFindingV2,
) -> bool:
    if finding.line_start is None or finding.line_end is None:
        return True
    for fragment_id in chunk_fragment_ids:
        fragment = fragments_by_id[fragment_id]
        if fragment.path != finding.file_path:
            continue
        if fragment.new_range.start <= finding.line_start and finding.line_end <= fragment.new_range.end:
            return True
    return False


def validate_chunk_results_scope_v2(
    *,
    manifest: ManifestV2,
    chunk_results: Sequence[ParsedChunkResultV2],
    evaluated_head_sha: str,
) -> Mapping[str, ParsedChunkResultV2]:
    """Revalidate every ``ParsedChunkResultV2`` against ``manifest``'s own
    structure, returning a ``chunk_id``-keyed mapping of the validated
    results (in first-seen order) once every check passes.

    A genuinely bound result (``parser_v2.parse_bound_chunk_response_v2``)
    always already satisfies every check here -- ``BoundChunkResponseV2``
    and ``validate_response_binding_v2`` guarantee it upstream. This
    function exists because ``ParsedChunkResultV2`` itself carries no proof
    of that upstream binding: it is a plain, freely constructible value, so
    a hand-built or malformed instance sharing this manifest's ``run_id``
    must still be caught here, not assumed correct from its type alone.

    Every caller of this function -- there must never be more than one
    revalidation authority for this data -- fails closed identically:
    wrong-typed input, cross-run input, duplicate ``chunk_id``, unknown
    ``chunk_id``, HEAD mismatch (on the result itself or on the caller's
    own ``evaluated_head_sha``), coverage claiming a path outside the
    chunk's own manifest scope, or a finding (by path or line range)
    outside that scope.
    """

    if evaluated_head_sha != manifest.identity.head_sha:
        raise ChunkResultScopeError(SYNTHESIS_EVALUATED_HEAD_MISMATCH_REASON_V2)

    fragments_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}
    chunks_by_id = {chunk.chunk_id: chunk for chunk in manifest.chunks}
    chunk_paths_by_id = {
        chunk.chunk_id: {fragments_by_id[fid].path for fid in chunk.fragment_ids} for chunk in manifest.chunks
    }
    must_review_files = set(manifest.must_review_files)

    results_by_chunk_id: dict[str, ParsedChunkResultV2] = {}
    for result in chunk_results:
        if not isinstance(result, ParsedChunkResultV2):
            raise ChunkResultScopeError(INVALID_CHUNK_RESULT_TYPE_REASON_V2)
        if result.run_id != manifest.run_id:
            raise ChunkResultScopeError(CROSS_RUN_CHUNK_RESULT_REASON_V2)
        if result.chunk_id in results_by_chunk_id:
            raise ChunkResultScopeError(DUPLICATE_CHUNK_RESULT_REASON_V2)
        if result.chunk_id not in chunks_by_id:
            raise ChunkResultScopeError(UNKNOWN_CHUNK_RESULT_REASON_V2)
        if result.head_sha != manifest.identity.head_sha:
            raise ChunkResultScopeError(CHUNK_RESULT_HEAD_MISMATCH_REASON_V2)

        chunk = chunks_by_id[result.chunk_id]
        chunk_paths = chunk_paths_by_id[result.chunk_id]
        if set(result.coverage.expected_files) != chunk_paths:
            raise ChunkResultScopeError(CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2)
        if set(result.coverage.must_review_files) != (chunk_paths & must_review_files):
            raise ChunkResultScopeError(CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2)

        for finding in result.findings:
            if finding.file_path not in chunk_paths:
                raise ChunkResultScopeError(FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2)
            if not _finding_within_chunk_fragments_v2(
                fragments_by_id=fragments_by_id, chunk_fragment_ids=chunk.fragment_ids, finding=finding
            ):
                raise ChunkResultScopeError(FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2)

        results_by_chunk_id[result.chunk_id] = result

    return results_by_chunk_id
