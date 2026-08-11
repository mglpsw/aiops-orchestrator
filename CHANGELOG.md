# Changelog

## Unreleased

### Added

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
    tuple (`check_name`, `workflow_path`, `workflow_ref`, `job_name`,
    `verifier_identity`), read exclusively from the trusted base/default
    checkout. Both `policy_source_bytes_digest` and
    `policy_source_semantic_digest` travel into the sidecar.
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
    `manual_review_required` on a fully covered review. `ChunkResults`,
    `FinalReview` and `ReviewQualityGate` gain an additive
    `model_reported_limitations`; `schema_version` stays `1` and consumers
    reading only `limitations` are unaffected. The rendered markdown gives
    each namespace its own heading and caps them independently, so model
    prose can no longer evict deterministic codes from the comment.
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
