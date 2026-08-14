# AgentReview v1/v2 compatibility matrix

**Status:** `CURRENT | V2 DEVELOPMENT` — linha sucessora em desenvolvimento; não é GA, não é default e não é required check. Estado atual: [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

Refs #133 (parent #89, epic #80, roadmap #46). Describes how v1 and v2
coexist during the shadow adoption period. Neither version is being
retired by this document; retirement is a separate, future release.

## Core rule

```text
v1 and v2 artifacts, envelopes, and gates are never mixed.
A target consumes exactly one version's full pipeline per run.
```

There is no partial-upgrade path within a single review run: a target
either runs the v1 pipeline end to end (intake → chunk plan → PR brief →
chunk response → synthesis → `review-quality-gate.json`) or the v2
pipeline end to end (profile load → manifest assembly → payload build →
bound chunk response → synthesis → `ReviewReadinessV2`). A v1 chunk
response is never fed into v2 synthesis, and a v2 manifest is never
consumed by the v1 quality gate.

## Side-by-side matrix

| Concern | v1 | v2 |
|---|---|---|
| Identity binding | PR brief + chunk plan, not cryptographically verified end to end | `ManifestV2`/`ChunkPayloadV2` with verified payload/response hash binding (#83) |
| Profile source | `TargetProfile` (v1 schema) | `TargetProfileV2` (#85), migratable from v1 via `profile_migration_v1_v2.py` — migration is explicit, non-destructive, and never automatic |
| Coverage | Reported, not contractually enforced | `must_review` coverage is a contract; `blocked_pipeline`/`manual_required` on incomplete coverage per policy |
| Lifecycle | Implicit; no formal disposition state machine | `FindingLifecycleRecordV2` with `NEW`/`CONFIRMED`/etc. dispositions and explicit precedence rules |
| Readiness decision | `review-quality-gate.json`, normalized outcomes | `ReviewReadinessV2` via `compute_readiness_decision_v2`, with documented precedence (e.g. P3-only findings never block — see `agentescala-contract-p3-finding-still-ready`) |
| Stale/cross-run detection | Partial | Contractual: stale HEAD, cross-run, cross-target, and mixed v1/v2 all fail closed |
| Dependency footprint | Full AIOps runtime importable | `requirements-agent-review.lock` — pydantic + PyYAML only, no FastAPI/Uvicorn/SQLAlchemy/DB driver |
| Toolrepo pin | Full 40-char SHA (already enforced) | Same requirement, unchanged |
| External observation (Codex) | Not integrated | `ExternalObservationV2` / `AiopsFindingReferenceV2`, explicitly outside `contracts_v2.py`'s registry, never authoritative over lifecycle/readiness (#88) |
| Quality gate authority | `review-quality-gate.json` is sole authority | `ReviewReadinessV2` is sole authority; Codex output (any lane) is never a required check |

## Migration path (per target, opt-in only)

```text
v1 stable (default, unchanged)
  → v2 opt-in/shadow (this release)
    → v2 advisory (per-target decision, after A6 observation window)
      → v2 default (per-target decision, separate grant)
        → v1 retirement (future release, separate decision)
```

Every arrow above is a distinct, target-owned decision with its own issue
and grant. This release (`v0.21.0`) only ever reaches the first arrow.

## What a target must never do

- Run v1 and v2 concurrently against the *same* PR/HEAD and merge their
  outputs into one decision.
- Treat a v2 shadow observation as input to the v1 quality gate, or a v1
  finding as input to v2 synthesis.
- Promote v2 to required/default because v1 and v2 happened to agree on a
  sample of runs — concordance between pipelines is not proof, exactly as
  concordance between AIOps and Codex is not proof (#88's own rule).

## Artifact naming (no collision)

v1 and v2 artifacts use disjoint schema IDs (`agent-review.chunk-response.v1`
vs `agent-review.chunk-response.v2`, etc.) and disjoint file names where both
could coexist in the same run directory, so tooling that inspects a run's
artifacts can always determine which version produced them without
ambiguity.
