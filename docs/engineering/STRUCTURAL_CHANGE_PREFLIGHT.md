# Structural Change Preflight

**Status:** `PREFLIGHT` — a reusable gate for future structural work. The method
it carries is *Authority-First Convergence Review*.

A change governed by this file answers to the rules as written here.

Operational authority is a different subject and is not owned here. See
§"Authority non-escalation".

The questions in §§1–7 cover the property a change guarantees, the authority
behind it, and how both are evidenced. The method asks, before any of them,
**which proposition is being asserted and which authority legitimately
establishes it**.

The rules below are written to stand without the cases that produced them: a
method whose meaning depends on its motivating cases has made those cases part
of its authority without admitting it. Patterns described here are illustration
and carry no authority of their own.

**This method is carried in prose, and no second artifact is normative for its
rules**, by the **circularity rule**: a check whose expected value is copied
from the authority it is supposed to check is a second copy of that authority,
not a check on it. A test over these rules could not distinguish corruption from
a legitimate amendment, because amending document and test together leaves the
check green. The rule reaches an artifact's own normative content. It does not
reach a gate between a generator and its generated view, where the oracle is the
generator.

Where an obligation here could be decided by a check, say so in the change, and
name the check if one exists. Being decidable is not being decided —
`Verifiable(x) ⇏ Enforced(x)` — and what a codebase actually checks is a
property of that codebase. Ask it there.

Answer the questions in §§1–7 **before writing production code** for any change
that introduces or modifies a rule about the shape or meaning of data another
component already has authority over. The recurrence questions later in this
document belong to the review loop and are answered at the end of each round.
Write the answers into
the PR body. "Unknown" is a valid answer to any of the §§1–7 questions, and a
blocking one — it means the design is not ready to implement yet. The recurrence
questions answer on a different footing, stated where they are: each load-bearing
one reports that no candidate formed, that a recurrence was admitted, that a
formed candidate was established out of scope, or that a formed candidate is
held for investigation, and the supporting one may be left undecided. A hold is
recorded as neither a "yes" nor a "no".

Uncertainty arises at several distinct stages of this document, and the subjects
must not merge. Three are named where they arise, and they are not a census:

```text
preflight Unknown         a §§1–7 question nobody can answer yet
                          → the design is not ready to implement

unresolved bearing        a finding is factually established, and whether it
                          bears on the claim is not
                          → the fact stands; no validated finding for that
                            claim; the claim may not rest on its irrelevance

recurrence hold           a candidate did form and its qualification is open
                          → the convergence claim is blocked on that candidate
```

Different subjects, different process contexts. Resolving any one of them
establishes nothing about the others, and none is another's answer.

## 1. Property

- What external property is this change guaranteeing?
- How is that property observed, mechanically?
- What is the conservative disposition when it cannot be established?

## 2. Authority

- Which existing component, library, or module already owns this semantic
  rule?
- Is this change *deriving* its decision from that authority, or
  *reimplementing* the authority's own logic independently? Deriving asks the
  authority and uses the answer. Reimplementing arrives at the answer by a
  parallel route — case by case, rule by rule — so that the two can disagree,
  and when they do, the authority is not the authority. The test is whether a
  change in the upstream authority's behaviour would change this code's
  decision automatically or silently leave it stale.
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
  not raise"? (A rejection-only corpus cannot detect over-rejection at all.
  A safe-counterexample corpus with an equality assertion detects it over the
  cases it actually exercises — which is a bound to declare, not a guarantee to
  assume.)
- Is there parity testing against the upstream authority, wherever one
  exists?

## 5. Evidence and mutation discrimination

- Which test represents this property?
- Which mutation would remove the property, and has that mutation actually
  been run and observed to make the test fail? (A mutation test that has
  never been observed failing proves nothing — it may be non-discriminating
  by construction.)
- Which epistemic predicates hold, drawn from §"Epistemic classification",
  which owns that vocabulary? Do not restate the list here, do not enumerate a
  second one, and do not claim a predicate the evidence does not admit. One
  subject gets one closed vocabulary, in one place.

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

**Name the convergence boundaries in the PR body before the review loop
starts.** A starting vocabulary: subject identity; relation or role identity;
semantic authority; authority versus evidence; representation fidelity;
epistemic representation; lifecycle derivation; runtime-behaviour derivation;
evidence qualification; claimed versus observed verification domain;
conformance-oracle admissibility. That is a vocabulary, not a limit and not a
census — declare a boundary of your own if the change needs one. Review uncovers
boundaries nobody could have named in advance; naming one late is expected, and
the finding that revealed it is its first occurrence.

**A round is one review of one head of the change.** Reviews of the same head
are the same round however many reviewers produce them; the next *reviewed* head
is the next round. A head nobody reviewed is not a round — otherwise pushing
commits would manufacture the rounds the questions count, which is the same
defeat closed below for the base, on the side the author controls more directly.

The base is deliberately not part of that unit. A base that advances underneath
an unchanged head does change what would land, and it stales evidence
accordingly — §"Exact-subject evidence" governs that, and a qualifying result
names the exact subject it supports. But it is not a new review, and counting it
as a new round would let ordinary traffic on the base manufacture the rounds the
questions count.

**A review loop is the sequence of rounds on one change**, from its first review
to the point where it lands or is abandoned. Redesign does not start a new one:
the point is to see across the redesigns.

### The recurrence conditions are answered, not computed

Recurrence is established by evidence that a correction declared in advance did
not do what it was declared to do. It is not a judgement about where findings
keep landing, and it is not anyone's view that two defects are alike — an earlier
draft made it both, and both made the measurement a matter of who was asked. Nor
is it a predicate over the author's own records alone, which would make the
measured party the source of the measurement. What carries it is the pair the
author cannot tune after the fact and no reviewer can assert into being: an
obligation fixed before the fact — the proposition a correction undertook to
restore, over the ground its finding had demonstrated — and later admissible
evidence that the same proposition is violated again inside that ground.

Four things are distinct here, and collapsing any adjacent pair is what has
repeatedly broken this section:

```text
ReviewerObservation      what someone reports — advisory, whatever its source
      ↓ factual qualification
FindingEstablished(f)    reproduced, violated proposition identified, defect
                         domain demonstrated — the defect is real
      ↓ decision-relevance, established separately and never derived from the above
MaterialToClaim(f, C)    whether that established finding bears on claim C
      ↓ both established, and only then
ValidatedFinding(f, C)   shorthand for the pair, not a third truth-maker
      ↓
RecurrenceCandidate      a ValidatedFinding(f2, C) on the subject a declared
                         correction produced, that correction answering an earlier
                         ValidatedFinding(f1, C) — same claim, structural, not a
                         similarity judgement
      ↓
RecurrenceAdmission   evidence establishes that the proposition the correction
                      meant to restore is validly violated again on the subject
                      it produced, inside the ground it answered for
      ↓
STOP / REDESIGN       policy over admitted recurrence, not over observations
```

```text
ValidatedFinding(f, C)  =  FindingEstablished(f)  ∧  Established(MaterialToClaim(f, C))
```

`ValidatedFinding` is a shorthand for both predicates being in hand, and nothing
more. It establishes no truth of its own: `FindingEstablished` owns whether the
defect is real, `MaterialToClaim` owns whether it bears on `C`, and neither is
derived from the other. Note that it now carries a claim — there is no
`ValidatedFinding(f)` unqualified, because there is no materiality without a
decision to be material to.

Collapsing the first two makes a reviewer's raw output a process decision.
Collapsing the last two makes the party the rule binds the party who decides
whether it binds them. Collapsing fact into relevance — which an earlier revision
did, by putting materiality inside the factual predicate — lets whoever answers
*"does this matter?"* control which facts the method can ever see. All three have
been tried here and all three failed.

**Lifecycle disposition sits on a different axis, and does not reach backwards.**
A established finding may later be dispositioned `fixed`, `superseded`, or
`out_of_scope`. New evidence that contradicts an earlier `VALID` is not a
lifecycle event at all: it records a later `FindingValidationOutcome(f)` at its
own epoch, on the factual axis, and does not by itself change
`LifecycleDisposition(f)`. Neither reaches back into what was established at
the time.

That axis is `LifecycleDisposition(f)`, and it is **not** the factual record of
whether the finding validated. That record is `FindingValidationOutcome(f)` —
`VALID`, `INVALID`, `PARTIAL` or `UNAVAILABLE_TO_VALIDATE` — owned by §"Finding
validation, and the corrective cut". Two axes, two identities, and neither
vocabulary may be written into the other's place:

```text
FindingValidationOutcome(f)   what the evidence showed, at the epoch it showed it
LifecycleDisposition(f)       what later happened to the work on f

FindingValidationOutcome(f)   ⇏   LifecycleDisposition(f)
LifecycleDisposition(f)       ⇏   FindingValidationOutcome(f)
```

A finding is routinely both at once — `VALID` on the factual axis and `fixed` on
the lifecycle axis — and a record that keeps only one of those has lost
information the method needs. Where this document says "record a disposition",
read which axis the surrounding section owns; where it says either name, that is
the axis meant. Two histories are kept, and they are not one record:

```text
ValidationHistory(f, C)         f was factually established and its bearing on C
                                was established, both at their epochs — facts
                                about those epochs, and about C. It records no
                                materiality to any other claim

RecurrenceAdmissionHistory(c)   candidate c qualified under the rule below at the
                                epoch it qualified — a fact about that epoch

LifecycleDisposition(f)  erases neither
```

A finding is never "admitted": findings are validated, candidates are admitted,
and there is no `FindingAdmission` here or anywhere below. New evidence that
contradicts `f1` afterwards records a later `FindingValidationOutcome(f1)` at
its own epoch; it does not, on that basis alone, change `f1`'s lifecycle
disposition, and it leaves standing both the validation that held at `f1`'s
epoch and any recurrence admitted on the strength of it.
`FinalState != History` throughout — a later state never rewrites the epoch it
succeeded.

That single property is what keeps the author from deciding, *after the fact*,
whether the rule fires. It costs nothing — the lifecycle record is kept as
usual — and it closes that defeat, because recurrence counts admissions, which
are facts about what was established at a time, not lifecycle states that can be
revised afterwards.

Be exact about the bound, because an earlier revision said this removed the
defeat "entirely" and it does not. The rules in this section govern what happens
**once a finding has admissibly reached the relation**. They do not establish
that the set of findings reaching it is complete, nor that the gate admitting
findings into it is itself beyond the reach of the party the rule constrains.
That upstream question is materiality, it is a different subject, and
§"Materiality is a relation, not a property" states how far it is currently
settled and where it is not.

