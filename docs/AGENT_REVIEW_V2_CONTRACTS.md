# AgentReview v2 contract foundation

Issue #80 introduces an explicitly versioned contract line alongside the
operational AgentReview v1 pipeline from `v0.20.0`. This first delivery freezes
only strict v2 data models and JSON Schemas. It does not activate v2 in a CLI,
planner, payload builder, parser, synthesizer, quality gate, Router endpoint, or
target-repository workflow.

## Frozen contracts

| Contract | Purpose |
| --- | --- |
| `agent-review.run.v2` | Deterministic run identity and explicit origin/lifetime metadata |
| `agent-review.chunk-payload.v2` | Run-bound payload material, verified payload hash, coverage, and typed references |
| `agent-review.chunk-response-envelope.v2` | Sanitized success/error response bound to run, chunk, verified payload, and HEAD |
| `agent-review.target-profile.v2` | Generic target inputs without control over engine safety boundaries |
| `agent-review.review-readiness.v2` | PR, identity, checks, coverage, degradation, blockers, and finding lifecycle proof |

The Python authority is `app/agent_review/contracts_v2.py`. Every object uses
Pydantic strict validation, freezes instances, and rejects unknown fields at
every nesting level. Schema ID, schema version, and source are required
constants. Git commit SHAs are lowercase 40-character hexadecimal values and
SHA-256 values are lowercase 64-character hexadecimal values.

The supported schema-generation toolchain is Python 3.11 with the repository
requirements, currently Pydantic `2.11.3`. Generate or verify schemas with:

```bash
python3 scripts/export-agent-review-v2-schemas.py
python3 scripts/export-agent-review-v2-schemas.py --check
```

`--check` is read-only and compares exact UTF-8 file bytes. The renderer uses a
named response root model, collapses Pydantic reference-only aliases, replaces
module-qualified internal definition names with unique model titles, and
normalizes the JSON type of `const` values. Committed schemas and a render from
a clean supported process must therefore be byte-identical.

Every JSON Schema object sets `additionalProperties: false`; required and
unknown fields are enforced by the schema. JSON Schema alone does not execute
Pydantic cross-field/after validators. Hash equality, normalized POSIX path
spelling, exact coverage partitions, response-to-payload file scope,
finish-reason semantics, lifecycle coherence, and readiness proofs described
below require validation through the Python contract authority.

## Canonical JSON bytes

All v2 hashes in this foundation use the same canonical JSON encoding:

```text
ensure_ascii=False
sort_keys=True
separators=(",", ":")
allow_nan=False
UTF-8 encoding
no trailing newline
```

Only JSON values with string object keys are accepted. Non-finite numbers,
timestamps inserted by the engine, random values, local paths, and non-JSON
objects are not introduced implicitly.

## Run identity and the manifest decision

`run_id` is the lowercase SHA-256 digest of the canonical JSON object containing
exactly these ten fields:

```text
repo
pr_number
base_sha
head_sha
tested_merge_sha
toolrepo_sha
profile_hash
policy_hash
manifest_hash
evidence_hash
```

The initial nine-field draft omitted `manifest_hash`. Inspection of v1 showed
that `ChunkPayloadManifest` is an independent artifact: it lists chunks and
their individual `payload_sha256` values, while v1 has no `evidence_hash`
derivation that commits that manifest. Consequently v2 does not claim that
`evidence_hash` covers it. `manifest_hash` is an independent material input and
is deliberately added before the contract is frozen.

`manifest_hash` is SHA-256 over the entire sanitized manifest JSON object using
the canonical encoding above. `canonical_manifest_bytes_v2` and
`compute_manifest_hash_v2` freeze that derivation. Sanitization covers values
and every string object key recursively, so a dynamic key containing a token,
credential header, or absolute local path is rejected before hashing; legitimate
relative path keys remain representable. The concrete multi-chunk v2 manifest
model remains work for PR 3. Changing profile, policy, manifest, or evidence
independently changes `run_id`.

`created_at`, `expires_at`, and origin metadata are explicit run-envelope
fields, not identity fields. The golden fixture
`tests/agent_review/fixtures/v2/golden_run_identity.json` freezes the exact
ten-field JSON text and digest.

## Payload hash and response binding

`payload_sha256` is not a format-only field. Its preimage is the complete
validated `ChunkPayloadMaterialV2` JSON object, excluding exactly the
`payload_sha256` field to avoid a circular self-hash. It therefore covers:

