# RI-A1 — Architecture ADR and component ownership map (#118)

Refs #118 (parent epic #126, roadmap #46). Formalizes the AIOps ecosystem's
architecture before any database, worker, sync, or runtime migration is
attempted. Preset `docs`. **Zero functional/production change** — no code,
migration, deploy, CT102 change, repository extraction, file removal, or
AgentReview v2 alteration. Every claim below is backed by a real file path,
`git log` entry, or `grep` result checked in this repository at HEAD
`a33bba8` (post-#122/RI-A0), not inferred from naming or memory.

## ADR-001: AIOps = Review Intelligence Control Plane

**Status:** proposed (this document is the ADR; ratification/promotion to
"accepted" is a separate act requiring its own authority, not implied by
merging this doc).

**Context.** `mglpsw/aiops-orchestrator` currently hosts, side by side, in
one FastAPI process (`app/main.py`): (a) the canonical AIOps Diagnostic
Engine/Action Planner v1 (`app/agent_router/`), (b) a legacy chat-driven
task/approval orchestrator with a real SQLite database
(`app/models/database.py`, `app/services/orchestrator.py`,
`app/services/task_service.py`, `app/api/routes.py`'s `/v1/chat`,
`/v1/tasks`, legacy `/v1/approvals`, `/v1/providers/status`), (c) a set of
quarantined execution/provider adapters (`app/adapters/*`) explicitly
marked, in their own module docstrings, `"LEGACY / NOT USED BY AIOPS
RUNNER V1"`, and (d) the offline AgentReview v1+v2 review engine
(`app/agent_review/`), which nothing else in this repository imports.

**Decision.** AIOps becomes the **Review Intelligence Control Plane**: the
one place that persists and processes review/operational knowledge
longitudinally (findings, dispositions, evals, suggestions), consuming
AgentReview v2 (offline, independent) and the canonical `app/agent_router/`
runtime as producers, never re-implementing their logic. Concretely, per
#118's own six minimum decisions, all independently verified true of the
CURRENT repository state (not merely proposed):

| Minimum decision | Verified current state |
|---|---|
| AgentReview permanece offline-first e independente | Confirmed: zero imports of `app.agent_review.*` from anywhere outside `app/agent_review/`, `scripts/aiops-review-*.py`, and its own tests (`git grep` across `app/`) |
| AIOps centraliza persistência e processamento longitudinal | Not yet true structurally — today `app/agent_router/` persists only JSONL run/approval/audit logs (`var/runs/`, `var/approvals/`, `var/audit/`), no longitudinal review memory exists anywhere; this is the actual gap RI is being built to close (tracked by #119's contracts and later implementation issues, out of THIS document's scope) |
| Router possui ownership exclusivo de inferência | Confirmed at the repo-boundary level: `docs/engineering/PROJECT_OVERLAY.md`'s own rule ("Router é transporte de inferência... sem autoridade sobre host ou verdict") already governs the SEPARATE `mglpsw/agent-router-api` repository (registry role `llm_inference_gateway_and_adaptive_router`, per `docs/engineering/REPOSITORY_REGISTRY.md`) |
| ProjectOps é evidence producer distinto | Not implemented in this repository at all (no `app/` module, no docs beyond the roadmap reference) — correctly out of scope here, named only as a boundary |
| CAEM é interface normativa | Confirmed via #122/RI-A0's own reuse matrix (`docs/RI_A0_CAEM_REUSE_MATRIX.md`) |
| runtime legado entra em freeze/auditoria, não remoção imediata | Confirmed ALREADY DONE, not merely decided: `git log` shows a real commit `chore(aiops): quarantine legacy execution adapters` (2026-04-27) that added the `"LEGACY / NOT USED"` docstrings to `app/adapters/executor_ssh.py`/`docker.py`/etc., and `app/api/legacy_usage.py` adds a `299` deprecation warning header + usage-tracking counter to every legacy endpoint hit, rather than removing them |

**Consequence.** RI's own implementation work (future issues, out of this
document's scope) targets `app/agent_router/` and a new, not-yet-built
persistence layer as its integration points — never the legacy
`app/models/database.py`/`app/services/orchestrator.py` chat path, and
never `app/adapters/*`'s quarantined executors. No provider registry,
SSH/Docker adapter, or legacy orchestrator enters the new product
automatically, per #118's own explicit prohibition — none of them is
referenced by anything in `docs/RI_A0_CAEM_REUSE_MATRIX.md` or this
document as a Review-Intelligence dependency.

## A naming collision worth flagging explicitly

**"Agent Router" (the boundary term used in `docs/engineering/
PROJECT_OVERLAY.md` and this issue's own "Agent Router" boundary) refers
to the SEPARATE repository `mglpsw/agent-router-api`** (per
`REPOSITORY_REGISTRY.md`: role `llm_inference_gateway_and_adaptive_router`)
— it is **not** the same thing as this repository's own Python package
`app/agent_router/` (the AIOps Diagnostic Engine v1 / Action Planner v1,
role confirmed via its own `main.py` docstring: `"FastAPI router for AIOps
Diagnostic Engine v1 and Action Planner v1"`). The two share a name purely
by historical accident; they are architecturally unrelated (one is an LLM
inference gateway in a different repo, the other is this repo's own
runtime diagnostic/action subsystem). This document uses **"Agent
Router (repo)"** for the former and **`app/agent_router/`** (backticked,
always with the path) for the latter throughout, and recommends #119/#120
adopt the same disambiguation rather than relying on context.

## Boundaries: AgentReview × AIOps × ProjectOps × Agent Router (repo) × CAEM

```text
AgentReview (app/agent_review/, this repo)
  = offline, deterministic, CT104-scoped review engine.
  Produces manifests/payloads/readiness artifacts. Consumes nothing from
  AIOps/`app/agent_router/`. Nothing outside itself imports it.

AIOps (app/agent_router/ + this repo's FastAPI app, CT102-scoped runtime)
  = diagnostic/action-planning runtime TODAY; Review Intelligence Control
  Plane TOMORROW (this ADR's own decision, not yet implemented). Owns
  persistence going forward. Never re-implements AgentReview's review
  logic; never re-implements Agent Router (repo)'s inference logic.

ProjectOps (not implemented in this repository)
  = a distinct evidence producer per the epic; out of scope for RI-A1
  beyond naming the boundary.

Agent Router (repo) = mglpsw/agent-router-api (separate repository)
  = LLM inference transport only. No authority over host, verdict, or
  readiness. AIOps/AgentReview may call it for inference; it decides
  nothing on their behalf.

CAEM (docs/engineering/*, .caem/*, this repo)
  = normative interface: policy, schemas, reason codes, authority grants,
  evidence bundles. AIOps/AgentReview are CONSUMERS, never a second
  normative source (per #122/RI-A0's own decision).
```

## CT102 (runtime) vs. CT104 (toolrepo) surface map

`app/services/environment_context.py` already encodes this distinction in
code today, not merely in docs: `KNOWN_REPO_MODES = {"aiops_runtime",
"agent_review_tooling", "ci"}`, with an explicit
`agent_review_tooling_allowed` gate. Mapped onto real directories:

| Surface | Repo mode | Runs on | Owns |
|---|---|---|---|
| `app/agent_router/`, `app/api/`, `app/models/`, `app/services/` (legacy), `app/adapters/` (quarantined) | `aiops_runtime` | CT102 (production runtime) | HTTP API, DB, JSONL stores, approval/audit trail |
| `app/agent_review/`, `scripts/aiops-review-*.py`, `evals/agent_review_v2/` | `agent_review_tooling` | CT104 (toolrepo/dev) | Offline review pipeline, benchmark harness, no runtime dependency |
| `.github/workflows/*.yml` | `ci` | GitHub-hosted runners | CI validation, comment-triggered AgentReview invocation |

RI's own future implementation must declare which of these three surfaces
each new module belongs to — per `environment_context.py`'s own existing
enum, not a new one.

## Component ownership map

Per #118's required matrix (componente / consumidor real / contrato
público / último uso observado / dados persistidos / dependências /
destino / justificativa / rollback-compatibilidade). "Último uso
observado" is each component's most recent `git log` entry touching it, at
HEAD `a33bba8`.

### Active, canonical surfaces (destino: manter)

| Componente | Consumidor real | Contrato público | Último uso observado | Dados persistidos | Dependências | Destino | Justificativa | Rollback/compat |
|---|---|---|---|---|---|---|---|---|
| `app/agent_review/` | `scripts/aiops-review-*.py` CLIs, `tests/agent_review/*`, `tests/evals/*` | No HTTP; CLI + Python library API; JSON Schemas in `schemas/agent-review/{v1,v2}/` | today (2026-08-02, this session's own PRs #147-#150) | None at runtime; JSON artifacts to disk, schema-validated | Own contracts only; zero dependency on `app/agent_router/`/DB | **manter** | Most actively developed, offline-by-design, zero coupling to the runtime it must stay independent from | v1/v2 both frozen-contract-disciplined (`ContractV2Model`, golden fixtures); rollback is ordinary git revert |
| `app/agent_router/` | `app/main.py` (mounts `aiops_router`); `app/services/orchestrator.py`/`aiops_chat_router.py` (legacy chat bridges INTO it, one-way) | HTTP: `/v1/aiops/diagnose`, `/v1/aiops/actions/{catalog,plan,dry-run,run}`, `/v1/aiops/actions/approvals*`, `/v1/aiops/runs/*`, `/v1/aiops/audit/recent`; all behind `require_api_token` | 2026-04-27 (`feat(aiops): add run history endpoints`) | JSONL: `var/runs/aiops_runs.jsonl`, `var/approvals/aiops_approvals.jsonl`, `var/audit/aiops_audit.jsonl` (only the audit file currently exists on disk) | `app/core/config.py`, `app/agent_router/services/action_runner.py` (fixed allowlist, `shell=False`, sanitized env, timeout, redaction — confirmed by dedicated test `tests/test_legacy_adapter_quarantine.py`) | **manter** — this IS the AIOps runtime RI's control-plane decision builds on | Canonical per `docs/ARCHITECTURE.md`/`docs/HANDOFF_AGENT_ROUTER_API.md`; the one HTTP surface with a real, tested execution-safety boundary | Additive-only HTTP surface so far; no breaking change proposed here |
| `.github/workflows/{ci.yml,agent-review.yml}` | GitHub Actions | `issue_comment`/`push`/`pull_request` triggers | today (this session's own merges) | None (ephemeral runners) | `scripts/github_agent_review.py`, `app/agent_review/` | **manter** | Already documented in `.github/AGENTS.md` (#87) | N/A |
| `.caem/*`, `docs/engineering/*` | `AGENTS.md` generation (external); this repo's own CLAUDE.md imports | Generated-view + vendored-JSON contract, per #122/RI-A0 | 2026-07-31 (per file mtimes) | N/A (static reference data) | External CAEM generator (not in this repo) | **manter** | Normative interface, per #122's own decision | Ordinary git revert; regeneration is external |

### Legacy, quarantined-but-not-removed surfaces (destino: manter em freeze/auditoria)

| Componente | Consumidor real | Contrato público | Último uso observado | Dados persistidos | Dependências | Destino | Justificativa | Rollback/compat |
|---|---|---|---|---|---|---|---|---|
| `app/models/database.py` (`TaskRecord`, `AuditRecord`, `ProviderCallRecord`, `ExecutionRecord` SQLAlchemy models, SQLite async engine) | `app/services/task_service.py`, `app/services/orchestrator.py`, `app/api/routes.py` | Internal ORM only, no direct HTTP contract of its own | 2026-04-18 (`chore(init): bootstrap aiops-orchestrator repo from homelab/aiops`) — no touch since | Real SQLite DB, 4 tables: `tasks`, `audit_log`, `provider_calls`, `executions` | `app/core/config.py` (`AIOPS_DATABASE_URL`) | **freeze/auditoria** (per this ADR's own ratified minimum decision) — not removed, not extended | Live DB with real historical task records; removing it is a data-loss risk this document has no authority to accept | Any future removal requires a real migration plan and a data-retention decision, out of scope here |
| `app/api/routes.py`'s legacy endpoints (`/v1/chat`, `/v1/chat/ingest`, `/v1/tasks`, `/v1/tasks/{id}`, legacy `/v1/approvals`, `/v1/providers/status`) | External callers not enumerated in this repo (unknown externally, by design — see limitation below) | HTTP, behind `require_api_token`, each hit tagged with a `299` deprecation warning (`app/api/legacy_usage.py`) and a usage counter | `routes.py` itself: 2026-04-26 (`feat: protect sensitive api routes`); `legacy_usage.py`'s own deprecation-marking commit: 2026-04-27 (`chore(aiops): mark legacy surfaces deprecated`) | Same SQLite DB as above | `app/services/orchestrator.py`, `app/models/database.py` | **freeze/auditoria** | Already actively deprecation-warned and usage-tracked in code — this document did not invent that decision, it observed it already implemented | Usage counters (`app/api/legacy_usage.py`) are the mechanism to decide, later, whether traffic has dropped enough to retire — not decided here |
| `app/adapters/{executor_ssh,docker,executor_local,claude,codex,ollama,openai_compatible}.py` | `app/services/provider_registry.py` only (confirmed: `grep -rl "from app.adapters"` shows no hit outside `provider_registry.py` and the adapters' own mutual imports) | None reachable from `app/agent_router/`'s HTTP surface — each file's own docstring: `"LEGACY / NOT USED BY AIOPS RUNNER V1... Do not wire this into /v1/aiops/actions/run"` | 2026-04-27 (`chore(aiops): quarantine legacy execution adapters`) | None observed beyond what `provider_registry.py`'s own config governs | `app/services/provider_registry.py`, `app/policies/*` | **freeze/auditoria** (already self-quarantined) | Real SSH/Docker/direct-LLM-provider execution capability; #118's own explicit prohibition ("nenhum provider registry, SSH/Docker adapter... entra automaticamente no novo produto") is already independently enforced by `tests/test_legacy_adapter_quarantine.py`, which asserts `app/agent_router/services/action_runner.py` never imports these and never calls `create_subprocess_shell` | Quarantine is a real, tested invariant already; RI must not add a new import path into these modules without triggering the SAME test's failure |
| `app/services/{orchestrator,task_service,aiops_chat_router,provider_registry,action_catalog,action_planner}.py` | Each other, plus `app/api/routes.py` | Internal, chat-intent-driven | `orchestrator.py`: 2026-04-27; `task_service.py`: 2026-04-18 (bootstrap, untouched since); `provider_registry.py`/`action_catalog.py`/`action_planner.py`: 2026-04-27 | SQLite (via `task_service.py`) | `app/models/database.py`, `app/adapters/*` | **freeze/auditoria** | Same legacy chat orchestrator described above | Same as `app/models/database.py` row |

### Out-of-scope-for-RI, unrelated surfaces (destino: manter, sem relação com RI)

| Componente | Papel | Destino |
|---|---|---|
| `app/core/config.py` | Settings/env for the whole app (both legacy and canonical paths read it) | **manter**, shared infrastructure |
| `app/policies/{command_guardrails,engine}.py` | Command-safety policy engine consumed by the legacy adapters/services above | **manter em freeze** alongside its consumers |
| `app/utils/{logging,secrets}.py` | Cross-cutting helpers | **manter**, shared infrastructure |
| `config/{actions.yaml,policies.yml,providers.yml,routes.yml,environment.example.yaml}` | Config for the runtime surfaces above (actions catalog validated by `scripts/validate_actions_catalog.sh`, referenced in `.github/workflows/ci.yml`) | **manter** |
| `deploy/{Dockerfile,docker-compose*.yml,systemd/}` | Deployment of the CT102 runtime | **manter**, out of scope for any change in this document (explicitly forbidden: "deploy") |
| `scripts/aiops-runtime-*.py`, `scripts/aiops-env-info.py`, `scripts/guard-aiops-environment.py`, `scripts/migrate_savings_to_sqlite.py` | CT102 operational tooling (backup, inventory, postcheck, environment guard) | **manter**, unrelated to RI's own review-intelligence surface |
| `evals/agent_review_v2/`, `scripts/run-agent-review-v2-evals.py`, `scripts/compare-review-observations.py` | AgentReview v2 benchmark harness (#88, merged this session) | **manter**; RI's own future eval/memory work should build alongside this, not duplicate it (per #122/RI-A0's own reuse principle) |

## Conflicts of source of truth, explicitly named (per #118's own acceptance criterion)

1. **`app/agent_router/` vs. "Agent Router" (the PROJECT_OVERLAY.md term)** —
   resolved above under "A naming collision worth flagging explicitly".
   This is the single most likely source of a future misdirected PR or
   misread architecture doc if left unflagged.
2. **`.caem/policy.json` vs. the generated views' `Policy-SHA256`** —
   already flagged in #122/RI-A0 (`docs/RI_A0_CAEM_REUSE_MATRIX.md`'s
   honesty-boundary section); repeated here because it is exactly the
   kind of "fonte de verdade" conflict #118 asks this document to
   surface, not a new finding of this document itself.
3. **Legacy `/v1/approvals`/`/v1/providers/status` vs. canonical `/v1/aiops/
   actions/approvals*`** — two different approval concepts exist under
   similar names (`app/api/routes.py`'s legacy, DB-backed approval vs.
   `app/agent_router/services/approval_store.py`'s JSONL-backed,
   canonical one). RI must integrate with the CANONICAL one only
   (`app/agent_router/services/approval_store.py`) — the legacy one is
   frozen, not a second source of truth to reconcile.

## What this document does NOT decide (explicitly out of scope)

Per #118's own "Fora de escopo": no code changes, no DB/migrations, no
sync, no deploy, no CT102 change, no repository extraction, no file
removal, no AgentReview v2 alteration. Verified: `git diff --stat` against
this branch shows exactly one new file (`docs/
RI_A1_ADR_OWNERSHIP_MAP.md`); nothing under `app/`, `scripts/`, `config/`,
`deploy/`, `.github/`, `schemas/`, `.caem/` was touched.

## Rejected alternatives

- **Removing the legacy chat/task/adapter surfaces now, since they're
  already marked "LEGACY"** — rejected. #118 explicitly requires freeze/
  audit, not immediate removal, and this document has no authority to
  accept the data-loss/breaking-change risk that removal would carry; the
  existing `app/api/legacy_usage.py` usage-counter mechanism is the
  correct tool to decide this later, with real traffic data.
- **Treating `app/agent_router/` and the external `agent-router-api` repo
  as the same "Agent Router" concept, to avoid a confusing rename** —
  rejected; the naming collision is real and already causes exactly the
  kind of confusion #118 asks to prevent. Disambiguating by always
  qualifying which one is meant is cheaper and safer than a rename (a
  rename would touch running code, out of this document's scope) or
  silence (which would let the ambiguity propagate into #119/#120).
- **Deferring the ownership map until a live database inventory tool
  exists** — rejected; #118's own deliverable is exactly this map, and
  `git log`/`grep` against the real repository state were sufficient to
  produce an accurate one without needing new tooling.

## Limitations

- External callers of the legacy `/v1/chat`/`/v1/tasks`/`/v1/approvals`/
  `/v1/providers/status` endpoints are not enumerated by this document —
  this repository's own code has no record of who calls them; only
  `app/api/legacy_usage.py`'s runtime usage counters could answer that,
  and reading them was not attempted here (would require querying a live
  environment, out of an offline documentation slice's scope).
- `mglpsw/agent-router-api` (the separate LLM inference gateway repo) was
  described only from `docs/engineering/REPOSITORY_REGISTRY.md`'s own
  entry — this session has no checkout of that repository to verify
  further.
- ProjectOps has no implementation in this repository to inventory; only
  its boundary is named, per the epic's own architecture.

## Acceptance criteria status

- [x] ADR publicado — ADR-001 above;
- [x] ownership map cobre todas as superfícies principais — component
      ownership map above (active/canonical, legacy/quarantined,
      out-of-scope-for-RI);
- [x] nenhum componente é movido por semelhança nominal — the naming-
      collision section exists specifically to prevent this;
- [x] conflitos de fonte de verdade são explicitados — dedicated section
      above, 3 conflicts named;
- [x] roadmap #46 e epic #126 referenciados — header;
- [x] nenhuma alteração funcional ou de produção — verified via
      `git diff --stat`, docs-only.
