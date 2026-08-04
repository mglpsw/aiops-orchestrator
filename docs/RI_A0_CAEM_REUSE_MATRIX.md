# RI-A0 — CAEM reuse matrix for AIOps Review Intelligence (#122)

Refs #122 (parent epic #126, roadmap #46). Precedes #118 (ADR + ownership
map), #119 (communication contracts), #120 (threat model/authority
matrix), #121 (conformance fixtures).

**Zero functional/runtime change.** This document is planning/architecture
only, exactly as #122 requires: no database, no HTTP API, no sync, no
workers, no deploy, no CT102 change, no AgentReview v2 implementation, no
normative change to CAEM itself.

> **Erratum (PR #173, slice #119.1), found by an independent Codex review:**
> every reference below to reading `.caem/policy.json`, `.caem/repository-
> profile.json`, `.caem/repository-registry.json`, or `.caem/schemas/*.json`
> **directly at those paths** describes a state this repository no longer has.
> PR #173 moved that CAEM 2.1.0 material, byte-unchanged, to
> `.caem/quarantine/caem-2.1/` (`authority_effect: none`, `read_only: true` —
> see `.caem/quarantine/caem-2.1/metadata.json`) and established
> `config/caem/caem-3.0-f0.pin.json` (loaded by `app.caem_consumer.f0`,
> verified by `scripts/verify-caem-f0-pin.py`) as the single active source of
> CAEM identity for this repository, pinning CAEM 3.0 F0
> (`mglpsw/caem`, carrier `28ca73f338417b5c7e9275c6154b6a0eddbb8bc7`,
> `maturity: development_freeze`, `published: false`). Any RI work reading
> this matrix for structured-JSON consumption guidance must resolve those
> paths against the quarantine location (historical-only) or the F0 pin
> (active identity), never against the original `.caem/*` paths, which no
> longer exist. The body below is preserved unedited as the historical
> record of this document's own #122 review; it is not itself CAEM-normative
> and was already documented as observational, not authoritative.

## Scope and honesty boundary

This matrix inventories CAEM primitives **observable in this repository**
(`mglpsw/aiops-orchestrator`) only: `docs/engineering/{CAEM_CORE,
PROJECT_OVERLAY,CURRENT_CHECKPOINT,REPOSITORY_REGISTRY}.md`, `.caem/
schemas/*.json`, the root `AGENTS.md`, and `CLAUDE.md`'s own imports. CAEM's
canonical specification/generator plausibly lives in a separate repository
this session has no checkout of — where this document cannot verify a
claim from that source, it says so explicitly rather than inventing one.

**Two material, real findings from this inventory, corrected once during
its own review (see the note at the end of this section) and not
previously documented anywhere in this repo:**

**1. The policy source IS vendored in-repo — just not at the path the
generated view's header names.** The root `AGENTS.md`'s own header
comment declares:

```text
Source: policy/caem-policy.json
Regenerate/validate: python tooling/generate.py && python tooling/validate.py
```

`policy/caem-policy.json` (that exact path) and `tooling/generate.py`/
`validate.py` do not exist anywhere in this repository (`find . -iname
policy -o -iname tooling` and direct path checks both come back empty) —
so the generator/validator itself is genuinely external tooling, not
vendored here. **But** `.caem/policy.json` — a sibling of `.caem/
schemas/`, in the SAME directory this document already inspected —
**is** present, and its top-level keys (`metadata, canonicality,
precedence, evidence_classes, principles, execution_axes, presets,
deprecated_aliases, state_vector, protected_actions, failure_classes,
surfaces, gate_catalog, governance`) match `caem-policy.schema.json`'s
own property list exactly; its `execution_axes`/`presets`/
`failure_classes` content is the same data `CAEM_CORE.md`'s markdown
tables render. `.caem/repository-profile.json` and `.caem/
repository-registry.json` are the same kind of vendored, schema-
conformant instance data behind `REPOSITORY_REGISTRY.md`. So the
**policy source data itself is directly, machine-readably present in
this repo** — only the generator/validator programs, and the specific
`policy/`/`tooling/` paths the header names, are external.

**Update (a later Codex review of PR #159):** `AGENTS.md`'s header
previously cited a `Policy-SHA256` (`9aa4949a...`) that reproduced
neither the raw bytes nor this repo's own canonical-JSON convention
(`json.dumps(d, ensure_ascii=False, sort_keys=True,
separators=(",",":"))`) applied to `.caem/policy.json`. That header was
corrected to the actual raw-bytes SHA-256 of the vendored
`.caem/policy.json` (`5f8d1368...`), so `AGENTS.md`'s own declared hash
now matches its own vendored source by construction. This repo's
canonical-JSON convention applied to the same file still produces a
different hash (`524a2b5f...`) — expected, since the header is scoped to
"the vendored `.caem/policy.json` above" (raw bytes), not a
canonicalized reinterpretation of it, not a remaining defect. However,
the root `CLAUDE.md` and `docs/engineering/{CAEM_CORE.md,
PROJECT_OVERLAY.md}` headers all still declare the OLD digest
(`9aa4949a...`) and were not part of that fix — so the drift this
section flags has narrowed from "AGENTS.md vs. its own source" to
"AGENTS.md's header vs. the other three generated-view headers", a
real, still-open inconsistency, not fabricated or resolved by this
note. See #118 for the same status.

**2. No application/script code consumes any of this today.** `grep -rli
caem app/ scripts/` returns zero hits: no application or script code in
this repository loads or validates against `.caem/policy.json`/`.caem/
schemas/*.json`/`.caem/repository-*.json` — they are present as static
data, unconsumed by any code path.

Both findings are load-bearing for the dependency strategy below.

*(Corrected during independent review of this document's own first
draft: an earlier revision claimed no policy-source instance data was
vendored here at all, having inspected only `.caem/schemas/` and not its
sibling files in the same `.caem/` directory. The claim was wrong; fixed
here, and named explicitly rather than silently amended, since this
document's whole value is being an accurate inventory.)*

## CAEM reuse matrix

| Primitive | Canonical source (observed in this repo) | Existing artifact/schema/tooling | AIOps Review Intelligence consumer | Adaptation needed | Duplication prohibited | Compat/versioning | Conformance test |
|---|---|---|---|---|---|---|---|
| Task contract | `docs/engineering/CAEM_CORE.md` §"Antes de agir"/"Durante"; `.caem/schemas/task-contract.schema.json` | Schema fields: `task_id`, `title`, `objective`, `project_profile`, ... | Every RI feature slice (memory write, eval run, suggestion) states objective/scope/gates as a task contract, same shape | None structural; RI populates `project_profile` with its own repository identity | Do not invent a second "task definition" shape for RI work items | Schema version tracked via `.caem/schemas/task-contract.schema.json`'s own `$id`/version field (not observed to have a version field distinct from the file itself in this repo's copy — **gap**: no in-repo version pin observed; see Prohibitions) | A task contract JSON for a real RI slice validates against the schema, unchanged |
| Authority grant | `docs/engineering/CAEM_CORE.md` "Matriz de autoridade"; `.caem/schemas/authority-grant.schema.json` | Fields: `grant_id`, `action`, `target`, `issued_by`, ... . **Correction, found by an independent Codex review:** `action` is a CLOSED enum (`commit, push, open_change_request, mark_ready, resolve_review_thread, merge, close_issue, deploy_canonical_dev, deploy_production, apply_migration, mutate_database, destructive_database_action, restart_service, invoke_external_provider, publish_review, modify_proxy_or_dns, rotate_secret, delete_branch, start_next_phase`) — verified directly against the real schema file. `memory_write`/`suggestion_promote` do NOT exist in it | Any RI action needing a protected action (writing memory to a real DB, promoting a suggestion) requires a real grant of this shape, never an RI-local approval concept | A grant for "writing memory to a real DB" must use the EXISTING `mutate_database` action. **Correction, found by an independent Codex review:** "promoting a persisted suggestion" (#119's internal lifecycle transition) is ALSO a state/database mutation, not a publication — mapping it to `publish_review` would require or consume publication authority for a purely internal transition, while a `publish_review` grant would not actually authorize that database mutation. Promotion must use `mutate_database`, exactly like the memory-write case; `publish_review` is reserved for actual review publication, never for an internal suggestion-lifecycle transition. RI must map onto real enum members, never invent new ones inline. If neither existing action is semantically adequate once RI's real design is written (#119), that is a versioned CAEM schema change (new enum member), proposed and reviewed on its own, never assumed here | Do not create an "RI approval" object with different fields/semantics for the same concept; do not invent new `action` enum values without a versioned CAEM change | `transferable=false`, non-reusable-across-target invariants (CAEM_CORE.md's own "Invariantes") apply unchanged to RI grants | A synthetic RI action requiring a grant, using `mutate_database`/`publish_review`, is rejected deny-by-default without one, exactly like every other action in the authority matrix — verified the enum accepts these two members directly from the schema file |
| Evidence bundle | `docs/engineering/CAEM_CORE.md` "evidência por identity/environment/gate"; `.caem/schemas/evidence-bundle.schema.json` | Top-level fields: `bundle_id`, `task_id`, `generated_at`, `identity`, `gate_runs`, `sanitization`, `actions`, `limitations`, `worktree`, `next_action`. **Correction, found by an independent Codex review:** `identity` is `additionalProperties: false` and permits ONLY `repository`/`base_sha`/`head_sha`/`tested_sha`/`synthetic_merge_sha`/`merge_commit_sha`/`release_commit_sha`/`deployed_revision`/`artifact_digests`/`migration_head` — no free-form field, and the schema has NO top-level `payload` property at all. Placing `EvalSummaryV2` there, as an earlier draft of this document proposed, would fail schema validation immediately | RI's own evaluation runs (#88's Lane 1 already does this pattern informally) should emit evidence bundles of this shape, not a bespoke "eval result" envelope, when the evidence needs to satisfy CAEM's own evidentiary bar | RI must reference `#88`'s `EvalSummaryV2`/`agent-review.v2-eval-summary` output through the schema's OWN supported location: `gate_runs[].artifacts[]`, each entry `{path_or_uri, sha256}` (verified directly against the schema) — e.g. `{"path_or_uri": "reports/agent-review-v2-eval-summary.json", "sha256": "<real sha256 of that file>"}` — never embedded inline in `identity` or a nonexistent `payload` field | Do not define a second "proof of correctness" envelope with different field names for the same evidentiary role; do not reference eval output through a field the schema does not have | Bundle identity binds to `task_id`; RI must generate a real `task_id` per run, matching the same discipline #86/#88 already apply to `run_id` | A real RI eval run's evidence bundle validates against the schema, with its eval artifact referenced via `gate_runs[].artifacts[]` (real path + real sha256), and cross-references a real, already-computed AgentReview v2 identity |
| Handoff | `docs/engineering/CAEM_CORE.md` "Handoff transfere contexto, nunca autoridade"; `.caem/schemas/handoff.schema.json` | Fields: `handoff_id`, `task_id`, `generated_at`, `from`, ... | Any RI work that spans multiple sessions/agents (e.g. a suggestion drafted in one session, reviewed in another) uses this shape to pass context | None structural | Do not invent an "RI session note" with different semantics that could be mistaken for authority transfer | N/A observed beyond the schema itself | A real cross-session RI handoff validates against the schema and, per CAEM_CORE's own invariant, carries zero grant fields that imply authority |
| Repository profile / registry | `docs/engineering/REPOSITORY_REGISTRY.md`; `.caem/schemas/repository-profile.schema.json`, `repository-registry.schema.json` | Fields: `profile_id`, `project`, `caem_version`, `repository`, ...; `registry_id`, `caem_version`, `observed_at`, `source`, ... | RI's "repository isolation" overlay concern (see below) is this primitive, not a new concept: each target repo (AgentEscala, InterLeitos, aiops-orchestrator itself) already has a canonical registry entry | RI must READ the existing registry entry for a target rather than re-deriving repo role/identity locally | Do not maintain a second, RI-local list of "known repositories and their roles" | `REPOSITORY_REGISTRY.md`'s own rule: "branches e HEADs... são checkpoint; revalidar antes de qualquer ação" — RI must revalidate, never cache indefinitely | A registry lookup for a real target repo used by an RI feature matches `REPOSITORY_REGISTRY.md`'s own entry (post-revalidation) |
| Runtime equivalence | `.caem/schemas/runtime-equivalence.schema.json` | Fields: `manifest_id`, `source_identity`, `environments`, `invariants`, ... | Out of scope for RI's own review/memory/eval work (this primitive concerns CT102/CT104 runtime parity, a deployment concern) | None — RI does not touch runtime equivalence | RI must not redefine "environment invariant" for its own review-run concept; use `RunIdentityV2` (AgentReview v2, already merged) for that instead | N/A | N/A for RI directly; noted here only to mark the boundary explicitly |
| `caem-policy` (the policy object itself) | `.caem/policy.json` (real instance data, present in-repo); `.caem/schemas/caem-policy.schema.json` (its schema); root `AGENTS.md`'s header (`Policy-SHA256: 5f8d1368...`, corrected by a later Codex review of PR #159 to the raw-bytes hash of the vendored file above it — see honesty-boundary section) | `.caem/policy.json`'s real top-level keys: `metadata, canonicality, precedence, evidence_classes, principles, execution_axes, presets, deprecated_aliases, state_vector, protected_actions, failure_classes, surfaces, gate_catalog, governance` — matches the schema's property list exactly | RI can read `.caem/policy.json` **directly, as structured JSON**, rather than parsing the markdown prose of the generated views — this is a materially more useful consumption path than this document's own first draft assumed | Confirm (outside this doc) whether `.caem/policy.json` is kept in sync with the generated views before relying on it as authoritative; `AGENTS.md`'s header now matches its raw bytes, but `CAEM_CORE.md`'s own separate header still declares the old digest — the drift is now between the two generated views, not resolved outright | Never hand-edit `.caem/policy.json` or any generated view directly; a change must come from whatever regenerates both | `Policy-SHA256` is the only drift-detection mechanism currently visible in this repo; `AGENTS.md`'s header now matches `.caem/policy.json`'s raw-bytes hash (not its canonical-JSON reinterpretation, which is a different, expected value), while `CLAUDE.md`, `CAEM_CORE.md` and `PROJECT_OVERLAY.md`'s own headers still do not match either; there is no `--check` command available here (`tooling/validate.py` itself does not exist in-repo) | A conformance check comparing `.caem/policy.json`'s content against `CAEM_CORE.md`'s rendered tables (execution axes, presets, failure classes) already passes by direct inspection; a BYTE-level hash check against `AGENTS.md`'s `Policy-SHA256` now passes, and against `CLAUDE.md`, `CAEM_CORE.md` and `PROJECT_OVERLAY.md`'s still fails and is flagged, not silently skipped |
| Reason codes (closed, fail-closed) | `docs/engineering/CAEM_CORE.md` "Classificação de falhas" table (`product`/`regression`/`preexisting`/`environment`/`gate_unavailable`/`contract`/`authority`/`security`/`data_integrity`/`unknown`) | Table itself (markdown, not a machine-checked enum in this repo) | RI's own failure classification (e.g. classifying why a suggestion was rejected) should map onto this SAME closed set, not invent parallel categories | RI-specific detail goes in the record's own free-text/`detail` field (matching AgentReview v2's own reason-code discipline, e.g. `ReadinessReasonV2`), never a new top-level category | Do not add an eleventh top-level failure class for RI without a normative CAEM change first | The table itself is markdown prose, not a versioned schema/enum in this repo — **gap**: no machine-checked `FailureClassV2`-equivalent exists for this table today | N/A until such an enum exists; recommended follow-up under #119 |
| Execution axes / presets | `docs/engineering/CAEM_CORE.md` "Eixos de execução e presets" (`operation`, `work_unit`, `validation_scope`, `environment_role`, `risk`, `lifecycle_phase`; presets `docs`/`pr-fast`/`pr-full`/`dev-iterate`/`slice-close`/`release`) | Table itself (markdown) | Every RI slice (this one included) should be describable by these axes — this document itself is `operation=document`, `work_unit=documentation`, `validation_scope=documentary`, `environment_role=documentation`, `risk=low`, `lifecycle_phase=implementation`, matching the `docs` preset exactly | None | Do not invent an RI-specific preset vocabulary parallel to this one | Markdown prose, not machine-checked in this repo | N/A (descriptive, not enforced by code) |
| Stop conditions | `docs/engineering/CAEM_CORE.md`; this session's own executable prompt (`ScheduleWakeup`-adjacent conventions) | Prose list | RI must stop, not silently proceed, on: real provider/secret need, PHI risk, target-repo write without a nominal grant, migration/release/deploy/production — all already established, unchanged, by #86/#87/#88's own precedent this session | None | Do not weaken any listed stop condition for RI's own convenience | N/A | Already exercised concretely: #88's Lane 2/3 deferral is a real, lived instance of this exact stop condition |

## Dependency strategy (ADR-style decision)

**Decision: continue the vendored-instance-data-checked-into-git pattern
this repository ALREADY uses (`.caem/policy.json`, `.caem/repository-
profile.json`, `.caem/repository-registry.json`, `.caem/schemas/*.json`,
plus the generated markdown views), rather than introducing a second
consumption mechanism — and prefer the structured JSON over the markdown
prose wherever RI needs to consume a field programmatically.**

Options considered, per #122's own list:

| Option | Assessment |
|---|---|
| Versioned package/library dependency on a CAEM tooling package | Not adopted for this repo today: no such package is referenced in `requirements.txt`/`requirements-dev.txt` (checked: no hit for "caem" in either file), and introducing one is itself a normative dependency-strategy change requiring its own ADR sign-off, not something this documentation-only slice should decide unilaterally |
| Checkout/toolrepo pinned by SHA | This is exactly what the AgentReview v2 convergence effort already does for `mglpsw/AgentEscala`/`mglpsw/interleitos` as TARGET repos (pin by full SHA, never branch/tag) — the same discipline, if a live CAEM SOURCE repo (as opposed to the instance data already vendored here) exists, should pin it by full SHA, never a moving branch, matching CAEM_CORE.md's own explicit rule ("Branch ou tag móvel não é identidade suficiente") |
| Schemas published as artifact/release | `.caem/schemas/*.json` AND `.caem/policy.json`/`.caem/repository-profile.json`/`.caem/repository-registry.json` are already vendored as static files directly in this repo (`.caem/`) — this IS "artifact vendoring", already done, just without an accompanying `--check`/regeneration script in this repo today |
| Generation vendorized only if byte-verified | `AGENTS.md`'s own header (`Policy-SHA256`) establishes the INTENT of byte-verified vendoring, and now matches the vendored `.caem/policy.json`'s raw-bytes hash (corrected by a later Codex review of PR #159; see honesty-boundary section) — but the root `CLAUDE.md` and `docs/engineering/{CAEM_CORE.md, PROJECT_OVERLAY.md}` headers still declare the old digest, so the intent is met for `AGENTS.md` alone, not across all four generated views; the verification tool itself (`tooling/validate.py`) is external and not currently runnable from within this checkout |

**Chosen approach:** RI consumes CAEM through the already-vendored
STRUCTURED data (`.caem/policy.json`, `.caem/repository-registry.json`,
`.caem/repository-profile.json`, `.caem/schemas/*.json`) as its primary,
machine-readable source — falling back to the generated markdown views
(`AGENTS.md`/`CAEM_CORE.md`/`PROJECT_OVERLAY.md`) only for prose a human
needs to read, never re-parsing markdown to extract structured fields a
JSON file already provides directly. All of these files are already
committed to this repo and already governed by the "DO NOT EDIT IN
ISOLATION" rule. RI does **not** introduce a new package dependency, a new
toolrepo checkout, or a new vendoring mechanism in this slice.

**Real, open gap requiring #118's own authority to resolve, not guessed
at here:** a later Codex review of PR #159 corrected `AGENTS.md`'s header
to the raw-bytes `Policy-SHA256` of the vendored `.caem/policy.json`
(`5f8d1368...`), resolving that specific mismatch — but
`docs/engineering/CAEM_CORE.md`'s own separate header still declares the
old digest (`9aa4949a...`) and was not part of that fix. The open gap is
now: `AGENTS.md` and `CAEM_CORE.md`'s headers disagree with EACH OTHER,
meaning at least one of the two generated views is stale relative to
`.caem/policy.json`, or the two headers cover different
canonicalizations than either document assumed. Any RI code that
consumes `.caem/policy.json` should treat this narrower drift as a live
risk (verify the specific fields it depends on against `CAEM_CORE.md`'s
own rendered tables at point of use) until #118 resolves which artifact
is authoritative and how all three are kept in sync.

**Update/rollback strategy:** since regeneration cannot happen from within
this repo today (the generator itself is external), any update to
`.caem/policy.json` or a generated view must arrive as a reviewed PR to
this repo — rollback is therefore already handled by ordinary git revert,
no special mechanism needed.

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
- **DLP/PHI** — **correction, found by an independent Codex review of an
  earlier draft of this document:** the `manual_required`/
  `model_uncertainty` readiness path (#86/#88) is NOT, by itself, PHI
  handling for RI's own persistence layer. That path only converts a
  chunk's post-review `synthesis.limitations` into a READINESS STATE for
  the CURRENT run's gate decision (`app/agent_review/
  readiness_decision_v2.py`) — it never touches, sanitizes, or blocks
  what gets written into whatever RI persists long-term. Separately,
  `app/agent_review/redaction.py`'s existing sanitizer (`redact_content`/
  `sanitize_artifact_value`) handles secret-shaped values and local
  filesystem paths — it has no clinical-identifier detection of any kind.
  So today, nothing in this repository would stop a clinical identifier
  from reaching a future RI persistent-memory write, even on a run that
  correctly resolved to `manual_required`. Per this slice's own
  established principle, RI must NOT invent a new ad hoc PHI detector to
  paper over this — but it also must NOT claim the gap is closed. This is
  a real, open, hard requirement for #120 (threat model/DLP/authority) to
  resolve BEFORE any RI code persists a clinical-target artifact:
  either a deterministic PHI detection/redaction step gates persistence,
  or persistence fails closed by default until one exists. Silence on
  this point in an earlier draft was itself the defect the review caught.
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
| `caem-policy` object | reuse with overlay (RI never edits `.caem/policy.json` or any generated view; reads the vendored JSON directly for structured fields, falls back to markdown for prose) |
| Failure classification (10 classes) | reuse unchanged |
| Execution axes/presets | reuse unchanged |
| Stop conditions | reuse unchanged |
| Review-run identity | reuse unchanged (`RunIdentityV2`, not re-derived) |
| Feedback/disposition vocabulary | reuse unchanged (`FindingLifecycleRecordV2`'s dispositions) |
| Per-run model-uncertainty readiness proxy | reuse unchanged (existing `manual_required`/`model_uncertainty` path) -- **correction, found by an independent Codex review:** this is NOT DLP/PHI handling; it only converts `synthesis.limitations` into a readiness state for the current run's gate decision, never detecting or blocking PHI content itself. Labelling it as PHI handling would make downstream work (#119/#120) treat the per-run DLP boundary as already implemented, when it is not -- see the DLP/PHI entry below for the real, open gap |
| DLP/PHI handling BEFORE any RI persistent-memory write | **real, open gap** — not covered by the readiness path above; a hard requirement for #120 to resolve (deterministic detection/redaction gating persistence, or fail-closed by default), not RI's to silently assume solved |
| Repository isolation policy for RI | AIOps-specific implementation (which repos RI may hold memory for) |
| Artifact allowlist for RI | AIOps-specific implementation |
| Memory/eval/suggestion lifecycle | AIOps-specific implementation (genuinely new) |
| Sync offline-first strategy | AIOps-specific implementation (genuinely new) |
| Machine-checked failure-class enum | extend CAEM through versioned change (real gap; not RI's to silently patch) |
| Local CAEM regeneration/validation tooling, and reconciling `.caem/policy.json`'s hash mismatch against the declared `Policy-SHA256` | extend CAEM through versioned change, or accept as an external dependency (real gap; #118 to decide) |

## Contract generation pipeline (planned, not implemented)

```text
CAEM policy/source (generator/validator external; instance data
  .caem/policy.json vendored IN this repo, drift-flagged against
  Policy-SHA256 -- see honesty-boundary section)
  -> generator (external; tooling/generate.py, not in this repo)
  -> schemas/views (IN this repo: .caem/schemas/*.json,
     .caem/{policy,repository-profile,repository-registry}.json,
     AGENTS.md, CAEM_CORE.md, PROJECT_OVERLAY.md)
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
- [ ] impacto registrado nas #118–#121 — #118/#119/#120/#121 already exist
      (open, fully authored, created 2026-08-01 — confirmed live via `gh
      issue view`); this document names the impact each of them should
      absorb (the `.caem/policy.json` hash-drift gap for #118, the
      structured-JSON-first consumption decision and `.caem/schemas/*
      .json`-referencing rule for #119, the "no new normative enum"
      constraint for #120's authority matrix), but does not itself edit
      those issues or cross-link from within them — left unchecked
      because that cross-linking step has not happened yet, not because
      the issues don't exist;
- [x] proposta de ADR pronta — Dependency strategy section is written in
      ADR decision form, ready for #118 to formalize as a numbered ADR;
- [x] zero mudança funcional/runtime — no code, no schema, no config
      changed by this PR, verified via `git diff --stat`.
