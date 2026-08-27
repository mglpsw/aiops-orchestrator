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

Every refusal reuses the originating authority's own `reason_code`. Eight codes
are new because no upstream authority owns the condition, each namespaced so it
cannot be mistaken for an upstream code:

- `operational_preparation_chunk_set_mismatch` — manifest, payloads and
  content each validated, but they do not describe the same chunk set;
- `operational_assembly_contract_invalid` — assembly constructs `RunIdentityV2`
  and `ManifestV2`, so a contract violation there arrives as a raw pydantic
  `ValidationError` rather than a `RunAssemblyError`. Named for the whole
  assembly contract, not identity alone, because manifest/fragment invariants
  surface here too. **Found by a failing test, not by review.**
- `operational_payload_set_invalid` — the emitted payload set failed its own
  contract for a reason `PayloadSetBindingError` does not name;
- `operational_repo_root_unusable` — the checkout is missing or unusable; the
  underlying `OSError` stringifies the local path, so only the code crosses;
- `readiness_invariant_violation` — the readiness artifact violated its own
  contract; its pydantic message embeds finding content;
- `operational_run_budget_invalid` — a caller-supplied chunk budget that cannot
  describe a chunk; the planner raises a bare `ValueError` for it;
- `operational_git_toolchain_unavailable` — diff acquisition could not run at
  all, typically no `git` on PATH. An environment failure, deliberately
  distinct from an input failure;
- `operational_payload_reference_unreadable` — a declared artifact/contract
  reference exists but could not be read;
- `assembly_blocked` — fallback when assembly reports a block with no reason
  object of its own.

(That is nine names for eight new conditions: `assembly_blocked` is a fallback,
not a distinct condition.)

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

## Upstream follow-up found during review — NOT fixed here

`payload_references_v2.build_payload_contract_references_v2` reads a contract
file with `path.read_bytes()` and has no `OSError` guard, though the artifact
branch beside it does. An unreadable-but-present contract file therefore leaks
a raw traceback carrying the absolute path. Classified `PLAUSIBLE` by review
(needs a non-root runner or a TOCTOU window) and reproduced only under those
conditions.

The upstream asymmetry is NOT fixed here: patching it would put `#200-D` inside
a module outside its granted surface. But recording it did not excuse the
missing clause at *this* module's own call site, which review correctly
separated — an unreadable contract file now refuses as
`operational_payload_reference_unreadable` instead of leaking a traceback with
the absolute path. The upstream gap remains a follow-up alongside the two
carried from PR #270.

## The rule that finally closed it — two boundaries, two rules

Four rounds produced an untyped escape each time, and rounds 3–4's fixes were
still instance-shaped. The reason was a single rule applied to two different
boundaries:

```text
INPUT PARSING          the operator handed us a file or a value.
(CLI reading argv,     ANY failure is their input -> always a typed refusal.
 files, JSON)          A codeless ValueError here is bad input, not our bug.

AUTHORITY DELEGATION   a v2 module refused. Preserve ITS reason_code; a
(calling into          codeless failure really is a defect in this
 app/agent_review)     repository and must stay a crash.
```

Treating a codeless failure as "our defect, re-raise" is correct only at the
second boundary. Applied at the first it turned a malformed operator file into
a traceback -- which is exactly how `--dlp-policy '"a string"'` escaped after
round 4 claimed the class was closed.

Recording the boundaries, not just the patches, is what stops the next slice
re-deriving this.

## Recurrence note — how the refusal-path class was finally closed

Three review rounds each produced one more "untyped escape" at the composition
boundary, because every guard enumerated the exceptions it had already SEEN:
`PayloadReferenceError` (a sibling family, not a subclass), a codeless pydantic
error from readiness emission, a bare `ValueError` from the chunk planner for a
non-positive budget.

The fourth round's fix is not another `except` clause. Caller-supplied inputs
are validated where they ENTER -- a non-positive chunk budget and a missing
checkout are refused before any authority is asked to interpret them -- which
is what lets the remaining `OSError` guard stay narrow enough to distinguish
"no such checkout" from "no `git` on PATH", a distinction a blanket guard had
silently conflated.

## STOP — the error model needs a design decision (`STOP_ARCHITECTURAL_BOUNDARY`)

Six independent exact-HEAD review rounds each surfaced the same class: a stage's
`except` list is narrower than the exception surface beneath it. Two structural
attempts reduced but did not close it — validating caller inputs where they
enter (round 4), then separating input-parsing from authority-delegation rules
(round 5). Round 6 still found three more:

```text
extraction stage   catches ExtractionBlockedError, but not the sibling
                   ReviewContentBindingError nor pydantic ValidationError
payload stage      lacks the ValidationError clause assembly and payload-set
                   both have
git OSError        every OSError reads as "no git on PATH", so a
                   PermissionError on repo_root misdirects the operator
```

The root cause is not any of these three. It is that this module converts
errors by **enumerating, per stage, the exception types it has seen** — while
each upstream v2 authority has an *open* exception surface: a sibling error
family, a pydantic `ValidationError`, an `OSError` from a file read it does not
guard. None of those surfaces is documented or closed, so no amount of
inspection enumerates them correctly; each review round finds one more.

Closing it properly means choosing an error model, which is a design decision
this slice was not granted:

- **A. Single conversion wrapper.** One `_delegate(...)` around every authority
  call, converting anything carrying a `reason_code`, and allow-listing only
  what may crash (programmer errors). Removes the per-stage lists entirely.
- **B. Close the upstream surfaces.** Make each v2 authority raise only its own
  typed family. Correct, but edits ~8 modules outside this slice's grant.
- **C. Accept enumeration** and add a conformance test asserting every
  delegation is guarded. Cheapest; does not prevent the next omission.

The composition itself is not in question — six rounds have confirmed stage
order, non-duplication, profile-derived authority, the preparation closure and
the provider-free proof. What is unresolved is only how this module converts
other modules' failures. Recorded rather than patched a seventh time.

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
