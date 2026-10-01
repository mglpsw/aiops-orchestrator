# V1 C2 — countermodel pack

## Role

This is a non-normative witness and control index. It cannot introduce, weaken, or interpret a C2 requirement. Each expected outcome is governed only by the referenced obligation in `02_OBLIGATION_MATRIX.json`.

| Witness | Positive control | Reference | Observation to preserve later |
| --- | --- | --- | --- |
| `CM-CL2-01`: result is presented as ClaimV1 | `PC-CL2-01`: result is bounded as contract-aware advisory | `OBL-CL2-01` | capability label and limitation |
| `CM-CL2-02`: relation cannot be explicitly established | `PC-CL2-02`: admitted pack and binding establish the relation | `OBL-CL2-02`, `INV-C2-01` | resolved versus unresolved disposition |
| `CM-CL2-03`: required source is absent or invalid | `PC-CL2-03`: required source is present_valid | `OBL-CL2-03`, `INV-C2-02` | distinct source state |
| `CM-CL2-04`: applicable context is missing, lost, or unresolved | `PC-CL2-04`: valid evaluation finds no applicable context | `OBL-CL2-04`, `INV-C2-03` | context and relevance disposition |
| `CM-CL2-05`: required semantic context or intact hunk material is lost | `PC-CL2-05`: required material remains complete and intact | `OBL-CL2-05`, `INV-C2-04`, `INV-C2-05` | required versus optional loss and resulting gate disposition |
| `CM-CL2-06`: evidence is attributed to a substituted or blended gate | `PC-CL2-06`: evidence is tied to its named gate and exact subject | `OBL-CL2-06` | gate identity and subject identity |
| `CM-CL2-07`: a #805 control is promoted beyond its bounded purpose | `PC-CL2-07`: Control A or Control B remains separately bounded | `OBL-CL2-07` | control identity, purpose, and limitation |

## Use at later gates

Later executable work may turn these witnesses into fixtures or checks, but the witness list itself is not a test suite, an implementation plan, or gate evidence. The legacy baseline is exercised only through the Gate A handoff; this pack does not characterize legacy internals.
