# AgentReview v1 — MethodGuidance carrier (V1-C2)

**Status:** `DRAFT_CARRIER_CONTRACT` for `mglpsw/aiops-orchestrator#221` (roadmap `#46`).
**Scope:** AgentReview v1 engine only. The target (AgentEscala) produces the sidecar in its own PR;
nothing here qualifies a target projection (Gate B/C), an exhaustive claim (ClaimV1), or a release.

## Decision

```yaml
MethodGuidanceCarrierDecision:
  producer: >-
    target-owned declarative sidecar `.aiops/method-guidance.yaml`, loaded by `load_repo_profile`
    (same canonical producer as `.aiops/domain-contracts.yaml` / `.aiops/review-packs.yaml`).
    The engine never parses the method document (no second Markdown parser): the target
    projects bounded `selected_text` and binds it to a source blob.
  canonical_source_shape: closed grammar, version 1 (below). Unknown key / version => INVALID.
  typed_carrier: >-
    `TargetProfile.method_guidance` (author document, verbatim) and
    `TargetProfile.method_guidance_verification` (loader-owned, never author-writable). Source
    verdicts are keyed by `source_id` (CURRENT | STALE | UNVERIFIABLE); text-binding verdicts are
    keyed `<method_id>/<section_id>` (BOUND | BOUND_REDACTED | UNBOUND) and exist only for a CURRENT
    source. Both fields survive `TargetProfile.model_dump` and `ReviewIntake.target_profile`; both
    keys are absent from the intake of a repository without a sidecar. An inline `method_guidance`
    in repo-profile.yaml is refused exactly like the other inline C2 sources.
  serialized_intake: ReviewIntake.target_profile.{method_guidance, method_guidance_verification}
  applicability: >-
    deterministic, per guidance item, from declared `applies_to` only (semantic_groups, exact
    paths, glob patterns, or explicit `scope: global`). At least one member is mandatory: no
    implicit apply-to-all, no inference from Markdown prose. Not applicable => not emitted, no
    limitation, and a chunk with no applicable item carries no method key at all.
  payload_surface: >-
    `chunk_context.method_guidance_context`, a sibling of (never a member of)
    `contracts_context`. Item and context carry `authority_class: METHOD_GUIDANCE`. Emitted only
    when at least one applicable item is CURRENT, so a repository without a sidecar produces
    byte-identical payloads to the previous engine.
  actual_review_input: the ChunkPayload JSON written by `aiops-review-build-payloads` (the request body the
    workflow sends to the Router). Tests assert the literal text in those bytes.
  budget_authority: >-
    `payload_cost_model` (single cost authority). `method_guidance_context()` is called by the
    planner projection and by the builder; the projection floor keeps REQUIRED items intact and
    drops OPTIONAL ones; the builder ladder drops optional method guidance before optional
    contracts and required method guidance before required contracts, always before hunks.
  loss_consequence:
    required: >-
      `required_method_guidance_context_lost:<method_id>/<section_id>` — a REQUIRED_CONTEXT_LOSS
      and critical limitation prefix: the builder refuses to route the affected chunk, the plan is
      `degraded`, the quality gate cannot return `passed`.
    optional: >-
      `optional_method_guidance_context_lost:<method_id>/<section_id>` — typed, informational; not a
      critical prefix, no automatic blocker.
    invalid_source: "`invalid_source_method_guidance:<SUBTYPE>` — critical, fail closed."
    stale_or_unverifiable: >-
      `method_guidance_source_stale:<source_id>` / `method_guidance_source_unverifiable:<source_id>`
      (informational) plus the required/optional loss of every applicable item bound to it. A stale
      reference never receives current authority and its text is never emitted.
    unbound_text: >-
      A CURRENT blob does not lend authority to text it does not contain: the item's `selected_text` must
      be a literal (whitespace-insensitive) excerpt of the verified document, else
      `method_guidance_text_unbound:<method>/<section>` plus the required/optional loss of that item.
    redacted_text: >-
      `method_guidance_text_redacted:<method>/<section>` (informational) when the redaction policy
      rewrote the emitted text; the rewrite is never silent.
  backwards_compatibility: >-
    No sidecar => no new key, no new limitation, no cost change (the projection adds nothing
    unless a method context exists). v1 goldens and the legacy differential oracle are unchanged.
```

