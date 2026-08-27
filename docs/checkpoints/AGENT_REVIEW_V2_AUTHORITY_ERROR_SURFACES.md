# Checkpoint — closed authority error surfaces (`#200-D` predecessor)

**Status:** implemented and qualified locally; exact-HEAD CI and independent
review are the next gates.

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  base_sha: 5b94632e4c1c243f746248131694933228edab83
  branch: fix/200-d-close-authority-error-surfaces
  forensic_subject: PR #271 (NOT modified)
state:
  published_schema_change_required: false
  operational_runner_ported: false
```

## Why this PR exists

PR #271 built the operational composer and was reviewed six times. Every round
surfaced the same class: a stage's `except` list was narrower than the
exception surface beneath it. Two consumer-side structural attempts reduced it
and neither closed it. #271 returned `STOP_ARCHITECTURAL_BOUNDARY` with the
diagnosis: **each authority's surface is open**, so no amount of consumer
inspection can enumerate it.

Model **B** was selected: close the surfaces at their owners.

> Operational composition may know THAT an authority refused.
> It must not know HOW that authority's internals failed.

## Evidence — measured, not assumed

Each open surface was reproduced through the **real public function** before
anything was changed:

| authority | witness | escaped as |
|---|---|---|
| `acquire_authoritative_diff_v2` | missing `repo_root` | `FileNotFoundError` |
| `acquire_authoritative_diff_v2` | `git` absent from PATH | `FileNotFoundError` |
| `assemble_manifest_from_diff_v2` | contract-invalid `toolrepo_sha` | `ValidationError` |
| `assemble_manifest_from_diff_v2` | `max_lines_per_chunk=0` | bare `ValueError` |
| `build_chunk_payloads_from_profile_v2` | required artifact missing | `PayloadReferenceError` (sibling) |
| `build_chunk_payloads_from_profile_v2` | unreadable declared contract | `PermissionError` |
| `emit_payload_set_v2` | empty payload list | `ValidationError` |
| `extract_review_content_v2` | missing `repo_root` | `FileNotFoundError` |
| `extract_review_content_v2` | binder/contract failure | `ReviewContentBindingError`, `ValidationError` |
| `produce_review_readiness_v2` | artifact contract failure | `ValidationError` |

Note the first two rows: both surfaced as the *same* `FileNotFoundError`, which
is precisely why #271 could not tell "no checkout" from "no git" and kept
misreporting one as the other. The owner can distinguish them, so it now does.

`load_target_profile_v2` and `bind_semantic_grouping_policy_to_target_profile_v2`
were examined and found **already closed**; they are untouched.

## What closure means here

Closure is **not** "catch more". Each authority satisfies both directions:

```text
expected operational failure -> exactly that authority's documented family
unexpected programmer defect -> escapes raw, never sanitized
```

The second direction is load-bearing. Without it every positive test could be
satisfied by `except Exception`, and a bug in this repository would reach an
operator as a reviewed verdict. `TypeError`, `AttributeError`, `AssertionError`,
`KeyError` and `IndexError` are proved to survive every closed authority, and a
structural test parses these modules' ASTs to fail if any of them ever reaches
for `except Exception`/`BaseException`.

That structural guard immediately found two **pre-existing** `except Exception`
in `payload_set_emission_v2`, wrapping digest verification: a `TypeError` from a
defect inside a verifier would have been reported as a *tampered payload*.
Narrowed to the contract/serialization failures that genuine tampering
produces.

## Precision where an originating reason exists — and where it does not

Reason codes stay owned by the earliest authority that can correctly name the
failure. `PayloadReferenceError`'s `payload_required_artifact_missing`,
`ReviewContentBindingError`'s `content_run_identity_mismatch` and every
`DiffAcquisitionError` reason reaching extraction survive the conversion rather
than collapsing into a generic "invalid".

**Where a pydantic failure has no reason code to preserve**, the pre-seal
epoch supplies one instead of collapsing everything into a single opaque code.
That is how `ready`+merged-PR and `ready`-without-green-checks became distinct
reasons again -- see the two-epoch section below. An earlier revision of this
checkpoint recorded that collapse as an accepted loss; it is no longer
accepted, and no longer true.

## Ownership, located precisely

Review round 3 made the boundary sharper in two ways.

**Closing one entry point is not closing the authority.**
`build_chunk_payloads_from_profile_v2` was closed while its singular public
sibling `build_chunk_payload_from_profile_v2` was not, and the very same
witness still escaped it raw. The inventory that drove round 1 covered only
what PR #271 happened to call; it has since been rebuilt from every public
function in these modules.

**The owner was one level deeper than first assumed.**
The unreadable-contract escape was originally converted in the payload
builder. Its true owner is `payload_references_v2`, whose artifact branch had
guarded its read all along while the contract branch beside it did not.
Closing it there fixes every caller at once and names the failure precisely
(`payload_contract_unreadable`) instead of a generic fallback.

That in turn made the builders' own `OSError` clauses redundant, and they were
removed rather than kept "just in case": a consumer re-catching what its
authority already owns is the enumeration habit this change exists to end, and
it masks which authority actually failed.

## Model B was attempted, and falsified — preserved as history

Head `56b4a874` is the falsified subject and is deliberately kept in this
branch's history. Under model B each authority converted `ValidationError` at
its OUTER boundary. That closed the positive direction and made the negative
direction impossible, proved by execution:

```text
internal defect raised as ValidationError, inside assembly
        -> RunAssemblyError(run_assembly_contract_invalid)
