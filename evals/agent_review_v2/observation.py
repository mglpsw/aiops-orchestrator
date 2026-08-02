"""External review observation schema (issue #88, "Correlação de findings").

A normalized representation of an OBSERVATION from a source outside the
canonical AIOps AgentReview engine -- Codex (local CLI or GitHub shadow
review) or a human reviewer. Deliberately NOT a `ContractV2Model`: it lives
outside `app/agent_review/contracts_v2.py`'s registry on purpose, carries no
hash, and is never accepted as input by `synthesize_chunk_results_v2`,
`compute_readiness_decision_v2`, or `emit_review_readiness_v2`. Per the
issue's own rule: "Essa estrutura serve para benchmark/telemetria. Ela não
pode, nesta issue, criar `confirmed` no lifecycle nem alterar readiness."

This module only defines the shape and a pure comparison function; it
performs no I/O, no network call, and does not itself invoke Codex or any
provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExternalObservationV2(BaseModel):
    """Plain, freely constructible external observation. Strict/closed like
    every contract in this codebase, but explicitly NOT a `ContractV2Model`
    -- it never enters that registry, is never hashed, and is never bound
    to a canonical run identity the way `ChunkPayloadV2`/`ManifestV2` are."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source: Literal["codex_local", "codex_github", "human"]
    repo: str
    pr_number: int
    head_sha: str
    file_path: str
    fragment_id: str | None = None
    severity_claimed: Literal["P0", "P1", "P2", "P3"]
    normalized_cause: str
    evidence_summary: str
    disposition: Literal["unverified", "matched", "rejected", "inconclusive"] = "unverified"


class AiopsFindingReferenceV2(BaseModel):
    """The AIOps-canonical side of a comparison: just enough of a real
    `ChunkFindingV2`/`FindingLifecycleRecordV2` pair to match against an
    `ExternalObservationV2` by location -- never the full contract, since
    this module must not need to import canonical hashing machinery to do
    a location-based comparison."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    finding_id: str
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    severity: Literal["P0", "P1", "P2", "P3"]


@dataclass(frozen=True)
class CorrelationResultV2:
    """One external observation's disposition against the AIOps-canonical
    findings for the same repo/PR/HEAD. `matched` never means "confirmed"
    -- it is purely a location/severity correlation for benchmark
    reporting, computed here, and is never written back into any
    `FindingLifecycleRecordV2` or `ReviewReadinessV2`."""

    source: str
    file_path: str
    severity_claimed: str
    disposition: Literal["matched", "rejected", "inconclusive"]
    matched_finding_id: str | None


def correlate_observation_v2(
    observation: ExternalObservationV2, *, aiops_findings: list[AiopsFindingReferenceV2]
) -> CorrelationResultV2:
    """Pure, deterministic correlation by (file_path, severity) -- never by
    exact wording, per the issue's own tolerance rule ("Correspondência deve
    tolerar redação diferente, mas não localização/causa distinta"). A
    genuinely different root cause at the same file/severity cannot be
    distinguished by this simple structural match alone; that is a real,
    accepted limitation of this offline correlation, not silently
    papered over -- disposition stays `inconclusive` whenever the match is
    ambiguous (more than one AIOps finding at the same file+severity)."""

    candidates = [
        f for f in aiops_findings if f.file_path == observation.file_path and f.severity == observation.severity_claimed
    ]
    if len(candidates) == 1:
        return CorrelationResultV2(
            source=observation.source,
            file_path=observation.file_path,
            severity_claimed=observation.severity_claimed,
            disposition="matched",
            matched_finding_id=candidates[0].finding_id,
        )
    if len(candidates) > 1:
        return CorrelationResultV2(
            source=observation.source,
            file_path=observation.file_path,
            severity_claimed=observation.severity_claimed,
            disposition="inconclusive",
            matched_finding_id=None,
        )
    return CorrelationResultV2(
        source=observation.source,
        file_path=observation.file_path,
        severity_claimed=observation.severity_claimed,
        disposition="rejected",
        matched_finding_id=None,
    )
