# RI-A0 — CAEM reuse matrix for AIOps Review Intelligence (#122)

Refs #122 (parent epic #126, roadmap #46). Precedes #118 (ADR + ownership
map), #119 (communication contracts), #120 (threat model/authority
matrix), #121 (conformance fixtures).

**Zero functional/runtime change.** This document is planning/architecture
only, exactly as #122 requires: no database, no HTTP API, no sync, no
workers, no deploy, no CT102 change, no AgentReview v2 implementation, no
normative change to CAEM itself.

## Scope and honesty boundary

This matrix inventories CAEM primitives **observable in this repository**
(`mglpsw/aiops-orchestrator`) only: `docs/engineering/{CAEM_CORE,
PROJECT_OVERLAY,CURRENT_CHECKPOINT,REPOSITORY_REGISTRY}.md`, `.caem/
schemas/*.json`, the root `AGENTS.md`, and `CLAUDE.md`'s own imports. CAEM's
canonical specification/generator plausibly lives in a separate repository
this session has no checkout of — where this document cannot verify a
claim from that source, it says so explicitly rather than inventing one.

**A material, real gap found during this inventory, not previously
documented anywhere in this repo:** the root `AGENTS.md`'s own header
comment declares:

```text
Source: policy/caem-policy.json
Regenerate/validate: python tooling/generate.py && python tooling/validate.py
```

Neither `policy/caem-policy.json` nor `tooling/generate.py`/`validate.py`
exist anywhere in this repository (`find . -iname policy -o -iname
tooling` and direct path checks both come back empty). Only the
**output** of that generation (`AGENTS.md` itself, and
`docs/engineering/CAEM_CORE.md`/`PROJECT_OVERLAY.md`) and the **schemas**
(`.caem/schemas/*.json`) are present here. The generator/validator/policy
source is external tooling, not vendored into this repo. Separately, `grep
-rli caem app/ scripts/` returns zero hits: **no application or script
code in this repository loads, validates against, or otherwise consumes
`.caem/schemas/*.json` today.** They are present as static reference
artifacts only. Both facts are load-bearing for the dependency strategy
below — there is no existing in-repo consumption pattern to imitate beyond
the generated-view-checked-into-git pattern `AGENTS.md` itself
demonstrates.

## CAEM reuse matrix