## Sidecar grammar (version 1, closed)

```yaml
version: 1                      # required, exactly integer 1
updated: "2026-10-13"           # optional scalar metadata
sources:                        # <= 16
  - source_id: aocm             # [A-Za-z0-9][A-Za-z0-9._:-]{0,63}, unique
    repository: owner/repo
    path: docs/canonical/aocm/METHOD.md   # canonical repo-relative path
    revision: <commit>          # identity, non-empty
    blob: <40 hex git blob oid> # compared against the checkout by the loader
guidance:                       # <= 32
  - method_id: AOCM
    section_id: obligation-matrix
    source_id: aocm             # must resolve to a declared source
    requiredness: optional      # required | optional — explicit, no default
    applies_to:                 # closed; >= 1 non-empty member
      semantic_groups: [primary_backend_logic]
      paths: [backend/api/shifts.py]
      patterns: ["backend/**"]
      scope: global
    selected_text: "..."        # non-empty, <= 4000 chars
```

Grammar notes (all enforced, each with a countermodel in `tests/agent_review/test_v1_method_guidance_carrier.py`):

- unknown keys at any level, `version` ≠ 1 and author-written verdicts are `UNSUPPORTED_NONEMPTY`; one violation
  invalidates the whole source (nothing is admitted);
- duplicate YAML keys are invalid (the stock loader would resolve them last-wins and silently);
- `source_id` / `method_id` / `section_id` match `[A-Za-z0-9][A-Za-z0-9._:-]{0,63}` and must not equal a
  redaction-sensitive key name (`token`, `secret`, `password`, ...) because the loader-owned mapping is keyed by them;
- `paths` members containing a glob character (`*`, `?`, `[`) are patterns (same convention as modern contracts), so
  a glob written as an exact path is not silently inert; `paths` / `patterns` are bounded (≤ 64 members, ≤ 256 chars
  each). **Known limits of that convention (shared matcher, reviewed):** a file whose literal name contains `[`…`]`
  (e.g. `pages/[id].jsx`) is treated as a glob and does not match itself — select it by `semantic_groups` instead;
  and `**/*.py` does not match a top-level `x.py` nor `backend/**/x.py` match `backend/x.py`;
- the source `path` is canonicalized by ONE function for verification and emission. Both the **sidecar** and every
  declared **source document** are read relative to the trusted `--repo-root` with no link followed on any path
  component (`O_NOFOLLOW` per component, size and regular-file checked on the opened descriptor): a link — to a host
  file, to another file inside the checkout, dangling or looping, on the file or on `.aiops` itself — is never
  read, so host bytes cannot become `method_guidance`. A **sidecar** that is such a link, a directory, a FIFO, an
  oversize (> 4 MiB) or undecodable file is typed INVALID (never ABSENT) and the read is bounded. A declared
  **source document** that is a link, unreadable, oversize or outside the checkout is UNVERIFIABLE. A platform
  without `O_NOFOLLOW` / descriptor-relative open fails closed (INVALID / UNVERIFIABLE). `updated`, when present,
  is a string, an integer or a **finite** float; `NaN` / `±Infinity` is MALFORMED_SHAPE. A source document whose
  bytes are not UTF-8 but whose blob oid matches is CURRENT and only the item whose text cannot be found is UNBOUND
  (U+FFFD never binds);
- `selected_text` must not contain control (Cc), format (Cf, e.g. bidi/zero-width), surrogate, private-use,
  unassigned or line/paragraph-separator characters, nor U+FFFD (`\n` and `\t` are allowed): otherwise the whole
  sidecar is INVALID. **Known false rejects** (author sees typed INVALID, never a silent drop): ZWJ/ZWNJ (emoji ZWJ
  sequences), soft hyphen U+00AD (common in PDF/Word copy), BOM/ZWNBSP inside text, word joiner, LRM/RLM and bidi
  embeddings, tag characters, VT/FF/DEL/NEL, and any code point unassigned in the Unicode database of the running
  interpreter (verdict depends on its `unicodedata` version; CI pins Python 3.11). **Known residual** of the category
  approach: invisible default-ignorable characters outside those categories (variation selectors supplement,
  U+3164, U+FFA0, U+2800, U+034F, Hangul fillers) are admitted; they must also appear in the blob-bound source, which
  has the same author trust. NBSP and other Zs spaces are admitted and treated as whitespace by the binding;
