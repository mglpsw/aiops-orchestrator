# ADR — AgentReview v2 TargetProfile YAML authority (`#203-S2`, PR-A)

**Status:** `ADR` — decisão registrada no corte em que foi tomada. Estado atual: [`../PROJECT_STATUS.md`](../PROJECT_STATUS.md).

**Status:** accepted
**Scope:** `aiops-orchestrator#203-S2`, PR-A of the target-pack validation/conformance slice under `#203`
**Decides:** how ambiguity in a target-authored `.aiops/target-profile.v2.yaml`
is determined, and which YAML language subset the profile accepts, so that
no receipt or `profile_hash`/`policy_hash` is ever minted over a reading a
different conforming parse could have disagreed with

This ADR is written retrospectively, at the merge of `#237`, directly from
the shipped source in `app/agent_review/profile_loader_v2.py` — not from
PR prose or chat history. Where this document and the merged code appear
to disagree, the code is authoritative and this document is wrong; see
`docs/engineering/AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md` for the
full account of how the design that preceded this one was falsified.

## Context

`app/agent_review/profile_loader_v2.py` loads and fully revalidates a
target-authored `TargetProfileV2` profile. Because the profile is
target-authored YAML, not a value the toolrepo constructs, the loader must
answer a question generic YAML parsers do not: when a document's bytes
admit more than one conforming reading (a duplicate authored key, a
mapping consumed as a scalar with more than one `!!value` candidate), can
this loader mint a `profile_hash`/`policy_hash` and receipt over *a*
reading, when a different conforming parser — or a human auditor — might
have seen a different one?

Two earlier designs for this loader (recorded in closed, unmerged PRs
`#235` and `#236`) were superseded before this one was accepted. Both are
summarized in the postmortem; this ADR states only the final decision.

## Normative invariant

For any document accepted by this authority:

1. the value returned is identical, field for field, to what stock
   `yaml.safe_load` returns for the same bytes;
2. no document is accepted whose bytes required the authority to make a
   choice a different conforming implementation could have made
   differently.

Conservative disposition when either property cannot be established: the
document is refused with a typed reason code
(`target_profile_unreadable` or `target_profile_invalid`) — never a raw
exception, and never a silently-chosen value.

## Implemented mechanism

**Primary semantic authority: PyYAML's own scanner, parser, composer,
resolver and constructors.** The decision is derived from that authority's
own behaviour, not reimplemented against it.

The authority (`_CollisionRefusingSafeLoaderV2`, a `yaml.SafeLoader`
subclass) instruments exactly two points — the two places stock PyYAML
silently *selects* among competing authored entries instead of refusing —
and refuses at the moment the real constructor is about to make that
selection, before it happens:

- **collision point 1 — mapping assignment.** `construct_mapping` refuses
  when a constructed key is already present in the mapping being built.
  Stock PyYAML instead overwrites the earlier entry and returns
  successfully.
- **collision point 2 — `!!value` candidates.** `construct_scalar` refuses
  when a `MappingNode` consumed as a scalar (the `!!value`/`=` idiom, e.g.
  `? !!str {=: repo}`) offers more than one `tag:yaml.org,2002:value`
  candidate. Stock PyYAML instead returns the *first* candidate found and
  ignores the rest.

Nothing else is added. The key returned by a successful `construct_object`
call is used as-is; the mapping is flattened by the unmodified, inherited
`flatten_mapping`; there is no rule keyed on node type, no reconstructed
node ancestry, no equivalence table, and no assumption about what a
different layer will or will not do later. The authority is a measurement
of the real constructor's own decision points, not a parallel
reimplementation of them.

**Duplicate semantics.** An authored duplicate is refused **even when
every occurrence carries the same value.** This authority does not compare
the values a document's readings would produce under any policy — doing so
would require predicting what those values are, which is exactly the class
of reasoning this design removes (see the postmortem's `A, B, A`
falsification). Once results are not compared, "the duplicate happens to
be harmless" is not a distinction this authority can draw.

