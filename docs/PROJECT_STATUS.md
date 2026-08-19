# AIOps Orchestrator — Project Status

**Status:** `CANONICAL | CURRENT` · corte 2026-08-14

Autoridade factual curta sobre o estado do repositório. O roadmap canônico é a
issue [#46](https://github.com/mglpsw/aiops-orchestrator/issues/46) — este documento
não o duplica.

## Canonical checkpoint

The current final release is `v0.22.0`, published on 12 August 2026 from:

```text
2ce1f45768b8779cb48ef8a302d4ed796349f0e5
```

It is final and immutable. The previous final release and rollback ref is
`v0.21.0` at `273864eaa01dfb708a5a26d3756e16c6cd918a9f`.

### `master` is ahead of the published release

`master` carries work that is **not in any published release**. This document
does not pin that fact to a "live master" SHA — doing so goes stale the
instant any further commit lands, which is exactly the defect this section
previously had (it named `abe034ad`, itself since superseded). Instead, three
identities are kept separate, mirroring
[`docs/engineering/CURRENT_CHECKPOINT.md`](engineering/CURRENT_CHECKPOINT.md)'s
own model:

```yaml
release_baseline:
  version: v0.22.0
  sha: 2ce1f45768b8779cb48ef8a302d4ed796349f0e5
implementation_anchor:
  sha: d454e8f2d272b9edb011513b4a8f5d4e89ece4c2
  meaning: >-
    last implementation state this reconciliation describes; the
    documentation-only commit that introduces this revision is itself
    NOT part of the count below.
live_master:
  source_of_truth: GitHub
  hardcoded_sha: false
```

The interval `v0.22.0..<implementation_anchor>` contains **18 commits**
(`git rev-list --count v0.22.0..d454e8f2d272b9edb011513b4a8f5d4e89ece4c2`).
Material evolution through that anchor, summarized (not a second changelog —
see `CHANGELOG.md` for the full record):

- **AgentReview v2 core and hardening** — trusted-check hardening, the CI
  provenance bridge (which also extracted shared JSON/digest primitives into
  `app/common/strict_json.py`, a CAEM/shared surface touch, not only v2),
  required-check readiness wiring, verified-check ordering, plus `#230`'s
  post-merge identity-hardening (H1-A) and `#233`'s trusted-authority runtime
  boundary fix (H1-C);
- **target-pack `#203`** — the first slice (`init`/`doctor`) with its
  init-plan binding, `#243`'s installed-pack identity contracts (`#203-C1`,
  the contract predecessor), and `#244`'s bounded offline `validate`
  (`#203-C2`, now canonical — see the Target Pack v2 section below);
- **`#237`/`#239`** — target-profile YAML ambiguity derived from the parser
  itself, then institutionalized into a reusable Structural Change Preflight
  and executable regression corpus;
- **AgentReview v1 stabilization** — `#225` (chunk planning by real hunk cost)
  and `#231`/H1-B (planner soundness: canonical path identity, brief-budget
  projection, dedupe parity, binary-vs-textual hunks, fallback completeness);
- **documentation reconciliation** (`#229`, `#234`), `.md`-only.

A consumer pinned at `v0.22.0` does **not** have the `#225` or H1-B v1 fixes.
Publishing a new release, repinning any consumer, and running a fresh canary are
separate, still pending actions, each under its own authorization.

The post-merge review debt tracked in `#205` (C1-C10) is now **entirely
`fixed_and_verified`** across H1-A, H1-B and H1-C. That does **not** make the
`POST_MERGE_REVIEW_DEBT_GATE` satisfied: `#232` (P2, AgentReview v1 — a
non-`must_review` file with no textual hunk still counts as covered) remains
`OPEN` and deliberately deferred. It does not block `#203` or `#204`, but it
requires an explicit disposition before the `#205` release candidate.

## Product surfaces

The repository contains two independent surfaces. Their environment and
authority boundaries must remain explicit.

| Surface | Environment | Purpose | Network/provider behavior |
| --- | --- | --- | --- |
| AIOps runtime | CT102 prod/runtime | diagnose, plan, dry-run, approve and execute fixed read-only actions | uses only approved runtime integrations |
| AgentReview engine | CT104 dev/toolrepo | build and validate deterministic review artifacts | offline CLIs; no direct provider or GitHub write calls |

AgentReview must never run on CT102. CT102 must never be used as a staging
environment for AgentReview.

## Runtime version vs. toolrepo release

These are independent and must not be conflated:

| Identity | Value | Meaning |
|---|---|---|
| Toolrepo release tag | `v0.22.0` | what a consumer pins for AgentReview |
| Last recorded AIOps runtime deployment | `0.20.0` (`app/__init__.py`) | last version this checkout's own source tree reports having been validated at deploy time |