- every emitted `source` field is constrained: `source_id`/`method_id`/`section_id` by identity regex, `path` by the
  canonicalizer **plus** the `selected_text` character-class policy and a 256-character cap (the canonicalizer alone only
  refuses traversal, absolute paths and C0/DEL), `blob` as 40 lowercase hex, `revision` by the allowlist
  `[A-Za-z0-9][A-Za-z0-9._/@+-]{0,127}` and `repository` by `owner/name` (`[A-Za-z0-9._-]`), otherwise INVALID;
- the repository identity anchor of a source is the trusted CLI `--target-repo` **only**: a missing or empty CLI
  value makes every source UNVERIFIABLE; the author-editable `repo-profile.yaml` label is never a fallback;
- `source.revision` is carried as identity only and is **never verified**; the git blob is the binding. A non-empty
  revision string does not prove the blob came from that revision.

**Residual (pre-existing loader class, shared with `domain-contracts.yaml` / `review-packs.yaml`, not repaired
here):** the YAML parser and the non-string-key scan have no alias-expansion or nesting bound. A sidecar with an
alias bomb can hang the CLI and a nesting depth of roughly 330–500 (interpreter-dependent) can raise `RecursionError` out of the key scan
(shallower is accepted, deeper is refused by the parser and typed INVALID). The sidecar parse step itself is typed
for any parser exception (including PyYAML explicit tags such as `!!bool maybe`). This is an availability failure,
not a false pass. Also pre-existing and shared with the contract sources: PR-brief sizing searches the whole
target profile for an integer knob (`pr_brief_max_chars`), so a key nested inside an INVALID sidecar still sets
that budget (an integer, never text, never a verdict); a bound belongs to a separate loader-hardening slice for all three sources. Whitespace-insensitive binding also treats NBSP as whitespace and has no minimum length or word boundary
(a negation-truncated excerpt still binds; right-excerpt judgment is a declared non-claim).

Source-state vocabulary reuses `PRESENT_VALID | ABSENT | INVALID` from OBL-CL2-03. `ABSENT` is not
`not_relevant`: an absent sidecar declares nothing and the engine claims nothing from it.

## Separation of authorities

| Class | Where it travels | Can it become an invariant? |
|---|---|---|
| ProductContract | `contracts_context` (`domain_contracts`, `review_packs`) | yes — target-declared |
| MethodGuidance | `method_guidance_context` | no — advisory review method |
| PRClaim | PR description/brief | never policy |
| ReviewObservation | model response | advisory |
| ReadinessAuthority | deterministic quality gate | decides |

Method guidance never enters `domain_contracts`, `review_packs` or `contract_bindings`, never feeds
`contract:<id>` resolution, and never changes the effective contract set of a pack.

## Non-claims

Known adjacent limits (not closed here): the v1→v2 profile migrator does not *carry* `method_guidance` (v2 has
no such carrier and none is invented here); it now reports the field as a typed pending decision, by name only,
instead of dropping it silently. Because the sidecar is loader-owned and never part of the v1 profile document, the
shipped script derives its presence from the trusted `--repo-root` (`os.path.lexists`, so a dangling link still
counts) and passes it to the migrator; callers of `migrate_profile_v1_to_v2` outside the script must do the same. An unparsable `domain-contracts.yaml` / `review-packs.yaml` still reads as "no
contracts declared" on this branch (separate slice: PR #374). Those two loaders (and `repo-profile.yaml`) still use
`read_text`, which follows links: the same host-file exposure measured for the sidecar was reproduced for
`domain-contracts.yaml` and `review-packs.yaml` on `master` (for `repo-profile.yaml` it depends on which fields the
model keeps). It is a separate, adjacent follow-up that can adopt `_read_bounded_in_checkout`. The engine cannot judge whether an excerpt is the
*right* excerpt of the method document, only that it is one.

No Gate B/C, Control A/B, ClaimV1, chunking/coverage closure (`AR-V1-C2-CHUNKING-COVERAGE-CLOSURE`),
repin, release or v2 change. A model finding that cites a method id as `contract_id` is still an
advisory observation; the carrier adds no contract-id validation (adjacent surface, deliberately
untouched).