**Candidate formation runs off a declared correction, not off a boundary.** A
`RecurrenceCandidate` exists when, and only when, all of the following hold:

```text
ValidatedFinding(f1, C) on S1            f1 is factually established, among its
                                         predicates the proposition P it violates,
                                         AND its bearing on this loop's claim C is
                                         established — a finding whose materiality
                                         is unresolved has not met this row
CorrectionAttempt(c) answering f1        declared before the review that follows
                                         it, naming its before/after subjects,
                                         the corrective abstraction, and the
                                         probes it plans to run
CorrectionObligation(c) = (P, Δ1)        not a free choice, and not the whole of
                                         P: an attempt that declares it answers
                                         f1 is an attempt to restore what f1 was
                                         validated as violating, across the
                                         defect domain f1 demonstrated. Both
                                         components are read off f1. An author
                                         may decline to answer f1; what they may
                                         not do is claim to answer it while
                                         aiming at something else, or while
                                         aiming at more or less ground than f1
                                         demonstrated
Δ1 = PreAttemptSnapshot(Δ(f1), epoch(c)) the state of f1's demonstrated defect
                                         domain immediately before c is recorded
                                         — see below
before_subject(c) is S1 or descends      the attempt answers f1 and sits on the
                  from it                same line of development
after_subject(c)  = S2                   the attempt produced the subject that
                                         was then reviewed
FreshReview(r2) on S2, a later subject
ValidatedFinding(f2, C) on S2            f2 met them too, against the same claim
                                         C — if the claim itself changed
                                         materially between the two findings, that
                                         is a different subject and this document
                                         does not settle it here
Subject(f2) = Subject(r2) = S2           and f2 came out of that review of that
                                         subject, not out of a report against an
                                         earlier one
ReviewEpoch(f1) < epoch(c) < ReviewEpoch(f2)
```

Nothing in that list is a similarity judgement and nothing in it is anyone's
assignment. Every row is a subject relation, an epoch relation, a finding-level
predicate already established under §"Finding validation, and the corrective
cut", or a record the author wrote before the finding that would test it existed.
A candidate is structurally present or it is not, and asking a second person does
not change which.

**`CorrectionObligation(c)` is derived, not declared.** Both components are read
off `f1`, and their epochs differ. `P` is fixed at `f1`'s validation — the
normative contract does not move between then and `c`. `Δ1` is not fixed there:
`f1`'s demonstrated domain may keep growing while the review loop continues to
investigate it, and `Δ1` takes whatever it has grown to by `epoch(c)`, not what it
was at validation. §"Canonical epoch of Δ1" below states the one rule this
document uses for that snapshot. The author chooses whether to answer `f1` and
how; they do not get to choose what answering it would mean, nor how much ground
it would cover.

**`f2` must belong to the review of the later subject, and the identity is the
binding — not the ordering.** Without it, a report against the *old* subject,
validated at any later date, satisfies every other condition. That is the
counterexample the binding exists to exclude:

```text
f1 on S1  →  correction produces S2  →  r2 reviews S2 cleanly
          →  a stale report against S1 is validated afterwards
          →  mandatory redesign, though the correction worked
```

A correction that succeeded on the subject it was made for cannot be turned into
a recurrence by a finding about the subject it replaced. Epoch ordering alone
does not exclude this, because the stale finding really is later in time; only
subject identity does.

A formed candidate claims one thing: a declared correction ran between two
validated findings on ordered subjects, against the same claim, and what the
later finding was validated against belongs to the next step. It is not a recurrence and fires nothing.
Formation is deliberately cheap, because the expensive question — is the
proposition the correction meant to restore violated again? — is where evidence
is needed, and where it can be held open rather than answered by default in
whichever direction the missing information happens to favour.

**§"Finding validation, and the corrective cut" owns `Δ(f)`.** What a
manifestation is inside it, outside it, or undetermined about; who may set its
extent — nobody does, it is demonstrated, never declared; how it grows. That
section states all of it, and it is not restated here. What this section adds is
narrow: a single rule for which state of `Δ(f1)` a given `c` is bound to.

**Canonical epoch of `Δ1`.**

```text
Δ1 = PreAttemptSnapshot( Δ(f1), epoch(c) )
```

`Δ1` is the state of `f1`'s demonstrated defect domain at the instant immediately
before `c` is recorded — not at `f1`'s validation, and not at any review epoch in
between. Domain evidence can keep accumulating throughout the loop: further
bounded sibling search between `f1`'s validation and `c` can demonstrate more of
`Δ(f1)`, and all of it belongs to `Δ1` once demonstrated before `c` exists. Only
`c`'s own epoch draws the line. `c` freezes that snapshot for its own obligation
and cannot move it in either direction: it may not narrow the ground it answers
for, and it does not acquire ground demonstrated only after it was written.

```text
CurrentDomainKnowledge(f1, t2)   ≠   HistoricalCorrectionDomain(c)
```

If later evidence — after `c` — shows the domain was wider than anything
demonstrated before `c`, current knowledge grows and `Δ1` does not. `c` promised
what had been demonstrated by its own epoch; it did not promise ground discovered
afterwards. That later discovery may be strong evidence the abstraction was drawn
too narrowly, but it reaches `c` only through some other relation legitimately
established, never by reading the present record back into the past. This is the
same `FinalState != History` property the lifecycle-disposition rule relies on.

**Admission is by evidence, not by anyone's say-so.** A formed candidate is
admitted when, and only when, the correction barrier holds across it, and one
derived relation is what establishes that barrier:

```text
EstablishedCorrectionFailure(c, f2)   ViolatedProposition(f2) = P
                                    ∧ the concrete validated violating witness
                                      behind f2 is established to lie in Δ1
                                      — what c was obliged to restore is violated
                                      again, inside the ground it answered for,
                                      on the exact after-subject it produced
```

This introduces no authority, no record kind and no entity. It is derived, each
time, from predicates that already exist and already have named authorities: the
proposition each finding was validated as violating, which comes from the
normative contract the change is measured against; and the domain each finding
demonstrated, which comes from its own reproducer, locus and sibling search. All
were fixed by their own findings before this relation was asked about.

**Both conjuncts are needed, and the second is what a broad proposition makes
necessary.** A normative proposition can be wide — "the parser preserves all
accepted YAML semantics" is one proposition and covers a great deal of unrelated
ground. Matching on the proposition alone would make any two defects under a wide
enough rule a recurrence of each other, which is the same over-merge that
disqualified boundary, relocated onto `P`. The domain conjunct is what keeps the
relation attached to the obligation `c` actually took on.

```text
c answers f1: proposition P, demonstrated domain Δ1
f2 is validated on S2 as violating P
f2's violating witness is established to lie in Δ1
      →  what c undertook to restore is violated again inside that ground
      →  EstablishedCorrectionFailure(c, f2)
```

Note what is **not** required: `Δ(f2) = Δ1`. `f2` may demonstrate a domain of its
own that is larger, smaller or differently shaped; that is a fact about `f2`. All
that matters here is whether the concrete violating witness behind `f2` — the one
its factual establishment rests on — lands inside the ground `c` answered for.

**The relation is reachable, not logically empty — nothing more is claimed.**
Non-vacuity here means `∃ f2 : EstablishedCorrectionFailure(c, f2)`, not that the
relation extends to manifestations nobody ever demonstrated. Two cases already
present in this document's own vocabulary suffice, and neither needs anything new:

```text
same manifestation
  m ∈ Δ1, demonstrated by f1's own reproducer
  c attempts to restore P over Δ1
  f2 on S2 reproduces the same P violation through m
    →  witness(f2) = m ∈ Δ1
    →  EstablishedCorrectionFailure(c, f2)

distinct, both pre-demonstrated
  f1's manifestation is m1; bounded sibling search before c also reproduces
  m2, so both m1 ∈ Δ1 and m2 ∈ Δ1 are demonstrated before c exists
  c attempts to restore P over Δ1 = {m1, m2}
  f2 on S2 reproduces P through m2 — a manifestation different from f1's own,
  but one the pre-c evidence had already placed in Δ1
    →  witness(f2) = m2 ∈ Δ1
    →  EstablishedCorrectionFailure(c, f2)
```

The second case is what rules out a causal-identity or exact-manifestation
requirement: `f2` need not reproduce `f1`'s own witness, only one already inside
the historical domain. It does not, and is not offered to, prove anything about a
manifestation `Δ(f1)`'s evidence never touched.

**An unseen manifestation stays `UNKNOWN`, and nothing here changes that.** For a
witness `u` no pre-`c` evidence ever placed in `Δ1`:

```text
u not previously demonstrated inside Δ1   ⇏   u ∈ Δ1
u not previously demonstrated inside Δ1   ⇏   u ∉ Δ1
                                           →   membership(u, Δ1) = UNKNOWN
```

Finite sampling never closes this gap by itself. Observing a defect on every
member of some finite set and absent otherwise is `EMPIRICALLY_SUPPORTED` over
that set, per §"Epistemic classification" — it is not a derivation that some
condition is the domain's true boundary, and treating it as one risks exactly what
that section already warns against: a hidden confound shared by every sampled
witness, later absent from an unrelated defect that happens to satisfy the same
finite pattern. No inference from sample to condition is licensed here, by an
author, a reviewer, or a model. Where some other legitimate basis — a formal
derivation, a mechanized contract, an authoritative exhaustive enumeration —
genuinely establishes that `u ∈ Δ1`, that basis is consumed like any other
established predicate. This document invents no such mechanism and promises none;
absent one, `UNKNOWN` is the honest and complete answer.

What may **not** establish it is resemblance of any kind. "The same abstraction
is wrong", "these look like one defect", "this is the same approach" — none is
the relation, whoever says it and however many agree. Nor may a plan establish
it: see §"Probes are a plan, not a verdict" below.

**What is precommitted is the answer relation, not the evidence plan.** Fixed at
`epoch(c)`, before the review that produces `f2`, is that `c` claims to answer
`f1` — and therefore both what it aims at, `P`, and how far that reaches, `Δ1`.
That is what cannot be rewritten once `f2` lands. An author who waits for `f2` and then says the earlier patch was
aimed at something else is redescribing a precommitment, and the redescription
does not participate.

The subject relations carry as much weight as the epochs. An attempt whose
after-subject is not the one reviewed is an attempt at something else, however
well its dates line up; an attempt whose before-subject is unrelated to `S1` is
not answering `f1`. Requiring `before_subject(c)` to *descend from* `S1` rather
than equal it leaves room for the ordinary case where other work lands between
the finding and the attempt.

