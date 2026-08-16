# Structural Change Preflight

**Status:** `PREFLIGHT` — a reusable gate for future structural work, not a
record of any specific past PR. For the incident that motivated this
document, see
[`AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md`](AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md).

Answer every question below **before writing production code** for any
change that introduces or modifies a rule about the shape or meaning of
data another component already has authority over. Write the answers into
the PR body. "Unknown" is a valid answer to any question, and a blocking
one — it means the design is not ready to implement yet.

## 1. Property

- What external property is this change guaranteeing?
- How is that property observed, mechanically?
- What is the conservative disposition when it cannot be established?

## 2. Authority

- Which existing component, library, or module already owns this semantic
  rule?
- Is this change *deriving* its decision from that authority, or
  *reimplementing* the authority's own logic independently? (Example: a
  rule that re-derives, node type by node type, what a YAML parser's own
  constructors already decide is reimplementing, not deriving.)
- How many semantic authorities for this rule exist before this change, and
  how many after?

## 3. Language / capability decisions

- Which inputs or features are this change explicitly required to accept?
- Which are deliberately unsupported, and where is that decision written
  down?
- Is any language or capability decision still implicit — assumed by the
  code but not stated anywhere a reviewer can check it against?

## 4. Positive and negative corpus

- Which attacks or malformed inputs must this change reject?
- Which legal, safe inputs must continue to pass — and is that asserted as
  **equality** with the upstream authority's own output, not merely "does
  not raise"? (An adversarial corpus alone cannot detect over-rejection; a
  safe-counterexample corpus with an equality assertion is the only thing
  that does.)
- Is there parity testing against the upstream authority, wherever one
  exists?

## 5. Evidence and mutation discrimination

- Which test represents this property?
- Which mutation would remove the property, and has that mutation actually
  been run and observed to make the test fail? (A mutation test that has
  never been observed failing proves nothing — it may be non-discriminating
  by construction.)
- Which evidence class is justified: `deterministic_complete`,
  `finite_exhaustive`, `empirically_supported`, or `advisory_observation`?
  Do not claim a stronger class than the evidence supports.

## 6. Cross-layer assumptions

- Does any comment or implementation assume another layer "always",
  "never", "will reject", "guarantees", or "cannot" do something? (Grep the
  diff for these words.)
- For each such assumption: which authority or test actually proves it?
  An assumption with no test backing it is a gap, not a fact.

## 7. Snapshot and ownership

- Are all reads used by one decision taken from a single captured snapshot,
  or could two reads of the same nominal source disagree?
- Is another consumer independently reading or reinterpreting the same
  source, with its own copy of the rule?
- Is any rule copied into a second table, helper, or module instead of
  consumed directly from its authority?

## Mandatory architectural STOP / REDESIGN condition

Stop patching and escalate to a redesign the moment **any** of the
following occurs:

- three consecutive adversarial review rounds land on the same abstraction
  boundary;
- the finding count across rounds shows no material convergence;
- a fix falsifies an assumption a previous fix in the same slice relied on;
- a legal/safe input that previously passed begins failing;
- fixing a finding would require reproducing substantial internals of an
  upstream authority inside this change;
- a test that is supposed to guard a load-bearing property repeatedly fails
  to discriminate the defect it claims to catch.

When triggered, the sequence is:

```text
freeze patching
  → preserve exact HEAD evidence (do not lose the failing corpus/repro)
    → run a disposable architecture spike, outside the production branch
      → select a mechanism before any further production edit
```

A spike is disposable: its purpose is to answer "is this the right layer,"
not to become the merged implementation by accretion.

## Handoff / closure requirements

- Every "yes" answer above must name the test or authority that backs it,
  not just assert it.
- A PR that triggers the STOP condition must record which trigger fired and
  what the spike concluded, even if the final mechanism differs from every
  design considered during patching.
- Future structural PRs on this authority (target-pack `validate`,
  `conformance`, and later slices) must restate their answers to this
  preflight in their own PR body — reference this document, do not copy it.
