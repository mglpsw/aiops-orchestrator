"""AgentReview v2 parser: exposes findings only from an already-bound response.

There is intentionally no v1-style normalization/downgrade heuristic here.
v2 findings arrive already strictly typed and schema-validated as
``ChunkFindingV2`` (severity, evidence, file scope, and disposition are all
enforced by ``contracts_v2`` before binding even completes), so this module
has no evidence-shape guessing left to do. Its only job is to refuse to
expose findings from anything but a verified, bound response.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent_review.consumer_v2 import BoundChunkResponseV2
from app.agent_review.contracts_v2 import ChunkCoverageV2, ChunkFindingV2


@dataclass(frozen=True)
class ParsedChunkResultV2:
    """Downstream-facing shape of a bound chunk result.

    Distinct from ``BoundChunkResponseV2`` on purpose: this type is a plain
    data value, freely constructible, meant for consumption by future
    synthesis/readiness work (#84/#86); ``BoundChunkResponseV2`` is the
    sealed proof-of-binding token and should not leak into that surface.
    """

    run_id: str
    chunk_id: str
    head_sha: str
    summary: str
    findings: tuple[ChunkFindingV2, ...]
    coverage: ChunkCoverageV2
    limitations: tuple[str, ...]


def parse_bound_chunk_response_v2(bound: BoundChunkResponseV2) -> ParsedChunkResultV2:
    """The only public entry point in this module that exposes findings.

    Accepts nothing but a ``BoundChunkResponseV2`` produced by
    ``consumer_v2.bind_chunk_response_v2``: a raw envelope, a plain dict, or
    any object that was not produced by that binder is rejected with
    ``TypeError`` before any attribute access happens.
    """

    if not isinstance(bound, BoundChunkResponseV2):
        raise TypeError(
            "parse_bound_chunk_response_v2 requires a BoundChunkResponseV2 "
            "produced by consumer_v2.bind_chunk_response_v2"
        )
    return ParsedChunkResultV2(
        run_id=bound.run_id,
        chunk_id=bound.chunk_id,
        head_sha=bound.head_sha,
        summary=bound.summary,
        findings=bound.findings,
        coverage=bound.coverage,
        limitations=bound.limitations,
    )
