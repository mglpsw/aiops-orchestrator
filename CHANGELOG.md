# Changelog

## Unreleased

### Fixed

- **K runtime carrier established disjoint from the diagnosed target, or
  refused (`#262`)**: `acquire_target_pack_epoch_v2` chose the carrier location
  from `euid` alone, with no reference to `target_root`, so a target that
  equalled, contained or aliased the carrier caused the pack's own runtime
  carrier to be materialized inside the very directory it was handed.
  Reproduced on `master@2876434` for a target containing the carrier, a bind
  alias of the runtime parent, a deep alias several levels inside an otherwise
  unrelated target, and a runtime parent grafted from a target subtree.

  Disjointness is established at the shared primitive, before any carrier
  materialization, from a single observation of `/proc/self/mountinfo`.
  Relevance is **derived from the topology query being asked** rather than
  written as a growing list of positions:

  ```text
  QueryKind -> SemanticSeeds -> DependencyClosure -> graph validity
            -> QueryResolution -> sealed consumer APIs
            -> per-authority name semantics -> physical domains -> K-DISJOINT
  ```

  `POINT_LOOKUP(P)` seeds on mounts attached at the prefixes a pathname walk
  traverses; `VISIBLE_SUBTREE(D)` adds attachments at-or-beneath `D`. Siblings
  and unrelated mounts are excluded by the derivation, not by exception, so an
  unrelated malformed record cannot refuse a legal acquisition. The target's
  domain is a visibility PARTITION -- each segment excludes the slices its
  visible children cover -- and the carrier's domain names exactly the two
  objects an acquisition operates on: the protocol directory and the exact
  `<key>.lock`. Chain validation establishes that each parent edge is
  geometrically possible, not merely that a parent exists. Name-semantics
  applicability is decided per lookup-authority directory from that
  directory's own governing filesystem, over a closed three-way classification
  (casefold-flag-capable / established case-sensitive / unknown), so an
  `ENOTTY` from `FS_IOC_GETFLAGS` is never read as proof of case sensitivity.
  The custom intermodular static analyzer that previously tried to seal raw
  traversal is **falsified for this property**. The exact-head native review of
  `6d17b55` returned P1=0/P2=17 across independent Python semantic classes
  (imports/packages, lexical scopes, receiver/type propagation, reflection and
  syntax, source discovery, owner identity, and qualification oracles). The
  mandatory `STOP_ARCHITECTURAL_BOUNDARY` fired; those findings are evidence
  that the enforcement architecture was insufficient, not 17 runtime defects
  to patch individually.

  The replacement is structural capability encapsulation. One private module,
  `app.agent_review._mount_topology_raw_v2`, owns mountinfo parsing, raw graph
  storage (`records`, `children`, `by_id`), graph validation and raw traversal.
  Its sole product importer, `target_pack_epoch_v2`, captures that representation
  in typed closures and gives ordinary consumers an immutable, slotted
  capability exposing only `resolve_query_v2`, `governing_mount_v2`,
  `visible_child_mounts_v2`, `is_visible_v2`, and `project_v2`. The consumer
  representation has no raw graph field, raw traversal method or `__dict__`.
  The exact-head native review of `ebff0a2` returned P1=1/P2=6. The P1 found
  that the ordinary typed result still returned the proof-only
  `validated_frontier`, allowing consumers to reconstruct the raw inventory;
  the public `TopologyQueryResolutionV2` now projects only `query`,
  `governing_mount`, and the legitimately required `visible_descendants`.
  The internal resolver retains its frontier for validation. A product
  consumer and positional-use sweep found no dependency on the removed field.
  Pickle round-tripping is restored through an explicit reconstruction channel
  without restoring raw ordinary fields, and `parse()`/`observe()` again invoke
  subclass construction. K-DISJOINT policy remains in
  `target_pack_epoch_v2`; it is not duplicated in the raw module. Topology and
  K-DISJOINT also consume the same canonical path-containment function rather
  than independently copied `_within_v2` bodies.

  The exact-head native review of `6a11425` returned P1=0/P2=1. The valid
  compatibility finding in `discussion_r3858955407` showed that reconstruction
  preserved the subclass type but re-ran constructor defaults without restoring
  post-construction subclass state. This is a different proposition from F28,
  so it admits no seventh recurrence. The explicit pickle channel now restores
  supported subclass-owned ordinary `__dict__` and declared slot state after
  canonical reconstruction while continuing to exclude raw topology state from
  the ordinary representation. The six admitted recurrences remain unchanged;
  this is not a `PROVED` or globally-clean claim.

  The 400+ line Python semantic frontend is deleted, not retained as a parallel
  authority. Its replacement is a small finite gate that answers only two
  questions: exactly which non-test repository source **paths** contain an
  ordinary static `Import`/`ImportFrom` site naming the unique
  `_mount_topology_raw_v2` leaf, whether the canonical owner path is the sole
  such product source, and whether the real capability/result ordinary shape
  exposes forbidden raw state. Two further recurrences on `ebff0a2` falsified
  the first replacement gate before it was corrected: it again lost package
  identity for a relative import in `__init__.py`, and again collapsed
  `module.py` with `module/__init__.py`. The gate was redesigned rather than
  taught those cases. It now counts exact source paths from the lexical leaf
  and deliberately reconstructs no package graph or module identity. It also
  performs no receiver propagation, import-graph inference,
  union/subclass/factory inference or `MatchClass` semantics. Computed imports,
  `callable.__closure__`, hostile deliberate reflection into implementation
  internals, monkeypatching and C-level introspection are explicit NONCLAIMS;
  this is not a claim of Python privacy. The closure-cell P2 was reproduced and
  retained as `CANDIDATE_OUT_OF_SCOPE` under that preimplementation nonclaim,
  not called false or fixed.

  Topology that cannot be established is refused as
  `target_pack_epoch_carrier_disjointness_unknown`; an established overlap as
  `target_pack_epoch_carrier_overlaps_target`. The two are deliberately
  distinct so "could not look" is never read as "looked and it was fine".

  Accepted topologies are narrowed deliberately: filesystems outside the
  direct-projection allowlist (`ext2`/`ext3`/`ext4`/`tmpfs`) -- including
  overlay -- refuse rather than acquire, and a runtime parent reached through a
  subtree bind is refused. Over-refusal is a bounded cost; under-refusal would
  fabricate the property. `PR #267` and `PR #268` remain forensic predecessors.
  Six recurrences were admitted during this work and remain historical fact:
  the first drove the redesign from positional relevance rules to the typed
  query frontier, the second drove sealing that authority against consumer
  bypass, and the third -- an incomplete production-source inventory, admitted
  because the evidence establishing `scripts/` as a topology-capable surface
  was in hand when the boundary was chosen and was reasoned past -- drove the
  attempted replacement of directory-derived inventory by intermodule
  reachability. The fourth is N15 -> N21 (`SAME_PROPOSITION`,
  `Δ15 = ESTABLISHED_INSIDE`): permitted raw-owner identity was incomplete,
  and the successor still collapsed unrelated owners; mechanized replay showed
  both witnesses suppressed by the same analyzer mechanism. The fifth is the
  repeated loss of package identity for a relative `ImportFrom` originating in
  `__init__.py`; the sixth is the repeated collapse of distinct `x.py` and
  `x/__init__.py` source paths. Both are `SAME_PROPOSITION` with their current
  witnesses `ESTABLISHED_INSIDE` the earlier demonstrated deltas. None of the
  six admissions is erased or downgraded by the successor architecture. No
  consumer decides disjointness, no target-facing schema changes, and writer K
  identity, SH/EX coordination and expected-plan binding are unchanged. The
  typed topology result intentionally narrows by one proof-only field; explicit
  pickle reconstruction and subclass construction are compatibility channels,
  not ordinary raw consumer APIs. The claim remains bounded to one topology
  snapshot taken immediately before materialization: it makes no claim against
  a concurrent external remounter, a non-cooperating external actor,
  distributed coordination or crash atomicity. It is not a `PROVED` or
  globally-clean claim.

