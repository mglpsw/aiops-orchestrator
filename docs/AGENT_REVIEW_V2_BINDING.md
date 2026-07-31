# AgentReview v2 — verified binding in consumers and parser

Refs #83. Builds on the contract foundation frozen in PRs #81/#82
(`docs/AGENT_REVIEW_V2_CONTRACTS.md`). This delivery wires
`contracts_v2.validate_response_binding_v2` into a consumer and a parser so
that no v2 finding is reachable before the payload/response binding is
proven, without changing `contracts_v2.py` or the v1 pipeline.

## What this delivery adds

- `app/agent_review/versioning.py` — explicit v1/v2 contract-version
  selection. Never infers a version from partial field presence: it checks
  each raw object's own complete, required `schema_id`/`schema_version`
  marker against the literal constants each contract module owns
  (`contracts_v2` for v2, `schemas` for v1).
- `app/agent_review/consumer_v2.py` — `BoundChunkResponseV2` and
  `bind_chunk_response_v2`/`load_and_bind_chunk_response_v2`.
- `app/agent_review/parser_v2.py` — `parse_bound_chunk_response_v2`, the
  only function in this delivery that exposes findings, and the only one
  that can: it accepts nothing but a `BoundChunkResponseV2`.

`app/agent_review/contracts_v2.py` and `tests/agent_review/test_contracts_v2.py`
are unmodified. `validate_response_binding_v2`,
`validate_chunk_response_envelope_v2`, `verify_payload_sha256_v2`,
`ResponseBindingError`, and `ResponseErrorReasonV2` are reused as-is, not
reimplemented.

## Version selection

`versioning.select_contract_version(*, requested, payload_raw, response_raw)`
is the single, explicit gate a call site uses before deciding whether to run
the v1 pipeline (untouched) or the v2 pipeline (this delivery). `requested`
is mandatory and must be exactly `"v1"` or `"v2"` — there is no default and
no inference from shape.

A payload/response's version is determined by its own complete marker pair:

- v2 payload: `schema_id == "agent-review.chunk-payload.v2"` and
  `schema_version == 2` (the same literal fields `ChunkPayloadV2` itself
  requires);
- v1 payload: `schema_id == "agent-review.chunk-payload.v1"` and
  `schema_version == 1` (`schemas.CHUNK_PAYLOAD_SCHEMA`);
- v2 response envelope: `schema_id ==
  "agent-review.chunk-response-envelope.v2"` and `schema_version == 2`;
- v1 response: `schema_version == 1` with no `schema_id` at all —
  `schemas.ChunkResponse` never declared that field, so its absence together
  with `schema_version == 1` is v1's own genuine, complete marker, not a
  partial-field guess.

`select_contract_version` raises `ResponseBindingError` (the existing
exception type, reused rather than a new hierarchy):

- `unsupported_contract_version` — `requested` is not `"v1"`/`"v2"`, or a raw
  object's markers do not correspond to any known version at all;
- `mixed_contract_versions` — the request, the payload, and (when supplied)
  the response each identify a *known* version, but they disagree.

This function never performs full Pydantic validation itself; that remains
`contracts_v2`'s job inside binding. It only decides whether it is safe to
attempt the v2 pipeline at all.

## Binding before findings

`consumer_v2.bind_chunk_response_v2(*, envelope, payload)`:

1. revalidates the envelope through `validate_chunk_response_envelope_v2`
   (any failure → `response_contract_invalid`);
2. obtains a fresh, independently-revalidated copy of the payload via
   `ChunkPayloadV2.model_validate_json(payload.model_dump_json(), strict=True)`
   (any failure → `payload_contract_invalid`) — never the caller's original
   payload reference;
3. calls `validate_response_binding_v2` once, unmodified, as the single
   authority for run/chunk/payload-hash/HEAD identity and file-scope/coverage
   comparison;
4. if the (now-verified) envelope is a structured error envelope, raises
   `ResponseBindingError` with that envelope's own
   `ResponseErrorReasonV2` value (`transport_failure`, `schema_failure`,
   `policy_failure`, or `model_uncertainty`) — reused verbatim, not
   reinvented;
5. only if all of the above succeed, returns `BoundChunkResponseV2`, built
   from the freshly revalidated envelope/payload obtained in steps 1–2, not
   the caller's originals.

