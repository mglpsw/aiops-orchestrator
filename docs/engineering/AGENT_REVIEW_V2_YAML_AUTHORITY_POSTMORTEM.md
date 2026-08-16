# Postmortem — AgentReview v2 TargetProfile YAML authority (`#203-S2`, PR-A)

**Status:** `POSTMORTEM` — engineering learning, not the decision and not
the current behaviour. The decision is
[`../adr/ADR_AGENT_REVIEW_V2_TARGET_PROFILE_YAML_AUTHORITY.md`](../adr/ADR_AGENT_REVIEW_V2_TARGET_PROFILE_YAML_AUTHORITY.md);
current consumer-facing behaviour is
[`../AGENT_REVIEW_V2_TARGET_PROFILE.md`](../AGENT_REVIEW_V2_TARGET_PROFILE.md).

This document records how and why two earlier designs for the TargetProfile
YAML authority were falsified before the one PR `#237` shipped was accepted.
It is factual and mechanism-oriented, not a transcript: it does not
reproduce chat history, and an external adversarial review is treated
throughout as a source of *findings to reproduce*, never as proof of
correctness by itself.

## The generalized pattern

Every falsified premise below is an instance of the same shape: a rule
written at the wrong layer, re-deriving something an existing authority
already decides.

```text
text / node representation
  → constructed value
    → consumer-visible semantics
      → authority boundary
```

The property this authority needs to guarantee lives at the *authority
boundary* — "does this reading match what a different conforming reader
would produce." Both superseded designs instead wrote rules against the
*text/node representation* or the *constructed value*, one or two layers
below where the property actually lives, and had to keep patching the gap
between the two.

## Minimal chronology

| Attempt | PR / head | Disposition | Layer falsified |
|---|---|---|---|
| Combined validate+conformance slice | `#235`, `48d75336e78b373acdb9b399319c36a5706d02c0` | closed, unmerged | test-integrity: 3 of the instruments used to declare it done were themselves defective |
| Node-graph pre-pass walker | `#236`, `9496a4628706798b60414a84e1ea9c056e12fec9` | closed, unmerged | re-derivation of parser semantics; 7 rounds, findings 2/2/1/2/2/3/4, wrong in both directions by round 7 |
| Comparison of two duplicate-resolution policies' final readings | (superseded within the work that became `#237`, no separate PR) | discarded before merge | result-comparison samples two points of a larger space; falsified by `A, B, A` |
| Collision observation, direct contract validation | `#237`, `426683ac9aae8c654341f41e8e3b1bf5c2deb964` → `6d613cf7398a89d659694e150b9b5483483ed997` | **merged** | — |

## Falsified premises

Each of these was a specific, stated (or implicit) assumption in the
superseded designs, and each was killed by a specific counterexample —
not by general dissatisfaction with the design.

- **Textual key identity vs. constructed identity.** A rule comparing key
  *text* (`repo:` vs. `"repo":`) cannot see that both construct to the same
  Python value, nor that a retagged key (`!!str`) and a plain one can
  construct identically. PyYAML's own `construct_object` already answers
  this; a text-level rule re-derives it, incompletely.
- **Node shape vs. constructed value.** A rule branching on whether a node
  is a `ScalarNode` or `MappingNode` diverges from what the node
  *constructs to*, which is what a collision actually is. The `!!value`
  idiom (`? !!str {=: repo}`) makes a `MappingNode` construct to a plain
  scalar; a shape-based rule mismodels it.
- **Assuming an unconstructible child implies downstream rejection.** A
  rule that skips validating a branch because "the contract will reject it
  anyway" stops being true the moment the branch *is* constructible and the
  contract accepts it under a different field name — the rule silently
  loses coverage instead of failing loudly.
- **Assuming a node tag alone determines contextual consumption.** Whether
  a `MappingNode` is consumed as a mapping or as a scalar (via `!!value`)
  depends on the *consumer*, not the tag alone; a rule keyed only on tag
  conflates the two contexts.
- **Duplicating parser semantics through an independent walker.** The core
  failure mode of `#236`: re-deriving, node type by node type, decisions
  PyYAML's own constructors already make, instead of observing those
  constructors directly. Each of the premises above is a specific instance
  of this general one.
- **Safe-input over-rejection as a first-class failure, not merely a
  missed attack.** By round 7, the walker refused three documents stock
  `SafeLoader` accepts (a retagged `!!value` key, discarded plain siblings
  next to a `!!value` pair, an unconsumed integer sibling). An adversarial
  corpus alone — built to find missed attacks — cannot detect this
  direction; only an *equality* assertion against stock `safe_load` on a
  safe-counterexample corpus can.