- **Post-merge review debt on the Authority-First Convergence Review
  methodology (`#263`)**: three P2 findings raised after that PR merged, all
  reproduced on `master@ff9fbdd`.  (1) The target-profile YAML corpus test's
  module documentation stated, in the present tense, that three consecutive
  findings on one abstraction boundary are the preflight's unconditional
  STOP/REDESIGN trigger — a rule the current MethodAuthority has superseded,
  and one a maintainer could have applied with no `CorrectionAttempt` or
  admitted recurrence in evidence.  Both occurrences are now marked as
  historical record and point to `STRUCTURAL_CHANGE_PREFLIGHT.md` as the sole
  authority for current STOP semantics, without restating it.  (2) The word
  `disposition` named two different axes — the factual validation outcome
  (`VALID`/`INVALID`/`PARTIAL`/`UNAVAILABLE_TO_VALIDATE`) and later lifecycle
  resolution (`fixed`/`superseded`/`out_of_scope`) — so a record implementing
  "record a disposition" could not tell whether the two shared one field and
  overwrote each other.  They now carry distinct identities,
  `FindingValidationOutcome(f)` and `LifecycleDisposition(f)`, with neither
  derivable from the other and both simultaneously representable.  (3) The
  round-output block declared an exhaustive set of three results while the
  correction barrier distinguishes `established`/`out of scope`/`unsettled`,
  leaving an established out-of-scope candidate with no honest outcome and
  routing it to a hold reserved for unsettled evidence.  A fourth result,
  `candidate out of scope`, is added, and `held for investigation` is narrowed
  to an unsettled barrier.  Prose only: no schema, enum, persisted record or
  new contract artifact, and no change to the S1 recurrence kernel, the S3a
  finding-truth/materiality separation, or the open status of S2a/S2b.

- **Post-merge review debt on `#265`**: two P2 findings raised by the native
  Codex review of `#265`'s own merged head (`0ea09a86`), both reproduced on
  `master@226c9c3`.  (1) `#265` added the fourth round-outcome result,
  `candidate out of scope`, to the authority block and to the round-record
  completeness rule, but two other places in the document still enumerate the
  outcomes exhaustively and had not been updated: the preflight's entry
  instructions, and the handoff/closure requirements, both of which still
  named only `no candidate formed` / `recurrence admitted` / `held for
  investigation`.  Both now name all four.  (2) `#265` established
  `FindingValidationOutcome(f)` and `LifecycleDisposition(f)` as distinct axes
  with neither derivable from the other, but three passages still treated
  "invalidated by new evidence" as a value or transition of
  `LifecycleDisposition(f)` — once in the lifecycle enumeration itself, and
  twice in prose describing what happens when later evidence contradicts an
  earlier `VALID`. All three now record a later-epoch `FindingValidationOutcome`
  on the factual axis instead, leaving the earlier epoch's history and any
  recurrence admitted on its strength exactly where they were, and not
  inventing an `invalidated` lifecycle value. A full consumer sweep found no
  further material occurrences of either pattern. Prose only: no schema, enum,
  persisted record or new contract artifact, and no change to S1/S2/S3.

### Changed

- **Target-pack `init --apply` now uses one private cooperative K epoch
  (`#203`)**: the canonical writer acquires a same-host/same-EUID/same-mount-
  namespace K EX carrier before recomputing the operation plan.  It applies
  only when that locked plan's existing semantic hash equals the explicitly
  authorized hash; otherwise it returns `target_pack_plan_stale` before any
  target-prefix creation, pack write, or receipt.  Target mutation and the
  success receipt consume the same live capability and a held `O_PATH`
  directory binding.  This adds no receipt/manifest/schema field, durable
  generation identity, journal, recovery behavior, or public mutation-
  exclusion vocabulary.  The guarantee is cooperative only; external,
  distributed, crash, and provenance claims are intentionally not made.

- **Target-pack CLI surface and validate check-domain now have single
  internal runtime authorities, consumed by the runtime itself (`#203`)**:
  `app/agent_review/target_pack_runtime_authority_v2.py` declares two
  deliberately distinct subjects — which commands the CLI exposes, and the
  domain of validate check identities. The CLI parser is built by iterating
  the command authority and verifies `C = K = P` (authority domain =
  configurator domain = exposed parser choices), and every validate report is
  constructed through one finalizer that canonicalizes against the declared
  domain and refuses an unknown identity, a duplicate, a caller-fabricated
  `unvalidated` row, or a locally evaluable check marked `unavailable`.
  `VALIDATE_CHECK_ORDER_V2` and `UNVALIDATED_CAPABILITIES_V2` survive only as
  derived projections. A deterministic internal view,
  `docs/generated/target-pack-runtime-authority.v1.json`, is emitted and
  gated byte-identical in CI so tooling can consume both relations without
  parsing Python. Observable behaviour is intended unchanged — verified by a
  baseline-vs-candidate differential over 9 validate scenarios (109 check
  rows, early-return paths included) and 10 CLI invocations. Public
  target-facing contracts are untouched: `TargetPackManifestV2` and
  `TargetInstallReceiptV2` have no diff, exported schemas are byte-identical,
  and the same real manifest builds to identical canonical bytes and digest
  on both sides. Refs #203.
- **Structural Change Preflight now defines *Authority-First Convergence
  Review*, the repository's reusable structural-review method (`#203`)**: a
  claim-first method in which the proposition under test and the authority that
  establishes it are named before implementation, and in which findings are
  reproduced and dispositioned before they are patched. The preflight already
  owned the `STOP / REDESIGN` criteria; this change amends them.

  The rules themselves are not restated here — that is the preflight's job, and
  a summary that restates them becomes a second copy that drifts from the one
  that is normative. Read them there. What follows records the *shape* of the
  change and its known limitations, and is not a definition of any rule.

  The method is stated without the cases that produced it: no past change,
  commit, review outcome or claim about another artifact appears in it, and
  removing every illustration leaves the rules intact. It carries no second
  artifact that is normative for its rules — the preflight states why, and this
  entry does not repeat the reasoning. Two mechanisms were retired rather than
  reformulated after repeated failure: a general rule for inferring tree equality
  from the shape of a squash merge, and the apparatus of computing convergence
  from the author's own records.

  Recurrence semantics were then reconciled over several rounds of adversarial
  review, each recorded on `#263`. Forming a recurrence candidate and qualifying
  one were separated; a candidate's second finding was bound to the subject that
  was actually reviewed; boundary identity was tied to participation rather than
  to predeclaration; and the vocabulary of admission was reserved for recurrence.
  A further round named the hold for the investigation it invites rather than for
  a resolver the method declines to define, separated a finding's validation
  history from a candidate's recurrence-admission history, and reconciled the
  round-output vocabulary across this file and the preflight. Later rounds bound
  the recurrence relation to a demonstrated defect domain rather than the
  proposition alone, and separated a finding's factual establishment from its
  bearing on the decision at hand — a finding can be real without yet being
  material to the claim under review, and that unresolved bearing is preserved
  rather than coerced either way. The revision applies prospectively; the
  preflight owns these rules and this entry does not restate them.

  **Known limitation.** The method has no route by which "no correction was
  attempted" becomes an established fact — that would need either a complete
  attempt ledger or a declaration by the party the rule constrains, and neither
  is introduced. An author who files no attempt record does not thereby form a
  candidate that holds; no candidate forms through that relation at all, and
  nothing is established about whether corrective work happened. Separately,
  which findings a convergence claim must account for before it can be declared
  established remains an open question the preflight does not yet close. The
  limitation is tracked separately; the preflight states the rules and the
  reasoning.

  Process/methodology only: no runtime, schema or public-contract change, and no
  test over the method's normative content, which has no oracle outside the
  document. That is a claim about the content: a document's syntax does have an
  external oracle in the Markdown grammar, and a lint over `docs/` would be
  non-circular by the same criterion. Adding one is a separate objective and is
  not taken here. Refs #203.

### Fixed

