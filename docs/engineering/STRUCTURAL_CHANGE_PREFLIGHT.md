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
one reports that no candidate formed, that a recurrence was admitted, or that a
formed candidate is held for investigation, and the supporting one may be left
undecided. A hold is recorded as neither a "yes" nor a "no".

Two uncertainties live in this document and must not merge. This section's
"Unknown" is a preflight question nobody can answer yet, and it blocks
implementation. A recurrence hold is a candidate that did form and whose
qualification is unresolved, and it blocks the convergence claim. Different
subjects, different process contexts; neither is the other's answer, and
resolving one establishes nothing about the other.

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
measured party the source of the measurement. What carries it is the one pair the
author cannot tune after the fact and no reviewer can assert into being: a
precommitted statement of what a correction would discriminate, and later
admissible evidence about whether it does.

Four things are distinct here, and collapsing any adjacent pair is what has
repeatedly broken this section:

```text
ReviewerObservation   what someone reports — advisory, whatever its source
      ↓
ValidatedFinding      reproduced, material, violated proposition identified
      ↓
RecurrenceCandidate   a validated finding on the subject a declared correction
                      produced, that correction answering an earlier validated
                      finding — structural, not a similarity judgement
      ↓
RecurrenceAdmission   evidence establishes that the correction's own declared
                      discriminants are still defeated
      ↓
STOP / REDESIGN       policy over admitted recurrence, not over observations
```

Collapsing the first two makes a reviewer's raw output a process decision.
Collapsing the last two makes the party the rule binds the party who decides
whether it binds them. Both have been tried here and both failed.

**Disposition sits on a different axis, and does not reach backwards.** A
validated finding may later be dispositioned `fixed`, `superseded`,
`out_of_scope`, or invalidated by new evidence. None of that reaches back into
what was established at the time. Two histories are kept, and they are not one
record:

```text
ValidationHistory(f)            f met the finding-level predicates at its review
                                epoch — a fact about that epoch

RecurrenceAdmissionHistory(c)   candidate c qualified under the rule below at the
                                epoch it qualified — a fact about that epoch

Disposition(f)  erases neither
```

A finding is never "admitted": findings are validated, candidates are admitted,
and there is no `FindingAdmission` here or anywhere below. Invalidating `f1`
afterwards changes `f1`'s disposition and leaves standing both the validation
that held at its epoch and any recurrence admitted on the strength of it.
`FinalState != History` throughout — a later state never rewrites the epoch it
succeeded.

That single property is what keeps the author from deciding whether the rule
fires. It costs nothing — the lifecycle record is kept as usual — and it removes
the defeat entirely, because recurrence counts admissions, which are facts about
what was established at a time, not lifecycle states that can be revised
afterwards.

**Candidate formation runs off a declared correction, not off a boundary.** A
`RecurrenceCandidate` exists when, and only when, all of the following hold:

```text
ValidatedFinding(f1) on S1               f1 met the finding-level predicates
CorrectionAttempt(c) answering f1        declared before the review that follows
                                         it, naming its before/after subjects,
                                         the proposition it means to restore, the
                                         corrective abstraction, and its intended
                                         discriminants D(c)
before_subject(c) is S1 or descends      the attempt answers f1 and sits on the
                  from it                same line of development
after_subject(c)  = S2                   the attempt produced the subject that
                                         was then reviewed
FreshReview(r2) on S2, a later subject
ValidatedFinding(f2) on S2               f2 met them too
Subject(f2) = Subject(r2) = S2           and f2 came out of that review of that
                                         subject, not out of a report against an
                                         earlier one
ReviewEpoch(f1) < epoch(c) < ReviewEpoch(f2)
```

Nothing in that list is a similarity judgement and nothing in it is anyone's
assignment. Every row is a subject relation, an epoch relation, or a record the
author wrote before the finding that would test it existed. A candidate is
structurally present or it is not, and asking a second person does not change
which.

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
validated findings on ordered subjects, and whether it did what it said belongs
to the next step. It is not a recurrence and fires nothing. Formation is
deliberately cheap, because the expensive question — did the correction's own
discriminants survive? — is where evidence is needed, and where it can be held
open rather than answered by default in whichever direction the missing
information happens to favour.

**Admission is by evidence, not by anyone's say-so.** A formed candidate is
admitted when, and only when, the correction barrier holds across it, and one
derived relation is what establishes that barrier:

```text
DefeatsCorrectionAttempt(f2, c)   the evidence behind f2 establishes that a
                                  discriminant in D(c) still fails on the exact
                                  after-subject the attempt produced
```

