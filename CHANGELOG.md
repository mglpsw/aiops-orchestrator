# Changelog

## Unreleased

### Added

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