`__version__` tracks the **runtime** and was deliberately left unchanged by the
`v0.21.0` and `v0.22.0` toolrepo releases. Publishing a toolrepo release does not
deploy anything and does not change the runtime version. `app/__init__.py`
proves only what the source tree declares, never what CT102 currently reports —
CT102 may have been deployed, rolled back, or redeployed independently of this
checkout since that value was last set. This table is not a live runtime
observation; see the paragraph below.

The last recorded CT102 runtime validation was performed for the `0.20.0` deploy
(health, readiness, metrics, database/providers/action catalog ready, no critical
errors, previous image retained for rollback). That deploy introduced no database
migration, route change, provider change, action-catalog change or runtime API
behavior change. **That is a historical record, not a current health assertion** —
runtime health must be observed live, never read from this document.

## Runtime architecture

The canonical read-only flow remains:

```text
diagnose -> plan -> dry-run -> approval -> run -> run history -> audit
```

Key properties:

- authenticated sensitive endpoints;
- structural action allowlist validated at startup;
- human approval before a read-only run;
- fixed internal runner functions with no request-provided command or argv;
- bounded output, timeout, sanitized environment and redaction;
- persistent JSONL audit, approval and run-history stores;
- no free shell, SSH, `docker exec`, free PromQL or automatic deploy.

The official runner is `app/agent_router/services/action_runner.py`. Legacy
executors under `app/adapters/` are compatibility code and are not part of the
official execution path.

## AgentReview v1 — released, maintenance/freeze

The v1 line is published and **frozen for features**: only critical bug, security,
regression and migration-compatibility changes are accepted. Its published baseline
is `v0.22.0`. The `#225` chunk-planning fix is merged to `master` and awaiting a
release.

The offline deterministic pipeline is:

```text
aiops-intake.json + redaction-report.json
-> semantic-chunk-plan.json
-> pr-brief.json + chunk-payload-manifest.json + chunk-payloads/
-> chunk-results.json
-> final-review.json + final-review.md
-> review-quality-gate.json
-> review-telemetry.json
-> optional false-positive-signatures.json
-> optional suggested-contract-updates.yaml
```

### Canonical authority

`review-quality-gate.json` is the canonical post-synthesis decision authority.
`final-review.json` is a synthesis artifact and must not be used as a fallback
authority when the gate is missing, malformed, incompatible, unknown or
contradictory.

Consumers must validate the gate schema, source, version, enumerations and
allowed field combinations before publication. Invalid gates produce a
deterministic fail-closed, non-conclusive result with manual review required.

### Deterministic context contract

- `pr-brief.json` is the sanitized deterministic PR summary;
- `chunk-payload-manifest.json` records the bounded payload set and hashes;
- each `chunk-payloads/<chunk_id>.json` contains isolated context for one
  semantic chunk and a complete structured response contract;
- truncation and coverage impact are explicit;
- response-compatible `chunk_id` validation is shared and fail-closed;
- path-bearing global validation evidence reaches every chunk while retaining
  sanitized provenance;
- non-global path-scoped evidence remains restricted to matching chunks.

### Telemetry and learning

`review-telemetry.json` observes the already-produced final review and gate. It
does not alter verdicts. False-positive signatures are deterministic, and
`suggested-contract-updates.yaml` is always human-reviewable, `manual_only` and
`applied: false`.

## Target-repository consumption

AgentEscala remains responsible for target-repository orchestration, optional
approved Agent Router calls and GitHub publication. It must consume this
toolrepo from an immutable canonical lowercase 40-character commit SHA.

The wrapper must not:

- resolve an operational branch, tag, short SHA or floating default branch;
- reimplement parsing, synthesis, quality-gate or telemetry logic;
- call `/v1/chat/ingest`;
- call a provider directly;
- treat `final-review.json` as substitute authority;
- apply contract suggestions automatically.

## Safety boundaries

The AgentReview CLIs require:

```text
AIOPS_ENVIRONMENT=dev
AIOPS_NODE_ROLE=toolrepo
AIOPS_REPO_MODE=agent_review_tooling
AIOPS_PRODUCTION_RUNTIME=false
```

They fail closed in production/runtime mode and write outputs outside the
target repository. Published artifacts must be allowlisted, sanitized and
scanned for secrets and local absolute paths.

## Validation baseline

The release baseline passed:

- full offline Python test suite;
- focused AgentReview unit, CLI and E2E contracts;
- deterministic byte-output checks;
- target/source fixture immutability checks;
- production-boundary fail-closed checks;
- repository CI validation.

Canonical local commands:

```bash
python3 -m pytest tests -q
bash scripts/ci_validate.sh
git diff --check
```