| Primitive | Canonical source (observed in this repo) | Existing artifact/schema/tooling | AIOps Review Intelligence consumer | Adaptation needed | Duplication prohibited | Compat/versioning | Conformance test |
|---|---|---|---|---|---|---|---|
| Task contract | `docs/engineering/CAEM_CORE.md` §"Antes de agir"/"Durante"; `.caem/schemas/task-contract.schema.json` | Schema fields: `task_id`, `title`, `objective`, `project_profile`, ... | Every RI feature slice (memory write, eval run, suggestion) states objective/scope/gates as a task contract, same shape | None structural; RI populates `project_profile` with its own repository identity | Do not invent a second "task definition" shape for RI work items | Schema version tracked via `.caem/schemas/task-contract.schema.json`'s own `$id`/version field (not observed to have a version field distinct from the file itself in this repo's copy — **gap**: no in-repo version pin observed; see Prohibitions) | A task contract JSON for a real RI slice validates against the schema, unchanged |
| Authority grant | `docs/engineering/CAEM_CORE.md` "Matriz de autoridade"; `.caem/schemas/authority-grant.schema.json` | Fields: `grant_id`, `action`, `target`, `issued_by`, ... | Any RI action needing a protected action (writing memory to a real DB, promoting a suggestion) requires a real grant of this shape, never an RI-local approval concept | None structural; RI-specific `action`/`target` values (e.g. `memory_write`, `suggestion_promote`) are DATA within the existing schema, not new fields | Do not create an "RI approval" object with different fields/semantics for the same concept | `transferable=false`, non-reusable-across-target invariants (CAEM_CORE.md's own "Invariantes") apply unchanged to RI grants | A synthetic RI action requiring a grant is rejected deny-by-default without one, exactly like every other action in the authority matrix |
| Evidence bundle | `docs/engineering/CAEM_CORE.md` "evidência por identity/environment/gate"; `.caem/schemas/evidence-bundle.schema.json` | Fields: `bundle_id`, `task_id`, `generated_at`, `identity`, ... | RI's own evaluation runs (#88's Lane 1 already does this pattern informally) should emit evidence bundles of this shape, not a bespoke "eval result" envelope, when the evidence needs to satisfy CAEM's own evidentiary bar | RI evidence bundles reference `#88`'s `EvalSummaryV2`/`agent-review.v2-eval-summary` content as the bundle's `identity`/payload, not a parallel format | Do not define a second "proof of correctness" envelope with different field names for the same evidentiary role | Bundle identity binds to `task_id`; RI must generate a real `task_id` per run, matching the same discipline #86/#88 already apply to `run_id` | A real RI eval run's evidence bundle validates against the schema and cross-references a real, already-computed AgentReview v2 identity |
| Handoff | `docs/engineering/CAEM_CORE.md` "Handoff transfere contexto, nunca autoridade"; `.caem/schemas/handoff.schema.json` | Fields: `handoff_id`, `task_id`, `generated_at`, `from`, ... | Any RI work that spans multiple sessions/agents (e.g. a suggestion drafted in one session, reviewed in another) uses this shape to pass context | None structural | Do not invent an "RI session note" with different semantics that could be mistaken for authority transfer | N/A observed beyond the schema itself | A real cross-session RI handoff validates against the schema and, per CAEM_CORE's own invariant, carries zero grant fields that imply authority |
| Repository profile / registry | `docs/engineering/REPOSITORY_REGISTRY.md`; `.caem/schemas/repository-profile.schema.json`, `repository-registry.schema.json` | Fields: `profile_id`, `project`, `caem_version`, `repository`, ...; `registry_id`, `caem_version`, `observed_at`, `source`, ... | RI's "repository isolation" overlay concern (see below) is this primitive, not a new concept: each target repo (AgentEscala, InterLeitos, aiops-orchestrator itself) already has a canonical registry entry | RI must READ the existing registry entry for a target rather than re-deriving repo role/identity locally | Do not maintain a second, RI-local list of "known repositories and their roles" | `REPOSITORY_REGISTRY.md`'s own rule: "branches e HEADs... são checkpoint; revalidar antes de qualquer ação" — RI must revalidate, never cache indefinitely | A registry lookup for a real target repo used by an RI feature matches `REPOSITORY_REGISTRY.md`'s own entry (post-revalidation) |
| Runtime equivalence | `.caem/schemas/runtime-equivalence.schema.json` | Fields: `manifest_id`, `source_identity`, `environments`, `invariants`, ... | Out of scope for RI's own review/memory/eval work (this primitive concerns CT102/CT104 runtime parity, a deployment concern) | None — RI does not touch runtime equivalence | RI must not redefine "environment invariant" for its own review-run concept; use `RunIdentityV2` (AgentReview v2, already merged) for that instead | N/A | N/A for RI directly; noted here only to mark the boundary explicitly |
| `caem-policy` (the policy object itself) | `.caem/schemas/caem-policy.schema.json`; root `AGENTS.md`'s header (`Policy-SHA256: 9aa4949a...`) | Fields observed: `metadata`, `canonicality`, `precedence`, `principles`, ... | RI consumes the GENERATED VIEWS (`AGENTS.md`, `CAEM_CORE.md`, `PROJECT_OVERLAY.md`) of this policy; it does not, and cannot, read `policy/caem-policy.json` directly, because that file is not present in this repository (see the honesty-boundary section above) | **Real, open gap** — see Dependency strategy below | Never hand-edit a generated view (`AGENTS.md`'s own header already says this) | `Policy-SHA256` is the only drift-detection mechanism currently visible in this repo; there is no `--check` command available here (the referenced `tooling/validate.py` does not exist in-repo) | Not currently testable from within this repo alone — flagged as a real limitation, not silently assumed passing |
| Reason codes (closed, fail-closed) | `docs/engineering/CAEM_CORE.md` "Classificação de falhas" table (`product`/`regression`/`preexisting`/`environment`/`gate_unavailable`/`contract`/`authority`/`security`/`data_integrity`/`unknown`) | Table itself (markdown, not a machine-checked enum in this repo) | RI's own failure classification (e.g. classifying why a suggestion was rejected) should map onto this SAME closed set, not invent parallel categories | RI-specific detail goes in the record's own free-text/`detail` field (matching AgentReview v2's own reason-code discipline, e.g. `ReadinessReasonV2`), never a new top-level category | Do not add an eleventh top-level failure class for RI without a normative CAEM change first | The table itself is markdown prose, not a versioned schema/enum in this repo — **gap**: no machine-checked `FailureClassV2`-equivalent exists for this table today | N/A until such an enum exists; recommended follow-up under #119 |
| Execution axes / presets | `docs/engineering/CAEM_CORE.md` "Eixos de execução e presets" (`operation`, `work_unit`, `validation_scope`, `environment_role`, `risk`, `lifecycle_phase`; presets `docs`/`pr-fast`/`pr-full`/`dev-iterate`/`slice-close`/`release`) | Table itself (markdown) | Every RI slice (this one included) should be describable by these axes — this document itself is `operation=document`, `work_unit=documentation`, `validation_scope=documentary`, `environment_role=documentation`, `risk=low`, `lifecycle_phase=implementation`, matching the `docs` preset exactly | None | Do not invent an RI-specific preset vocabulary parallel to this one | Markdown prose, not machine-checked in this repo | N/A (descriptive, not enforced by code) |
| Stop conditions | `docs/engineering/CAEM_CORE.md`; this session's own executable prompt (`ScheduleWakeup`-adjacent conventions) | Prose list | RI must stop, not silently proceed, on: real provider/secret need, PHI risk, target-repo write without a nominal grant, migration/release/deploy/production — all already established, unchanged, by #86/#87/#88's own precedent this session | None | Do not weaken any listed stop condition for RI's own convenience | N/A | Already exercised concretely: #88's Lane 2/3 deferral is a real, lived instance of this exact stop condition |

## Dependency strategy (ADR-style decision)

**Decision: continue the generated-view-checked-into-git pattern this
repository ALREADY uses for `AGENTS.md`/`CAEM_CORE.md`/`PROJECT_OVERLAY.md`,
rather than introducing a second consumption mechanism.**

Options considered, per #122's own list:

| Option | Assessment |
|---|---|
| Versioned package/library dependency on a CAEM tooling package | Not adopted for this repo today: no such package is referenced in `requirements.txt`/`requirements-dev.txt` (checked: no hit for "caem" in either file), and introducing one is itself a normative dependency-strategy change requiring its own ADR sign-off, not something this documentation-only slice should decide unilaterally |
| Checkout/toolrepo pinned by SHA | This is exactly what the AgentReview v2 convergence effort already does for `mglpsw/AgentEscala`/`mglpsw/interleitos` as TARGET repos (pin by full SHA, never branch/tag) — the same discipline, if a live CAEM source repo exists, should pin it by full SHA, never a moving branch, matching CAEM_CORE.md's own explicit rule ("Branch ou tag móvel não é identidade suficiente") |
| Schemas published as artifact/release | `.caem/schemas/*.json` are already vendored as static files directly in this repo (`.caem/schemas/`) — this IS effectively "artifact vendoring", just without an accompanying `--check`/regeneration script in this repo today |
| Generation vendorized only if byte-verified | `AGENTS.md`'s own header (`Policy-SHA256`) already establishes the INTENT of byte-verified vendoring — the verification tool itself (`tooling/validate.py`) is external and not currently runnable from within this checkout |

**Chosen approach:** RI continues consuming CAEM exclusively through the
already-established generated-view files
(`AGENTS.md`/`CAEM_CORE.md`/`PROJECT_OVERLAY.md`) and the vendored schemas
(`.caem/schemas/*.json`), both already committed to this repo, both
already governed by the "DO NOT EDIT IN ISOLATION" rule. RI does **not**
introduce a new package dependency, a new toolrepo checkout, or a new
vendoring mechanism in this slice. The **real, open gap** — this repo has
no way to locally regenerate or verify these views against their real
source — is named explicitly as a limitation for #118 (ADR) to resolve
with actual authority to decide (it may require coordinating with whatever
holds the external CAEM generator, out of this session's reach) rather
than guessed at here.

**Update/rollback strategy:** since regeneration cannot happen from within
this repo today, any update to a generated view must arrive as an external
PR (from whatever process owns `tooling/generate.py`) that this repo
reviews and merges like any other change — rollback is therefore already
handled by ordinary git revert, no special mechanism needed.

## Overlay: AIOps Review Intelligence

Per #122's own list, only these are genuinely AIOps/RI-specific
specializations — none of them redefines a CAEM-central rule:

- **Repository isolation** — reuses the repository profile/registry
  primitive above; RI's specialization is which target repos it is
  authorized to hold review memory for (initially: none beyond
  `aiops-orchestrator` itself and, per existing grants, AgentEscala/
  InterLeitos as read/review-only, never write, targets).
- **Review-run identity** — reuses AgentReview v2's own `RunIdentityV2`
  (`app/agent_review/contracts_v2.py`) directly; RI must NOT invent a
  second "run identity" concept for memory/eval purposes.
- **Artifact allowlist** — RI's own specialization: which artifact kinds
  (diff, contract, response, evidence bundle) it is permitted to persist
  long-term, distinct from AgentReview v2's per-run, ephemeral artifacts.
- **DLP/PHI** — reuses the `manual_required`/`model_uncertainty`
  readiness path already established (#86/#88); RI must not invent a new
  detector, per that same precedent.
- **Feedback/dispositions** — reuses `FindingLifecycleRecordV2`'s
  disposition vocabulary (`new`/`confirmed`/`fixed`/`dismissed`/
  `superseded`/`stale`) as the semantic base for any longer-lived feedback
  RI persists; it must not invent parallel disposition values.
- **Memory/eval/suggestion lifecycle** — genuinely new to RI (no existing
  CAEM or AgentReview v2 primitive covers "a suggestion that persists
  across runs and can be promoted/rejected over time"); this is the
  primary AIOps-specific implementation surface, to be specified in #119.
- **Router observation non-authoritative** — reuses the same "Router é
  transporte de inferência... sem autoridade sobre host ou verdict" rule
  already stated in `PROJECT_OVERLAY.md`, applied to RI's own use of the
  Router for any future summarization/embedding calls.
- **Sync offline-first** — genuinely new to RI; out of scope for
  specification here (belongs to #119/#120), but must not contradict
  AgentReview v2's own "offline by default, CT104-scoped" posture.

## Reuse/adaptation boundaries

| Primitive | Classification |
|---|---|
| Task contract | reuse unchanged |
| Authority grant | reuse unchanged |
| Evidence bundle | reuse unchanged |
| Handoff | reuse unchanged |
| Repository profile/registry | reuse unchanged (read-only consumption) |
| Runtime equivalence | out of scope for RI (retire from RI's own concern list) |
| `caem-policy` object | reuse with overlay (RI never edits it; consumes generated views only) |
| Failure classification (10 classes) | reuse unchanged |
| Execution axes/presets | reuse unchanged |
| Stop conditions | reuse unchanged |
| Review-run identity | reuse unchanged (`RunIdentityV2`, not re-derived) |
| Feedback/disposition vocabulary | reuse unchanged (`FindingLifecycleRecordV2`'s dispositions) |
| DLP/PHI handling | reuse unchanged (existing readiness path, no new detector) |
| Repository isolation policy for RI | AIOps-specific implementation (which repos RI may hold memory for) |
| Artifact allowlist for RI | AIOps-specific implementation |
| Memory/eval/suggestion lifecycle | AIOps-specific implementation (genuinely new) |
| Sync offline-first strategy | AIOps-specific implementation (genuinely new) |
| Machine-checked failure-class enum | extend CAEM through versioned change (real gap; not RI's to silently patch) |
| Local CAEM regeneration/validation tooling | extend CAEM through versioned change, or accept as an external dependency (real gap; #118 to decide) |

## Contract generation pipeline (planned, not implemented)

```text
CAEM policy/source (external; not in this repo)
  -> generator (external; tooling/generate.py, not in this repo)
  -> schemas/views (IN this repo: .caem/schemas/*.json, AGENTS.md, CAEM_CORE.md, PROJECT_OVERLAY.md)
  -> AIOps Review Intelligence overlay/profile (to be specified: #119)
  -> exported communication contracts (to be specified: #119, referencing
     .caem/schemas/*.json and app/agent_review/contracts_v2.py's own
     canonical-JSON/hashing discipline wherever a new RI contract type is
     needed)
  -> conformance validation (to be specified: #121, following the same
     dual-target conformance pattern #86 already established)
```

#119's own schemas "devem referenciar ou compor primitives CAEM existentes
sempre que possível" — concretely, this means any new RI contract type
should embed or reference `.caem/schemas/task-contract.schema.json`/
`authority-grant.schema.json`/`evidence-bundle.schema.json` shapes rather
than redefining equivalent fields, and should follow AgentReview v2's own
`ContractV2Model` discipline (`extra="forbid"`, `strict=True`, `frozen=True`,
canonical JSON hashing) for anything RI-specific layered on top.

## Prohibitions honored by this document

- No generated CAEM view was hand-edited (`AGENTS.md`, `CAEM_CORE.md`,
  `PROJECT_OVERLAY.md` are all untouched by this PR — verified via `git
  diff`).
- No normative enum/reason-code was duplicated; every RI-specific value
  named above is documented as living in a free-text/detail field or as an
  explicit, flagged "real gap" requiring a future versioned CAEM change,
  never silently invented as a parallel enum.
- No `authority`/`handoff`/`checkpoint`/`evidence` concept was redefined;
  every RI use of these primitives above reuses the existing schema
  unchanged.
- Nothing here is asserted from conversation memory as if it were a
  verified fact about a repository outside this checkout — every claim
  about CAEM tooling's absence is backed by an actual `find`/`grep` run
  against this repo's real, current file tree.

## Acceptance criteria status

- [x] matriz completa de primitives CAEM reutilizáveis — table above;
- [x] estratégia de dependência/pin/upgrade definida — Dependency strategy
      section (continues the existing generated-view pattern; SHA-pin
      recommended if/when a live CAEM source repo is identified);
- [x] nenhuma primitive normativa duplicada sem justificativa — every
      RI-specific addition is classified as "AIOps-specific
      implementation" or an explicitly named CAEM gap, never silent
      duplication;
- [x] overlay AIOps delimitado — Overlay section;
- [x] gaps reais do CAEM separados de necessidades específicas do AIOps —
      Reuse/adaptation boundaries table distinguishes both explicitly;
- [x] pipeline geração → composição → conformance definido — Contract
      generation pipeline section;
- [ ] impacto registrado nas #118–#121 — this document names the impact;
      #118/#119/#120/#121 themselves still need to be authored (S18-S21)
      and cross-link back here;
- [x] proposta de ADR pronta — Dependency strategy section is written in
      ADR decision form, ready for #118 to formalize as a numbered ADR;
- [x] zero mudança funcional/runtime — no code, no schema, no config
      changed by this PR, verified via `git diff --stat`.
