# Cross-repository dissolution matrix — AIOps → AgentReview/Router/HomeOps/CAEM (#351)

Refs #351 (slices A and D), parent roadmap #46 (read as updated 2026-09-27T23:14Z, including its
Advisory/Assured product model), repository-identity decision owner #358. This
document is **not** part of the RI-A (`docs/RI_A0_CAEM_REUSE_MATRIX.md`/`RI_A1`/`RI_A2`, epic
#126) lineage — that epic is itself one of the capabilities disposed of below, not a template for
this one.

**Zero functional/runtime change.** Documentation only: no code, migration, deploy, CT102 change,
repository rename, extraction into a new `agentreview` repository, or deletion of any file.

Bare issue/PR references (`#N`) always mean **this** repository (`mglpsw/aiops-orchestrator`).
References to any other repository are always fully qualified (`mglpsw/<repo>#N`).

## Authority by question type

Each field below is only as strong as the authority that can answer it. This matrix is a
projection of that evidence, not a new authority over it — in particular it never clears a
limitation recorded by the underlying artifact.

| Question | Authority | Not an authority |
|---|---|---|
| Does a file/module exist? | live source at the stated SHA | module or directory name |
| Who calls it? | source call graph / imports at the stated SHA | naming similarity, docstrings |
| Is an issue open/closed, and why? | forge (`gh issue view --json state,stateReason`) | issue title |
| Is a service running / deployed? | live runtime observation (owned by #19) | source, docs, historical deployment records |
| Does an external consumer exist? | the external repository's own source/workflows | this repository's docs |
| What limits an evaluation result? | the evaluation artifact itself (e.g. `reports/…`) | this matrix |
| Who is the final owner? | #46/#351 and the owning repository | this matrix alone |

Live bases used, verified `2026-09-27` via `git rev-parse`/`gh api repos/<repo>/branches/<default>`:

| Repo | Branch | HEAD verified this round |
|---|---|---|
| `mglpsw/agent-router-api` | `master` | `a6ea6ba5fa0335cb77852e03d77cd9cd4ed15d6e` |
| `mglpsw/aiops-orchestrator` | `master` | `9abcde6420a59b814b5faaff10ca5904c5d23370` |
| `mglpsw/homelab` | `main` | `10352e9b040db6a20edb8ebf1ffd2812deb3e138` |
| `mglpsw/caem` | `main` | `854e321a99170eebd7795cb50d2e5b5e0e94db2f` |
| `mglpsw/AgentEscala` | `develop` | `75bcc1af370a35df61e018406ef2190197759264` (moved from an earlier checkpoint `0ddfbe4b…` via an unrelated UX/IA-registry commit — no architectural conflict with this matrix) |
| `mglpsw/sacr-as` | `main` | `61003a120e540f92594c859f0888955f672ffa77` |
| `mglpsw/interleitos` | `main` | `8207b159e190a0f694b70d249eea31542e11ea9c` |

`FINAL_OWNER_ASSIGNED` (this document assigns a semantic owner) is distinct from
`MIGRATION_IMPLEMENTED` (code/docs actually moved), `CONSUMER_CUTOVER_COMPLETE` (a real consumer
switched), and `LEGACY_RETIRED` (the prior surface is gone). Each row states which of these is
actually true today — most rows here are `FINAL_OWNER_ASSIGNED` only.

## Repository identity

```text
ProductIdentity != RepositoryLocator
```

- **Product now:** AgentReview. Current docs, roadmap, new modules and new issues should present
  AgentReview as the product; "AIOps" appears only in legacy, historical, compatibility,
  retirement or migration context.
- **Repository locator now:** `mglpsw/aiops-orchestrator`, kept as a historical/compatibility
  locator. **No rename in this round.**
- **Why not now:** the dissolution is not complete (#351, #19), and live source still binds the old
  slug as an identity, not just a URL — `app/caem_consumer/f0.py:82`
  (`EXPECTED_CONSUMER_REPOSITORY = "mglpsw/aiops-orchestrator"`, enforced at `f0.py:564`). Renaming
  now would add an identity migration to this documentation change.
- **Owner of any future rename:** #358 (OPEN) — #46 intro/§4: #358 may only run the identity
  census/planning; "rename continua sem grant". Keeping the old slug indefinitely is a valid
  outcome of #358, not a failure. A rename requires, at minimum, #351 source separation, #19
  runtime inventory, a working release consumed by the first-wave targets, and an identity census
  (Actions, reusable workflows, clone URLs, repository strings, package names, the F0 consumer
  identity handled without rewriting history, rollback).

## Capability matrix

### AIOps Runtime

```text
current_owner: aiops-orchestrator — app/main.py FastAPI app, app/agent_router/ (local AIOps
               Diagnostic Engine v1 — NOT the mglpsw/agent-router-api inference service),
               app/services/orchestrator.py + app/models/database.py (legacy chat/task orchestrator),
               app/services/provider_registry.py, app/adapters/*
source_state: IMPLEMENTED at master@9abcde6
runtime_deployment_state: UNKNOWN_PENDING_19 — no live runtime was observed for this matrix;
               docs/PROJECT_STATUS.md states its deployment record is "a historical record, not a
               current health assertion — runtime health must be observed live"
adapters (same directory, different lifecycle):
               - quarantined executors: app/adapters/executor_ssh.py, executor_local.py, docker.py
                 (carry the "LEGACY / NOT USED" marker; registered as executor providers in
                 provider_registry.py:43-45)
               - LIVE LLM adapters: app/adapters/claude.py, codex.py, ollama.py,
                 openai_compatible.py — instantiated by ProviderRegistry.initialize()
                 (provider_registry.py:36-39) and called directly by the orchestrator; see
                 "Legacy in-process inference" below
current_consumers: UNKNOWN_PENDING_19 — docs/RI_A1_ADR_OWNERSHIP_MAP.md records the callers of the
               legacy HTTP endpoints as externally unknown by design. One known historical caller
               is the Router-side AIOpsRouterAdapter shim in mglpsw/agent-router-api (direction
               Router → this repo), already inert per that repository's
               docs/AIOPS_ORCHESTRATOR_CONTRACT.md; it is not claimed to be the only caller
final_owner: none (no future semantic owner)
disposition: RETIRE pending #19
migration_dependency: #19 live inventory + backup/restore/rollback + explicit grant; #351 slice C
               ("retirada controlada de source AIOps")
countermodels: - app/agent_review/ (AgentReview v1/v2) is NOT part of this runtime —
                 RI_A1/ADR-001 verified zero imports of app.agent_review.* from RUNTIME modules
               - "no live deployment observed" != "not deployed": retirement must not proceed on
                 this matrix's evidence alone
retirement_gate: #19 produces live inventory/backup/rollback with its own grants before any
               service retirement; nothing here authorizes CT102 changes
evidence: #19 (OPEN), #351 (OPEN), app/services/provider_registry.py:31-45,
               docs/PROJECT_STATUS.md:82-83, docs/RI_A1_ADR_OWNERSHIP_MAP.md (legacy endpoints row)
limitations: source-observed only; deployment and external callers are UNKNOWN until #19
status: FINAL_OWNER_ASSIGNED only
```

### Legacy in-process inference (direct provider path)

```text
current_owner: AIOps Runtime (this repository)
current_implementation: ProviderRegistry.initialize() instantiates Ollama, Claude,
               OpenAI-compatible and Codex adapters (app/services/provider_registry.py:36-39);
               app/services/orchestrator.py calls them directly via registry.get_llm()
               (lines 236, 275, 311 — classify, plan, answer), without the Agent Router
source_state: IMPLEMENTED; runtime_deployment_state: UNKNOWN_PENDING_19
current_consumers: the legacy chat/task orchestrator itself; its external callers are
               UNKNOWN_PENDING_19 (same as AIOps Runtime above)
final_owner: Agent Router for inference; the orchestrator capability that uses it has no successor
disposition: RETIRE with the AIOps Runtime (#19). Any inference need that survives the inventory
               is MOVE → Agent Router, never re-hosted as another direct-provider path
migration_dependency: #19 live inventory; cutover of any surviving caller to the Agent Router
countermodels: while this path exists, "Agent Router is the only inference plane" is false —
               it is the canonical/final plane, not yet the only current one
retirement_gate: #19 proves no live caller remains, or remaining callers are cut over to the
               Agent Router
evidence: app/services/provider_registry.py:31-39, app/services/orchestrator.py:236/275/311
limitations: whether this path is reachable in any live deployment is UNKNOWN_PENDING_19
status: FINAL_OWNER_ASSIGNED only — residual direct-provider path, not migrated
```

### AgentReview v1

```text
current_owner: aiops-orchestrator, app/agent_review/ (v1 line) — per #46 the historical
               LEGACY_ADVISORY_BASELINE: debt/quality fixes only, no new trust architecture;
               product successor after the first Assured release is #357
current_implementation: released, baseline v0.22.0@2ce1f45768b8779cb48ef8a302d4ed796349f0e5
               consumed by mglpsw/AgentEscala#802 and mglpsw/AgentEscala#803 (per #46 §1)
current_consumers: mglpsw/AgentEscala — .github/workflows/agent-review.yml pins this repository
               at the v0.22.0 SHA and transports through the Agent Router
               (scripts/call-agent-router.sh → /v1/chat/completions); active on every internal PR
               (run history, per mglpsw/AgentEscala#859)
final_owner: AgentReview
disposition: KEEP in AgentReview → freeze pending #221
migration_dependency: #221 (material-debt fix, immutable release, repin/canary, freeze)
countermodels: v1 does not become default/required by administrative decision (#46 §2)
retirement_gate: n/a — kept, not retired; freeze is a maintenance state, not removal
evidence: #221 (OPEN), #46 §1/§3 (M1), mglpsw/AgentEscala#859
limitations: freeze timing depends on #232/#307/#315/#343, not on this matrix
status: FINAL_OWNER_ASSIGNED; MIGRATION_IMPLEMENTED in AgentEscala (live GA consumer)
```

### AgentReview v2

```text
current_owner: aiops-orchestrator, app/agent_review/ (v2 line) — per #46 the architecture of the
               ASSURED profile; first operational path #80 → #350 → #199
current_implementation: C3 integrated (#304/PR #349), C4-Q integrated (#333/PR #352); C4-S+E
               (#301) is the current open core slice per #46 §1 — its S0 contract is ratified in
               PR #355, which is still Draft/open and not integrated in master
current_consumers: mglpsw/AgentEscala in shadow only — agent-review-v2-evidence.yml,
               agent-review-v2-analysis.yml, agent-review-v2-publish.yml are wired end to end, but
               repository variable AGENT_REVIEW_V2_ROUTER_ENABLED=false (no real Router call yet)
               and AGENT_REVIEW_V2_MODE=shadow (per mglpsw/AgentEscala#859). No production
               consumer. mglpsw/caem and mglpsw/sacr-as are pre-integration
final_owner: AgentReview
disposition: KEEP in AgentReview
migration_dependency: #301 → #298 → #314 → #350 → #203 → #204 → #205 (first Assured release),
               then #357 post-release convergence (per #46 §4's own sequence)
countermodels: v2 does not become default/required check by this reconciliation (#46 §2)
retirement_gate: n/a — kept
evidence: #46 §1/§4/§5, PR #349, PR #352, mglpsw/AgentEscala#859
limitations: AgentEscala evidence is workflow source + run history + repository variables, not an
               end-to-end Router-backed review
status: FINAL_OWNER_ASSIGNED; MIGRATION_IMPLEMENTED (shadow) in AgentEscala; not
               CONSUMER_CUTOVER_COMPLETE
```

### Review Intelligence generic (the former "AIOps Review Intelligence" control plane)

```text
current_owner: none currently building it — the implementation epic is closed
current_implementation: two distinct things, not one:
               (a) planning documents RI-A0/A1/A2 (#122/#118/#120, CLOSED/COMPLETED):
                   docs/RI_A0_CAEM_REUSE_MATRIX.md, docs/RI_A1_ADR_OWNERSHIP_MAP.md,
                   docs/RI_A2_THREAT_MODEL.md — each self-declares "Zero functional/runtime change"
               (b) EXECUTABLE RI-B0a artifacts that exist in source: app/ri_b0a/reuse_manifest.py,
                   config/ri/ri-b0a-2-reuse-manifest.json,
                   scripts/generate-ri-b0a-2-reuse-view.py, tests/ri_b0a/; plus the RI-B0A-1 CAEM
                   pin loader app/caem_consumer/f0.py (see CAEM row). RI-B0a/B0f (#119/#121) and
                   the parent epic (#126) are CLOSED/NOT_PLANNED
runtime_service: never deployed — no database, HTTP API, sync or workers were built
current_consumers: no runtime consumer; the RI-B0a artifacts are exercised by their own
               generator script and tests
final_owner: none for the service — RETIRE unless a concrete claim + concrete consumer exists
disposition: - RI service: RETIRE (never built; #46 §6 "Fora do novo escopo do produto
                 (not_planned, não entregue): … #126 … (RI/proof executor/Workbench/persistência)")
               - RI-B0a artifacts: KEEP as historical/provenance (#46 intro allows preservation
                 "como provenance, shared primitive ainda consumida ou owner explícito de
                 retirement") until the #351 slice A census, then RETIRE,
                 or explicitly rehome any generic primitive (precedent: strict JSON helpers
                 already extracted from app.caem_consumer.f0 into app/common/strict_json.py)
migration_dependency: #351 slice A census for the RI-B0a artifacts; none for the service
countermodels: - "no deployed RI service" != "no RI implementation artifacts"
               - a future claim citing "AIOps Review Intelligence" as a dependency is
                 STOP_NEW_CROSS_CUTTING_SERVICE
retirement_gate: service: already closed (#119, #121, #126 = NOT_PLANNED); artifacts: #351 slice A
               disposition, in its own PR
evidence: gh issue state for #118 (CLOSED/COMPLETED), #119 (CLOSED/NOT_PLANNED), #120
               (CLOSED/COMPLETED), #121 (CLOSED/NOT_PLANNED), #122 (CLOSED/COMPLETED), #126
               (CLOSED/NOT_PLANNED); #46 §6; the source paths listed above at master@9abcde6
limitations: #46 §6's not_planned list names #119/#121/#123/#124/#125/#126 explicitly but not
               #118/#120/#122 (the docs-only RI-A0/A1/A2 issues) — those three remain CLOSED/
               COMPLETED because each delivered a real, zero-side-effect documentation artifact;
               #46 calls such prior work "specs and evidence" that "permanecem acessíveis". This
               matrix preserves that distinction rather than reclassifying them
status: LEGACY_RETIRED at the implementation-epic level (#126/#119/#121); RI-B0a artifacts still
               present, disposition pending #351-A; planning docs preserved as historical/formative
               evidence, not as a live dependency
```

### Review-specific evals / calibration / replay / false-positive evaluation

```text
current_owner: aiops-orchestrator (evals/agent_review_v2/harness.py)
current_implementation: benchmark harness exists; obligation/claim-guided evolution owned by #353
               (M7), quality/calibration by #256, impact context by #273
dependency_direction: the harness CONSUMES AgentReview, not the reverse — harness.py imports
               app.agent_review.consumer_v2 (line 38), readiness_decision_v2 (line 52) and
               synthesis_v2 (line 59); no module under app/ imports evals
current_consumers: qualification tooling, not the review runtime — scripts/run-agent-review-v2-evals.py
               (CI gate: .github/workflows/ci.yml:87, `--check`), scripts/generate-benchmark-report.py,
               scripts/compare-review-observations.py, scripts/materialize-benchmark-case.py,
               scripts/generate-benchmark-corpus-manifest.py,
               scripts/validate-benchmark-corpus-safety.py, tests/evals/*
final_owner: AgentReview (as an external qualification gate, never a runtime/readiness dependency)
disposition: KEEP in AgentReview — scoped to concrete claims only (#353/#256/#273), never a
               "global AI memory" or "autonomous knowledge brain" (consistent with #46 §6's RI
               retirement above)
migration_dependency: none — already in the correct repository/product; #351 slice B must keep it
               out of the runtime install path
countermodels: - treating the harness as a runtime dependency would drag qualification tooling into
                 AgentReview's install/run path
               - any proposal to persist cross-project opinion/knowledge beyond a concrete review
                 claim reopens the retired Review Intelligence scope
retirement_gate: n/a
evidence: #353, #256, #273, #46 §3 (M7), the import and CI lines cited above
limitations (carried forward verbatim from reports/agent-review-v2-benchmark-summary.json, not
               re-evaluated here):
               - sample size is 6 semantic_positive / 4 semantic_safe_counterexample cases — a
                 descriptive baseline, not a statistically powered study
               - exact-severity correlation: codex_local's severity diverged from ground truth on
                 3/6 positive cases (all location-correct)
               - stale/pipeline_integrity/transport_or_dlp_stop cases are Lane 1/2A-only by design
                 and excluded from the detection denominators
               - Lane 4 (human) is deferred; no human precision/recall is reported
status: FINAL_OWNER_ASSIGNED — already correctly placed pre-#351; not promotion evidence
```

### Infrastructure observation (physical entity registry, ObservationSnapshot)

```text
current_owner: homelab, HL-HO series (mglpsw/homelab#61 parent, mglpsw/homelab#62
               contract/vocabulary, mglpsw/homelab#63 registry, mglpsw/homelab#64 ObservationSnapshot
               of CT200, mglpsw/homelab#65 reconciliation + maintenance_projection_v1)
current_implementation: mglpsw/homelab#62, mglpsw/homelab#63, mglpsw/homelab#64 and
               mglpsw/homelab#65 all OPEN — foundation not yet built
current_consumers: none yet (no ObservationSnapshot exists to consume)
final_owner: HomeOps (mglpsw/homelab)
disposition: MOVE was already the design — this repository never implemented physical
               observation; nothing to move except the intent previously described in the
               now-not_planned #126 epic
migration_dependency: mglpsw/homelab#62 → mglpsw/homelab#63 → mglpsw/homelab#64 →
               mglpsw/homelab#65, in that order (mglpsw/homelab
               docs/homeops-control-plane.md sequencing)
countermodels: HomeOps must not become `HomeOps → AIOps service → Router`; the sanctioned shape is
               `Registry → ObservationSnapshot → reconciliation → maintenance_projection_v1 →
               Incident Journal → bounded Ops Analysis → Agent Router (when inference is useful) →
               advisory → explicit human grant`
retirement_gate: n/a for this repository (it never held this capability); the gates are
               mglpsw/homelab#62–mglpsw/homelab#65 acceptance criteria
evidence: mglpsw/homelab#58, mglpsw/homelab#61, mglpsw/homelab#62, mglpsw/homelab#63,
               mglpsw/homelab#64, mglpsw/homelab#65 (all OPEN this round);
               mglpsw/homelab docs/homeops-control-plane.md (merged via mglpsw/homelab#66)
limitations: nothing is implemented in homelab by this document; the dissolution framing there is
               mglpsw/homelab#68 (OPEN, unmerged)
status: FINAL_OWNER_ASSIGNED
```

### Infrastructure incidents (Incident Journal)

```text
current_owner: mglpsw/homelab#58 (HL-AIOPS-01 — the "AIOPS" prefix is historical naming, not a
               claim that the AIOps product owns it)
current_implementation: OPEN, planned/downstream of mglpsw/homelab#65 — stated in mglpsw/homelab#58's own
               body (HOMEOPS-RECONCILIATION-20260926 block: integration preferred after
               mglpsw/homelab#65)
current_consumers: none yet
final_owner: HomeOps (mglpsw/homelab)
disposition: MOVE (semantic owner label only; mglpsw/homelab#58 keeps its history and name)
migration_dependency: mglpsw/homelab#65 must land first; mglpsw/homelab#58 is not skipped ahead
countermodels: none — do not rename/dissolve mglpsw/homelab#58's history to erase its origin
retirement_gate: mglpsw/homelab#65 acceptance, then mglpsw/homelab#58's own criteria
evidence: mglpsw/homelab#58 (OPEN, body block cited above), mglpsw/homelab#65 (OPEN)
limitations: this repository has no Incident Journal implementation to retire
status: FINAL_OWNER_ASSIGNED
```

### Operational analysis (bounded Ops Analysis, advisory recommendation)

```text
current_owner: none implemented anywhere yet
current_implementation: not built — depends on the HomeOps foundation
               (mglpsw/homelab#62–mglpsw/homelab#65) and,
               when useful, the Agent Router as an inference service it consumes
current_consumers: none
final_owner: HomeOps, as a consumer of the Agent Router (never the reverse)
disposition: DEFER — no implementation before mglpsw/homelab#65 is proven
migration_dependency: full HL-HO chain, then explicit human grant per operation
countermodels: `ConsumedProjection(X) != TruthOfProjection(X)`; the Router may record only a
               `consumed_capacity_projection_digest`, never the truth of the projection itself;
               no inference-receipt v3 is introduced by this matrix or authorized elsewhere
retirement_gate: n/a — not yet built
evidence: mglpsw/homelab#61–mglpsw/homelab#65 (OPEN); forward contract proposed in mglpsw/homelab#68
               (docs/homeops-control-plane.md §0.5, OPEN, unmerged)
limitations: purely a forward contract statement; no code exists to evaluate
status: FINAL_OWNER_ASSIGNED (contract-only)
```

### ProjectOps

```text
current_owner: none in this repository (RI_A1/ADR-001: "Not implemented in this repository at all
               (no app/ module, no docs beyond the roadmap reference)")
current_implementation: none
current_consumers: none
final_owner: none assigned
disposition: DEFER — no automatic successor; #46 §6 lists #91–#95 (ProjectOps) as not_planned
migration_dependency: requires a concrete claim + concrete consumer to reopen anywhere
countermodels: creating a new ProjectOps-shaped service in any repository without a named consumer
               is STOP_NEW_CROSS_CUTTING_SERVICE. mglpsw/interleitos has its own local
               "CI Intelligence/ProjectOps" track (mglpsw/interleitos#45); it is an
               InterLeitos-owned initiative, not a successor to this one
retirement_gate: n/a — never built
evidence: #46 §6, RI_A1/ADR-001, mglpsw/interleitos docs/roadmap.md
limitations: none recorded
status: LEGACY_RETIRED at the tracker level (#91–#95 not_planned); no successor exists to assign
```

### Workbench

```text
current_owner: none in this repository; described only as a future cockpit/projection surface
current_implementation: none confirmed built
current_consumers: none — not a direct Router consumer (mglpsw/agent-router-api#111 compatibility
               checkpoint)
final_owner: none assigned
disposition: RETIRE_OR_REASSESS — no automatic successor; #46 §6 lists Workbench work as not_planned
migration_dependency: none
countermodels: any receipt/projection field added "for Workbench" without a named, implemented
               consumer reopens a retired scope
retirement_gate: n/a — never built
evidence: #46 §6; mglpsw/agent-router-api#111; mglpsw/agent-router-api docs/AGENT_ROUTER.md
               (Workbench row reconciled in mglpsw/agent-router-api#116, OPEN)
limitations: no Workbench prototype was found in the seven repositories checked; repositories
               outside that set were not searched
status: LEGACY_RETIRED / no successor
```

### Generic evidence semantics (identity, binding, qualification, claims, obligations,
### countermodels, authority, stale/reuse/invalidation)

```text
current_owner: mglpsw/caem (upstream semantic authority)
current_implementation: CAEM 3.0 F0 pinned here via config/caem/caem-3.0-f0.pin.json; F2
               (mglpsw/caem#97) and RK-1 (mglpsw/caem#74) OPEN; independent reviewer lanes /
               AgentRouter capability broker (mglpsw/caem#63) OPEN
current_local_consumer: the F0 pin loader app/caem_consumer/f0.py, which declares itself the
               #119 RI-B0A-1 slice (f0.py:3) and binds this repository's slug as the consumer
               identity (f0.py:82). Its only non-test caller is the pin verifier
               scripts/verify-caem-f0-pin.py
AgentReview_relation: vocabulary and design references only — no module under app/agent_review/
               imports app.caem_consumer (the one textual match,
               app/agent_review/trusted_check_supervisor_v2.py:110, is a docstring citing f0's
               discipline). AgentReview runtime consumption of CAEM F0 is NOT proven; it is
               prospective until a real callsite exists
final_owner: CAEM
disposition: KEEP in CAEM — CAEM is upstream semantic authority, never a runtime
migration_dependency: mglpsw/caem#63 (independent reviewer lanes/broker), mglpsw/caem#74 (RK-1),
               mglpsw/caem#97 (F2)
countermodels: - CAEMDesignInfluence != CAEMRuntimeConsumption
               - CAEM must never gain GPU/Ollama/review-engine/routing/scheduler/Incident
                 Journal/CI-planner/review-database runtime (reaffirmed in the 2026-09-27
                 CURRENT/RECONCILIATION comment on mglpsw/caem#63)
retirement_gate: n/a for CAEM itself. The local F0 carrier follows the RI-B0a disposition
               (#351 slice A); its consumer-identity binding is an input to #358
evidence: mglpsw/caem#63, mglpsw/caem#74, mglpsw/caem#97 (all OPEN);
               app/caem_consumer/f0.py:3/82/564;
               scripts/verify-caem-f0-pin.py:19; RI_A0 erratum (F0 pin location)
limitations: - this document does not audit mglpsw/caem's own repository
               - #46 §6 keeps "o pin/consumer CAEM compartilhado que AgentReview realmente usa"
                 preserved. That rule is unaffected here — nothing is removed. This row only
                 records, from source, that what AgentReview demonstrably uses today is CAEM
                 vocabulary/design, while the F0 pin's current caller is the pin verifier; if a
                 real AgentReview → F0 callsite exists outside the paths searched, #46's
                 preservation rule applies to it unchanged
status: FINAL_OWNER_ASSIGNED for the semantics; no AgentReview → CAEM runtime cutover exists
```

### Inference execution

```text
current_owner: mglpsw/agent-router-api — the canonical/final inference execution plane
current_state: NOT yet the only current inference path — the legacy in-process direct-provider
               path in this repository still exists (see "Legacy in-process inference")
current_implementation: OpenAI-compatible API, preset resolution, provider/model registry,
               admission, routing policy, fallback, `agent-router.inference-receipt.v2`
               (mglpsw/agent-router-api#99, mglpsw/agent-router-api#111; F2-A integrated at
               master@a6ea6ba)
current_consumers: mglpsw/AgentEscala's AgentReview lanes via scripts/call-agent-router.sh →
               /v1/chat/completions (per mglpsw/AgentEscala#859). AgentReview is the canonical
               consumer (mglpsw/agent-router-api#116). The Router-side AIOpsRouterAdapter shim is
               NOT a consumer of the Router — it is a deprecated Router → aiops-orchestrator
               adapter, already inert
final_owner: Agent Router
disposition: KEEP in Agent Router
migration_dependency: retirement or cutover of the legacy in-process inference path (#19)
countermodels: no "policy brain"/semantic reducer/review planner/infrastructure
               collector/incident memory/global intelligence may enter the Router roadmap;
               mglpsw/agent-router-api#65 already records the review planner/reducer as superseded
               (2026-08-24 reconciliation)
retirement_gate: n/a
evidence: mglpsw/agent-router-api@a6ea6ba:app/agent_router/main.py:2103
               (`@app.post("/v1/chat/completions")`) and app/agent_router/inference_receipt.py;
               mglpsw/agent-router-api#65, mglpsw/agent-router-api#99, mglpsw/agent-router-api#111,
               mglpsw/agent-router-api#116. NOT this repository's
               app/agent_router/main.py, which is the local AIOps Diagnostic Engine
               (RI_A1, "A naming collision worth flagging explicitly")
limitations: receipt v3 is not authorized by this or any document in this round
status: FINAL_OWNER_ASSIGNED; the "only inference plane" property awaits the legacy-path
               retirement (#19)
```

## First-wave target adoption (informational — no implementation here)

| Target | Evidence | Disposition |
|---|---|---|
| `mglpsw/AgentEscala` | v1 lane (`agent-review.yml`, `agent-review-publish.yml`) active, pinned to `v0.22.0`, Router-only transport; v2 shadow trio wired, `AGENT_REVIEW_V2_ROUTER_ENABLED=false`; `aiops-runner-smoke.yml` (on-demand CT104 infrastructure smoke) and `issue-aiops.yml` (issue triage, already Router-based) active — all per `mglpsw/AgentEscala#859` workflow source + run history | v1: GA consumer, retirement `DEFER` until v2 promotion; v2: shadow, not cut over. **None of the four legacy-named workflows is a `RETIRE` candidate today** |
| `mglpsw/caem` | No AgentReview-specific workflow found; `mglpsw/caem#63` (broker) is the open contract | Dogfooding advisory adoption path exists on paper; not yet cut over |
| `mglpsw/sacr-as` | Only `.github/workflows/validate.yml`; no AgentReview workflow | `DEFER` — adoption contract with synthetic-corpus and DLP/clinical constraints proposed in `mglpsw/sacr-as#45` (OPEN) |
| `mglpsw/interleitos` | Only `ci.yml`/`ct104-deployment.yml`; no AgentReview workflow | Deferred consumer, not a first-wave release gate (#46's 2026-09-25 addendum; `mglpsw/interleitos#138`, OPEN) |

## What this document does not claim

- It does not claim any `MOVE`/`RETIRE` disposition has been implemented by this PR — only
  `FINAL_OWNER_ASSIGNED` for the rows so marked.
- It does not claim any runtime state: every deployment/external-consumer field is
  `UNKNOWN_PENDING_19` unless a live observation is cited.
- It does not close #351, #19, #46, #358, or any issue in another repository.
- It does not authorize CT102 access, secrets, runners, provider credentials, deploy, or a
  repository rename.
- It does not supersede #46 as scope authority — #46 remains the single source of
  priority/Go-No-Go for this repository.
