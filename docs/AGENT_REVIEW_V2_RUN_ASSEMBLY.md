# AgentReview v2 — assembling a real run/manifest from a real diff (#129)

Refs #129, child of tracker #109 (assembly). Consumes #103's
`acquire_authoritative_diff_v2` (merged, PR #112), #85's `TargetProfileV2`,
#106's `SemanticGroupingPolicyV2` (merged, PR #137), and #128's
`compute_evidence_hash_v2` (merged, PR #141). Blocks F1/#131 and F2/#132
(payload content and `PayloadSetV2` emission), which both need a real
`ManifestV2` to work against.

Closes the gap that, until now, `ManifestV2` was only ever constructed in
tests: `app/agent_review/run_assembly_v2.py` is the first production adapter
turning an already-acquired diff into a real, identity-bound manifest.

## What this delivers

`assemble_manifest_from_diff_v2(file_diffs, *, profile, grouping_policy,
repo, pr_number, base_sha, head_sha, tested_merge_sha, toolrepo_sha,
evidence_hash, max_lines_per_chunk, expected_paths=None) ->
ManifestAssemblyOutcomeV2`:

- adapts each `ParsedFileDiffV2`/`ParsedHunkV2` (#103) into `HunkInputV2`
  (`planner_v2.py`), preserving `diff_sha256` and hunk order;
- resolves `must_review` from `TargetProfileV2.must_review` — explicit
  `paths`, `patterns` (matched via `fnmatch.fnmatchcase`, the same
  convention `classify_semantic_group_v2` already uses), and `artifact_ids`
  (resolved to each artifact's configured `path` — no new semantics
  invented, just following an existing field reference);
- classifies every path's `semantic_group` via #106's
  `classify_semantic_group_v2`, never a caller-supplied kwarg and never a
  branch on repository name — #86's own ban, verified by this module never
  mentioning `AgentEscala`/`interleitos` by name;
- orchestrates N `plan_lossless_chunks_v2` calls (one per distinct semantic
  group encountered) into a single `ManifestMaterialV2`, respecting
  `TargetBudgetsV2.max_chunks` as a GLOBAL cap across every group;
- routes `missing_paths`/`unrepresentable_paths`/`truncated_paths` (from
  `validate_diff_completeness_v2`) to either a whole-assembly refusal (if a
  must-review path is affected) or silent, audited exclusion (if not);
- computes `profile_hash` (`profile_loader_v2.compute_profile_hash_v2`) and
  `policy_hash` (#106's `compute_effective_policy_hash_v2` — **not**
  `profile_loader_v2.compute_policy_hash_v2`, which stays frozen,
  profile-policy-only) and assembles a real `RunIdentityV2` + `ManifestV2`.

## Two things this module deliberately does NOT compute

- **`evidence_hash` is caller-supplied.** Computing it for real requires a
  real `EvidenceBundleV2` (#128), which requires reading real
  artifact/contract content from a target checkout and sanitizing it
  (`redaction.sanitize_artifact_value`) — F1/#131's job, not this one's.
  This module only consumes the already-computed hash.
- **`max_lines_per_chunk` is caller-supplied**, not derived from
  `TargetBudgetsV2.max_chars_per_chunk`. `TargetBudgetsV2` is
  char-denominated; `plan_lossless_chunks_v2` is line-denominated.
  Reconciling those units is a real, pre-existing gap in this codebase
  (present before this issue, not introduced by it), and inventing a
  chars-per-line conversion policy here would be a speculative,
  hard-to-reverse guess this issue never asked for. Only
  `TargetBudgetsV2.max_chunks` — the one budget field the issue's own
  acceptance criteria names — is consumed.

## The multi-group orchestrator's one real design decision

`plan_lossless_chunks_v2` plans exactly one semantic group per call. Groups
are processed in **sorted semantic-group-name order** (deterministic,
reproducible regardless of input file-diff ordering), each against the
REMAINING global chunk budget after previously-processed groups. A group
that cannot fit within its remaining share blocks the WHOLE assembly.

This is a conservative, deterministic, order-stable allocation, **not** a
search for a globally optimal cross-group packing — a different processing
order might occasionally find a feasible split this one does not.
Global-optimal cross-group bin-packing is out of scope for this
foundational slice; "budget global respeitado entre grupos" (the issue's
own acceptance criterion) means never exceeded, not optimally distributed.

**A group's own auxiliary content can never outgrow its own required
need.** Found by independent review: `plan_lossless_chunks_v2` best-effort-
packs *auxiliary* (droppable-by-design) fragments into any leftover bins up
to whatever `max_chunks` a call is given — so a group with, say, one small
required fragment and several large auxiliary ones could consume bins far
beyond what its required content alone needed, starving a *later* group's
required content of the shared global budget even though the combined
required content across every group would have fit easily. For a group
with any required hunks, this module now packs in two passes: first the
required hunks ALONE, to learn the true minimum bin count they need; then
the full (required + auxiliary) hunk set, capped to EXACTLY that count —
auxiliary can only use leftover room inside bins already claimed for
required content, never an extra one. A group with zero required hunks
anywhere keeps its original, uncapped behavior (full access to the
remaining budget) — it carries no coverage obligation to protect, and this
fix only closes the more surprising case of a group's own auxiliary
outgrowing its own need, not the already-documented "not globally optimal"
cross-group ordering trade-off above.

## Paths that never produce a fragment at all

`ManifestDegradationV2` requires `affected_fragment_ids` — it can only
describe a REAL fragment's omission, and has no way to represent a path
that never produced any fragment in the first place (binary, submodule,
hunkless, truncated, or simply absent from the diff despite being
expected). Rather than force an invented fragment_id or misuse an unrelated
reason code, this module keeps that decision OUTSIDE `ManifestV2` entirely,
via its own `ManifestAssemblyOutcomeV2`/`AssemblyBlockedReasonV2`:

- if such a path is must-review, the WHOLE assembly is refused
  (`state="blocked_pipeline"`) with a typed `AssemblyBlockedReasonV2` —
  never a fabricated `ManifestDegradationV2`;
- otherwise it is silently excluded from `expected_files` (mirroring the
  planner's own precedent for dropped auxiliary fragments) and surfaced,
  for audit, in the outcome's own `excluded_paths`.

A group whose REQUIRED fragments genuinely cannot be packed (global or
per-group budget exhausted) still goes through the EXISTING, already-tested
`plan_lossless_chunks_v2` `blocked_pipeline`/`ManifestDegradationV2` path —
that case always has real fragment_ids, so no new representation is needed
there; this module just reports it as its own
`RUN_ASSEMBLY_GROUP_PACKING_INFEASIBLE_REASON_V2`/
`RUN_ASSEMBLY_GLOBAL_BUDGET_EXHAUSTED_REASON_V2` at the whole-assembly
level.

`expected_paths`, when supplied, is an independent source of "files this run
is expected to touch" (e.g. a GitHub changed-files API list) to cross-check
against the diff's own paths — this is what makes
`validate_diff_completeness_v2`'s `missing_paths` meaningful. Without it
(the default), expected paths are derived from the diff itself, so
`missing_paths` is trivially always empty — there is nothing external to
compare against.

## Deliberately out of scope

- payload content (F1/#131);
- `PayloadSetV2` emission or validation (F2/#132);
- readiness/gate (C1/C2);
- conformance;
- any CLI;
- calling `acquire_authoritative_diff_v2` itself — this module accepts
  already-acquired `ParsedFileDiffV2` tuples, keeping git-subprocess
  acquisition and manifest assembly independently testable;
- deriving `evidence_hash` or a `max_lines_per_chunk`-from-budgets policy
  (see above).

## Tests

`tests/agent_review/test_run_assembly_v2.py` — 18 tests: basic assembly
(real manifest + identity from a single file diff), the issue's own
explicit acceptance criteria verbatim (two targets with different policies
producing different groups without engine changes; a material fragment
change changing both `manifest_hash` and `run_id`; global budget respected
across groups; insufficient `max_chunks` blocking the pipeline; a grep
proving the module never mentions `AgentEscala`/`interleitos`), multi-group
orchestration merging two semantic groups into one manifest, byte-identical
output for identical input, `must_review` resolution (explicit paths,
patterns, and artifact_ids), the artifact_ids defense-in-depth guard
(unreachable through any validly-constructed profile, confirmed via
`model_copy` bypassing that profile's own validator), paths that never
produce a fragment (a non-required binary path silently excluded; a
required binary path blocking the whole assembly; a required path expected
but missing from the diff blocking the whole assembly), the
auxiliary-starvation fix (a group's own auxiliary content never starving a
later group's required coverage, and a pure-auxiliary group's original
behavior confirmed unregressed), and a `SemanticGroupingPolicyV2`
incompatible with the profile raising `RunAssemblyError` rather than a raw
`SemanticGroupingError`.

**Verification of non-vacuity:** the required-path-blocks guard was
temporarily disabled and the corresponding test confirmed to fail (the
binary path was silently assembled instead of blocking), then restored.
