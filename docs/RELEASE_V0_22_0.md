# Release v0.22.0 — AgentReview v1 evidence taxonomy and v2 review-content extraction

Refs `AgentEscala#675` (v1 evidence taxonomy, #213/#214), `#199` (v2
distribution epic, slices #200-A/#200-B/#200-C/#201-A/#201-B1/#201-B2 and
their adversarial-audit follow-up). The release itself — tag, artifact
publication — is a protected action requiring its own `publish_release`
grant, not authorized by this document.

## 1. Release identity

- Release: `v0.22.0` — first release after `v0.21.0` to touch the v1 engine.
- Release candidate: `v0.22.0-rc.1`, a **prerelease**, published at the exact
  source SHA that results after this release-prep PR merges.
- Final tag `v0.22.0` targets the **same source SHA as the RC**, provided no
  code changes after the RC. If any code changes after the RC, the final tag
  is **not** published at the RC's SHA — a new `v0.22.0-rc.2` is cut instead,
  per `docs/RELEASE_V0_21_0.md`'s own rule (never move or reuse an existing
  tag), carried forward unchanged here.
- Source SHA: filled in at `publish_release` grant time, not before.
- Baseline before this release: `v0.21.0` (release SHA
  `273864eaa01dfb708a5a26d3756e16c6cd918a9f`).
- Previous release and rollback ref: `v0.21.0`.

## 2. Numbering rationale

`git tag` on this repository lists exactly 8 tags: `v0.18.0`, `v0.19.0-rc.1`,
`v0.19.0-rc.2`, `v0.19.0`, `v0.20.0-rc.1`, `v0.20.0`, `v0.21.0-rc.1`,
`v0.21.0`. Every **final, stable** tag (`v0.18.0` → `v0.19.0` → `v0.20.0` →
`v0.21.0`) is a MINOR bump; none is a patch release. The `-rc.N` tags are
excluded from this comparison deliberately, not by omission: each is a
prerelease of the *same* version number that follows it (e.g.
`v0.19.0-rc.1`/`v0.19.0-rc.2` both precede `v0.19.0`), so they are not
independent data points about the next number — they are part of how that
one number gets published, per `docs/RELEASE_V0_21_0.md`'s own RC→final
convention.

`docs/RELEASE_NOTES.md` also has a prose entry titled `v0.18.0-hotfix.1`
("Docker build project name alignment"). It is **not** a real tag — `git
tag` above has no `hotfix` entry — so it cannot be evidence of an actual
patch-release convention; it documents a build/deploy correction that was
never independently tagged.

