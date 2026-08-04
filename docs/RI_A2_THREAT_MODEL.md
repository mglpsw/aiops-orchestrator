# RI-A2 — Threat model, data policy and authority boundaries for CAEM proof execution (#120)

Refs #120 (parent epic #126, roadmap #46). Depends on #118 (completed —
`docs/RI_A1_ADR_OWNERSHIP_MAP.md`) and CAEM F0 (`mglpsw/caem#10`, pinned in
this repo by #119.1, PR #173). Blocks #160 (real DLP/PHI gate), #165
(certificates/challenges), #121 (F2/RI-B0f conformance).

**Zero functional/production change.** This document is documentation only,
exactly as #120 requires: no database, no HTTP API, no sync, no workers, no
deploy, no CT102 change, no auth implementation, no DLP detector
implementation, no secrets, no real data, no pentest. Every claim below is
backed by a real file path, `git log` entry, or a reference to a real,
already-pinned CAEM F0 contract (via `config/caem/caem-3.0-f0.pin.json` /
`app/caem_consumer/f0.py`), not invented or redefined here.

## Scope and honesty boundary

This document inventories threats and boundaries for **CAEM proof execution
in this repository's Review Intelligence (RI) track** — the not-yet-built
RI-B0 compiler/executor and the not-yet-built RI-B1 persistence layer — plus
the components that already exist and that RI-B0 will have to interoperate
with (`app/agent_review/`, `app/agent_router/`, this repo's CAEM consumer
pin). Where a component is planned but not implemented, this document says so
explicitly; it does not invent code, schemas, or behavior for components that
don't exist yet. Where a claim depends on CAEM's own active F0 schemas
(the contract registry, interface manifest), this document references
field names and enum values already verified against the real pinned
interface, not redefined here. **Correction, found by an independent
Codex review:** an earlier draft of this paragraph also named
`authority-grant` and `evidence-bundle` here, alongside genuinely active
F0 schemas; verified that neither is part of the active F0 interface --
both exist only as historical, quarantined CAEM 2.1 material (per PR
#173's quarantine and §5/§8's own correction notes below). Where this
document references those two schemas' field shapes, it does so as
documented local historical reference material, not as active CAEM
schemas. Where a claim depends instead on this
repository's own local operational policy (`docs/engineering/CAEM_CORE.md`'s
authority matrix and failure classification — a historical CAEM 2.1
projection per that file's own header, `authority_effect=none`, not
CAEM-F0-normative content), §8.1 states that distinction explicitly rather
than presenting local policy as if it were CAEM normative authority.

---

## 1. Threat model — surfaces

Each surface below states: the threat, why it matters for CAEM proof
execution specifically, and the boundary that prevents it (existing,
already-enforced boundaries are marked **[enforced]**; boundaries that
depend on RI-B0/RI-B1 code that doesn't exist yet are marked **[planned —
RI-B0]** or **[planned — RI-B1]**, and are load-bearing requirements this
document imposes on that future code, not implementations of it).

### 1.1 Artifact poisoning

A target repository, a compromised branch, or a malicious contributor
supplies a manifest/registry/schema file, a diff, or a proof artifact whose
declared identity (path, digest, contract ID) doesn't match its actual bytes,
attempting to make a later consumer trust forged content under a legitimate
name.

- **[enforced]** `app/caem_consumer/f0.py`'s loader recomputes every digest
  (`artifact_digest`, `schema_set_digest`, `contract_registry_digest`,
  `interface_manifest_digest`) byte-for-byte against the pin; a mismatch is
  `ARTIFACT_DIGEST_MISMATCH`/`INTERFACE_DIGEST_MISMATCH`/
  `CONTRACT_REGISTRY_INVALID`, never silently accepted (proven by
  `tests/caem_consumer/test_f0_interface.py`,
  `test_f0_total_fail_closed.py`).
- **[planned — RI-B0]** any artifact RI-B0 consumes as CAEM-normative input
  (packs, decision tables, plan, batch results) must be digest-verified
  against the contract registry entry it claims to satisfy before use; an
  artifact whose digest doesn't match its declared contract is rejected, not
  quarantined-and-continued.

### 1.2 Stale / cross-run / cross-repository replay

A result, proof, or evidence bundle produced for one `(repository, HEAD,
run_id)` is replayed as if it were current, for a different HEAD, a
different repository, or a later point in time after the subject changed.

- **[enforced]** AgentReview v2's `RunIdentityV2` binds every finding/payload
  to a specific run identity; the existing conformance suite
  (`tests/agent_review`) already tests that HEAD changes invalidate prior
  results.