- **AgentReview v2 target-profile YAML loading now derives ambiguity from
  the parser (`#237`)**: `load_target_profile_v2`/`load_target_profile_text_v2`
  now reject a duplicate authored key (even when every occurrence carries
  the same value) at the exact point PyYAML's own constructors would
  otherwise choose silently, instead of comparing readings or projecting
  to JSON. Any YAML merge key (`<<:`) is refused separately, by a
  composition-level pre-pass over the composed node graph before that
  reading ever runs -- not a second collision the constructor observes;
  a merge key needs no competing entry to be refused. Contract validation
  now runs directly against the parsed object
  (`TargetProfileV2.model_validate`), with no intermediate
  `json.dumps`/`model_validate_json` round-trip. See
  `docs/adr/ADR_AGENT_REVIEW_V2_TARGET_PROFILE_YAML_AUTHORITY.md`.
- **AgentReview v1 chunk planning by real hunk cost, not path length
  (`#225`)**: the semantic chunk planner sized a chunk from
  `max(256, len(path) * 12)` — a proxy for the file *path*, never the diff
  it actually contains — while `chunk_payload_builder` only injected real
  hunks and applied the 24,000-char budget afterward. A plan could declare
  `status: complete` for a chunk the builder was always going to have to
  truncate; on `mglpsw/AgentEscala#774`'s real canary this silently dropped
  9 of 10 `must_review` files' hunks behind an unchanged `hunks_included`
  count, while the quality gate correctly (but only after the fact) reported
  `manual_review_required`.
  - **Single shared cost authority.** New `app/agent_review/payload_cost_model.py`
    is imported by both the planner and the builder — including the
    `checks_context`/`evidence_context`/`contracts_context` construction
    functions moved there from `chunk_payload_builder.py` — so there is
    structurally one formula for what a chunk will cost, never two that can
    drift apart again. `project_min_hunk_preserving_chars` computes the
    payload's terminal state — every optional context already at the exact
    minimal form the real shrink ladder converges to, every hunk left fully
    intact — which is sound by construction: the ladder always tries that
    state before it is ever allowed to touch a hunk.
  - **Split before truncate.** The planner now packs each semantic group
    with deterministic first-fit-decreasing bin packing driven by that
    projection, splitting an oversized group across multiple chunks of the
    same `semantic_group` (already supported by `chunk_result_parser` and
    `pr_brief`) instead of shrinking hunks to fit one. `max_blocks`
    selection is two-phase: every candidate partition is generated first,
    then candidates that themselves contain a `must_review` file are always
    selected before ones that do not, cutting across `GROUP_PRIORITY`.
  - **Fail closed, never fragmented.** A single file whose payload exceeds
    the budget alone is reported `must_review_payload_oversize:<path>` (or
    `payload_oversize:<path>` if not `must_review`) and left out of the
    plan — never silently truncated into a "partial" chunk. A `must_review`
    file with no observable diff hunk (binary, or a diff-producer gap) is
    `must_review_hunk_unavailable:<path>` and never counted as covered.
    Malformed path identities (absolute, `~`-relative, traversal, empty) are
    excluded per-file with a `path_identity_*` reason; two identities that
    would redact to the same publishable path fail the whole plan closed
    (`path_identity_collision`) rather than risk merging them.
  - **Hard guard before the Router.** Nothing downstream of
    `chunk-payload-manifest.json` (`parse-chunks`, `synthesize`,
    `quality-gate`, `telemetry`) actually reads it, so a residual
    hunk-transport divergence can never be reported as a mere manifest
    limitation. `build_chunk_payloads` now refuses to route any chunk whose
    covered-file hunk was reduced, altered, or dropped by the shrink ladder
    — that manifest entry gets `payload_path: null` and
    `chunk_hunk_material_not_transported:<path>`, and
    `aiops-review-build-payloads.py` exits non-zero instead of reporting
    `partial`/`ok: true`.
  - **Single budget authority.** `--payload-max-chars` diverging from a
    chunk's own planned `prompt_budget_chars` now fails closed
    (`payload_budget_mismatch`) instead of silently overriding it — the
    exact shape of divergence this whole fix closes, one level up.
  - **Projection-input binding.** An explicit `--checks` /
    `--validation-evidence` document (`aiops-review-build-payloads.py`, and
    optionally the same new flags on `aiops-review-plan-chunks.py` for
    projection precision) must be canonically equivalent to what the intake
    already embeds, or the run fails closed
    (`payload_projection_input_mismatch`) rather than let the builder review
    a document the planner never actually projected against.
  - **Deterministic.** `SemanticChunkPlan.created_at` now derives from
    `intake.created_at` (was wall-clock); file order within a chunk is
    canonical-path order, not intake order — the same intake, in any file
    order, now produces byte-identical plans.
  - **Additive diagnostics only.** `hunks_included` keeps its existing
    meaning; new `hunks_fully_included`/`hunks_reduced`/`hunks_omitted`
    counts and sanitized `files_with_hunks_reduced`/`_omitted`/`_missing`
    path lists live only on `chunk-payload-manifest.json` entries, never on
    the routed payload itself. `SemanticChunk.estimated_chars` changes
    meaning (path-length proxy → real payload projection); no schema version
    changes, no consumer decides on it directly.
  - v2 (`*_v2.py`, `schemas/agent-review/v2/**`) is untouched — proven, not
    just asserted, by a new AST-scanning test. See
    `docs/AGENTESCALA_TOOL_REPO_INTEGRATION.md` for the full reason-code
    reference AgentEscala should expect.

### Added

- **`agentreview-v2-target-pack` — installable target pack, first slice
  (`#203`)**: begins packaging AgentReview v2 as an installer for consumer
  repositories, without forking the engine. This commit ships `init` and
  `doctor` (read-only, mechanically proven by AST/call-graph inspection),
  two new additive contracts (`TargetPackManifestV2`,
  `TargetInstallReceiptV2`), a pure drift/idempotence plan computer, and
  the sole atomic-write installer. `validate`/`conformance`/
  `install-workflows`/`upgrade`/`rollback` are deferred to a follow-up
  commit on the same branch/PR. Full spec:
  `docs/AGENT_REVIEW_V2_TARGET_PACK.md` and
  `docs/checkpoints/AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md`. Post-adversarial-
  review hardening on the same PR closed four in-scope defects found by an
  exact-HEAD external review: `doctor` now cross-checks a receipt's
  `pack_version`/`toolrepo_sha`/`target_profile_hash` against the manifest and
  loaded profile instead of trusting structural validity alone;
  `target_policy_hash` is `null` (not a fabricated all-zero digest) until a
  real policy artifact ships; `generated_file_hashes`/`target_owned_paths` are
  derived from the manifest's own ownership classification instead of "files
  written this invocation" (previously lost `target_owned_paths` across an
  idempotent re-`init`); and `init --rollout` is now capped by
  `TargetPackManifestV2.max_supported_rollout_mode` so `shadow_full` cannot be
  requested against a pack version that ships no trusted-check integration.
  Zero existing (pre-`#203`) schema/contract touched; see the PR for the final
  combined suite count.
