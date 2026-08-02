# AgentReview v2 benchmark — AIOps vs. Codex vs. human (#88)

Refs #88 (parent roadmap #46, related epic #80). Depends on #83–#87
(#86/#87 merged). Creates a reproducible evaluation suite to measure
AgentReview v2's own pipeline quality, without ever treating concordance
between AIOps, Codex, and a human as automatic proof of anything.

```text
AIOps, Codex e humano são observadores distintos.
Concordância não confirma automaticamente um finding.
Discordância não é falha por si só.
A verdade de avaliação vem de fixture/expectation versionada e evidência reproduzível.
```

## What this slice executes, and what it deliberately does not

This slice fully implements and RUNS **Lane 1 (AIOps v2 deterministic/
offline)** — the pipeline's own behavior given a synthetic corpus, with no
network, no real provider, no GitHub write, no CT102. It builds, but does
**not itself invoke**, the wiring for **Lane 2 (Codex local/CLI)** and
**Lane 3 (Codex GitHub shadow review)**.

This is a deliberate scope boundary, not an oversight: actually running
Codex (local CLI or GitHub shadow review) is a real external-provider call.
CAEM's own stop-condition list for this convergence effort names
"necessidade de secret/provider real" explicitly — invoking a real
inference provider is a protected action requiring its own authority grant,
not implied by the standing grant that covers this offline, CT104-scoped
convergence work. `scripts/compare-review-observations.py` is built to
correlate ALREADY-PRODUCED Codex/human observation records against Lane
1's own findings, for whichever future, separately-authorized run produces
them — this script itself never calls Codex or any provider.

**Lane 4 (human review)** is inherently manual and cannot be executed by
an agent; the observation schema and correlation tooling exist so a human
disposition can be recorded and correlated the same way a Codex one would
be.

## Architecture

```text
evals/agent_review_v2/
  __init__.py
  harness.py        -- EvalCaseV2 schema, run_eval_case_v2, compute_eval_summary_v2
  observation.py     -- ExternalObservationV2, correlate_observation_v2
  cases/*.yaml        -- the synthetic corpus (9 cases at merge time)

scripts/run-agent-review-v2-evals.py     -- Lane 1 runner + --check drift gate
scripts/compare-review-observations.py    -- Lane 2/3/4 correlation (no provider call)

reports/agent-review-v2-eval-summary.json  -- committed, synthetic-corpus-only
reports/agent-review-v2-eval-summary.md
```

`evals/agent_review_v2/` is deliberately separate from `app/agent_review/`
(the production engine): it only CONSUMES the engine's real, already-merged
functions (`profile_loader_v2`, `run_assembly_v2`, `payload_builder_v2`,
`consumer_v2`/`parser_v2`, `synthesis_v2`, `readiness_decision_v2`) to run
cases — it adds no review logic, no contract, and no gate authority of its
own.

## What Lane 1 actually measures

Lane 1 does **not** measure whether a real LLM/Codex would notice a given
defect (that requires a real provider call, out of scope here). Each case's
`injected_findings` are synthesized directly into a chunk response,
standing in for "a reviewer, human or model, claimed this" — the harness
then measures whether the DETERMINISTIC pipeline downstream of that claim
(coverage bridging, lifecycle aggregation, readiness precedence, stale/
binary-block handling) reaches the case's declared `expected_readiness`
and preserves every expected finding's identity, by real provenance
tracking, through to the final decision — never a count-based
approximation. A mismatch is a real, meaningful signal: either the case's
own expectation is wrong, or the pipeline has a real bug.

## Case schema

```yaml
case_id: stable-id
category: contract|security|coverage|domain|false-positive|stale
target: agent_escala|interleitos
files:
  - path: relative/path.py
    hunks: [{old_start, old_lines, new_start, new_lines, seed}]
    is_binary: false
injected_findings: [{file_path, severity, line_start, line_end, title}]
confirmed_findings: [{file_path, severity, line_start, line_end, title}]
injected_limitations: [model_uncertainty]
stale_reason_codes: [head_mismatch]
expected_readiness: ready|blocked_code|blocked_pipeline|manual_required|stale
expected_findings: [{severity, file_path, line_start, line_end, invariant, root_cause}]
forbidden_findings: [... same shape ...]
must_review_fragments_complete: true
safe_counterexample: false
rationale: reproducible evidence
```

`confirmed_findings` is this harness's own addition beyond the issue's
literal schema, needed to genuinely exercise `blocked_code`: it re-runs
synthesis a second time with a real `prior_lifecycle` `CONFIRMED` record
for exactly the finding_id the first pass computed — mirroring the
new-then-confirmed round-trip #86's own test suite established, never
inferring confirmation from concordance.

`files`/`hunks` are constructed directly as `ParsedFileDiffV2`/
`ParsedHunkV2` objects (never hand-written unified-diff text, which proved
fragile in #86's own early draft) — real diff ACQUISITION (`git diff`)
remains explicitly out of scope, matching #103/#129's own established
boundary.

Uses the same real, on-disk `TargetProfileV2` fixtures #86 already
established (`tests/agent_review/fixtures/v2/agent_escala/`, `.../
interleitos/`) — not duplicated here.

## Corpus (9 cases at merge time)

| case_id | category | target | expected_readiness |
|---|---|---|---|
| `aiops-contract-stale-head` | stale | agent_escala | stale |
| `aiops-contract-missing-must-review` | coverage | agent_escala | blocked_pipeline |
| `agentescala-contract-p3-finding-still-ready` | contract | agent_escala | ready |
| `agentescala-false-positive-unrelated-change` | false-positive | agent_escala | ready |
| `agentescala-domain-new-finding-manual-required` | domain | agent_escala | manual_required |
| `agentescala-security-confirmed-finding-blocked-code` | security | agent_escala | blocked_code |
| `interleitos-domain-dlp-suspicion-manual-required` | domain | interleitos | manual_required |
| `interleitos-security-blocked-prior-to-response` | security | interleitos | blocked_pipeline |
| `interleitos-false-positive-synthetic-clinical-terms` | false-positive | interleitos | ready |

Every AgentEscala/InterLeitos case is modeled on the SHAPE of a real,
publicly-referenced issue (AgentEscala #677/#681, InterLeitos's IAM/DLP
concerns) with entirely synthetic content — never real product logic,
patient data, or secrets, matching #86's own established fixture
discipline.

**Categories the issue names that are NOT duplicated in this corpus**,
because dedicated, already-merged regression suites already cover them more
precisely than this harness could without re-implementing their own
machinery:

- cross-run/cross-target replay — `tests/agent_review/
  test_v2_dual_target_e2e.py::test_cross_target_policy_binding_is_rejected`
  and `::test_cross_target_payload_set_binding_is_rejected` (#86);
- v1/v2 mixed contracts fail closed — `tests/agent_review/
  test_aiops_review_quality_gate_v2_cli.py` and `versioning.py`'s own suite
  (#130/C2);
- coverage promotion / structural split — `tests/agent_review/
  test_synthesis_v2.py`/`test_readiness_decision_v2.py` (#107, C1/#127).

## Metrics and initial thresholds (measured, not invented)

Computed by `compute_eval_summary_v2` from this corpus's own real run —
these ARE the initial versioned baseline, measured, not guessed ahead of
time:

| Metric | Baseline (this corpus) | Threshold before any promotion |
|---|---|---|
| readiness matches | 9/9 | 100% (any regression fails `--check` and CI) |
| false approvals (critical KPI) | 0 | **must stay 0** — a single false approval blocks any promotion |
| stale cases correctly rejected | 1/1 | 100% |
| expected findings recovered | 2/2 | 100% |
| forbidden findings leaked | 0 | **must stay 0** |
| byte stability (manifest_hash/payload_hashes across 2 runs) | identical | must stay identical |

These are Lane 1's own thresholds. Per the issue's own rule, thresholds for
promoting Codex to an operational signal are explicitly **not** set here —
they require a real Lane 2/3 run, which this offline slice does not
execute; setting a number without that measurement would be exactly the
"não inventar meta sem medição inicial" mistake the issue itself warns
against.

## `scripts/run-agent-review-v2-evals.py`

Runs every `evals/agent_review_v2/cases/*.yaml` case through Lane 1 and
writes `reports/agent-review-v2-eval-summary.{json,md}`. `--check` compares
a fresh run against the existing committed JSON report, ignoring
`duration_ms`/`total_duration_ms` (legitimate wall-clock operational
metrics that vary run to run, never part of the deterministic
byte-stability guarantee) — mirroring `export-agent-review-v2-schemas.py
--check`'s own drift-detection pattern. Exits non-zero on any readiness
mismatch, false approval, forbidden-finding leak, or report drift.

## `scripts/compare-review-observations.py`

Reads already-produced `ExternalObservationV2` JSON records (Codex local/
CLI, Codex GitHub shadow, or human — whichever a separately-authorized
process produced) plus a JSON array of AIOps-canonical finding references,
and reports a location-based correlation (`matched`/`rejected`/
`inconclusive`) per source. Matching is by `(file_path, severity)` only —
per the issue's own tolerance rule ("tolerar redação diferente, mas não
localização/causa distinta") — and resolves to `inconclusive`, never an
arbitrary pick, when more than one AIOps finding shares the same location
and severity. A `matched` correlation here **never** means `confirmed` in
the AIOps lifecycle sense; this script never writes to any
`FindingLifecycleRecordV2` or `ReviewReadinessV2`, and `ExternalObservationV2`
deliberately lives outside `contracts_v2.py`'s registry so it can never be
mistaken for one.

## Security and non-scope (per the issue)

- no CT102;
- no auto-approve, auto-merge, or required check;
- model consensus is never treated as truth;
- no real InterLeitos patient/institution/professional data anywhere in
  the corpus;
- no direct provider call from AIOps (Lane 1 synthesizes provider
  responses itself; it never calls one);
- no automatic application of a contract suggestion.

## Tests

- `tests/evals/test_harness_v2.py` (11 tests): case-schema validation,
  every readiness outcome (`ready`/`blocked_pipeline`/`manual_required`/
  `blocked_code`/`stale`), the confirmed-finding round-trip, forbidden-
  finding-leak detection exercised in BOTH directions (present vs. absent),
  byte-stability across two runs, and `compute_eval_summary_v2`'s
  false-approval/stale-correctness counting.
- `tests/evals/test_observation_v2.py` (7 tests): schema validation,
  matched/rejected/inconclusive correlation, and an explicit proof that
  `correlate_observation_v2` can never produce a `confirmed` disposition.
- `tests/evals/test_run_agent_review_v2_evals_cli.py` (9 tests): the real
  9-case corpus runs clean via subprocess, is byte-reproducible (duration
  fields excluded), `--check` passes against its own fresh output and fails
  closed on drift or a missing report, and the loader fails closed on an
  empty cases directory, a malformed case file, and a duplicate `case_id`.
- `tests/evals/test_compare_review_observations_cli.py` (3 tests): a real
  correlation run via subprocess, and fail-closed on an invalid observation
  or non-array input.

Confirmed non-vacuous by direct mutation testing before acceptance: a
wrong `expected_readiness` is caught by the runner (exit 1); a
deliberately-leaked forbidden finding is caught; disabling the
confirmed-finding round-trip demotes the `blocked_code` case to
`manual_required` and is caught by the runner, not silently accepted;
`--check` catches a tampered committed report.

Full suite impact: `tests/agent_review` 1054 passed (unchanged — no
production code touched), `tests/evals` 29 passed (new), broad
marker-excluded selection 1600 passed/12 deselected (was 1571, +29 exactly
this slice's own tests).
