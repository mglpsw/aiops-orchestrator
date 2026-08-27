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

**Where they do not, and why.** A pydantic `ValidationError` carries no reason
code to preserve, so `run_assembly_contract_invalid`,
`content_contract_invalid`, `payload_set_contract_invalid` and
`readiness_emission_contract_invalid` are each a single code covering every
contract violation at that boundary. For readiness this is a real loss: the
quality-gate CLI previously printed pydantic's message, which named the failing
rule, so an operator could tell `ready`+merged-PR from `ready`-without-green-
checks and now cannot.

Recovering that discrimination would mean either string-matching pydantic
messages -- fragile, and a re-implementation of contract knowledge this module
documents that it never does -- or surfacing the message itself, which is
unsafe in general: round 2 of review on this PR proved a `ValidationError` from
fragment construction embeds the reviewed diff bytes in `input_value`. Neither
is acceptable, so the single code stands, and the limitation is recorded here
rather than papered over. New codes were
added only where no owner had one:

```text
diff        git_unavailable, repo_root_unusable, diff_acquisition_io_failed
assembly    run_assembly_contract_invalid, run_assembly_budget_invalid
payload     payload_reference_unreadable, payload_contract_invalid
payload_set payload_set_contract_invalid
content     content_contract_invalid
readiness   readiness_emission_contract_invalid
```

## Evaluated caller change

Two tests in `test_review_readiness_emission_v2.py` deliberately asserted the
raw `ValidationError`. Their proposition -- `ready` + merged PR fails closed --
is unchanged and equally strong; only the type moved to the authority's own
family. Evaluated and updated explicitly, with the reason recorded in the
tests, rather than silently.

## Acceptance oracle

A test drives the whole front half catching **one family per stage** and
nothing else -- no `ValidationError`, no `OSError`, no `PayloadReferenceError`,
no `ReviewContentBindingError`, no `except Exception`, no dynamic
`getattr(exc, "reason_code")` -- against inputs that make every stage refuse in
turn. If any surface reopened, the raw exception would escape its narrow
handler and fail the test.

Every refusal is additionally asserted stable, content-free and path-free.

## Scope fence

`operational_run_v2.py` and `aiops-review-run-v2.py` are deliberately **not**
ported here, and PR #271 is not modified. This predecessor exists so that the
future composition does not need compensating knowledge of internal error
surfaces; adopting it is a separate successor.

No published schema changed. No workflow, target pack, live Router or provider
call is part of this slice.