- **AgentReview v2 required-check readiness wiring (`#201-C`)**: connects
  `#201-C0`'s legitimated required checks to `ReviewReadinessV2` — the last
  piece needed for the readiness contract to represent a required-check
  problem as a state instead of crashing.
  - **The gap this closes.** `compute_readiness_decision_v2` never read
    `checks`; `ReviewReadinessV2.validate_state_invariants` already required
    a non-empty, all-green `checks` list for `ready`, so a red or missing
    required check produced a `pydantic.ValidationError`, never a
    representable state. Separately, `run_synthetic_review_v2` accepted a
    `checks` parameter with **no verification at all** — the last ungated
    door for `ReviewReadinessV2.checks` the `#217` residual class named.
  - **Single production constructor path.** New
    `review_readiness_emission_v2.produce_review_readiness_v2` is the only
    public entry point that can build a `ReviewReadinessV2`. It always
    re-verifies `checks`/`checks-provenance` claims against `#201-C0`'s real,
    unpatched `reassemble_and_verify_required_checks_v2` before folding a
    precedence decision and assembling the artifact — no
    `except RequiredCheckProvenanceErrorV2` anywhere in the chain, so a
    forged submission always propagates uncaught rather than becoming a
    routine artifact. `run_synthetic_review_v2` no longer has an ungated
    `checks` parameter.
  - **Completeness is derived, never caller-supplied.** New
    `required_check_readiness_v2.py` derives the required-check name set
    exclusively from a `TargetProfileV2` loaded fresh from a trusted
    `target_profile_root` and bound to `identity.profile_hash` — no function
    on the public readiness path accepts `required_check_names` or
    `loaded_policy`. Proven, not just documented: a new AST/call-graph test
    file scans production code for exactly this property.
  - **Ratified precedence, three outcomes.** A verified, legitimate required
    check reaching `FAILED` or `AUTHORITY_NOT_ESTABLISHED` adds
    `POLICY_FAILURE` and forces `MANUAL_REQUIRED` — except when a genuinely
    `CONFIRMED` code finding already forced `BLOCKED_CODE`, which it joins
    rather than creates (`CHECK FAILURE != CONFIRMED CODE FINDING`). A red
    check is never dropped from the artifact. `STALE` is sovereign and
    untouched.
  - **`CLI_EXIT_SUCCESS != READINESS_READY`.** `scripts/aiops-review-quality-
    gate-v2.py` no longer fails outright when a required check has no
    legitimate submission — it now emits a real `manual_required` +
    `policy_failure` artifact and exits 0; the decision consumable by any
    caller is `readiness.state`, never the exit code. A forged/invalid
    submission still writes no artifact and exits 1, unchanged. No consumer
    of this CLI exists in this repository today, so the exit-code semantics
    changed with no live blast radius.
  - **No contract change.** `RequiredCheckResultV2`, `ReviewReadinessV2`, and
    every other frozen/published contract are byte-identical; the assessment
    that connects the two is internal, non-wire state, never accepted by any
    public function.

- **AgentReview v2 authoritative CI provenance bridge (`#201-C0`)**: makes it
  provable where an authoritative required check came from, before anything is
  connected to readiness. Closes the provenance bypass `#217` describes, for
  the path `#201` exercises.
  - **The gap.** `#201-B3` left `subject_code` checks permanently advisory, so
    pytest's authoritative verdict must come from deterministic CI — while
    `RequiredCheckResultV2` carries no producer identity and the quality gate
    matched required checks **by name**, so any object called `pytest` with
    `conclusion=success` satisfied it regardless of who built it.
  - **Typed union of exactly two sources.** `TrustedHostPromotion ∪
    AuthoritativeCIPromotion`, validated by separate functions and never joined
    by `check_name`. A green GitHub check named `pytest` is not evidence about
    an advisory executor result named `pytest`.
  - **Additive sidecar.** `RequiredCheckProvenanceV2` binds 1:1 to the exact
    `RequiredCheckResultV2` by digest, in both directions; each matched pair
    must also agree on `run_id`, `head_sha`, `repository`, `base_sha`,
    `tested_merge_sha` and `check_name`. `RequiredCheckResultV2` and every
    other frozen contract are unchanged, and the 15 pre-existing published
    schemas stay byte-identical.
  - **Authority is derived, never declared.** There is no `authoritative=True`
    parameter anywhere; `authority_effect="promotable"` is the output of the
    checks, not an input, and a sidecar that validates against its own digest
    is merely well-formed, not entitled.
  - **Base-owned policy.** `.aiops/authoritative-checks.v2.yaml` names the one
    producer entitled to speak for each required check as a complete identity
    tuple (`check_name`, `workflow_path`, `job_name`, `verifier_identity`,
    `producer_kind`, `producer_workflow`), read exclusively from the trusted
    base/default checkout. Both `policy_source_bytes_digest` and
    `policy_source_semantic_digest` travel into the sidecar.
  - **Producer evidence, separate from review origin.** `RunOriginV2` stays
    frozen: it types what the *review* is about, not how the producer fired.
    `AuthoritativeProducerEvidenceV2` separates `producer_trigger`,
    `workflow_execution_ref`, the producer workflow identity
    (`repository + path @ 40-char SHA + ref`) and the executed-tree
    attestation. Review origin `pull_request` and producer trigger
    `workflow_run` legitimately differ, and representing that is the point.
  - **Only a producer outside the pull request's reach is even considered.**
    `base_owned_workflow_run` is a `workflow_run` producer, whose definition
    GitHub loads from the default branch and whose run the pull request
    cannot add jobs to. A PR-triggered producer is refused outright — inside
    its own run a pull request can call any pinned workflow *and* upload an
    attestation carrying every field a verifier checks, because it already
    knows all of them. `merge_group` is not assumed base-owned: each event
    runs the workflow from its own ref, so it needs its own model.
    `sha_pinned_reusable_workflow` stays declarable and is refused at policy
    load time, becoming eligible only once the attestation's issuer is
    cryptographically authenticated.
  - **Base-ownership is necessary, not sufficient — and, on its own, still not
    enough.** The producer must also declare that it re-executed the check
    itself rather than republishing an artifact from the pull request's run
    (GitHub's own guidance: artifacts from a workflow that processed untrusted
    code are untrusted data), and that it read the executed tree from its own
    verified checkout rather than repeating a value it was handed. The
    attestation job performs **no checkout and runs no pull-request code**.
    The acquirer fetches attestations from the producer run's artifacts,
    bounded and strictly parsed, refusing when two artifacts (or two zip
    members) share the conventional name; fetching them does not validate
    them.
  - **`AuthoritativeCIPromotion` is refused unconditionally, by ratified
    architectural correction.** An independent audit of the base-owned model
    above found that `reexecuted_in_producer_run` means exactly what it says:
    the producer re-runs the PULL REQUEST'S OWN test suite and reports its
    exit code. A base-owned workflow *definition* does not change who
    authors the value being measured — `#201-B3`'s theorem
    (`controls(subject, success_signal) => not authoritative(success_signal)`)
    still applies; running the subject's tests inside a differently-owned
    runner relocates that boundary, it does not cross it. The round-7
    acceptance condition this model was built to satisfy is **revoked, not
    reinterpreted**: no `check_execution_mode` defined today supplies a
    semantic judge independent of the subject, so promotion is refused by a
    single, final, explicit gate (`verify_independent_semantic_judge_v2`),
    reached only after producer identity, base-ownership and tree binding
    have all already succeeded — that infrastructure remains real and still
    closes `#217`'s check-name-only bypass; it no longer implies a subject-code
    check can ever become authoritative on its own. `AUTHORITATIVE_PYTEST_
    PROMOTION=UNAVAILABLE_BY_DESIGN` until a producer kind with a genuinely
    independent judge is designed and ratified.
  - **Acquirer hardening**, from the same audit: every GitHub list endpoint is
    paged and refuses rather than truncating — a silently short list is
    indistinguishable from "the producer did not run". `--output` may not
    alias an input. A fabricated `str(None)` no longer masquerades as a real
    run/check identity (two runs missing `id` could otherwise collapse onto
    one fabricated identity, letting a stale green outrank a current red). The
    GitHub token is no longer replayed to the artifact-storage host on
    redirect. A fork pull request's own run — which cannot report its own
    base/head, ordinary GitHub behaviour for cross-repository runs — no longer
    poisons acquisition for every other observation in the same snapshot.
    Known limitation, recorded rather than fixed here: the acquirer's query
    scope (`?head_sha=`) cannot retrieve the base-owned producer run it is
    meant to observe; correcting it is deferred to a ratified amendment
    together with `#203`'s producer-installation design.
  - **HEAD is not the tested tree.** `review_subject_sha = head_sha` versus
    `execution_subject_sha = tested_merge_sha`, with order-sensitive
    `[base, head]` parentage proven per origin — `pull_request_target`,
    `manual` and `replay` never inherit synthetic-merge semantics.
  - **Deterministic run selection.** Highest `run_attempt` wins outright, so a
    stale green never outranks a current red; two survivors at the highest
    attempt is refused rather than resolved. Only `success` and `failure` are
    promotable — an environmental failure never becomes a product regression,
    and a product regression never disappears as an environmental failure.
  - **Gate hardened.** `--checks-provenance` is required (omitting it is an
    error, not a silent legacy path). `review_readiness_emission_v2` and
    `readiness_decision_v2` are untouched — wiring remains `#201-C`.
  - **Known limitation.** GitHub's Actions API reports no field asserting which
    ref a workflow *definition* was loaded from, and `pull_request` runs
    execute the workflow file from the pull request's own merge commit. Threat
    `C0-T4` is therefore not closed by GitHub metadata alone; the acquirer
    records what actually happened and never asserts a base-owned origin it
    cannot observe. Resolving it is a target-configuration decision. See
    `docs/checkpoints/AGENT_REVIEW_V2_201C0_PROVENANCE_BRIDGE.md`.
  - **Strict-parsing discipline closed on four remaining paths**, found by a
    Codex review after the architectural correction: `--observations` used
    plain `json.loads` (duplicate keys silently resolved to the last value,
    unlike every other input in this slice); `canonical_json_text` accepted
    `NaN`/`Infinity` (the same module's own strict parser would refuse to read
    the resulting text back); the base-owned policy's YAML loader accepted a
    duplicate mapping key (an auditor or a different YAML implementation
    could see a different `verifier_identity` than the one that was actually
    used to authorise a producer); and the policy's semantic digest depended
    on `authoritative_checks` entry order, even though the list is
    semantically a set keyed by `check_name` — entries are now sorted before
    hashing.

