# AgentReview / CAEM design-reference reuse ledger

**Status:** created fresh by this entry (#200-G2C, issue #299). Absent from
`master` at the time this slice branched; not present on any unmerged
predecessor branch this slice depends on. If a later slice (e.g. G1C/#303)
lands its own version of this file first, append this section rather than
overwrite it.

**Purpose:** record, per slice, exactly what design material this
repository read from `mglpsw/caem` as a REFERENCE, under what authority
boundary, and what is explicitly NOT claimed as a result. This repository's
CAEM pin (`config/caem/caem-3.0-f0.pin.json`) is `authority_effect: none`
for this purpose -- nothing in this file grants CAEM qualification,
certification, or normative authority over AgentReview. `mglpsw/caem` is
read here as prior art, the same way any external design document would
be, never as a governing contract for this repository.

---

## G2C -- structural egress closure for AgentReview v2's outbound Router
payload (#200-G2C, issue #299)

### predecessors

- `#280`/`#200-G2` (PR #286, frozen forensic): blocklist-style content
  classification (`redaction.py`'s "suspect unless benign" redesign),
  `STOP_G2_SAFE_REVIEW_MATERIAL_NOT_CONVERGING` -- refuted by 6 leak
  shapes round 1, worse-than-baseline regressions and a DoS class round 2.
- `#287`/`#200-G2B` (PR #293, frozen forensic): allowlist-style
  independent oracle at the real pre-HTTP seat,
  `STOP_G2B_ARCHITECTURE_NOT_CONVERGING` -- the oracle's independence from
  the forward redactor held across two full review rounds, but the oracle
  itself was refuted twice by different false-safe mechanisms (dict-
  literal/kwarg-shaped assignments, prefix carve-outs, an under-scoped
  key-suffix exemption, a plural/synonym key-form miss, a regex nesting-
  depth limit, an entropy-dilution side effect, a removed-on-one-path-not-
  the-other CapitalCase heuristic).
- `#299`/`#200-G2C` architecture spike (issue body, 2026-09-02): both
  predecessors are one failure mode -- "classifying arbitrary source text
  is an enumeration problem over an open, unenumerable domain" -- in two
  directions (blocklist, allowlist). Recommends Family B: a closed,
  schema-typed structural AST projection, replacing per-value judgment
  with a closure/type proof.

### reuse

From `mglpsw/caem`, ADR `0011-n5-proof-carrying-bundle-independent-replay.md`
(H2-ratified; `caem-3.0-c7` implementation target), section "DLP and
carrier closure", read verbatim via `gh api
repos/mglpsw/caem/contents/docs/adr/0011-...md`:

> "The carrier has no extension region, executable replay material,
> generic payload, README, or checksums view. Fixtures are typed,
> schema-valid, synthetic-only. This proves structural closure, not
> universal absence of sensitive semantics; residual semantic DLP risk
> remains externally governed."

What this slice reuses from that quote is the SHAPE of the argument, not
any CAEM mechanism, schema, or authority:

1. **"Structural closure, not universal absence of sensitive semantics"**
   is exactly this slice's own load-bearing property, restated for a
   different carrier (an outbound Router chat-completion body instead of
   an N5 replay bundle): the claim is that the OUTBOUND REPRESENTATION'S
   TYPE has no slot capable of carrying a raw literal's bytes -- never a
   claim that no sensitive value was present in the input, or that a
   scanner looked and found nothing. G2/G2B's shared failure mode was
   exactly the thing ADR 0011 disclaims in its last clause: their content-
   detection results were being read as universal absence, which they
   could never prove.
2. **"Typed, schema-valid, synthetic-only" fixtures** is the same
   discipline this slice's Tier-1 falsifier corpus follows: neutral,
   synthetic literals, because the closure property is content-agnostic
   by construction and does not need credential-shaped fixtures to be
   proven for the scope this session covers.
3. **"No extension region, generic payload, ... or checksums view"** is
   the same shape as this slice's schema-field-closure audit
   (`test_schema_field_closure_no_free_text_slot_anywhere`): every model
   in `structural_egress_projection_v2.py`'s schema graph is walked and
   asserted to have no unconstrained free-text field anywhere, the direct
   analogue of "no generic payload / extension region" for this carrier.

Nothing else from ADR 0011 is reused. Its N1--N13 stage machinery, its
carrier-manifest/plan.json semantic-root split, its independent-replay
boundary, its trusted-CAS/digest-pinning mechanism, and its terminal
reproduction-result union are NOT invoked, copied, or approximated here --
this slice has no bundle, no replay, no independent adapter, and makes no
claim about any of those N-series primitives.

### scope (what was actually built)

- `app/agent_review/structural_egress_projection_v2.py`: a closed,
  pydantic-schema-typed AST projection. Every `ast.Constant` scalar
  (str/bytes/int/float/complex) becomes an opaque
  `{kind, length, sha256_12}` placeholder unconditionally. Comments
  (`tokenize`-derived, `ast` does not represent them) project through the
  same shape. `None`/`True`/`False`/`Ellipsis` are kept as closed-enum
  tags (four-member finite domain, zero free-text capacity, control-flow-
  legible). Every identifier is deterministically aliased to a stable
  `sym_NNN` token (`EXTERNAL_SAFE_STRUCTURAL` mode -- the only mode wired
  to egress; no raw-identifier mode exists in this module).
- **Grammar universe authority**: `AUTHORIZED_AST_NODE_TYPES_V2` is
  derived live from the running interpreter's own `ast` module (every
  `ast.AST` subclass it exports) at import time -- never a hand-maintained
  list. A node type outside that live set is a hard block. Every field's
  runtime shape is separately classified by a total function
  (`_classify_leaf_v2`) against a small, closed enumeration (AST node /
  list of them / identifier string / `Constant` scalar / one of four
  named structural-integer exceptions -- `ImportFrom.level`,
  `FormattedValue.conversion`, `AnnAssign.simple`,
  `comprehension.is_async`); anything else is a hard block. The exception
  set was NOT hand-guessed: it was derived by exhaustively scanning this
  repository's own 80-file `app/agent_review` corpus for every non-
  `Constant`, non-identifier, non-node leaf shape CPython 3.11 actually
  produces (`test_structural_int_exceptions_are_exhaustive_over_the_real_
  corpus`) -- the first draft under-claimed with only `ImportFrom.level`
  and this same test caught the gap before merge, RED-before-GREEN.
- **Diff-hunk deshaping**: real reviewable content is a unified-diff hunk
  body (`diff_acquisition_v2`'s own per-line `' '`/`'+'`/`'-'` marker
  convention), not a standalone file. A bounded, format-based (not
  content-based) reconstruction of the "new side" is applied before
  parsing is attempted. Text that still does not parse as Python (or a
  non-`.py` path) is BLOCKED from egress entirely -- never a best-effort
  partial send.
- **Wired into production**: `app/agent_review/review_transport_v2.py`'s
  `_build_agent_router_messages_v2` now projects `chunk_content` before
  embedding it in the outbound `messages[1]["content"]`; a block raises
  `ChunkTransportError`, degrading the whole chunk to `manual_required`
  via the existing, unmodified failure taxonomy in
  `execute_chunk_review_v2` -- never a crash, never a partial send. The
  exact pre-HTTP body-construction logic was extracted to a standalone,
  independently callable `build_agent_router_request_body_v2` so the
  egress proof inspects the SAME bytes production sends, not a separate
  reconstruction (the specific #200-G2B lesson this slice was told to
  reuse with revalidation).
- **Tests**: `tests/agent_review/test_structural_egress_projection_v2.py`
  -- Tier-1 neutral falsifier corpus (string/bytes/numeric/dict-literal/
  kwarg/prefix-wrapper/long-identifier/docstring/comment/adjacent-literal/
  unterminated-syntax-edge-case shapes), two completeness-gate mutation
  tests (Gate A: node-type universe; Gate B: leaf-shape classifier), one
  production-wiring mutation test (bypass the projector call and confirm
  the same neutral literal that was proven absent now leaks, then
  restore), an automated schema-field-closure audit, and a negative-
  direction scan across this repository's real 80-file
  `app/agent_review` source corpus (excluding this module's own file, for
  the documented self-referential-vocabulary reason) proving no crash, no
  raw-literal leak, and exact per-node-type structural-count parity
  against the real `ast.parse` tree (review-quality/control-flow shape
  survives; only literal/identifier bytes are opaque).

### not_claimed

- No CAEM authority, qualification, or certification transfer. This
  repository's CAEM pin remains `authority_effect: none`; nothing in this
  slice changes that pin or invokes it.
- No CAEM N1--N5 pipeline (canonicalization, carrier manifest, replay,
  independent adapter, proof-carrying bundle) is invoked, approximated, or
  partially implemented by this slice. The reuse above is a design-
  reference citation of one ADR paragraph's ARGUMENT SHAPE, not an
  integration with any CAEM mechanism.
- No claim that this slice's closure proof is complete against the
  historical secret-shaped falsifier corpus from #280/#287/#293 (JWT-
  shaped, AWS-key-shaped, credential-keyword-adjacent values). That
  regression pass is explicitly deferred to a separate, later, isolated
  session against this finished implementation -- named, not hidden, in
  the Draft PR body.
- No claim of measured review-quality impact against
  `evals/agent_review_v2/` for the wired-in production path (issue #299's
  own falsifier condition 4 -- "real review-quality loss unacceptable for
  the product" -- was explicitly NOT measured in the architecture spike,
  and is not measured by this slice either; degradation can only ever be
  toward MORE `manual_required`, never toward a leak, but the volume of
  that degradation on real-world diffs is unmeasured).
- No claim that `EXTERNAL_SAFE_STRUCTURAL` is the only identifier-handling
  profile this codebase will ever need, and no raw-identifier-preserving
  mode is shipped in this slice at all (so there is no second mode whose
  egress-eligibility this ledger needs to caveat).
