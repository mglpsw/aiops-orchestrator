# `#203` — `agentreview-v2-target-pack` — Execution-Ready Engineering Specification rev.1

**Trigger:** core reconciliation gate `CORE_GATE_FOR_203=READY` (post `#201-C`/PR #220 merge, `master`=`750096625e8a2ab8793e3c106d38580cca86a617`).
**Class:** design + implementation, single grant, no merge/tag/release/repin/CT104 authorized.

---

## 0. What already exists, and what #203 actually adds

`aiops-orchestrator` already contains a complete, tested AgentReview v2 engine:
content extraction (`#200`), trusted required checks (`#201`), canonical path
identity (`#202`), and readiness wiring (`#201-C`). What does **not** exist is
a way for a **consumer repository** to acquire and operate this engine without
a human manually copying files and writing bespoke YAML by hand — which is
exactly how `AgentEscala`/`InterLeitos` are integrated today (via fixtures in
`tests/agent_review/fixtures/v2/*`, never a real installer).

`#203` builds the **installer**, not a new engine. Every contract, loader, CLI
convention, and file shape below is reused verbatim from the existing v2
implementation; nothing here re-derives review, readiness, or authority logic.

**Reuse inventory (verified live, not assumed):**

| Existing | Reused for |
|---|---|
| `app/agent_review/profile_loader_v2.py` (`load_target_profile_v2`, `compute_profile_hash_v2`) | pack `validate`/`doctor` profile loading |
| `app/agent_review/authoritative_check_policy_v2.py` | trusted-check inventory validation |
| `scripts/verify-agent-review-v2-conformance.py` | foundation for `agent-review target conformance` |
| `tests/agent_review/fixtures/v2/agent_escala/.aiops/{target-profile.v2.yaml,authoritative-checks.v2.yaml}` | canonical shape of what `init` generates |
| `schemas/agent-review/v2/*.schema.json` (19 files) | pack ships/pins these verbatim; never forks them |
| `scripts/export-agent-review-v2-schemas.py` byte-identity discipline | applied to the new pack manifest/receipt schema too |
| `docs/AGENT_REVIEW_V2_*.md` doc set | the pattern this spec's own docs follow |

---

## 1. Ownership boundary (restated precisely, binding)

```text
aiops-orchestrator (this repo):
  engine (app/agent_review/*)                     -- UNCHANGED by #203
  generic templates                                -- NEW, #203
  schemas (existing v2 + 2 new pack-only schemas)   -- additive only
  pack compiler/installer CLI                       -- NEW, #203
  doctor/validate/conformance                       -- NEW CLI wrapping existing engine + verify script
  install/upgrade/rollback                          -- NEW, #203
  generic trusted-check adapter (inventory SCHEMA)  -- NEW, #203 (not commands)
  receipts/manifests (identity contract)            -- NEW, #203

target repo (consumer, e.g. AgentEscala):
  target profile / domain contracts / review packs  -- target-authored, pack VALIDATES, never GENERATES content
  extra DLP                                         -- target-authored plugin point
  trusted-check inventory / allowlisted commands     -- target-authored data, pack provides the SCHEMA only
  runner/secret binding DECLARATIONS (names only)    -- target-authored
  rollout decision (OFF/SHADOW_MINIMAL/SHADOW_FULL)  -- target-authored, pack enforces ceiling

Router: inference transport only. Never readiness, never authority. Unchanged.
CAEM: referenced/pinned (`config/caem/caem-3.0-f0.pin.json`), never reinvented.
```

**Invariant carried forward from `#201-C`, restated for `#203`:**

```text
#203 MAY INSTALL/CONFIGURE INTEGRATION.
#203 MUST NEVER CREATE AUTHORITY, FORK THE ENGINE, OR SILENTLY PROMOTE ROLLOUT.
```

---

## 2. New contracts (additive, non-breaking)

Two new pydantic contracts, following the exact `ContractV2Model` pattern in
`contracts_v2.py` (frozen, strict, `schema_id`/`schema_version` literals,
`model_validator(mode="after")` for cross-field invariants). **Neither touches
any existing schema file.**

### 2.1 `TargetPackManifestV2` (`app/agent_review/target_pack_manifest_v2.py`)

The compiled, versioned description of what the pack CAN install — built once
per toolrepo release, consumed by every target's `install`/`upgrade`.

```python
class TargetPackManifestV2(ContractV2Model):
    schema_id: Literal["agent-review.target-pack-manifest.v2"]
    schema_version: Literal[2]
    pack_version: SafeText              # semver, e.g. "2.3.0"
    toolrepo_sha: GitSha
    generated_files: tuple[GeneratedFileEntryV2, ...]   # relative path -> content sha256 + OWNERSHIP class
    schema_digests: Mapping[str, Sha256]  # every schemas/agent-review/v2/*.json this pack version pins
    required_capabilities: tuple[SafeIdentifier, ...]    # e.g. "isolated_executor", "router_transport"
    min_engine_contract_version: Literal[2]
```

`GeneratedFileEntryV2.ownership` is one of `UPSTREAM_GENERATED` /
`TARGET_OWNED` / `MERGED_DECLARATIVE` (§7). This is the single source of
truth an installer diffs against — never a hand-maintained file list.

### 2.2 `TargetInstallReceiptV2` (`app/agent_review/target_install_receipt_v2.py`)

Written to `.aiops/install-receipt.v2.json` in the **target** repo after every
successful `install`/`upgrade`. This is the pack's own frozen contract,
independent of `TargetProfileV2`/`RunIdentityV2` (which describe a *review
run*, not an *installation*).

```python
class TargetInstallReceiptV2(ContractV2Model):
    schema_id: Literal["agent-review.target-install-receipt.v2"]
    schema_version: Literal[2]
    pack_version: SafeText
    toolrepo_sha: GitSha
    target_repo: SafeText
    target_profile_hash: Sha256
    target_policy_hash: Sha256
    review_pack_hashes: Mapping[SafeIdentifier, Sha256]
    generated_file_hashes: Mapping[SafeText, Sha256]     # path -> content sha256, UPSTREAM_GENERATED only
    target_owned_paths: tuple[SafeText, ...]              # preserved, never hashed/diffed by us
    required_capabilities: tuple[SafeIdentifier, ...]
    expected_runner_labels: tuple[SafeIdentifier, ...]
    required_secret_names: tuple[SafeIdentifier, ...]     # NAMES ONLY -- validated to reject anything value-shaped
    rollout_mode: Literal["off", "shadow_minimal", "shadow_full"]
    compatibility: Literal["compatible", "major_incompatible"]
    previous_install_identity: ReceiptIdentityRefV2 | None  # for rollback
    receipt_hash: Sha256   # self-referential digest over everything above, computed the same way compute_run_id works

    @model_validator(mode="after")
    def validate_no_secret_values(self) -> "TargetInstallReceiptV2":
        # required_secret_names entries must match a NAME shape (identifier-like,
        # <=128 chars) -- refuses anything that looks like it could be a token/
        # credential value, fail-closed. Mirrors the discipline `redact_content`/
        # `sanitize_artifact_value` already apply elsewhere in this codebase.
        ...
```

`generation timestamp` is deliberately **excluded** from `receipt_hash`'s
preimage (per the grant's own instruction) — it may be stored as a plain
informational field, never part of canonical identity, so two installs of the
byte-identical pack+profile+policy always produce byte-identical receipts.

No new field carries a secret VALUE anywhere. `required_secret_names` is
validated, not merely documented, to reject value-shaped strings.

---

## 3. CLI surface

New script `scripts/agent-review-target-pack-v2.py`, dispatching subcommands —
matches the existing `-v2` suffix convention (`#102`) and the existing
single-file-per-concern-but-shared-argparse-dispatch pattern already used by
`aiops-review-quality-gate-v2.py`/`aiops-acquire-authoritative-checks-v2.py`.

```text
agent-review-target-pack-v2.py init        --target-root PATH --profile-seed PATH [--rollout off|shadow_minimal]
agent-review-target-pack-v2.py doctor      --target-root PATH                       # READ-ONLY, always
agent-review-target-pack-v2.py validate    --target-root PATH
agent-review-target-pack-v2.py conformance --target-root PATH --matrix PATH         # wraps verify-agent-review-v2-conformance.py
agent-review-target-pack-v2.py install-workflows --target-root PATH [--dry-run]
agent-review-target-pack-v2.py upgrade     --target-root PATH [--dry-run] [--yes]
agent-review-target-pack-v2.py rollback    --target-root PATH [--dry-run] [--yes]
```

Each subcommand is a thin CLI wrapper — same discipline as `#201-C`'s own
`produce_review_readiness_v2`: **the CLI never re-implements a decision**, it
calls one pure/testable library function in `app/agent_review/target_pack_*`
and prints/writes its result. This is what makes the 30-scenario test matrix
(§9) tractable without a subprocess per case — every scenario has a direct
Python entry point, subprocess E2E only for a representative subset (mirrors
`test_aiops_review_quality_gate_v2_cli.py`'s own split).

`doctor` is READ-ONLY by construction: its library function
(`app/agent_review/target_pack_doctor_v2.py::run_doctor_v2`) takes no
mutating parameter and is proven so by an AST test analogous to
`test_required_check_readiness_arch_v2.py` (no `Path.write_text`/`.mkdir`/
`shutil.*` call anywhere in its call graph) — the same mechanical-proof
discipline `#201-C` established, applied to a new invariant.

---

## 4. Installation contract

### 4.1 Determinism / idempotence

`install`/`upgrade` compute a **plan** (list of `(path, action, ownership)`
tuples) from `(TargetPackManifestV2, current target-root state)` as a PURE
function (`app/agent_review/target_pack_plan_v2.py::compute_install_plan_v2`)
before touching the filesystem. Applying the identical plan twice produces
byte-identical output and an empty second-run diff — proven by property test
(§9), not by convention.

### 4.2 Drift detection (the safe-file-ownership core, §7)

Before writing any `UPSTREAM_GENERATED` file, compute its current on-disk
sha256 and compare to the value recorded in the **previous**
`TargetInstallReceiptV2.generated_file_hashes`. Three cases:

- no previous receipt (fresh `init`) → write, no drift possible;
- on-disk hash matches previous receipt → safe to overwrite (target never
  touched it);
- on-disk hash diverges from previous receipt → **drift**: refuse by
  default, print the specific path + a real diff, require `--force-overwrite
  PATH` naming that exact path (never a blanket force flag) to proceed.

`TARGET_OWNED` paths are **never** read, hashed, diffed, or written by the
pack outside `init` (which creates them once from a seed template, then never
touches them again). `MERGED_DECLARATIVE` files (e.g., a target's
`.gitignore` needing one pack-owned block) use a fenced-block merge strategy
(`# --- agent-review-v2:begin ---` / `:end`) — only the fenced region is ever
replaced.

### 4.3 Rollback

`rollback` restores the file set recorded in
`previous_install_identity` (itself a full prior
`TargetInstallReceiptV2` reference, stored by `upgrade`/`install` before
overwriting). Refuses if the target's CURRENT state doesn't match the receipt
it's about to roll back FROM (cross-target/tampered-receipt protection, see
§10 adversarial list) — same "verify before trusting" discipline as
`#201-C0`'s re-derivation, applied to install state instead of check results.

---

## 5. Workflow templates

Three generic GitHub Actions templates in `templates/workflows/`:
`evidence.yml`, `analysis.yml`, `publish.yml`. Security model (restated from
the grant, now bound to concrete jobs):

```text
evidence.yml:  pull_request trigger, secretless, checkout + acquire diff/
               manifest only, uploads artifact for analysis.yml.
analysis.yml:  workflow_run trigger (never pull_request_target on subject
               code), base-owned checkout, Router access only if the
               target's rollout mode allows it, DLP before any transport,
               trusted-check executor invoked only via the isolated broker
               (#201-B2/B3) -- never inline shell.
publish.yml:   minimum GITHUB_TOKEN scope (pull-requests: write, contents:
               read), consumes ONLY the sanitized ReviewReadinessV2/finding
               artifact from analysis.yml, never re-derives anything.
```

None of the three ever gives PR-controlled code the harness, serializer,
authority decision, publisher, policy, or trusted inventory — restating
`#201`'s own boundary, now enforced at the workflow-template level too.

---

## 6. Trusted-check ownership, precisely

Per the grant: the pack owns the **mechanism**, the target owns the
**inventory**.

- `TrustedCheckInventoryV2` (new, additive schema) — the SHAPE a target's
  `.aiops/trusted-checks.v2.yaml` must have: `check_name`, allowlisted
  `command` (argv list, never a shell string), `working_directory`,
  `timeout_seconds`, `resource_limits`. This schema lives in
  `aiops-orchestrator`; the pack's `install` copies it as a seed TEMPLATE the
  target then edits.
- The pack's `validate`/`doctor` load this file with
  `load_authoritative_check_policy_v2`-style strict parsing and cross-check
  it against `TargetProfileV2.policies.required_checks` using the SAME
  bidirectional-equality discipline `validate_policy_against_profile_v2`
  already enforces (`#201-C0`) — reused, not reimplemented.
- The pack NEVER invents a target-specific command (no hardcoded `pytest`,
  no hardcoded `mypy`) anywhere in `app/agent_review/*` or
  `templates/*`. Grepped for after implementation as an architecture test
  (mirrors the `#201-C` "no target-name branch in generic engine" invariant,
  §9's last scenario).
- Allowlisting a command is `containment` — it says nothing about whether
  that command's result reaches `authority=TRUSTED` in a given run.
  `containment != evidence != provenance != authority` stays a hard
  vocabulary boundary in every docstring here, exactly as demanded.

---

## 7. Safe file ownership (formalized)

```text
UPSTREAM_GENERATED  -- pack writes/overwrites (subject to drift check, §4.2)
TARGET_OWNED         -- pack writes ONCE at init, never touches again
MERGED_DECLARATIVE   -- pack writes/updates ONLY its own fenced block
```

Classification lives in `TargetPackManifestV2.generated_files[].ownership`
(§2.1) — a single source of truth, not scattered path-pattern-matching in
each CLI command.

---

## 8. Rollout modes

```text
OFF             -- pack installed, nothing runs.
SHADOW_MINIMAL  -- semantic review + informational publication only.
                   No trusted-check integration configured; a required-check
                   claim under this mode is refused by install/doctor
                   (there is nothing legitimate to promote to -- this mode
                   cannot even ask #201-C0's boundary a required-check
                   question, because the pack never wires trusted checks in).
SHADOW_FULL     -- semantic review + trusted-check integration wired through
                   the existing #201-C0/#201-C chain; ReviewReadinessV2 is
                   REAL and calculable. Still never becomes a required/
                   default/primary branch-protection check -- that is a
                   human action outside this grant's authority, always.
```

`install`/`upgrade` refuse to write a rollout mode HIGHER than
`--rollout` explicitly requested — no silent ceiling promotion, checked by a
dedicated property test (§9).

---

## 9. Test strategy (the full list from the grant, mapped to concrete tests)

Organized like `#201-C`'s own Class A/B/C split: **Class A** = real CLI
subprocess E2E against a synthetic target-root in `tmp_path`; **Class B** =
pure `compute_install_plan_v2`/manifest-diff composition, no filesystem;
**Class C** = deferred (a REAL AgentEscala/InterLeitos live install is target
adoption, out of `#203`'s own scope per `#201`/`#202`'s own reconciliation).

| Scenario (from the grant) | Class | Test |
|---|---|---|
| init in eligible empty/synthetic repo | A | `test_init_creates_the_full_generated_set` |
| reinstall exact same pack = idempotent | A/B | `test_install_twice_produces_an_empty_second_diff` |
| generated-file drift | A | `test_upgrade_refuses_on_unrecorded_generated_file_drift` |
| target-owned modification preservation | A | `test_upgrade_never_touches_a_target_owned_path` |
| compatible upgrade | A | `test_upgrade_minor_version_applies_cleanly` |
| incompatible upgrade | A | `test_upgrade_major_incompatible_refuses_without_force` |
| rollback | A | `test_rollback_restores_the_previous_generated_set` |
| missing profile | A | `test_doctor_reports_missing_profile_without_mutating` |
| invalid profile | A | `test_validate_refuses_a_structurally_invalid_profile` |
| missing target-owned policy | A | `test_doctor_reports_missing_trusted_check_inventory` |
| missing runner/secret diagnosed w/o mutation | A | `test_doctor_reports_missing_secret_names_read_only` |
| Unicode paths | B | `test_plan_handles_accented_target_root_paths` (reuses `#202`'s own fixtures) |
| quoted/backslash paths | B | same, reusing `#202` fixtures directly -- no new heuristic |
| two different synthetic targets from same pack | A | `test_agent_escala_and_interleitos_synthetic_targets_diverge_correctly` |
| target-specific DLP extension | A | `test_target_owned_dlp_plugin_blocks_before_router` (synthetic, no real PHI) |
| v1 coexistence | A | `test_install_never_touches_v1_required_checks_or_branch_protection` |
| OFF → SHADOW_MINIMAL | A | `test_upgrade_off_to_shadow_minimal` |
| SHADOW_MINIMAL → SHADOW_FULL plan | A | `test_upgrade_plan_shadow_minimal_to_shadow_full` |
| forbidden upward rollout | A | `test_install_refuses_to_silently_exceed_requested_rollout_ceiling` |
| manifest/receipt determinism | B | `test_receipt_hash_is_deterministic_and_excludes_timestamp` |
| tampered receipt | A | `test_rollback_refuses_on_a_tampered_previous_receipt` |
| changed toolrepo SHA | B | `test_upgrade_records_the_new_toolrepo_sha` |
| changed target profile | A | `test_upgrade_detects_a_changed_target_profile_hash` |
| clean environment install | A | `test_init_in_a_genuinely_empty_synthetic_repo` |
| repeated install/upgrade cycles | A | `test_five_consecutive_upgrade_cycles_stay_idempotent` (metamorphic) |
| no target-name branch in generic engine | static | `test_no_target_specific_literal_in_generic_pack_code` (AST/grep architecture test) |

Plus the injection/adversarial set lands under §10, run iteratively during
implementation per the grant's own instruction (not saved for the end).

---

## 10. Threat model / adversarial checklist (attacked iteratively, not at the end)

| ID | Threat | Control |
|---|---|---|
| P-T1 | ownership escape (target file silently overwritten) | §4.2 drift detection, `TARGET_OWNED` never diffed |
| P-T2 | path traversal in target-root or generated path | reuse `#202`'s own fail-closed path validators |
| P-T3 | symlink swap between plan compute and apply | `os.path.realpath` resolution + re-check immediately before write, TOCTOU-aware like `#201-C`'s merge gate |
| P-T4 | stale receipt reuse across rollback | §4.3 refuses if current state != receipt-recorded state |
| P-T5 | forged manifest | `TargetPackManifestV2` validated against the SAME toolrepo release building it; `install` refuses a manifest whose `toolrepo_sha` isn't the one it was invoked from |
| P-T6 | rollback to a mismatched target | receipt binds `target_repo`; refuse cross-target rollback |
| P-T7 | cross-repo / cross-target replay | same binding as P-T6, symmetric to `#201-C0`'s run-identity binding |
| P-T8 | toolrepo pin mismatch | `required_capabilities`/`min_engine_contract_version` checked at `doctor`/`install` |
| P-T9 | upgrade/downgrade attack | `compatibility` field + semver comparison, refuse silent downgrade |
| P-T10 | install interrupted midway / partial writes | write-to-temp-then-atomic-rename per file, plan is all-or-nothing at the file level |
| P-T11 | workflow generation nondeterminism | golden-file byte-identity test, same discipline as `export-agent-review-v2-schemas.py --check` |
| P-T12 | malicious target input attempting YAML/script injection | `yaml.safe_load` only, never `yaml.load`; generated workflow YAML never string-interpolates target data into shell |
| P-T13 | shell quoting | trusted-check commands are argv lists (§6), never a shell string, mirrors `#201`'s own "comando derivado de inventário, nunca texto livre" |
| P-T14 | arbitrary command injection via trusted-check inventory | schema-level allowlist validation (§6) + `subprocess.run(argv, shell=False)` always |
| P-T15 | secret VALUE persisted | `TargetInstallReceiptV2.validate_no_secret_values` (§2.2) |
| P-T16 | rollout ceiling bypass | §8's explicit refuse-to-exceed-requested-ceiling test |

---

## 11. Self-audit of this specification (per the grant's own mandated step)

- **Duplicated concepts?** Checked: `TargetInstallReceiptV2` is deliberately
  NOT another `RunIdentityV2` — it identifies an *installation*, not a
  *review run*; the two never collapse into one contract. Trusted-check
  inventory SCHEMA (pack-owned) is deliberately separate from trusted-check
  COMMANDS (target-owned data) — no duplication of `#201`'s own
  containment/evidence/provenance/authority vocabulary.
- **Ownership inversion?** Checked: nothing in `app/agent_review/target_pack_*`
  reads a target-specific literal; §6 commits to an architecture test
  enforcing this permanently, not just at spec time.
- **Authority inflation?** Checked: `SHADOW_FULL` still never self-promotes
  to a required/default check (§8); the pack has no code path that calls
  any GitHub branch-protection API at all — confirmed as a design absence,
  not a guarded one, since "guarded" would still mean the capability exists.
- **Target leakage?** Checked: `templates/workflows/*.yml` are Jinja-free,
  parameterized only via files the pack ALREADY validates schema-first
  (§5) — no target string ever gets string-interpolated into generated
  YAML that then executes as shell.
- **Non-idempotent writes?** Checked: §4.1's plan-then-apply split, with the
  empty-second-diff property test as the actual proof, not a claim.
- **Rollback ambiguity?** Checked: §4.3 + P-T4/P-T6/P-T7 close the three
  concrete ambiguity vectors (stale, cross-target, tampered).
- **Drift ambiguity?** Checked: §4.2's three-case table is total and
  disjoint — no state falls outside it.
- **Hidden mutable state?** Checked: `compute_install_plan_v2` and
  `run_doctor_v2` are pure; the only mutation points are the `apply_plan_v2`
  writes, explicitly named as such.
- **Schema/versioning mistakes?** Checked: both new contracts pin
  `schema_version: Literal[2]` matching the rest of the v2 family (not v1,
  not an unrelated v3); `pack_version` (semver) is kept explicitly SEPARATE
  from `schema_version` (contract shape) — an upgrade can bump one without
  the other, and the compatibility check (§4.3/P-T9) operates on the right
  one for each question.

No blocking defect found in this self-audit; proceeding to implementation.

---

## 12. Deferred (explicitly, not silently)

- Class C tests (real AgentEscala/InterLeitos live install) — target
  adoption, per `#204`'s own charter, not `#203`.
- Actual publication/pinning of the pack (`#205`).
- The round-11 adjacent CLI `--output` OSError defect (`#222`) — not folded
  in here per the merge grant's own explicit instruction, unless `#203`'s
  own new IO layer happens to own that exact code path naturally (it does
  not — that defect is in the existing quality-gate v2 CLI, not the new
  pack CLI).
- Real GitHub Actions runner execution proof — CT104-scoped, deferred to
  whatever capability grant actually has that access.
