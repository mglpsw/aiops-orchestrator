# V1 C2 — scope and non-claims

## Replacement identity

| Field | Value |
| --- | --- |
| Subject | `V1_C2_REQUIREMENTS_AND_INVARIANTS_FROZEN` |
| Repository | `mglpsw/aiops-orchestrator` |
| Predecessor | PR #367, discovery and reconciliation only |
| Predecessor final head | `90bd04e056c79cd27d806c70911213a78c4a23d7` |
| Legacy differential baseline | `6bbd2f949989da3e90e1d9c527e37059c0b628ff` |
| Current status | requirements candidate; not an executable or release qualification |

This replacement freezes the canonical documentation boundary for C2. It consolidates the reconciliation recorded in #367 without transferring that PR's qualification or changing its review state.

## Authority and document roles

`02_OBLIGATION_MATRIX.json` is the sole normative authority for C2. The other files have deliberately narrower roles:

| Artifact | Role | It does not do |
| --- | --- | --- |
| `00_SCOPE_AND_NONCLAIMS.md` | scope, identities, and explicit exclusions | define C2 behavior |
| `01_CLAIM_LEDGER.json` | index of claims and references | add or interpret requirements |
| `03_COUNTERMODEL_PACK.md` | witnesses and verification controls | define semantics or acceptance |
| `04_EXECUTION_ROADMAP.md` | sequence, owners, gates, terminals, and external freeze matrix | restate C2 rules |

## Included boundary

The C2 boundary is a contract-aware advisory capability. Its canonical requirements cover the mapping mode, source-state distinctions, applicable context, semantic/hunk preservation, loss handling, and the separation of Gate A, Gate B, Gate C, and Control B.

The legacy implementation is a differential baseline, not a prose specification. Its observable behavior is compared at the fixed baseline only by the later Gate A qualification named in the normative matrix.

## Explicit non-claims

- This documentation does not implement Gate A, Gate B, Gate C, or Control B.
- It does not certify ClaimV1 or establish exhaustive per-claim coverage.
- It does not qualify a release, a consumer repin, a canary, a rollback, or a final freeze.
- It does not describe, preserve, or ratify a legacy internal algorithm.
- It does not alter runtime code, AgentEscala, the predecessor PR, or any review-thread state.
- It does not turn a candidate C6 source into a final external freeze.

## Controlled handoff

The only C2 semantic handoff is to `02_OBLIGATION_MATRIX.json`. Execution and external dependencies are indexed in `04_EXECUTION_ROADMAP.md`; they remain future, separately evidenced work.
