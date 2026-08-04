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

**Known-fixed defect, confirmed by an independent review before merge:**
`app/agent_review/lifecycle_v2.py::_dedup_key` deliberately excludes
severity from its dedup key (the same underlying defect can legitimately
be re-observed at a different severity across rounds). When a case
combines `injected_findings` and `confirmed_findings` at the SAME
`(file_path, line_start, line_end)` but a DIFFERENT severity, the engine
folds both raw findings into one synthesized record carrying TWO
provenance entries. An earlier revision of this harness picked a single,
arbitrary provenance key when deciding what to confirm, and silently
missed the confirmation in exactly that overlap case (reproduced directly:
it resolved to `manual_required` instead of `blocked_code`). Fixed by
checking every one of a record's provenance keys against
`confirmed_findings`, never just one — see
`test_confirmed_finding_overlapping_injected_finding_at_same_location`
in `tests/evals/test_harness_v2.py`.

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
| expected findings recovered (overall) | 2/2 | 100% |
| recall by severity (P0/P1/P2/P3) | P1: 1/1, P2: 1/1, P0/P3: 0/0 | 100% recovered wherever `total > 0` |
| forbidden findings leaked | 0 | **must stay 0** |
| duplicate finding_ids detected (within-Lane-1) | 0 | **must stay 0** |
| byte stability (manifest_hash/payload_hashes across 2 runs) | identical | must stay identical |

These are Lane 1's own thresholds. Per the issue's own rule, thresholds for
promoting Codex to an operational signal are explicitly **not** set here —
they require a real Lane 2/3 run, which this offline slice does not
execute; setting a number without that measurement would be exactly the
"não inventar meta sem medição inicial" mistake the issue itself warns
against.

## Status against issue #88's own acceptance criteria

An independent review of this slice correctly flagged that merging with
`Closes #88` would be premature: two of the issue's explicit "Métricas
mínimas" items — **precisão** (precision) and **duplicação entre AIOps/
Codex/humano** (cross-lane duplication) — are not, and cannot honestly be,
measured by Lane 1 alone. Lane 1 only ever injects EXACTLY the findings a
case expects to survive; there is no mechanism here for the pipeline to
reject a spurious claim, so a Lane-1-only "precision" number would be
vacuously 100% by construction, not a real measurement. Both metrics
require an actual Lane 2/3 run (a real reviewer/model that could produce a
false positive, or two independent sources whose findings could
genuinely collide) — out of scope for this offline slice per the
protected-action boundary above.

| Acceptance criterion | Status |
|---|---|
| Corpus contém trigger, safe counterexample e unrelated change para regras críticas | done (9 cases, `safe_counterexample` flag, categories spanning all 6) |
| AgentEscala, InterLeitos e contratos AIOps representados | done |
| AIOps, Codex e humano medidos como lanes separadas | Lane 1 executed; Lanes 2-4 wired via `observation.py`/`compare-review-observations.py`, not executed (protected action) |
| Observação Codex não altera gate/readiness | done — `ExternalObservationV2` lives outside `contracts_v2.py`'s registry, never accepted by synthesis/readiness/emission |
| Métricas distinguem recall, precisão, cobertura, stale, duplicação, aprovação falsa | recall (overall + per-severity), cobertura (via readiness/coverage_status), stale, aprovação falsa, and within-Lane-1 duplication are done; **precisão and cross-lane duplicação are deferred to a Lane 2/3 slice** |
| Relatório determinístico para lanes offline | done — `--check` mode, byte-stability tests |
| Thresholds e processo de promoção explícitos | done for Lane 1 (table above); Codex-promotion thresholds explicitly deferred, not invented |
| Nenhum secret, PHI, raw prompt/diff/response ou path local versionado | done |

This PR merges with `Refs #88`, not `Closes #88` — issue #88 stays open
with a summary comment recording exactly this table, so the decision to
treat Lane 1 alone as sufficient (or to require a follow-up Lane 2/3
slice before closing) is made explicitly by whoever holds the authority to
grant that follow-up's provider access, not inferred from a merge.

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

- `tests/evals/test_harness_v2.py` (12 tests): case-schema validation,
  every readiness outcome (`ready`/`blocked_pipeline`/`manual_required`/
  `blocked_code`/`stale`), the confirmed-finding round-trip (including the
  overlapping-severity regression an independent review found and this
  slice fixed), forbidden-finding-leak detection exercised in BOTH
  directions (present vs. absent), byte-stability across two runs, and
  `compute_eval_summary_v2`'s false-approval/stale-correctness counting.
- `tests/evals/test_observation_v2.py` (7 tests): schema validation,
  matched/rejected/inconclusive correlation, and an explicit proof that
  `correlate_observation_v2` can never produce a `confirmed` disposition.
- `tests/evals/test_run_agent_review_v2_evals_cli.py` (8 tests): the real
  9-case corpus runs clean via subprocess (case count checked exactly
  against the real cases directory, not merely `>= 8`), is byte-reproducible
  (duration fields excluded, reusing the SCRIPT's own `_without_durations`
  rather than an independently-maintained copy), `--check` passes against
  its own fresh output and fails closed on drift or a missing report, and
  the loader fails closed on an empty cases directory, a malformed case
  file, and a duplicate `case_id`.
- `tests/evals/test_compare_review_observations_cli.py` (3 tests): a real
  correlation run via subprocess, and fail-closed on an invalid observation
  or non-array input.

