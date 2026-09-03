# AgentReview canonical backlog and CAEM reuse ledger

**Status:** `CANONICAL | CURRENT` (bookkeeping/reconciliation document, not an
implementation record). Created in a read-only-census-plus-bookkeeping
session on `2026-09-03`, master `6d13aa02e1cffd97f298bbe128e798203ab1c146`.

## Why this file exists, and a factual correction about its own premise

This file was commissioned to either extend an assumed-existing
`docs/checkpoints/AGENT_REVIEW_CAEM_REUSE_LEDGER.md` (said to already carry a
`G1C` entry plus `G1D`/`G1B`/`G3C`/`G2C`/`REVIEWS`/`G5`/`MERGE_CI` TODO
placeholders, added by the G1C2 merge) or to stand alongside it as a broader
backlog+ledger document.

**That premise does not match live state.** Verified directly against
`master` (`git ls-files | grep -i ledger` → no result; `git show --stat
6d13aa02...` → G1C2's merge touches only `app/agent_review/` and
`tests/agent_review/`, no `docs/checkpoints/` changes at all):
`docs/checkpoints/AGENT_REVIEW_CAEM_REUSE_LEDGER.md` **does not exist on
master**. It exists only on the now-closed-unmerged PR #309 branch
(`feat/200-g2c-structural-egress-closure`, preserved as forensic evidence,
not deleted), where it was created fresh with a single `G2C` entry — not a
`G1C` entry, and not with the other placeholders described above. Per
`STOP_G2C_CRASH_CONTRACT_NOT_CONVERGING`, that branch's content did not
land on master and its qualification does not transfer.

Consequence: this file is the **first** canonical backlog/ledger document on
master for this purpose. It supersedes no prior version, because no prior
version exists here. If a future slice's branch independently produces its
own ledger content before landing, that content should be reconciled into
*this* file at merge time, not treated as a second source of truth.

## Section 1 — AgentReview v2 trust-primitive lineage (critical path)

Classification taxonomy: `ACTIVE_CRITICAL_PATH`, `ACTIVE_PARALLEL`,
`DEFERRED_WITH_VALID_DEPENDENCY`, `COMPLETED_CLOSE_CANDIDATE`,
`SUPERSEDED_CLOSE_CANDIDATE`, `DUPLICATE_CLOSE_CANDIDATE`,
`FORENSIC_REQUIREMENT_PRESERVE`, `STALE_BODY_RECONCILE`, `RESEARCH_HOLD`,
`PERMANENT_GOVERNANCE`, `UNKNOWN_REQUIRES_EVIDENCE`.

