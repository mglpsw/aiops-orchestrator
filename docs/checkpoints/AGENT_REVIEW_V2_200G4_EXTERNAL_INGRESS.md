# Checkpoint — `#200-G4`: external material ingress closure

**Status:** implemented and locally qualified; independent adversarial review
pending (required before any Ready/merge consideration, which this checkpoint
does not request).

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  base_sha: f70af2e635643d1ee96ba431857002ae079b502b
  branch: feat/200-g4-external-material-ingress
  issue: 282 (#200-G4)
  parent_issue: 200
  forensic_source: PR #277 (CLOSED, unmerged), commit fe5b0705c66087fb50312865b545c8a3a2b359e0
  forensic_classification: FROZEN_FORENSIC_200F, ported WITH REVALIDATION only
state:
  merged: false
  ready_marked: false
  ci_workflow_modified: false
  router_or_live_provider_called: false
```

`base_sha` matches the recovery checkpoint's `master_sha` exactly (no drift
observed at task start). The recovery checkpoint is at
`origin/docs/200-post-f-recovery-checkpoint:docs/checkpoints/AGENT_REVIEW_V2_POST_200F_RECOVERY.md`
(not yet on `master`), fetched and read before any code change.

## Why this exists

`#277` ("`#200-F`", branch `feat/200-f-derivable-operational-boundary`) built
a two-process operational run for AgentReview v2, was reviewed repeatedly, and
stopped on `STOP_200F_ARCHITECTURE_NOT_CONVERGING`. Its inner-authority
channel (`operational_inner_control_v2.py`) was independently refuted twice
for different reasons and is `DO_NOT_PORT`. Independent of that authority
question, round-2 review of the same branch found a **second, narrower**
defect class still open and never fixed: several caller-controlled material
sources were still read or parsed *after* validated scalar input, producing
raw exceptions, filesystem paths, and (in one shape) the caller's own bytes on
stderr. The post-`#200-F` recovery decomposed the remaining work into four
independent primitives (G1 identity, G2 DLP, G3 scope, G4 this slice) plus a
G5 recomposition that only starts once G1-G4 are each independently
`PRIMITIVE_NON_REFUTED`. This checkpoint is G4's delivery.

## What is, and is not, on `master` at the base SHA

Per the recovery checkpoint (re-confirmed by grep at task start): **none** of
`operational_ingress_v2.py`, `operational_refusal_v2.py`,
`operational_inner_control_v2.py`, `operational_run_v2.py`,
`operational_subject_v2.py`, `operational_bounded_git_v2.py`,
`operational_result_binding_v2.py`, `operational_scope_v2.py`, or
`scripts/aiops-review-run-v2.py` exist on `master`. All of `#277`'s work was
branch-only and died unmerged. `diff_acquisition_v2.py`, `profile_loader_v2.py`,
`semantic_grouping_policy_v2.py`, and `contracts_v2.py` do exist, in a form
that predates `#277`'s branch-only edits to them (confirmed by diff against
`fe5b0705`).

## Scope decision: this is the ingress boundary, not the two-process product

`scripts/aiops-review-run-v2.py` as delivered here is **not** a port of
`#277`'s two-process outer/inner architecture. That architecture is
`PORT_AS_CONCEPT` for `#200-G5` (re-derive the channel, do not copy it), its
inner-authority channel is `DO_NOT_PORT` (refuted twice on authority
grounds), and subject materialisation / scope completeness / run composition
belong to `#200-G1` / `#200-G3` / `#200-G5` respectively — none of which are
qualified on `master` yet. Building fake stand-ins for them here to make a
"complete-looking" script would smuggle unreviewed authority into a slice
whose only job is the ingress boundary, and would produce throwaway code G5
would have to discard.

What is delivered instead is a single-process script that owns exactly the
ingress boundary: every caller-controlled material source a real run
receives is validated or safely read, in the same shapes and against the
same real contracts (`TargetProfileV2`, `SemanticGroupingPolicyV2`,
`ChunkResponseEnvelopeV2`) a real run would use, before being handed
downstream. It intentionally does not materialise a git subject, does not
compute review scope, and does not execute a review — it emits a summary of
what ingress observed and validated. `#200-G5` is where this composes with
G1/G3's outputs into an actual run.