Confirmed non-vacuous by direct mutation testing before acceptance: a
wrong `expected_readiness` is caught by the runner (exit 1); a
deliberately-leaked forbidden finding is caught; disabling the
confirmed-finding round-trip demotes the `blocked_code` case to
`manual_required` and is caught by the runner, not silently accepted;
`--check` catches a tampered committed report; the overlapping-severity
`confirmed_findings` bug was reverted and reproduced the exact reported
failure (`manual_required` instead of `blocked_code`) before being
restored fixed.

Full suite impact: `tests/agent_review` 1054 passed (unchanged — no
production code touched), `tests/evals` 30 passed (new), broad
marker-excluded selection 1601 passed/12 deselected (was 1571, +30 exactly
this slice's own tests).

## Update — Lane 2/3 execution and #88 closure (rev. 4.1 master plan)

The narrative above describes the Lane 1-only slice as it stood at merge
time. It is preserved as a historical record; this section documents what
changed afterward, culminating in #88's closure.

### What was missing, and why

Two blockers stood between the Lane 1 slice above and an honest #88
closure:

1. **No reviewable code.** The 9 historical `cases/*.yaml` fixtures declare
   only hunk geometry and a `seed` string (`diff_sha256=sha256(seed)`); the
   paths they reference do not exist in this repository. Lane 1 measures
   the deterministic pipeline downstream of an alleged finding, never
   detection.
2. **No real identity on the AIOps side.** `ExternalObservationV2` and
   `AiopsFindingReferenceV2` both require exact `(repo, pr_number,
   head_sha)` equality before `correlate_observation_v2` will correlate
   anything; Lane 1 uses a fixed synthetic head SHA constant, and creating
   real PRs for Codex alone would still leave the AIOps side keyed to that
   same synthetic identity — every correlation would resolve `rejected` by
   construction.

### What was built to resolve them

- `evals/agent_review_v2/reviewable_corpus/`: 10 provider-reviewable cases
  (6 `semantic_positive` — 3 AgentEscala / 3 InterLeitos, severity coverage
  P1≥2/P2≥2/no P0 — plus 4 `semantic_safe_counterexample`), each with real,
  behaviorally-verified base code and an inert `mutation.patch`, generated
  deterministically by `scripts/materialize-benchmark-case.py` and
  round-trip verified against the real `patch(1)` binary.
- `evals/agent_review_v2/reviewable_corpus/MANIFEST.yaml`: aggregates
  lane-applicability for all 15 relevant cases — the 10 provider-applicable
  ones plus the 5 historical Lane-1-only cases (`pipeline_integrity`/
  `identity_negative`/`transport_or_dlp_stop`), which ask no detection
  question and never count as a false negative.
- **OP-BENCH**: 10 real, ephemeral PRs (`eval/agent-review-v2-shadow/
  base-*`/`head-*` branch pairs), each diffing only its own case's
  mutation against its own materialized safe baseline. All 10 were closed
  unmerged and all 20 branches deleted once acquisition completed.
- `evals/agent_review_v2/aiops_projection.py`: runs the real v2 pipeline
  against each PR's REAL diff (via `acquire_diff_v2`/`parse_unified_diff`),
  bound to that PR's real `(repo, pr_number, base_sha, head_sha)` — the
  AIOps side of real identity, without which correlation would be
  vacuous. Explicitly a projector, not a new authority: reuses only
  already-merged engine functions, adds no gate authority.
- Lane 2 (Codex CLI local, `codex review --base <real base>`, explicit
  `sandbox_mode=read-only`/`approval_policy=untrusted` overrides) and
  Lane 3 (Codex GitHub shadow, `@codex review` on the same real PRs) were
  both executed for real against all 10 cases.
- `scripts/generate-benchmark-report.py`: the deterministic half. Reads
  only already-committed observations (never re-invokes a provider),
  validates every one against the real, strict `ExternalObservationV2`
  model, and uses the real, unmodified `correlate_observation_v2` for
  correlation.

### Result

- **aiops_pipeline** (pipeline correctness, not detection): 10/10
  readiness accuracy, 6/6 finding preservation, 0 false approvals — all
  against real PR/HEAD identity.
- **codex_local_detection**: 6/6 location recall, 3/6 exact severity
  match against ground truth, 0 false positives on the 4 counterexamples.
- **codex_github_detection**: 6/6 location recall, 6/6 exact severity
  match, 0 false positives.
- **cross_source_overlap**: both lanes independently flagged the same
  location on 6/6 positive cases.

Full detail: `reports/agent-review-v2-benchmark-summary.md`.

### Disposition

```yaml
codex_operational_eligibility: eligible
allowed_role: shadow
advisory_eligibility: deferred_to_target_observation
required_check_eligible: false
readiness_authority: false
statistical_status: descriptive_provisional_baseline
promotion_authority: false
```

n=6 positives / n=4 counterexamples is a descriptive baseline, not a
statistically powered study — this qualifies Codex for **shadow** only.
`advisory` requires real shadow-adoption data from the observation window
(A6), not this benchmark alone.

### Lane 4 (human)

Deferred to RI-C/RI-D by explicit disposition (not fabricated), posted on
#88: <https://github.com/mglpsw/aiops-orchestrator/issues/88#issuecomment-5183154624>.
`human_lane.completed` is `false`; this report makes no claim about human
precision or recall.

### Closure

#88 is closed as `completed` with this update as the closing evidence,
per `Refs #88` (not `Closes #88` in any commit message, to avoid the
accidental-auto-close pattern that reopened this issue twice already in
this repository's history).
