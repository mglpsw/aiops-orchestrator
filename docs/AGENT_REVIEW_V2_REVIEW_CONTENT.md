# AgentReview v2 — semantic review content: contract (#200-A), real extraction (#200-B), and transport/E2E (#200-C)

Refs `aiops-orchestrator#200`, all three slices of the distribution epic
`aiops-orchestrator#199`. Design rationale for the contract lives in
`docs/adr/ADR_AGENT_REVIEW_V2_REVIEW_CONTENT.md`; this document is the
operational reference for the contracts and the extractor/transport that
populate and move them.

**Capability state**: `#200` is now `core_synthetic_complete` — the full
chain from a real diff to a real `ReviewReadinessV2` is proven, offline and
synthetically. It is **not** `semantic_reviewer_shadow` yet: no live Router
call has ever been made by this code, and no real repository has been
canary-reviewed (`AgentEscala#763-A`, gated on a repin to a release
containing this work and an explicit grant to enable the Router). See the
epic `#199`'s own nomenclature note: `core_synthetic_complete` and
`target_adoption_complete`/`semantic_reviewer_shadow` are different,
independent states — this closes the former only.

## Scope of `#200-A` (contract and ADR only)

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

## `#200-B` — real extraction (`app/agent_review/review_content_extraction_v2.py`)

Turns a real diff into a `ReviewContentV2` bound to an already-assembled
`ManifestV2` (`run_assembly_v2.assemble_manifest_from_diff_v2`). Reuses,
never reimplements: `diff_acquisition_v2.acquire_authoritative_diff_v2`
(path/hunk identity), `redaction.redact_text` (generic redaction),
`review_content_v2`'s own contract, DLP declaration, and manifest binding.

```text
acquire_authoritative_diff_v2 + extract_hunk_bodies_v2  (real diff, real bodies)
  -> slice_hunk_body_by_range_v2 per fragment             (lossless line-selection)
  -> exact diff_sha256 recomposition (whole-hunk fragments only)
  -> classification (binary / submodule / generated / minified)
  -> redaction.redact_text
  -> declarative DLP rule evaluation
  -> per-chunk char budget (TargetBudgetsV2.max_chars_per_chunk)
  -> ReviewContentV2, bound to the manifest before returning
```

### `diff_acquisition_v2` additions this slice needed

`ParsedHunkV2` only ever kept a hunk's `diff_sha256`/`diff_chars` — the
body text itself was hashed and discarded (by design, per that module's own
header). Real extraction needs the body back, so `#200-B` extended the
SAME parser (`_FileBlockBuilder`), not a second one:

- `compute_hunk_diff_sha256_v2(body_text, *, old_no_newline_at_eof,
  new_no_newline_at_eof)` — the one preimage definition, now called by
  both `_FileBlockBuilder` (while parsing) and `extract_hunk_bodies_v2`
  (while re-deriving), so the two can never silently drift into two
  different hashes for the same body;
- `HunkBodyV2` — one hunk's real body text, keyed by
  `path`/`hunk_index`/`diff_sha256`, carrying `old_start`/`old_lines`/
  `new_start`/`new_lines` too (needed for per-fragment line-range
  slicing). Never embedded in `ParsedFileDiffV2`/`ParsedHunkV2` — the
  parsed contract stays raw-content-free exactly as before;
- `extract_hunk_bodies_v2(diff_text)` — re-parses with the same builder,
  and re-verifies every returned body against `compute_hunk_diff_sha256_v2`
  before returning it (`HUNK_BODY_DIGEST_MISMATCH_REASON_V2` on any
  divergence — unreachable through this path today, kept fail-closed).

### The line-selection rule (why a "window" fragment is not a substring)

A fragment produced by `planner_v2`'s windowing (a hunk larger than the
per-chunk line budget) has its `old_range`/`new_range` computed
INDEPENDENTLY per side. For a hunk with a long uninterrupted run of
deletions followed by a long run of insertions, "window k" on the old side
and "window k" on the new side can be physically disjoint stretches of the
hunk body — there is no single contiguous substring that represents a
window fragment in general.

`slice_hunk_body_by_range_v2` therefore selects per LINE, not per
substring: every body line whose old or new line number falls inside the
fragment's declared range, in original order. This is provably lossless
(every line the fragment claims to cover is included exactly once — proven
by `test_extract_review_content_windows_a_hunk_larger_than_the_line_budget_
losslessly`, which asserts zero line is double-counted across windows for a
realistic interleaved hunk) and, for the whole-hunk case, reproduces the
entire body byte-for-byte — verified against `diff_sha256`, not assumed.

### `payload_sha256` is caller-supplied, never fabricated

