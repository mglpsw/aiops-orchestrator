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
questions take no "Unknown": an unanswered one is handled where they are stated.

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

Recurrence is a judgement about where findings keep landing, and the party best
placed to make it is the one that found them. Stating it instead as a predicate
over the author's own records makes the measured party the source of the
measurement, which is the shape this document rejects elsewhere.

Four things are distinct here, and collapsing any adjacent pair is what has
repeatedly broken this section:

```text
ReviewerObservation   what someone reports — advisory, whatever its source
      ↓
ValidatedFinding      reproduced, material, violated proposition identified
      ↓
RecurrenceCandidate   two validated findings on one predeclared boundary
      ↓
RecurrenceAdmission   the candidate qualified under the rule below
      ↓
STOP / REDESIGN       policy over admitted recurrence, not over observations
```

Collapsing the first two makes a reviewer's raw output a process decision.
Collapsing the last two makes the party the rule binds the party who decides
whether it binds them. Both have been tried here and both failed.

**Disposition sits on a different axis, and does not reach backwards.** A finding
that was admitted may later be dispositioned `fixed`, `superseded`,
`out_of_scope`, or invalidated by new evidence. None of that erases the admission:

```text
Disposition(f)  does not erase  AdmissionHistory(f)
```

That single property is what keeps the author from deciding whether the rule
fires. It costs nothing — the lifecycle record is kept as usual — and it removes
the defeat entirely, because recurrence counts admissions, which are facts about
what was established at a time, not lifecycle states that can be revised
afterwards.

**Admission is by rule, not by anyone's say-so.** A `RecurrenceCandidate` is
admitted when, and only when, all of the following hold:

```text
ValidatedFinding(f1, B)                  f1 met the predicates, on a boundary
                                         declared before its review
CorrectionAttempt(c, B)                  declared before the next review, naming
                                         its before/after subjects, the boundary,
                                         the proposition it means to restore, the
                                         corrective abstraction and its intended
                                         discriminants
ReviewEpoch(f1) < epoch(c) < ReviewEpoch(f2)
FreshReview(r2) on the after-subject
ValidatedFinding(f2, B)                  f2 met them too, on the same boundary
f2 is not another observation of f1      a distinct defect, not the same one twice
```

The temporal shape is the point. **Concurrence is not recurrence.** Two reviewers
validating the same defect on the same subject is corroboration — the finding is
better evidenced, not repeated — and admitting it would mean that adding
reviewers makes a change likelier to be redesigned, which is an absurd
incentive. Equally, two findings in one boundary with no correction between them
are one episode still open, not a recurrence: nothing was tried and failed. What
recurrence means is that the boundary was corrected, reviewed again on a later
subject, and produced a validated finding anyway.

**A change touching a boundary is not a correction of it.** Version control
establishes the subjects, their order, and the exact delta between them; it
cannot establish that the delta was corrective, and treating it as though it
could would let a cosmetic touch manufacture the barrier:

```text
ChangeTouches(B)   ⇏   CorrectionOf(B)
```

So the barrier is a `CorrectionAttempt`, not a correction, and the authorities
divide:

```text
version control        the two subjects, their ordering, the delta
the attempt record     that this delta was meant to correct B, and how
tests / reproducers    whether the intended discriminants exercise it
the later review       whether a validated finding in B recurred anyway
```

Nothing here has to prove the attempt worked. If `f2` appears, that is evidence it
did not — which is the whole signal. What matters is that the attempt is
**declared before the review that follows it**. An author chooses which correction
to attempt, and that is theirs to choose; what they cannot do is wait until `f2`
lands and then decide the earlier patch was not a correction of `B` after all.
Precommitment is what separates choosing a correction from controlling whether
recurrence can be established.

Note that `f2` need not violate the same proposition as `f1`. Requiring that would
be too narrow: working by boundary exists precisely to catch a *different*
material defect thrown off by the same abstraction.

