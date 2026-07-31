# AgentReview v2 — target-profile loader and v1 -> v2 migration

Refs #85. Builds on `contracts_v2.TargetProfileV2` (PR #81) and the v2
binding delivery (#83, `docs/AGENT_REVIEW_V2_BINDING.md`).

## Strict loader

`app/agent_review/profile_loader_v2.py` (`load_target_profile_v2`) loads a
target profile from `<repo_root>/.aiops/target-profile.v2.yaml` and fully
revalidates it through `TargetProfileV2.model_validate_json(..., strict=True)`
-- never through JSON Schema alone, and never through a plain-dict
`model_validate` call (which, unlike `model_validate_json`, allows Python-side
enum-from-string coercion even under `strict=True`; this loader always
round-trips through JSON text so strict mode is uniform for every nested
field).

Unlike v1's `repo_profile.load_repo_profile`, there is no silent
degradation to a placeholder profile. Every failure raises
`TargetProfileLoadErrorV2` with one of three reason codes:

- `target_profile_missing` -- the file does not exist;
- `target_profile_unreadable` -- the file exists but cannot be read, is not
  valid YAML, or does not parse into a mapping;
- `target_profile_invalid` -- the document parses, but fails full v2
  schema/contract validation (unknown field, missing required field, type
  coercion, a path outside the safe grammar, an empty `required_checks`
  list, or an attempt to weaken a hard boundary).

### Hard boundaries a target cannot weaken

`network_policy=forbidden`, `fail_closed=true`, `redaction_required=true`,
and `allow_partial_coverage=false` are not loader-level checks: they are
`Literal` types on `TargetPoliciesV2` in `contracts_v2.py` itself, so any
other value is a Pydantic validation error (`target_profile_invalid`) before
the loader ever inspects the profile's business fields.

### Profile and policy hashes

`compute_profile_hash_v2(profile)` / `compute_policy_hash_v2(profile)` hash
the profile's full canonical JSON, and the `policies` object alone,
respectively, using the same canonical-JSON convention documented and
tested throughout `contracts_v2.py` (`sort_keys=True`,
`separators=(",", ":")`, UTF-8, no trailing newline). A policy-only change
flips `policy_hash` without flipping unrelated fields' influence on
`profile_hash` for anything outside `policies`; these functions do not
themselves populate `RunIdentityV2.profile_hash`/`policy_hash` -- that
remains the caller's responsibility when constructing a run identity.

### Base/default-only loading

`load_target_profile_v2` has no opinion on *which* checkout it is pointed
at. Loading effective profile/policy only from the trusted base/default
checkout -- never from a PR branch's working tree -- is the responsibility
of the privileged workflow that calls it, matching the target-repo
integration contract described in `docs/AGENT_REVIEW_V2_ROADMAP.md`.

## Repository-neutral engine

Nothing in this delivery, or anywhere in `app/agent_review/`, branches on a
repository name (`if repo == "mglpsw/AgentEscala"` and similar are absent).
Differences between AgentEscala, InterLeitos, or any other target belong
entirely in each target's own `TargetProfileV2` document, review packs, and
domain contracts -- never in engine code.

## Explicit, non-destructive v1 -> v2 migration

`app/agent_review/profile_migration_v1_v2.py` (`migrate_profile_v1_to_v2`)
is a pure function -- it performs no file I/O and is never invoked by the
review pipeline. Given a validated v1 `TargetProfile`, it returns a
`ProfileMigrationReportV2`:

- `candidate` -- a v2-shaped `dict`, deliberately **not** guaranteed to
  validate as `TargetProfileV2`. v1 profiles structurally lack budgets,
  must-review rules, required checks, and typed contract references with
  hashes; those fields are left `None` rather than invented;
- `pending_decisions` -- a `ProfileMigrationDecisionV2` per field the
  candidate could not resolve, each with a human-readable reason.
  `candidate_is_directly_usable` is `False` whenever any decision is
  pending, which for a v1 input is always: v1 fundamentally lacks the
  information v2 requires;
- artifact `kind` maps directly and losslessly (v1's `ArtifactKind` literal
  is exactly `TargetArtifactV2.kind`'s literal set), but `max_bytes` is
  always flagged pending since v1 never bounded artifact size;
- the hard-boundary policy fields (`network_policy`, `fail_closed`,
  `redaction_required`, `allow_partial_coverage`) are pre-filled with their
  only legal v2 values -- there is nothing to decide there, since v2 does
  not allow a target to choose otherwise.

### CLI tool

`scripts/migrate-agent-review-profile-v1-v2.py` is the only way this
migrator runs. It:

- never overwrites the canonical v2 profile (it only ever writes to
  `--output`, and rejects `--output` paths inside `--repo-root`);
- never runs automatically as part of a review;
- writes a single JSON report (`candidate`, `candidate_is_directly_usable`,
  `pending_decisions`) for human review and manual completion.

## Minimal, hash-pinned toolrepo lock

`requirements-agent-review.lock` pins exactly what
`app/agent_review` imports beyond the standard library: `pydantic` (with
its own required dependencies `pydantic-core`, `annotated-types`,
`typing-extensions`, `typing-inspection`) and `PyYAML`. No FastAPI,
Uvicorn, SQLAlchemy, database driver, or other production-runtime
dependency is present or required.

See `docs/AGENT_REVIEW_V2_INSTALLATION.md` for the install procedure and
toolrepo SHA-pinning contract.