- **[planned — RI-B0]** every proof obligation and its resolution must carry
  `(repository, subject_sha, run_id)`; a result computed under a different
  triple is `stale` by construction, never merged into a fresh result set.
  **Correction, found by an independent Codex review:** this was
  previously attributed to `docs/engineering/CAEM_CORE.md`; verified that
  the quoted invariant actually appears in this repository's own root
  `CLAUDE.md` ("## Regras": "Mudança de HEAD torna provas afetadas stale
  por padrão"), not in `CAEM_CORE.md`. This mirrors that rule.

### 1.3 Untrusted branch/policy as input

A non-canonical branch or a locally-modified policy file is fed into a proof
or gate calculation as if it were the canonical policy for the target
repository, letting an attacker who controls a branch silently change the
rules a proof is judged against.

- **[enforced]** the CAEM F0 pin is a single, digest-verified file
  (`config/caem/caem-3.0-f0.pin.json`); nothing in this repo reads a
  "current branch's policy" — the identity checked is always the pinned
  carrier, independent of what branch is checked out
  (`app/caem_consumer/f0.py:_verify_git_projection`, hardened this session —
  see PR #173's round-4 fix for `git_root` HEAD verification).
- **[planned — RI-B0]** RI-B0's own executor must resolve policy/profile
  from the pinned identity of the *target* being evaluated, never from the
  branch the analysis happens to run on; a profile sourced from an untrusted
  branch is rejected, not merely flagged.

### 1.4 Path traversal / symlink / extra-file injection

A manifest or archive declares a path that escapes the interface root
(`../`, absolute path, backslash-as-separator), or a symlinked file/directory
substitutes attacker-controlled content for a path that appears legitimate,
or extra files are smuggled into a transport archive beyond the manifest's
declared set.

- **[enforced]** `UNSAFE_ARTIFACT_PATH` rejects path traversal, absolute
  paths, backslash separators, symlinked leaf files, **and symlinked
  ancestor directories** (this repo's own PR #173 description, red-first
  item 9); zip transport is verified against the manifest's exact declared
  file set, rejecting extras (item 10 in the same PR).
- **[planned — RI-B0]** any archive/bundle RI-B0 unpacks (proof-carrying
  bundles, per CAEM's reserved `caem.proof-carrying-bundle` contract, when
  implemented) must apply the same path-safety discipline before extraction.

### 1.5 Secret / token / header / env / local path leakage

A change-request, log, evidence bundle, or comment posted to a public
surface (GitHub PR/issue, this repo's own generated docs) accidentally
carries a credential, an internal hostname, a local filesystem path, or an
environment variable value.

- **[enforced, operationally]** **Correction, found by an independent
  Codex review:** an earlier draft attributed the guardrail "Não exponha
  segredos, tokens, IPs privados desnecessários ou dados pessoais/
  clínicos" to `docs/engineering/CAEM_CORE.md`; verified that
  `CAEM_CORE.md` does not contain that text -- it comes from this
  operator's own private global tooling configuration, not from a file
  committed to this repository, so it is not cited here as a repo-visible
  source. What IS verifiable from the repo alone: this session's own PR
  #173 comments contain zero secrets, tokens, or local
  paths outside the repo's own checkout (`/tmp/caem-work`, itself only a
  disposable clone path, never a credential).
- **[planned — RI-B0/RI-B1]** any artifact or change request RI-B0 emits
  toward CAEM (`caem.contract-change-request.v1`, `implemented_at_f0`, and
  therefore already schema-constrained) must pass through a sanitizer before
  transport; this document does not implement that sanitizer (that's #160),
  it requires that one exist and run **before** any network call or
  persistence write, fail-closed if unavailable.

### 1.6 PHI / identifiable clinical narrative

Given this AIOps deployment's real consumers include clinical products
(AgentEscala, InterLeitos, per `docs/engineering/REPOSITORY_REGISTRY.md`),
any pipeline that ingests real diagnostic/action data risks a raw clinical
narrative, a patient identifier, or a CPF/CNS-shaped string reaching an
artifact, log, or persisted record.

- **[enforced today, operationally, not structurally]** RI-B0/RI-B1 do not
  exist; no pipeline in this repo ingests real clinical data for RI
  purposes today. `app/agent_router/`'s existing action-runner path is
  unrelated (infra diagnostics, not clinical content). **Correction, found
  by an independent Codex review:** an earlier draft cited
  `tests/test_legacy_adapter_quarantine.py` as proof this path is
  redaction-tested; verified directly that file's three tests
  (`test_action_runner_does_not_import_legacy_adapters`,
  `test_run_path_does_not_use_legacy_adapters`,
  `test_docker_adapter_starts_disabled_by_default`) check legacy-adapter
  isolation only, never redaction. No dedicated redaction test for
  `app/agent_router/`'s action-runner path was found in this repo; the
  claim is removed rather than replaced with an unverified substitute.
  **Further correction,
  found by an independent Codex review:** the AgentReview runner's own
  sanitizer (`app/agent_review/redaction.py:sanitize_artifact_value`, see
  its row in §3) redacts secrets and local paths only, with no PHI
  detection — so the "no PHI reaches this pipeline" property is true only
  because AgentReview is not currently pointed at a clinical-target repo,
  not because the pipeline would refuse or redact clinical content if it
  were. This is an operational fact, not a structural guarantee, and is the
  concrete reason #160 is a hard prerequisite (§6) rather than a
  nice-to-have.
- **[planned — RI-B0/RI-B1]** this is the single highest-consequence future
  surface. Real DLP/PHI detection (#160) is an explicit prerequisite —
  **not this document's job to implement** — but this document's closure
  criterion requires that no future component gets a code path that reaches
  persistence, transport, or a public artifact without first passing through
  that gate. See §3 (data policy) and §6 (stop conditions).

### 1.7 Identity spoofing

An actor (human, automation, or a component) presents itself as a different,
more-trusted actor — e.g. a script claiming to be "authorized human," a
component asserting a `verifier_identity` it doesn't hold, or a target repo
presenting another repo's identity.

- **[enforced]** CAEM F0's own `verifier_identity` field is pinned and
  digest-verified per interface, not asserted freely by a caller; this
  session's own round-3 Codex fix (PR #173, commit `ad76f3b`) specifically
  closed a TOCTOU bypass where a caller object could present one identity
  during a check and a different one during use — proof that identity
  checks in this codebase are held to "verified once, reused from a
  snapshot," not "trusted because it was asserted."
- **[planned — RI-B0]** every actor writing to a shared artifact
  (registry, manifest, evidence bundle) must be bound to a real,
  independently-verifiable identity (repository + SHA + tool identity, per
  `docs/engineering/PROJECT_OVERLAY.md`'s "Identidade mínima de uma
  execução" -- corrected here from an earlier draft that misattributed
  this to `CAEM_CORE.md`, which does not contain it), never a free-text
  `actor` string trusted at face value.

### 1.8 Fraudulent disposition

A finding, review thread, or proof obligation is marked "resolved,"
"dismissed," or "non-blocking" without the substantive condition that
disposition is supposed to certify actually holding — e.g. marking a P1
finding "out of scope" to clear a merge gate.

- **[enforced]** this session's own PR #173 merge discipline: every P1/P2
  finding across 5 Codex rounds was fixed with a red-first test proving the
  bug existed, then a passing test proving the fix, documented in a PR
  comment naming the exact finding — never silently marked resolved.
- **[planned — RI-B0]** any `disposition` field RI-B0 emits (accepting,
  rejecting, or deferring a proof obligation) must reference the specific
  gate/evidence that grounds it; a disposition with no backing evidence
  reference is rejected by schema, not merely discouraged by convention.

### 1.9 Fabricated proof / counterexample

A proof-carrying bundle or counterexample certificate is asserted without
the corresponding computation/replay actually having been performed — the
CAEM-side analogue of "hallucinated test output."

- **[enforced — general principle, already in force]** `docs/engineering/
  **Correction, found by an independent Codex review:** an earlier draft
  attributed the quote "Nunca fabrique teste, print, inspeção, estado de
  PR ou sucesso de deploy" to `docs/engineering/CAEM_CORE.md`; verified
  that `CAEM_CORE.md` does not contain that text -- like the §1.5
  correction above, it comes from private operator tooling configuration,
  not a repo-committed source, so it is not cited here as one. This
  session followed that discipline literally regardless: every
  Codex finding in PR #173 was reproduced against the prior commit via
  `importlib`-loaded old code before being called a real bug, and every fix
  was re-verified with the same reproduction script before being called
  fixed (see PR #173's round-3 and round-4-confirmation comments for two
  concrete instances).
- **[planned — RI-B0, depends on CAEM promoting these from `reserved`]** proof-carrying bundles and
  counterexample certificates are CAEM `reserved` contracts today
  (`caem.proof-certificate.v1`, `caem.counterexample-certificate.v1`,
  `caem.proof-carrying-bundle` family) — RI-B0 cannot consume them as
  "implemented" until CAEM promotes them; consuming a `reserved` contract as
  if implemented is already structurally rejected today
  (`CaemF0ContractUnavailable(CONTRACT_RESERVED)`,
  `require_caem_f0_contract`).

### 1.10 False independent verifier

A component that itself produced a result also certifies that result as
"independently verified," collapsing the separation between producer and
verifier that gives independent verification its meaning.

- **[planned — RI-B0]** "independent verifier"
  requires a genuinely separate execution context (clean environment,
  offline, pinned toolchain) from the component whose output is being
  verified — a requirement this document imposes directly, not one it
  attributes to an external roadmap document. RI-B0's own
  executor can never simultaneously be the verifier for its own run. This
  is a hard separation this document imposes on RI-B0's design, not a
  feature to build here.

### 1.11 Duplicate amplification / correlated evidence counted as independent

The same underlying observation is counted multiple times (e.g. the same
finding surfaced by two different chunk fragments, or the same run replayed
and both copies counted) as if each occurrence were independent corroborating
evidence, inflating confidence without new information.

- **[enforced]** AgentReview v2's chunk-coverage and fragment-manifest work
  (this repo's recent `feat(agent-review/v2)` commits — coverage proof,
  fail-closed policy, synthesis/lifecycle aggregation) already dedupes
  findings across chunk boundaries by binding them to a canonical
  `(file, line, finding)` identity rather than counting raw occurrences.
- **[planned — RI-B0]** the accumulation semantics CAEM eventually normalizes for
  variable/evidence combination (duplicate_key, conflict/missing/retraction
  semantics) must be the single mechanism RI-B0 uses to combine
  observations, once CAEM defines them; RI-B0 does not invent a second, ad
  hoc deduplication scheme in the meantime.

### 1.12 Memory leakage between repositories

A memory, suggestion, or learned pattern derived from one target repository
(possibly containing that repo's sensitive context) is applied to, or
surfaces in, a different target repository's analysis.

- **[enforced today]** RI-B1 (persistence, memory) does not exist yet in
  this repo; there is no cross-repository memory store to leak from.
- **[planned — RI-B1]** any future memory/suggestion store must be
  partitioned by repository identity as a hard boundary (not a filter
  applied at read time); a query that can return another repository's memory
  by omitting a repository filter is a design defect, not an edge case to
  patch later.

### 1.13 Observation treated as proof

A raw signal (a log line, a metric, a single LLM-produced review comment) is
propagated as if it had the evidentiary weight of a verified proof —
collapsing exactly the distinction §4 exists to keep separate.

- **[enforced]** `docs/engineering/PROJECT_OVERLAY.md`: "Review textual do
  modelo é advisory. `review-readiness` determinístico é a decisão
  consumível" — already the operating principle for every existing
  AgentReview review in this repo.
- **[planned — RI-B0]** any pipeline stage that receives an "observation"
  (per CAEM's `caem.evidence-projection.v1`/observation-lineage concepts,
  when promoted from `reserved`) must carry that classification through
  unchanged; nothing is allowed to relabel an observation as a proof by
  omitting its provenance field.

### 1.14 Proof treated as authority; handoff treated as grant

A valid proof (of plan, execution, or result) is treated as if it also
authorized an action (merge, deploy, mutate a database); or a handoff
between sessions/agents is treated as if it transferred the authority the
sending session held, rather than just its context.

- **[enforced, operationally]** **Correction, found by an independent
  Codex review:** an earlier draft attributed the quotes "LLM produz
  hipótese; contratos determinísticos decidem" and "Handoff transfere
  contexto, nunca autoridade" to `docs/engineering/CAEM_CORE.md`; verified
  that file does not contain either quote -- both come from private
  operator tooling configuration, not a repo-committed source. The
  underlying principle already governed this session's own conduct
  regardless of which document states it (every
  protected action in this session — merge, mark-ready — was gated on a
  real, currently-live authority grant, never inferred from a prior proof or
  a prior session's handoff).
- **[planned — RI-B0]** RI-B0's own outputs (readiness decisions, proof
  results) are consumed by the authority layer, never treated as
  self-authorizing; the schema separation in §4 is the mechanism.

### 1.15 Migration / destructive action without grant

A schema migration, a destructive database operation, or any other
irreversible action is executed because a proof or readiness result implied
it was safe, without an actual, currently-live authority grant scoped to
that specific action and target.

- **[enforced]** `docs/engineering/CAEM_CORE.md`'s authority matrix: every
  protected action (`migration`, `ação destrutiva DB`, etc.) requires its own
  grant with `transferable: false`; this document's own governing "overnight
  execution" grant explicitly excludes migration, deploy, and database
  actions from its scope, and this session has performed none.
- **[planned — RI-B0/RI-B1]** RI-B1 persistence (when built) must gate every
  migration and destructive action behind the same authority-matrix
  discipline CAEM_CORE.md already defines for this repo generally — RI does
  not get a lighter-weight authority model just because its own team wrote
  the migration.

---

## 2. Trust-boundary diagram

```text
                          ┌─────────────────────────────┐
                          │   CAEM (mglpsw/caem)          │
                          │   normative grammar, schemas,  │
                          │   contract registry, F0/candidate│
                          │   revisions, verifier reference │
                          └──────────────┬──────────────┘
                                         │ pinned by digest, read-only
                                         │ (config/caem/caem-3.0-f0.pin.json)
                                         ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │                mglpsw/aiops-orchestrator (this repo)              │
   │                                                                    │
   │  ┌───────────────┐   offline, CT104   ┌───────────────────────┐  │
   │  │ AgentReview     │◄──────────────────┤ app/caem_consumer/f0.py │  │
   │  │ runner          │   scoped, no       │ (identity gate,         │  │
   │  │ (app/agent_review/)│  network, no    │  fail-closed loader)    │  │
   │  └───────┬───────┘   persistence        └───────────────────────┘  │
   │          │ manifests/payloads/readiness artifacts (JSON, on disk)  │
   │          ▼                                                          │
   │  ┌───────────────────────┐        ╔══════════════════════════╗   │
   │  │ RI-B0 compiler/executor │◄──────╢  NOT YET IMPLEMENTED       ║   │
   │  │ (planned; #162-#165)     │       ║  every box below is a      ║   │
   │  └───────┬───────────────┘        ║  planned boundary, not an   ║   │
   │          │                         ║  existing runtime           ║   │
   │          ▼                         ╚══════════════════════════╝   │
   │  ┌───────────────────┐   ┌────────────────────┐                   │
   │  │ Verification Index │   │ human feedback       │                   │
   │  │ (planned; #164)     │   │ surface (planned)    │                   │
   │  └───────┬───────────┘   └──────────┬─────────┘                   │
   │          │                          │                              │
   │          ▼                          ▼                              │
   │  ┌────────────────────────────────────────────┐                   │
   │  │ RI-B1 persistence (planned; explicitly        │                   │
   │  │ out of scope for #119-#121; own future grant) │                   │
   │  └────────────────────────────────────────────┘                   │
   │                                                                    │
   │  ┌───────────────────────┐        ┌──────────────────────────┐   │
   │  │ app/agent_router/        │        │ ProjectOps                │   │
   │  │ ("Agent Router" — CT102, │        │ (separate track; advisory,│   │
   │  │  HTTP, action execution) │        │  fail-safe CI intelligence)│   │
   │  └───────────────────────┘        └──────────────────────────┘   │
   │                                                                    │
   │  ┌───────────────────────┐                                       │
   │  │ publisher (planned;      │  revalidates PR/HEAD before          │
   │  │  not yet implemented)    │  publishing; never mutates code,     │
   │  └───────────────────────┘  deploy, or infra                      │
   └─────────────────────────────────────────────────────────────────┘
                                         │
                                         │ protected actions only
                                         │ through a live authority grant
                                         ▼
                          ┌─────────────────────────────┐
                          │        authorized human         │
                          │  (axioms, risk, grants,         │
                          │   ratification, tag/release)     │
                          └─────────────────────────────┘
```

**Naming collision, flagged explicitly (same discipline as RI-A1's own
finding for "Agent Router"):** the `agent-router-api` **repository**
(`mglpsw/agent-router-api`, the LLM inference gateway/adaptive router
described in `docs/engineering/REPOSITORY_REGISTRY.md`) is a **different**
system from **`app/agent_router/`**, this repo's own CT102 diagnostic/action
runtime module. This document uses **"Agent Router (repo)"** for the former
and **`app/agent_router/`** (backticked, with the path) for the latter,
matching `docs/RI_A1_ADR_OWNERSHIP_MAP.md`'s own disambiguation. CAEM's
"Router" (transport of inference, per `docs/engineering/
PROJECT_OVERLAY.md`'s trust architecture) is a **third**, unrelated concept:
"transporte de inferência por contrato, sem autoridade sobre host ou
verdict" — it moves model calls, it does not run actions and it is not a
package in this repository at all.

---

## 3. Authority / data matrix

For each component: **read** (what it may read), **write** (what it may
produce), **data** (what real data classes it may touch), **denied actions**
(explicitly forbidden even if technically reachable), **grant** (what
authority-matrix action, per `CAEM_CORE.md`, would be required for it to do
more), **audit** (what evidence its actions leave), **blast radius** (worst
case if compromised or buggy), **kill switch** (how to stop it).

| Component | Status | Read | Write | Data | Denied actions | Grant required for more | Audit | Blast radius | Kill switch |
|---|---|---|---|---|---|---|---|---|---|
| **CAEM verifier** | real, external (`mglpsw/caem`) | its own schemas/policy/registry | contract registry, interface manifest, F0/candidate revisions | none (normative grammar only) | writing to any consumer repo; asserting authority over merge/deploy | n/a — CAEM is normative source, not an actor in this repo | CAEM's own repo history + digests | a bad CAEM revision could mis-verify proofs repo-wide | consumers pin an exact digest; a bad revision simply isn't adopted (`candidate adoption` is explicit, never automatic) |
| **AgentReview runner** | real (`app/agent_review/`, this repo) | diff, checks, existing evidence artifacts (offline, CT104) | manifests, payloads, `ReviewReadinessV2` artifacts (JSON, disk) | diff content, file paths, findings, sanitized against secrets and local paths only. **Correction, found by an independent Codex review of an earlier draft:** this row previously claimed "no PHI/secrets by design of the surfaces it touches" — verified directly that `app/agent_review/redaction.py:sanitize_artifact_value` (called by `chunk_payload_builder.py` and `payload_references_v2.py` before any payload is persisted) only redacts secret-shaped values, private keys, bearer tokens, and local paths; its own docstring is exactly "Redact secrets and local paths before emitting an uploadable artifact" — it has no PHI/clinical-narrative detection. If this runner is ever pointed at a target whose diffs can contain clinical narrative (e.g. AgentEscala/InterLeitos, per `REPOSITORY_REGISTRY.md`), persisted payload JSON can carry that content today. This is a real, currently-existing gap, not a hypothetical one — see the DLP requirement in §4 and the stop condition in §6 | network calls, git push/merge, provider calls, persistence beyond disk artifacts, **and (until #160 exists) any run against a target repository whose diffs may contain clinical narrative** | any protected action from `CAEM_CORE.md`'s matrix (commit/merge/etc.) — none granted to this component itself; a real DLP/PHI gate (#160) before running against clinical-adjacent targets | JSON artifacts on disk, `tests/agent_review`/`tests/evals` suites, this repo's own commit history | a bug produces a wrong readiness verdict, consumed as advisory only (`PROJECT_OVERLAY.md`: "Review textual do modelo é advisory"); **additionally, until #160 lands, a clinical-target run could leak PHI into a persisted JSON artifact** | stop invoking the CLI/harness; readiness gate (`review-readiness` schema) is the only consumable decision, and a `manual_required`/`blocked_*` state is always available |
| **AIOps RI-B0 compiler/executor** | **planned** (#162-#165, not implemented) | pinned CAEM candidate revision's contracts; AgentReview v2 artifacts | packs/tables/plan/batch results, per contracts RI-B0 will consume once CAEM promotes them from `reserved` | evidence classes per `caem.canonical-variable-catalog.v1` (once implemented) — must exclude raw diff/prompt/PHI per §4 | consuming a `reserved` CAEM contract as implemented; self-verifying its own output (§1.10); persistence writes without RI-B1's own grant | `mutate_database` (once RI-B1 exists), any `deploy_*`/`migration` action | evidence bundle per the shape documented in `docs/RI_A0_CAEM_REUSE_MATRIX.md` (`bundle_id, task_id, identity, gate_runs, sanitization, actions, limitations, worktree, next_action`) -- see this section's own correction note below on why that schema is historical CAEM 2.1 reference material, not an active F0 contract | a compromised executor could fabricate proof obligations at scale; contained by verifier independence (§1.10) and per-contract digest binding | its own future feature flag / config toggle; until built, this "kill switch" is simply "it doesn't exist" |
| **Verification Index** | **planned** (#164, not implemented) | RI-B0's proof results, dependency projection digests | an index of what's been verified, for lookup — not a source of truth itself | proof metadata (hashes, IDs, verdicts) — no raw content | asserting a result it didn't itself observe from RI-B0's real output; being queried as if it were the verifier | none beyond read access to RI-B0's outputs | its own query log | a poisoned index could misdirect which proofs are trusted; mitigated by requiring index entries to carry a `dependency_projection_digest` back to the real artifact | rebuild the index from RI-B0's real artifacts (it must be regenerable, per the same discipline as CAEM's own generated views) |
| **future ingestion API/worker** | **planned** (RI-B1, explicitly out of scope for #119-#121) | external inputs destined for RI (real diagnostics, potentially clinical-adjacent per AgentEscala/InterLeitos) | queued/persisted records | **highest-risk data class in this whole model** — potential PHI/clinical narrative, secrets, tokens | ANY persistence or transport of raw, unsanitized input; bypassing DLP (#160) for any reason, including debugging | its own dedicated grant, scoped narrowly, requiring #160's real DLP gate as a precondition, never bundled into a general "RI-B1" grant | full request/response audit trail with DLP-pass evidence attached | worst-case: PHI leak, secret leak, cross-tenant data exposure | must not exist without #160 merged and gating it; if it misbehaves post-launch, the ingestion endpoint itself is disabled at the network/deploy layer (out of this document's scope to specify further — that's a deploy-time control) |
| **human feedback surface** | **planned**, not implemented | human-authored feedback text | suggestions, F1 change-request drafts (`caem.contract-change-request.v1`, sanitized-evidence-only per §4) | free text from a human — must be treated as untrusted input for DLP purposes, same as any other ingestion path | writing directly to CAEM (F1 is a request, never a direct write); asserting `authority_effect` other than `none` | any grant that would let feedback bypass F1's review-and-accept step | the F1 change request itself, plus whatever DLP pass gated it | a malicious/careless human feedback entry could contain PHI/secrets if unsanitized — same mitigation family as 1.5/1.6 | reject the ingestion path entirely; feedback with no accepted change request has zero effect by construction |
| **ProjectOps** | real, separate track (per `CURRENT_CHECKPOINT.md`: "ProjectOps v1 permanece trilha separada de inteligência de CI, advisory e fail-safe") | CI signals | advisory CI intelligence output | CI metadata; no RI-specific data | asserting readiness/authority over RI's own gates; being treated as an RI component (it isn't one) | none from RI's side — it's out of this document's authority scope entirely | its own track's evidence, not this document's concern | out of scope for this threat model beyond noting the boundary | out of scope |
| **`app/agent_router/` (CT102 runtime, "Agent Router" — see naming-collision note above)** | real, this repo | diagnostics, action catalog, approval requests | HTTP responses; JSONL run/approval/audit logs (`var/runs/`, `var/approvals/`, `var/audit/`) | operational diagnostics; NOT clinical data, NOT RI proof data — already isolated by design (no import of `app/agent_review/` in either direction) | executing an unallowlisted command (`command_guardrails.py`); running quarantined SSH/Docker/local-shell adapters (`tests/test_legacy_adapter_quarantine.py` already proves the two paths it tests) | any RI-specific grant — this component doesn't touch RI at all today, and this document does not propose that it should | `var/audit/aiops_audit.jsonl`, existing test suite | worst case is within CT102's own already-documented action-execution blast radius, unrelated to CAEM proof execution | existing `require_api_token` gate + action allowlist; out of this document's scope to redesign |
| **publisher** | **planned**, not implemented anywhere in this repo | a readiness decision + the artifact it applies to | a publication action (e.g. posting a review, updating a check) | whatever the artifact being published contains — must already be sanitized upstream | mutating code, deploy, or infrastructure (`PROJECT_OVERLAY.md`: "publisher não altera código, deploy ou infraestrutura"); publishing without revalidating PR/HEAD first | **correction, found by an independent Codex review:** `CAEM_CORE.md`'s real authority matrix has no `publicar`/publish action at all -- only `resolver thread` is real there; the only `publish_review` enum member observed in this checkout is in the quarantined CAEM 2.1 `authority-grant.schema.json`, which this document elsewhere declares non-authoritative. The real, active grant this row can point to today is `resolver thread` only; mapping publication itself onto a specific active grant action is left as a future decision (a new/adapted authority-matrix entry), not prescribed here | the publish action itself (API call, comment, check update) | a stale or wrong publish action misinforms reviewers; mitigated by the mandatory PR/HEAD revalidation immediately before every publish | don't grant the publish action; it is not autonomous by design |
| **authorized human** | real | everything, subject to their own judgment | grants, ratifications, merges, deploys, releases | full access by design — this is the root of authority, not a component to sandbox | none — this is the actor every other row is bounded relative to | n/a | their own actions' natural GitHub/Git audit trail | full — this is why every grant in this model is scoped, non-transferable, and target-specific, so a human's own mistake stays contained to what they explicitly authorized | n/a |

---

## 4. Data policy

- **Classification and isolation by repository.** Every artifact, memory, or
  evidence record RI-B0/RI-B1 ever produce must carry the identity of the
  repository it concerns (per `docs/engineering/PROJECT_OVERLAY.md`'s
  "Identidade mínima de uma execução" -- corrected here from an earlier
  draft that misattributed this to `CAEM_CORE.md`, which does not contain
  it); cross-repository queries default-deny unless a query
  explicitly and narrowly opts into cross-repo aggregation for a stated
  reason (e.g. a roadmap-level report), never as the default behavior. This
  directly closes §1.12.
- **Artifact allowlist.** Only artifacts of a type already named in this
  document's component matrix (manifests, payloads, readiness artifacts,
  evidence bundles, contract-change-requests, proof/counterexample
  certificates once promoted from `reserved`) may cross a trust boundary
  (disk → network, or process → persistence). An artifact type not on this
  list requires a new version of this document before it's allowed through,
  not an ad hoc exception.
- **Prohibited fields — proposed by this document, not an existing CAEM/
  AIOps policy.** **Correction, found by an independent Codex review of an
  earlier draft:** this list was previously presented as "reused verbatim"
  from `docs/engineering/PROJECT_OVERLAY.md`; verified directly that
  `PROJECT_OVERLAY.md` contains no such list — it only requires sanitized
  metrics/logs for the weekly AIOps collection. The list itself (raw diff,
  raw prompt, raw response, secret/token/header/env, local path, PHI,
  narrativa clínica identificável, CPF/CNS, identidade móvel usada como
  identidade) is a reasonable data-policy requirement, but this document is
  its actual source, not an existing normative document — it requires the
  same review and approval as any other new policy this document proposes,
  not the weight of an already-ratified CAEM/AIOps rule. Any artifact
  schema RI-B0 defines must make these fields structurally absent (not
  merely "usually empty") — the same *mechanism* the (quarantined,
  historical CAEM 2.1) `evidence-bundle.schema.json` already applies
  (`additionalProperties: false`, closed enums), per
  `docs/RI_A0_CAEM_REUSE_MATRIX.md`'s verified finding on that schema. This
  is cited only as an example of the mechanism (closed-schema field
  rejection), not as a claim that this schema is part of the active F0
  interface — it is not (see §5's correction note); the specific field
  list above is new, proposed by this document.
- **DLP before proof artifact, transport, and persistence.** A real DLP/PHI
  gate (#160, explicitly out of scope for this document to implement) must
  run before any of these three events — emitting a proof artifact,
  transporting data across a network boundary, or persisting to RI-B1. No
  code path may perform any of the three without first passing through that
  gate; where the gate is unavailable, the correct behavior is fail-closed
  (block the action), never fail-open (proceed without the check).
- **Raw diff/prompt/response: default-denied.** This document's own
  requirement, not an existing CAEM/AIOps rule. **Correction, found by an
  independent Codex review:** an earlier draft attributed this to "CAEM's
  own F1 protocol (`authority_effect: none`, `sanitized_evidence_only`)
  per `docs/engineering/PROJECT_OVERLAY.md`'s 'AIOps semanal' section";
  verified that `PROJECT_OVERLAY.md` contains neither an F1 protocol
  section nor the term `sanitized_evidence_only`, and that
  `authority_effect: none` there describes this repo's own CAEM-consumer
  pin identity, not a raw-content handling rule. Nothing
  in RI's own pipeline gets a "debug mode" exception to persist or transmit
  raw content; if a real incident needs raw content for investigation, that
  is a separate, explicitly-granted, human-supervised action, never a
  default code path.
- **Retention / deletion / export.** Not decided by this document (the
  issue's own scope names this as a required section, not a place to invent
  a policy without a data owner's sign-off). This document's contribution is
  the boundary: retention/deletion/export policy, once decided, must itself
  be repository-scoped (per the isolation rule above) and must never apply
  to CAEM's own upstream artifacts (RI never deletes or exports CAEM
  identity — RI only pins it).
- **Encryption requirements (future).** Not decided here; flagged as a gap
  RI-B1's own design must close before handling anything above the
  "operational diagnostics" sensitivity class already handled today by
  `app/agent_router/`.
- **Backup scope.** Same disposition as `app/models/database.py`'s existing
  row in `docs/RI_A1_ADR_OWNERSHIP_MAP.md`: a live database with real
  historical records is a data-loss-risk decision this document has no
  authority to make. RI-B1's backup scope is future work, gated by whoever
  owns data-retention decisions for this repo.
- **Observability without sensitive cardinality.** Metrics/logs describing
  RI-B0/RI-B1 behavior (counts, durations, verdicts, reason codes) are fine;
  metrics/logs keyed by content that could re-identify a person, a specific
  clinical case, or a secret value are not — mirroring
  `app/api/legacy_usage.py`'s existing pattern of counting by **endpoint
  label**, never by caller identity or payload content
  (`docs/RI_A1_ADR_OWNERSHIP_MAP.md`'s own verified description of that
  module).
- **Preservation of causal/independence groups without identifiable
  content.** Where RI-B0 needs to know that two observations are causally
  linked or come from independent sources (to avoid §1.11's duplicate
  amplification), it must carry that linkage as an opaque, non-reversible
  correlation ID or digest — never as the underlying content that would let
  a reader reconstruct what was linked.

---

## 5. Separações obrigatórias

```text
evidence ≠ observation ≠ proof ≠ disposition ≠ suggestion ≠ authority ≠ readiness
```

Defined precisely, so future RI-B0/RI-B1 code has no ambiguity about which
one it's producing:

- **evidence** — a bundle of concrete, verifiable facts about a specific
  run/gate, shaped like the `evidence-bundle.schema.json` documented in
  `docs/RI_A0_CAEM_REUSE_MATRIX.md` (`bundle_id, task_id, identity,
  gate_runs, sanitization, actions, limitations, worktree, next_action`).
  **Correction, found by an independent Codex review:** an earlier draft
  called this "CAEM's real" schema; verified that this exact schema file
  lives only under `.caem/quarantine/caem-2.1/schemas/` -- historical,
  non-normative CAEM 2.1 material (per PR #173's quarantine), not part of
  the active F0 interface, whose own `schema_set` covers a different,
  smaller set of schemas (contract-registry, interface-manifest,
  contract-change-request). This shape is used here as a well-documented
  local reference for what a bundle-of-facts artifact looks like, not as a
  claim about CAEM F0's own contracts; RI-B0/RI-B1 must not implement or
  validate against it as if it were an F0 contract without CAEM actually
  promoting an equivalent one. Evidence describes what happened; it does
  not, by itself, decide anything.
- **observation** — a single raw signal (a log line, a metric sample, one
  model-produced comment) with provenance attached. An observation is weaker
  than evidence: it hasn't necessarily been corroborated, deduplicated, or
  bound to a verified identity yet (§1.13).
- **proof** — a result that has actually been computed/verified against a
  stated obligation (CAEM's `caem.assertion.v1`/`caem.proof-obligation.v1`,
  once promoted from `reserved` by CAEM), following the six-state total
  resolution CAEM F0 already freezes. A proof is stronger than evidence: it's
  evidence that has been run through a specific, named verification
  procedure and produced one of the six total-resolution states.
- **disposition** — a decision about what to do with a specific finding or
  proof obligation (accept/reject/defer), which must reference the evidence
  or proof that grounds it (§1.8). A disposition is an act on top of a proof
  or evidence record, not a proof itself.
- **suggestion** — a proposed change or action a human or automation puts
  forward for consideration (e.g. #119's internal "suggestion" lifecycle
  concept, per `docs/RI_A0_CAEM_REUSE_MATRIX.md`'s own analysis of that
  term). A suggestion carries no authority; promoting a suggestion to
  something acted-on is itself a `mutate_database`-class action requiring
  its own grant (already established in RI-A0's analysis).
- **authority** — a live, currently-valid, target-scoped, non-transferable
  grant to perform a specific protected action (`CAEM_CORE.md`'s matrix). Not
  implied by any of the above, however strong — this is exactly §1.14's
  boundary.
- **readiness** — the deterministic, schema-shaped consumable decision
  (`agent-review.review-readiness.v2`, or its future RI-B0 analogue) that
  downstream tooling is allowed to act on. Everything above readiness in
  this list feeds it; nothing above it substitutes for it
  (`PROJECT_OVERLAY.md`: "Review textual do modelo é advisory.
  `review-readiness` determinístico é a decisão consumível").

**The failure mode this separation exists to prevent:** collapsing any
arrow above into an equals sign — e.g. treating an observation as a proof
(§1.13), a proof as authority (§1.14), or a suggestion as already-authorized
(the exact concern RI-A0 flagged for #119's suggestion-promotion design).
Any RI-B0/RI-B1 schema that merges two of these seven concepts into one
field is a design defect against this document, not a stylistic choice.

---

## 6. Stop conditions (RI-B0 / RI-B1)

RI-B0 implementation must stop and escalate, rather than proceed, if:

- a CAEM contract it needs is still `reserved` — checked live against the
  currently-pinned interface at the time of the check (`scripts/
  verify-caem-f0-pin.py` / `app.caem_consumer.f0.load_caem_f0_interface`),
  never against a count baked into this document. **Correction, found by
  an independent Codex review of an earlier draft:** this stop condition
  previously stated "15 of 31 F0 contracts" as a fact this document itself
  established. That count was real at the time it was checked (loaded via
  `app.caem_consumer.f0.load_caem_f0_interface` against a full local clone
  of the pinned carrier: `reserved=15, legacy_reference=11,
  implemented_at_f0=4, external_reference=1, total=31`), but the clone used
  to verify it was an ephemeral, session-local checkout outside this
  repository and its git history — not something a future reader of this
  document can reproduce from the repo alone, and not something this
  static document should assert as a persisted fact regardless. RI-B0 must
  request promotion via CAEM's own live issue tracker (this document does
  not assert specific issue numbers without live verification at the time
  of the check) or wait, never reimplement the contract locally;
- an artifact fails digest verification against its own manifest/registry
  entry (§1.1) — reject, don't quarantine-and-continue;
- a proof obligation's `(repository, subject_sha, run_id)` doesn't match the
  current subject being evaluated (§1.2) — mark stale, recompute, never
  merge;
- the DLP/PHI gate (#160) is unavailable when a persistence, transport, or
  proof-artifact-emission event is about to occur (§1.6) — fail-closed,
  block the event;
- two proof/counterexample results disagree for the same obligation — mark
  `conflicted` (CAEM's own six-state resolution), never `latest-wins`;
- a critical surface has no covering proof obligation at all — mark
  `unknown`, never silently promote to a positive result;
- an artifact, memory entry, or query would cross a repository isolation
  boundary (§1.12/§4) without an explicit, narrowly-scoped exception —
  refuse the cross-repo read/write;
- any component in §3 is asked to perform an action outside its "denied
  actions" column without the specific grant named in that row already
  being live and unconsumed.

RI-B1 implementation must additionally stop if:

- a migration or destructive database action is about to run without a
  grant scoped exactly to that action and target, per `CAEM_CORE.md`'s
  authority matrix (§1.15) — this repeats CAEM_CORE.md's existing rule
  verbatim; RI-B1 does not get an exception;
- backup/retention/encryption requirements (§4) have not been decided by
  whoever owns that decision for this repo — RI-B1 does not default to "no
  policy" as if that were a valid policy.

---

## 7. Adversarial plan

Each threat in §1 should eventually have a corresponding adversarial test,
in the same style already used for this repo's CAEM consumer loader (a real,
reproducible red-first repro before the fix, a green test after). Minimum
corpus once RI-B0/RI-B1 exist:

- an artifact whose declared digest doesn't match its bytes → rejected, not
  quarantined (§1.1);
- a proof result replayed against a HEAD that has since changed → marked
  stale (§1.2);
- a policy/profile sourced from a non-canonical branch → rejected (§1.3);
- path traversal / absolute path / symlinked ancestor directory / extra
  file in a bundle → `UNSAFE_ARTIFACT_PATH`-class rejection (§1.4) — this
  exact class of test already exists for the CAEM consumer loader
  (`tests/caem_consumer/test_f0_interface.py`) and is the template RI-B0
  should follow;
- a raw-content field (diff/prompt/response/secret/PHI) present in any
  artifact schema RI-B0 defines → schema validation failure, not a runtime
  warning (§4);
- an actor asserting an identity it can't back with a real, independently
  checkable identity → rejected (§1.7) — this repo's own round-3 Codex fix
  (`ad76f3b`, PR #173) is a directly analogous precedent to replicate;
- a disposition with no evidence/proof reference → schema rejection (§1.8);
- a fabricated proof/counterexample (asserted without the corresponding
  computation) → rejected at the point of independent verification (§1.9),
  once CAEM promotes those contracts from `reserved`;
- a component certifying its own output as "independently verified" → the
  verifier-identity check must distinguish producer from verifier by
  construction, not by convention (§1.10);
- duplicate/correlated observations counted as independent corroboration →
  deduplicated by canonical identity, not raw count (§1.11);
- a query that returns another repository's memory by omitting a repository
  filter → rejected/empty by construction, not merely undocumented (§1.12);
- an observation relabeled as a proof by dropping its provenance field →
  schema-level rejection, provenance is a required field, not optional
  metadata (§1.13);
- a merge/deploy/migration attempted on the strength of a proof or handoff
  alone, with no live grant → rejected by the authority layer (§1.14/§1.15) —
  this is exactly the discipline this session's own PR #173 merge already
  followed (5 Codex rounds, explicit grant revalidation before every
  protected action).

This corpus is a requirement RI-B0/RI-B1's own test suites must satisfy
before those components are considered complete — it is not implemented by
this document, which contains no code.

---

## 8. References to CAEM F0 contracts (no redefinition)

This document references, but does not redefine, CAEM-normative content —
content that is actually part of the pinned CAEM 3.0 F0 interface:

- `caem.contract-registry.v1` / `caem.interface-manifest.v1` /
  `caem.canonical-json.v1` — `implemented_at_f0`, pinned in this repo by
  `config/caem/caem-3.0-f0.pin.json` (#119.1, PR #173).
- `caem.contract-change-request.v1` — `implemented_at_f0`; the only channel
  by which RI-B0 may propose a normative change to CAEM (§1.15's F1
  distinction).
- The `reserved` contracts named throughout this document
  (`caem.assertion.v1`, `caem.proof-obligation.v1`,
  `caem.canonical-variable-catalog.v1`, `caem.evidence-projection.v1`,
  `caem.proof-certificate.v1`, `caem.counterexample-certificate.v1`, and
  others) are real contract IDs observed in the pinned F0 registry at the
  time this document was written (see §6's correction note on how that was
  verified, and its non-reproducibility caveat) — RI-B0 consumes these once
  CAEM promotes them; it does not implement its own version of any of them
  in the meantime.
- `docs/engineering/PROJECT_OVERLAY.md`'s trust architecture and readiness
  states (`ready, blocked_code, blocked_pipeline, manual_required, stale`) —
  the target shape for RI-B0's own future readiness output, not a shape this
  document invents independently.

### 8.1 References to this repository's own local operational policy (not CAEM-normative)

**Correction, found by an independent Codex review:** an earlier draft of
this section listed `docs/engineering/CAEM_CORE.md`'s authority matrix and
failure classification alongside the genuinely CAEM-normative content
above, under the same "References to CAEM F0 contracts" heading. That
conflates two different sources of authority. `CAEM_CORE.md`'s own header
(added by this repo's own PR #173) is explicit:
`body_provenance: historical_caem_2_1_projection`,
`not_a_CAEM_3_0_F0_generated_view: true`, `authority_effect=none` — its
body is a preserved historical CAEM 2.1 operational overlay, not F0
content and not CAEM-normative at all. What this document actually reuses
from `CAEM_CORE.md` (§1.15, §6) is **this repository's own already-agreed
local operating discipline for agent conduct** — the authority matrix and
failure-classification table that govern how work happens in
`mglpsw/aiops-orchestrator` specifically — not a claim about what CAEM 3.0
F0 itself specifies. RI-B0/RI-B1 inherit that local discipline because
they're built in this repository, under the same governance, not because
CAEM F0 requires it of them.

---

## 9. Fora de escopo

- a functional DLP/PHI detector (#160) — this document requires one exist
  before certain events are allowed to occur; it does not build one;
- the RI-B0 executor's implementation — this document constrains its
  design; it contains no executor code;
- auth, API, database, or migration implementation;
- deploy of any kind;
- secrets, real data, or penetration testing of any live system.

## 10. Critério de fechamento

This document is closed when: the threat model covers both CAEM proof
execution (§1, all 15 surfaces) and future persistence (§1.12, §1.15, §4's
retention/backup gaps explicitly flagged rather than silently decided); no
surface or component in §3 receives implicit authority (every "more than
this" case in §3 names the specific grant required); and #160, #165, and
#121 each have testable boundaries to build against (§6's stop conditions +
§7's adversarial corpus, keyed to the specific issues that will consume
them).