Runtime validation remains a separate CT102-only, explicitly authorized
operation. Offline documentation or AgentReview work does not authorize it.

## Explicitly absent or out of scope

- free shell or request-provided commands;
- SSH or `docker exec` in the official runner;
- automatic deploy, remediation, approval or merge;
- AgentReview on CT102;
- direct provider calls from the AIOps AgentReview CLIs;
- real second-opinion implementation;
- automatic contract suggestion application;
- using telemetry score as a merge decision.

## AgentReview v2 — successor in development

The v2 line is the successor and receives all new engineering. It is **not GA, not
default, and not a required check** in any target repository. Adoption today is
`shadow`/opt-in, pinned independently of v1.

Already shipped in a published release, not merely on `master`:

- **`v0.21.0`**: the complete v2 engine foundation — run/manifest/payload
  assembly, verified binding, coverage, lifecycle, readiness, quality gate,
  CLIs, byte-reproducible JSON Schemas, dual-target conformance;
- **`v0.22.0`**: real hunk-content extraction, redaction, declarative DLP
  enforcement, and the trusted-check contracts, offline simulator, and real
  isolated executor.

Delivered on `master`, not yet in a published release:

- authoritative CI provenance bridge and required-check readiness wiring;
- `agentreview-v2-target-pack` (`init`/`doctor`, operation-plan binding, and
  the post-merge identity-hardening closed in `#230`);
- the trusted-authority runtime boundary fix closed in `#233` (H1-C).

The complete v1 pipeline and quality gate remain operational and authoritative.
Migration must select v2 explicitly, preserve a documented v1 compatibility window,
and never silently mix contract versions. "v2 is the successor" does **not** mean v1
is removed now. See [AgentReview v2 contracts](AGENT_REVIEW_V2_CONTRACTS.md).

### Core prerequisites for `#203` are satisfied; the issues stay open

`#200`, `#201` and `#202` remain formally `OPEN`, but for **target-adoption**
reasons, not engine gaps. Each carries a published per-criterion reconciliation
against the live code:

| Issue | Core state | Formal closure waits on |
|---|---|---|
| `#200` real review content | `CORE_SYNTHETIC: COMPLETE` | first real AgentEscala semantic canary (`AgentEscala#759`) |
| `#201` trusted checks | `CORE: COMPLETE` | real target adoption (`AgentEscala#750`) |
| `#202` path codec | `CORE: COMPLETE` | consumer repin/migration to the upstream codec (`AgentEscala#752`) |

None of the three blocks `#203`. Their checkboxes are not treated as a second
state machine — the per-criterion classification in each issue's reconciliation
comment is the source of truth.

### Target Pack v2

`IMPLEMENTED`: `init`, `doctor`, `validate` (target-only, offline,
read-only, local-coherence — not independent proof of upstream pack
provenance), operation-plan binding, and the H1-A identity hardening.
`NOT YET IMPLEMENTED`: `conformance`, `install-workflows`, `upgrade`,
`rollback`.

This pack version's `max_supported_rollout_mode` is `off` — `shadow_minimal`
and `shadow_full` are interface-level options that are refused before preview
or apply. `#203` is the next active implementation frontier.
See [target pack](AGENT_REVIEW_V2_TARGET_PACK.md).

## Release work still pending

- decide the version number for the next v1 release (not decided; not authorized
  by any document in this repository);
- publish that release;
- repin the consumer and observe a fresh canary;
- reconcile the v1 freeze/GA track (`#221`).

## Explicitly not started

- v2 release/tag and v2 GA declaration;
- unified installer (v1-compat + v2 + dual-shadow);
- promotion of v2 to default or required in any target;
- removal or deprecation timeline for v1.

## Canonical references

- [Architecture](ARCHITECTURE.md)
- [Project manual](AIOPS_PROJECT_MANUAL.md)
- [AgentReview engine](AGENT_REVIEW_ENGINE.md)
- [AgentReview E2E pipeline](AGENT_REVIEW_E2E_PIPELINE.md)
- [AgentReview quality gate](AGENT_REVIEW_QUALITY_GATE.md)
- [AgentEscala target-repository contract](AGENTESCALA_TARGET_REPO_CONTRACT.md)
- [AgentReview v2 contracts](AGENT_REVIEW_V2_CONTRACTS.md)
- [AgentReview v2 target pack](AGENT_REVIEW_V2_TARGET_PACK.md)
- [Release notes](RELEASE_NOTES.md) — historical snapshots
- [Environment boundaries](ENVIRONMENT_BOUNDARIES.md)
- [Testing](TESTING.md)
- Roadmap: [issue #46](https://github.com/mglpsw/aiops-orchestrator/issues/46)