This introduces no authority, no record kind and no entity. It is derived, each
time, from two things that already exist: what `c` declared it would
discriminate, and the reproducer that established `f2`. The question is narrow
and checkable — *`c` said `d1` would hold on `S2`; does `d1` hold on `S2`?* — and
it is answered by the same evidence that validated `f2`, against the same exact
subject.

```text
c declares:      restore proposition P, discriminants d1, d2
f2 establishes:  d1 still fails on S2, the exact after-subject of c
                 →  DefeatsCorrectionAttempt(f2, c)
```

What may **not** establish it is resemblance of any kind. "The same abstraction
is wrong", "these look like one defect", "this is the same approach" — none is
the relation, whoever says it and however many agree. The relation is a defeated
declared discriminant, or it is nothing.

**Discriminants are read as they were declared.** `D(c)` is fixed at `epoch(c)`,
which is before the review that produces `f2`. A discriminant rewritten once `f2`
exists is not `D(c)` and does not participate; the precommitment is the entire
reason this relation cannot be tuned toward the answer its author would prefer.

The subject relations carry as much weight as the epochs. An attempt whose
after-subject is not the one reviewed is an attempt at something else, however
well its dates line up; an attempt whose before-subject is unrelated to `S1` is
not answering `f1`. Requiring `before_subject(c)` to *descend from* `S1` rather
than equal it leaves room for the ordinary case where other work lands between
the finding and the attempt.

The barrier is established, unsettled, or shown not to hold — and that third
outcome is reached only by positive evidence that the declared discriminants
survived, never by the absence of a record:

```text
established       DefeatsCorrectionAttempt(f2, c) holds on the evidence
                  →  the candidate is admitted
shown not to hold the evidence establishes that every discriminant in D(c)
                  survives on S2  →  the correction did what it said, and there
                  is no recurrence to admit
unsettled         the evidence settles neither  →  HOLD_FOR_INVESTIGATION
```

Keep the third row distinct from the negative this document refuses elsewhere.
"This correction worked" is a claim about `c` and `S2` backed by a reproducer,
and it needs no authority over any interval. "No correction was attempted" is a
claim about everything that did not happen over a whole interval, and it has no
legitimate source here. Establishing the first is ordinary; the second remains
unreachable.

`HOLD_FOR_INVESTIGATION` is not a verdict about the boundary. It does not say a
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
            about the boundary
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
it. What is left is a decision about the *work* — defer it, abandon it, restart
from another design, or permit some further transition where whatever governs the
change allows — and that decision belongs to the operational authority described
in §"Authority non-escalation". None of it writes a value into `P`:

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
incentive. Equally, two findings in one boundary with nothing tried between them
are one episode still open, not a recurrence — but that is a statement about what
recurrence *means*, not a disposition anyone may reach from a silent record. What
recurrence means is that the boundary was corrected, reviewed again on the
corrected subject, and produced a validated finding anyway. Where the record does
not show that, the question is held, not answered.

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
  discriminants defeated          →  recurrence admitted, whether or not the
                                     second finding is "the same defect"
  discriminants survived          →  the correction did what it said; no
                                     recurrence
  neither established             →  HOLD_FOR_INVESTIGATION; the candidate stands
later subject, no declared        →  no candidate, and no mandatory transition.
correction                           A structural signal worth investigating
```

A second reviewer agreeing that two findings look like the same recurrence adds
nothing to the relation above. Agreement is not evidence that a declared
discriminant failed; independence does not make it so; and no quantity of it
substitutes for the conditions above, because concurring opinions compose into a
stronger opinion and never into an established relation. That is what a model
reviewer's output is worth here, and a human reviewer's too: either can raise
observations and point at evidence, and neither step produces a mandatory
transition, because the transition runs off a defeated precommitment rather than
off anyone's view that a recurrence occurred.

**Validation is predicate-based, not label-based, and each predicate names the
authority that can establish it. The predicates sit at two levels, and the
levels must not be mixed.**

Finding-level predicates are everything a finding needs to stand on its own.
Each is knowable at the epoch of that finding, using nothing that happens after
it:

```text
exact subject / head        the version-control record
reproducer result           executable evidence
violated proposition        the normative contract the change is measured against
materiality                 the change's acceptance conditions, against evidence
```

A boundary assignment is recorded alongside these and is deliberately **not** one
of them. It organises the record rather than establishing anything, so a finding
whose boundary is disputed is still a validated finding, and an unresolved
membership blocks nothing.

Candidate-level predicates relate two findings and only exist once both do.
They belong to a `RecurrenceCandidate` — to forming one, or to qualifying one
already formed — never to the validation of a single finding:

```text
correction delta            the version-control record: the before/after
                            subjects, their ordering, and what changed