**Probes are a plan, not a verdict.** A `CorrectionAttempt` is expected to say
which probes, witnesses or discriminants it intends to run. Recording them is
good practice — it makes the attempt reviewable, guides the sibling search, and
is exactly the mutation-discrimination discipline §5 asks for. What they do
**not** do is decide anything here:

```text
every planned probe passes  →  those probes passed
                            ⇏  the correction succeeded
                            ⇏  the barrier is out of scope or cleared

a planned probe fails       →  that probe failed
                            ⇏  a recurrence
                            ⇏  anything, until it is carried into a
                               ValidatedFinding under the rules above
```

An earlier revision made this set decisive in both directions, and it was wrong
in both. The author writes the set, so a narrow one could clear a correction
whose proposition was still violated by a manifestation nobody planned to probe,
and a broad one could turn any unrelated failure into a mandatory redesign. A
set the measured party composes cannot carry a mandatory transition, and the
repair is not to certify the set — no `SoundD`, `CompleteD` or `ApprovedD` is
introduced, and certifying it would need exactly the completeness oracle refused
elsewhere. The repair is that the set stops deciding.

The barrier is established, out of scope for this attempt, or unsettled:

```text
established     ViolatedProposition(f2) = P, and f2's violating witness is
                established to lie in Δ1
                →  the candidate is admitted

out of scope    f2 was validated against a different proposition; or the same
                proposition, with its witness established to lie outside Δ1
                →  this is not a recurrence of c. It is a finding in its own
                   right, with whatever obligations that carries, and it may be
                   strong evidence that the abstraction was drawn too narrowly —
                   but it does not falsify an obligation c never took on. The
                   round reports this as `candidate out of scope`, which is
                   neither a hold nor `no candidate formed`

unsettled       the proposition f2 violates could not be established, or its
                identity with P is genuinely contested; or the proposition
                matches but membership of f2's witness in Δ1 is undetermined
                →  HOLD_FOR_INVESTIGATION
```

The third row covers the case a wide proposition makes common: `P` matches, and
whether the witness sits inside the ground `c` answered for was never
established either way. That is `UNKNOWN`, and it stays `UNKNOWN` — it does not
become "outside the domain", it does not become `NO_CANDIDATE`, and it does not
become a correction that held.

Note what the middle row is and is not. It says this attempt is not what `f2`
reports on; it does not say the attempt succeeded, and nothing here licenses
reading it that way. A correction is not declared to have worked by this
document at all. And the absence of a validated finding against `P` is weaker
evidence than it looks: it may mean no defect was established, or that one was
established and shown not to bear on this claim, or that one was established and
its bearing was never settled at all. Only the first is "no defect found", and
§"Epistemic classification" already refuses to treat even that as proof. `NON_REFUTED` within a declared scope is the most a clean review yields,
and it is not a clearance certificate for `c`.

Keep all three rows distinct from the negative this document refuses elsewhere.
"No correction was attempted" is a claim about everything that did not happen
over a whole interval, and it has no legitimate source here. None of the rows
above reaches it.

`HOLD_FOR_INVESTIGATION` is not a verdict about the correction. It does not say a
recurrence occurred, does not say none did, does not say a correction was
attempted and does not say none was. It says the record does not settle the
question. The state is named for what remains available — investigation — and
deliberately not for an adjudicator: nothing here appoints anyone to convert an
unresolved question into a resolved one, and a name promising a resolver this
document declines to define would promise an exit it does not supply.

What a hold suspends, and what it does not:

```text
suspends    the convergence claim, and any promotion resting on that claim
preserves   the candidate, which stands and carries into later rounds, and the
            evidence and the epoch as they were
does not    fire the STOP / REDESIGN trigger, or the freeze-preserve-spike
            sequence that is reserved for an admitted recurrence
does not    freeze the branch, forbid further work, or assert anything at all
            about the correction or its domain
```

A hold suspends a decision, not a repository. Only the decisions that actually
rest on the unresolved predicate are suspended; work the predicate does not bear
on continues, and continues under a candidate that has not gone away.

**What a hold invites is investigation.** An unresolved predicate has a shape:
something specific could not be established from its declared authority. Name
that, and name what would settle it:

```text
Unknown(P)  →  identify MissingBasis(P) — what the record does not contain
            →  identify what admissible evidence would establish it
            →  derive the questions that evidence would have to answer
            →  investigate within the scope and authority already held
```

If later evidence legitimately establishes `P`, current knowledge changes and the
historical unknown does not. `Unknown(P)` at `t1` stays a true fact about `t1`
after `P` is established at `t2`, and the record is not rewritten as though
`t2`'s evidence had been available at `t1`.

If admissible investigation is exhausted and `P` is still unresolved, `P` stays
unresolved. There is no state to promote it to and nobody appointed to promote
it. What is left is a decision about the *work* — continue investigating, defer
the claim, abandon it, redesign, or pursue a demonstrably different claim — and
that decision belongs to the operational authority described in §"Authority
non-escalation".

What is **not** on that list is promoting the same claim while it still depends
on `P`. A disposition may decide what happens to the work; it cannot supply the
basis the claim is missing, and §"Convergence is a claim" governs that
separately. An earlier revision of this document did offer "permit some further
transition" as an open-ended option here, which made a hold reachable by
promotion through an operational grant — the exact composition the next line
forbids. None of these dispositions writes a value into `P`:

```text
ProcessDisposition(Unknown(P))   ⇏   EpistemicResolution(P)
```

This is that section's non-composition read in the direction this case needs. No
quantity of semantic evidence composes into operational authority, and no grant
makes a claim better supported. Someone who authorises work to continue over an
unresolved barrier has authorised work to continue and has established nothing
about the barrier; the record shows both facts and does not merge them.

Where the party the rule constrains and the party holding that operational
authority are the same person, the separation is procedural rather than
organizational. Role identity is not person identity, and the same human acting
under a recorded operational grant acquires no semantic authority over `P` by
acting under it — but the independence is then a discipline the arrangement does
not enforce, and this document does not claim otherwise. What compensates is
independent review or evidence alongside an explicitly recorded disposition, not
an assertion of separation the structure does not supply.

The temporal shape is the point. **Concurrence is not recurrence.** Two reviewers
validating the same defect on the same subject is corroboration — the finding is
better evidenced, not repeated — and admitting it would mean that adding
reviewers makes a change likelier to be redesigned, which is an absurd
incentive. Equally, two findings against one proposition with nothing tried
between them are one episode still open, not a recurrence — but that is a
statement about what recurrence *means*, not a disposition anyone may reach from
a silent record. What recurrence means is that a declared attempt to restore a
proposition over demonstrated ground ran, the subject it produced was reviewed,
and that proposition was validly violated there again inside that same ground. Where the record does not show that, the question
is held, not answered.

**A change touching the ground a finding sits on is not a correction of it.**
Version control establishes the subjects, their order, and the exact delta
between them; it cannot establish that the delta was corrective, and treating it
as though it could would let a cosmetic touch manufacture the barrier:

```text
ChangeTouches(the ground f1 sits on)   ⇏   CorrectionAttempt answering f1
```

So the barrier is a `CorrectionAttempt`, not a correction, and the authorities
divide:

```text
version control        the two subjects, their ordering, the delta
the attempt record     that this delta was declared to answer f1
the normative contract what proposition f1 was validated as violating, and so
                       what c is aiming at
the later review       whether that same proposition was validly violated on the
                       exact after-subject, inside the ground f1 demonstrated
tests / reproducers    the evidence that settles the row above; and, separately,
                       the planned probes, which guide work and settle nothing
```

Nothing here has to prove the attempt worked, and nothing here treats the mere
appearance of `f2` as proof it failed. An earlier revision said exactly that —
*"if `f2` appears, that is evidence it did not"* — and it is withdrawn: a
validated finding against some other proposition on `S2` says nothing about
whether `c` restored `P`. What matters is that the attempt is **declared before
the review that follows it**. An author chooses which correction to attempt, and
that is theirs to choose; what they cannot do is wait until `f2` lands and then
decide the earlier patch was aimed elsewhere. Precommitment is what separates
choosing a correction from controlling whether recurrence can be established.

**`f2` must violate the same proposition as `f1`, inside the same demonstrated
ground, and this reverses an earlier choice in this document.** The previous text
said requiring the same proposition "would be too narrow", on the reasoning that
working by boundary exists to catch a *different* material defect thrown off by
the same abstraction. That reasoning is sound about what is worth investigating
and wrong about what may fire a mandatory transition: with boundary no longer a
truth-maker, "different defect, same abstraction" has nothing left establishing
it except someone's reading of how far the abstraction extends. A different
proposition violated on `S2` is a finding with its own obligations and may be
strong architectural evidence; it is not this attempt failing, and it does not
fire `STOP` on `c`. The same holds for the same proposition violated outside
`Δ1`: wide propositions cover ground `c` never answered for, and matching the
proposition alone would relocate the old over-merge from the boundary onto `P`.

**Nor need `f2` be a different defect from `f1`.** An earlier draft required that
`f2` "is not another observation of `f1`", and that condition is removed. It
asked for a judgement about causal identity — whether two symptoms are one
defect — for which no authority exists here: a reproducer establishes how a
defect manifests, not what it is, and letting a reviewer settle it by assertion
would hand a single reviewer the power to manufacture a mandatory stop. Worse,
it excluded the most informative case there is. A defect that survives a
declared, precommitted attempt to correct it is the clearest evidence the
abstraction is wrong, and a rule whose purpose is to notice that must not
discard it.

What separates corroboration from recurrence is therefore temporal shape alone,
and it needs no causal judgement:

```text
same subject, several reviewers   →  corroboration; one finding, better evidenced
same subject, several defects     →  distinct findings in one round; no declared
                                     correction ran between them, so nothing to
                                     have been defeated yet
later subject, after a declared   →  candidate formed; the evidence then decides
correction
  same proposition, witness       →  recurrence admitted, whether or not the
     established inside Δ1            second finding is "the same defect"
  a different proposition, or a   →  not a recurrence of this attempt; a finding
     witness established outside     in its own right
     Δ1
  neither established             →  HOLD_FOR_INVESTIGATION; the candidate stands
later subject, no declared        →  no candidate, and no mandatory transition.
correction                           A structural signal worth investigating
```

A second reviewer agreeing that two findings look like the same recurrence adds
nothing to the relation above. Agreement is not evidence that a proposition was
violated again; independence does not make it so; and no quantity of it
substitutes for the conditions above, because concurring opinions compose into a
stronger opinion and never into an established relation. That is what a model
reviewer's output is worth here, and a human reviewer's too: either can raise
observations and point at evidence, and neither step produces a mandatory
transition, because the transition runs off a defeated precommitment rather than
off anyone's view that a recurrence occurred.

