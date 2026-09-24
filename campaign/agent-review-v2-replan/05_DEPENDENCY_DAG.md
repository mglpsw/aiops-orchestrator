# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Status: PROPOSED_AOCM_REPLAN
# AgentReview v2 — Dependency Directed Acyclic Graph (DAG)

**Repository:** `mglpsw/aiops-orchestrator`  
**Method:** AOCM-MPACK 0.1.0-preview.1  
**Campaign:** `agent-review-v2-replan`  
**Date:** 2026-09-23  
**Roadmap Authority:** [Issue #46](https://github.com/mglpsw/aiops-orchestrator/issues/46)  

---

## 1. Visual Dependency Graph

```mermaid
flowchart TD
    %% Base Anchors
    %% Critical Path Chain
    M0["AOCM Adoption & Reconciliation Freeze<br/>(PR #344, PR #345 @ master@c58eecd)"] --> C2A
    C2A["C2_A / #331-B<br/>Storage Capability Enforcement (PR #348)<br/>(DescriptorIdentity != ReResolvedPathIdentity)"]
    C2A --> C3["C3 / #304<br/>Canonical Tree Materialization<br/>(Blob-faithful sandbox extraction)"]
    C2A -.-> C2B["C2_B / #331-B, #46<br/>Host Policy & Consumer Binding<br/>(Operational Provenance)"]
    C2B -.-> G5
    
    subgraph C4_Preconditions ["C4 Preconditions (Closed in Master)"]
        direction TB
        P321["#321: Hostile-read boundary (PR #312)"]
        P319["#319/#322: Trust anchor exact str gate"]
        P341["#341: Component-wise no-follow locator"]
    end
    
    C3 --> C4_Preconditions
    C4_Preconditions --> C4["C4 / #301, #333<br/>Execution Provenance<br/>(Subprocess & env scrub)"]
    
    C4 --> C5["C5 / #298<br/>Semantic Coverage<br/>(Hunk chunking & conservation)"]
    C5 --> C6["C6 / #314<br/>Safe Outbound Representation<br/>(DLP & path sanitization)"]
    C6 --> C78["C7-C8 / #320, #323<br/>Router Receipt & Deterministic Readiness<br/>(Pure functional gate)"]
    
    %% Independent Parallel Lanes
    subgraph Parallel_Lanes ["Independent Parallel Lanes"]
        direction TB
        C9["C9 / #201<br/>Trusted Required Checks<br/>(Host runner attestation)"]
        C10["C10 / #202<br/>Canonical Path Identity<br/>(Unicode NFC / C-quote)"]
    end
    
    M0 -.-> C9
    M0 -.-> C10
    
    %% Convergence at Gate G5
    C78 --> G5["C11 / Gate G5 (#326, #332)<br/>Operational Composition<br/>(Full end-to-end qualification)"]
    C9 --> G5
    C10 --> G5
    
    %% Distribution & Release Chain
    G5 --> C12["C12 / #203<br/>Portable Distribution<br/>(Target pack lifecycle)"]
    C12 --> C13["C13 / #204<br/>Dual-Target Conformance<br/>(AgentEscala & InterLeitos)"]
    C13 --> C14["C14 / #205<br/>Pinable Release Candidate<br/>(v2.0.0-rc.1 release freeze)"]

    classDef closed fill:#d4edda,stroke:#28a745,stroke-width:2px;
    classDef ready fill:#fff3cd,stroke:#ffc107,stroke-width:2px;
    classDef blocked fill:#e2e3e5,stroke:#6c757d,stroke-width:1px;
    
    class M0,P321,P319,P341 closed;
    class C2A,C9,C10 ready;
    class C2B,C3,C4,C5,C6,C78,G5,C12,C13,C14 blocked;
```

---

## 2. Critical Path Sequence and Execution Rules

The primary critical path consists of strictly dependent transformations:

```text
Knowledge / Claim Freeze (PR #344, PR #345)
  ↓
C2_A: Storage Capability Enforcement (#331-B, PR #348)  <-- IMPLEMENTED, PENDING EXACT-HEAD REVIEW
  ↓
C3: Canonical Tree Materialization (#304)
  ↓
C4: Execution Provenance (#301, #333)
  ↓
C5: Semantic Coverage & Manifest (#298)
  ↓
C6: Safe Outbound Representation / DLP (#314)
  ↓
C7-C8: Router Receipt Binding & Deterministic Readiness (#320, #323)
  ↓
C11: Gate G5 Operational Composition (#326, #332)
  ↓
C12: Portable Distribution (#203)
  ↓
C13: Dual-Target Conformance (#204)
  ↓
C14: Pinable Release Candidate (#205)
```

### Execution Rules:
1. **WIP = 1 on Critical Chain**: Work on the critical chain proceeds strictly one obligation at a time. PRs for successor obligations may not be opened until the predecessor's draft is approved and integrated.
2. **Independent Lanes Policy**:
   - `C9 / #201` (Trusted Required Checks) and `C10 / #202` (Canonical Path Identity) are structurally orthogonal to the internal hunk-chunking / DLP modules.
   - They may advance as separate branches if and only if their write sets do not touch `app/agent_review/trusted_object_authority_v2.py` or `diff_authority_v2.py`.
   - Both must converge and be merged before Gate G5 (`C11`) qualification.
3. **No Retroactive Weakening**: Successor obligations must never silently drop or relax preconditions established by predecessors.