- **AgentReview v2 trusted-check adversarial hardening (`#201-B3`)**: closes
  the two mandatory acceptance criteria `#201-B2` left open.
  - **Authority boundary.** `trusted_check_authority_v2` classifies every
    inventory entry as `data_only_host_tool`, `subject_code`, or `unknown`;
    only `data_only_host_tool` may back `authority=TRUSTED`, refused
    *before* any process is spawned. `controls(subject, success_signal) =>
    not authoritative(success_signal)`: isolating PR-controlled code does
    not make its output authoritative, so pytest and any checkout-authored
    script can never be `TRUSTED`, no matter how strong the containment.
  - **Process-lifetime containment.** A real PID namespace
    (`unshare --pid --fork --kill-child --mount-proc --net`) replaces
    `#201-B2`'s process-group supervision; the kernel destroys every
    process in the namespace unconditionally the moment its init dies.
    Identity is host-discovered and pinned via `pidfd_open` — never
    trusted from anything the namespace's own occupant reports about
    itself. Zero survivors is proven (not assumed) across `SUCCESS`,
    `FAILURE`, `TIMEOUT` and `CANCELLED`.
  - **Amendment A1 — privileged broker for the sudo-elevated strategy.**
    This project's own GitHub Actions runner uses passwordless-sudo
    elevation, under which the host process cannot itself read a
    root-owned namespace's identity. `trusted_check_broker_v2` — a small,
    host-owned, stdlib-only process launched via `sudo -n python -I` —
    performs the same discovery/containment/teardown because it genuinely
    is root, and answers only a closed, enumerated protocol back to the
    host: no PID of any kind crosses that channel. A broker crash is
    contained by the kernel (`PR_SET_PDEATHSIG`), not by broker code that
    might not get to run.
  - `#201-B2`'s pgid-report temp-file handshake is deleted, not hardened
    again. No frozen contract changed; exported v2 schemas byte-identical.
  - See `docs/checkpoints/AGENT_REVIEW_V2_201B3_ADVERSARIAL_HARDENING.md`.

## v0.22.0 - 2026-08-08 - AgentReview v1 evidence taxonomy and v2 review-content extraction

### Fixed

