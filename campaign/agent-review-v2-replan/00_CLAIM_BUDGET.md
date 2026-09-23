# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Status: PROPOSED_AOCM_REPLAN
# AgentReview v2 — Terminal Claim Budget (C0–C14)

**Repository:** `mglpsw/aiops-orchestrator`  
**Method:** AOCM-MPACK 0.1.0-preview.1 (Local Engineering Method)  
**Campaign:** `agent-review-v2-replan`  
**Date:** 2026-09-23  
**Status:** `PROPOSED_AOCM_REPLAN` (pending human adjudication)  
**Single Roadmap Authority:** [Issue #46](https://github.com/mglpsw/aiops-orchestrator/issues/46)  

---

## 1. Governance and Closure Boundary

This claim budget defines the finite, necessary, and sufficient set of terminal propositions that AgentReview v2 must truthfully claim at its first pinable release (`v2.0.0-rc.1` / release candidate).

A claim is terminal if and only if:
1. It expresses an externally observable capability or invariant required for operational correctness.
2. It is grounded in named obligations with deterministic truth-makers.
3. Its failure falsifies release readiness.

Issues and PRs are not requirements; they are carriers of obligations. Work on AgentReview v2 proceeds exclusively by discharging named obligations derived from this budget.

---

## 2. Terminal Claim Set (C0–C14)

### C0 — Engine Ownership
- **Proposition:** The generic AgentReview engine is owned exclusively by `mglpsw/aiops-orchestrator` (toolrepo). Target repositories (`AgentEscala`, `InterLeitos`) specialize configuration, policy, rules, and domain prompts without maintaining or owning engine forks.
- **Truth Maker:** Single unified codebase under `app/agent_review/` and target-pack generation under `app/agent_review/packaging/`. Zero repository-name branched logic in core engine modules.
- **Carrier / Issues:** #199, #203, #204.
- **Closure Predicate:** Multi-target test harness validates that identical engine byte-distributions execute both AgentEscala and InterLeitos policies without engine modifications.

### C1 — Exact Review Subject
- **Proposition:** Every review result, gate evaluation, and generated artifact is cryptographically and structurally bound to the exact repository identity, pull request / change request number, base commit OID, head commit OID, authoritative diff hash, toolrepo commit OID, policy profile digest, and run ID actually consumed.
- **Truth Maker:** `ReviewSubjectIdentityV2` schema and provenance manifest generated in `app/agent_review/models_v2.py` and validated by `ReviewReadinessV2`.
- **Carrier / Issues:** #46, #298, #304.
- **Closure Predicate:** Any tampering with or substitution of repository, base OID, head OID, or toolrepo OID causes immediate validation rejection.

### C2 — Authorized Git Acquisition
- **Proposition:** Git objects, trees, commits, and blobs used as review authority originate exclusively from storage locations explicitly authorized by the host/caller trust model.
- **Invariant:** `SafePath != AuthorizedStorage`. A path being syntactically safe (no `..`, no traversal) does not establish that its underlying `.git` directory is an authorized storage boundary.
- **Truth Maker:** Host-authorized storage boundary verifier in `app/agent_review/trusted_object_authority_v2.py` and `app/agent_review/external_path_ingress_v2.py`. Rejects external/untrusted repos, spoofed gitdirs, and unauthorized worktrees.
- **Carrier / Issues:** #331-B (successor to #331 / #318 / #312).
- **Closure Predicate:** Host trust boundary rejects repos residing outside authorized root paths or referencing unauthorized commondir/gitdir pointers.

### C3 — Canonical Materialization
- **Proposition:** An admitted commit is materialized into an isolated working subject faithful to the declared Git tree representation without filesystem path reinterpretation changing identity.
- **Truth Maker:** Materialization primitives in `app/agent_review/materialize_v2.py` (and benchmark harness `scripts/materialize-benchmark-case.py`), verifying every extracted blob against Git tree OIDs.
- **Carrier / Issues:** #304.
- **Closure Predicate:** Materialized disk files exactly match Git tree OIDs with zero path aliasing, symlink-induced path escaping, or case-folding identity corruption.

### C4 — Execution Provenance
- **Proposition:** Application code and test binaries executed by the review process derive exclusively from the authenticated subject, after authentication, under a fresh-process execution binding.
- **Invariant:** `CurrentDiskMatchesCommit != ExecutedBytesWereAuthenticated`. Observing that current disk matches a commit does not prove that bytes currently being executed by Python/subprocesses were authenticated.
- **Truth Maker:** Isolated runner in `app/agent_review/trusted_checks_v2.py`, hostile-read boundary (#321), and trust-anchor type gate (#319 / #322).
- **Carrier / Issues:** #301, #319, #321, #322, #333.
- **Closure Predicate:** Check execution runs in a sandboxed, environment-scrubbed subprocess where executable code is verified prior to spawning, with hostile import paths blocked.

### C5 — Semantic Coverage
- **Proposition:** Every material `must_review` obligation defined by policy is either covered by authoritative fragment identity in model input/output or explicitly marked as not covered / blocked.
- **Invariant:** A change fragment cannot be silently dropped due to chunking, token budgets, or parser truncation without explicit accounting in readiness.
- **Truth Maker:** Chunking and fragment tracking engine in `app/agent_review/diff_authority_v2.py`, `manifest_v2.py`, and semantic coverage matrix in `ReviewReadinessV2`.
- **Carrier / Issues:** #298.
- **Closure Predicate:** Total changed hunks in authoritative diff equal the sum of reviewed fragments plus explicitly recorded omitted/truncated fragments; unaccounted fragments fail closed.

### C6 — Safe Outbound Representation
- **Proposition:** No raw source code, internal host filesystem path, or unvetted literal outside the declared outbound schema contract reaches external semantic transport (Agent Router / LLM providers).
- **Truth Maker:** Outbound contract sanitizer and DLP redactor in `app/agent_review/sanitization_v2.py` and `app/agent_review/dlp_v2.py`.
- **Carrier / Issues:** #314.
- **Closure Predicate:** Data Loss Prevention (DLP) and path sanitizer guarantee zero leaking of secrets, host root paths, or out-of-scope files; unadmitted payload structures fail closed.

### C7 — Router Receipt Binding
- **Proposition:** A response from the model/router is accepted if and only if it cryptographically binds to the exact request ID, run token, payload digest, reviewed fragment set, and subject identity that produced the invocation.
- **Truth Maker:** `RouterReceiptV2` validation in `app/agent_review/router_receipt_v2.py`.
- **Carrier / Issues:** #46, #320.
- **Closure Predicate:** Mismatched receipt nonces, altered payload hashes, or unassociated fragment citations cause immediate receipt rejection.

### C8 — Deterministic Readiness
- **Proposition:** Model output is non-authoritative observation. A deterministic, contract-bound gate owns consumable readiness (`ReviewReadinessV2`).
- **Truth Maker:** Pure functional readiness evaluation in `app/agent_review/readiness_v2.py` verifying structured schema conformance, critical issue thresholds, required check statuses, and policy compliance.
- **Carrier / Issues:** #46, #320, #323.
- **Closure Predicate:** Readiness calculation is 100% reproducible from committed inputs and receipts without stochastic model influence.

### C9 — Trusted Required Checks
- **Proposition:** Required-check evidence consumed for review readiness is produced by host-controlled execution and cannot be fabricated or spoofed by the pull request under review.
- **Truth Maker:** Host execution harness and signed attestation in `app/agent_review/trusted_checks_v2.py`.
- **Carrier / Issues:** #201.
- **Closure Predicate:** Check outputs from repo-controlled code require host runner attestation; unverified check results fail closed.

### C10 — Canonical Path Identity
- **Proposition:** Git representation, API payloads, raw unified diffs, patch manifests, and publisher comments preserve one unambiguous, canonical path identity across POSIX, Windows, Unicode (NFC), and git C-quoted filenames.
- **Truth Maker:** Canonical path parser and normalizer in `app/agent_review/path_identity_v2.py`.
- **Carrier / Issues:** #202.
- **Closure Predicate:** Filenames with non-ASCII characters, octal escape sequences, or mixed separators resolve to identical path identity across all pipeline components.

### C11 — Operational Composition
- **Proposition:** All qualified v2 pipeline primitives (acquisition -> materialization -> diff authority -> checks -> router -> readiness) compose end-to-end without subject, authority, or evidence substitution between boundaries.
- **Truth Maker:** Gate G5 operational composition integration test suite in `tests/agent_review_v2/test_composition_g5.py`.
- **Carrier / Issues:** G5 (#326, #332).
- **Closure Predicate:** End-to-end test executes full lifecycle from raw git repo to consumable readiness report; zero mock leakage across module boundaries.

### C12 — Portable Distribution
- **Proposition:** The target pack installs, validates, upgrades, and rolls back the same AgentReview engine without repo-name branches or copied engine forks.
- **Truth Maker:** Packaging generator and verification tooling in `app/agent_review/packaging/` and `.aocm/` distribution profile.
- **Carrier / Issues:** #203.
- **Closure Predicate:** Pack installation on clean test target passes self-test verification; upgrade and rollback preserve profile state without data corruption.

### C13 — Dual-Target Conformance
- **Proposition:** The identical AgentReview v2 engine and target pack operate against both AgentEscala and InterLeitos repositories, with divergence strictly confined to target-owned configuration, rule profiles, and DLP exclusions.
- **Truth Maker:** Dual-target benchmark and conformance test suite in `evals/agent_review_v2/test_dual_target_conformance.py`.
- **Carrier / Issues:** #204.
- **Closure Predicate:** Both target configurations validate against their respective benchmark corpora; zero target-specific monkey-patching in engine code.

### C14 — Pinable Release
- **Proposition:** A reproducible, immutable, rollback-capable AgentReview v2 release candidate exists, with all qualification evidence cryptographically bound to exact source trees and build artifacts.
- **Truth Maker:** Release manifest, SHA-256 asset checksums, CAEM-compatible pin carrier, and frozen benchmark report.
- **Carrier / Issues:** #205.
- **Closure Predicate:** Fresh clone from release tag reproduces byte-identical artifacts and passes all release gates deterministically.

---

## 3. Claim Cardinality and Completeness

- Total Claims: 15 (C0 through C14).
- Overlap: Zero. Each claim covers an orthogonal architectural or verification boundary.
- Completeness: Satisfies all requirements of the v2 closure contract under AOCM-MPACK.
