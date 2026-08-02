# AGENTS.md — schemas/agent-review

Specializes the root `AGENTS.md`. Does not weaken any hard boundary declared
there; only adds invariants specific to this directory.

## What lives here

Exported JSON Schema files for every AgentReview contract (v1 and v2),
generated from — and checked byte-identical against — the real Pydantic
models in `app/agent_review/contracts_v2.py` and siblings via
`app/agent_review/schema_export_v2.py`'s `render_v2_json_schemas()` (v1
export lives alongside it). These files are a published surface: an
external tool or a target repository may validate a document against them
directly, without ever importing this codebase's Python.

## `additionalProperties: false` is load-bearing

Every object schema here is closed (`additionalProperties: false`). An
unknown field in an incoming document must be rejected, not silently
accepted and ignored. If a new field is genuinely needed, add it to the
real Pydantic model first (with `extra="forbid"`, matching
`ContractV2Model`'s own base), then regenerate — never hand-edit a
`.schema.json` file directly, and never widen `additionalProperties` to
make a validation error go away.

## JSON Schema is not the authority for cross-field rules

JSON Schema can express "this field exists and has this type/shape", but it
cannot express most of this package's real invariants: `run_id ==
compute_run_id(identity)`, "a `blocked_code` state requires a confirmed
finding and a matching structured pipeline cause", "a list must not contain
duplicates under a specific dedup key", and so on. The Python
`model_validator`s in `contracts_v2.py` and siblings are the sole authority
for those checks. A document that merely validates against one of these
exported schemas is NOT proven correct — it must still pass the real
Pydantic construction (`model_validate`) before being trusted. Do not
suggest that passing schema validation alone is sufficient evidence of
correctness.

## New contract types

A new v2 contract type must be added to BOTH:

1. `schema_export_v2.py`'s registry (so `render_v2_json_schemas()` emits
   it), and
2. the corresponding hardcoded filename set in
   `tests/agent_review/test_contracts_v2.py::test_exported_json_schemas_are_stable_and_deny_unknown_objects`.

Adding it to only one of the two is an incomplete change — the test exists
specifically to catch that.

## What a reviewer here must never suggest

- hand-editing a `.schema.json` file to fix a validation failure instead of
  fixing the source Pydantic model and regenerating;
- widening `additionalProperties` to accept an unrecognized field;
- treating schema-only validation as equivalent to the real Python
  `model_validate` cross-field authority.