`extract_review_content_v2` requires `payload_sha256_by_chunk_id`: the
REAL `payload_sha256` of each chunk's already-built `ChunkPayloadV2`
(`payload_builder_v2.build_chunk_payload_v2`, built from the SAME manifest
before this function is called). It does not build payloads itself and
never substitutes a placeholder hash — a missing entry blocks the whole
extraction fail-closed (`chunk_payload_sha256_unavailable`).
Cross-checking that this hash actually matches the real payload object
byte-for-byte remains `#200-C`'s job, per `bind_review_content_to_
manifest_v2`'s own documented split.

### Fail-closed paths (never a silent approval)

| Condition | Result |
|---|---|
| `coverage_required` fragment is binary/submodule/unrepresentable | `ExtractionBlockedError(hunk_body_unavailable)` |
| `coverage_required` fragment's content matches a DLP rule | `ExtractionBlockedError(transport_blocked_by_dlp)` |
| `coverage_required` fragment's redacted content exceeds `max_chars_per_chunk` | `ExtractionBlockedError(content_over_budget_requires_replan)` |
| whole-hunk fragment fails to reproduce `diff_sha256` from its own slice | `ExtractionBlockedError(hunk_recomposition_failed)` |
| manifest has zero chunks (e.g. every file excluded as non-must-review binary) | `ExtractionBlockedError(no_reviewable_chunks)` |
| a non-`coverage_required` fragment hits any of the above | typed `ReviewContentPolicyV2` omission instead (never blocks the run) |

Automatic re-planning when extracted content exceeds budget (invoking
`planner_v2` again with a smaller line budget and retrying in the same
call) is explicitly **not** implemented here — it needs the original
`HunkInputV2` list this module does not hold, and blurring it into this
module's one job (extraction) would reopen `run_assembly_v2`'s territory.
Named as a limitation, not silently faked.

### Binary/submodule files: a documented reachability gap

`ReviewContentPolicyV2.OMITTED_BINARY`/`OMITTED_SUBMODULE` exist and are
exercised directly (`test_classify_unrepresentable_marks_binary_and_
submodule_files_typed`), but are **not reachable through today's real
pipeline**: a binary file never produces a `ParsedHunkV2` at all, so it
never produces a `FragmentV2`/`fragment_id` either. A non-must-review
binary file is silently excluded by `run_assembly_v2` before any fragment
exists for it (invisible to `ReviewContentV2`, not present with a typed
omission — see `test_extract_review_content_excludes_a_non_must_review_
binary_file_entirely`); a must-review one blocks the whole manifest
assembly before extraction is ever reached. This is defense in depth for a
future change to that upstream exclusion, not dead code removed here.

## `#200-C` — transport and end-to-end synthesis (`app/agent_review/review_transport_v2.py`)

Wires the fixed order of authority the #199 execution plan's D2 requires:

```text
ReviewContentV2 (#200-A/#200-B)
  -> ChunkReviewRequestV2                  (#200-A)
  -> transport                              (#200-C)
  -> ChunkReviewTransportEnvelopeV1         (#200-A)
  -> verify_transport_echo_v1               (#200-A)
  -> consumer_v2.bind_chunk_response_v2     (already existed)
  -> parser_v2.parse_bound_chunk_response_v2
  -> synthesis_v2.synthesize_chunk_results_v2
  -> readiness_decision_v2.compute_readiness_decision_v2
  -> review_readiness_emission_v2.produce_review_readiness_v2   (#201-C)
```

`execute_chunk_review_v2` is the single choke point: no finding is
reachable from a chunk unless transport succeeds, the echo verifies, AND
the response binds, in that exact order. Every failure mode — transport
error, tampered echo, malformed response — degrades EXACTLY that chunk to
`ChunkReviewOutcomeV2(state="manual_required", ...)`; it is never raised
up as a run-wide exception and never fabricates a result. `run_synthetic_
review_v2` (the orchestrator) hands `synthesize_chunk_results_v2` only the
chunks that bound; the existing, unmodified coverage/readiness authority
(`synthesis_v2`/`readiness_decision_v2`) reflects the gap as real missing
coverage — proven directly: a run with one tampered-echo chunk never
reaches `ReadinessStateV2.READY`.

### Transport is an injected callable

`ChunkReviewTransportV2` is a `Protocol`. Two implementations ship:

- `offline_file_transport_v2(responses_dir)` — reads one pre-placed
  `{chunk_id}.json` transport envelope per chunk. **Default in tests**, and
  the one this slice's synthetic E2E actually exercises end-to-end,
  including a real `ReadinessStateV2.READY` outcome from a fully bound,
  fully covered run;
- `agent_router_transport_v2(base_url, api_key, model)` — the real Agent
  Router, locked to EXACTLY `{base_url}/v1/chat/completions` (mirrors
  `scripts/github_agent_review.py`'s own `call_agent_router_review`
  discipline — no provider-direct call, no second endpoint). Raises
  `ChunkTransportError(ROUTER_DISABLED_REASON_V2)` immediately, before any
  network attempt, if `api_key` is empty — there is no ambient/implicit
  enablement path. Covered by unit tests against a **mocked** HTTP layer
  only; this slice never makes a live network call to any Router.

