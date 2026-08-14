# Release v0.21.0 — AgentReview v2 Offline and Shadow Adoption

**Status:** `RELEASE SNAPSHOT` — registro histórico desta release, preservado como publicado. Não descreve o estado atual do repositório; ver [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

Refs #89 (parent #80, roadmap #46). Prepared by #133 (this document); the
release itself — tag, GitHub Release, artifact publication — is a
protected action requiring its own `publish_release` grant, not authorized
by this document.

## 1. Release identity

- Release: `v0.21.0` — first pinnable, consumable release of AgentReview v2.
- Release candidate: `v0.21.0-rc.1`, a **prerelease**, published at the exact
  source SHA that results after PR-4 (CI release-gates job) merges.
- Final tag `v0.21.0` targets the **same source SHA as the RC**, provided no
  code changes after the RC. If any code changes after the RC, the final tag
  is **not** published at the RC's SHA — a new `v0.21.0-rc.2` is cut instead
  and every gate re-run, per the frozen master plan's own rule: never move or
  reuse an existing tag.
- Source SHA: filled in at `publish_release` grant time, not before.
- Baseline before this release: `v0.20.0` (release SHA
  `13695c73d1da9f16eba5c20e6478e7d51aefbb45`).
- Previous release and rollback ref: `v0.20.0`.
- Release tracking issue #89 stays open until the tag and GitHub Release are
  actually published under a granted `publish_release` action; this document
  does not close #89.

## 2. Declaration

```text
Repository/toolrepo release: v0.21.0
AgentReview v2: available opt-in/shadow
AgentReview v1: preserved
AgentEscala current pin: remains v0.20.0 until its separate adoption PR
CT102 deployed runtime: remains v0.20.0 unless separately authorized
```

Publishing `v0.21.0` does not itself deploy anything, does not touch CT102,
and does not change AgentEscala's own pin. Every consumer keeps its own
decision and its own PR.

## 3. What v0.21.0 includes

- The complete AgentReview v2 engine: run/manifest/payload assembly,
  verified binding, coverage, lifecycle, readiness, quality gate, CLIs, and
  dual-target conformance (#83–#86, #102–#110, #127–#132).
- CAEM 3.0 F0 consumer pin and legacy 2.1 quarantine (#119.1).
- The completed AgentReview v2 benchmark (#88): a provider-reviewable
  synthetic corpus (`evals/agent_review_v2/reviewable_corpus/`, 6
  `semantic_positive` + 4 `semantic_safe_counterexample` cases), a
  deterministic AIOps-side pipeline projection bound to real PR/HEAD
  identity, and real Lane 2 (Codex CLI local)/Lane 3 (Codex GitHub shadow)
  execution. Result: `reports/agent-review-v2-benchmark-summary.md`.
  Disposition: `allowed_role: shadow` only (see §5).
- This release preparation slice: compatibility matrix, rollback runbook,
  shadow rollout specification (this document's siblings).

## 4. What v0.21.0 explicitly does not do

- Does not remove, deprecate, or change the v1 consumer contract in any way.
- Does not promote v2 to default or required for any target.
- Does not write to AgentEscala, InterLeitos, or any other consumer
  repository.
- Does not touch CT102 or any production runtime.
- Does not integrate CAEM 3.0 proof-carrying execution (RI-B0) — that
  remains a separate, future adapter.
- Does not include #63 (provider-neutral external confirmation) or #64
  (Validation Evidence integration) — both are explicitly post-v2, tracked
  for a release of their own after the observation window (A6) produces
  real data to calibrate against.

## 5. Codex operational disposition (from the benchmark, #88)

```yaml
codex_operational_eligibility: eligible
allowed_role: shadow
advisory_eligibility: deferred_to_target_observation
required_check_eligible: false
readiness_authority: false
statistical_status: descriptive_provisional_baseline
promotion_authority: false
```

The benchmark's n=6 positive / n=4 counterexample corpus is a descriptive
engineering baseline, not a statistically powered study. It qualifies Codex
for **shadow** adoption only. Promotion to `advisory` requires real
shadow-adoption data from each target's own observation window (A6) and a
separate, per-target decision — never inferred from this benchmark alone.

## 6. Compatibility

See `docs/AGENT_REVIEW_V1_V2_COMPATIBILITY.md` for the full v1/v2 matrix.
Summary: v1 and v2 artifacts, envelopes, and gates are never mixed; a target
consumes exactly one version's full pipeline per run.

## 7. Installation

See `docs/AGENT_REVIEW_V2_INSTALLATION.md`. Clean-environment reproduction
with `pip install --require-hashes --no-deps -r requirements-agent-review.lock`
is a mandatory gate before the RC is published (§9 of the master plan),
executed against the RC's exact source SHA, not before.

## 8. Rollback

See `docs/AGENT_REVIEW_V2_ROLLBACK.md`. Rollback reference: `v0.20.0`.

## 9. Shadow adoption

See `docs/AGENT_REVIEW_V2_SHADOW_ROLLOUT.md`. AgentEscala and InterLeitos
adoption are separate executions, separate repositories, separate grants —
never bundled into this release PR or this release.

## 10. Ensaio checklist (executed at `publish_release` grant time)

- [ ] tag resolves to the declared source SHA
- [ ] clean-environment install (`--require-hashes`) succeeds at that SHA
- [ ] schema/checksum artifacts attached to the release match that SHA
- [ ] rollback to `v0.20.0` demonstrated
- [ ] no code changed between RC and final tag (or a new RC is cut)

## 11. Closure

This document does not close #89. #89 closes only when the tag, GitHub
Release, and artifact publication have actually happened under a granted
`publish_release` action, with rollback demonstrated.