- **AgentReview v1 evidence taxonomy (AgentEscala#675)**: the v1 engine
  collapsed five independent axes — model observation, deterministic
  limitation, artifact availability, file coverage and review status — into a
  single flat `limitations: list[str]`, so a published verdict could not say
  what had actually been reviewed. Three fixes, no engine redesign, no new
  normative contract.
  - **Provenance.** `chunk_result_parser` flattened the model's own
    `ChunkResponse.limitations` (`type`/`detail`, both unconstrained free
    text) into `f"{type}:{detail}"` and `extend`-ed the *same* list that
    carries engine-authored reason codes. LLM prose was therefore published
    as if deterministic; a model echoing a real code back with a sentence
    produced a second, apparently independent cause for one root cause; and —
    found while auditing this — a model emitting a bare code from
    `CRITICAL_LIMITATIONS` (e.g. `coverage_missing`, with no `:` suffix to
    tell it apart) drove `status` to `degraded` and the verdict to
    `manual_review_required` on a fully covered review. `ChunkResults` and
    `FinalReview` gain an additive `model_reported_limitations`, carried
    through to the rendered Markdown; `ReviewQualityGate` has no such field
    and `evaluate_review_quality_gate` does not read one -- the gate's
    transport ends at the synthesizer. `schema_version` stays `1` and
    consumers reading only `limitations` are unaffected. The rendered
    markdown gives each namespace its own heading and caps them
    independently, so model prose can no longer evict deterministic codes
    from the comment.
    (Correction, `#214`/`#229`: this entry originally also named
    `ReviewQualityGate` as a recipient of the field; it never was one.)
  - **Artifact requiredness.** `pr_brief` emitted `artifact_missing:<name>`
    for every absent artifact, ignoring the `required: false` already
    declared in the target profile — which is why a trusted recomputation
    that legitimately produces none of the target's optional artifacts
    reported five missing inputs indistinguishable from real ones. It now
    joins `target_profile.artifacts` by name and emits
    `required_artifact_missing:` / `optional_artifact_missing:`, reusing the
    codes `artifact_loader` and `aiops-review-build-payloads.py` already use.
    With no declaration the conservative `artifact_missing:` form is kept.
  - **Coverage vs. limitation.** `semantic_chunker._plan_status` returned
    `partial` whenever `limitations` was non-empty, so an informational
    `intake_schema_id_missing` stamped a 100%-covered plan as partial, and
    `final_synthesizer` republished that as `chunk_plan_status_partial` —
    reading downstream as a second, independent coverage failure that had
    never happened. Plan status now derives from coverage facts only; every
    coverage-bearing limitation already has a structural counterpart in
    `files_partially_covered` / `files_not_covered`, so nothing is lost.

- AgentReview v2 content extraction (#200-B/#200-C adversarial audit
  follow-up, distribution epic #199): a hunk windowed by
  `planner_v2`'s own documented repeated-anchor exception (a starved side
  collapsing to the same range across multiple windows, e.g. 1 old line
  replaced by hundreds of new lines) sent the same real line of code to
  the model in every window sharing that anchor -- confirmed by direct
  reproduction and fixed via `_assign_hunk_line_ownership_v2`, a
  deterministic per-line ownership resolution using only `planner_v2`'s
  own already-emitted fragment ranges (no second parser or planner). A
  `detector_name`-only DLP policy was silently treated as "clean" instead
  of "coverage never actually executed" -- `_apply_dlp_v2` now blocks
  unconditionally whenever a detector is declared, with a distinct reason
  code from an actual rule match. Budget was enforced per-fragment only,
  never summed across a chunk, and accepted a bare caller-supplied int --
  `extract_review_content_v2` now requires a real `TargetBudgetsV2` (no
  default) and a new `_enforce_chunk_budget_v2` sums every chunk's
  `INCLUDED` content, blocking `must_review` overflow fail-closed and
  dropping the largest auxiliary fragments first otherwise. Local paths
  were not actually redacted before reaching `FragmentContentV2`'s
  constructor -- fixed by applying `redaction._redact_local_paths`.
  `scripts/ci_validate.sh` gained a new §8 running the `requires_network`-
  marked real-git-subprocess tests that both CI gates were silently
  deselecting by default, closing the gap between "passed locally" and
  "the CI gate GitHub actually reports" (refs #200, #199,
  docs/checkpoints/AGENT_REVIEW_V2_200_ADVERSARIAL_AUDIT_FOLLOWUP.md).
  A second, independent adversarial pass over that same fix confirmed it
  and found 3 more real issues, closed in this same follow-up: the
  `TargetBudgetsV2` from the paragraph above proved values only, not
  provenance, so a caller could still construct a looser one than the
  profile that planned the manifest -- `extract_review_content_v2` now
  takes the full `TargetProfileV2` and checks its hash against
  `manifest.identity.profile_hash` before reading any budget from it;
  `_enforce_chunk_budget_v2` blocked the instant ANY `coverage_required`
  fragment shared an over-budget chunk, even when dropping auxiliary
  content alone would have made it fit, contradicting its own documented
  doctrine -- it now only blocks when `coverage_required` content alone
  exceeds the budget; and the add/modify/delete/rename hardening test
  asserted a fragment merely existed for the deleted/renamed path instead
  of proving its real content and canonical identity -- it now does both.

### Added

- AgentReview v2 offline trusted-check simulator (#201-B1, second slice of
  #201, distribution epic #199): `simulate_trusted_check_plan_v2`
  (`app/agent_review/trusted_check_simulator_v2.py`) produces real,
  fully-validated, plan-bound `TrustedCheckResultV2` instances without
  spawning a process, reading a checkout, or touching a filesystem --
  proven directly (not assumed) by a test that patches
  `subprocess.Popen`/`subprocess.run` to raise. `authority` is a required
  keyword argument with no default, so no call site can get a `trusted`
  result "by accident". Every check the plan authorizes must have a
  matching fixture; a fixture naming an unauthorized check is equally
  rejected. Mirrors `review_transport_v2.offline_file_transport_v2`'s own
  role: the deterministic default every downstream consumer builds
  against before the real isolated executor (`#201-B2`) exists (refs
  #201, #199, docs/AGENT_REVIEW_V2_TRUSTED_CHECKS.md)
- AgentReview v2 isolated trusted-check executor (#201-B2, third slice of
  #201, distribution epic #199): `execute_trusted_check_plan_v2`
  (`app/agent_review/isolated_executor_v2.py`) runs a real, isolated
  subprocess per plan check and produces a real, plan-bindable
  `TrustedCheckResultV2` -- the real thing `#201-B1`'s simulator stands in
  for. Isolation uses portable Linux primitives (unprivileged
  user+network namespaces via `unshare`, privilege drop to an
  unprivileged uid, `RLIMIT_AS`/`RLIMIT_NPROC`), proven for real in this
  session's own dev sandbox (explicitly not the project's pinned CT104
  runner, which is offline this corte -- CT104-specific guarantees are
  `blocked_external: ct104_unavailable`, never faked via CT102). Which
  command runs is resolved only from a host-owned inventory keyed by the
  plan's `command_token`, never free text; the verdict is derived
  EXCLUSIVELY from the kernel-observed exit code of the isolated child --
  nothing it prints or writes to any file is ever read to determine
  SUCCESS/FAILURE, proven directly by an adversarial test simulating a
  malicious `conftest.py` that forges both a success banner and a forged
  report file while the real process still exits nonzero. Also proven:
  real outbound network denial (`ENETUNREACH` in the fresh namespace),
  real `sudo` refusal, typed `TIMEOUT`/`CANCELLED` outcomes, refusal to
  run a command not in the inventory, deterministic results across
  repeated runs, and fail-closed refusal to run at all (never silently
  unisolated) when the isolation primitive itself is unavailable. Wiring
  into `ReviewReadinessV2` is `#201-C`'s job, not this slice's. Two
  further independent reviews of the first CI-green commit found 5 more
  real issues; 4 closed in this same slice, 1 confirmed as an
  architectural gap and made explicit rather than papered over: the
  inventory a caller supplies is now checked against the plan's own
  `authority_suite_digest` before any `command_token` resolves
  (`compute_check_command_inventory_digest_v2`, mirroring `#211`'s
  `target_profile` fix exactly) instead of being accepted unverified,
  and its dict keys must equal each entry's own `command_token` (a
  contradictory `{"other_token": spec_whose_token_is_"token"}` is now
  refused, not silently tolerated); OOM classification -- previously
  guessed from a signal-death signature that could misclassify a genuine
  unrelated crash as environmental, hiding a real regression from
  readiness -- is removed entirely, so every signal death is now the
  conservative, attributable `FAILURE` (`RLIMIT_AS` enforcement itself is
  unchanged and still proven); `process.communicate()` after the tracked
  process exits now has a bounded grace period and kills any lingering
  descendant holding the output pipe open instead of risking an
  indefinite hang -- fixed TWICE, since the first attempt used a pgid
  that could already be reaped/recycled by the time it was needed, and
  verifying the real fix under the sudo-elevated strategy (the one this
  project's own GitHub Actions runner actually uses) surfaced a second,
  deeper bug where `sudo`'s own monitor-process architecture decouples
  the command's real process group from the one `subprocess.Popen`
  reports, now solved by having the isolated process report its own
  real pgid back via a host-controlled file and killing through `sudo`
  too when that strategy was used; and `authority=TRUSTED` being
  caller-declared rather than independently verified is now an explicit,
  prominent rule in the module's own docstring -- do not use it against
  real adversarial PR code until `#201-B3` closes the still-open
  exit-code-forgery gap. Two further real CI-only failures surfaced and
  were fixed after that same round: the pgid-report file write inside
  the dropper script (needed only for later best-effort cleanup) crashed
  the WHOLE dropper with an uncaught `PermissionError` on the real
  GitHub Actions runner when written from the sudo-elevated identity --
  not reproducible in this session's own sandbox despite direct attempts
  -- fixed by making that write best-effort (`try/except OSError: pass`)
  plus a defensive `chmod 0666`, since reporting the pgid must never be
  allowed to invalidate an otherwise-correct verdict; and a leader that
  exited `0` while an orphaned descendant it never waited on kept the
  output pipe open past the kill-and-retry grace window was
  misclassified as `INFRA_FAILURE` instead of `SUCCESS` -- fixed by no
  longer raising on the second `communicate()` timeout, since the
  leader's own `returncode` is already known and authoritative by that
  point and an unrelated orphan's fate has no bearing on it. A second
  independent review, run against the actual current HEAD, then found a
  real P1: the `chmod 0666` from the paragraph above never got locked
  back down, so the pgid-report file stayed world-writable for the
  ENTIRE lifetime of the isolated check -- letting the untrusted command
  itself (running as `nobody`, same `/tmp`, well-known filename prefix)
  overwrite the pgid the host later trusts for a PRIVILEGED kill
  (`sudo -n kill -9 -- -<pgid>`). Fixed by having the dropper chmod the
  file back to `0600` immediately after writing it, still under the
  elevated identity and strictly before dropping to `nobody` and
  exec'ing the untrusted command, closing the window rather than
  narrowing it; proven by a new adversarial test that globs for the
  file from inside the dropped-privilege check itself and asserts every
  overwrite attempt is denied. A third independent review, again
  against current HEAD, found that lockdown chmod was itself fail-open
  (bare `try/except: pass` -- a failed chmod there still let the
  untrusted command run against a possibly-still-`0666` file) and that
  the unprivileged-userns fallback isolation strategy (no real uid
  separation from the host caller) could still back a `TRUSTED` result.
  Fixed: the dropper now chmod's BEFORE writing content and `os._exit()`s
  via a dedicated sentinel if that chmod fails, corroborated host-side
  by the report file still having no valid pgid content so a legitimate
  check sharing the same exit value is never misclassified; and
  `execute_trusted_check_plan_v2` now refuses to back `TRUSTED` with the
  weak fallback (`UNTRUSTED_ADVISORY` can still use it), since that
  fallback's isolated command runs as the exact same real uid as the
  host caller (refs #201, #199,
  docs/checkpoints/AGENT_REVIEW_V2_201B2_ISOLATED_EXECUTOR.md)

- AgentReview v2 trusted-check plan/result contracts (#201-A, first slice
  of #201, distribution epic #199): `TrustedCheckPlanV2` (host-owned,
  never PR-influenced -- checks are named by a fixed `command_token`,
  never raw argv, and `network_allowed` is pinned `Literal[False]`),
  `TrustedCheckResultV2` (raw per-check outcome: `authority` (`trusted`/
  `untrusted_advisory`) and `outcome` (`success`/`failure`/`timeout`/
  `oom`/`cancelled`/`infra_failure`), self-hashing and bound to a specific
  plan by `bind_trusted_check_result_to_plan_v2`. `promote_trusted_check_
  to_required_v2` is the ONLY function permitted to construct the already-
  published `RequiredCheckResultV2` from this sidecar, and refuses both
  `untrusted_advisory` authority and every environmental outcome -- proven
  structurally: `RequiredCheckConclusionV2` has exactly four values and
  none represents an environmental failure, so promotion cannot invent
  one. Contract only -- an offline simulator, an isolated executor,
  adversarial hardening, and wiring into a real readiness computation are
  `#201-B1`/`#201-B2`/`#201-B3`/`#201-C` (refs #201, #199,
  docs/AGENT_REVIEW_V2_TRUSTED_CHECKS.md)

- AgentReview v2 Agent Router transport wiring and synthetic end-to-end
  readiness (#200-C, third and final slice of #200, distribution epic
  #199): `run_synthetic_review_v2`
  (`app/agent_review/review_transport_v2.py`) wires the fixed order of
  authority content -> request -> transport -> envelope -> echo -> binding
  -> parser -> synthesis -> readiness for the first time, proven end-to-end
  against a real temporary git repository through to a real
  `ReviewReadinessV2` with `state=READY`. `ChunkReviewTransportV2` is an
  injected `Protocol`: `offline_file_transport_v2` (default in tests) and
  `agent_router_transport_v2` (the real Agent Router, locked to exactly
  `{base_url}/v1/chat/completions`, refuses with no `api_key` before any
  network attempt, tested against a mocked HTTP layer only -- never called
  live). A tampered echo, a missing response, or a malformed response all
  degrade exactly that chunk to `manual_required` and are proven to keep
  the resulting readiness out of `READY` -- never a silent approval. `#200`
  closes with this slice: `core_synthetic_complete` is now `true`, though
  `#199`'s own `semantic_reviewer_shadow` capability state remains `false`
  until a live canary (`AgentEscala#763-A`) (refs #200, #199,
  docs/AGENT_REVIEW_V2_REVIEW_CONTENT.md)

- AgentReview v2 real hunk-content extraction (#200-B, second slice of
  #200, distribution epic #199): `extract_review_content_v2`
  (`app/agent_review/review_content_extraction_v2.py`) turns a real diff
  into a `ReviewContentV2` bound to an already-assembled `ManifestV2`,
  reusing `diff_acquisition_v2.acquire_authoritative_diff_v2`,
  `redaction.redact_text`, and `#200-A`'s own contracts/binding -- no
  second engine. `diff_acquisition_v2` gained `compute_hunk_diff_sha256_v2`
  (the one preimage definition, shared by the parser and the extractor),
  `HunkBodyV2`, and `extract_hunk_bodies_v2` (reuses the existing
  `_FileBlockBuilder`, re-verifies every body against its own hash). A
  windowed (over-line-budget) fragment's content is reconstructed by a
  lossless per-line selection rule
  (`slice_hunk_body_by_range_v2`), proven duplicate-free on a realistic
  interleaved-hunk fixture. Every `must_review` fail-closed path (DLP
  match, budget overflow, recomposition failure, empty manifest) raises a
  stable, typed `ExtractionBlockedError` -- never a silent approval.
  Automatic content-budget-triggered re-planning is an explicit,
  documented limitation, not implemented (refs #200, #199,
  docs/AGENT_REVIEW_V2_REVIEW_CONTENT.md)

- AgentReview v2 semantic review content contract (#200-A, first slice of
  the distribution epic #199): `ReviewContentV2`
  (`app/agent_review/review_content_v2.py`), a sidecar bound to a
  `ManifestV2` by `run_id`/`manifest_hash` carrying real, redacted fragment
  content -- never folded into the already-published, already-pinned
  `ChunkPayloadV2` (zero `payload_sha256` changes). A second integrity
  anchor closes the gap where `payload_sha256` alone cannot distinguish two
  content sidecars for the same chunk: `ChunkReviewTransportEnvelopeV1`
  (`app/agent_review/review_transport_contract_v2.py`) wraps the unmodified
  `ChunkResponseEnvelopeValueV2` and requires the far end to echo back
  `content_sha256`/`request_sha256`, verified fail-closed before
  `consumer_v2.bind_chunk_response_v2` ever sees the response. A
  `coverage_required` fragment can never be represented without content
  (construction-time refusal, not a limitation). DLP policy is declarative
  or a digest-pinned host-owned detector only (`DlpPolicyDeclarationV2`) --
  structurally no field for a target-owned module/path/import/entrypoint.
  Three new schemas (`agent-review.review-content.v2`,
  `agent-review.review-transport-envelope.v1`, `agent-review.dlp-policy.v1`)
  registered in the RI-B0a.2 reuse manifest. Contract and ADR only --
  extraction, redaction, DLP execution, and Router wiring are `#200-B`/
  `#200-C` (refs #200, #199, docs/adr/ADR_AGENT_REVIEW_V2_REVIEW_CONTENT.md)

## v0.21.0 - 2026-08-04 - AgentReview v2 Offline and Shadow Adoption

See [`docs/RELEASE_V0_21_0.md`](docs/RELEASE_V0_21_0.md) for the full release
contract. This section was never split out of `Unreleased` when the tag was
cut; backfilled here verbatim from the same entries, without rewriting any
claim, so `CHANGELOG.md` matches the real tag history.

### Added

- AgentReview v2 benchmark (#88): a provider-reviewable synthetic corpus
  (`evals/agent_review_v2/reviewable_corpus/`, 6 `semantic_positive` + 4
  `semantic_safe_counterexample` cases with real, behaviorally-verified
  code and a deterministic materializer), a lane-applicability manifest
  covering all 15 relevant cases, a corpus-scoped safety gate, an
  AIOps-side pipeline projection bound to real PR/HEAD identity
  (`evals/agent_review_v2/aiops_projection.py`), and real Lane 2 (Codex
  CLI local)/Lane 3 (Codex GitHub shadow) execution against 10 real,
  ephemeral PRs (closed unmerged, branches deleted after acquisition).
  Result: 10/10 AIOps pipeline readiness accuracy, 6/6 location recall on
  both Codex lanes, 0 false positives; disposition `allowed_role: shadow`
  only (`reports/agent-review-v2-benchmark-summary.md`). Lane 4 (human)
  deferred to RI-C/RI-D by explicit disposition. Issue #88 closed as
  completed (refs #88, #177, #188, #189)

- AgentReview v2 release preparation (#133, parent #89): release notes
  (`docs/RELEASE_V0_21_0.md`), v1/v2 compatibility matrix
  (`docs/AGENT_REVIEW_V1_V2_COMPATIBILITY.md`), a rollback runbook
  distinct from the CT102 runtime rollback
  (`docs/AGENT_REVIEW_V2_ROLLBACK.md`), and a shadow/advisory rollout
  specification that authorizes no target-repository write
  (`docs/AGENT_REVIEW_V2_SHADOW_ROLLOUT.md`). No tag, GitHub Release, or
  target-repository change in this slice (refs #133)

- `config/ri/ri-b0a-2-reuse-manifest.json` + generated view
  `docs/generated/RI_B0A_2_REUSE_REFERENCE.md`: maps all 10 existing
  AgentReview v2 contracts plus the ProjectOps track boundary into one of
  four states (`reuse`, `reference`, `future_adapter`, `not_applicable`)
  for RI-B0, with a fail-closed loader (`app/ri_b0a/reuse_manifest.py`)
  and a `--check` CI gate. No AgentReview or CAEM schema is copied or
  redefined (refs #119, slice #119.2)

- `docs/RI_A2_THREAT_MODEL.md`: threat model, data policy, and authority
  boundaries for CAEM proof execution — 15 threat surfaces, a trust-boundary
  diagram, an authority/data matrix across 10 components, stop conditions
  for RI-B0/RI-B1, and an adversarial test corpus. Documentation only; no
  code, DLP detector, executor, auth, database, or deploy change (refs #120,
  parent #126)

- CAEM 3.0 F0 consumer pin (`config/caem/caem-3.0-f0.pin.json`) and a strict,
  offline, fail-closed loader (`app/caem_consumer/f0.py`,
  `scripts/verify-caem-f0-pin.py`) that verifies the pinned identity against a
  local artifact copy byte-for-byte; the exact CAEM 3.0 F0 carrier
  (`28ca73f3…`) is the only identity this consumer accepts — `main`, any
  branch/tag, and the post-F0 repair carrier (`dee7018e…`) are all rejected
  (refs mglpsw/aiops-orchestrator#119, slice #119.1)
- CAEM 2.1.0 material previously vendored under `.caem/` (`policy.json`,
  `repository-profile.json`, `repository-registry.json`, `schemas/*.json`) is
  quarantined, read-only, `authority_effect=none`
  (`.caem/quarantine/caem-2.1/`); `AGENTS.md`, `CLAUDE.md`,
  `docs/engineering/{CAEM_CORE,PROJECT_OVERLAY,CURRENT_CHECKPOINT}.md` now
  reference the single consumer pin instead of declaring CAEM 2.1.0/2.2.0
  digests that no longer correspond to anything in this checkout

- AgentReview v2: explicit v1/v2 contract-version selection
  (`app/agent_review/versioning.py`), rejecting unknown or mixed versions
  fail-closed
- AgentReview v2: verified payload-response binding wired into a new
  consumer and parser (`app/agent_review/consumer_v2.py`,
  `app/agent_review/parser_v2.py`); no v2 finding is reachable before run
  identity, HEAD, chunk, payload hash, response hash, file scope, and
  coverage-without-promotion are all proven (refs #83)
- `docs/AGENT_REVIEW_V2_BINDING.md` documents the version-selection
  contract, binding sequence, and reason-code precedence
- AgentReview v2: strict, fail-closed `TargetProfileV2` loader
  (`app/agent_review/profile_loader_v2.py`) with reproducible
  profile/policy hashing; no silent degradation to a placeholder profile
  (refs #85)
- AgentReview v2: explicit, non-destructive v1 -> v2 target-profile
  migrator (`app/agent_review/profile_migration_v1_v2.py` +
  `scripts/migrate-agent-review-profile-v1-v2.py`); never fabricates
  required checks, must-review rules, or contract hashes, and never runs
  automatically (refs #85)
- AgentReview v2: minimal, hash-pinned offline toolrepo lock
  (`requirements-agent-review.lock`,
  `scripts/install-agent-review-toolrepo.sh`); installs only what
  `app/agent_review` imports (`pydantic`, `PyYAML`, and pydantic's own
  dependencies), verified installable in a clean venv with
  `pip --require-hashes` (refs #85)
- `docs/AGENT_REVIEW_V2_TARGET_PROFILE.md` and
  `docs/AGENT_REVIEW_V2_INSTALLATION.md` document the loader, migrator, and
  installation contract
- AgentReview v2: typed, deny-unknown fragment manifest
  (`app/agent_review/manifest_v2.py`) with a structural losslessness
  invariant -- every must-review fragment is referenced by exactly one
  chunk or explicitly accounted for by a degradation cause; published as
  `schemas/agent-review/v2/agent-review.manifest.v2.schema.json` (refs #84)
- AgentReview v2: lossless line-range multi-chunk planner
  (`app/agent_review/planner_v2.py`) with an exact (not merely heuristic)
  bin-packing decision procedure for required content, and honest
  three-way reason codes (`budget_exhausted` / `packing_search_exhausted`
  / `planner_limit_exceeded`) distinguishing proven infeasibility from a
  bounded search's inconclusive result (refs #84)
- AgentReview v2: git unified-diff acquisition and parsing
  (`app/agent_review/diff_acquisition_v2.py`) -- renames, binaries
  (including the real `GIT binary patch` format), submodules, missing
  trailing newlines, and truncated hunks all recognized structurally,
  verified against real `git diff --binary` output (refs #84)
- AgentReview v2: payload builder (`app/agent_review/payload_builder_v2.py`)
  turning a planned manifest's chunks into actual `ChunkPayloadV2`
  objects, with N-chunk propagation through the existing #83
  consumer/parser proven end-to-end (refs #84)
- `docs/AGENT_REVIEW_V2_CHUNKING.md` documents the manifest, planner, diff
  acquisition, and payload builder, including the one deliberately
  deferred piece (symbol/AST-aware grouping, explicitly optional in the
  issue) and the documented file-vs-line-range coverage granularity gap

### Notes

- `app/agent_review/contracts_v2.py` (PRs #81/#82) is unmodified; this
  delivery only wires its existing authority into new v2-only call paths
- the v1 pipeline (`v0.20.0`) is unmodified and remains the only active
  operational path; v2 is not yet wired into any CLI or workflow

## v0.20.0 - 2026-07-19 - AgentReview Quality Gate

### Added

- deterministic post-synthesis quality gate with
  `review-quality-gate.json` as the canonical decision authority
- deterministic PR brief, bounded per-chunk payloads and payload manifest
- review telemetry, false-positive signatures and human-reviewable contract
  suggestions that remain `manual_only`
- offline E2E coverage from intake/redaction through telemetry

### Changed

- AgentEscala consumption contract now requires an immutable lowercase full
  commit SHA and fail-closed gate validation
- chunk payload contracts preserve validation risks, synthesizer facts,
  provenance and strict response-compatible chunk identity
- runtime-reported default version advanced from `0.19.0` to `0.20.0`

### Security

- no AgentReview execution on CT102
- no direct provider, `/v1/chat/ingest`, deploy, SSH, Docker or GitHub write
  call from the offline AIOps CLIs
- no automatic contract update, remediation, approval or merge
- sanitized artifacts reject secrets and local absolute-path leakage

### Release

- signed RC and final tags target
  `13695c73d1da9f16eba5c20e6478e7d51aefbb45`
- final GitHub release is non-draft, non-prerelease and signature-verified
- CT102 reported `0.20.0` with health, readiness, metrics, database, providers
  and action catalog ready
- rollback remains `v0.19.0`

## v0.19.0 - 2026-06-02 - AgentReview E2E and CT102 transition

### Added

- offline AgentReview intake, redaction, semantic chunk planning, structured
  chunk parsing and deterministic final synthesis
- AgentEscala thin-wrapper E2E validation on CT104
- explicit CT104 toolrepo and CT102 runtime environment boundaries

### Changed

- CT102 runtime-reported version advanced to `0.19.0`
- production health, readiness, metrics, stores, providers and action catalog
  were validated with documented rollback evidence

## v0.18.0 - AIOps readonly/chat checkpoint

### Added

- chat/OpenWebUI com intents AIOps determinísticas em pt-BR para diagnose, runs, approvals e status
- GitHub Agent Review com `/agent review`, `/agent review llm` e `/agent ask`
- follow-up contextual separado para `/agent ask`
- resposta pública em pt-BR por padrão com fallback seguro via `GITHUB_STEP_SUMMARY`
- diagnóstico severity-aware com findings enriquecidos e baseline temporal simples

### Changed

- runner read-only mantido em allowlist estrutural e fail-closed
- documentação alinhada ao fluxo canônico da fase readonly/chat
- review e chat seguem sem execução de código do PR, SSH, shell livre, deploy automático ou ações não allowlisted

### Security

- sem shell livre
- sem `docker exec`
- sem PromQL livre
- sem persistir `command`, `argv`, tokens, headers ou payloads brutos sensíveis
- sem GitHub Bridge real, Local Agent Bridge genérico ou Claude/Codex Bridge

### Validation

- suíte completa e scripts de validação passam
- catálogo de actions validado no startup e no CI
- composes base e blue/green seguem válidos
- redaction e fallback do `/agent ask` permanecem cobertos por teste

### Known limitations

- o GitHub Agent Review ainda depende de permissão para comentar no PR
- quando o comentário não pode ser publicado, o fallback seguro vai para `GITHUB_STEP_SUMMARY`
- o runner continua estritamente read-only nesta fase

### Next: agent-router-api

O próximo foco recomendado é `agent-router-api`, com fronteiras explícitas entre chat,
diagnóstico e qualquer superfície futura de execução.