```

The outer boundary cannot distinguish "the caller's material violates this
contract" from "our derivation built a malformed object", because both arrive
as the same type from the same call. Three correction rounds each closed real
surfaces and each left that proposition failing. B is not rewritten here as
though it had succeeded.

## Model A* — two-epoch owner validation (selected)

```text
G0 = caller / external / environment material
   -> owner validation, parsing, acquisition classification
   -> V = validated owner material
   ------------------------- SEAL -------------------------
   -> internal derivation
   -> output
```

Only failures BEFORE the seal may be converted from generic parsing,
validation or I/O mechanics into an owner refusal. After it,
`ValidationError`, `ValueError`, `TypeError`, `AttributeError`, `KeyError`,
`IndexError` and unexpected `OSError` are repository defects and escape.

| authority | external ground | seal | pre-seal refusal |
|---|---|---|---|
| diff | `subprocess` + checkout | after acquisition returns bytes | `DiffAcquisitionError` |
| assembly | caller identity + budget | after `_ValidatedAssemblyIdentityInputV2` | `RunAssemblyError` |
| payload | declared artifact/contract files | after references validated | `PayloadBuilderError` |
| payload-set | caller payload collection | after non-empty + binding checks | `PayloadSetBindingError` |
| content | git bytes, hunks, redaction, DLP | after representability check | `ExtractionBlockedError` |
| readiness | decision + identity + checks | after `ready` preconditions | `ReadinessEmissionError` |

Note where the seals are NOT at function entry. Content acquires its external
material *inside* the call, so "validate at entry" would have been wrong; its
seal is after redaction and DLP. Assembly's is early, because its external
material is the caller's own arguments.

### One rule, one definition

No rule is restated to make a seal possible.
`_ValidatedAssemblyIdentityInputV2` imports the contract's own `GitSha`,
`Sha256`, `Repository` and `PositiveInt`. The content check validates against
`ReviewableContentTextV2` itself via a `TypeAdapter`. The `ready`
preconditions moved into `evaluate_ready_preconditions_v2` in `contracts_v2`,
consulted by BOTH the artifact's validator and the emission owner. Nothing
string-matches a validation message.

### Discrimination recovered

Model B collapsed every readiness contract failure into one opaque
`readiness_emission_contract_invalid`, which the previous checkpoint recorded
as an accepted loss. It is no longer accepted: `ready`+merged-PR and
`ready`-without-green-checks are distinct, rule-naming reasons again, obtained
without re-implementing the contract.

### Taxonomy removed

Codes that existed only to launder post-seal `ValidationError` are deleted —
this branch is unmerged, so no compatibility argument preserves a falsified
meaning:

```text
run_assembly_contract_invalid    -> run_assembly_identity_invalid (pre-seal)
payload_contract_invalid         -> deleted (derivation defects escape)
payload_set_contract_invalid     -> payload_set_empty (pre-seal)
content_contract_invalid         -> content_unrepresentable (pre-seal)
readiness_emission_contract_invalid -> ready_requires_* (pre-seal, plural)
```

### The control that had been missing

The earlier defect controls covered `TypeError`, `AttributeError`,
`AssertionError`, `KeyError` and `IndexError` — and omitted `ValidationError`,
the type that carries most internal defects here. That omission is why they
stayed green while the property was false. `ValidationError` is now first in
the control set, and every injection happens with valid caller material,
strictly after that material has crossed its seal. A test that never crosses
the seal is not evidence.

## Readiness partition — the falsifier is resolved

Two heads are preserved as history and neither is rewritten as successful:

```text
56b4a874  model B   positive closure achieved, negative closure falsified
e7e07b83  two-epoch five owners converged, readiness falsified
```

### The partition, by truth-maker

The question was never "which validator clause is caller-visible" but "who
produced this value, relative to `produce_review_readiness_v2`":

```text
C  submitted BY the caller   decision, findings, checks, identity,
                             evaluated_identity, pr_state