A second reviewer agreeing that two findings look like the same recurrence is
**supporting evidence for the candidate**, and worth more when the reviewers are
independent — but agreement is not admission, and no quantity of it substitutes
for the conditions above. That is what a model reviewer's output is worth here: a
model can raise observations and support a candidate, and neither step lets it
produce a mandatory transition, because the transition runs off admitted
recurrence rather than off anyone's opinion that a recurrence occurred.

**Validation is predicate-based, not label-based, and each predicate names the
authority that can establish it.**

```text
exact subject / head        the version-control record
reproducer result           executable evidence
violated proposition        the normative contract the change is measured against
boundary predeclaration     the record made before the review
correction barrier          the change history between the two subjects
materiality                 the change's acceptance conditions, against evidence
same-boundary relation      evidence read against the declared boundary
correction barrier          the attempt record, precommitted, plus the delta
```

A finding is validated by meeting those predicates, not by being labelled
`VALID`; an `INVALID` is a claim that contradicting evidence exists and is open to
challenge on that evidence. Where a predicate cannot be established from its
declared authority its value is `UNKNOWN` — not a value the author selects, and
**not a "no"**:

```text
UNKNOWN(admission)          ≠   recurrence disproved
UNKNOWN(correction-barrier) ≠   no correction was attempted
```

It cuts both ways deliberately. An unresolvable predicate that is material to
whether patching continues holds the question open — `HOLD_FOR_ADJUDICATION` —
rather than resolving it in the direction the party holding the question prefers.
Read the first line alone and ambiguity becomes a way to avoid a recurrence;
read the second alone and it becomes a way to manufacture one. Neither is
available: an unestablished barrier does not admit a recurrence, and it does not
clear one either.

Otherwise the rule is defeated by manufacturing ambiguity instead of writing
`INVALID` — the cheaper attack, and the harder one to see.

**The reviewer is not the author, and the record says who they were.**
Observations are worth having because they come from someone who does not pay the
cost of a "yes"; an author reviewing their own change collapses that. Recording
observations without recording who made them leaves that unverifiable. Who
reviews is not this document's to decide, but a change whose reviewer is its
author has not been reviewed for convergence, whatever was recorded.

**Each finding carries the boundary it landed in, assigned by the reviewer who
raised it.** Where reviewer and author disagree about the boundary, the
reviewer's assignment is the recorded one, since the author is the party the rule
constrains. A boundary is not split, renamed or merged into another while a loop
is under way: recurrence asks whether findings keep landing in the same place,
and a place that can be redrawn mid-loop cannot answer that. Naming a new
boundary is not an exception. One named late covers ground no existing boundary
claimed; where it would take ground from one that does, that is a split wearing a
new name, and the finding belongs to the boundary that already existed.

The boundaries named before the loop are a starting proposal, and the author
makes it. That is harmless only because it does not bind assignment: a reviewer
assigns a finding to the boundary it belongs in, joining two of the proposed
boundaries or ignoring the vocabulary entirely if that is what the finding calls
for. A partition drawn finely enough to scatter recurrences is defeated by
assignment, not by a rule against drawing it.

**At the end of each round, these are answered on the record**, over admitted
findings:

1. Did an admitted finding this round land in a boundary that already holds an
   admitted finding from an earlier round of this loop? Name the boundary and
   both findings.
2. Does an admitted finding this round hold that an approach is wrong which an
   earlier admitted finding also held was wrong — as opposed to objecting to how
   that approach was tuned? Name both.
3. From round three onward: does one boundary hold an admitted finding from this
   round and from both preceding ones? Before round three the answer is "no" —
   there is no window to look at.

Question 1 reaches the second landing and question 3 the third; both are kept
because a recurrence across two rounds and a boundary that never stops taking
findings are different signals. An unanswered question is not a "no": a round
whose questions were not put has not been reviewed for convergence, and the
change may not be declared ready on the strength of it.

A "yes" is an admitted recurrence, so the trigger fires on it and the author does
not get to dispose of it — the admission already happened, and disposition does
not reach backwards. What the trigger buys is bounded, which is why it can be
unconditional: the freeze-preserve-spike sequence below, where "does the
abstraction need replacing?" gets answered. A spike concluding the abstraction is
sound is a permitted outcome.

