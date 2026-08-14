# AgentReview v2 shadow/advisory rollout specification

**Status:** `CURRENT | V2 DEVELOPMENT` — linha sucessora em desenvolvimento; não é GA, não é default e não é required check. Estado atual: [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

Refs #133 (parent #89, epic #80, roadmap #46). Specifies how a target
repository adopts AgentReview v2 reversibly. This is a **specification**
only — it authorizes no PR, no write, and no configuration change in any
target repository. AgentEscala's adoption is its own execution (A4, refs
#675/#678/#754); InterLeitos's adoption is its own execution (A5, refs #19,
#28–#31). Each requires its own task contract and grant in that target's
own repository.

## Rollout states

```text
not_adopted
  → shadow      (this release's ceiling; v2 runs, observes, never blocks)
    → advisory  (per-target decision, after A6 observation window)
      → default (per-target decision, separate grant)
```

`v0.21.0` never authorizes anything past `shadow` for any target. Per the
benchmark disposition (#88): `allowed_role: shadow`, `advisory_eligibility:
deferred_to_target_observation`.

## Shadow wrapper contract

A target's shadow wrapper for v2 must:

1. **Pin by full SHA or immutable release**, never a floating branch/tag —
   consuming exactly the `v0.21.0` (or later) release this toolrepo
   publishes.
2. **Run alongside, never instead of, the target's existing v1 pipeline**
   (or whatever mechanism the target currently uses). v2's output is
   additional signal, not a replacement decision.
3. **Never gate, block, or annotate the PR as a required check.** v2 shadow
   output is informational only — no status check, no merge block, no
   auto-comment presented as authoritative.
4. **Use a separate, non-privileged evidence job** for the v2 run, with no
   credential/secret access beyond what v2's own offline pipeline needs
   (none, per `requirements-agent-review.lock`'s dependency footprint) —
   consistent with the target's existing secretless-evidence-job pattern
   (AgentEscala #675/#678 already establish this for v1; v2 reuses the same
   boundary, does not weaken it).
5. **Use a separate publisher** from the one that publishes v1's result —
   never let a v2 publish path acquire v1's write scope, or vice versa.
6. **Revalidate PR/HEAD identity immediately before publishing** its own
   observation, exactly as `ExternalObservationV2`/`correlate_observation_v2`
   require identity equality before correlating anything.
7. **Fail closed on a missing or invalid artifact** — a shadow run that
   cannot produce a valid profile/manifest/gate output records nothing
   (or an explicit `gate_unavailable`), never a fabricated "clean" result.
8. **Never mix v1 and v2 artifacts** in one published comment or one
   evidence bundle, per `docs/AGENT_REVIEW_V1_V2_COMPATIBILITY.md`.
9. **Carry a kill switch** — a single configuration flag that disables the
   v2 shadow run entirely without touching the pinned SHA, for immediate
   response to an unexpected issue (see `docs/AGENT_REVIEW_V2_ROLLBACK.md`).
10. **Never auto-merge, auto-approve, or remediate** anything. Shadow output
    is read by a human; it takes no action of its own.

## Canary criteria (before widening beyond an initial small sample of PRs)

- The wrapper has completed at least one full run in the target's own CI
  environment without an unhandled exception.
- Profile/policy load succeeds against the target's own real
  `TargetProfileV2` (or its migrated-from-v1 equivalent).
- No secret, credential, or PHI-shaped content appears in any published
  shadow artifact (target's own DLP gate, e.g. InterLeitos's real PHI
  detector — never the benchmark-only `corpus_safety.py` from #88, which is
  explicitly scoped to that synthetic corpus and is not a production DLP
  gate).
- The kill switch has been exercised at least once in a non-production
  context to confirm it actually suppresses the v2 run.

## Observation policy (feeds A6)

While in `shadow`, the target should record, per run: conclusive vs.
`manual_required` rate, coverage completeness, false-positive-shaped
observations (a human judges these, v2 does not self-report a "false
positive" label), stale/cross-run rejections, divergence from v1's own
result on the same PR (informational, never a merge decision), run
duration, and any kill-switch activations. This is exactly the metric list
A6 (observation window) already specifies in the master plan — a target's
shadow wrapper should be built to emit these from day one rather than
retrofitted later.

## Condition to move from shadow to advisory

Advisory promotion requires, per target, ALL of:

- a completed observation window (A6) with real data, not this benchmark's
  synthetic corpus;
- an explicit evaluation of false positive and aprovação falsa rates from
  that real data;
- a disposition specific to that target, in that target's own issue, under
  that target's own grant;
- continued absence of required-check status and readiness authority for
  v2 (advisory still never blocks a merge by itself).

No target automatically advances. Each promotion is a deliberate, separate
decision.

## What this specification explicitly forbids in the FIRST adoption PR of any target

- v2 as a required or default check.
- Reusing InterLeitos DLP infrastructure changes as part of an AgentEscala
  adoption or vice versa — each target's boundary is its own.
- Moving 24H/12H/10–22H scheduling rules (or any other target-specific
  domain logic) into this toolrepo.
- Retiring or bypassing the target's existing v1 pipeline.
- Any CT102 interaction.
