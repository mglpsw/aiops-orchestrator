# AgentReview v2 rollback runbook

**Status:** `CURRENT | V2 DEVELOPMENT` — linha sucessora em desenvolvimento; não é GA, não é default e não é required check. Estado atual: [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

Refs #133 (parent #89, epic #80, roadmap #46). This is the rollback
procedure for the **AgentReview v2 toolrepo consumption pin** in a target
repository's workflow — distinct from `docs/ROLLBACK.md`, which covers the
CT102 **runtime** deployment (Docker container, database, AIOps API). Never
confuse the two: rolling back AgentReview v2 touches only a target's own
pinned SHA/version reference and never restarts, redeploys, or reconfigures
CT102.

## What "rollback" means here

A target repository's workflow references this toolrepo by a full 40-char
commit SHA or an equivalent pinned release version (never a floating branch
or tag). Rollback means changing that one pinned reference back to a known-
good prior value — nothing else.

```text
target workflow
  AIOPS_ORCHESTRATOR_SHA / AIOPS_ORCHESTRATOR_RELEASE
    → currently: v0.21.0 (or an intermediate SHA during shadow adoption)
    → rollback to: v0.20.0, SHA 13695c73d1da9f16eba5c20e6478e7d51aefbb45
```

## Preconditions before rollback

- Identify the exact pin the target currently uses (workflow env var, e.g.
  `AIOPS_ORCHESTRATOR_SHA`/`AIOPS_ORCHESTRATOR_RELEASE` in AgentEscala's
  `agent-review.yml`).
- Confirm the rollback target (`v0.20.0`) is still installable in a clean
  environment: `bash scripts/install-agent-review-toolrepo.sh <venv-dir>
  --toolrepo-sha 13695c73d1da9f16eba5c20e6478e7d51aefbb45`.
- Confirm no in-flight v2 shadow run depends on artifacts that only v2
  produces (shadow runs are advisory-only and non-blocking by design, so
  this should never be true, but verify before acting).

## Procedure

1. In the target repository's workflow, change the pinned SHA/version back
   to the rollback reference (`v0.20.0` / `13695c73d1da9f16eba5c20e6478e7d51aefbb45`).
2. Open a normal PR in the target repository changing only that pin — this
   is a target-repository action requiring the target's own review process
   and, if the target enforces it, its own grant. AgentReview's own toolrepo
   is never modified to perform a target's rollback.
3. Merge that PR through the target's normal process.
4. Confirm the next run in that target uses the rolled-back pin (visible in
   the run's own logged `aiops_orchestrator_sha`/`aiops_orchestrator_release`
   fields).
5. If the target had any v2-specific configuration (e.g. an
   `AIOPS_AGENT_REVIEW_ENGINE` variable selecting v2), revert that
   configuration in the same PR.

## What rollback does NOT require

- No change to `master` of this toolrepo.
- No tag deletion or re-tagging.
- No CT102 action of any kind.
- No coordinated multi-target rollback — each target rolls back
  independently, on its own schedule, exactly as each target adopted v2
  independently.

## Kill switch (faster than a full rollback PR)

If a target's workflow already reads an engine-selection variable (e.g.
`AIOPS_AGENT_REVIEW_ENGINE`), flipping that variable to `aiops` (v1) or
unsetting it is a same-repository, no-code-change kill switch — faster than
re-pinning the SHA, and sufficient when the goal is "stop using v2 right
now" rather than "return to the exact prior pin." Follow with the full
re-pin procedure above at the target's own convenience.

## Condition to return to v2 after a rollback

Re-adopting v2 after a rollback follows the same shadow-first procedure as
the original adoption (`docs/AGENT_REVIEW_V2_SHADOW_ROLLOUT.md`) — it is
never re-enabled at the same trust level it was rolled back from without
re-running the target's own validation.

## Fallback when an artifact is missing or invalid

If a v2 shadow run's own artifact (profile, manifest, or gate output) is
missing or fails to validate, the shadow wrapper must fail closed to
`manual_required`/no-observation — it must never fall back to treating v1's
output as if it were v2's, and never silently promote a missing v2 result
to "clean." See `docs/AGENT_REVIEW_V2_SHADOW_ROLLOUT.md` for the wrapper's
own fallback contract.