Because steps 1–2 always construct new objects (Pydantic validation never
returns the input object), a caller mutating its own `envelope`/`payload`
references *after* a successful `bind_chunk_response_v2` call cannot affect
the already-returned `BoundChunkResponseV2`. `BoundChunkResponseV2` also
copies `findings`/`limitations` into tuples, not the source lists.

`load_and_bind_chunk_response_v2(*, payload_path, response_path)` adds file
I/O: a missing/unreadable response file is `transport_failure` (no response
bytes exist at all); a response file that exists but fails to validate as a
v2 envelope (including malformed JSON) is `response_contract_invalid` (bytes
were received, they just are not bindable). Neither is ever interpreted as
"zero findings approved" — no `BoundChunkResponseV2` is produced in either
case.

### `BoundChunkResponseV2` is an internal-API guard, not a cryptographic boundary

Its constructor is gated by a module-private sentinel object so that
constructing it directly — bypassing `bind_chunk_response_v2` — fails fast
with `TypeError`. This catches accidental misuse; it is not a defense
against code that has access to this module's internals (such code could
already reach around `contracts_v2`'s `frozen=True` models via
`model_copy`, a risk that module's own docs and tests already document).
The actual guarantee is operational: `parser_v2.parse_bound_chunk_response_v2`
— the only function in this delivery that returns findings — accepts
nothing but a `BoundChunkResponseV2`, and no other function in
`consumer_v2.py`/`parser_v2.py` exposes findings from any other input shape.

## Reason codes and precedence

No new binding-identity/scope taxonomy is introduced. `contracts_v2.py`
remains the sole authority for `response_contract_invalid`,
`payload_contract_invalid`, `run_id_mismatch`, `chunk_id_mismatch`,
`payload_sha256_mismatch`, `head_sha_mismatch`, and
`response_scope_mismatch`. `ResponseErrorReasonV2` remains the sole
authority for a structurally valid error envelope's own reason
(`transport_failure`, `schema_failure`, `policy_failure`,
`model_uncertainty`). Only two reason codes are new, both belonging to the
version-selection stage that has no prior authority anywhere in the
repository: `unsupported_contract_version` and `mixed_contract_versions`.

Precedence is enforced by sequential, short-circuiting stages — not a
priority table evaluated after the fact — so exactly one reason code is ever
produced, and it is always the first stage that fails:

| Stage | Reason code(s) | Where |
|---|---|---|
| 0. Version selection | `unsupported_contract_version`, `mixed_contract_versions` | `versioning.select_contract_version` |
| 1. Transport/load | `transport_failure` | `load_and_bind_chunk_response_v2` (missing/unreadable file), or passthrough of an already-valid error envelope's own reason |
| 2. Envelope revalidation | `response_contract_invalid` | `bind_chunk_response_v2` (wraps `validate_chunk_response_envelope_v2`) |
| 3. Payload revalidation | `payload_contract_invalid` | `bind_chunk_response_v2` |
| 4. Identity | `run_id_mismatch`, `chunk_id_mismatch`, `payload_sha256_mismatch`, `head_sha_mismatch` (this exact order) | `validate_response_binding_v2` (unmodified) |
| 5. Scope/coverage | `response_scope_mismatch` | `validate_response_binding_v2` (unmodified) |
| 5b. Structured error passthrough | `schema_failure` / `policy_failure` / `model_uncertainty` / `transport_failure` | `bind_chunk_response_v2`, after step 5 passes structurally |

Every reason code is a short string from this closed list. No exception
text, raw payload/response content, token, or local path is ever attached
to a `ResponseBindingError`; the original exception is preserved only as
Python's `__cause__`, never serialized.

## What is intentionally not in this delivery

- No new CLI. The v1 CLIs (`scripts/aiops-review-*.py`) are untouched; v2
  selection and binding are exercised directly as library calls and by the
  test suite. A CLI is natural once there is an end-to-end flow to drive
  (#86).
- No `ReviewReadinessV2` computation, no quality-gate v2, no synthesis v2.
  `ParsedChunkResultV2` preserves `run_id`, `chunk_id`, `head_sha`, coverage,
  and typed findings so that future work can build `ReviewReadinessV2`
  without recomputing binding, but this delivery does not construct it.
- No manifest/multi-chunk planning (#84) and no target-profile loader (#85):
  `bind_chunk_response_v2` operates on a single already-presented
  payload/envelope pair.