```yaml
issue_200:
  title: "extrair, redigir e vincular conteúdo real dos hunks ao payload semântico"
  track: agentreview_v2_core
  canonical_property: "hunk content extraction/redaction/binding + G4B ingress debt"
  implementation_status: complete
  qualification_status: post_merge_qualified
  disposition: CLOSED_COMPLETED
  closed_at: "2026-09-02T02:52:55Z"
  closing_commit: 2242169cb6410baf9ddc9ce96b8c6f9f70f51875
  verified_deliberate_close: true   # commit title itself is "close #200-G4B post-merge external-review debt (#296)", not an accidental keyword match
  next_gate: none

issue_310:
  title: "#200-G1C2 — descriptor-anchored Git object-store acquisition authority"
  track: agentreview_v2_trust_primitive
  canonical_property: "acquisition is descriptor-anchored (open-then-authenticate-then-reuse-same-fd), never pathname-re-resolved"
  implementation_status: complete
  qualification_status: CODEX_UNAVAILABLE_EXCEPTION_substitute_quorum_2_reviews_0_unresolved_P0P1
  disposition: COMPLETED_CLOSE_CANDIDATE   # already correctly closed
  successor_pr: 311
  merged_as: 6d13aa02e1cffd97f298bbe128e798203ab1c146
  next_gate: none

issue_312:
  title: "#200-G1C2-F1 — unbounded blocking open on hostile-planted FIFO"
  track: agentreview_v2_trust_primitive
  canonical_property: "availability only — probe opens must reject non-regular files / use O_NONBLOCK+fstat, not a trust-boundary break"
  implementation_status: not_started
  qualification_status: n/a
  disposition: ACTIVE_PARALLEL
  blocking: false
  next_gate: implementation_grant

issue_313:
  title: "#200-G1C2-F2 — trusted_ref anchor resolved against hostile-derived authority"
  track: agentreview_v2_trust_primitive
  canonical_property: "authorize_commit_for_execution_v2 must require an out-of-band-verified SHA trusted_ref_sha, never a ref name resolved inside the hostile-derived store"
  implementation_status: complete
  qualification_status: 2_internal_adversarial_lanes_reconciled_lane_a_no_p0p1_lane_b_1_p1_disposed_via_docs_plus_regression_test_plus_followup_issue_319   # Codex dispatch pending
  disposition: ACTIVE_CRITICAL_PATH   # still blocks G5 wiring until qualified
  blocking: true
  caem_predecessor_search: NO_RELEVANT_CAEM_PREDECESSOR_FOUND   # ADR 0012/0014 adjacent, not this mechanism
  successor_branch: fix/200-g1c2-f313-out-of-band-authorization-anchor
  successor_pr: 317
  followup_issue: 319   # #200-G1C2-F3, caller-side ref-laundering via resolve_commit_v2, deferred until a live caller exists
  next_gate: review_and_merge_grant

issue_319:
  title: "#200-G1C2-F3 — authorize_commit_for_execution_v2: no mechanism to prevent caller-side ref-laundering via resolve_commit_v2"
  track: agentreview_v2_trust_primitive
  canonical_property: "the #313 shape check proves trusted_ref_sha LOOKS like a commit sha, never where it came from; closing the residual gap requires a caller-side provenance/attestation channel that never touches this module family's own hostile-derived read path"
  implementation_status: not_started
  qualification_status: n/a
  disposition: DEFERRED_WITH_VALID_DEPENDENCY   # no live caller of authorize_commit_for_execution_v2 to design the channel against yet
  blocking: false   # does not block #313/PR #317 or anything currently in flight
  depends_on: ["#200-G1B", "G5"]   # design against the real composition layer once implemented, not speculatively now
  next_gate: implementation_grant   # only once a live caller exists

issue_304:
  title: "#200-G1D — canonical no-follow commit materialization authority"
  track: agentreview_v2_trust_primitive
  canonical_property: "committed tree structure validated for safe 1:1 filesystem representability, fully in-memory against a canonical trie, before any byte is written; materialization via dir_fd/O_NOFOLLOW, symlinks only as leaves"
  implementation_status: architecture_only
  qualification_status: n/a
  disposition: ACTIVE_CRITICAL_PATH
  predecessor: "PR #302 (4 refuted attempts, forensic, not merged)"
  next_gate: implementation_grant

issue_301:
  title: "#200-G1B — fresh-process execution provenance from verified commit subject"
  track: agentreview_v2_trust_primitive
  canonical_property: "verify materialized subject THEN start fresh process THEN import only after verification, producing a distinct execution-provenance receipt; IDENTITY/AUTHORIZATION/EXECUTION-PROVENANCE stay three separate composable questions"
  implementation_status: architecture_only
  qualification_status: n/a
  disposition: ACTIVE_CRITICAL_PATH
  depends_on: [304]
  next_gate: implementation_grant

issue_298:
  title: "#200-G3C — sealed manifest authority and deterministic authoritative-diff subject"
  track: agentreview_v2_trust_primitive
  canonical_property: "sealed material != retained identity scalars — manifest content must be re-verified at every consumption boundary, not trusted via a stored hash/run_id scalar; plus SHA-pair diff subject with pinned git-interpretation config"
  implementation_status: architecture_only
  qualification_status: n/a
  disposition: ACTIVE_CRITICAL_PATH
  predecessor: "PR #297 (STOP_G3B_ARCHITECTURE_NOT_CONVERGING, forensic, not merged)"
  next_gate: implementation_grant

issue_299:
  title: "#200-G2C — non-enumerative outbound safety truth-maker (architecture)"
  track: agentreview_v2_trust_primitive
  canonical_property: "outbound Router payload representation has no structural slot capable of carrying any raw literal's bytes, by construction (closed AST projection), not by content classification"
  implementation_status: attempted_stopped   # PR #309 closed unmerged, STOP_G2C_CRASH_CONTRACT_NOT_CONVERGING
  qualification_status: partial   # structural-closure property itself held across 2 exact-head reviews; crash-vs-degrade contract refuted twice
  disposition: ACTIVE_CRITICAL_PATH
  successor: 314
  grant_note: "implementation grant referenced in this issue's own 2026-09-02T21:47:16Z comment supersedes architecture-only restriction for #303/#304/#301/#298/#299 — not executed by this bookkeeping session"
  next_gate: implementation_grant   # for #314 specifically

issue_314:
  title: "#200-G2C-C1 — crash-vs-degrade contract cannot be established by enumerating exception types"
  track: agentreview_v2_trust_primitive
  canonical_property: "replace exception-type enumeration with a fail-closed structural boundary conversion at project_fragment_structural_v2's public API — anything not a successful validated projection becomes a typed controlled block, never an uncaught crash"
  implementation_status: not_started
  qualification_status: n/a
  disposition: ACTIVE_CRITICAL_PATH
  blocking: true   # blocks G2C completion, independent of G1D/G1B implementation
  next_gate: implementation_grant

g5_operational_composition:
  title: "G5 — provider-free operational composition (successor concept to earlier '#200-D clean operational runner')"
  track: agentreview_v2_trust_primitive
  canonical_property: "wire IDENTITY + AUTHORIZATION + EXECUTION-PROVENANCE + sealed-manifest + non-enumerative egress + G4B ingress into one live-Router-capable operational composer, provider-free qualification first"
  implementation_status: not_started
  qualification_status: n/a
  disposition: ACTIVE_CRITICAL_PATH
  depends_on: [310(done), 304, 301, 298, 314, "G4B(done)", 313]
  next_gate: "all dependencies implemented + own implementation_grant"
```