**Validation is predicate-based, not label-based, and each predicate names the
authority that can establish it. The predicates sit at two levels, and the
levels must not be mixed.**

Finding-level predicates are everything a finding needs to stand on its own —
they belong to `f1` alone, use no fact about a second finding, and none needs a
correction attempt or a later review to exist. They establish that the defect
**is real**, and nothing about which decision it bears on:

```text
exact subject / head        the version-control record — fixed once f1 is raised
reproducer result           executable evidence — fixed once f1 is validated
violated proposition        the normative contract — fixed once f1 is validated
demonstrated defect domain  this finding's own reproducer, earliest locus and
                            bounded sibling search — what they placed there, and
                            no more; unlike the rows above, further bounded
                            sibling search may keep demonstrating more of it after
                            f1 is validated, up to whatever a CorrectionAttempt
                            freezes — see §"Canonical epoch of Δ1"
```

Call that `FindingEstablished(f)`. Note what is **no longer** in the list.

**Materiality was in it, and moving it out is this section's whole point.** A
finding's truth and a finding's bearing on a decision are different
propositions, and an earlier revision made the second a precondition of the
first. That put the question *"does this matter?"* upstream of the question
*"is this real?"*, so whoever answered the first controlled which facts the
method could ever see. Every truth-maker repair in this document — boundary
extent, the discriminant set, reviewer identity, the dependency relation — was
the same defect one level higher, and this is where it had come to rest.

```text
FindingEstablished(f)      ⇏   MaterialToClaim(f, C)
MaterialToClaim(f, C)      ⇏   FindingEstablished(f)
```

Both directions are needed. A real formatting defect can be established beyond
dispute and bear on no runtime-semantics claim. An unvalidated report against a
change's central requirement can be obviously decisive *if true* while its truth
is still unresolved. Neither relation derives the other, and §"Materiality is a
relation, not a property" below governs the second.

A boundary assignment is recorded alongside these and is deliberately **not** one
of them. It organises the record rather than establishing anything, so a finding
whose boundary is disputed is established exactly as it was, and an unresolved
membership blocks nothing. Boundary assignment bears on neither axis: it does not
establish or defeat the factual predicates, and it does not establish or defeat
`MaterialToClaim(f, C)` — which is settled on its own basis, or not at all.

Candidate-level predicates relate two findings and only exist once both do.
They belong to a `RecurrenceCandidate` — to forming one, or to qualifying one
already formed — never to the validation of a single finding:

```text
correction delta            the version-control record: the before/after
                            subjects, their ordering, and what changed
correction attempt          the precommitted record: that this delta was declared
                            to answer f1, and so takes on f1's proposition over
                            the ground f1 demonstrated
epoch ordering              the version-control and review records together
                            — these three form the candidate

correction failure          the two findings' violated propositions, each
                            established against the normative contract; and f2's
                            concrete violating witness, established against Δ1 as
                            f1 demonstrated it before c
                            — this one qualifies a formed candidate
```

That split is load-bearing. An earlier draft scoped one undivided table over
"a finding is validated by meeting those predicates", which pulled the
correction barrier into the validation of `f1` — and it does not exist yet when
`f1` is raised. Validating `f1` then required
knowing about a correction made in response to `f1` and about a later `f2`, so
no first finding could be validated and no first recurrence could ever be
admitted. A finding is factually established by information available when it is
found; anything else is a fact about a pair. Whether it then bears on a claim is
a further question again, and not one this paragraph settles.

The two rows that were both called `correction barrier` are the same repair
seen from the other side. They were never rival authorities for one relation:
version control establishes the delta and the order of the subjects, and the
precommitted record establishes that the delta was *meant* to answer `f1` — and
so, by `f1`'s own finding-level predicates, which proposition and which
demonstrated ground it took on.
Naming both the same thing made the falsified reading — history alone
establishes the barrier — look like a live alternative to the correct one. The
barrier is the two together:

```text
correction barrier  =  correction delta
                     ∧  correction attempt
                     ∧  EstablishedCorrectionFailure(c, f2)

ChangeTouches(f1's ground)  ⇏  CorrectionAttempt answering f1
```

All three conjuncts must be in hand. The first two form the candidate and the
third qualifies it; a barrier with any one missing is not established, and an
unestablished conjunct is never read as a settled answer in whichever direction
happens to be convenient. Note what the third conjunct excludes: a later finding against some other
proposition, or against the same one outside the ground `c` answered for, leaves
the first two satisfied and the barrier unestablished — the correct result rather
than a gap.

A finding is factually established by meeting the finding-level predicates, not
by being labelled `VALID` — and factual establishment alone does not make it a
`ValidatedFinding(f, C)`, which needs its bearing on `C` established too; an `INVALID` is a claim that contradicting evidence exists and is open to
challenge on that evidence. Where a predicate cannot be established from its
declared authority its value is `UNKNOWN` — not a value the author selects, and
**not a "no"**:

```text
UNKNOWN(P)                  ⇏   P
UNKNOWN(P)                  ⇏   ¬P
UNKNOWN(admission)          ≠   recurrence disproved
UNKNOWN(correction-barrier) ≠   the correction demonstrably held
UNKNOWN(correction-barrier) ≠   no correction was attempted
```

It cuts both ways deliberately. An unresolvable predicate that is material to a
decision this loop rests on holds that decision open — `HOLD_FOR_INVESTIGATION` —
rather than resolving it in the direction the party holding the question prefers.
Read the first line alone and ambiguity becomes a way to avoid a recurrence;
read the second alone and it becomes a way to manufacture one. Neither is
available: an unestablished barrier does not admit a recurrence, and it does not
clear one either.

Otherwise the rule is defeated by manufacturing ambiguity instead of writing
`INVALID` — the cheaper attack, and the harder one to see.

**A missing attempt record leaves the barrier unestablished, and nothing more.**
The cheapest attack on this section is not to argue about a barrier but to file
nothing, so that none can be established and the candidate reads as cleared:

```text
MissingAttemptRecord   ⇏   NoCorrectionAttempt
absence of evidence    ⇏   evidence of absence
```

The precommitted record is what makes an attempt *admissible*. It is not a census
of what the author did, so its absence establishes nothing about whether
corrective work happened, and cannot be read as establishing that none did. An
author who files no record leaves no candidate to qualify: with nothing
precommitted, there is nothing a later finding can be shown to have defeated. The
same holds for a record answering some other finding — a record about `f1'`
reports on `f1'`, and says nothing about whether the defect behind `f1` was
corrected.

Be exact about what omission buys, because two earlier statements here were wrong
in opposite directions. It is not true that omission "can cost a decision but
never buy one". Nor does it buy a clearance: nothing is established about the
history, the findings and their establishment stand, and two established
findings on related ground with no record of what was tried between them remain a
structural signal worth investigating.

What omission does buy is narrow and stated exactly: **no candidate forms from
that relation, so no mandatory recurrence can be established through it.** That
is a fact about the recurrence relation. It is not a fact about the history, and
it is not a licence to claim the loop converged — those are separate subjects,
and §"Convergence is a claim" below governs the second one.

**There is deliberately no route to the negative.** Reaching "no correction was
attempted" as an established fact would need an authority able to speak for the
whole interval — a complete attempt ledger, or a declaration by the one party the
rule constrains. This version has neither and invents neither: an author-declared
negative would hand the measured party the power to clear its own candidate,
which is the shape this document rejects everywhere else, and a completeness
oracle is architecture nobody has shown a need for. So the negative is simply not
reachable here, and the cost of that is stated plainly rather than designed
around: a history in which nothing was in fact attempted can hold, conservatively
and perhaps for a long time.

Inaction is still not failed correction — a hold does not assert one. What the
absence of a route to the negative costs is speed, and what it buys is that no
party can talk their own history into the clear. Where the burden of that proves
material in practice, the answer is evidence about how often it happens, not a
negative authority added on the strength of the inconvenience.

**Who reviewed is recorded, and it is not a truth-maker.** Recording
observations without recording who made them leaves the record unverifiable, and
an observation from someone who does not pay the cost of a "yes" is worth having.
But reviewer identity carries no load here. Independence can make an observation
more worth investigating; it cannot establish a predicate the evidence does not,
and a reviewer who is also the author does not thereby lose the ability to
establish one the evidence does. Every load-bearing relation in this section is
decided by its named authority or its evidence, or it stays unresolved. That is
what makes the section usable where one person holds both roles — the ordinary
case in a small repository, and not something to legislate around by requiring a
second person the process may not have.

**Each finding carries the boundary it landed in, assigned by whoever raised
it.** That assignment organises the record; it qualifies nothing. Where two
legitimate readings disagree about which boundary a finding belongs to, neither
party's say-so settles it and the membership is simply unresolved: the author
does not win because it is their change, and the reviewer does not win because
they found it. Nothing load-bearing rests on the outcome, which is precisely why
leaving it unresolved costs nothing.

**What a boundary is for, now that it decides nothing.** Boundaries organise the
search: which siblings to check, which region a defect probably lives in, what a
round covered, how findings cluster into an explanation. All of that is real and
worth keeping. What a boundary does **not** do is qualify a recurrence. Two
findings sharing one is not by itself a mandatory anything, because "same
boundary" has no truth-maker that survives two parties disagreeing about its
extent — an author can partition finely enough to scatter every pairing, and a
reviewer can merge freely enough to manufacture one, and no evidence settles
between them. The rules below still protect boundary identity against retroactive
redrawing, because a record that can be rewritten afterwards is not a record.
They no longer protect a transition, because none runs off them.

**A boundary is predeclared or emergent, and both participate.** The difference
is only when it became knowable:

```text
predeclared   known_since = the start of the loop
emergent      known_since = the review epoch of the finding that revealed it,
              which is that boundary's first occurrence
```

Requiring every boundary to have been declared before the review that finds a
defect in it asks for knowledge nobody had. A review that discovers a boundary
the predeclaration missed is doing the job; the finding that revealed it is its
first occurrence, not a finding that cannot count. An earlier draft required
`f1`'s boundary to be declared before `f1`'s review, which — while boundaries
still qualified recurrences — meant a genuinely emergent one could never hold an
admitted recurrence at all, and the stop condition failed open exactly where
discovery was real. Boundaries no longer qualify anything, so that particular
failure is now closed twice over; the predeclared/emergent distinction is kept
because coverage records still need it.

Predeclaration is still preferred, because a boundary named in advance was named
without knowing which findings would land in it. What it buys is evidential
weight, not admissibility.

