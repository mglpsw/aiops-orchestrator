# AgentReview v2 — semantic review content contract (#200-A)

Refs `aiops-orchestrator#200`, first slice of the distribution epic
`aiops-orchestrator#199`. Design rationale lives in
`docs/adr/ADR_AGENT_REVIEW_V2_REVIEW_CONTENT.md`; this document is the
operational reference for the contracts themselves.

## Scope of this PR (`#200-A`)

Contract and ADR only. Nothing here extracts real hunk bytes from a diff,
runs redaction, runs a DLP engine, calls the Agent Router, or touches
`app/agent_review/diff_acquisition_v2.py`. That is `#200-B`/`#200-C`, later
PRs in the same DAG.

## What was missing

`ChunkPayloadV2` (`contracts_v2.py`) never carried content — only
`path`/`old_range`/`new_range`/`diff_sha256` on `FragmentV2`. The v2
pipeline could compute a fully valid, fully bound `ReviewReadinessV2`
without a model ever seeing one line of real code
(`review_content_extraction_not_implemented`, surfaced by AgentEscala's
shadow adoption, `AgentEscala#759`).

## The two new contracts

### `app/agent_review/review_content_v2.py` — `ReviewContentV2`

A sidecar bound to a specific `ManifestV2` by `run_id`/`manifest_hash`
(never folded into `ChunkPayloadV2` — see the ADR's Decision 1).

```text
ReviewContentV2
├── run_id, manifest_hash                 (binds to a specific ManifestV2)
├── dlp_policy_digest                     (which DLP policy applied, if any)
├── content_set_sha256                    (self-hash, canonical preimage)
└── chunks: [ChunkContentV2]
      ├── chunk_id, payload_sha256        (which payload this content is for)
      ├── content_sha256                  (self-hash of this chunk's content)
      └── fragments: [FragmentContentV2]
            ├── fragment_id, path, diff_sha256   (must match the manifest exactly)
            ├── coverage_required                (mirrors FragmentV2.coverage_required)
            ├── policy                           (included | omitted_* | blocked_* | unrepresentable)
            ├── content, content_sha256          (present iff policy == included)
            └── redaction_applied, chars
```

Use `bind_review_content_to_manifest_v2(content, manifest)` before trusting
a `ReviewContentV2` you did not just build yourself — it fail-closes on any
divergence from the manifest (run identity, manifest hash, chunk set,
fragment set, path, `diff_sha256`, `coverage_required`, and a tampered
`content_set_sha256`). It does **not** cross-check `payload_sha256` against
a real, built `ChunkPayloadV2` — that is `#200-C`'s job.

A `coverage_required` fragment can never be represented with anything other
than `policy=included` — `FragmentContentV2` refuses to construct that
combination (`CONTENT_REQUIRED_FRAGMENT_MISSING_REASON_V2`).

### `app/agent_review/review_transport_contract_v2.py` — the transport envelope

Closes the gap where `payload_sha256` alone cannot prove a response was
produced over one *specific* content sidecar (see the ADR's Decision 2).

```text
ChunkReviewRequestV2
├── run_id, chunk_id, head_sha, payload_sha256, content_sha256
└── request_sha256 = sha256(all of the above, canonical JSON)

ChunkReviewTransportEnvelopeV1
├── request_sha256, content_sha256        (must echo the request's own values)
└── response: ChunkResponseEnvelopeValueV2    (the UNMODIFIED v2 union, untouched)
```

`verify_transport_echo_v1(envelope, request=request)` is the fail-closed
gate: it re-validates the inner v2 envelope on its own terms, then requires
both hashes to echo exactly, before returning the inner envelope for
`consumer_v2.bind_chunk_response_v2`. A syntactically perfect,
`payload_sha256`-correct response produced against the wrong content is
rejected here, before it ever reaches v2's own binding authority.

## Declarative / host-owned DLP policy (`DlpPolicyDeclarationV2`)

A target may declare either inline pattern rules (`DlpPolicyRuleV2` —
pattern + `block` action, interpreted by a host-owned engine, never
executed) or a reference to a host-owned detector by name, pinned by digest.
There is no `path`/`module`/`import`/`entrypoint` field in this schema —
`load_dlp_policy_declaration_v2` rejects any document that tries to add one
(`DLP_POLICY_NOT_HOST_OWNED_REASON_V2`), and the closed schema
(`extra="forbid"`) backs that up structurally. `verify_dlp_policy_digest_v2`
fail-closes on a policy that no longer matches its pinned digest
(`DLP_POLICY_DIGEST_MISMATCH_REASON_V2`).

Loading, allowlisting, and executing a real detector by name is `#200-B`'s
job — this PR only freezes the shape and the digest that pins it.

## Contract compatibility

Zero already-published v2 schema changed. Verified, not assumed:

```bash
.venv/bin/python scripts/export-agent-review-v2-schemas.py --check
```

`agent-review.chunk-payload.v2.schema.json` and
`agent-review.chunk-response-envelope.v2.schema.json` are byte-identical
before and after this PR. Three new schemas are added:
`agent-review.review-content.v2`, `agent-review.review-transport-envelope.v1`,
`agent-review.dlp-policy.v1` — all registered in the RI-B0a.2 reuse manifest
(`config/ri/ri-b0a-2-reuse-manifest.json`) as `reference`/`not_applicable`
respectively (RI-B0 does not consume per-chunk content or transport
plumbing, mirroring `agent-review.chunk-payload.v2`/`agent-review.
payload-set.v2`'s own classification).

## Reason codes

| Constant | Raised by |
|---|---|
| `CONTENT_RUN_IDENTITY_MISMATCH_REASON_V2` | `bind_review_content_to_manifest_v2` |
| `CONTENT_MANIFEST_HASH_MISMATCH_REASON_V2` | `bind_review_content_to_manifest_v2` |
| `CONTENT_CHUNK_SET_MISMATCH_REASON_V2` | `bind_review_content_to_manifest_v2` |
| `CONTENT_FRAGMENT_NOT_IN_MANIFEST_REASON_V2` | `bind_review_content_to_manifest_v2` |
| `CONTENT_PATH_MISMATCH_REASON_V2` | `bind_review_content_to_manifest_v2` |
| `CONTENT_DIFF_SHA256_MISMATCH_REASON_V2` | `bind_review_content_to_manifest_v2` |
| `CONTENT_COVERAGE_REQUIRED_MISMATCH_REASON_V2` | `bind_review_content_to_manifest_v2` |
| `CONTENT_REQUIRED_FRAGMENT_MISSING_REASON_V2` | `FragmentContentV2` construction, and `bind_review_content_to_manifest_v2` as defense in depth |
| `CONTENT_SET_HASH_MISMATCH_REASON_V2` | `bind_review_content_to_manifest_v2` |
| `CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2` | reserved for `#200-C`; not raised here |
| `DLP_POLICY_NOT_HOST_OWNED_REASON_V2` | `load_dlp_policy_declaration_v2` |
| `DLP_POLICY_DIGEST_MISMATCH_REASON_V2` | `verify_dlp_policy_digest_v2` |
| `CONTENT_ECHO_MISMATCH_REASON_V2` | `verify_transport_echo_v1` |
| `REQUEST_ECHO_MISMATCH_REASON_V2` | `verify_transport_echo_v1` |
| `TRANSPORT_ENVELOPE_INVALID_REASON_V2` | `verify_transport_echo_v1` |

`OMITTED_BINARY`/`OMITTED_SUBMODULE`/`OMITTED_GENERATED`/`OMITTED_MINIFIED`/
`OMITTED_OVER_BUDGET`/`BLOCKED_BY_REDACTION`/`BLOCKED_BY_TARGET_DLP`/
`UNREPRESENTABLE` are `ReviewContentPolicyV2` enum values, not raised
exceptions — a caller uses `policy.value` directly for telemetry.

## What is deliberately not here

- extracting real hunk bytes from a diff, redaction, or a DLP engine
  (`#200-B`);
- calling the Agent Router or any transport (`#200-C`);
- cross-checking `ChunkContentV2.payload_sha256` against a real, built
  `ChunkPayloadV2` (`#200-C`, mirrors `payload_set_v2` vs.
  `payload_set_emission_v2`'s own split);
- a canary review of a real repository — the first one is `AgentEscala
  #763-A`, gated on `#200-B`/`#200-C` landing first.