- **Result-comparison as an ambiguity authority.** The first successor
  design read a document under two duplicate-resolution policies and
  compared the two final values, treating agreement as proof of
  unambiguity. A document authored `repo: A`, `repo: B`, `repo: A` resolves
  to `A` under both first-wins and last-wins — the comparison agrees while
  the document plainly contains a real conflict. Comparing outcomes samples
  two points of a much larger space; it does not observe the collision
  itself.
- **Lossy canonicalisation/projection used to decide ambiguity.** Two
  further defects followed directly from the comparison design once it
  existed: a `default=repr` canonicalisation collapsed distinct scalar
  types with coincident textual representations (`!!binary YXBwL3g=` vs. a
  quoted string), and a `json.dumps` round-trip manufactured a literal
  duplicate-key JSON document from two Python keys (`"1"` and `1`) that
  were never in collision. Both disappeared, without a targeted fix, the
  moment the steps that produced them were removed.

## Two process failures visible only across attempts

**Green deterministic gates did not qualify an architecture.** `#235`
reached CI green with zero open review threads while three of the
instruments used to declare it done were themselves defective: a guard
that compared field *names* instead of values, a "call-graph" read-only
proof that was actually a single-file AST scan, and Class-4 tests that
stayed green with both mechanisms they claimed to guard disabled. Passing
gates measured the presence of tests, not whether those tests discriminated
the property they claimed to protect.

**A mutation that does not reproduce its defect proves nothing.** Across
this work, four mutations were found to pass against deliberately broken
code before being rewritten until they actually failed: a pre-read restored
without its original file position; a merge enabled on a method PyYAML
does not dispatch through for that construct; a performance ratio measured
where parse cost, not the mechanism under test, dominated the timing; and
an outcome assertion for a document that two independent paths both
happened to refuse, which could not tell those paths apart. Each is now
represented, instead, as a documented mutation-discrimination requirement
(see the Structural Change Preflight and
`tests/agent_review/test_profile_loader_v2_mutation_discrimination.py`).

## Where the architectural stop should have occurred

By the criteria now codified in
[`STRUCTURAL_CHANGE_PREFLIGHT.md`](STRUCTURAL_CHANGE_PREFLIGHT.md), the stop
signal was present after round 3 of `#236`'s seven adversarial rounds: three
consecutive findings landing on the same abstraction boundary (re-derived
parser semantics), with no material convergence in finding count round over
round. The work continued for four more rounds before the mechanism itself
was replaced, each round finding a *different* way the same re-derivation
was wrong — a repetition of finding class the preflight's STOP/REDESIGN
condition now names explicitly.

## Why a disposable spike was necessary

No amount of patching the node-graph walker could answer "is this the
right layer to write the rule at" — every fix to a re-derivation is still a
re-derivation, one abstraction level below the authority it is trying to
model. Only building the collision-observation mechanism outside the
production branch, as a throwaway comparison against the same adversarial
corpus, could establish that a *derivation* (observing the real
constructor) converged where a *re-derivation* (modeling the constructor)
kept finding new gaps.

## What was preserved

- the adversarial corpus (ambiguous, merge, malformed families);
- the safe-counterexample corpus, asserted as value equality against stock
  `yaml.safe_load` — the property that actually caught the over-refusal
  direction;
- typed input boundaries (reason-coded failures, never a raw exception
  escaping to a caller);
- the shared receipt-authority work from the same PR sequence;
- mutation tests that were rewritten until they genuinely discriminated;
- the PyYAML constructor-behaviour discoveries themselves (the `!!value`
  idiom, the enumerated non-`YAMLError` constructor failure families).

## What was discarded or superseded

- the bespoke node-graph ambiguity walker;
- the structural-surrogate machinery that existed only to sustain that
  walker (node-type rule tables, reconstructed ancestry);
- the manual key-equivalence rules the walker used to decide "same key";
- result-comparison as an ambiguity authority;
- the canonicalisation and JSON-projection machinery used to make that
  comparison possible;
- the `json.dumps`/`model_validate_json` contract-validation round-trip.

Everything in this list existed only to sustain a design that was itself
superseded; none of it is missing functionality that would need to be
rebuilt elsewhere.

> **DERIVE, DON'T RE-DERIVE.**
