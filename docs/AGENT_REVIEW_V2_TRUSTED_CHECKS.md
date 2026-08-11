# AgentReview v2 — trusted checks: contracts (#201-A)

Refs `aiops-orchestrator#201`, first slice of the distribution epic
`aiops-orchestrator#199`. This document is the operational reference for
the plan/result/promotion contracts; design rationale for each decision
lives inline in `app/agent_review/trusted_checks_v2.py`'s own module
docstring.

## Scope of this slice (`#201-A`)

Contract only. Nothing here spawns a process, reads a repository
checkout, or touches a filesystem — offline simulation is `#201-B1`,
isolated execution is `#201-B2`, adversarial hardening is `#201-B3`, and
wiring a real result into `ReviewReadinessV2` is `#201-C`. All four are
later slices in this same DAG.

## The principle this contract exists to enforce, preserved literally

> O código do HEAD da PR é executado isoladamente como sujeito sob teste.
> O harness, a seleção de checks, os testes de autoridade e o
> serializador vêm da base confiável / host e são pinados por digest. A
> PR nunca pode controlar a evidência que a aprova.

## The contracts

```text
TrustedCheckPlanV2                         (host-owned, never PR-influenced)
├── run_id, head_sha
├── harness_digest, authority_suite_digest  (host-owned code, pinned)
└── checks: [AllowlistedCheckCommandV2]
      ├── check_name, command_token         (a fixed identifier, never raw argv)
      ├── timeout_seconds, max_memory_mb, max_processes
      └── network_allowed: Literal[False]   (pinned -- no network check is representable)

TrustedCheckResultV2 (extends TrustedCheckResultMaterialV2)
├── run_id, head_sha, check_name             (must match a specific plan)
├── authority: trusted | untrusted_advisory
├── outcome: success | failure | timeout | oom | cancelled | infra_failure
├── harness_digest                           (must match the plan's own)
├── artifact_sha256                          (present iff outcome is success/failure)
└── result_sha256                            (self-hash, canonical preimage)
```

`bind_trusted_check_result_to_plan_v2(result, plan)` fail-closes on any
divergence (run identity, HEAD, harness digest, an unauthorized
`check_name`, or a tampered `result_sha256`) — mirrors `review_content_v2.
bind_review_content_to_manifest_v2`'s own shape and discipline exactly.

## Why `RequiredCheckResultV2` is untouched

`RequiredCheckConclusionV2` (`contracts_v2.py:386`) has exactly four
values: `success`, `failure`, `pending`, `missing`. There is no fifth
value for "the runner timed out" or "the process was OOM-killed" — that
absence is structural, not an oversight, and it is exactly why this
slice's environmental-failure taxonomy
(`TrustedCheckOutcomeV2`) lives in its own sidecar contract rather than
extending the frozen one.

`promote_trusted_check_to_required_v2` is the **only** function in this
codebase permitted to construct a `RequiredCheckResultV2` from a
`TrustedCheckResultV2`. It refuses:

- any result whose `authority` is `untrusted_advisory` — a check the PR's
  own code produced (its own `conftest.py`, its own reporter) can carry a
  `TrustedCheckResultV2` all the way through this contract, but can never
  cross into `RequiredCheckResultV2` and therefore can never influence
  `ReviewReadinessV2`;
- any environmental outcome (`timeout`/`oom`/`cancelled`/`infra_failure`)
  even from a `trusted` authority — there is no honest
  `RequiredCheckConclusionV2` value for "the environment failed", so
  promotion raises instead of inventing one. The caller's readiness
  computation sees a missing/absent check, already fail-closed by
  `readiness_decision_v2`'s own existing precedence — never a fabricated
  result.

Proven directly, not merely documented:
`test_no_required_check_conclusion_value_exists_for_environmental_failure`
asserts `RequiredCheckConclusionV2`'s own member set has exactly four
values and none represents an environmental failure.

## Reason codes