- schema ID, schema version, and source;
- canonical `run_id` and full run identity;
- chunk ID and semantic group;
- total/must-review coverage and structured degradation causes;
- every typed artifact and contract reference.

`canonical_chunk_payload_bytes_v2`, `compute_payload_sha256_v2`, and
`verify_payload_sha256_v2` implement this rule. Dictionary insertion order does
not affect the digest. Any accepted material change does. Model validation
rejects a stale or fabricated digest, including a copied Pydantic instance that
attempts to bypass validation. `golden_chunk_payload_hash.json` freezes the
complete canonical payload preimage and its digest byte for byte.

`validate_response_binding_v2` accepts a validated payload (directly or through
`ResponseBindingV2`), serializes and fully revalidates the envelope, recalculates
its response hash, re-verifies the payload hash, and then compares response
`run_id`, `chunk_id`, `payload_sha256`, and `head_sha` before a future parser may
consume findings. For a success response, the helper then requires both
`result.coverage.expected_files` to equal the bound payload's
`coverage.expected_files` and every finding `file_path` to be within that exact
file universe. A stale hash, invalid contract, copied model, or mutated nested
list produces the stable binding reason `response_contract_invalid`. Wiring
this helper into consumers remains PR 2 work.

Binding failures are intentionally separated without embedding exception or
payload content in the reason: `response_contract_invalid` means the envelope or
its response hash failed full revalidation; `payload_contract_invalid` means the
expected payload itself failed revalidation; and `payload_sha256_mismatch` means
both objects are valid but the response is bound to a different valid payload.
`response_scope_mismatch` means a valid success response claims coverage or a
finding outside the payload's file scope. The original validation exception is
retained only as the Python exception cause. These reason codes are contract
helpers only and are not yet wired into consumers.

## Response hash and finish reason

`response_sha256` is SHA-256 over every field of the sanitized response
envelope except `response_sha256` itself. The preimage includes identity and
transport metadata, `response_received`, status, finish reason, and the typed
result or typed error. It is not a hash of a raw provider body, prompt, header,
credential, or other sensitive content; none of those values may be stored in
the envelope.

The semantics are explicit:

- `response_received=true` requires a matching `response_sha256` for both
  success and error envelopes;
- `response_received=false` is allowed only for `transport_failure` with
  `finish_reason=error`, and requires `response_sha256=null` because no response
  bytes exist to verify;
- the null no-response case proves only the structured failure state, not a
  cryptographic property of an absent response.

`success` accepts only the conclusively complete `finish_reason=stop`.
`length`, `content_filter`, `error`, and `unknown` are error states. `tool_call`
is reserved for possible future orchestration and is currently non-conclusive,
so it is also accepted only in an error envelope. No tool execution is
implemented here.

## Coverage without silent omission

For every `ChunkCoverageV2`, `reviewed_files`, `partially_reviewed_files`, and
`missing_files` are unique, pairwise disjoint, and their union is exactly
`expected_files`. `must_review_files` is a subset of expected files and
`missing_must_review_files` must equal the must-review intersection with partial
or missing files.

State rules are fail-closed:

- `complete` means every expected file is reviewed, with no partial, missing,
  missing-must-review, or degradation cause;
- `partial` has at least one partial or missing file and no degraded-state
  cause;
- `degraded` has at least one partial or missing file plus typed causes whose
  affected-file union accounts for every such file and no other file.

No expected file can disappear from the partitions.

At binding time the payload supplies the outer file-scope boundary. A success
response must retain that complete expected-file set and may report findings
only within it; a cryptographically valid response can neither omit a payload
file nor introduce an unbound repository path.

## Target profile hard boundaries and sanitization

`TargetProfileV2` is repository-neutral and contains no AgentEscala,
InterLeitos, or other target-specific branch. A target cannot weaken engine
safety:

- `network_policy` is always `forbidden`;
- `fail_closed` and `redaction_required` are always `true`;
- `allow_partial_coverage` is always `false` in this frozen foundation.

Every `RelativePath` must already be in its exact normalized POSIX spelling.
Empty and dot components, duplicate separators, trailing separators, parent
traversal, absolute paths, home-relative forms, and Windows forms are rejected,
so one repository file cannot acquire multiple coverage identities.
`RelativePattern` has a separate validator with the same structural path rules;
glob components such as `*`, `**`, and `test_*.py` remain valid and are not
normalized away.