## Section 2 — AgentReview v1 line

```yaml
issue_213:
  canonical_property: "deterministic taxonomy: model-authored limitations vs. reason codes, required/optional artifact, coverage-derived plan status"
  implementation_status: complete
  qualification_status: released_v0.22.0
  disposition: COMPLETED_CLOSE_CANDIDATE_PENDING_221   # governance ownership explicitly handed to #221 per its own 2026-08-13 comment; not independently closed because #221 owns final v1 disposition
  stale_body: true   # body's "Estado de entrega" still reads NOT_AUTHORIZED, contradicted by its own later comments; not rewritten this session (out of explicit edit scope)
  next_gate: "#221 V1_FINAL_FREEZE reconciliation"

issue_232:
  canonical_property: "non-must_review files with no observable hunk still declared coverage complete (must_review case already fixed by C8/PR #231)"
  implementation_status: not_started
  disposition: ACTIVE_PARALLEL
  next_gate: "#221 v1 debt census disposition"

issue_307:
  canonical_property: "TS1 mutation survivor (P1, changes_requested vs untrusted-blocker warning) + vacuous no-plan gate assertion needs a real golden byte-preservation test"
  implementation_status: not_started
  disposition: ACTIVE_PARALLEL
  predecessor: "PR #275 (closed unmerged)"
  next_gate: "#221 v1 debt census disposition"

issue_315:
  canonical_property: "v1 lane (github_agent_review.py/agent-review.yml) sends raw diff/paths/full source to Router under regex-blocklist-only protection; no structural closure comparable to v2's G2C exists for v1"
  implementation_status: not_started
  disposition: ACTIVE_PARALLEL
  newly_opened: true
  opened_this_session: true
  provenance: "PR #309 comment 2026-09-03T11:52:53Z (mglpsw); also carried into #314's body"
  next_gate: "#221 V1_FINAL_FREEZE disposition (fix, document-as-accepted-risk, or disable, per #315's own body)"

issue_221:
  canonical_property: "v1 GA operational consolidation, post-merge canary, final freeze umbrella"
  implementation_status: in_progress   # V1_FINAL_DEBT_RECONCILIATION phase active
  disposition: ACTIVE_PARALLEL
  owns: [213, 232, 307, 315]
  next_gate: "debt census completion -> release-prep -> repin -> canary -> freeze, each its own grant"
```

## Section 3 — Distribution / installer / post-v2 / v2.1 clusters

Researched this session (full body + comment reads); see issue #199/#46
comments posted in this same reconciliation pass for the authoritative
current-state summary. Condensed here for backlog completeness:

