# Checkpoint — AgentReview v2 operational composition (`#200-D`)

**Status:** provider-free operational composition implemented and qualified
locally; exact-HEAD forge CI and independent review are the next gates. This
is a checkpoint of this slice, not a claim about live execution.

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  issue: 200
  slice: 200-D
  branch: feat/200-d-operational-composition
  base_sha: 5b94632e4c1c243f746248131694933228edab83
  router_authority:
    repository: mglpsw/agent-router-api
    sha: 80e921dfc28436bd4fed8a4e1fa72ffaa168d10c

state:
  public_agentreview_schema_changed: false
  run_synthetic_review_v2_modified: false
  duplicate_orchestrator_created: false
  workflow_changed: false
  target_pack_changed: false
  live_router_call_made: false
  provider_call_made: false
```

## The gap this closes

Every v2 stage was implemented and qualified, but nothing composed them.
`run_synthetic_review_v2` already owned the whole back half and already
accepted either transport — yet it had **no caller anywhere in `app/` or
`scripts/`**, because its three inputs (`content`, `manifest`,
`payload_by_chunk_id`) had no producer. That wiring existed only inside tests.

Verified mechanically at the base, not inferred from docs:

```text
.github/workflows/agent-review.yml -> scripts/github_agent_review.py
    imports nothing from app.agent_review   (fully disjoint legacy reviewer)

agent_router_transport_v2   -> no production caller
run_synthetic_review_v2     -> no caller at all
extract_review_content_v2   -> no caller
assemble_manifest_from_diff_v2 -> no caller
```

v2 had CLIs at both *ends* (`aiops-review-build-payload-set-v2.py` consumes an
already-built manifest and payload files; `aiops-review-quality-gate-v2.py`
owns readiness) and none in the middle.

## What was added

One preparation/composition authority plus a thin CLI:

```text
repo checkout + trusted profile root + base/head
  -> load_target_profile_v2
  -> bind_semantic_grouping_policy_to_target_profile_v2
  -> acquire_authoritative_diff_v2            (real git subprocess)
  -> assemble_manifest_from_diff_v2
  -> build_chunk_payloads_from_profile_v2
  -> emit_payload_set_v2                      (manifest<->payload closure)
  -> extract_review_content_v2                (redaction/DLP, self-binding)
  -> preparation closure                      (content<->payload edge)
  -> run_synthetic_review_v2                  (UNCHANGED back half)
```

No stage is re-implemented. `run_synthetic_review_v2` was not modified and
deliberately not renamed: its name is misleading now that it can drive the
real Router, but renaming a just-qualified surface buys no capability. The new
wrapper gives callers a truthful name; a later cleanup may deprecate the old
one.

## No self-issued authority

| fact | route |
|---|---|
| `policies` | DERIVED from the loaded profile (identity, not equality — tested) |
| repository identity | DERIVED from `profile.identity.repo` |
| `target_profile_root` | caller-explicit, trusted base checkout |
| `origin`, `pr_state`, `toolchain_digest` | caller-owned, explicit |
| `snapshot` | through `parse_authoritative_ci_snapshot_v2` only |
| `checks`/`provenance` | CLAIMS, re-verified by `#201-C0`; empty degrades honestly |

Nothing fabricates a snapshot, an origin or a digest to make a run succeed.

## Reason-code taxonomy

Every refusal reuses the originating authority's own `reason_code`. Two codes
are new because no upstream authority owns the condition, both namespaced so
they cannot be mistaken for an upstream code:

- `operational_preparation_chunk_set_mismatch` — manifest, payloads and
  content each validated, but they do not describe the same chunk set;
- `operational_run_identity_invalid` — `RunIdentityV2` is constructed inside
  assembly, so contract-invalid identity material arrives as a raw pydantic
  `ValidationError` rather than a `RunAssemblyError`. It is now converted to a
  typed refusal: a traceback must never be what tells a caller their identity
  material was malformed. **This was found by a failing test, not by review.**

## Earliest-authority precedence, proved by discrimination

`base_sha` malformed resolves to `invalid_git_ref` (diff acquisition) and NOT
to the identity code — if assembly were reached first the reason would change,
which is how the stage order is pinned rather than assumed. Front-half
failures cannot produce a Router call structurally: the transport is not
reachable from `prepare_operational_review_v2` at all.

## Recorded boundaries (not defects)

**Staleness.** `run_synthetic_review_v2` uses `manifest.identity` for both
`identity` and `evaluated_identity`; nothing here independently observes
whether the PR head moved mid-run. This slice inherits that bound unchanged
and adds no live head observer. Independent head observation and stale
detection belong to the live-canary grant.

**`tested_merge_sha`** is a CallerDeclared fact: well-formed-but-unresolvable
values are accepted into identity, because nothing in this composition proves
the declared tested tree exists. Recorded by a named test.

## Adjacent follow-ups carried from PR #270 — still NOT fixed

Recorded here because both were forge-comment-only until now, and neither has
a dedicated issue:

1. **Final `ChunkReviewRequestV2` revalidation outside typed conversion**
   (`_router_receipt_v2.py`). Unreachable from a valid request; outside the
   HTTP boundary C5 scoped. Revalidated against `#200-D`: the operational
   runner reaches that code path only through the same already-validated
   request object, so it is **not** newly reachable.
   Disposition: `NON_BLOCKING_FOLLOWUP`.

2. **`{"schema_version": True}` accepted on the historical v1 classification
   path** (`versioning.py`, pre-existing on `master` before #270). Proved
   unable to forge v2; changes only which *rejection* applies on the v1
   branch. `#200-D` adds no new call site for it.
   Disposition: `NON_BLOCKING_FOLLOWUP`.

Neither blocks the operational runner or the live canary. Both still warrant
dedicated issues.

## Scope fence

Included: `operational_run_v2.py`, `aiops-review-run-v2.py`, focused tests,
checkpoint, CHANGELOG.

Not included: workflow mutation, `scripts/github_agent_review.py` replacement,
`#203` target-pack installation, `#194`–`#198` provenance, live Router or
provider execution, secrets, AgentEscala/InterLeitos mutation, CT102/CT104,
public schema change, Ready, merge, release/tag or deploy.

This slice establishes `OPERATIONAL_COMPOSITION_PROVIDER_FREE`. It does not
establish `LIVE_ROUTER_PROVEN`, `AGENTESCALA_CANARY_PROVEN`,
`PRODUCTION_WORKFLOW_CONNECTED` or `TARGET_PACK_INSTALLED`.
