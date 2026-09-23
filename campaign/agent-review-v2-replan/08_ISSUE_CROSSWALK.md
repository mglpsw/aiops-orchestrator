# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Status: PROPOSED_AOCM_REPLAN
# AgentReview v2 — Issue to Claim/Obligation Crosswalk

**Repository:** `mglpsw/aiops-orchestrator`  
**Method:** AOCM-MPACK 0.1.0-preview.1  
**Campaign:** `agent-review-v2-replan`  
**Date:** 2026-09-23  
**Roadmap Authority:** [Issue #46](https://github.com/mglpsw/aiops-orchestrator/issues/46)  

---

## 1. Issue to Obligation Mapping Table

Issues on GitHub are communication and tracking carriers. They do not own requirements; they carry obligations supporting terminal claims.

| GitHub Issue | Title / Role | Terminal Claim | Obligation ID | Current State | Critical Path Position |
|:---|:---|:---:|:---:|:---:|:---:|
| **#331-B** | Authorized external Git storage transitions | **C2** | `OBL-C2-01` | **READY_TO_IMPLEMENT** | **Slice 1 (Root unclosed)** |
| **#304** | Canonical tree materialization & blob verification | **C3** | `OBL-C3-01` | OPEN | Slice 2 (Blocked by #331-B) |
| **#333** | Proposition definition for execution provenance | **C4** | `OBL-C4-01` | OPEN | Slice 3 Precondition |
| **#321** | Hostile-read boundary (special files) | **C4** | `OBL-C4-02` | MERGED (PR #312) | Closed in master |
| **#319** | Trust-anchor provenance gate | **C4** | `OBL-C4-02` | MERGED (PR #322) | Closed in master |
| **#301** | Execution provenance & env scrubbing | **C4** | `OBL-C4-01` | OPEN | Slice 3 (Blocked by #304) |
| **#298** | Semantic coverage & diff hunk accounting | **C5** | `OBL-C5-01`, `OBL-C1-01` | OPEN | Slice 4 (Blocked by #301) |
| **#314** | Outbound DLP & safe path sanitization | **C6** | `OBL-C6-01` | OPEN | Slice 5 (Blocked by #298) |
| **#320** | Router receipt nonce & payload binding | **C7** | `OBL-C7-01` | OPEN | Slice 6 (Blocked by #314) |
| **#323** | Deterministic pure functional readiness gate | **C8** | `OBL-C8-01` | OPEN | Slice 6 (Blocked by #314) |
| **#201** | Trusted required checks (host runner) | **C9** | `OBL-C9-01` | OPEN | Independent Lane (Parallel) |
| **#202** | Canonical path identity (Unicode/C-quote) | **C10** | `OBL-C10-01` | OPEN | Independent Lane (Parallel) |
| **#326, #332** | Gate G5 operational composition integration | **C11** | `OBL-C11-01` | OPEN | Gate G5 (Convergence point) |
| **#203** | Portable target pack distribution & lifecycle | **C12** | `OBL-C12-01` | PLANNED | Slice 7 (Post-G5) |
| **#204** | Dual-target conformance (AgentEscala/InterLeitos) | **C13** | `OBL-C13-01` | PLANNED | Slice 8 (Post-C12) |
| **#205** | Pinable release candidate orchestration | **C14** | `OBL-C14-01` | PLANNED | Slice 9 (Terminal release) |

---

## 2. Formal Disposition of PR #327 and Issue #329

### PR #327
- **Branch:** `docs/324-ledger-authority-architecture` (Head: `0b0e58ce0d1a7c339eef9d83495625cc8ae7b49f`)
- **Status:** `OPEN / DRAFT`
- **Disposition:** `HISTORICAL_CANDIDATE_CORPUS` (NOT MERGED)
- **Rationale:** PR #327 contains valuable architectural insights and mechanism attempts, but its claimed mutation-corpus completeness failed to converge under adversarial review (`STOP_324_MUTATION_CORPUS_NOT_CONVERGING`).
- **Authorization:** Merge is explicitly NOT authorized by this campaign.

### Issue #329 (Structural Successor)
- **Status:** `OPEN`
- **Role:** Successor to PR #327 defining root causes of non-convergence:
  1. R2 (`STALE_LINE_LOCATOR`) line-level exclusion caused false-negative on motivating historical defect.
  2. Parametrized mutation tests dynamically tied to constants are self-concealing under deletion.
  3. R4 sub-expression narrowing survived mutations.
  4. R5 per-value coverage gaps.
- **Durable Lessons Extracted:**
  - `GreenMechanism != AdequateDiscriminator`
  - Dynamic parametrization over live constants can conceal removed obligations.
  - Real historical replay across git history is required for locator rules.
  - `FORGE_DERIVED` sentinels properly separate commit facts from mutable forge observations.
