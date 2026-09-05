# AgentReview v2 — emitting ReviewReadinessV2 and the quality gate CLI (#130)

Refs #130, child of tracker #108 (readiness). Consumes C1/#127's
`compute_readiness_decision_v2` (merged, PR #140) and E1/#128's evidence
hash discipline (`RunIdentityV2.evidence_hash`, merged, PR #141). Closes
tracker #108: both C1/#127 and C2/#130 are now complete.

Delivers exactly what the issue asks: emission of a real
`ReviewReadinessV2` artifact from a real C1 decision, and the quality gate
CLI. No GitHub publication, no change to `quality_gate.py` v1 or
`scripts/aiops-review-quality-gate.py` — both deliberately out of scope.

## `ReviewReadinessV2` is the authority — this module is not

Per tracker #108's own rule: *"`ReviewReadinessV2` é a AUTORIDADE: o
computador decide o estado e deixa o contrato reprovar. Proibido
reimplementar as invariantes de `validate_state_invariants` fora do
contrato."* `app/agent_review/review_readiness_emission_v2.py`'s
`emit_review_readiness_v2` (renamed `_assemble_review_readiness_v2` and made
internal by `#201-C` — see the update section near the bottom of this
document) therefore does exactly one thing: assemble the
`ReviewReadinessV2` constructor call from a C1 `ReadinessDecisionV2` plus
identity/`pr_state`/`checks`/`findings`, and let
`ReviewReadinessV2.validate_state_invariants` (`contracts_v2.py`) decide —
refusing if the combination does not satisfy it. That refusal surfaces as
`ReadinessEmissionError` with a reason that NAMES the unmet rule
(`ready_requires_open_pr`, `ready_requires_green_checks`, ...), evaluated
before the artifact is constructed by the single authority in `contracts_v2`
that `validate_state_invariants` also consults (`#200-D` two-epoch model). The
validator remains the sole authority on whether the artifact is well-formed,
and its rules are never restated. A `ValidationError` from construction after
that point is a derivation defect and escapes raw.

Nothing here re-implements what the validator owns. "Ready requires an open PR
and every check green" is defined once, in
`contracts_v2.evaluate_ready_preconditions_v2`, and both this module's
pre-seal check and `validate_state_invariants` call it. Verified directly:
feeding a `MERGED` `pr_state` alongside a decision that is still `ready` after
the required-check assessment fails closed as `ready_requires_open_pr`, which
names the rule. An empty `checks` list does NOT: the assessment legitimately
downgrades such a run to `manual_required`, and refusing it would destroy a
valid artifact — so `ready_requires_green_checks` is enforced by the contract
against the final material, where a violation means the transformation
produced an incoherent state.

## `pr_state`/`checks` are caller-supplied, not acquired here

Real acquisition of a PR's live `pr_state`/`checks` (e.g. via `gh pr
view`/`gh pr checks`) is a live GitHub network operation. Every prior slice
in this convergence effort has drawn the identical boundary between
ACQUISITION (reading something external) and ASSEMBLY (a pure function over
already-acquired values) — #103's git-subprocess diff acquisition, #129's
`assemble_manifest_from_diff_v2` (accepts already-parsed `ParsedFileDiffV2`
tuples rather than calling `acquire_authoritative_diff_v2` itself), #131's
artifact/contract reading. This module mirrors that same separation:
`pr_state`/`checks` are accepted as parameters, already acquired by
whatever caller has legitimate, granted network/GitHub read access in its
own execution context. A live `gh`-based adapter is explicitly deferred —
wiring one is future work requiring its own grant for real network/GitHub
access in this offline, CT104-scoped environment, not implied by this
issue.

## `scripts/aiops-review-quality-gate-v2.py`

Thin CLI wiring around `emit_review_readiness_v2` (as of `#201-C`, this
means `produce_review_readiness_v2` — see the update section below) — no second
implementation of `ReviewReadinessV2`'s invariants. Reads JSON files for
the C1 decision, identity/evaluated-identity, findings, and checks, plus a
`--pr-state` flag; writes the resulting `ReviewReadinessV2` JSON.
`--contract-version v2` is required and explicit, per the CLI naming
decision registered in #102 — a brand-new v2 CLI script, using the
established `-v2` suffix convention, never touching the existing v1
`scripts/aiops-review-quality-gate.py`.

**Closes the "`select_contract_version` sem call site em produção" gap**
(`versioning.py`'s own module docstring already named this): when
`--payload`/`--response` are both supplied, the CLI calls
`select_contract_version(requested="v2", payload_raw=..., response_raw=...)`
BEFORE anything else runs. A v1-shaped payload/response fed into a
`--contract-version v2` invocation is refused as `mixed_contract_versions`
— zero conversion, matching the issue's own acceptance criterion verbatim.

## Deliberately out of scope

- publishing anything to GitHub;
- altering `quality_gate.py` v1 or `scripts/aiops-review-quality-gate.py`
  (confirmed untouched — this PR is additions-only);
- acquiring `pr_state`/`checks` live from GitHub (see above).

## Tests

`tests/agent_review/test_review_readiness_emission_v2.py` — 4 tests: a real
`READY` artifact from a real C1 decision; a real `BLOCKED_CODE` artifact
carrying its findings; a `READY` decision combined with a `MERGED` PR
state failing closed via `ReviewReadinessV2`'s own validator (never
re-implemented here); a `READY` decision with no green checks failing
closed the same way.

`tests/agent_review/test_aiops_review_quality_gate_v2_cli.py` — 5 tests:
a valid `READY` emission via subprocess; refusal without
`--contract-version v2`; fail-closed on a readiness-invariant violation
(merged PR + ready decision); rejection of a v1-shaped payload/response
mixed into a `--contract-version v2` run (the issue's own acceptance
criterion, verbatim); acceptance of a genuinely matching v2 payload and
response.

## `#201-C` update — where required-check authority now connects

`emit_review_readiness_v2` no longer exists under that name. It became
`_assemble_review_readiness_v2` — internal, no longer accepting a raw
`checks` array from any caller — and a new `produce_review_readiness_v2`
is now THE single production entry point for constructing a
`ReviewReadinessV2`:

```text
produce_review_readiness_v2                 <- public, the only entry point
  -> _verify_and_assess_required_checks_v2   (required_check_readiness_v2,
                                               #201-C0's real boundary)
  -> _apply_required_check_assessment_v2     (readiness_decision_v2, precedence)
  -> _assemble_review_readiness_v2           (this module, pure, internal)
       -> ReviewReadinessV2(...)             <- the only construction site
```

`checks`/`checks-provenance` are no longer trusted arrays — they are
CLAIMS, always re-verified against `#201-C0`'s
`reassemble_and_verify_required_checks_v2` before any of them can reach
the emitted artifact. `required_check_names` is never a parameter
anywhere in this chain: it is derived, every call, from a `TargetProfileV2`
loaded fresh from a trusted `target_profile_root` and bound to
`evaluated_identity.profile_hash`. See
`app/agent_review/required_check_readiness_v2.py`'s own module docstring
for the full design, and
`tests/agent_review/test_required_check_readiness_arch_v2.py` for the
AST-level proof that no other production path exists.

**`_validate_required_check_provenance`/`_validate_required_checks_complete`
are gone from the CLI.** Both concerns — "is the required set complete?"
and "may each submitted check be here at all?" — now live inside
`produce_review_readiness_v2`. The CLI is purely: parse args, load inputs,
call `produce_review_readiness_v2`, write the result.

**A required check with no legitimate submission no longer fails the CLI.**
It emits a real `manual_required` artifact with `policy_failure` in
`reason_codes`, and the CLI exits 0. `#217`/`#145`'s own fix is preserved —
this state is still never `ready` — but it is now representable instead of
crashing:

```text
CLI_EXIT_SUCCESS != READINESS_READY

exit 0  => a ReviewReadinessV2 artifact was written. The decision consumable
           by any caller is readiness.state, never the exit code.
exit !=0 => no artifact was written at all (forged/invalid/cross-run
            submission, or a broken --target-profile checkout).
```

**Preexisting trust assumption, not created or widened here.**
`produce_review_readiness_v2` authenticates required checks; it does not
authenticate the origin of a caller-supplied `ReadinessDecisionV2`/
`findings`. `ReadinessDecisionV2` remains, by C1's own design, a plain,
freely constructible value — the only binding enforced on it is REPLAY
protection (`decision.run_id`/`manifest_hash` must match
`evaluated_identity`), never origin. This is inert for any target that has
not authorised an independent judge (Path A has no caller; Path B needs an
independent-judge execution mode that the target's own base-owned policy has
authorized — see `#331` SGAQ-CI1R) — a fabricated `READY` decision is then
still narrowed to `manual_required` the moment any required check is missing
or unestablished.

**The ground of that inertness changed with `#331` SGAQ-CI1R, and it is worth
being exact about.** Path B used to be refused *unconditionally*: the
capability did not exist, so no configuration could reach it. It is now
refused *conditionally*: the capability exists and is gated on an explicit
opt-in in the trusted base-owned `AuthoritativeCheckPolicy`. The assumption
doing the work is no longer "this path is closed". It is "this path is closed
unless a target authorizes it", which makes the requirement that the policy be
read from a TRUSTED BASE checkout load-bearing where it was previously inert.

**What is and is not known about who has opted in.** The two policy fixtures
shipped in this repository do not. Policies live in target repositories and are
loaded from a caller-supplied base checkout, so no test or inventory here
establishes the state of any real target — `test_no_policy_file_in_this_
repository_opts_in` says so in its own docstring. A target that has opted in
has, by that act, made `decision`/`findings` origin authentication a live
concern rather than a latent one; per the paragraph below that is a new
trust-boundary decision, not something `#201-C` or `#331` absorbed silently.

Tests: `tests/agent_review/test_required_check_readiness_v2.py` (the choke
point, unit + real-C0), `test_readiness_decision_v2.py` (the precedence
table), `test_review_readiness_emission_v2.py` (`_assemble_review_
readiness_v2` unaffected pure-assembly tests, plus new
`produce_review_readiness_v2` Class A tests), `test_required_check_
readiness_arch_v2.py` (AST proof of the single path).

## `#200-D` update — the pre-seal epoch also owns staleness

`produce_review_readiness_v2` validates caller material before the seal. Two
shared authorities in `contracts_v2.py` decide that material, and both are
called by the artifact contract's own model validator as well, so a document
that never meets this emitter gets the identical verdict:

| Authority | Question | Refusal code |
|---|---|---|
| `evaluate_readiness_common_material_v2` | reason/blocker/finding material well-formed? | `readiness_submitted_material_invalid` |
| `evaluate_readiness_staleness_material_v2` | does the claimed state agree with the two identities? | `readiness_staleness_material_invalid` |

Order inside the pre-seal epoch is deliberate: `#145`'s decision-provenance
guard runs first because a decision replayed from a different run is a more
specific fault, then common material, then staleness. Staleness is checked
before the `stale` short-circuit, because that path returns without ever
running the required-check assessment.

Both identities are caller-submitted and never transformed here, so no sealed
carrier is needed to tell derived material apart from submitted material. Past
the seal, a `ValidationError` is a defect in this repository and escapes raw.