No dedicated versioning-policy document in this repo prescribes patch-only
numbering for a fix-scoped change, and the actual tag history is 4-for-4
MINOR bumps with zero real patch tags. `v0.21.1` is therefore not the
semantically correct next number — `v0.22.0` is. This is also consistent
with what's actually shipping (§4): not only the `AgentEscala#675` v1 fix,
but five accumulated v2 feature slices (#206–#212) that never got their own
release — a feature-inclusive MINOR bump, not a fix-only patch, regardless
of how the historical-precedent question above is read.

## 3. Declaration

```text
Repository/toolrepo release: v0.22.0
AgentReview v1: hardened (evidence taxonomy — provenance, artifact
  requiredness, plan-status-vs-limitation) — engine change only
AgentReview v2: content extraction, DLP, budget and trusted-check contracts
  completed for this slice — still opt-in/shadow only, unchanged disposition
AgentEscala current pin: remains v0.20.0 until its own separate repin PR
  (Track 3, AgentEscala#675) — this release does not change any consumer's
  pin
CT102 deployed runtime: remains v0.20.0 unless separately authorized
```

Publishing `v0.22.0` does not itself deploy anything, does not touch CT102,
and does not change any consumer's own pin. Every consumer keeps its own
decision and its own PR.

## 4. What v0.22.0 includes

Everything merged to `master` between `v0.21.0` and this release's source
SHA (`git log v0.21.0..<source SHA>`):

- **AgentReview v1 evidence taxonomy** (`AgentEscala#675`, upstream #214):
  provenance separation (`model_reported_limitations` kept out of the
  deterministic `limitations` namespace), `required_artifact_missing:` vs.
  `optional_artifact_missing:` in `pr_brief`, and `semantic_chunker`'s plan
  status deriving from coverage facts instead of limitation presence. See
  `CHANGELOG.md` for the full entry, including the model-authored
  `coverage_missing` regression found and closed during the corrective audit.
- **AgentReview v2 review content** (#200-A/#200-B/#200-C, distribution epic
  #199): `ReviewContentV2` sidecar, transport-echo integrity anchor, real
  hunk-content extraction and redaction, declarative DLP enforcement, Agent
  Router transport wiring, and the adversarial-audit follow-up that closed 3
  further issues (profile-hash-bound budgets, `coverage_required`-only
  overflow blocking, real-content hardening test assertions).
- **AgentReview v2 trusted-check contracts and executor** (#201-A, #201-B1,
  #201-B2, distribution epic #199): host-owned
  `TrustedCheckPlanV2`/`TrustedCheckResultV2` contracts, the offline
  simulator, and the real isolated executor (`execute_trusted_check_plan_v2`)
  that runs a plan check in an isolated subprocess and derives its verdict
  exclusively from the kernel-observed exit code — proven against this
  session's own dev sandbox, explicitly not the project's pinned CT104
  runner, which stays `blocked_external: ct104_unavailable` and is never
  substituted by CT102.

## 5. What v0.22.0 explicitly does not do

- Does not remove, deprecate, or change the v1 consumer contract beyond the
  evidence-taxonomy fixes listed above (`schema_version` stays `1`).
- Does not promote v2 to default or required for any target.
- Does not write to AgentEscala, InterLeitos, or any other consumer
  repository, and does not change any consumer's pin.
- Does not touch CT102 or any production runtime.
- Does not include the required-checks integration into a real readiness
  computation (`#201-B3`/`#201-C`) or required-check reliability (#201,
  upstream target of AgentEscala#750) — those remain open.

## 6. Compatibility

`ChunkResults`/`FinalReview` gain an additive `model_reported_limitations`
field; existing consumers reading only `limitations` are unaffected.
`pr_brief`'s artifact-missing reason codes are more specific
(`required_artifact_missing:`/`optional_artifact_missing:` instead of a flat
`artifact_missing:`) — a consumer that pattern-matches the old flat code
verbatim needs to widen its match, same as any other reason-code addition.
No schema-version bump; no database migration; no provider, route, or
runtime API behavior change.

## 7. Installation

Same procedure as `v0.21.0` — see `docs/AGENT_REVIEW_V2_INSTALLATION.md`.
Clean-environment reproduction with
`pip install --require-hashes --no-deps -r requirements-agent-review.lock`
is a mandatory gate before the RC is published, executed against the RC's
exact source SHA, not before.

## 8. Rollback

Toolrepo consumption-pin rollback only (see
`docs/AGENT_REVIEW_V2_ROLLBACK.md` — distinct from the CT102 runtime
rollback in `docs/ROLLBACK.md`, which this release does not touch).
Rollback reference: `v0.21.0`. No consumer currently pins `v0.22.0`, so
there is no live target to roll back at release time; the ensaio checklist
below instead confirms `v0.21.0` remains independently installable and
unaffected by this release.

## 9. Shadow adoption

Unchanged from `v0.21.0` — see `docs/AGENT_REVIEW_V2_SHADOW_ROLLOUT.md`.
This release does not change v2's `shadow`-only disposition.

## 10. Ensaio checklist (executed at `publish_release` grant time)

- [ ] tag resolves to the declared source SHA
- [ ] clean-environment install (`--require-hashes`) succeeds at that SHA
- [ ] schema/checksum artifacts at that SHA are internally consistent
      (`bash scripts/ci_validate.sh`)
- [ ] `v0.21.0` (rollback ref) remains independently installable, unaffected
      by this release
- [ ] no code changed between RC and final tag (or a new RC is cut)

## 11. Closure

This document does not close any issue. `AgentEscala#675`'s remaining
criteria close only after this release is published, the AgentEscala v1 pin
is repinned to this release's SHA (Track 3), and a real post-repin canary is
observed — none of which this document performs.
