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
`emit_review_readiness_v2` therefore does exactly one thing: assemble the
`ReviewReadinessV2` constructor call from a C1 `ReadinessDecisionV2` plus
identity/`pr_state`/`checks`/`findings`, and let
`ReviewReadinessV2.validate_state_invariants` (`contracts_v2.py`) decide —
raising `pydantic.ValidationError`, unwrapped, if the combination does not
satisfy it. Nothing in this module re-checks what that validator already
owns (e.g. "ready requires an open PR and every check green" — verified
directly: feeding a `MERGED` `pr_state` alongside a `ready` decision, or an
empty `checks` list, both fail closed via the contract's own validator, not
a copy of its logic).

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

Thin CLI wiring around `emit_review_readiness_v2` — no second
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