**A predeclared label is a proposal; participation is what freezes an identity.**
The two are distinct, and an earlier draft's failure to separate them made the
same assignment both permitted and forbidden — a reviewer was free to join two
proposed boundaries, while those boundaries were frozen against merging. A label
that no finding has ever been assigned to has no history to protect. It is
vocabulary the author offered, and the author is the party the rule constrains,
so it cannot bind the reviewer:

```text
proposed label, no assignment yet   →  vocabulary; a reviewer may adopt it, join
                                       two of them, or ignore the set entirely
first BoundaryAssignment lands      →  that boundary now has an identity in the
                                       history, and it is frozen from here
```

No recurrence turns on how the partition is drawn — that is what demoting the
boundary bought — but the record still has to mean something, and a fifty-label
partition nobody ever occupied describes no coverage at all. An author who
predeclares fifty narrow labels has proposed fifty names, not fifty protected
boundaries; the reviewer who meets the first finding spanning several of them
assigns it to the boundary it actually belongs in and the redundant labels never
acquire an identity. No reviewer is forced to pick a cell out of a partition that
no finding has ever occupied.

Once a boundary has participated, the protection is absolute and runs the other
way — including against the same joining that was free a moment earlier:

```text
retroactive split                   forbidden
retroactive merge or join           forbidden
retroactive semantic reassignment   forbidden
identity-preserving rename          allowed, recorded, identity unchanged
```

Nothing may be redrawn in a way that rewrites what the record showed at the time.
No recurrence turns on this either way — boundaries qualify nothing — but a
record that can be reshaped afterwards stops being evidence of what a round
covered, which is the whole of what boundaries are kept for. A rename that
preserves identity is a rename; one that moves the ground it covers is a
reclassification, and after participation it is not available. Emergent boundaries freeze the same way — at their first
occurrence rather than at the start of the loop — because that occurrence is
exactly the participation that creates the identity.

Naming a new boundary late is not an exception to any of this. One named late
covers ground no existing boundary claimed; where it would take ground from a
boundary that has already participated, that is a split wearing a new name, and
the finding belongs to the boundary that already existed.

**`Admitted` means one thing here, and it is about recurrence.** A finding that
meets the finding-level predicates is **established** — never an "admitted" one,
and not yet a validated finding either, which additionally needs its bearing on
the claim established. `RecurrenceCandidate` and `RecurrenceAdmission` are the
only things the word "admitted" covers, which is why the two histories the
lifecycle-disposition rule above keeps are named for their subjects and not for each
other: `ValidationHistory` takes a finding **and the claim it was material to**,
`RecurrenceAdmissionHistory` takes a candidate, and there is no third and no
`FindingAdmission`, implied or otherwise. The questions below run over
`ValidatedFinding(f, C)` for this loop's claim `C` — never over findings whose
bearing on `C` was never established.
New evidence that contradicts `f1` afterwards records a later
`FindingValidationOutcome(f1)` at its own epoch; it does not, on that basis
alone, change `f1`'s lifecycle disposition, and it leaves the validation that
held at `f1`'s epoch and any recurrence admitted on the strength of it exactly
where they were.

**At the end of each round, these are answered on the record**, over validated
findings. The first two carry a transition; the third does not, and the
difference is stated rather than left to the reader:

**Load-bearing.** Each of these forms candidates and then reports what the
correction barrier did with them, so each answers in exactly one of four ways:

```text
no candidate formed      the facts do not assemble a RecurrenceCandidate. This
                         is the only place a bare "no" is honest.
recurrence admitted      candidate formed, barrier established. The trigger
                         fires and the freeze-preserve-spike sequence runs.
candidate out of scope   candidate formed, barrier established to be out of
                         scope for this attempt — f2 was validated against a
                         different proposition, or against the same one with its
                         witness established to lie outside Δ1. Nothing is
                         unsettled here: the evidence resolved the question and
                         resolved it away from recurrence. The trigger does not
                         fire, and this outcome blocks nothing on its own. f2
                         remains a finding in its own right, carrying whatever
                         obligations it carries.
held for investigation   candidate formed, barrier unsettled. The trigger does
                         not fire, the candidate stands, and the change may not
                         be declared ready or convergent on that history.
```

Those are four classes of result, not a schema: nothing here asks for an enum, a
field name or a machine-readable form. Naming them is only what stops one from
being written down as another. The third and fourth are the pair most easily
merged and must not be: a hold is reserved for evidence that did not settle, so
recording an established out-of-scope candidate as a hold would block a claim on
a question the evidence already answered, and recording it as `no candidate
formed` would deny that a candidate formed at all. Neither question reaches a
transition from landings alone.

**Before the questions are put, sort this round's established findings by their
bearing on the claim.** The questions run over validated findings, and a finding
is not one until both its axes are established — so this step decides what is
even eligible to be asked about:

```text
for each FindingEstablished(f) in this round's record, against this loop's claim C

  MaterialToClaim(f, C) established
        →  f participates as ValidatedFinding(f, C)

  NotMaterialToClaim(f, C) independently established
        →  f stands as an established fact; it does not participate in
           recurrence for C on materiality grounds; this is a real answer

  MaterialToClaim(f, C) unresolved
        →  record the unresolved bearing
        →  f is NOT a ValidatedFinding(f, C)
        →  no candidate forms through it
        →  and this is NOT "no candidate formed" in the sense the questions
           mean — nothing has been cleared
```

The third row is the one that gets lost. A finding sitting there has not been
answered, dismissed or resolved; it has been left open on an axis the questions
below do not ask about, and the round's record says so rather than passing over
it in silence.

1. Did a `ValidatedFinding(f2, C)` from this round land on the subject a declared
   correction produced, where that correction answered an earlier
   `ValidatedFinding(f1, C)` of this loop — the same claim `C` in both? Name
   `C`, the correction `c`, both findings, the proposition `P` that `c` was
   aiming at, the ground `Δ1` it answered over, and the subjects `S1` and `S2`.
   That forms a candidate; the evidence then decides between an admitted
   recurrence, a finding that falls outside what `c` answered for, and a hold. A
   candidate whose evidence settles neither is not thereby a "no"; it is a hold.
   Where `f1` and `f2` were material to *different* claims, or the relation
   between those claims is unresolved, do not read them as one: the recurrence
   relation qualified here does not settle cross-claim applicability, and
   nothing is cleared by that gap either.
2. From round three onward: does this loop hold two or more candidates whose
   declared corrections were each shown to have been defeated? Before round three
   there is no window to look at. Name the candidates the window contains and
   what the evidence established for each. Repeated landings on their own are a
   pattern worth reporting, not an admission: this question fires only on
   established defeats inside the window, and holds where one is unsettled.

Question 1 reaches the first defeated correction and question 2 the repeated
one; both are kept because one correction failing and a sequence of them failing
are different signals. Both run through the same barrier and the same evidence
relation, so neither offers a route to a mandatory stop that the other does not.

**Supporting only — no transition of its own.**

3. Does a `ValidatedFinding(f, C)` this round hold that an approach is wrong
   which an earlier `ValidatedFinding(f', C)` of this loop also held was wrong —
   the same claim `C` in both — as opposed to objecting to how that approach was
   tuned? Name both, or answer `undecided` where the two cannot responsibly be
   compared. That `undecided` is local to this non-load-bearing comparison: it is
   not any of the unresolved states defined elsewhere in this document, and
   nothing transitions on it either way. Where the claim relation between the two
   findings is unresolved, do not infer continuity.

This question was load-bearing in an earlier draft and is deliberately demoted.
Deciding that two findings condemn "the same approach" is a judgement with no
named authority: there is no record that fixes what an approach is, and no
evidence that settles when two objections are to one approach rather than two.
Left as a trigger it was the cheapest way for a single reviewer to produce a
mandatory stop — the mirror of the defect the whole section exists to prevent,
since the party who found the defect would decide that the process must halt.
A "yes" is recorded and may motivate a spike or a sibling search. It may **not**
reach a load-bearing predicate, directly or by influence: it cannot change a
boundary membership, cannot supply or strengthen `EstablishedCorrectionFailure`,
cannot turn a hold into an admission, cannot convert an unresolved membership
into an established one, and has no precedence over the barrier. An earlier
version let it "inform how a boundary is read", which was a mandatory transition
reached sideways — boundary reading fed candidate formation, so an advisory
judgement with no named authority moved a mandatory process. That path is closed,
and closing it invents no `ApproachIdentity` to replace it. Leaving it
undecided is an available and often correct answer: an advisory question is
allowed to stay indeterminate precisely because nothing transitions on it. This
document names no authority over what "the same approach" is, and does not
invent one.

An unanswered question is not a "no", and neither is a held one, but the two fail
differently: a round whose questions were not put has not been reviewed for
convergence at all, while a round that answers "held" has answered honestly and
still may not be declared convergent. The change may not be declared ready on the
strength of either. A round's record is complete when each load-bearing question
reports one of the four classes above and names the candidates behind it — the
correction, both findings, the proposition it answered for and the ground it
answered over, for an admitted recurrence, for an out-of-scope candidate or for a
hold; for a hold also what could not be established, and for an out-of-scope
candidate what *was* established — the differing proposition, or the witness's
established position outside `Δ1`. A bare "no" is available only where no
candidate formed at all. The supporting question must be asked and answered too, and may be
answered as undecided; what it may not do is transition.

A "yes" to a load-bearing question, once the candidate qualifies under the
admission rule, is an admitted recurrence, so the trigger fires on it and the
author does not get to dispose of it — the admission already happened, and
lifecycle disposition does not reach backwards. What the trigger buys is
bounded, which is why it can be
unconditional: the freeze-preserve-spike sequence below, where "does the
abstraction need replacing?" gets answered. A spike concluding the abstraction is
sound is a permitted outcome.

This is not a mechanical check and does not pretend to be one. What it changes is
where authority sits: observations are advisory whatever their source, admission
is by rule, and only admitted recurrence moves the process.

### Convergence is a claim

"This loop has converged" is a claim like any other in this document, about a
declared subject and scope, and it answers to the same rule every other claim
does: it must name the basis that establishes it, and it may not consume a
proposition whose basis has not been established. Nothing about it is special
except how easily it gets asserted by default.

It is **not** established by any of these, alone or together:

```text
no recurrence candidate formed
no STOP trigger fired
this round's reviewer found nothing further
the branch has been open long enough
a maintainer is willing to authorise promotion
```

The first deserves saying plainly, because the recurrence rules above make it
tempting:

```text
NoRecurrenceCandidate   ⇏   ConvergenceEstablished
```

