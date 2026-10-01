# V1 C2 — execution roadmap

## Status and sequence

| Order | Handoff | Owner | Terminal / output |
| --- | --- | --- | --- |
| 1 | Requirements replacement | documentation owner | C2 requirements candidate at this PR head |
| 2 | Gate A | executable qualification owner | differential evidence against baseline `6bbd2f949989da3e90e1d9c527e37059c0b628ff` |
| 3 | Gate B | AgentEscala issue #869 owner | #869 handoff evidence at its exact reviewed subject |
| 4 | Gate C | exact-pair qualification owner | qualification evidence for the exact required pair only |
| 5 | Control A | #805 control owner | bounded deterministic transport/gate regression terminal and evidence |
| 6 | Control B | #805 control owner | bounded semantic-utility evidence, separately granted |
| 7 | C6 source | C6 source owner | candidate source identity; not final freeze |
| 8 | External freeze decision | release/freeze authority | separately granted final decision |

No row substitutes for another. A terminal records the named row's state only; it is not authorization to mutate the next row or to declare final freeze.

## Gate C exact pair contract

Gate C qualifies one ordered pair. The pair is recoverable before either future
member exists, but neither member's future SHA is invented here:

| Member | Repository / source owner | Exact subject required | Material evidence required |
| --- | --- | --- | --- |
| Engine member | `mglpsw/aiops-orchestrator` / Gate A owner | the exact engine commit qualified by Gate A | Gate A receipt bound to the engine repository, commit, legacy baseline, and differential observations |
| Target member | `mglpsw/AgentEscala` / issue #869 owner | the exact target projection/configuration subject accepted by Gate B | target commit or immutable source identities for the projection, review-pack/domain-contract inputs, and material configuration |

The Gate C subject is the tuple `(engine exact commit, target exact subject, material source/config identities)`. A different engine commit, target subject,
or source/config identity is a different pair and cannot inherit this pair's
qualification. Missing identity prevents Gate C qualification; it does not
select a substitute.

## Control A handoff

Control A is the distinct #805 deterministic transport/gate regression control.
Its terminal and evidence are owned by the #805 owner and are recorded as a
separate bounded result. The existing #221 C2 task-contract decision is the
provenance for the control's purpose, not execution evidence. Gate A, Gate B,
Gate C, and Control B do not silently satisfy Control A.

## External freeze prerequisite matrix

This is the single external prerequisite matrix for C2 final-freeze work.

| Dependency | Repository | Required state before final freeze | Evidence identity required later | Owner | Blocking effect |
| --- | --- | --- | --- | --- |
| AgentEscala issue #869 | `mglpsw/AgentEscala` | resolved at its exact subject | issue state, implementation subject, and Gate B record | `mglpsw` (author; no assignee at revalidation) | blocks its Gate B handoff and final freeze |
| AgentEscala issue #871 | `mglpsw/AgentEscala` | resolved at its exact subject | issue state, implementation subject, and acceptance record | `mglpsw` (author; no assignee at revalidation) | blocks final freeze |
| AgentEscala issue #678 | `mglpsw/AgentEscala` | resolved at its exact subject | issue state, implementation subject, and acceptance record | `mglpsw` (author and assignee at revalidation) | blocks final freeze |
| Maintenance release | `mglpsw/aiops-orchestrator` | identified, built, and accepted under its release grant | release identity and acceptance record | release owner | blocks final freeze |
| Consumer repin | consumer repository | exact accepted release is repinned by the consumer | consumer subject and resolved dependency identity | consumer owner | blocks final freeze |
| Canary | deployment environment | bounded canary is accepted under its operational grant | canary scope, identity, and result | operations owner | blocks final freeze |
| Rollback | deployment environment | rollback path is identified and accepted for the canary scope | rollback identity and acceptance record | operations owner | blocks final freeze |

## Scope guard

This roadmap sequences evidence and authority only. C2 semantics remain solely in `02_OBLIGATION_MATRIX.json`; witness material remains solely in `03_COUNTERMODEL_PACK.md`.
