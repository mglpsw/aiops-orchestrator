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
- Which evidence class is justified? Select it from `evidence_classes` in the
  [normative registry](#normative-registry), which owns that vocabulary. Do
  not claim a stronger class than the evidence supports, and do not restate
  the list here — a second copy in prose is a second authority, and it drifts
  in exactly the direction nobody is watching.

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

## Epistemic classification — typed predicates, not a lifecycle

The registry's `epistemic_predicates` are **not states of one lifecycle**, and
there is deliberately no transition relation between them. Each is a typed
predicate over a proposition, admitted by its own `basis`; several may hold at
once whenever they are logically compatible.

That is not a stylistic preference — a single-valued status field was tried
here and falsified by this repository's own evidence. `#247` is simultaneously
mechanically verified, mutation discriminated, empirically supported and
non-refuted. One field can carry one of those, so three were silently dropped
the moment the record was written.

The general algebra of these categories — whether they form an order, a
partial order, a product, or simply independent predicates — is **an open
formal question and is intentionally not settled here**. What this document
fixes is narrower and sufficient for process use: the predicates are typed,
they may co-hold, and none of them is a step toward another.

A clean external review is `NON_REFUTED`: the absence of a found defect, not
the presence of a proof, and no number of clean reviews composes into one.
`UNAVAILABLE` is a real, reportable outcome — not a failure to try, and not a
state waiting to be upgraded.

### Proof is admitted, never reached

`PROVED` is not the top of a ladder. It has its own admission rule, stated
positively in the registry's `proof_admission`: it may be asserted only from
explicit proof evidence carrying, at minimum, the proposition, its declared
domain, the assumptions, and the derivation basis.

Positive admission replaces the deny-list an earlier revision used, and the
reason is structural rather than aesthetic. A deny-list over eight categories
has 56 ordered pairs; that revision enumerated 8, leaving 48 implicitly
permitted, and review immediately found two of the gaps. "What makes `PROVED`
admissible" has one answer; "which transitions are forbidden" has as many
holes as nobody happens to be looking at.

Every other basis — bounded mechanical verification, causal mutation
discrimination, a declared empirical corpus, bounded adversarial search — is
declared insufficient for proof, individually and by accumulation. Adding a
new predicate therefore cannot silently open a new route to `PROVED`.

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

## Method evidence — explanatory, and deliberately non-normative

```text
MethodAuthority  !=  MethodEvidence
```

Everything in this section is **evidence, not method**. It records why the
rules above were adopted; it does not define them. The registry carries no PR
number, commit SHA, review outcome or other historical fact, so the method
stays semantically complete if every locator below becomes unavailable. An
earlier revision put these facts inside the normative registry, where a
`first_fresh_review_findings: 0` field sat with nothing able to contradict it.

**Negative corpus.** `#242`, `#245` and `#246` each converged on
`STOP / REDESIGN` after reconstructing a stronger semantic claim downstream of
the authority that could establish it — `#242`/`#245` on representation
fidelity, `#246` on deriving runtime behaviour from static source.

**Positive control.** `#247` established its authority first and was the first
in the sequence to pass adversarial review clean on its first qualified head,
with no corrective commits after review. Source `75c80ab999`, squashed as
`99e8bb838e`, trees equal at `f35a46d971`.

Of those identities, only the **tree equality is locally checkable** — both
commit objects are present, and `git` can compare their trees. The review
outcome is a **forge observation**: it is not derivable from anything in this
repository, and the offline process test deliberately does not revalidate it.
Asserting `findings == 0` locally would be two copies of a number agreeing:

```text
ManualValue(0)  ∧  TestExpects(0)   ⇏   ForgeReviewFindings(0)
```

That contrast is **positive empirical evidence consistent with the
authority-first hypothesis** — one favourable case against three unfavourable
ones is a strong prior for a method, not a theorem about software. `#247`
carries `MECHANICALLY_VERIFIED`, `MUTATION_DISCRIMINATED`,
`EMPIRICALLY_SUPPORTED` and `NON_REFUTED` **simultaneously**, which is
precisely the observation that retired the single-status model. None of them
is a step toward `PROVED`.

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
    "evidence_qualification",
    "method_authority_vs_method_evidence",
    "epistemic_representation",
    "claimed_vs_observed_verification_domain"
  ],
  "non_coercions": [
    {"from": "GitObjectExists(c)", "to": "CanonicalOnForge(c)"},
    {"from": "ObjectIdentity(c)", "to": "CanonicalRole(c)"},
    {"from": "ExposedAtAnchor(x)", "to": "Deferred(x)"},
    {"from": "SourceOccurrenceIdentity(x)", "to": "RuntimeRoleIdentity(x)"},
    {"from": "StaticBindingIdentity(x)", "to": "EffectiveBindingIdentity(x)"},
    {"from": "EvidenceIdentityMatch(e)", "to": "EvidenceQualification(e)"},
    {"from": "LocalVerification(f)", "to": "ForgeVerification(f)"},
    {"from": "Representation(x)", "to": "Role(x)"}
  ],
  "evidence_classes": [
    "deterministic_complete",
    "finite_exhaustive",
    "empirically_supported",
    "advisory_observation"
  ],
  "epistemic_predicates": [
    {"id": "DEFINED", "basis": "definition"},
    {"id": "MECHANICALLY_VERIFIED", "basis": "bounded_mechanical_verification"},
    {"id": "MUTATION_DISCRIMINATED", "basis": "causal_mutation_discrimination"},
    {"id": "EMPIRICALLY_SUPPORTED", "basis": "declared_empirical_corpus"},
    {"id": "NON_REFUTED", "basis": "bounded_adversarial_search"},
    {"id": "REFUTED", "basis": "validated_counterexample"},
    {"id": "UNAVAILABLE", "basis": "no_objective_satisfying_supportable_claim"},
    {"id": "PROVED", "basis": "explicit_proof"}
  ],
  "proof_admission": {
    "predicate": "PROVED",
    "basis": "explicit_proof",
    "required_evidence_fields": [
      "proposition",
      "declared_domain",
      "assumptions",
      "derivation_basis"
    ],
    "insufficient_bases": [
      "definition",
      "bounded_mechanical_verification",
      "causal_mutation_discrimination",
      "declared_empirical_corpus",
      "bounded_adversarial_search",
      "validated_counterexample",
      "no_objective_satisfying_supportable_claim"
    ]
  },
  "discriminants": [
    {"id": "source_occurrence_vs_runtime_role", "a": "SourceOccurrenceIdentity", "b": "RuntimeRoleIdentity"},
    {"id": "object_existence_vs_forge_role", "a": "GitObjectExists", "b": "CanonicalOnForge"},
    {"id": "subject_identity_vs_relation_identity", "a": "SubjectIdentityEquality", "b": "RelationIdentityEquality"},
    {"id": "reviewed_head_vs_corrective_head", "a": "ReviewedHead", "b": "LaterCorrectiveHead"},
    {"id": "commit_identity_vs_tree_identity", "a": "CommitIdentity", "b": "TreeIdentity"},
    {"id": "intended_kill_vs_incidental_kill", "a": "KilledByIntendedDiscriminator", "b": "KilledByUnrelatedShortCircuit"},
    {"id": "first_finding_vs_recurrence_in_boundary", "a": "FirstFindingInBoundary", "b": "RecurrenceAfterCorrection"},
    {"id": "best_claim_vs_no_supportable_claim", "a": "BestSupportableClaim", "b": "Unavailable"},
    {"id": "non_refuted_vs_proved", "a": "NonRefuted", "b": "Proved"},
    {"id": "semantic_vs_operational_authority", "a": "SemanticAuthority", "b": "OperationalAuthority"},
    {"id": "local_vs_forge_verification", "a": "LocalVerification", "b": "ForgeVerification"},
    {"id": "method_authority_vs_method_evidence", "a": "MethodAuthority", "b": "MethodEvidence"}
  ]
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
