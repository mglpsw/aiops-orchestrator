# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Status: PROPOSED_AOCM_REPLAN
# AgentReview v2 — Activation Gate Specification

**Repository:** `mglpsw/aiops-orchestrator`  
**Method:** AOCM-MPACK 0.1.0-preview.1  
**Campaign:** `agent-review-v2-replan`  
**Date:** 2026-09-23  
**Roadmap Authority:** [Issue #46](https://github.com/mglpsw/aiops-orchestrator/issues/46)  

---

## 1. Activation Lifecycle Phases

AgentReview v2 progresses through four strictly gated activation stages:

```text
SHADOW / OPT-IN (Current)
        ↓  [Gate PRE-G5 Passed: C1-C8 qualified]
OPERATIONAL COMPOSITION (Gate G5 Passed: C11 qualified)
        ↓  [Gate G6 Passed: C9, C10, C12, C13 qualified]
ADVISORY / CANARY DUAL-RUN (Target pack validated on real PRs)
        ↓  [Gate G7 Passed: C14 release frozen, human authorization]
DEFAULT REVIEW ENGINE (v2 authoritative, v1 preserved as fallback)
```

---

## 2. Gate PRE-G5 Specification

**Objective:** Validate that all primitive components (C1 through C8) function with full adversarial isolation before attempting integrated end-to-end execution.

**Gate Criteria (All Must Be Satisfied):**
1. **C2 Storage Boundary Closed (#331-B):** Host authorized-storage verifier rejects all unauthorized repo roots and worktree commondir paths.
2. **C3 Tree Materialization Closed (#304):** Commit extraction into isolated sandbox byte-matches git tree OIDs with zero path escapes.
3. **C4 Execution Provenance Closed (#301, #333):** Subprocess test execution runs in clean, scrubbed environment with verified bytecode.
4. **C5 Semantic Coverage Closed (#298):** Authoritative diff chunker accounts for 100% of hunks without silent drops.
5. **C6 Outbound Sanitization Closed (#314):** DLP and path sanitization guarantee zero secret leaks or absolute path leaks.
6. **C7-C8 Readiness Closed (#320, #323):** Router receipt verification and deterministic pure functional readiness gate pass property tests.

---

## 3. Gate G5 Specification (Operational Composition)

**Objective:** Qualify the end-to-end pipeline composition (`tests/agent_review_v2/test_composition_g5.py`).

**Gate Criteria:**
1. Zero mock substitution between internal component boundaries.
2. Complete dataflow from raw Git repository input to consumable `ReviewReadinessV2` output.
3. Rejection of replayed, forged, or unauthenticated intermediate tokens.
4. Deterministic byte-reproducibility across repeated runs on identical subjects.

---

## 4. Hard Stop Conditions

Any running slice or review must immediately trigger `STOP / REDESIGN` upon detecting:

```text
SUBJECT_DRIFT:
  Review execution observed on commit OID different from PR head.

UNKNOWN_TRUTH_MAKER:
  A claim or gate condition depends on an unverified or informal assertion.

DUPLICATE_SEMANTIC_AUTHORITY:
  A second document or subsystem attempts to order roadmap priorities outside #46.

CLAIM_OUTRUNS_MECHANISM:
  A test or docstring claims a security invariant that the code mechanism does not enforce.

PROOF_SURFACE_BECOMES_SECOND_PRODUCT:
  Test harness or benchmark tooling grows unchecked complexity that obscures the product mechanism.

SUCCESSOR_SILENTLY_WEAKENS_OBLIGATION:
  A newer PR relaxes a security boundary or validation rule established by an earlier slice.

LIVE_CONSUMER_USES_SUPERSEDED_AUTHORITY:
  Active runtime or session config reads a deprecated or historical snapshot as live truth.

PRE_G5_DEFERRED_DEBT_BECOMES_REACHABLE:
  A shortcut taken during component development causes an unhandled exception during G5 composition.
```