| Constant | Raised by |
|---|---|
| `RESULT_RUN_IDENTITY_MISMATCH_REASON_V2` | `bind_trusted_check_result_to_plan_v2` |
| `RESULT_HEAD_SHA_MISMATCH_REASON_V2` | `bind_trusted_check_result_to_plan_v2` |
| `RESULT_HARNESS_DIGEST_MISMATCH_REASON_V2` | `bind_trusted_check_result_to_plan_v2` |
| `RESULT_CHECK_NOT_IN_PLAN_REASON_V2` | `bind_trusted_check_result_to_plan_v2` |
| `RESULT_HASH_MISMATCH_REASON_V2` | `bind_trusted_check_result_to_plan_v2` |
| `PROMOTION_NOT_TRUSTED_AUTHORITY_REASON_V2` | `promote_trusted_check_to_required_v2` |
| `PROMOTION_OUTCOME_NOT_RESOLVED_REASON_V2` | `promote_trusted_check_to_required_v2` |

## `#201-B1` — offline simulator (`app/agent_review/trusted_check_simulator_v2.py`)

`simulate_trusted_check_plan_v2(plan, *, fixtures, authority)` produces
real, fully-validated, plan-bound `TrustedCheckResultV2` instances WITHOUT
spawning a process, reading a checkout, or touching a filesystem — proven
directly by a test that patches `subprocess.Popen`/`subprocess.run` to
raise if ever called. This mirrors `review_transport_v2.offline_file_
transport_v2`'s own role for the Router transport: the deterministic
default every downstream consumer (and this repository's own test suite)
builds against before the real isolated executor (`#201-B2`) exists.

`authority` is a **required** keyword argument with no default — there is
no way to call this function and get a `TrustedCheckAuthorityV2.TRUSTED`
result "by accident". A caller stamping simulated output as trusted is
asserting something about its OWN test context, not something this
simulator earns or grants.

Every `check_name` the plan authorizes must have a matching `fixtures`
entry (`SIMULATION_MISSING_FIXTURE_REASON_V2` otherwise); a fixture naming
a check the plan does NOT authorize is equally rejected
(`SIMULATION_UNKNOWN_CHECK_REASON_V2`) — the simulator's own inputs are
held to the same closed-set discipline the real executor will eventually
need. `artifact_sha256` for a resolved (`success`/`failure`) simulated
outcome is deterministic — derived only from `(run_id, head_sha,
check_name, outcome)`, no clock or randomness — proven by a direct
same-input-same-output test.

## What is deliberately not here

- an isolated executor that actually runs a check, with no network, no
  sudo, no docker socket, and a result channel the PR cannot write to
  (`#201-B2`);
- adversarial hardening against a malicious `conftest.py`/reporter/plugin
  attempting to falsify a result (`#201-B3` — this slice's contracts make
  the STRUCTURE of such an attack impossible to represent as `trusted`,
  but nothing here yet proves a real subprocess is actually isolated);
- wiring a real `TrustedCheckResultV2` into `run_synthetic_review_v2`'s
  `checks` parameter (`#201-C`);
- AgentEscala's own adoption and closure of `#750` (target-side, tracked
  in that repository, not here).

## Where an authoritative `RequiredCheckResultV2` comes from (`#201-C0`)

`promote_trusted_check_to_required_v2` remains the only function permitted to
build a `RequiredCheckResultV2` from a `TrustedCheckResultV2`. `#201-C0` does
not weaken that; it answers the question the rule leaves open — if pytest can
never be `TRUSTED` here, where does its authoritative verdict come from?

```text
RequiredCheck = TrustedHostPromotion  ∪  AuthoritativeCIPromotion
```

- **`trusted_host_promotion`** — a `data_only_host_tool` result from the
  `#201-B3` executor, wrapping `promote_trusted_check_to_required_v2` and
  additionally supplying the toolchain and host-owned-config digests that
  `trusted_check_authority_v2` explicitly defers to `#201-C0` (a declaration
  that config is host-owned is necessary, never sufficient).
- **`authoritative_ci`** — a deterministic CI check run, matched against a
  base-owned policy naming the full producer identity, bound to `RunIdentityV2`,
  with an origin-specific proof of which tree actually ran.

The two paths are never joined by `check_name`, and neither is evidence for the
other. Every promoted check carries a `RequiredCheckProvenanceV2` sidecar bound
to it 1:1 by digest, and `scripts/aiops-review-quality-gate-v2.py` refuses any
check that is not covered by one — the bypass `#217` describes.

`RequiredCheckResultV2` itself remains untouched, for the same reason recorded
above: the proof that does not fit in the frozen contract goes beside it, not
inside it.

See `docs/checkpoints/AGENT_REVIEW_V2_201C0_PROVENANCE_BRIDGE.md`, including
the known limitation about base-owned workflow definitions.
