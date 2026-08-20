# Structural Change Preflight

## Authority-First Convergence Review

**Status:** `PREFLIGHT` — a reusable gate for future structural work, not a
record of any specific past PR. For the incident that motivated this
document, see
[`AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md`](AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md).

This document is the **single owner** of the criteria, thresholds and
procedure for `STOP / REDESIGN`, and now of the review method that surrounds
them — *Authority-First Convergence Review*. `PROJECT_OVERLAY.md` states that
ownership explicitly and refuses to restate the rules; nothing else in this
repository may define a second copy. The method below generalizes the
questions in §§1–7: those ask whether a change re-derives an authority; the
method asks, before that, **which proposition is being asserted and which
authority legitimately establishes it**.

The parts a machine can check are declared once in the
[normative registry](#normative-registry) at the end of this document and
verified by `tests/test_structural_change_preflight.py`. Prose here may
reference those entries; it must not restate them as a second list.

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

**Name the convergence boundaries before the review loop starts**, in the PR
body, drawn from `convergence_boundaries` in the normative registry. A
boundary named only after a finding lands is not a boundary; it is a
rationalization. The registry's list is the vocabulary, not a limit — a
change may declare a boundary of its own, provided it does so up front.

Stop patching and escalate to a redesign the moment **any** of the
following occurs:

- three consecutive adversarial review rounds land on the same abstraction
  boundary;
- a fresh material finding recurs inside a boundary already corrected once
  under the same abstraction, whatever the round count;
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

**The three-round count is process policy, not a theorem.** No argument here
establishes that three is the correct number for an arbitrary change; it is a
threshold this repository adopted after `#238`, where the signal was present
at round 3 and the work continued to round 7. Treat it as a tripwire that
must be honoured or *amended in this document*, never as a quantity to be
reasoned around in a PR body. `#238`'s round-5 spike records exactly that
failure mode: the rule applied selectively, with the convergence judgement
written only into the PR. If a change believes the threshold is wrong for its
situation, the correct move is to amend this file under review, not to
proceed past it.

When `STOP / REDESIGN` fires, ask these in order **before** considering any
further code:

1. Is the **subject** wrong — are we asserting about the wrong thing?
2. Is the asserted **relation** stronger than what the objective needs?
3. Is the **authority** insufficient, illegitimate, or absent?
4. Is the **representation** lossy for the decision being made?
5. Can the objective be satisfied by a **weaker valid claim**?
6. Does the **abstraction** itself need replacing?

Adding another leaf rule is not on this list. If the answer to 1–5 is "no"
and the abstraction is sound, the finding is probably an ordinary defect and
the STOP was mis-fired — say so explicitly rather than redesigning by reflex.

## The relation carrier, and why coercion is the recurring defect

Every claim this process governs is a relation, not a value:

```text
Π = <S, A, R, T, E>

S  exact subject          which object the claim is about
A  semantic authority     which source legitimately establishes R
R  asserted relation      what is being claimed about S
T  epoch                  which snapshot / lifecycle state it holds in
E  evidence               observation, coverage, and stated limitations
```

This is a reasoning model for humans and agents. It is deliberately **not** a
public schema, and nothing should serialize it merely because it is written
down here.

**No implicit semantic coercion.** `P ⇒ Q` only where an explicit, valid
composition rule `ρ : P → Q` exists and is named. Every entry in the registry's
`non_coercions` is a coercion this repository actually performed and paid for;
they are recorded so the same substitution is recognizable the next time it is
proposed. The general shape underneath all of them:

```text
Representation(x)  ⇏  Role(x)
```

A representation is evidence *about* a role only through an authority that
licenses the step. Absent that authority, the two are different propositions
that happen to look alike at the point of use.

## Authority-first topology

Identify the legitimate authority for `R` **before** designing how anything
consumes it. Where a change controls both sides, prefer:

```text
             Authority
             /       \
            v         v
        Consumer   Projection

Runtime        = Consumer(Authority)
View / docs    = Projection(Authority)
never            Documentation = Infer(RuntimeSource)
```

This is a preference about causal direction, **not** a claim that every system
must contain a registry. Legitimate outcomes of the authority question include:
reuse an existing authority; create one; weaken the claim; withdraw it; or
report `UNAVAILABLE`. Creating a new authority to rescue a claim the objective
never required is the failure mode, not the goal — and extending an existing
authority whose subject is different (a public install contract asked to carry
internal topology) is the same failure wearing a smaller diff.

A projection must never become an authority. If a build artifact starts
mediating a semantic decision, the direction has inverted.

## Earliest lossy boundary

For a decision relation `~R`, if

```text
x  !~R  y      and      F(x) == F(y)
```

then `F` is insufficient for that decision: it discards exactly what the
decision depends on. Repair `F` — the earliest boundary where the distinction
was lost. Do **not** compensate downstream with vocabulary scanners, an
ever-widening AST grammar, extra leaf conditionals, or heuristic
reconstruction; each of those re-derives, one layer below the authority, what
was already thrown away.

The dual obligation is equivalence invariance: where `x ~R y`, the decision
outcome must be materially the same under the declared assumptions. A repair
that starts distinguishing genuinely equivalent inputs has over-corrected.

## Finding validation, and the corrective cut

`ReviewerFinding != ValidatedFinding`. For each material finding, in order:

reproduce → establish materiality → identify the violated proposition →
locate the earliest boundary → search bounded siblings → define the defect
domain → select the corrective abstraction.

Patch after this classification, not before, whenever the finding permits it.
Several similar manifestations frequently belong to one defect class; fixing
them one at a time is how a review loop fails to converge.

Then choose the **smallest correction that restores the intended proposition
across the demonstrated defect domain** — not the smallest textual diff. A
minimal diff that leaves the abstraction false is a larger change deferred,
usually to the next review round.

## Causal mutation discrimination

§5 requires that a mutation has actually been observed to fail. That is
necessary and, where the property is load-bearing, not sufficient: also
establish that the **intended discriminator** produced the failure.

```text
Test_P(S) == GREEN
Test_P(M¬P(S)) == RED          necessary
Killed(M¬P) BY IntendedDiscriminator     also required when material
```

A mutant that dies to an unrelated short-circuit — an import error, a fixture
that never reached the assertion, a different test failing first — has
demonstrated nothing about `P`. `#238` found four such mutations that passed
against deliberately broken code and had to be rewritten before they
discriminated anything.

## Exact-subject evidence

Every qualifying result names the exact subject it supports. Keep these
distinct, because they are routinely conflated at merge time:

```text
source commit identity   content / tree identity   merge (squash) identity
lineage relation         forge role
```

A squash merge is the standing example:

```text
source_commit != squash_commit        AND        source_tree == squash_tree
```

Tree equality proves the merged **content** is the qualified content. It
proves nothing about commit identity or lineage, and `git compare` will
correctly report the two commits as diverged. Cite tree equality for what it
establishes and stop there.

## Epistemic status — two axes, not one ranking

Evidence class answers *how the evidence was obtained*; claim status answers
*what may now be asserted*. They are separate axes, and neither is a scalar
ranking of the other. The vocabulary is declared once in the registry:
`evidence_class` reuses the four terms §5 already established — no second
vocabulary is introduced here — and `claim_status` names what a reader may
rely on.

The registry's `forbidden_promotions` are the transitions this repository has
seen attempted or nearly attempted. In particular a clean external review is
`NON_REFUTED`: it is the absence of a found defect, not the presence of a
proof, and no number of clean reviews composes into one. `UNAVAILABLE` is a
real, reportable outcome — not a failure to try.

## Best supportable claim

```text
CandidateClaims(E, O) = { C | C is supportable from E  ∧  C satisfies objective O }
```

If a unique maximum exists under a defined semantic-strength relation, select
it. Do **not** assume one always exists: candidates are frequently
incomparable, and where several maximal claims survive, keep the set or
require an explicit decision rather than inventing a total order.

If `CandidateClaims(E, O)` is empty, the answer is `UNAVAILABLE`. Never
substitute the nearest supportable claim, a semantic guess, or a silently
strengthened one. Weakening has a floor: a claim weakened until it no longer
satisfies the objective is not a weaker claim, it is a vacuous one, and
`UNAVAILABLE` is the honest report.

## Authority non-escalation

Two different meanings of "authority" must never merge:

```text
semantic authority     which source establishes a proposition
operational authority  which grant permits a protected action
```

`technically qualified ⇏ authorized to merge / release / deploy`. A protected
transition requires its own grant, revalidated against live state immediately
before it is performed, however complete the technical evidence is.

## Process contract

**Before the change.** Live subject verified; proposition stated; semantic
authority identified; scope frozen; protected actions enumerated; convergence
boundaries named; discriminants identified.

**Per finding.** Reproduce; classify; locate the earliest failing boundary;
sibling-search inside the bounded envelope; choose leaf vs structural
correction; add a permanent witness; discriminate causally where material;
reconcile the evidence record.

**Before requesting a fresh review.** Exact HEAD fixed; focused tests; full
tests; mutation tests; allowlist and diff audited; no claim beyond what the
evidence supports; no protected transition performed by accident.

**After the review.** Clean → `NON_REFUTED`. First material finding →
validate and classify. Recurrence inside a frozen boundary → `STOP / REDESIGN`.

**Before a protected action.** Live TOCTOU revalidation; the exact evidence
subject still valid; issue-transition safety checked; an explicit grant held.

## Evidence corpus

The registry's `corpus` records the lineage this method was extracted from.
The negative entries are counterexamples: each converged on `STOP / REDESIGN`
after reconstructing a stronger semantic claim downstream of the authority
that could establish it. The positive entry, `#247`, established its
authority first and was the first in the sequence to pass adversarial review
clean on its first qualified head, with zero corrective commits after review.

That contrast is **positive empirical evidence consistent with the
authority-first hypothesis**. It is not a proof of it, and this document must
never be read as claiming otherwise: one favourable case against three
unfavourable ones is a strong prior for a method, not a theorem about
software. The scoped invariants in `#247` are `MECHANICALLY_VERIFIED`, its
selected mutants are `MUTATION_DISCRIMINATED`, its behaviour differential is
`EMPIRICALLY_SUPPORTED` over a declared finite corpus, and its clean review is
`NON_REFUTED`. None of those promote to `PROVED`.

## Normative registry

The machine-checkable content of this method, declared once. Prose above may
reference these entries; a second copy anywhere is a defect this document's
own rules forbid.

<!-- BEGIN NORMATIVE: convergence-review-registry-v1 -->
```json
{
  "format_id": "aiops.engineering.convergence-review-registry.v1",
  "convergence_boundaries": [
    "subject_identity",
    "relation_or_role_identity",
    "semantic_authority",
    "lifecycle_derivation",
    "representation_fidelity",
    "runtime_behavior_derivation",
    "evidence_qualification"
  ],
  "non_coercions": [
    {"from": "GitObjectExists(c)", "to": "CanonicalOnForge(c)"},
    {"from": "ObjectIdentity(c)", "to": "CanonicalRole(c)"},
    {"from": "ExposedAtAnchor(x)", "to": "Deferred(x)"},
    {"from": "SourceOccurrenceIdentity(x)", "to": "RuntimeRoleIdentity(x)"},
    {"from": "StaticBindingIdentity(x)", "to": "EffectiveBindingIdentity(x)"},
    {"from": "EvidenceIdentityMatch(e)", "to": "EvidenceQualification(e)"},
    {"from": "Representation(x)", "to": "Role(x)"}
  ],
  "evidence_class": [
    "deterministic_complete",
    "finite_exhaustive",
    "empirically_supported",
    "advisory_observation"
  ],
  "claim_status": [
    "DEFINED",
    "MECHANICALLY_VERIFIED",
    "MUTATION_DISCRIMINATED",
    "EMPIRICALLY_SUPPORTED",
    "NON_REFUTED",
    "REFUTED",
    "UNAVAILABLE",
    "PROVED"
  ],
  "forbidden_promotions": [
    {"from": "DEFINED", "to": "PROVED"},
    {"from": "NON_REFUTED", "to": "PROVED"},
    {"from": "EMPIRICALLY_SUPPORTED", "to": "PROVED"},
    {"from": "MECHANICALLY_VERIFIED", "to": "PROVED"},
    {"from": "MUTATION_DISCRIMINATED", "to": "PROVED"},
    {"from": "DEFINED", "to": "MECHANICALLY_VERIFIED"},
    {"from": "NON_REFUTED", "to": "MECHANICALLY_VERIFIED"},
    {"from": "EMPIRICALLY_SUPPORTED", "to": "MECHANICALLY_VERIFIED"}
  ],
  "discriminants": [
    {"id": "source_occurrence_vs_runtime_role",
     "a": "SourceOccurrenceIdentity", "b": "RuntimeRoleIdentity",
     "evidence": {"kind": "repository_path", "value": "tests/agent_review/test_target_pack_runtime_authority_v2.py"}},
    {"id": "object_existence_vs_forge_role",
     "a": "GitObjectExists", "b": "CanonicalOnForge",
     "evidence": {"kind": "repository_path", "value": "app/agent_review/target_pack_runtime_authority_v2.py"}},
    {"id": "subject_identity_vs_relation_identity",
     "a": "SubjectIdentityEquality", "b": "RelationIdentityEquality",
     "evidence": {"kind": "repository_path", "value": "docs/engineering/AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md"}},
    {"id": "reviewed_head_vs_corrective_head",
     "a": "ReviewedHead", "b": "LaterCorrectiveHead",
     "evidence": {"kind": "forge_record", "value": "PR 247 review names its reviewed commit explicitly"}},
    {"id": "squash_inequality_with_tree_equality",
     "a": "CommitIdentity", "b": "TreeIdentity",
     "evidence": {"kind": "forge_record", "value": "PR 247 source 75c80ab9997a40a4e770e9ec16df59527f618ad6 squash 99e8bb838e997dfd69cdc575b05ec235d5f8942d tree f35a46d97135ef4473907abd4edbeac9bad512b4"}},
    {"id": "intended_kill_vs_incidental_kill",
     "a": "KilledByIntendedDiscriminator", "b": "KilledByUnrelatedShortCircuit",
     "evidence": {"kind": "repository_path", "value": "tests/agent_review/test_profile_loader_v2_mutation_discrimination.py"}},
    {"id": "first_finding_vs_recurrence_in_boundary",
     "a": "FirstFindingInBoundary", "b": "RecurrenceAfterCorrection",
     "evidence": {"kind": "repository_path", "value": "docs/engineering/AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md"}},
    {"id": "best_claim_vs_no_supportable_claim",
     "a": "BestSupportableClaim", "b": "Unavailable",
     "evidence": {"kind": "repository_path", "value": "docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md"}},
    {"id": "clean_review_vs_proof",
     "a": "NonRefuted", "b": "Proved",
     "evidence": {"kind": "repository_path", "value": "docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md"}},
    {"id": "qualification_vs_operational_grant",
     "a": "SemanticAuthority", "b": "OperationalAuthority",
     "evidence": {"kind": "repository_path", "value": "docs/engineering/CAEM_CORE.md"}}
  ],
  "corpus": {
    "negative": [
      {"pr": 242, "converged_on": "representation_fidelity"},
      {"pr": 245, "converged_on": "representation_fidelity"},
      {"pr": 246, "converged_on": "runtime_behavior_derivation"}
    ],
    "positive": [
      {"pr": 247,
       "source_commit": "75c80ab9997a40a4e770e9ec16df59527f618ad6",
       "squash_commit": "99e8bb838e997dfd69cdc575b05ec235d5f8942d",
       "tree": "f35a46d97135ef4473907abd4edbeac9bad512b4",
       "first_fresh_review_findings": 0,
       "corrective_commits_after_review": 0,
       "claim_status": "NON_REFUTED"}
    ]
  }
}
```
<!-- END NORMATIVE: convergence-review-registry-v1 -->

## Handoff / closure requirements

- Every "yes" answer above must name the test or authority that backs it,
  not just assert it.
- A PR that triggers the STOP condition must record which trigger fired and
  what the spike concluded, even if the final mechanism differs from every
  design considered during patching.
- Future structural PRs on this authority (target-pack `validate`,
  `conformance`, and later slices) must restate their answers to this
  preflight in their own PR body — reference this document, do not copy it.