A recurrence question answers a recurrence question. A candidate that never
formed says the facts did not assemble that relation — it says nothing about
whether the claim has the basis it needs for everything else it rests on.

**Two different subjects, and the distinction is the whole of this section.** One
is historical: *did corrective work happen?* The other is evidential: *does this
claim have the basis it requires?* Confusing them is how a missing record turns
into either a fabricated negative or a free pass, and both have been tried here:

```text
MissingAttemptRecord   ⇏   NoCorrectionAttempt          (history: still UNKNOWN)
MissingRequiredBasis   ⇒   the claim that needs it is not established
```

The second line asserts nothing about the world. It does not establish that no
correction was attempted, that a correction failed, that a recurrence occurred,
or that none did. It says only that a claim is being made on a basis that is not
in hand, which is the same objection this document raises everywhere else.

**The convergence-basis closure problem — which established findings a claim
must reconcile — is open here, and is deliberately left open.** Two attempts have failed. The first scoped it to the
propositions a claim "actually consumes", which let the claimant decide what
their own claim depended on. The second derived a set from validation history,
historical materiality and loop membership — and materiality was itself
established against locally written acceptance conditions, so the same selection
survived one level down. Both are withdrawn rather than left standing as rules
with a known defect.

What that leaves is a stated gap, not a silent one:

```text
a convergence claim may not rest on a proposition whose basis is absent
      →  established, and enforced by everything below

which findings such a claim must account for
      →  NOT established here
```

Closing it needs materiality to be establishable without the claimant choosing
it, which is §"Materiality is a relation, not a property" — and where that
relation is unresolved, §"Findings whose bearing is unresolved" says what
follows. The closure itself is a later question and is not answered in this
revision. Until it is, this section blocks what it can name and does not pretend
to name everything.

**A lifecycle disposition is a record, not a basis.** Writing `fixed` against a finding
records a lifecycle state; it does not establish that a declared correction
answered it, and it cannot supply the basis its own claim is missing:

```text
LifecycleDisposition(f) = fixed        ⇏  an admissible CorrectionAttempt answering f
LifecycleDisposition(f) = out_of_scope ⇏  f is out of scope
LifecycleDisposition(f) = superseded   ⇏  f was superseded
LifecycleDisposition(f)                ⇏  ConvergenceEstablished
```

The middle two matter as much as the first, because they are the cheaper escape:
a finding that cannot be argued out of a claim's reckoning can be labelled out of
it instead. It cannot. Each of those labels asserts something, each assertion needs
the evidence it asserts, and none of them is self-supporting. "Out of scope" in
particular is a materiality claim wearing a lifecycle label, and it answers to
§"Materiality is a relation, not a property" like any other. That is the same `FinalState != History`
property the lifecycle-disposition rule relies on elsewhere, read in the direction that
matters here: a label cannot manufacture the evidence it summarises.

**Where a material predicate is unresolved, so is the claim that rests on it.**
A recurrence candidate that is `HOLD_FOR_INVESTIGATION` blocks the convergence
claim for as long as it holds. So does a finding whose bearing on the claim was
never established, wherever the claim would need it to be irrelevant — that is
§"Findings whose bearing is unresolved", and it reaches the claim by the same
route. A hold, an open finding, an unestablished membership, an unresolved
bearing: each leaves the claim that rests on it unestablished, none becomes false
by being unresolved, and no operational disposition converts any of them into
semantic sufficiency. This is claim sufficiency, not a verdict on history.

The structural analogy is `NON_REFUTED`, which §"Epistemic classification"
already refuses to admit for a review with no declared scope: the absence of a
found defect is not the presence of a basis. Convergence answers to the same
discipline. It is a different predicate about a different subject, and nothing
here merges them.

### Promotion, and what a grant does not repair

Two conditions, on different axes, and both are required:

```text
SemanticPromotionEligible(C)   the convergence claim C is established — and it
                               is not, wherever any material predicate it rests
                               on is unresolved. Which established findings such
                               a claim must reconcile is the unresolved
                               convergence-basis closure problem above, so this
                               conjunct is a necessary condition and not a
                               complete one
Authorized(g, promote, C)      an operational grant permits the action
```

```text
SemanticPromotionEligible(C) ∧ Authorized(g, promote, C)
      →  promotion may proceed under whatever rules govern it

Authorized(g, promote, C)   ⇏   SemanticPromotionEligible(C)
Authorized(g, promote, C)   ⇏   the convergence basis is sufficient
```

Which actions are protected, what a grant must contain and how it is revalidated
are not this document's subject; §"Authority non-escalation" owns that boundary
and this section does not extend it. What this section adds is only the first
conjunct — that eligibility is a question about evidence, answered before any
question about permission arises.

Two consequences worth stating because both have been got wrong here before. A
grant does not repair a missing basis: *no grant makes a claim better supported*
is the existing rule, and this is that rule applied to convergence. And where one
person holds every role — author, reviewer, grant holder — nothing changes, in
either direction. Occupying two roles supplies no evidence, and occupying them
does not remove evidence either; the predicates are decided by their bases
whoever is asking.

### Triggers, and what follows when one fires

Stop patching and escalate to a redesign the moment **any** of the following is
**established** — established meaning the trigger's load-bearing predicates were
shown to hold by their named authority or by admissible evidence, not merely that
the situation is believed to obtain:

- a recurrence is admitted under the rule in §"The recurrence
  conditions are answered, not computed";
- a fix falsifies an assumption an earlier fix in the same change relied on.
  Unlike the recurrence questions this one is noticed while it happens, by
  whoever holds both fixes in mind, and nothing here reconstructs it afterwards
  from a record. It is therefore established when someone establishes it, which
  is the general rule below rather than an exception to it;
- an input the change is accountable to begins failing. A feature deliberately
  made unsupported under §3 is a contraction rather than a regression **only
  where the authority for that support decision is independently established**.
  Writing the decision down before the failure prevents hindsight rewriting and
  establishes nothing else: timing fixes an epoch, not a right to narrow the
  accepted domain, exactly as it does not for the discriminant set. An earlier
  revision made the record's date sufficient, which let a prewritten local
  declaration convert a break into a contraction and avoid this trigger. Where
  that authority is unresolved, so is whether this is a contraction — investigate
  it rather than defaulting either way;
- fixing a finding would require reproducing internals of an upstream authority
  inside this change, to a degree that makes this change a second
  implementation of that authority;
- a test that some obligation here relies on fails twice to discriminate the
  defect it claims to catch — twice, because once is a bug in the test and
  twice is evidence the property is not testable the way it is being framed.

When triggered, the sequence is:

```text
freeze patching
  → preserve exact HEAD evidence (do not lose the failing corpus/repro)
    → run a disposable architecture spike, outside the production branch
      → select a mechanism before any further production edit
```

A spike is disposable: its purpose is to answer "is this the right layer,"
not to become the merged implementation by accretion.

**Established, not merely occurrent.** A trigger that occurs and is never
established fires nothing, and the honest statement of that is a limitation
rather than a guarantee:

```text
EstablishedTrigger(t)         →  mandatory STOP / REDESIGN
occurred but not established  →  no transition, and a coverage limitation
absence of observation        ⇏  the trigger did not occur
```

The last line is the one that must not be dropped. Nothing here licenses reading
a quiet record as evidence that nothing happened, and a round that knows its
coverage was thin should record that rather than let silence pass for a clean
result. What this process guarantees is a mandatory response to established
triggers — not detection of every occurrence, which would need a coverage oracle
over the whole history: the same illegitimate architecture as a complete attempt
ledger, refused here for the same reason. Where a trigger becomes established
later, it fires from the epoch it became established, and the earlier record is
not rewritten to pretend it was known sooner.

**The three-round count is process policy, not a theorem.** Nothing here
establishes that three is correct for an arbitrary change. Treat it as a
tripwire to be honoured, or *amended in this file* — never as a quantity to be
reasoned around in the PR body of the change it is currently constraining. A
threshold an actor may reinterpret while it is binding them is not a threshold,
and the indirection is closed too: **an amendment to this paragraph, or to any
rule a STOP trigger depends on — a threshold, the recurrence questions, the
correction-obligation and defect-domain rules, wherever in this document they
are defined — does not apply to a review loop already under way when it
lands.** Otherwise the
tripwire is
defeated by a second change that merely raises the number and returns.

When `STOP / REDESIGN` fires, ask these in order **before** considering any
further code:

1. Is the **subject** wrong — are we asserting about the wrong thing?
2. Is the asserted **relation** stronger than the objective needs?
3. Is the **authority** insufficient, illegitimate, or absent?
4. Is the **representation** lossy for the decision being made?
5. Can the objective be satisfied by a **weaker valid claim**?
6. Does the **abstraction** itself need replacing?

Adding another leaf rule is not on this list. If the honest answers leave the
abstraction intact, the corrective cut may still be small — but the trigger
stays fired, the spike still runs, and the conclusion is recorded. The trigger
is unconditional by design: an actor who may declare it inapplicable while it
binds them holds no trigger at all.

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

This is a reasoning aid for humans and agents. It is deliberately not a schema,
and nothing should serialize it merely because it is written down.

**No implicit semantic coercion.** `P ⇒ Q` only where an explicit composition
rule `ρ : P → Q` exists and is named. The universal rule is the normative part;
the instances below are evidence that it is worth stating, recorded so the same
substitution is recognizable next time it is proposed:

```text
GitObjectExists(c)            ⇏  CanonicalOnForge(c)
ObjectIdentity(c)             ⇏  CanonicalRole(c)
ExposedAtAnchor(x)            ⇏  Deferred(x)
SourceOccurrenceIdentity(x)   ⇏  RuntimeRoleIdentity(x)
StaticBindingIdentity(x)      ⇏  EffectiveBindingIdentity(x)
EvidenceIdentityMatch(e)      ⇏  EvidenceQualification(e)
LocalVerification(f)          ⇏  ForgeVerification(f)
SemanticProperty(x)           ⇏  StringPatternProperty(x)

                Representation(x)  ⇏  Role(x)
```

The list is illustrative and open. Its emptiness would not weaken the rule; its
growth is how the repository remembers.

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
must contain a registry. Legitimate answers to the authority question include:
reuse an existing authority; create one; weaken the claim; withdraw it; or
report `UNAVAILABLE`. Creating an authority to rescue a claim the objective
never required is the failure mode — and extending an authority whose subject
is different (a public install contract asked to carry internal topology) is
the same failure with a smaller diff.

**A projection must never become an authority.** If a build artifact begins
mediating a semantic decision, the direction has inverted.