```yaml
issue_201: {disposition: DEFERRED_WITH_VALID_DEPENDENCY, reason: "core complete, closure blocked on AgentEscala#750 target adoption"}
issue_202: {disposition: DEFERRED_WITH_VALID_DEPENDENCY, reason: "core complete (audit verdict: no_gap), closure blocked on AgentEscala#752 target adoption"}
issue_203: {disposition: ACTIVE_CRITICAL_PATH, reason: "init/doctor/validate implemented; conformance/install-workflows/upgrade/rollback absent; carries known G1/G3 doctor debt"}
issue_204: {disposition: DEFERRED_WITH_VALID_DEPENDENCY, reason: "blocked on #203 completion"}
issue_205: {disposition: DEFERRED_WITH_VALID_DEPENDENCY, reason: "blocked on #204 + debt gate (#232/#259 G1-G3/#260/#261)"}
issue_226: {disposition: RESEARCH_HOLD, reason: "correctly gated behind A0 v1 freeze + A1 #205 publication; spec-only, implementation_authorized: false"}
issues_249_255: {disposition: RESEARCH_HOLD, reason: "same gate as #226, sequential dependency chain 249->250->251->252->253->254->255, all spec-only"}
issue_63: {disposition: ACTIVE_PARALLEL, reason: "optional precision-lane signal per #46's own A1-Q framing; explicitly non-blocking to #203/#204/#205"}
issue_64: {disposition: ACTIVE_PARALLEL, reason: "same A1-Q framing as #63"}
issue_256: {disposition: ACTIVE_PARALLEL, reason: "self-contained shadow lane, explicitly zero effect on readiness/lifecycle in phase 1, non-blocking"}
issue_264: {disposition: PERMANENT_GOVERNANCE, reason: "methodology/epistemic-gap record with an explicit non-closure criterion, not meant to complete in the normal sense"}
issues_193_198: {disposition: ACTIVE_PARALLEL, reason: "real Router-receipt-v2 upstream dependency correctly tracked, does not block #199/#203 critical path", stale_body: true, stale_note: "bodies/early comments still reference receipt v1/F1; each issue's own 2026-08-27 comment already supersedes this to v2/F2-A — recommend a documentation-only body refresh in a future pass"}
```

## Section 4 — Other tracks (ProjectOps, Review Intelligence, Observability, legacy/Workbench)

Out of this session's verification depth by design — this reconciliation
pass's mandate is the AgentReview v1/v2 lines. The following open issues
were inventoried (title-level, via `gh issue list`) but not individually
re-verified against their own current bodies/comments in this pass:
`#19, #30-#36, #91-#95, #119, #121, #123-#126, #160-#172, #192`. Each
already has an authoritative owner and milestone position in issue #46's
Track B (Review Intelligence)/C (ProjectOps)/D (Observability)/E (Agent
Router)/F (legacy/Workbench) sections and its M2-M11 milestone table,
which this reconciliation pass did not find any live-state reason to
dispute. Retain their existing dispositions as recorded there. A future
session scoped to those tracks should re-verify them with the same
full-body-and-comments depth applied to the AgentReview lines above.

## Section 5 — CAEM design-reference reuse ledger