## Inventory — every caller-controlled material source

| # | Source | Provenance | Disposition | Where |
|---|---|---|---|---|
| 1 | 9 scalar CLI flags (`--repo`, `--pr-number`, `--base-sha`, `--head-sha`, `--tested-merge-sha`, `--toolchain-digest`, `--event-type`, `--event-action`, `--delivery-id`) | argv | **PORTED unchanged** — `_PublicInputsModelV2` / `validate_public_inputs_v2`, proven sound at `#277`; reused as-is | `operational_ingress_v2.py` |
| 2 | `--profile` file path | argv | **PORTED unchanged** — `validate_existing_file_v2` (absolute + exists) | `operational_ingress_v2.py` |
| 3 | `--profile` file *content* | file bytes, caller path | **PORTED unchanged pattern**, content routed through `load_target_profile_text_v2` (stricter than generic JSON — rejects ambiguous YAML) | `operational_ingress_v2.py` + `profile_loader_v2.py` |
| 4 | `--grouping-policy` file path | argv | **PORTED unchanged** — `validate_existing_file_v2` | `operational_ingress_v2.py` |
| 5 | `--grouping-policy` file *content* | file bytes | **PORTED unchanged pattern** — `validate_caller_document_v2` against `SemanticGroupingPolicyV2` | `operational_ingress_v2.py` |
| 6 | `--diff` file path | argv | **PORTED unchanged** — `validate_existing_file_v2` | `operational_ingress_v2.py` |
| 7 | `--diff` file *content* (bytes) | file bytes | **PORTED unchanged pattern** — `read_caller_document_text_v2` (OSError/UnicodeDecodeError -> typed refusal) | `operational_ingress_v2.py` |
| 8 | `--diff` file *content* (structural parse) | file bytes | **NEW fix** — `parse_unified_diff` raises `DiffAcquisitionError` for a structurally unreadable diff; that class was a bare `ValueError` on `master`, not a family member, so it would have escaped this script's `except ExpectedOperationalRefusalV2` uncaught. Mixed `ExpectedOperationalRefusalV2` in (additive, one line) | `diff_acquisition_v2.py` |
| 9 | `--responses` directory path | argv | **PORTED unchanged** — `validate_existing_directory_v2` | `operational_ingress_v2.py` |
| 10 | `--responses` individual entries (one file per requested chunk) | file bytes, caller-influenced path component | **NEW, the mandatory RED witness** — `read_offline_response_document_v2`: absent is `None` (ordinary "not yet answered" state), present-and-malformed (bad JSON, non-UTF-8, schema-invalid) is a typed refusal via the same document pattern, chunk id is never echoed, and a defence-in-depth containment check rejects a chunk id that would resolve outside the responses directory | `operational_ingress_v2.py` |
| 11 | `AGENT_REVIEW_INNER_CONTROL_FD_V2` environment variable | process environment (caller/CI-workflow controlled) | **NEW fix** — `resolve_inner_control_fd_v2`: absent/empty -> no channel; out-of-range -> typed refusal instead of `OverflowError`; `0`/`1`/`2` (stdio) explicitly refused instead of accepted-and-later-hung-on | `operational_ingress_v2.py` |
| 12 | argparse usage-error path (`--unknown-flag`, missing required flag, etc.) | argv, verbatim | **NEW fix** — `NoEchoArgumentParserV2.error()` override discards argparse's own message (which embeds argv text for several error classes) and raises a typed, content-free refusal instead | `operational_ingress_v2.py` |
| 13 | Ambiguous-YAML profile documents (merge-key / duplicate-key collisions) | file bytes | **NEW fix, precedent-matched** — `AmbiguousProfileDocumentV2` was a bare, un-coded `Exception` on `master`; `#277` independently concluded (documented in its own diff) that it must join the family with a reason code because it no longer qualifies for the module-private exemption (public name). Applied the identical fix | `profile_loader_v2.py` |
| 14 | Replay fixtures (`event_type`/`event_action` = `"replay"`) | argv (part of source #1) | **Covered by #1** — `RunOriginV2`'s own cross-field rule already requires the replay pair; no separate source | `operational_ingress_v2.py` / `contracts_v2.py` |
| 15 | `--target-root` directory path | argv | **Scoped to path-existence only** — `validate_existing_directory_v2`; reading git content from it (subject materialisation) is `#200-G1`'s authority, not wired here. Dropped from this script's own flag set because nothing in the ingress-only surface consumes it yet; documented rather than silently omitted | this document |

## The mandatory RED witness, reproduced

Before writing the fix, reproduced the exact predecessor shape directly
(`tests/agent_review/test_operational_ingress_v2.py::test_the_raw_predecessor_shape_really_did_leak`,
and manually at the REPL):

```pycon
>>> from app.agent_review.contracts_v2 import ChunkResponseEnvelopeV2
>>> ChunkResponseEnvelopeV2.model_validate_json("{not valid json")
pydantic_core._pydantic_core.ValidationError: 1 validation error for ChunkResponseEnvelopeV2
  Invalid JSON: key must be a string at line 1 column 2 [type=json_invalid,
  input_value='{not valid json', input_type=str]
```

`input_value='{not valid json'` is pydantic's own echo of the offending
bytes — for a real caller mistake (or a compromised/misconfigured upstream
producer), this is exactly the shape by which a credential embedded in a
malformed response document would have reached stderr. The non-UTF-8 half
was reproduced the same way (`Path.read_text` raising a raw
`UnicodeDecodeError`). Both are fixed via
`read_offline_response_document_v2`, verified end-to-end through the real
script over a subprocess in
`tests/agent_review/test_aiops_review_run_v2_ingress.py`.

## The other three "also open at STOP" items, RED then GREEN

All four were reproduced raw first, then covered by both a unit-level test
(`test_operational_ingress_v2.py`) and a process-level test
(`test_aiops_review_run_v2_ingress.py`) that runs the real script as a
subprocess and inspects real stdout/stderr/exit code:

| Item | RED reproduction | Fix | GREEN test |
|---|---|---|---|
| temp-dir cleanup skipped on refusal | `test_operational_workspace_v2.py::test_pre_fix_ordering_really_did_leak` reproduces `#277`'s exact `mkdtemp` before `try` ordering and shows the directory survives a refusal | `operational_workspace_v2.temp_workspace_v2` — a context manager whose `try/finally` is established in the same frame that creates the directory, so there is no window to obtain one without the other | `test_operational_workspace_v2.py` (4 tests: typed refusal, `diff_unreadable`-shaped refusal, and a genuine defect all still get cleaned up) |
| `OverflowError` on out-of-range control fd | `test_operational_ingress_v2.py::test_the_raw_overflow_shape_really_did_happen` — `os.fstat(10**30)` raises a raw `OverflowError` | `resolve_inner_control_fd_v2` bounds the parsed value to `[3, 2**31-1]` before it is ever used | `test_the_277_witness_out_of_range_fd_is_refused_not_an_overflow_error` + subprocess-level `test_red_witness_out_of_range_control_fd_is_a_typed_refusal_no_overflow_error` |
| `fd=0` hangs forever on stdin | Structural: refusing `fd < 3` means no code path in this codebase can ever attempt the blocking read that caused the hang | Same function; `_MIN_CONTROL_FD_V2 = 3` excludes the three inherited stdio descriptors | `test_the_277_witness_fd_zero_is_refused_not_used_to_read_stdin` + subprocess-level `test_red_witness_control_fd_zero_does_not_hang` (tight `timeout=30`, so a regression fails fast instead of hanging the suite) |
| argparse usage-error echoes argv | `test_operational_ingress_v2.py::test_the_raw_argparse_echo_shape_really_did_leak` — stock `argparse.ArgumentParser.error()` really does embed argv text in its message | `NoEchoArgumentParserV2.error()` discards `message` entirely and raises a typed, content-free refusal | `test_the_277_witness_usage_error_does_not_echo_argv_text` + subprocess-level `test_red_witness_usage_error_does_not_echo_argv_secret` and a second shape (`test_red_witness_missing_required_flag_does_not_echo_the_partial_argv`) |

## Mandatory bidirectional invariant

Tested in both directions, repeatedly, not just once:

* **External failure -> typed refusal**: every RED witness above, plus the
  full ported `#277` test suite for the nine scalars and the two
  single-document sources (unchanged in intent).
* **Internal defect -> raw, unmasked**: `test_a_genuine_internal_assertion_error_is_not_swallowed_by_response_reading`,
  `..._by_document_validation`, `test_a_genuine_internal_defect_in_argparse_action_callback_is_not_swallowed`,
  `test_resolve_inner_control_fd_does_not_swallow_a_non_str_programmer_defect`
  (all unit-level, injecting a real `AssertionError`/type confusion into the
  *mechanism*, not the caller material), plus a full-process version
  (`test_a_genuine_internal_defect_still_produces_a_raw_traceback`) that
  monkeypatches `validate_public_inputs_v2` inside the real script via
  `runpy` and asserts a raw Python traceback reaches stderr with a
  non-family exit code.

**This invariant testing found a real bug, corrected during this slice, not
merely asserted:** `validate_caller_document_v2`, ported from `#277`
unchanged, originally caught bare `Exception` around
`model.model_validate_json(raw_text)` ("normalised; the cause must not
escape" — `#277`'s own words). Writing
`test_a_genuine_internal_assertion_error_is_not_swallowed_by_document_validation`
first (an injected `AssertionError` from a deliberately broken model) proved
this laundered a genuine internal defect into an ordinary-looking
`operational_ingress_document_invalid_*` refusal. Narrowed the catch to
`except pydantic.ValidationError` — confirmed sufficient for every real
model this codebase passes through the function (`SemanticGroupingPolicyV2`,
`ChunkResponseEnvelopeV2`; pydantic-core wraps both malformed-JSON and
schema-violation failures in `ValidationError` for `model_validate_json`,
confirmed by direct REPL reproduction) — then reconfirmed both directions
green. This is exactly the "over-catching hides real bugs" failure mode the
G4 mandate named as a known recurring pattern in this codebase's history;
recorded here because a ported pattern being "proven sound" at its origin
does not mean it stays sound unchanged once its own bidirectional invariant
is actually tested against, rather than merely asserted in prose.

## Mutation testing (commit, mutate, confirm RED, restore, confirm GREEN)

Base commit for all five mutations: `7a115c9` (this slice's implementation
commit). Each mutation was applied, the relevant test(s) run and confirmed
failing, then `git checkout --` restored the file and the full new-test
suite was reconfirmed green before the next mutation:

1. `validate_caller_document_v2`'s `except ValidationError` replaced with
   `except NameError` (never actually catches) -> 4 tests failed (unit +
   subprocess RED witnesses for malformed responses/documents) -> restored,
   green.
2. `resolve_inner_control_fd_v2`'s `_MIN_CONTROL_FD_V2` changed `3 -> 0`
   (reintroduces the `fd=0` hang path) -> the dedicated fd=0 test failed ->
   restored, green.
3. `read_offline_response_document_v2`'s directory-escape check replaced
   with `escapes = False` -> the path-traversal-containment test failed ->
   restored, green.
4. `NoEchoArgumentParserV2.error()` changed to also write argparse's
   `message` to stderr before raising -> the subprocess-level no-echo test
   failed (the unit-level test did not, because it only inspects the raised
   exception object, not real stderr writes -- confirms the two test layers
   catch different things and neither alone is sufficient) -> restored,
   green.
5. `temp_workspace_v2` changed to `yield` outside any `try/finally` (exact
   `#277` ordering bug, reintroduced) -> 3 of 5 workspace tests failed,
   including the genuine-defect-still-cleaned-up case -> restored, green.

All five mutations flipped the intended test(s) RED and only those tests;
all five restorations returned the suite to the pre-mutation green state
(`git status` clean against `7a115c9` after each restore).

## Test suite added

| File | Purpose |
|---|---|
| `app/agent_review/operational_refusal_v2.py` | Ported unchanged (family marker) |
| `app/agent_review/operational_ingress_v2.py` | Ported + extended (see inventory) |
| `app/agent_review/operational_workspace_v2.py` | New — cleanup-safe temp workspace |
| `scripts/aiops-review-run-v2.py` | New — ingress-boundary-only script (see scope decision above) |
| `tests/agent_review/test_operational_refusal_family_v2.py` | Scoped-down re-derivation of `#277`'s whole-package family invariants, ranged over what G4 actually owns (see file docstring for why the whole-package version was not ported as-is) |
| `tests/agent_review/test_operational_ingress_v2.py` | Ported scalar/document tests + new response/fd/usage-error/bidirectional tests |
| `tests/agent_review/test_operational_workspace_v2.py` | Cleanup-guarantee RED/GREEN |
| `tests/agent_review/test_aiops_review_run_v2_ingress.py` | Process-level (subprocess) RED/GREEN for all four STOP items + bidirectional invariant |

65 new/ported tests, all green at `7a115c9`.

## `#277` whole-package family test: deliberately not ported as-is

`#277`'s `test_operational_refusal_family_v2.py` asserted its three
invariants over every exception class in all ~75 `app.agent_review` modules,
seeded from the now-dead two-process CLI. Porting it unmodified either fails
immediately (imports modules that do not exist on `master`) or, patched to
import what exists, silently narrows its claimed scope while keeping prose
claiming whole-package coverage. Retrofitting `ExpectedOperationalRefusalV2`
onto every reason-code-carrying class across the package
(`RunAssemblyError`, `SynthesisErrorV2`, `ReadinessDecisionError`,
`ChunkResultScopeError`, `ReviewContentBindingError`,
`_router_receipt_v2.RouterReceiptError`, and more) is real, valuable work,
but it is `#200-G5` recomposition's work: it touches dozens of modules this
primitive does not own, with no independent-review budget here to cover
that surface. The scoped-down version in this PR ranges over exactly what
G4 added or touched, honestly declared in its own docstring rather than
silently narrowed under unchanged claims.

## Absorption question (mandatory to address, either direction)

Considered whether G4's boundary could be structurally absorbed into
`#200-G1` (executed source identity) rather than shipped as a fifth parallel
module. Conclusion: **no** — kept separate, for a reason stated explicitly
rather than left implicit:

* G1's stated job is a *directional identity* model: `commit -> bytes`,
  verifying which code is actually executing, replacing a falsified
  self-reported `bytes + caller document -> claimed commit` model. Its
  authority question is "is this the code we think it is?", evaluated
  **after** a subject is sealed and materialised from committed bytes.
* G4's job is *pre-seal material validation*: is this caller-supplied value
  (a flag, a path, file content, a directory entry, an environment variable,
  an argv-parse failure) safe to let reach anything downstream at all? Its
  authority question is answered **before** anything is sealed, and it says
  nothing about which commit produced the code that will eventually consume
  the validated value.
* The two compose (G4's `ValidatedPublicInputsV2` and validated document
  values are exactly the inputs G1's eventual subject materialisation would
  consume) but are not the same authority. Merging them would enlarge G1's
  own independent-review surface with an unrelated concern (input
  sanitisation vs. identity verification), making G1 harder to review on its
  own terms — the opposite of what the recovery checkpoint's per-primitive
  independent-review structure is for.

No absorption disposition filed; G4 remains its own primitive, deliverable
recorded in this PR.

## Not authorized / not attempted

Marking Ready, merging, tagging/releasing, deploying, modifying CI workflow
files, calling a live Router or real LLM provider, mutating
AgentEscala/InterLeitos/CAEM repos, closing `#200`, modifying `#273` — none
attempted. PR opened in Draft state.

## Independent review — round 1 (at `ba5fd4e`)

Two adversarial passes dispatched via the `Agent` tool (not the `codex`
CLI, per standing preference), against frozen head `ba5fd4e` (PR #283
Draft), explicitly instructed to hunt for (a) any remaining
caller-controlled source still reaching a raw exception/path/secret, and
(b) a case where a genuine internal defect is wrongly swallowed as an
external refusal.

**Lane A (ingress leak hunt) — P1, independently reproduced:**
`read_offline_response_document_v2` had two unguarded escapes: a `chunk_id`
containing an embedded NUL byte raised a raw `ValueError` from
`Path.resolve()` (only `OSError` was caught by the escape-check), and an
overlong `chunk_id` (~100k chars) let `resolve()` succeed while a separate,
unguarded `is_file()` call raised a raw `OSError` whose message embedded
the full absolute subject temp-directory path. Not reachable through the
current CLI (`chunk_id` is always internally generated as `chunk-NNNN`),
but the function's own contract claims to be a boundary for a `chunk_id` of
any shape, and G5 is expected to wire real caller-derived chunk ids into
this exact function next.

**Lane B (bidirectional invariant hunt) — P0, independently reproduced,
directly refuting the checkpoint's central claim:**

1. `profile_loader_v2.load_target_profile_text_v2`'s
   `except (ValidationError, TypeError, ValueError)` around
   `TargetProfileV2.model_validate(raw)` laundered an injected internal
   `TypeError` into `target_profile_invalid` — a real gap, not present in
   `operational_ingress_v2.py`'s own already-narrowed pattern, but reachable
   because this PR wired `load_target_profile_text_v2` directly into the
   ingress-boundary script and made `TargetProfileLoadErrorV2` a family
   member.
2. `_read_unambiguously_v2`'s `except _YAML_PARSE_FAILURES_V2` (a tuple
   including `AttributeError`/`TypeError`/`KeyError`/`IndexError`) wraps
   `yaml.load` calls that recurse into this codebase's OWN
   `_CollisionRefusingSafeLoaderV2.construct_mapping`/`construct_scalar`
   overrides, not only stock PyYAML — a bug in that override code (reproduced
   by deliberately breaking `construct_mapping`) is indistinguishable from a
   legitimate malformed-YAML failure.
3. Both `except ValidationError` sites in `operational_ingress_v2.py`, plus
   `validate_caller_document_v2`: pydantic v2 (2.11.3) wraps ANY
   `ValueError`/`AssertionError` raised inside a `@model_validator`/
   `@field_validator` into `ValidationError`, indistinguishable from genuine
   caller-content rejection. Also identified that the two existing
   bidirectional-invariant tests (this checkpoint's own "found and fixed a
   real bug" evidence) monkeypatched the whole `model_validate_json`
   classmethod, bypassing pydantic-core's real validator dispatch entirely —
   never exercising the actually-vulnerable surface.

Both P0/P1 findings from both lanes were independently reproduced (not
merely trusted) before any correction, with exact reproduction scripts run
directly against the frozen head. See git history at `b8e713c` for the full
reproduction transcripts embodied as the new `test_the_raw_*_shape_really_
did_leak` / `test_KNOWN_LIMITATION_*` tests.

## Round-2 correction (one bounded round, per grant)

Applied at commit `b8e713c`:

- **Lane A, fixed cleanly**: both `resolve()` and `is_file()` calls in
  `read_offline_response_document_v2` are now guarded
  (`except (OSError, ValueError)` / `except OSError`), converted to a new
  `INGRESS_RESPONSE_PATH_UNUSABLE_REASON_V2` refusal. Verified against the
  exact reproductions; mutation-tested (reverting the guard flips the new
  regression tests RED; restoring returns green).
- **Lane B #1, fixed cleanly**: `profile_loader_v2`'s
  `except (ValidationError, TypeError, ValueError)` narrowed to
  `except ValidationError`, matching the same empirically-verified pattern
  already applied to `validate_caller_document_v2`. Mutation-tested.
- **Lane B #2 and #3, NOT narrowly fixed — named as accepted structural
  limitations instead, per the grant's explicit instruction not to ship a
  fix that only looks like it works.** Both are genuinely undecidable from
  inside the code that would need to decide them:
  - The YAML-loader ambiguity (#2) is pre-existing code
    (`_CollisionRefusingSafeLoaderV2`, issue #203-S2, commit `6d613cf`,
    "supersedes #236" after 7+ prior adversarial rounds — see git log and
    this agent's own memory of that slice). Re-architecting its internal
    error provenance under this bounded correction round's time pressure
    risks regressing already-hardened collision-detection logic for a
    theoretical (monkeypatch-only) live risk; out of this primitive's
    ownership and scope. Documented with a precise comment at
    `_YAML_PARSE_FAILURES_V2`'s definition.
  - The pydantic-validator-body ambiguity (#3) was verified empirically to
    be genuinely undecidable from `ValidationError.errors()`'s own shape:
    a validator-raised `ValueError`/`AssertionError` is wrapped identically
    whether it is a legitimate rejection or an internal defect that happens
    to raise the same way; only `TypeError`/`KeyError`/`AttributeError`/
    `RuntimeError` are left unwrapped by pydantic-core and were confirmed
    (against a REAL `field_validator`, not a monkeypatched classmethod) to
    still escape raw. Documented in a new, prominent module-docstring
    section in `operational_ingress_v2.py` ("A named, accepted limit of the
    two-epoch discipline"). The two original bidirectional-invariant tests
    that used the unrealistic classmethod-bypass reproduction were kept
    (they still prove a real, narrower property) and supplemented with:
    tests against a real validator proving the achievable direction
    (`TypeError`/`KeyError`/`AttributeError` escape raw), and dedicated
    `test_KNOWN_LIMITATION_*` tests that PIN the documented gap so it cannot
    silently drift wider or narrower without forcing a deliberate decision.

Full new/ported test suite after correction: 76 passed (up from 65; +11 for
the Lane A regression tests, the realistic-validator tests, and the
KNOWN_LIMITATION pins). `profile_loader_v2`'s full pre-existing test suite
(163 tests) reconfirmed green after the narrowing fix.

## Independent review — round 2 (pending)

Per the grant: after this one bounded correction round, two FRESH
independent review lanes are dispatched against the new frozen head, at
minimum re-targeting Lane B's exact bidirectional-invariant class (the
abstraction that would trigger `STOP_G4_ARCHITECTURE_NOT_CONVERGING` if
refuted a second time in the sense of finding something this checkpoint
does NOT already own up to — i.e. a NEW gap, or evidence that the "closed"
sources aren't actually closed, not a re-discovery of the two limitations
already named above). Findings recorded in a further follow-up once both
return.

## Next minimum action

Await both round-2 independent review passes; if a genuinely new P0/P1
surfaces (not the two already-named, documented limitations), assess
whether it is narrowly fixable within a further bounded round or whether it
indicates the ingress-closure abstraction itself is not converging. If the
SAME class of finding recurs a second time in a way that contradicts what
this checkpoint actually claims (as opposed to confirming an already-named
limitation), stop with `STOP_G4_ARCHITECTURE_NOT_CONVERGING` rather than
attempting a third round. If round 2 finds nothing beyond the two named,
accepted limitations, report `PRIMITIVE_NON_REFUTED` (scoped precisely: not
"everything is closed", but "every source is closed except two explicitly
named, structurally-inherent-to-pydantic/PyYAML residuals that are out of
this primitive's proportionate scope to close") and hand off to whichever
primitive (`#200-G1`/`#200-G3`) is next scheduled, per the recovery
checkpoint's decomposition — G4 does not itself decide G5's start
condition.