## Earliest lossy boundary

For a decision relation `~R`, if

```text
x  !~R  y      and      F(x) == F(y)
```

then `F` is insufficient for that decision: it discards exactly what the
decision depends on. Repair `F` — the earliest **lossy** boundary, the point in
the path where the distinction was discarded. That is a different subject from
the convergence boundaries a change declares for its review loop, which organise
the search and qualify nothing, and the two are not interchangeable. Do **not** compensate downstream with vocabulary
scanners, an
ever-widening AST grammar, extra leaf conditionals, or heuristic
reconstruction; each re-derives, one layer below the authority, what was
already thrown away.

The dual obligation is equivalence invariance: where `x ~R y`, the decision
outcome must be materially the same under the declared assumptions. A repair
that starts distinguishing genuinely equivalent inputs has over-corrected.

## Finding validation, and the corrective cut

`ReviewerFinding != FindingEstablished`. This section establishes the factual
axis only; whether an established finding bears on a claim is
§"Materiality is a relation, not a property", and the two together are what
`ValidatedFinding(f, C)` is shorthand for. For each finding, in order — every
finding, since deciding in advance which ones matter is the same discretion the
STOP triggers refuse:

reproduce → identify the violated proposition → locate the defect's earliest
locus → search bounded siblings → define the defect domain → select the
corrective abstraction.

Where the defect is that a distinction the decision needed was discarded, that
locus is the earliest lossy boundary and §"Earliest lossy boundary" applies.
Where nothing was discarded — an authority is simply wrong, a check is absent, two
lossless reads race — the locus is wherever the proposition first fails to hold,
and the lossy-boundary analysis does not apply. Do not manufacture a lossy
boundary to satisfy the sequence.

**The defect domain records what the evidence placed there.** `Δ(f)` is that
domain, and *demonstrated* is the word carrying the weight — the sequence above
reaches it only after the reproducer, the earliest locus and the bounded sibling
search:

```text
in Δ(f)        a manifestation this finding's own evidence placed there
not in Δ(f)    established by evidence to lie elsewhere
undetermined   everything else — and this is UNKNOWN, not "outside"
```

Neither party sets the extent. An author who writes "the domain is all parser
behaviour" over evidence covering the scalar construction path has written a
larger sentence, not demonstrated a larger domain; an author who writes "scalar
only" over evidence that reproduced scalar *and* list has not made the list
manifestation go away. The same holds for the reviewer: naming ten mechanisms as
one domain is an investigation hypothesis until the evidence places them there,
and the validation-outcome rule below applies unchanged — a record "does not make the
evidence show it, and either party may dispute one on the evidence". Enlarging
`Δ(f)` is always available, and always by the same route: demonstrate it.

`Δ(f)` is a positive record of what was shown, never a claim of exhaustiveness. A
manifestation nobody investigated is undetermined, which is why the third row is
`UNKNOWN`; reading it as "outside" would be the coercion this document refuses
everywhere else. §"The recurrence conditions are answered, not computed" is the
consumer of this domain and states how, and this section does not restate that.

Patch after this classification, not before.
Several similar manifestations frequently belong to one defect class; fixing
them one at a time is how a review loop fails to converge.

Then choose the **smallest correction that restores the intended proposition
across the demonstrated defect domain** — not the smallest textual diff. A
minimal diff that leaves the abstraction false is a larger change deferred,
usually to the next round.