**Merge keys (`<<:`) are unsupported.** `_document_uses_merge_v2` observes
whether the merge tag (`tag:yaml.org,2002:merge`) appears anywhere in the
composed node graph, and refuses with `target_profile_invalid` before any
document containing it reaches the collision-observing loader at all. This
is a deliberate language decision, not an incidental gap: YAML's merge
specification already fixes which entry wins when sources conflict, so
whether "the document authored this key twice" is even well-defined stops
being answerable once merged pairs can be spliced in alongside authored
ones. Excluding merge from the language is what makes tracking *provenance*
of a key (authored vs. merged) unnecessary — not something this authority
re-derives and gets wrong. It also matches
`app/agent_review/authoritative_check_policy_v2.py`'s own
`_DuplicateKeyRejectingLoaderV2`, which has never called `flatten_mapping`
and so has never given `<<:` merge semantics either.

**Contract validation is direct.** `load_target_profile_text_v2` validates
the object this authority parsed via `TargetProfileV2.model_validate(raw)`
— never through a `json.dumps`/`model_validate_json` round-trip. A JSON
round-trip coerces non-string mapping keys to strings, which can
manufacture a literal duplicate-key JSON document out of two Python objects
that were never in collision (`{"1": a, 1: b}` → `{"1": "a", "1": "b"}`,
reparsed last-wins) — a second, downstream key-resolution policy applied
by the very validation step that exists to make sure only one such policy
ever runs. Direct validation was measured, not assumed, to be equivalent
to the removed round-trip on every valid profile in the corpus.

**Reason-code mapping.**

| Condition | Reason code |
|---|---|
| Profile file absent | `target_profile_missing` |
| Unreadable file, invalid UTF-8, not valid YAML, or a document that does not parse into a mapping | `target_profile_unreadable` |
| A collision point fires (ambiguous document) | `target_profile_unreadable` |
| A merge key is present anywhere in the document | `target_profile_invalid` |
| The parsed object fails `TargetProfileV2` contract validation | `target_profile_invalid` |

The asymmetry between the two refusal classes is intentional: ambiguity
means the bytes cannot be read to one meaning at all, which this loader
treats the same as any other unreadable-YAML failure; a merge key or a
contract failure means the bytes read to exactly one meaning, but that
meaning is outside the language or shape this authority accepts.

## Why the superseded node-graph pre-pass was rejected

The design in `#236` walked PyYAML's composed node graph and re-derived,
independently, what "the same key" and "ambiguous" mean at each node type
— a structural surrogate for the parser's own behaviour rather than a
measurement of it. Seven adversarial review rounds each found that
re-derivation wrong at a different layer (textual key identity vs.
constructed identity; node shape vs. constructed value; an assumption that
an unconstructible child implies downstream rejection; a node tag alone
assumed to determine contextual consumption), and by the seventh round it
was wrong in *both* directions at once: it still accepted some genuinely
ambiguous documents and it now refused several documents stock
`SafeLoader` accepts. A first successor to that design compared two
duplicate-resolution policies' final readings instead of walking the graph;
that too was falsified, by a document authored as `A, B, A`, which resolves
to `A` under both first-wins and last-wins policies despite plainly
containing three competing values — proving that comparing outcomes
samples two points of a much larger space rather than detecting ambiguity
directly. The full mechanism-level account, including which premise failed
at which round, is in the postmortem.

## Consequences

**Positive.**

