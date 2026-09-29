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

Live bases used, reverified `2026-09-28` via `git rev-parse`/`gh api repos/<repo>/branches/<default>`:

| Repo | Branch | HEAD verified this round |
|---|---|---|
| `mglpsw/agent-router-api` | `master` | `a6ea6ba5fa0335cb77852e03d77cd9cd4ed15d6e` |
| `mglpsw/aiops-orchestrator` | `master` | `9abcde6420a59b814b5faaff10ca5904c5d23370` |
| `mglpsw/homelab` | `main` | `10352e9b040db6a20edb8ebf1ffd2812deb3e138` |
| `mglpsw/caem` | `main` | `854e321a99170eebd7795cb50d2e5b5e0e94db2f` |
| `mglpsw/AgentEscala` | `develop` | `d7627e4ead16e13b62a99ca186cb35b413d36aba` (advanced via unrelated dependency/frontend work; target-side reconciliation PR mglpsw/AgentEscala#859 remains Draft and is not qualification evidence by itself) |
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
               - legacy executors: app/adapters/executor_ssh.py, executor_local.py, docker.py carry
                 the historical "LEGACY / NOT USED" marker, but the marker is NOT reachability
                 evidence. ProviderRegistry.initialize() registers all three executor providers.
               - LIVE LLM adapters: app/adapters/claude.py, codex.py, ollama.py,
                 openai_compatible.py — instantiated by ProviderRegistry.initialize() and called
                 directly by the orchestrator; see "Legacy in-process inference" below
reachable_executor_path: POST /v1/approvals/{task_id} with decision=approved
               → Orchestrator.execute_approved_task(task_id)
               → each planned step calls registry.get_executor(tool)
               → get_executor() returns the requested enabled executor or falls back to the
                 unconditionally registered local executor. Source establishes reachability in the
                 application graph; live deployment/caller reachability remains UNKNOWN_PENDING_19.
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
               - "LEGACY / NOT USED" marker != unreachable executor
               - "no live deployment observed" != "not deployed": retirement must not proceed on
                 this matrix's evidence alone
               - "legacy executor path inventoried" != "AIOps Runtime execution inventoried": the
                 Diagnostic Engine's /v1/aiops/actions/run → execute_action() is a second,
                 independent executable surface with its own persisted data
retirement_gate: #19 must inventory the legacy approvals/task execution path, the Diagnostic
               Engine execution path and its three persisted stores (see
               diagnostic_engine_execution_path), executor configuration, live callers and
               deployment state, then prove cutover/desuse before source/runtime
               removal; nothing here authorizes CT102 changes. The gate covers BOTH executable paths
               above and below; inventorying only the legacy /v1/approvals path does not satisfy it
diagnostic_engine_execution_path (independent of the legacy path above — its own approval
               concept, executor and stores; app/agent_router/ is retired with this row):
               - routes (all behind require_api_token, app/agent_router/main.py:65):
                 POST /v1/aiops/actions/approvals, GET /v1/aiops/actions/approvals[/{approval_id}],
                 POST /v1/aiops/actions/approvals/{approval_id}/approve and /reject,
                 POST /v1/aiops/actions/run (main.py:508-729)
                 → resolve_approval() → execute_action(action_id) (main.py:659;
                 app/agent_router/services/action_runner.py:467) → the fixed _RUNNERS allowlist
                 (config/actions.yaml). Neighbouring read/plan routes of the same engine:
                 /v1/aiops/diagnose, /actions/{catalog,plan,dry-run}, /v1/aiops/runs/*,
                 /v1/aiops/audit/recent
               - persisted stores (app/core/config.py:79-95):
                 var/audit/aiops_audit.jsonl (audit_log_path),
                 var/approvals/aiops_approvals.jsonl (approval_store_path),
                 var/runs/aiops_runs.jsonl (run_store_path)
               - source-known callers: the HTTP surface itself and the legacy chat bridge
                 app/services/aiops_chat_router.py (imports diagnose_aiops, list_approvals,
                 get_run, list_recent_runs — read/diagnose side); app/services/orchestrator.py
                 imports get_catalog_readiness. External callers of /actions/run and the
                 approval routes: UNKNOWN_PENDING_19 (RI_A1 records them as unknown by design)
               - live state of the three stores (existence, size, rotation/compaction, retention
                 needs) is UNKNOWN_PENDING_19: docs/RI_A1_ADR_OWNERSHIP_MAP.md's "only the audit
                 file exists on disk" is a historical observation, not a current one
               - #19 gate (inventory + backup + restore + rollback) must list these routes, their
                 live callers and all three stores, and decide retain/archive/discard for the
                 persisted records before any source retirement; #351 slice C must not remove
                 app/agent_router/ or its stores until that decision exists
evidence: #19 (OPEN), #351 (OPEN), app/api/routes.py:87-110,
               app/services/orchestrator.py:execute_approved_task,
               app/services/provider_registry.py:get_executor,
               app/agent_router/main.py:508-729, app/agent_router/services/action_runner.py:467,
               app/agent_router/services/{approval_store,run_store,audit_log}.py,
               app/core/config.py:79-95, app/services/aiops_chat_router.py:16-20,
               docs/PROJECT_STATUS.md:82-83, docs/RI_A1_ADR_OWNERSHIP_MAP.md
limitations: source-observed reachability only; actual deployment/external callers remain UNKNOWN
               until #19 live inventory. Whether the Diagnostic Engine's allowlisted runners are
               side-effect-free is documented (README.md, RI_A1) but not re-verified here
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
current_owner: aiops-orchestrator, app/agent_review/ (v1 line) — per #46 the
               LEGACY_ADVISORY_BASELINE: debt/quality fixes only, no new trust architecture;
               product convergence after the first Assured release is #357
current_implementation:
               - published/consumed baseline: v0.22.0@2ce1f45768b8779cb48ef8a302d4ed796349f0e5
               - merged-unreleased v1 source on master: includes later planner/soundness fixes from
                 #225/PR #227 (merge da5a03b4…) and H1-B/PR #231 (merge ffa30406…).
                 PublishedBaseline != LiveMasterV1Source.
current_consumers:
               - internal toolrepo consumers: v1 CLIs/scripts importing app.agent_review, including
                 scripts/aiops-review-intake.py, aiops-review-plan-chunks.py,
                 aiops-review-parse-chunks.py, aiops-review-synthesize.py,
                 aiops-review-quality-gate.py and telemetry/false-positive tooling
               - standalone GitHub review lane — a SEPARATE surface, not part of app.agent_review:
                 .github/workflows/agent-review.yml (step at line 59) → scripts/github_agent_review.py,
                 which has 0 imports of app.agent_review and implements its own
                 deterministic/Router-assisted review path. A package-based census of
                 app.agent_review importers does not capture it. Lifecycle: attached to AgentReview
                 v1 / LEGACY_ADVISORY_BASELINE; its source/path egress debt is tracked in #315
                 (which names this workflow and script); closure/freeze owner is #221. It needs its
                 own explicit disposition/cutover before any #351 source separation
               - external: AgentEscala consumes the v0.22.0 baseline per #46/AgentEscala lineage;
                 current run-history assertions recorded in Draft mglpsw/AgentEscala#859 remain
                 target-side evidence pending independent review and are not used here as sole
                 qualification
final_owner: AgentReview
disposition: KEEP in AgentReview → repair/freeze pending #221; decide release/repin of the
               merged-unreleased delta explicitly rather than collapsing it into v0.22.0
migration_dependency: #221 (material-debt fix, immutable release when needed, repin/canary, freeze)
countermodels: - PublishedBaseline == LiveMasterV1Source is false
               - ImporterCensus(app.agent_review) == v1 product surface is false: the standalone
                 GitHub review lane has no such import
               - v1 does not become default/required by administrative decision (#46 §2)
retirement_gate: n/a — kept, not retired; freeze is a maintenance state, not removal
evidence: #221 (OPEN), #315 (OPEN), #46 §1/§3, #225, PR #227, PR #231;
               internal import graph; .github/workflows/agent-review.yml:59;
               target-side evidence is separately qualified
limitations: freeze timing depends on #232/#307/#315/#343 and on explicit disposition of the
               merged-unreleased source delta
status: FINAL_OWNER_ASSIGNED; external consumer cutover/freeze status is not inferred solely from
               this repository
```

### AgentReview v2

```text
current_owner: aiops-orchestrator, app/agent_review/ (v2 line) — per #46 the architecture of the
               ASSURED profile; first operational path #80 → #350 → #199
current_implementation: C3 integrated (#304/PR #349), C4-Q integrated (#333/PR #352).
               #301-S0 is ratified in Draft PR #355 but NOT integrated. Per current #46 §4, the
               next production step is integrate/read-back #355 under its own grant, then execute
               #301 S1-A followed by S1-B/C/D, S_D and E; only then #298 → #314.
current_consumers:
               - internal toolrepo consumers: v2 CLIs and qualification tooling import
                 app.agent_review directly, including scripts/aiops-review-quality-gate-v2.py,
                 agent-review-target-pack-v2.py, verify-agent-review-v2-conformance.py,
                 export-agent-review-v2-schemas.py and evals/agent_review_v2/harness.py
               - external target: AgentEscala has v2 target/workflow material in source, but
                 mglpsw/AgentEscala#859 is still Draft/unreviewed as a target-side reconciliation;
                 its run-history/variable claims are evidence to qualify, not a completed cutover
required_asset_trees (outside app/agent_review/, inside the product boundary):
               - templates/agentreview-v2-target-pack/
               - schemas/agent-review/v2/
               app/agent_review/target_pack_build_v2.py:107-108 binds both paths and reads them from
               the pinned Git tree (lines 155-180); a missing template tree raises
               TargetPackBuildError(BUILD_TEMPLATE_ROOT_MISSING_REASON_V2) at line 159, so the Target
               Pack cannot be built without them. Disposition: KEEP as part of AgentReview Assured
               while consumers exist; related to #203. Any future extraction/rehome must prove
               preservation of templates, schemas, schema digests, byte identity where contracted,
               and Target Pack/conformance behavior. No physical migration has been executed
required_install_boundary (offline toolrepo installation contract, outside app/agent_review/,
               inside the product boundary):
               - requirements-agent-review.lock (hash-pinned; pydantic closure + PyYAML)
               - scripts/install-agent-review-toolrepo.sh (hard-fails when the lock is absent:
                 lines 54-57; installs with --require-hashes --no-deps)
               - docs/AGENT_REVIEW_V2_INSTALLATION.md (the consumption contract that pinned
                 target workflows follow; lines 12-20 require installing the lock)
               - tests/agent_review/test_minimal_toolrepo_lock.py (lock/installer contract tests;
                 the real-install cases are marked requires_network)
               Disposition: KEEP as part of AgentReview Assured while pinned targets consume the
               toolrepo. Extraction with source, templates and schemas intact but without these
               four artifacts leaves a product that pinned target workflows cannot install. The
               installer also binds --toolrepo-sha to `git rev-parse HEAD` of its own checkout
               (lines 47-49), so a rehome into another repository invalidates existing target pins:
               any extraction must prove the lock/installer/contract/tests are preserved, the
               clean-install check passes at the new SHA, and a re-pin path exists for targets.
               No physical migration has been executed
final_owner: AgentReview
disposition: KEEP in AgentReview
migration_dependency: integrate/read-back PR #355
               → #301 S1-A/B/C/D + S_D + E
               → #298 → #314 → #350 → #203 → #204 → #205 (first Assured release)
               → #357 (post-release Advisory/Assured convergence on the common engine)
countermodels: - #355 ratified != #355 integrated
               - target wiring != Router-backed semantic review
               - "extract app/agent_review/" != "extract AgentReview v2": the required asset trees
                 and the offline install boundary (lock, installer, installation contract, tests)
                 live outside the package
               - "source + templates + schemas preserved" != "installable by pinned targets"
               - v2 does not become default/required check by this reconciliation (#46 §2)
retirement_gate: n/a — kept
evidence: #46 §1/§4/§5, PR #349, PR #352, Draft PR #355; internal import graph;
               docs/AGENT_REVIEW_V2_INSTALLATION.md:12-20, scripts/install-agent-review-toolrepo.sh:47-57,
               requirements-agent-review.lock, tests/agent_review/test_minimal_toolrepo_lock.py;
               mglpsw/AgentEscala#859 only as unqualified target-side evidence
limitations: no end-to-end Router-backed v2 review is established by this matrix
status: FINAL_OWNER_ASSIGNED; target migration/cutover remains unqualified until its own exact-head
               review and the upstream Assured gates
```

### AgentReview shared source dependencies

```text
environment_context:
  current_path: app/services/environment_context.py
  disposition: KEEP_SHARED
  consumers (non-test importers, census at master@9abcde6 / PR head 1a9819b):
    - AgentReview family: app/agent_review/cli.py; scripts/aiops-review-build-payloads.py,
      aiops-review-false-positives.py, aiops-review-parse-chunks.py,
      aiops-review-plan-chunks.py, aiops-review-quality-gate.py, aiops-review-synthesize.py,
      aiops-review-telemetry.py
    - AIOps Runtime / environment-boundary family: scripts/guard-aiops-environment.py (lines
      18-21; its --require-mode enforces both aiops_runtime and agent_review_tooling, lines 26 and
      62-66, and docs/ENVIRONMENT_BOUNDARIES.md:86 invokes it for AgentReview tooling) and
      scripts/aiops-env-info.py (lines 16-20, runtime context reporting)
    - indirect test coverage only: tests/test_aiops_environment_contract.py runs both scripts; no
      test imports the module directly
  invariant: AgentReviewNeeds(X) != AgentReviewOwnsExclusively(X)
  retirement_rule: do not move or remove while both consumer families exist; rehome only after the
                   pertinent AgentReview standalone/cutover (#351 slice B) AND the pertinent AIOps
                   Runtime cutover/retirement (#19)

strict_json:
  current_path: app/common/strict_json.py
  consumers: multiple AgentReview v2 runtime modules and CLIs; also app/caem_consumer/f0.py
  disposition: KEEP_SHARED while both consumer families exist; rehome only with an explicit
               compatibility/cutover proof
  retirement_rule: app/common is not AIOps-only merely because it sits outside app/agent_review

final_owner: AgentReview toolrepo/shared support surface; environment_context stays shared with the
             AIOps Runtime until #19, and the CAEM F0 carrier's use of strict_json is preserved
             until that carrier receives its own lifecycle below
countermodel: "app/agent_review is the whole product boundary" is false while required imports live
              outside that subtree
evidence: live import graph at master@9abcde6
status: FINAL_OWNER_ASSIGNED; physical rehome not implemented
```

### Review Intelligence generic (the former "AIOps Review Intelligence" control plane)

```text
current_owner: none currently building the service — the implementation epic is closed/not_planned
current_implementation: two distinct things, not one:
               (a) planning documents RI-A0/A1/A2 (#122/#118/#120, CLOSED/COMPLETED)
               (b) executable RI-B0a artifacts still present:
                   app/ri_b0a/reuse_manifest.py,
                   config/ri/ri-b0a-2-reuse-manifest.json,
                   scripts/generate-ri-b0a-2-reuse-view.py,
                   tests/ri_b0a/
runtime_service: never deployed — no RI database, HTTP API, sync or workers were built
current_consumers: executable RI-B0a is NOT orphaned:
               - generator/tests
               - .github/workflows/ci.yml reuse-view check
               - scripts/ci_validate.sh repository-validation check
               The loader also scans live AgentReview schema material.
final_owner: none for an RI service — RETIRE unless a concrete claim + consumer exists
disposition:
               - RI service/product plan: CLOSED/NOT_PLANNED; no runtime surface was delivered
               - RI-B0a artifacts: KEEP_PENDING_351_CENSUS; then explicit RETIRE or rehome only
                 after the CI/repository-validation consumers are cut over or removed
migration_dependency: #351 slice A inventory + explicit disposition of CI/ci_validate consumers
countermodels: - closed/not_planned tracker != executable artifacts retired
               - no deployed RI service != no RI implementation artifacts
               - removing RI-B0a while CI still invokes it breaks an active conformance gate
retirement_gate: RI-B0a can be marked LEGACY_RETIRED only after source is removed/rehomed AND
               .github/workflows/ci.yml + scripts/ci_validate.sh are explicitly cut over
evidence: issue states #118/#119/#120/#121/#122/#126; #46 §6;
               app/ri_b0a/reuse_manifest.py; .github/workflows/ci.yml;
               scripts/ci_validate.sh; generator/tests
limitations: planning docs remain historical/formative evidence; none of this reopens the retired
               RI service/product
status: TRACKER_CLOSED_NOT_PLANNED; LEGACY_RETIRED=false for RI-B0a while executable artifacts and
               active CI consumers remain
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
status: TRACKER_CLOSED_NOT_PLANNED; no runtime/product surface was delivered, so `LEGACY_RETIRED` is not used
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
status: TRACKER_CLOSED_NOT_PLANNED / no successor; no implemented Workbench surface was established to retire
```

### Local CAEM F0 carrier (repository-governance dependency)

```text
current_owner: this toolrepo's repository-governance/shared-support layer; upstream semantic
               authority remains mglpsw/caem
current_implementation: config/caem/caem-3.0-f0.pin.json
               + app/caem_consumer/f0.py
               + scripts/verify-caem-f0-pin.py
               + generated identity headers/views
current_consumers: active repository-level gates, independently of RI-B0a:
               - .github/workflows/ci.yml invokes the pin verifier
               - scripts/ci_validate.sh section 5 invokes the pin verifier
               - AGENTS.md/CLAUDE.md/.caem/README.md and generated engineering views declare the
                 pin as the single active CAEM identity source
final_owner: AgentReview toolrepo repository-governance layer while this repository remains the
               carrier; any locator/identity change is owned by #358 planning/census, not by RI
disposition: KEEP independently of RI-B0a. Rehome/migrate only with #351-B/#358 evidence and
               explicit cutover of verifier/CI/generated identity consumers
migration_dependency: #351-B standalone product boundary + #358 identity census/planning;
               repository rename has no grant
countermodels: - F0CarrierLifecycle != RIB0aLifecycle
               - pin/loader/verifier present in active CI != historical-only artifact
retirement_gate: never retire by RI disposition; require replacement carrier, CI cutover,
               generated-header reconciliation and identity read-back
evidence: config/caem/caem-3.0-f0.pin.json; app/caem_consumer/f0.py;
               scripts/verify-caem-f0-pin.py; .github/workflows/ci.yml;
               scripts/ci_validate.sh; AGENTS.md/.caem/README.md
status: KEEP; independent lifecycle established
```

### Generic evidence semantics (identity, binding, qualification, claims, obligations,
### countermodels, authority, stale/reuse/invalidation)

```text
current_owner: mglpsw/caem (upstream semantic authority)
current_implementation: CAEM 3.0 F0 semantics are consumed locally through the separate carrier
               described above; F2 (mglpsw/caem#97), RK-1 (mglpsw/caem#74) and independent
               reviewer lanes/AgentRouter broker (mglpsw/caem#63) remain OPEN
AgentReview_relation: vocabulary/design influence is proven; direct AgentReview runtime import of
               app.caem_consumer is not. CAEMDesignInfluence != CAEMRuntimeConsumption.
final_owner: CAEM
disposition: KEEP in CAEM — CAEM is upstream semantic authority, never the runtime
migration_dependency: mglpsw/caem#63, mglpsw/caem#74, mglpsw/caem#97
countermodels: CAEM must never absorb GPU/Ollama/review-engine/routing/scheduler/Incident
               Journal/CI-planner/review-database runtime
retirement_gate: n/a for CAEM semantics. The local F0 carrier has its own KEEP lifecycle above and
               MUST NOT inherit the RI-B0a disposition.
evidence: mglpsw/caem#63, mglpsw/caem#74, mglpsw/caem#97; local F0 carrier source/gates above
limitations: this matrix does not qualify mglpsw/caem itself or prove a direct AgentReview→F0
               runtime callsite
status: FINAL_OWNER_ASSIGNED for generic semantics
```

### Inference execution

```text
current_owner: mglpsw/agent-router-api — canonical/final inference execution plane
current_state: NOT yet the only current inference path — legacy in-process direct-provider
               execution still exists in this repository and must be retired/cut over by #19
current_implementation: OpenAI-compatible API, preset resolution, provider/model registry,
               admission/routing/fallback and agent-router.inference-receipt.v2
receipt_truth_boundary: receipt v2 records Router-observed execution facts at the adapter-start /
               selected-attempt boundary (provider/model argument, attempts/transitions where
               observed). It does NOT prove ProviderSawExactly(input), provider-side model
               revision unless separately observed, semantic correctness, repo/HEAD truth or
               AgentReview readiness.
current_consumers: AgentReview is the canonical intended consumer contract
               (mglpsw/agent-router-api#116). AgentEscala source contains Router integration
               surfaces, but mglpsw/AgentEscala#859 remains Draft and its run-history/cutover
               assertions are not treated here as independent qualification.
ct102_contract_state: DOCUMENTED_NOT_E2E_EXERCISED_THIS_ROUND — mglpsw/agent-router-api#116 documents authenticated HTTP
               access to the Router runtime on CT102, but the reconciliation environment lacked
               both Router credential and homelab network path; no real HTTP smoke was executed.
final_owner: Agent Router
disposition: KEEP in Agent Router
migration_dependency: retirement/cutover of the legacy in-process inference path (#19)
countermodels: - "canonical/final" != "currently sole"
               - AdapterStarted(provider, model) != ProviderSawExactly(input)
               - DocumentedCT102Contract != RuntimeSmokeQualified
retirement_gate: n/a
evidence: mglpsw/agent-router-api#65, mglpsw/agent-router-api#99,
               mglpsw/agent-router-api#111, mglpsw/agent-router-api#116 and Router source;
               this repository's legacy direct-provider path above
limitations: receipt v3 is not authorized; CT102 smoke remains blocked in the documented mglpsw/agent-router-api#116
               environment; target-side AgentEscala qualification remains separate
status: FINAL_OWNER_ASSIGNED; sole-plane property awaits #19 cutover/retirement
```

## First-wave target adoption (informational — no implementation here)

| Target | Evidence | Disposition |
|---|---|---|
| `mglpsw/AgentEscala` | Bounded source evidence shows Router integration surfaces; Draft `mglpsw/AgentEscala#859` records a broader workflow/run-history census but has not completed independent review. Treat those target-side claims as evidence pending qualification, not as cutover proof. | First-wave target by #46. v1 baseline consumption is established by upstream lineage; v2 remains unqualified for cutover here. No retirement decision is derived solely from Draft mglpsw/AgentEscala#859. |
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