Per-slice record of what, if anything, this repository read from
`mglpsw/caem` as design reference under this repo's own `authority_effect:
none` CAEM pin (`config/caem/caem-3.0-f0.pin.json`). No row in this section
grants CAEM qualification, certification, or normative authority — see each
row's own issue body for the full verbatim citation and authority-boundary
disclaimer.

```yaml
agentreview_property:
  - issue: 310   # #200-G1C2, already integrated
    failure_class: TOCTOU_pathname_reresolution
    caem_predecessor: "mglpsw/caem ADR-0012 (N5 authenticated detached-launcher attestation), design reference only, authority_effect: none for this repo"
    predecessor_truth_maker: "retained capability opened once + authenticated, never re-resolved by pathname (openat-relative descent, O_NOFOLLOW per component)"
    predecessor_falsifiers: TODO
    agentreview_domain_delta: "applied to a Git object store's acquisition (fanout dirs, loose objects, pack files, ref files) instead of a launcher/interpreter/dependency tree"
    implementation_owner: "PR #311 (merged 6d13aa02)"
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: INTEGRATED_COMPLETED

  - issue: 304   # #200-G1D, architecture-only
    failure_class: write_side_symlink_interposition_during_materialization
    caem_predecessor: "mglpsw/caem ADR-0012, native launcher (tooling/launch_n5_replay_native.c), design reference only, authority_effect: none for this repo"
    predecessor_truth_maker: "parent descriptor is path authority; per-component no-follow reopen; missing primitives fail closed, no pathname fallback"
    predecessor_falsifiers: TODO
    agentreview_domain_delta: "canonical in-memory trie from git ls-tree, structural representability validation before any write, symlinks only as leaves — Python os.open(dir_fd=)/O_NOFOLLOW, no openat2/RESOLVE_BENEATH stdlib binding available so parity is not claimed beyond what's demonstrated"
    implementation_owner: not_started
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: OPEN_ARCHITECTURE_ONLY

  - issue: 301   # #200-G1B, architecture-only
    failure_class: import_time_trust_of_module.__file__
    caem_predecessor: "mglpsw/caem ADR-0011 (proof-carrying bundle) + ADR-0012 (N5 attestation) + ADR-0016 (WHAT vs HOW), design reference only, authority_effect: none for this repo"
    predecessor_truth_maker: "closed digest inventory authenticated before import; bootstrap loader over in-memory byte map; module.__file__ never proof; isolation flags precede first import"
    predecessor_falsifiers: TODO
    agentreview_domain_delta: "AttemptIdentity(G1)/ExecutionBinding(this issue) split; TCB floor for AgentReview not yet named"
    implementation_owner: not_started
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: OPEN_ARCHITECTURE_ONLY

  - issue: 298   # #200-G3C, architecture-only
    failure_class: stored_identity_scalar_not_reverified_against_current_content
    caem_predecessor: TODO   # no CAEM citation found in this issue's body as of this census
    predecessor_truth_maker: TODO
    predecessor_falsifiers: TODO
    agentreview_domain_delta: TODO
    implementation_owner: not_started
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: OPEN_ARCHITECTURE_ONLY

  - issue: 299   # #200-G2C, attempted/stopped
    failure_class: content_classification_over_open_domain
    caem_predecessor: "mglpsw/caem ADR-0011, section \"DLP and carrier closure\", design reference only, authority_effect: none for this repo"
    predecessor_truth_maker: "structural closure (typed/synthetic fixtures suffice; no generic/free-text payload region) proves closure, not universal absence of sensitive semantics"
    predecessor_falsifiers: TODO
    agentreview_domain_delta: "applied to an outbound Router chat-completion body instead of an N5 replay carrier; unconditional AST-literal-to-opaque projection, not detection"
    implementation_owner: "PR #309 (closed unmerged, STOP_G2C_CRASH_CONTRACT_NOT_CONVERGING)"
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: ATTEMPTED_STOPPED_SUCCESSOR_314_ACTIVE

  - issue: 314   # #200-G2C-C1, not started
    failure_class: exception_type_enumeration_over_C_level_parser
    caem_predecessor: TODO
    predecessor_truth_maker: TODO
    predecessor_falsifiers: TODO
    agentreview_domain_delta: TODO
    implementation_owner: not_started
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: OPEN_NOT_STARTED

  - issue: 312   # F1, availability
    failure_class: unbounded_blocking_open_on_special_file
    caem_predecessor: TODO
    predecessor_truth_maker: TODO
    predecessor_falsifiers: TODO
    agentreview_domain_delta: TODO
    implementation_owner: not_started
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: OPEN_NOT_STARTED

  - issue: 313   # F2, authorization-anchor semantics
    failure_class: trust_anchor_resolved_against_hostile_derived_store
    caem_predecessor: TODO
    predecessor_truth_maker: TODO
    predecessor_falsifiers: TODO
    agentreview_domain_delta: TODO
    implementation_owner: not_started
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: OPEN_NOT_STARTED

  - issue: G5
    failure_class: n/a   # composition, not a single truth-maker
    caem_predecessor: TODO
    predecessor_truth_maker: TODO
    predecessor_falsifiers: TODO
    agentreview_domain_delta: TODO
    implementation_owner: not_started
    tests_ported: TODO
    tests_rederived: TODO
    authority_effect_in_aiops: none
    qualification_transferred: false
    current_status: NOT_STARTED
```

## Process rule cross-reference

`SEARCH_CANONICAL_PREDECESSOR before INVENT_NEW_TRUST_MECHANISM` already
exists in [`app/agent_review/AGENTS.md`](../../app/agent_review/AGENTS.md)
("Trust-mechanism design" section) as of this census — not duplicated here.
This ledger's Section 5 is the per-slice evidence trail that rule points
implementers toward before designing a new detection-based defense.