### Failure taxonomy (never a silent approval)

| Condition | Chunk outcome |
|---|---|
| response file missing / unreadable | `manual_required` / `transport_failure` |
| response body is not valid JSON or does not validate | `manual_required` / `transport_invalid_response` |
| HTTP 5xx / 429 from the real Router | `manual_required` / `transport_unavailable` |
| network timeout | `manual_required` / `transport_timeout` |
| tampered `content_sha256`/`request_sha256` echo | `manual_required` / `content_echo_mismatch` or `request_echo_mismatch` |
| response fails `bind_chunk_response_v2` | `manual_required` / that function's own reason code |

A run with any `manual_required` chunk never reaches `READY` — proven
directly, not assumed, by
`test_run_synthetic_review_degrades_a_tampered_echo_chunk_to_manual_required`
and its missing-file/malformed-JSON siblings.

## Adversarial audit follow-up (post-merge)

A human-authored adversarial replan gate against the live, already-merged
`#200-B` code found and this repository fixed five confirmed issues
(zero false positives) — see `docs/checkpoints/AGENT_REVIEW_V2_200_
ADVERSARIAL_AUDIT_FOLLOWUP.md` for the full classification:

1. `requires_network`-marked E2E tests were silently deselected by both
   CI gates by default — fixed by `scripts/ci_validate.sh`'s new §8;
2. a `detector_name`-only DLP policy was silently treated as "clean"
   instead of "never actually checked" — `_apply_dlp_v2` now blocks
   unconditionally whenever a detector is declared;
3. budget was enforced per-fragment only, never summed per chunk, and
   accepted a bare caller-supplied int — `extract_review_content_v2` now
   requires a real `TargetBudgetsV2`, and `_enforce_chunk_budget_v2` sums
   per chunk;
4. **the most severe**: `planner_v2`'s own documented repeated-anchor
   exception (a starved side collapsing to the same range across multiple
   windows) caused the SAME real line of code to be extracted into every
   window sharing that anchor — confirmed by direct reproduction (15/15
   fragments carried a duplicated line before the fix) and fixed via
   `_assign_hunk_line_ownership_v2`, using only `planner_v2`'s own already-
   emitted fragment ranges, no second parser or planner;
5. local paths were not actually redacted before content reached
   `FragmentContentV2`'s constructor (only its own last-line guard caught
   it, as a raw exception) — fixed by applying `_redact_local_paths`.

A second, independent adversarial pass over that same fix found three
more confirmed issues, all closed in the SAME follow-up (no new phase):

6. the `TargetBudgetsV2` from finding 3 above proved values only, not
   provenance — any caller could still construct a looser one than the
   profile that actually planned the manifest. `extract_review_content_v2`
   now takes the full `target_profile: TargetProfileV2` and checks
   `compute_profile_hash_v2(target_profile) == manifest.identity.
   profile_hash` before reading `target_profile.budgets` at all;
7. `_enforce_chunk_budget_v2` blocked the instant ANY `coverage_required`
   fragment shared an over-budget chunk, even when dropping auxiliary
   content alone would have made it fit — contradicting the module's own
   documented doctrine. It now only blocks when the `coverage_required`
   fragments' chars ALONE exceed the budget; otherwise auxiliaries are
   still dropped largest-first as originally intended;
8. the add/modify/delete/rename hardening test asserted a fragment existed
   for the deleted/renamed path but never checked its actual content, and
   accepted either the rename's old or new path with a permissive `OR` —
   now proves the deleted line is really present and that the canonical
   path is deterministically the new name (`ParsedFileDiffV2.path` is
   `new_path or old_path`), never the stale one.

`extract_review_content_v2`'s signature changed twice in this follow-up:
`max_chars_per_chunk: int = 20_000` → `target_budgets: TargetBudgetsV2`
(finding 3) → `target_profile: TargetProfileV2` (finding 6, hash-checked
against `manifest.identity.profile_hash`). No production caller outside
this repository's own tests existed yet.

## What is deliberately not here

- cross-checking `ChunkContentV2.payload_sha256` against a real, built
  `ChunkPayloadV2` byte-for-byte (the caller — `run_synthetic_review_v2`'s
  own caller — is responsible for having built `payload_by_chunk_id` from
  real `payload_builder_v2` output; this module trusts what it is handed,
  exactly like `bind_review_content_to_manifest_v2` already does for the
  same field);
- automatic content-budget-triggered re-planning (see the `#200-B`
  section above);
- a real out-of-process, host-owned DLP detector (`detector_name`-only
  policies are accepted but contribute zero rule coverage from the
  extractor);
- a live call to the real Agent Router, from this repository, in this
  slice — `agent_router_transport_v2` is real, tested code, never
  exercised against the network;
- a canary review of a real repository — the first one is `AgentEscala
  #763-A`, gated on a repin to a release containing this work and a
  separate grant to enable `AGENT_REVIEW_V2_ROUTER_ENABLED`.