`TargetIdentityV2.default_branch` uses a dedicated strict `BranchName`, not an
internal identifier. It implements the documented
[`git check-ref-format --branch`](https://git-scm.com/docs/git-check-ref-format)
restrictions in-process, with no subprocess or installed-Git dependency:
hierarchical `/` components are supported, while leading/trailing or duplicate
slashes, dot-leading or `.lock` components, `..`, `@{`, a lone `@`, controls,
the reserved pseudo-ref `HEAD`, spaces, backslash, and Git's other forbidden
ref characters are rejected. The JSON Schema exposes the safe-character subset,
rejects `HEAD` explicitly, and includes the `x-git-ref-format: --branch`
annotation; the remaining structural rules require the Python contract
authority.

Check names use bounded safe text, so names such as `Validate repository` and
identifiers such as `secret-scan` remain representable. `SafeText` accepts
bounded printable UTF-8, including Portuguese and small technical snippets.
Words such as `secret`, `password`, or `cookie` are not rejected by themselves.
The real artifact sanitizer still rejects token-shaped credentials,
`Authorization: Bearer` values, credential assignments/URLs, private keys, and
absolute Linux, Windows, or home-relative paths.

## Readiness proof

`ReviewReadinessV2` carries both expected and evaluated full run identities,
their computed run IDs, expected/evaluated HEADs, observed PR state, every
deterministic required check and conclusion, exact total/must-review coverage,
structured pipeline degradation, blockers, and finding lifecycle records.

Every lifecycle record is a statement revalidated in the evaluated context:
`observed_at_head_sha` must equal `evaluated_head_sha`; every non-`new`
`decided_at_head_sha` must equal it as well; and each evidence `head_sha` means
the HEAD on which that evidence was revalidated and must also match. A commit
evidence item continues to identify its concrete commit through `reference`.
An older decision may be reconstructed only as a new record after revalidation
on the current evaluated HEAD; it cannot silently make a later HEAD ready.

The principal invariants are:

- `ready` requires an open non-merged PR, exact run identity and HEAD, at least
  one required deterministic check with every result green on the evaluated
  HEAD, complete total/must-review coverage, and no degraded pipeline;
- actionable `new` or `confirmed` P0/P1/P2 findings prevent `ready`;
- an isolated `new` P0/P1/P2 is not confirmation: it produces
  `manual_required` with `finding_confirmation_required`, may use a healthy
  pipeline, and its blocker points exactly to the pending finding;
- an isolated actionable P3 does not create `blocked_code`;
- `blocked_code` requires exactly one distinct active blocker for every existing
  confirmed, actionable P0/P1/P2 finding through `confirmed_code_finding`. It
  may additionally preserve degraded pipeline reasons, active blockers, and
  matching structured causes without surrendering code-block precedence;
- pipeline blockers never point to findings. Manual pipeline reasons still
  match structured degradation causes, while `finding_confirmation_required`
  is deliberately not a pipeline cause and may coexist with those reasons;
- `manual_required` cannot mask an already confirmed actionable P0/P1/P2
  finding as uncertainty or another manual cause;
- `blocked_pipeline` cannot mask such a confirmed finding as a transport,
  schema, coverage, or policy failure; when pipeline degradation coexists,
  `blocked_code` carries both the confirmed finding blockers and the structured
  degradation evidence;
- when confirmed and new blocking findings coexist, confirmed code takes
  precedence as `blocked_code`; every finding remains in the audit record and
  the pending new finding must be reconsidered after the confirmed block clears;
- `blocked_pipeline` and `manual_required` may preserve partial findings for
  audit without becoming ready;
- `new` and `confirmed` are actionable; `fixed`, `dismissed`, `superseded`, and
  `stale` are not;
- every non-new disposition records the responsible identity and decision HEAD;
  dismissal additionally requires a justification and typed commit/test
  evidence bound to a HEAD;
- `stale` explicitly records `head_mismatch`, `identity_mismatch`, or both,
  based on expected versus evaluated identities.

This contract represents the proof inputs but does not calculate them in the v1
quality gate.

## Compatibility and remaining PRs

The `v0.20.0` v1 models, artifacts, CLIs, quality gate, and wrapper contract are
unchanged. During migration, consumers must select v1 or v2 explicitly and
must never mix envelopes silently.

- PR 2: connect verified payload/response binding and fail-closed precedence to
  consumers and parser;
- PR 3: implement a typed manifest and deterministic real multi-chunk planning;
- PR 4: implement the complete TargetProfile v2 loader/migrator and minimal
  isolated toolrepo lockfile.

Until those deliveries adopt v2 explicitly, these schemas are development
contracts only. They do not represent a release, deploy, Router/provider call,
or production behavior change.