Record a `FindingValidationOutcome(f)` for every finding — `VALID`, `INVALID`,
`PARTIAL`, or `UNAVAILABLE_TO_VALIDATE`. This is the **factual** axis, and it is
not `LifecycleDisposition(f)` (§"The recurrence conditions are answered, not
computed"), which records what later happened to the work. An outcome records
what the evidence shows; it does not make the evidence show it, and either party
may dispute one on the evidence.
`VALID` requires the reproducer; `INVALID` requires the contradicting evidence;
`PARTIAL` requires **both**, because it asserts two
things at once — that some portion reproduces and that the rest does not hold —
and a `PARTIAL` backed on one side only is an unexamined claim wearing a
validation outcome. Never resolve on less. The fourth is for a
finding that could not be validated either way: it requires the attempt made and
the exact limitation that blocked it, and it leaves the finding **open** — it is
a record of an unfinished validation, not a way to close one. Never patch merely
because a reviewer suggested text.

These four outcomes are about the **factual** axis and nothing else. `VALID`
says the evidence establishes the defect; `INVALID` says contradicting evidence
exists; `PARTIAL` says both, over different portions; `UNAVAILABLE_TO_VALIDATE`
says the attempt was made and something specific blocked it. None of them says
anything about whether the finding bears on a decision, and none may be
repurposed to say it. In particular `UNAVAILABLE_TO_VALIDATE` does **not** mean
"real but perhaps irrelevant", and `INVALID` does **not** mean "true but does
not matter here". A finding may be factually `VALID` while its bearing on a given
claim is established, refuted, or unresolved; the factual record is the same in
all three cases.

## Materiality is a relation, not a property

Whether a finding bears on a decision is a separate question from whether the
finding is true, and it is always a question **about a particular decision**:

```text
MaterialToClaim(f, C)
```

Never `Material(f)` unqualified. A finding has no global materiality to be
labelled with, because the same established defect can bear on one claim and not
another:

```text
MaterialToClaim(f, C1)   ≠   MaterialToClaim(f, C2)      in general
```

A formatting defect may be immaterial to a claim about a runtime invariant and
squarely material to a claim about preserving formatting. Nothing about the
defect changed between those two sentences; the decision did.

**The relation takes three values, and the third is not a gap to be closed by
default.**

```text
established material      the finding bears on C, on a basis named below
established immaterial    it does not, on a basis named below
unresolved                neither has been established
```

```text
Unknown(MaterialToClaim(f, C))   ⇏   MaterialToClaim(f, C)
Unknown(MaterialToClaim(f, C))   ⇏   NotMaterialToClaim(f, C)
MissingMaterialityAuthority      ⇏   immaterial
MissingMaterialityAuthority      ⇏   material
```

Both coercions are tempting from opposite directions and both are refused. *"It
is true, so it must matter"* over-attaches every real defect to every claim.
*"I cannot tell whether it matters, so I may proceed"* is the one that clears a
finding by shrugging. The honest state between them is the third row.

**What may not establish it.** Not the reviewer's judgement that a finding is
important, and not the author's that it is not — the same reason reviewer
identity carries no load anywhere else here. Not a declaration's timing: writing
an acceptance set before any review prevents adaptive rewriting and does not make
its author entitled to have written it, exactly as §"Probes are a plan, not a
verdict" holds for the discriminant set. And not the structural validity of
whatever carries it: a well-formed contract, PR body or scope declaration is a
carrier, and this document's standing rule is that a carrier never becomes an
authority by being well formed.

```text
ReviewerJudgement          ⇏   MaterialToClaim / NotMaterialToClaim
PrecommittedAcceptance     ⇏   AuthoritativeAcceptance
CarrierValidity            ⇏   ObjectiveAuthority
OperationalGrant           ⇏   MaterialToClaim
```

**What does establish it: nothing this document supplies.** An earlier revision
claimed a floor here — that a proposition owned by an upstream authority is
thereby material — and it was wrong, because owning a rule and that rule
governing a particular decision are different relations:

```text
Owns(A, P)   ⇏   Applicable(P, C)
```

An upstream policy may legitimately own a rule about production parsers while a
change to a test-only serializer is not governed by it. The owner is known; the
applicability is not, and asserting it would be the same unowned predicate this
document refuses everywhere else. That derivation is withdrawn.

What survives is narrower and is a **non-waiver** law, not a source of
materiality. It says what a local declaration cannot undo, given a relation
already established by some independent admissible basis:

```text
Established(Applicable(P, C))  ∧  AuthorityOwns(A, P)
      →  no local acceptance, scope text or capability declaration waives P
```

That has real force where its antecedent holds: a change genuinely governed by a
public contract or a security requirement does not escape it by omitting it
locally, however early the omission was recorded and however valid the document
recording it. This is §"Authority non-escalation" applied to materiality — a
change cannot acquire, by declaration, an authority it does not have. But it
establishes nothing on its own, because it consumes `Applicable(P, C)` rather
than producing it.

**This document supplies no general derivation of applicability or of
materiality, and will not approximate one.** It does not name an objective owner
and will not appoint one by default: not the author, not the maintainer, not the
reviewer, not whoever wrote a scope first, not the holder of an operational
grant, and not a contract's signer merely because a field exists to sign. Nor
will it accept a proxy — same repository, same file, same module, same violated
proposition, or the appearance of relatedness. Where an independent authority or
evidence relation establishes materiality, this relation consumes that result
like any other established predicate. Where none does:

```text
no independently established Applicable(P, C)
∧ no legitimate authority over the objective is established
      →  MaterialToClaim(f, C) is unresolved
```

That residual is real and is recorded rather than closed. It is the honest limit
of what materiality can be established from here, and closing it needs an
authority relation that belongs to a different subject than this document.

**Same person, different roles.** Person identity is not the truth-maker and
never has been. One human may legitimately hold authority over a task and also
implement it; that overlap alone neither validates nor invalidates the
objective-setting act, and nothing here requires an independent second person.
What the overlap does not license is amending an already-bound objective from the
implementation side afterwards to clear a finding — that is a later act by a
different role, and it is the same hindsight this document refuses everywhere.

## Findings whose bearing is unresolved

A finding may be established and its bearing on the current claim unresolved.
That state has to be recordable, because both ways of collapsing it lose
something:

```text
FindingEstablished(f)  ∧  Unknown(MaterialToClaim(f, C))
      →  the finding stands as a fact
      →  it may not be treated as material
      →  it may not be treated as immaterial
      →  it does not silently drop out of the claim's reckoning
      →  no RecurrenceCandidate forms from that unresolved relation
      →  and no claim may rest on its irrelevance
```

The last line is the operative one. A convergence claim that depends on this
finding not mattering is resting on something unestablished, and §"Convergence is
a claim" governs that as it governs any other missing basis.

**This is not the recurrence hold, and the two must not be run together.** They
have different subjects and different exits:

```text
unresolved bearing        the finding is established; whether it bears on C is
                          open; no candidate has formed
HOLD_FOR_INVESTIGATION    a candidate has formed; the correction barrier is open
```

Resolving either establishes nothing about the other. A finding whose bearing is
settled may still produce a candidate that holds; a candidate that resolves says
nothing about some other finding whose bearing was never established.

**It is also not `UNAVAILABLE_TO_VALIDATE`.** That validation outcome records a factual
validation that could not be completed. This records a factual validation that
*was* completed, with a relevance question left open. Recording one as the other
would put a fact in the file as though it were a doubt.

## Causal mutation discrimination

§5 requires that a mutation has actually been observed to fail. That is
necessary and never sufficient: also establish that the **intended
discriminator** produced the failure. This section adds a requirement and
removes none — whether a given property is mutation-tested at all is §5's
question, and a kill by the wrong mechanism supports nothing either way.

```text
Test_P(S) == GREEN
Test_P(M¬P(S)) == RED                      necessary
Killed(M¬P) BY IntendedDiscriminator       necessary
```

A mutant that dies to an unrelated short-circuit — an import error, a fixture
that never reached the assertion, another test failing first — has
demonstrated nothing about `P`. Mutations that pass against deliberately broken
code are common enough that observing the RED is not optional — a mutation
suite never watched failing is an assumption wearing a test's clothes.

Where a property is mechanically testable, write the failing witness **first**.
Where it is not, say so rather than manufacturing an executable check.

## Exact-subject evidence

A result qualifies a change when it was produced against that change's exact
subject and still holds for it. Every qualifying result names that subject. Keep
these
distinct, because they are routinely conflated at merge time:

```text
source commit identity   content / tree identity   merge (squash) identity
lineage relation         forge role
```

A squash merge is where these come apart, and it is the standing reminder that
**source-tree equality with the merged result is established per merge, or not at
all**. Compare the trees for the merge in front of you rather than reasoning
from how
the merge was performed. Where the trees agree, that
establishes the merged content is the
qualified content — and nothing about commit identity or lineage, which are
separate questions with separate answers.

**And evidence that was observable once may not be observable later.**

```text
PreviouslyObservedLocally(x)  ⇏  ReproducibleInArbitraryFutureCheckout(x)
```

Record such facts as *established during the qualified procedure* rather than as
*independently re-checkable today* — and establish which of the two actually
holds before writing either. A negative is a claim like any other. "This can no
longer be checked" is as falsifiable, and as easy to get wrong, as the positive
it replaces, and retention mechanisms are rarely all known to the person writing
the sentence.

Note what evidence costs to preserve, and what a digest actually buys. Recording
an object's digest fixes **what that object is** — whatever the object's own
identity covers, which for some objects includes their lineage. What it never
fixes is a *property* of the object: the digest of something that passed a gate
says nothing about the gate once the run that produced the verdict is gone. Any
claim beyond the object's own identity needs its own attestation, made while the
thing being attested is still observable.

A claim about a **relation between two objects** is the sharp case: re-deriving
it later requires both terms, so if one may disappear, the relation must be
attested while both are in hand. A digest recorded then, compared later against
whichever term survives, is an attestation — not a re-derivation — and belongs in
the record as one.

## Epistemic classification

These are typed predicates over a proposition, each admitted by its own basis.
**They are not states of one lifecycle, and none is a rung toward another.**
Several may hold at once when logically compatible. The single dependency among
them is `DEFINED`, which every other predicate presupposes and which is spelled
out below; it is a precondition, not a rank.

That is not a stylistic preference. A field restricted to one of these atomic
values cannot record a proposition that is bounded-verified,
mutation-discriminated, empirically supported and non-refuted at once: it keeps
one and silently drops the rest at the moment
the record is written. Each predicate has its own admission basis, so collapsing
them loses exactly the information the record exists to carry.

- `DEFINED` — the proposition is stated.
- `MECHANICALLY_VERIFIED` — bounded mechanical verification, within a declared
  domain.
- `MUTATION_DISCRIMINATED` — a violating mutant was killed by the intended
  discriminator.
- `EMPIRICALLY_SUPPORTED` — supported over a declared, finite corpus.
- `NON_REFUTED` — bounded adversarial search found no counterexample.
- `REFUTED` — a validated counterexample exists.
- `PROVED` — see below.

`UNAVAILABLE` is deliberately **not** in this list. It is an outcome for an
*objective* — no supportable claim satisfies it — and so has no single
proposition to attach to. §"Best supportable claim" defines it, and nothing else
does. Where a *mechanical check* is what is missing,
say so plainly and name what is absent; that is a different
proposition about a different subject.

A clean review admits `NON_REFUTED` **only within the scope the reviewer
declared**, and admits nothing outside it: it is the absence of a found defect,
not the presence of a proof. A review with no declared scope admits nothing,
because the predicate is defined over a bounded search and an unbounded one has
no bound to record. No number of clean reviews composes into a proof.

**Whether these categories carry an order, and of what kind, is an open formal
question and is intentionally not settled here.** What this document fixes is
narrower and sufficient for process
use: they are typed, they may co-hold, and holding one is never by itself a
reason to record another. Whether some strength order exists over them is left
open; the process rule does not need one, and inventing one here would settle
by fiat what this paragraph declines to settle.

The `DEFINED` dependency named above holds because a proposition that has not
been stated cannot be verified, discriminated, supported or refuted. Holding it
suggests nothing about which others hold.

### Proof is admitted, never reached

`PROVED` is not the top of a ladder. It may be asserted only when the actor can
name the **proposition**, its **domain**, the **assumptions**, and the
**proof or derivation basis** — and when that method actually establishes the
proposition within those bounds. Bounded mechanical verification, mutation
discrimination, empirical support and bounded adversarial search do not
constitute it, individually or by accumulation.

Externality is part of that basis, not a separate rule: the proof or derivation
basis must be one someone other than the claimant can check — a formal system, a
mechanized proof, or a reviewable derivation. Self-attestation does not admit it.
Where no such basis exists for the proposition at hand, record **every** predicate
that proposition's actual basis admits — not "the strongest", since these
predicates carry no order this document is willing to assert — and `PROVED` is
unavailable rather than merely unchecked.
Whether any authority is named is a question for the change, asked each time;
this document does not carry a standing answer, because the answer changes
without this file changing.

The gap is deliberate. Inventing a `ProofRecord`-shaped artifact so that a test
could go green would create the second authority this method exists to
prevent.

## Best supportable claim

```text
CandidateClaims(E, O) = { C | C is supportable from E  ∧  C satisfies objective O }
```

Invoking this is only meaningful if its inputs are stated: the evidence `E`
actually held, the objective `O`, the candidates considered, and the
claim-strength relation being used — an ordering over candidate claims, unrelated
to the epistemic predicates, which carry no order this document asserts. Left
implicit, whichever claim was already preferred
satisfies the construction trivially, and the selection has to be reviewable
rather than merely asserted. `UNAVAILABLE` is not one of the candidates — it is
what is reported when the set turns out to be empty — so an unstated candidate
set makes reaching it unreviewable in exactly the same way.

If a unique maximum exists under that stated relation, select it. Do not assume
one always exists: candidates are frequently incomparable,
and where several maximal claims survive, keep the set or require an explicit
decision rather than inventing a total order.

If `CandidateClaims(E, O)` is empty, the answer is `UNAVAILABLE`. Never
substitute the nearest supportable claim, a semantic guess, or a silently
strengthened one. Weakening has a floor: a claim weakened until it no longer
satisfies the objective is not weaker, it is vacuous, and `UNAVAILABLE` is the
honest report.

## Authority non-escalation

Two different meanings of "authority" must never merge:

```text
semantic authority     which source establishes a proposition
operational authority  which grant permits a protected action
```

`technically qualified ⇏ authorized to merge / release / deploy`. What this
document owns is the non-composition alone: no quantity of semantic evidence
composes into operational authority, and no grant makes a claim better
supported.

**Which** actions are protected, what a grant must contain, and how it is
revalidated belong to a different subject, and this document does not define
them or name their owner. A change subject to those requirements resolves them
against whatever governs it.

## Process contract

**Before the change.** Live subject verified; proposition stated; semantic
authority identified; scope frozen; protected actions enumerated; convergence
boundaries named; discriminants identified.

**Per finding.** Run the sequence in §"Finding validation, and the corrective
cut" — this line is a pointer, not a second statement of it — then record the
disposition, add a permanent witness where the property admits one, discriminate
causally as that section requires, and reconcile the evidence record.

**Before requesting a fresh review.** Exact HEAD fixed; focused tests; full
tests; a mutation test for each property the change claims to guarantee, and for
any property admitting no mutant, an explicit record that none exists and why —
a review-only
change has none, and manufacturing one to satisfy this line is the defect
§"Causal mutation discrimination" forbids; the diff audited against the live
base the change will actually merge into, never a stale local copy of it; no
claim
beyond what the evidence supports; no protected transition performed by
accident.

**After the review.** A clean review admits `NON_REFUTED` on the terms
§"Epistemic classification" sets, and nothing beyond them. First finding →
validate and classify. Before the round closes, the recurrence questions in
§"Mandatory architectural STOP / REDESIGN condition" are put to the reviewer and
answered on the record, and the round's findings are read against the other
triggers in that section — it states them and this line does not restate them.
Any change to the reviewed subject invalidates
prior clean-review evidence: the subject moved.

**Before a protected action.** Confirm the exact evidence subject is still the
one being acted on — evidence goes stale when the subject moves, which is this
document's subject. Everything else that governs a protected action, including
what authorises it and what revalidation it requires, is not this document's;
see §"Authority non-escalation".

## Handoff / closure requirements

- Every answer above must name the test or authority that backs it,
  not just assert it — **including the negative ones**. "No other consumer",
  "no duplicate authority", "nothing else reads this" are claims, and they need
  the search that establishes them and the scope that search covered. An
  unbacked negative is the easiest way for this document to be satisfied by a
  change that violates it.
- **Every round records who reviewed it and their answers to the three
  recurrence questions**, in the form that section requires — the bare answer
  where no candidate formed, and the correction, both findings and the
  proposition it answered for and the ground it answered over named for an
  admitted recurrence, for an out-of-scope candidate, or for a hold, with a hold
  also recording what could not be established, and an out-of-scope candidate
  also recording what *was* established — the differing proposition, or the
  witness's established position outside `Δ1`. Recording only the fires makes
  the rest unreconstructable: a later reader can see that a STOP was declared,
  but not that one was ever considered and correctly declined, nor that a
  question was left open or resolved away from recurrence.
  Together with the per-finding violated propositions and demonstrated defect
  domains, this is what lets someone other than the author check the claim that a
  loop was converging.
- A PR that triggers the STOP condition must additionally record which trigger
  fired and what the spike concluded, even if the final mechanism differs from
  every design considered during patching.
- A structural PR governed by this preflight restates **its own** answers in its
  own PR body, and references this document rather than copying it. A copy is a
  second statement of these rules that will drift from them.