This is not a mechanical check and does not pretend to be one. What it changes is
where authority sits: observations are advisory whatever their source, admission
is by rule, and only admitted recurrence moves the process.

### Triggers, and what follows when one fires

Stop patching and escalate to a redesign the moment **any** of the following
occurs:

- a reviewer answers yes to any of the three questions in §"The recurrence
  conditions are answered, not computed";
- a fix falsifies an assumption an earlier fix in the same change relied on.
  Unlike the recurrence questions this one is noticed while it happens, by
  whoever holds both fixes in mind, and nothing here reconstructs it afterwards
  from a record — the honest statement is that it fires when someone sees it and
  is silent when nobody does;
- an input still inside the change's declared required-acceptance domain
  begins failing (a feature deliberately made unsupported under §3 is a
  contraction, not a regression — but only if that decision is written down
  before the failure, not after it);
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

**The three-round count is process policy, not a theorem.** Nothing here
establishes that three is correct for an arbitrary change. Treat it as a
tripwire to be honoured, or *amended in this file* — never as a quantity to be
reasoned around in the PR body of the change it is currently constraining. A
threshold an actor may reinterpret while it is binding them is not a threshold,
and the indirection is closed too: **an amendment to this paragraph, or to any
rule a STOP trigger depends on — a threshold, the recurrence questions, the
boundary-assignment rule, wherever in this document they are defined — does not
apply to a review loop already under way when it lands.** Otherwise the
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
the convergence boundaries the STOP triggers are evaluated against, and the two
are not interchangeable. Do **not** compensate downstream with vocabulary
scanners, an
ever-widening AST grammar, extra leaf conditionals, or heuristic
reconstruction; each re-derives, one layer below the authority, what was
already thrown away.

The dual obligation is equivalence invariance: where `x ~R y`, the decision
outcome must be materially the same under the declared assumptions. A repair
that starts distinguishing genuinely equivalent inputs has over-corrected.

## Finding validation, and the corrective cut

`ReviewerFinding != ValidatedFinding`. For each finding, in order — every
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

Patch after this classification, not before.
Several similar manifestations frequently belong to one defect class; fixing
them one at a time is how a review loop fails to converge.

Then choose the **smallest correction that restores the intended proposition
across the demonstrated defect domain** — not the smallest textual diff. A
minimal diff that leaves the abstraction false is a larger change deferred,
usually to the next round.

Record a disposition for every finding — `VALID`, `INVALID`, `PARTIAL`, or
`UNAVAILABLE_TO_VALIDATE`. A disposition records what the evidence shows; it does
not make the evidence show it, and either party may dispute one on the evidence.
`VALID` requires the reproducer; `INVALID` requires the contradicting evidence;
`PARTIAL` requires **both**, because it asserts two
things at once — that some portion reproduces and that the rest does not hold —
and a `PARTIAL` backed on one side only is an unexamined claim wearing a
disposition. Never resolve on less. The fourth is for a
finding that could not be validated either way: it requires the attempt made and
the exact limitation that blocked it, and it leaves the finding **open** — it is
a record of an unfinished validation, not a way to close one. Never patch merely
because a reviewer suggested text.

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
  recurrence questions**, in the form that section requires — the bare answer where it is
  "no", and the boundary and finding named where it is "yes". Recording only the
  fires makes the negative unreconstructable: a later reader can see that a STOP
  was declared, but not that one was ever considered and correctly declined.
  Together with the per-finding boundary assignments, this is what lets someone
  other than the author check the claim that a loop was converging.
- A PR that triggers the STOP condition must additionally record which trigger
  fired and what the spike concluded, even if the final mechanism differs from
  every design considered during patching.
- A structural PR governed by this preflight restates **its own** answers in its
  own PR body, and references this document rather than copying it. A copy is a
  second statement of these rules that will drift from them.