T  produced INSIDE           the required-check assessment, and the adjusted
                             decision derived from it
D  derived INSIDE            run_id, evaluated_run_id, head_sha,
                             evaluated_head_sha
```

**No sealed carrier is required, and the evidence is discriminating.** The
emitter derives NEITHER decision source: a `--decision` JSON and a
`compute_readiness_decision_v2` output arrive as the same argument and are
equally caller-material at this boundary. Provenance beyond that belongs to
whoever produced the decision — a different authority's epoch. The partition is
total and disjoint without introducing a token.

### Where the seal actually is

`_apply_required_check_assessment_v2` runs INSIDE the emitter, so
`_assemble_review_readiness_v2` receives TRANSFORMATION output on the main
path, not caller material. The previous round put its pre-seal check there,
which is why a transformation defect became `ready_requires_green_checks` — the
laundering had simply moved one step earlier than the `compute_run_id`
witness.

The epoch now sits at `produce_review_readiness_v2`'s entry, on submitted
material. `_assemble_review_readiness_v2` converts nothing.

### Only one `ready` precondition is decidable pre-seal

`checks` are replaced by the assessment's own, and
`pipeline`/`state`/`reason_codes`/`blockers` are adjusted by it. A submitted
`READY` legitimately degrades to `manual_required` when required-check
authority is not established — refusing it pre-seal would break that
documented shadow-minimal behaviour, and an earlier attempt in this round did
exactly that.

`pr_state` is never transformed, so `ready_requires_open_pr` IS decidable from
submitted material and keeps its precise code. The other four remain
final-material invariants, where a violation means the transformation produced
an incoherent state — a defect, correctly raw. `ready_requires_green_checks` is
therefore no longer a pre-seal refusal, and that is a deliberate, recorded
narrowing rather than a regression.

Stated plainly, because an earlier draft of this checkpoint overclaimed it:
**one** of the five `ready` codes reaches an operator as a named reason. The
other four are the contract validator's internal vocabulary and surface
through the quality-gate CLI as a single `readiness_invariant_violation`.
Recovering more would require either string-matching pydantic messages or
re-implementing contract knowledge in the emitter, both of which this design
rejects.

### Taxonomy

`readiness_material_invalid` is deleted: it could not be given an honest narrow
meaning, since it covered derivation defects and caller faults alike. Its
replacement is `readiness_submitted_material_invalid`, produced by exactly one
pre-seal authority, plus the precise `ready_requires_*` codes.

### Two more controls that could not fail

`test_published_contract_still_rejects_a_wrong_run_id_on_direct_parse` passed
while the `run_id` check was deleted, because `model_validate` on a dict trips
the list-vs-tuple strictness trap first — it was rejecting for the wrong
reason. And a raising-derivation witness fires at the provenance check's own
`compute_run_id` call, so it cannot discriminate a catch around the derivation
lines; that mutant is dropped as unkillable-by-construction rather than left
looking green.

That is the third and fourth control in this branch to fail this way. The
pattern is recorded because it matters more than any single instance.

## Scope fence

`operational_run_v2.py` and `aiops-review-run-v2.py` are deliberately **not**
ported here, and PR #271 is not modified. This predecessor exists so that the
future composition does not need compensating knowledge of internal error
surfaces; adopting it is a separate successor.

No published schema changed. No workflow, target pack, live Router or provider
call is part of this slice.