correction attempt          the precommitted record: that this delta was declared
                            to answer f1, with which discriminants D(c)
epoch ordering              the version-control and review records together
                            — these three form the candidate

defeated discriminant       the reproducer behind f2, read against D(c) on the
                            exact after-subject
                            — this one qualifies a formed candidate
```

That split is load-bearing. An earlier draft scoped one undivided table over
"a finding is validated by meeting those predicates", which pulled the
correction barrier into the validation of `f1` — and it does not exist yet when
`f1` is raised. Validating `f1` then required
knowing about a correction made in response to `f1` and about a later `f2`, so
no first finding could be validated and no first recurrence could ever be
admitted. A finding is validated by information available when it is found;
anything else is a fact about a pair.

The two rows that were both called `correction barrier` are the same repair
seen from the other side. They were never rival authorities for one relation:
version control establishes the delta and the order of the subjects, and the
precommitted record establishes that the delta was *meant* to answer `f1` and
what it claimed it would discriminate.
Naming both the same thing made the falsified reading — history alone
establishes the barrier — look like a live alternative to the correct one. The
barrier is the two together:

```text
correction barrier  =  correction delta
                     ∧  correction attempt
                     ∧  DefeatsCorrectionAttempt(f2, c)

ChangeTouches(f1's ground)  ⇏  CorrectionAttempt answering f1
```

All three conjuncts must be in hand. The first two form the candidate and the
third qualifies it; a barrier with any one missing is not established, and an
unestablished conjunct is never read as a settled answer in whichever direction
happens to be convenient. Note what the third conjunct excludes: a declared
correction that ran and demonstrably worked leaves the first two satisfied and
the barrier unestablished, which is the correct result rather than a gap.

A finding is validated by meeting the finding-level predicates, not by being labelled
`VALID`; an `INVALID` is a claim that contradicting evidence exists and is open to
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
never buy one": under this version, omitting the record means no candidate forms
and no mandatory recurrence can be established at all, which is the largest thing
omission has ever bought here. Nor does it buy a clearance: nothing is
established about the history, the findings and their validation stand, and two
validated findings on related ground with no record of what was tried between
them remain a structural signal worth investigating.

**This is the sharpest open edge in this version, and it is recorded rather than
smoothed over.** Closing it needs a rule about what an unexplained correction
history costs a change that wants to be declared convergent — a question about
promotion rather than about what this section establishes, and one this version
deliberately leaves open instead of answering in whichever direction is
convenient.

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
`f1`'s boundary to be declared before `f1`'s review, which meant a genuinely
emergent boundary could never hold an admitted recurrence at all, and the stop
condition failed open exactly where discovery was real.

Predeclaration is still preferred, because a boundary named in advance was named
without knowing which findings would land in it. What it buys is evidential
weight, not admissibility.

**A predeclared label is a proposal; participation is what freezes an identity.**
The two are distinct, and an earlier draft's failure to separate them made the
same assignment both permitted and forbidden — a reviewer was free to join two
proposed boundaries, while those boundaries were frozen against merging. A label
that has never held a `ValidatedFinding` has no history to protect. It is
vocabulary the author offered, and the author is the party the rule constrains,
so it cannot bind the reviewer:

```text
proposed label, no assignment yet   →  vocabulary; a reviewer may adopt it, join
                                       two of them, or ignore the set entirely
first BoundaryAssignment lands      →  that boundary now has an identity in the
                                       history, and it is frozen from here
```

This is what defeats a partition drawn finely enough to scatter recurrences. An
author who predeclares fifty narrow labels has proposed fifty names, not fifty
protected boundaries; the reviewer who meets the first finding spanning several
of them assigns it to the boundary it actually belongs in and the redundant
labels never acquire an identity. No reviewer is forced to pick a cell out of a
partition that no finding has ever occupied.

Once a boundary has participated, the protection is absolute and runs the other
way — including against the same joining that was free a moment earlier:

```text
retroactive split                   forbidden
retroactive merge or join           forbidden
retroactive semantic reassignment   forbidden
identity-preserving rename          allowed, recorded, identity unchanged
```

Nothing may be redrawn in a way that changes a recurrence already established or
prevents one already implied. A rename that preserves identity is a rename; one
that moves the ground it covers is a reclassification, and after participation it
is not available. Emergent boundaries freeze the same way — at their first
occurrence rather than at the start of the loop — because that occurrence is
exactly the participation that creates the identity.

Naming a new boundary late is not an exception to any of this. One named late
covers ground no existing boundary claimed; where it would take ground from a
boundary that has already participated, that is a split wearing a new name, and
the finding belongs to the boundary that already existed.

**`Admitted` means one thing here, and it is about recurrence.** A finding that
meets the finding-level predicates is a **validated finding** — never an
"admitted" one. `RecurrenceCandidate` and `RecurrenceAdmission` are the only
things the word covers, which is why the two histories the disposition rule
above keeps are named for their subjects and not for each other:
`ValidationHistory` takes a finding, `RecurrenceAdmissionHistory` takes a
candidate, and there is no third and no `FindingAdmission`, implied or
otherwise. The questions below run over validated findings in the record.
Invalidating `f1` afterwards changes `f1`'s disposition; it leaves the
validation that held at `f1`'s epoch and any recurrence admitted on the
strength of it exactly where they were.

**At the end of each round, these are answered on the record**, over validated
findings. The first two carry a transition; the third does not, and the
difference is stated rather than left to the reader:

**Load-bearing.** Each of these forms candidates and then reports what the
correction barrier did with them, so each answers in exactly one of three ways:

```text
no candidate formed      the facts do not assemble a RecurrenceCandidate. This
                         is the only place a bare "no" is honest.
recurrence admitted      candidate formed, barrier established. The trigger
                         fires and the freeze-preserve-spike sequence runs.
held for investigation   candidate formed, barrier not established. The trigger
                         does not fire, the candidate stands, and the change may
                         not be declared ready or convergent on that history.
```

Those are three classes of result, not a schema: nothing here asks for an enum, a
field name or a machine-readable form. Naming them is only what stops a hold from
being written down as one of the other two. Neither question reaches a transition
from landings alone.

1. Did a validated finding this round land on the subject a declared correction
   produced, where that correction answered an earlier validated finding of this
   loop? Name the correction, both findings, and the discriminants `c` declared.
   That forms a candidate; the evidence then decides between an admitted
   recurrence, a correction shown to have held, and a hold. A candidate whose
   evidence settles neither is not thereby a "no"; it is a hold.
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

3. Does a validated finding this round hold that an approach is wrong which an
   earlier validated finding also held was wrong — as opposed to objecting to
   how that approach was tuned? Name both, or answer `undecided` where the two
   cannot responsibly be compared — the word this section already uses below, and
   not either of the two unknowns above, neither of which this question carries.

This question was load-bearing in an earlier draft and is deliberately demoted.
Deciding that two findings condemn "the same approach" is a judgement with no
named authority: there is no record that fixes what an approach is, and no
evidence that settles when two objections are to one approach rather than two.
Left as a trigger it was the cheapest way for a single reviewer to produce a
mandatory stop — the mirror of the defect the whole section exists to prevent,
since the party who found the defect would decide that the process must halt.
A "yes" is recorded and may motivate a spike or a sibling search. It may **not**
reach a load-bearing predicate, directly or by influence: it cannot change a
boundary membership, cannot supply or strengthen `DefeatsCorrectionAttempt`,
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
reports one of the three classes above and names the candidates behind it — the
boundary and both findings for an admitted recurrence or for a hold, and for a
hold also what could not be established. A bare "no" is available only where no
candidate formed at all. The supporting question must be asked and answered too, and may be
answered as undecided; what it may not do is transition.

A "yes" to a load-bearing question, once the candidate qualifies under the
admission rule, is an admitted recurrence, so the trigger fires on it and the
author does not get to dispose of it — the admission already happened, and disposition does
not reach backwards. What the trigger buys is bounded, which is why it can be
unconditional: the freeze-preserve-spike sequence below, where "does the
abstraction need replacing?" gets answered. A spike concluding the abstraction is
sound is a permitted outcome.

This is not a mechanical check and does not pretend to be one. What it changes is
where authority sits: observations are advisory whatever their source, admission
is by rule, and only admitted recurrence moves the process.

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
  recurrence questions**, in the form that section requires — the bare answer
  where no candidate formed, and the boundary and both findings named for an
  admitted recurrence or for a hold, with a hold also recording what could not be
  established. Recording only the fires makes the rest unreconstructable: a later
  reader can see that a STOP was declared, but not that one was ever considered
  and correctly declined, nor that a question was left open.
  Together with the per-finding boundary assignments, this is what lets someone
  other than the author check the claim that a loop was converging.
- A PR that triggers the STOP condition must additionally record which trigger
  fired and what the spike concluded, even if the final mechanism differs from
  every design considered during patching.
- A structural PR governed by this preflight restates **its own** answers in its
  own PR body, and references this document rather than copying it. A copy is a
  second statement of these rules that will drift from them.
