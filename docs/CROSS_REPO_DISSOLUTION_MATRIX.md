# Cross-repository dissolution matrix — AIOps → AgentReview/Router/HomeOps/CAEM (#351)

Refs #351 (slices A and D), parent roadmap #46. This document is **not** part of the RI-A
(`docs/RI_A0_CAEM_REUSE_MATRIX.md`/`RI_A1`/`RI_A2`, epic #126) lineage — that epic is itself one of
the capabilities disposed of below, not a template for this one.

**Zero functional/runtime change.** Documentation only: no code, migration, deploy, CT102 change,
repository rename, extraction into a new `agentreview` repository, or deletion of any file. Every
claim below is backed by a real issue state (`gh issue view --json state,stateReason`), a real file
path/workflow filename observed in the target repository, or an explicit reference to an existing
normative document (`#46`, `#351`, `RI_A0`/`RI_A1`). Live bases used, verified `2026-09-27` via
`git rev-parse`/`gh api repos/<repo>/branches/<default>`:

| Repo | Branch | HEAD verified this round |
|---|---|---|
| `mglpsw/agent-router-api` | `master` | `a6ea6ba5fa0335cb77852e03d77cd9cd4ed15d6e` |
| `mglpsw/aiops-orchestrator` | `master` | `9abcde6420a59b814b5faaff10ca5904c5d23370` |
| `mglpsw/homelab` | `main` | `10352e9b040db6a20edb8ebf1ffd2812deb3e138` |
| `mglpsw/caem` | `main` | `854e321a99170eebd7795cb50d2e5b5e0e94db2f` |
| `mglpsw/AgentEscala` | `develop` | `75bcc1af370a35df61e018406ef2190197759264` (moved from the mission's checkpoint `0ddfbe4b…`; diff is an unrelated UX/IA-registry feature, `docs/781e0_information_architecture.md` and friends — no architectural conflict with this matrix) |
| `mglpsw/sacr-as` | `main` | `61003a120e540f92594c859f0888955f672ffa77` |
| `mglpsw/interleitos` | `main` | `8207b159e190a0f694b70d249eea31542e11ea9c` |

`FINAL_OWNER_ASSIGNED` (this document assigns a semantic owner) is distinct from
`MIGRATION_IMPLEMENTED` (code/docs actually moved), `CONSUMER_CUTOVER_COMPLETE` (a real consumer
switched), and `LEGACY_RETIRED` (the prior surface is gone). Each row states which of these is
actually true today — most rows here are `FINAL_OWNER_ASSIGNED` only.

## Capability matrix

### AIOps Runtime

```text
current_owner: aiops-orchestrator (app/agent_router/ Diagnostic Engine v1, app/models/database.py
               chat/task orchestrator, app/adapters/* legacy executors)
current_implementation: running FastAPI process, quarantined adapters marked
               "LEGACY / NOT USED BY AIOPS RUNNER V1" (RI_A1/ADR-001, verified there against
               real `grep -rl` results, not repeated here)
current_consumers: agent-router-api's now-deprecated `AIOpsRouterAdapter`/`agent-router:aiops`
               shim (agent-router-api docs/AIOPS_ORCHESTRATOR_CONTRACT.md — already marked
               "histórico — descreve um fluxo desativado")
final_owner: none (no future semantic owner)
disposition: RETIRE pending #19
migration_dependency: #19 inventory + backup/restore/rollback + explicit grant; #351 slice C
               ("retirada controlada de source AIOps")
countermodels: `app/agent_review/` (AgentReview v1/v2) is NOT part of this runtime — RI_A1/ADR-001
               already verified zero imports of `app.agent_review.*` from any RUNTIME module
retirement_gate: #19 produces inventory/backup/rollback with its own grants before any service
               retirement; nothing here authorizes CT102 changes
evidence: #19 (open), #351 (open), agent-router-api docs/AIOPS_ORCHESTRATOR_CONTRACT.md (shim
               already inert)
limitations: this document does not re-run the full source census of #351 slice A; it only
               reconciles the cross-repo disposition already implied by #46/#351/RI_A1
status: FINAL_OWNER_ASSIGNED only
```

### AgentReview v1

```text
current_owner: aiops-orchestrator, app/agent_review/ (v1 line)
current_implementation: released, baseline v0.22.0@2ce1f45768b8779cb48ef8a302d4ed796349f0e5
               consumed by AgentEscala#802/#803 (per #46 §1)
current_consumers: mglpsw/AgentEscala (workflows agent-review.yml, agent-review-publish.yml,
               observed in that repo's .github/workflows/ this round)
final_owner: AgentReview
disposition: KEEP in AgentReview → freeze pending #221
migration_dependency: #221 (material-debt fix, immutable release, repin/canary, freeze)
countermodels: v1 does not become default/required by administrative decision (#46 §2)
retirement_gate: n/a — kept, not retired; freeze is a maintenance state, not removal
evidence: #221 (open), #46 §1/§3 (M1)
limitations: freeze timing depends on #232/#307/#315/#343, not on this matrix
status: FINAL_OWNER_ASSIGNED (already substantively true pre-#351; this doc changes no code)
```

### AgentReview v2

```text
current_owner: aiops-orchestrator, app/agent_review/ (v2 line)
current_implementation: C3 integrated (#304/PR #349), C4-Q integrated (#333/PR #352); C4-S+E
               (#301) is the current open core slice per #46 §1
current_consumers: none in production yet — first-wave targets (AgentEscala, CAEM, SACR-AS) are
               pre-integration per #46's 2026-09-25 addendum; AgentEscala already carries
               `agent-review-v2-analysis.yml`/`agent-review-v2-evidence.yml`/
               `agent-review-v2-publish.yml` workflow files (evidence/analysis/publisher lanes),
               observed this round, but this matrix does not verify they are wired to a live gate
final_owner: AgentReview
disposition: KEEP in AgentReview
migration_dependency: #301 → #298 → #314 → #350 → #203 → #204 → #205 (per #46 §4's own sequence)
countermodels: v2 does not become default/required check by this reconciliation (#46 §2)
retirement_gate: n/a — kept
evidence: #46 §1/§4/§5, PR #349, PR #352
limitations: this document verifies workflow file names only, not their gating status or
               pass/fail history — that belongs to AgentEscala's own repo, see below
status: FINAL_OWNER_ASSIGNED
```

### Review Intelligence generic (the former "AIOps Review Intelligence" control plane)

```text
current_owner: none currently building it — the implementation epic is closed
current_implementation: RI-A0/A1/A2 (#122/#118/#120) are CLOSED/COMPLETED **planning documents
               only** (`docs/RI_A0_CAEM_REUSE_MATRIX.md`, `docs/RI_A1_ADR_OWNERSHIP_MAP.md`,
               docs/RI_A2_THREAT_MODEL.md) — each self-declares "Zero functional/runtime change".
               RI-B0a/B0f (#119/#121) and the parent epic (#126) are CLOSED/NOT_PLANNED — verified
               via `gh issue view --json state,stateReason` this round, not inferred from title
current_consumers: none (no database, no HTTP API, no sync, no workers were ever built per the
               RI docs' own disclaimers)
final_owner: none — RETIRE unless a concrete claim + concrete consumer exists
disposition: RETIRE unless concrete claim (per #46's own "Fora do novo escopo do produto
               (not_planned, não entregue): ... #126 ... (RI/proof executor/Workbench/persistência)")
migration_dependency: none — nothing to migrate, the runtime was never built
countermodels: a future claim citing "AIOps Review Intelligence" as a dependency is
               STOP_NEW_CROSS_CUTTING_SERVICE
retirement_gate: already closed (#119, #121, #126 = NOT_PLANNED); no separate gate needed since
               nothing was deployed
evidence: gh issue state for #118 (CLOSED/COMPLETED), #119 (CLOSED/NOT_PLANNED), #120
               (CLOSED/COMPLETED), #121 (CLOSED/NOT_PLANNED), #122 (CLOSED/COMPLETED), #126
               (CLOSED/NOT_PLANNED); #46 §6 consolidation list
limitations: #46 §6's not_planned list names #119/#121/#123/#124/#125/#126 explicitly but not
               #118/#120/#122 (the docs-only RI-A0/A1/A2 issues) — those three remain CLOSED/
               COMPLETED rather than NOT_PLANNED because they delivered a real, zero-side-effect
               documentation artifact each; #46 itself calls this class of prior work "specs and
               evidence" that "permanecem acessíveis". This matrix preserves that distinction
               rather than silently reclassifying #118/#120/#122 as if they too were not_planned —
               doing so would misrepresent what those three issues actually closed as
status: LEGACY_RETIRED at the implementation-epic level (#126/#119/#121); docs preserved as
               historical/formative evidence, not as a live dependency (per mission's own CAEM
               guidance in a different repo, applied here by analogy: historical implementation,
               counterexample source, formative evidence — not future dependency)
```

### Review-specific evals / calibration / replay / false-positive evaluation

```text
current_owner: aiops-orchestrator (evals/agent_review_v2/harness.py per RI_A1's own enumeration)
current_implementation: benchmark harness exists; obligation/claim-guided evolution owned by #353
               (M7), quality/calibration by #256, impact context by #273
current_consumers: internal to the review pipeline itself (readiness/synthesis), not an external
               service
final_owner: AgentReview
disposition: KEEP in AgentReview — explicitly scoped to concrete claims only (#353/#256/#273),
               never a "global AI memory" or "autonomous knowledge brain" (mission §14 prohibition,
               already mirrored by #46 §6's RI retirement above)
migration_dependency: none — already in the correct repository/product
countermodels: any proposal to persist cross-project opinion/knowledge beyond a concrete review
               claim reopens the retired Review Intelligence scope
retirement_gate: n/a
evidence: #353, #256, #273, #46 §3 (M7)
limitations: none identified this round
status: FINAL_OWNER_ASSIGNED — already correctly placed pre-#351
```

### Infrastructure observation (physical entity registry, ObservationSnapshot)

```text
current_owner: homelab, HL-HO series (#61 parent, #62 contract/vocabulary, #63 registry, #64
               ObservationSnapshot of CT200, #65 reconciliation + maintenance_projection_v1)
current_implementation: #62/#63/#64/#65 are all OPEN — foundation not yet built (verified this
               round via gh issue view)
current_consumers: none yet (no ObservationSnapshot exists to consume)
final_owner: HomeOps (homelab)
disposition: MOVE was already the design — this repo (aiops-orchestrator) never implemented
               physical observation; nothing to move except the *intent* previously described in
               the now-not_planned #126 epic and the still-open #58 (Incident Journal), which is
               explicitly sequenced downstream of #65, not skipped
migration_dependency: #62 → #63 → #64 → #65, in that order (mission §10 explicit sequencing)
countermodels: HomeOps must not become `HomeOps → AIOps service → Router`; the only sanctioned
               shape is `Registry → ObservationSnapshot → reconciliation → Incident Journal →
               bounded Ops Analysis → Agent Router (when inference is useful) → advisory →
               explicit human grant`
retirement_gate: n/a for aiops-orchestrator (it never held this capability); homelab's own gates
               are #62/#63/#64/#65 acceptance criteria, out of this repo's authority
evidence: homelab #58/#61/#62/#63/#64/#65 (all OPEN this round)
limitations: this document does not implement anything in homelab; see the separate homelab-side
               reconciliation PR for that repo's own docs/tests-only changes
status: FINAL_OWNER_ASSIGNED
```

### Infrastructure incidents (Incident Journal)

```text
current_owner: homelab, #58 (HL-AIOPS-01, still using the legacy "AIOPS" issue prefix in its own
               title — historical naming, not a claim that AIOps product owns it)
current_implementation: OPEN, planned/downstream of #65 per mission §10 ("Até #65 estar provada:
               #58 remains planned/downstream")
current_consumers: none yet
final_owner: HomeOps (homelab)
disposition: MOVE (semantic ownership was always HomeOps' once HomeOps existed as a concept;
               #58 predates the HL-HO series and used the "AIOPS" prefix only as historical
               naming convention, not a AIOps-product dependency)
migration_dependency: #65 (maintenance_projection_v1) must land first; #58 is not skipped ahead
countermodels: none — do not rename/dissolve #58's history to erase its origin
retirement_gate: #65 acceptance, then #58's own criteria
evidence: homelab #58 (OPEN), #65 (OPEN)
limitations: this repo has no implementation of an Incident Journal to retire — nothing to
               migrate physically, only the semantic owner label
status: FINAL_OWNER_ASSIGNED
```

### Operational analysis (bounded Ops Analysis, advisory recommendation)

```text
current_owner: none implemented anywhere yet
current_implementation: not built — depends on HomeOps foundation (#62-#65) and, when useful,
               Agent Router as an inference consumer
current_consumers: none
final_owner: HomeOps, as a consumer of Agent Router (never the reverse)
disposition: DEFER — no implementation authorized before #65 is proven (mission §10/§11)
migration_dependency: full HL-HO chain, then explicit human grant per operation
countermodels: `ConsumedProjection(X) != TruthOfProjection(X)`; Router records only a
               `consumed_capacity_projection_digest`, never the truth of the projection itself
               (mission §11); no receipt v3 is introduced by this matrix or authorized elsewhere
retirement_gate: n/a — not yet built
evidence: mission mandate §10/§11; homelab #61-#65 (OPEN)
limitations: purely a forward contract statement; no code exists to evaluate
status: FINAL_OWNER_ASSIGNED (contract-only)
```

### ProjectOps

```text
current_owner: none in this repository (RI_A1/ADR-001 already confirmed: "Not implemented in
               this repository at all (no app/ module, no docs beyond the roadmap reference)")
current_implementation: none
current_consumers: none
final_owner: none assigned
disposition: DEFER — no automatic successor; #46 §6 lists #91-#95 (ProjectOps) as
               not_planned/not_entregue
migration_dependency: requires a concrete claim + concrete consumer to reopen anywhere
countermodels: creating a new ProjectOps-shaped service in any repo without a named consumer is
               STOP_NEW_CROSS_CUTTING_SERVICE
retirement_gate: n/a — never built
evidence: #46 §6, RI_A1/ADR-001
limitations: none
status: LEGACY_RETIRED at the tracker level (#91-#95 not_planned); no successor exists to assign
```

### Workbench

```text
current_owner: none in this repository; described only as a future cockpit/projection surface
current_implementation: none confirmed built (docs/AGENT_ROUTER.md in agent-router-api already
               hedges: "Sua descrição como cockpit read-only não prova que todas as integrações
               planejadas já estejam implementadas")
current_consumers: none — agent-router-api's own docs state "Workbench is not a direct Router
               consumer" (issue #111 compatibility checkpoint, verified this round)
final_owner: none assigned
disposition: RETIRE_OR_REASSESS — no automatic successor (mission §14)
migration_dependency: none
countermodels: any receipt/projection field added "for Workbench" without a named, implemented
               consumer reopens a retired scope
retirement_gate: n/a — never built
evidence: agent-router-api #111 compatibility checkpoint; agent-router-api docs/AGENT_ROUTER.md
limitations: this document does not confirm whether any Workbench prototype exists outside the
               repositories in scope of this mission; none was found in the seven repos checked
status: LEGACY_RETIRED / no successor
```

### Generic evidence semantics (identity, binding, qualification, claims, obligations,
### countermodels, authority, stale/reuse/invalidation)

```text
current_owner: caem (mglpsw/caem), consumed by aiops-orchestrator via a pinned F0 interface
               (config/caem/caem-3.0-f0.pin.json, app/caem_consumer/f0.py — per RI_A0's own
               erratum, verified this round to still be the documented active pin mechanism)
current_implementation: CAEM 3.0 F0 pinned; F2 (evidence acquisition/local state plane, caem#97)
               and RK-1 (repository knowledge closure, caem#74) OPEN in caem; independent reviewer
               lanes / AgentRouter capability broker (caem#63) OPEN
current_consumers: aiops-orchestrator (AgentReview, via the F0 pin), and, prospectively, any
               repository invoking the same generic semantics
final_owner: CAEM
disposition: KEEP in CAEM — CAEM is upstream semantic authority, never a runtime
migration_dependency: caem#63 (independent reviewer lanes/broker), caem#74 (RK-1), caem#97 (F2)
countermodels: CAEM must never gain GPU/Ollama/review-engine/routing/scheduler/Incident
               Journal/CI-planner/review-database runtime (mission §12 explicit prohibition)
retirement_gate: n/a — CAEM is the final owner, nothing to retire here
evidence: caem#63/#74/#97 (all OPEN), aiops-orchestrator RI_A0 erratum (F0 pin location)
limitations: this document does not audit caem's own repository; see the separate caem-side
               reconciliation for that
status: FINAL_OWNER_ASSIGNED (already true structurally; #351 changes no CAEM consumption code)
```

### Inference execution

```text
current_owner: agent-router-api (the only execution plane of inference in this ecosystem)
current_implementation: OpenAI-compatible API, preset resolution, provider/model registry,
               admission, routing policy, fallback, `agent-router.inference-receipt.v2` (#99/#111,
               F2-A integrated per agent-router-api master@a6ea6ba)
current_consumers: `aiops-orchestrator`'s deprecated `AIOpsRouterAdapter` shim (inert); AgentReview
               is the intended canonical consumer per the reconciliation in
               agent-router-api PR #116 (this mission round)
final_owner: Agent Router
disposition: KEEP in Agent Router
migration_dependency: none — already correctly owned
countermodels: no "policy brain"/semantic reducer/review planner/infrastructure collector/incident
               memory/global intelligence may be added to the Router roadmap (mission §6.3); none
               was found proposed in agent-router-api's live roadmap docs/issues this round
retirement_gate: n/a
evidence: agent-router-api #65/#99/#111, PR #116 (this round), app/agent_router/main.py routes
limitations: receipt v3 is explicitly not authorized by this or any document in this round
status: FINAL_OWNER_ASSIGNED and MIGRATION_IMPLEMENTED for the naming reconciliation (PR #116);
               the underlying execution-plane ownership itself was already true before this round
```

## First-wave target adoption (mission §13, informational — no implementation here)

| Target | Evidence observed this round | Disposition |
|---|---|---|
| `mglpsw/AgentEscala` | `.github/workflows/agent-review-v2-analysis.yml`, `agent-review-v2-evidence.yml`, `agent-review-v2-publish.yml` exist (evidence/analysis/publisher lanes present); legacy `agent-review.yml`, `agent-review-publish.yml`, `aiops-runner-smoke.yml`, `issue-aiops.yml` also present | Thin AgentReview target integration in progress; legacy `aiops-*`-named workflows are RETIRE candidates once the v2 lanes are confirmed cut over — not asserted done here, needs AgentEscala-side census |
| `mglpsw/caem` | No AgentReview-specific workflow found; caem#63 (broker) is the open contract | Dogfooding advisory adoption path exists on paper (caem#63); not yet cut over |
| `mglpsw/sacr-as` | Only `.github/workflows/validate.yml` present; no AgentReview workflow found | Adoption not yet started; DLP/clinical constraints (mission §13) apply before any real integration |
| `mglpsw/interleitos` | Only `ci.yml`/`ct104-deployment.yml`; no AgentReview workflow found | Confirmed deferred consumer, not a first-wave release gate (per #46's 2026-09-25 addendum) |

## What this document does not claim

- It does not claim any of the above `MOVE`/`RETIRE` dispositions have been code-implemented in
  this PR — only `FINAL_OWNER_ASSIGNED` for the rows so marked.
- It does not close #351, #19, #46, or any homelab/caem/target issue.
- It does not authorize CT102 access, secrets, runners, provider credentials, deploy, or repository
  rename.
- It does not supersede #46 as scope authority — #46 remains the single source of priority/Go-No-Go
  for this repository.
