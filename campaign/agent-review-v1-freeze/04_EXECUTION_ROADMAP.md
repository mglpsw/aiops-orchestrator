# V1 C2 — execution roadmap

## Status and sequence

| Order | Handoff | Owner | Terminal / output |
| --- | --- | --- | --- |
| 1 | Requirements replacement | documentation owner | C2 requirements candidate at this PR head |
| 2 | Gate A | executable qualification owner | differential evidence against baseline `6bbd2f949989da3e90e1d9c527e37059c0b628ff` |
| 3 | Gate B | AgentEscala issue #869 owner | #869 handoff evidence at its exact reviewed subject |
| 4 | Gate C | exact-pair qualification owner | qualification evidence for the exact required pair only |
| 5 | Control B | #805 control owner | bounded semantic-utility evidence, separately granted |
| 6 | C6 source | C6 source owner | candidate source identity; not final freeze |
| 7 | External freeze decision | release/freeze authority | separately granted final decision |

No row substitutes for another. A terminal records the named row's state only; it is not authorization to mutate the next row or to declare final freeze.

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