- One semantic authority (PyYAML's own constructors) instead of two
  (PyYAML plus an independent re-derivation of parts of it) — nothing in
  this design needs to be kept in sync with a future PyYAML behaviour
  change beyond the two instrumented points themselves.
- Legal documents are guaranteed value-identical to stock `yaml.safe_load`
  by construction, not by a rule that has to be proven not to over-refuse.
- No intermediate projection (JSON round-trip, canonicalisation) exists
  that could itself introduce a second key-resolution policy.

**Negative.**

- A deliberate strictness increase: a target that (harmlessly, in its own
  view) authors the same key twice with the same value is now refused,
  where the superseded comparison-based design accepted it.
- Merge keys are unavailable to targets authoring this profile, with no
  provenance-tracking escape hatch.
- The authority is coupled to exactly two named PyYAML construction
  points (`SafeConstructor.construct_mapping`,
  `SafeConstructor.construct_scalar`); a future PyYAML release that adds a
  third silent-selection point would not automatically be covered.

## Non-goals

This ADR does not cover, and this PR did not implement, `target validate`,
`target conformance`, any public schema change, or any claim about YAML
documents outside `.aiops/target-profile.v2.yaml`.

## Required conformance evidence

- Every case in `tests/agent_review/fixtures/target_profile_yaml/`
  (`legal/`, `ambiguous/`, `invalid/`, `malformed/`) passes under its
  declared `expected_disposition`/`expected_reason_code`, observed against
  this module, not assumed.
- Every `legal` case asserts **value equality** with stock
  `yaml.safe_load`, not merely the absence of an exception.
- At least one committed, production-path mutation test exists per
  instrumented collision point and per language exclusion, each proven to
  fail when the guard it claims to protect is removed (see
  `tests/agent_review/test_profile_loader_v2_mutation_discrimination.py`).

## Empirical evidence class

**`empirically_supported`**, over the enumerated families in the corpus:
mapping-assignment collisions, `!!value`-candidate collisions, seven shapes
of merge-key document, and the constructor-failure families enumerated in
`profile_loader_v2._YAML_PARSE_FAILURES_V2`'s own docstring. This is
systematic evidence over the families a human adversarial review process
enumerated across `#236`'s seven rounds and `#237`'s own reproducers — it
is **not** a universal completeness proof over PyYAML's behaviour, and no
claim to the contrary is made anywhere in this authority's tests or
documentation.

## Known completeness limitations

- **Two collision points are what has been demonstrated, not a claim that
  PyYAML has no others.** Both are explicit, standing targets of future
  adversarial review; a third silent-selection point discovered later
  would need a third instrumented refusal, not a reinterpretation of the
  first two.
- `_YAML_PARSE_FAILURES_V2` (the non-`YAMLError` exception families a
  target-authored document's constructors can raise — `ValueError`,
  `KeyError`, `IndexError`, `AttributeError`, `TypeError`,
  `UnicodeDecodeError`, `RecursionError`) is an enumerated set, not a
  proof that no other PyYAML constructor failure mode exists.
- The corpus enumerates families an adversarial human review process
  found; the absence of a family from the corpus is not evidence that
  PyYAML has no such behaviour, only that this review process has not yet
  produced a reproducer for it.

## References

- **PR #237** (merged) — `feat(agent-review/v2): derive target-profile
  YAML ambiguity from the parser (supersedes #236)`. Qualified source head
  `426683ac9aae8c654341f41e8e3b1bf5c2deb964`, squash SHA
  `6d613cf7398a89d659694e150b9b5483483ed997`, base
  `70ba4e3c7d97fddbc04f14bad6b93b5a2a7a3207`.
- **PR #236** (closed, unmerged) — the superseded node-graph pre-pass,
  head `9496a4628706798b60414a84e1ea9c056e12fec9`. Forensic record only;
  see the postmortem.
- **PR #235** (closed, unmerged) — the first combined `#203-S2` attempt,
  head `48d75336e78b373acdb9b399319c36a5706d02c0`. Forensic record only.
- Issue `#203` (target-pack distribution epic), slice `#203-S2`.
- `docs/engineering/AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md` — the
  falsification history behind this decision.
- `docs/AGENT_REVIEW_V2_TARGET_PROFILE.md` — the current, consumer-facing
  description of loader behaviour this ADR normatively backs.
