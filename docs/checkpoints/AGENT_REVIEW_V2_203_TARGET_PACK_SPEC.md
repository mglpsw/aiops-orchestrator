# `#203` — `agentreview-v2-target-pack` — Execution-Ready Engineering Specification **rev.2**

**Status:** `OPERATIVE SPECIFICATION | V2 DEVELOPMENT` — current specification for
`#203`, supersedes rev.1 in full, still the authority `AGENT_REVIEW_V2_TARGET_PACK.md`
cites for every deferred (`§12`) subcommand's classification. Not superseded by
any later document. This is distinct from the SHA/PR/issue-state snapshot
embedded in its own body, which the "Live-state rule" below already marks as
dated, never a live trigger. For overall repository state, see
[`../PROJECT_STATUS.md`](../PROJECT_STATUS.md).

**rev.1 = historical design baseline.** Its architecture was approved and is
substantially carried forward here; it is preserved in Git history and is no
longer the operative document. Where rev.1 and rev.2 differ, rev.2 governs.

**Live-state rule.** Every SHA, PR number and issue state in this document is a
**dated checkpoint, never a live trigger**. rev.1 pinned
`master=750096625e8a2ab8793e3c106d38580cca86a617` as if it were a standing
condition; it was merely the base at authoring time. Before acting on this
spec, revalidate live `master`, PR and HEAD. A checkpoint that disagrees with
observed live state loses.

**Authoring context (checkpoint, not condition):** written after PR #220
(`#201-C`) and PR #224 (verified-check canonical order) merged; reconciled
against the adversarial findings on PR #223 slice 1.

**Class:** design specification. Confers no implementation, merge, tag,
release, repin, rollout or CT104/CT102 authority by itself.

---

## 0. What already exists, and what `#203` actually adds

`aiops-orchestrator` already contains a complete, tested AgentReview v2 engine:
content extraction (`#200`), trusted required checks (`#201`), canonical path
identity (`#202`), and readiness wiring (`#201-C`). What does **not** exist is
a way for a **consumer repository** to acquire and operate this engine without
a human manually copying files and writing bespoke YAML — which is how
`AgentEscala`/`InterLeitos` are integrated today (via fixtures under
`tests/agent_review/fixtures/v2/*`, never a real installer).

`#203` builds the **installer**, not a new engine. Every contract, loader, CLI
convention and file shape below is reused from the existing v2 implementation;
nothing here re-derives review, readiness or authority logic.

**Reuse inventory (verified live, not assumed):**

| Existing | Reused for |
|---|---|
| `profile_loader_v2.py` (`load_target_profile_v2`, `compute_profile_hash_v2`) | pack `validate`/`doctor` profile loading and profile-hash binding |
| `authoritative_check_policy_v2.py` | trusted-check inventory validation |
| `scripts/verify-agent-review-v2-conformance.py` | foundation for `agent-review target conformance` |
| `tests/agent_review/fixtures/v2/agent_escala/.aiops/*` | canonical shape of what `init` generates |
| `schemas/agent-review/v2/*.schema.json` | pack ships/pins these verbatim; never forks them |
| `scripts/export-agent-review-v2-schemas.py` byte-identity discipline | applied to the new pack schemas too |
| `contracts_v2.py` `ContractV2Model`, `RelativePath`, `Sha256`, `GitSha` | both new contracts; path safety is inherited, never re-implemented |

---

## 1. Ownership boundary (binding)

```text
aiops-orchestrator (this repo):
  engine (app/agent_review/*)                       -- UNCHANGED by #203
  generic templates                                  -- #203
  schemas (existing v2 + pack-only additions)        -- additive only
  pack compiler/installer CLI                        -- #203
  doctor/validate/conformance                        -- #203, wrapping existing engine
  install/upgrade/rollback                           -- #203
  trusted-check inventory SCHEMA (not commands)      -- #203
  receipts/manifests (install identity contract)     -- #203

target repo (consumer):
  target profile / domain contracts / review packs   -- target-authored; pack VALIDATES, never GENERATES content
  extra DLP                                          -- target-authored plugin point
  trusted-check inventory / allowlisted commands     -- target-authored data; pack provides the SCHEMA only
  runner/secret binding DECLARATIONS (names only)    -- target-authored
  rollout decision                                   -- target-authored, pack enforces the ceiling

Router: inference transport only. Never readiness, never authority.
CAEM: referenced/pinned (config/caem/caem-3.0-f0.pin.json), never reinvented.
```

**Invariant carried forward from `#201-C`:**

```text
#203 MAY INSTALL/CONFIGURE INTEGRATION.
#203 MUST NEVER CREATE AUTHORITY, FORK THE ENGINE, OR SILENTLY PROMOTE ROLLOUT.
```

### 1.1 `TARGET_OWNED != PR_HEAD_OWNED`  *(rev.2)*

Ownership of a file's **content** by the target never implies that the **PR
branch** may supply that file to a privileged reader.

```text
target-owned  ≠  PR-controlled

privileged analysis consumes:
  profile / policy / trusted-check inventory / DLP policy
  from the base/default-owned checkout,
  bound to digest and identity,
  never the PR HEAD's versions.
```

This restates, at pack level, the trust assumption `profile_loader_v2` already
documents and `#201-C`'s R1 amendment enforces: the required-check set is
derived from a trusted checkout bound to `identity.profile_hash`, never from
caller- or branch-supplied data. Any future slice wiring trusted checks,
workflows or `SHADOW_FULL` must satisfy this boundary before it ships.

---

## 2. Contracts (additive, non-breaking)

Two pack contracts following the `ContractV2Model` pattern (frozen, strict,
`schema_id`/`schema_version` literals, `model_validator(mode="after")` for
cross-field invariants). Neither touches any pre-existing schema.

### 2.1 `TargetPackManifestV2`

The compiled, versioned description of what one toolrepo release CAN install.

```python
class TargetPackManifestV2(ContractV2Model):
    schema_id: Literal["agent-review.target-pack-manifest.v2"]
    schema_version: Literal[2]
    pack_version: SafeText
    toolrepo_sha: GitSha
    generated_files: tuple[GeneratedFileEntryV2, ...]     # min_length=1; path -> sha256 + ownership
    schema_digests: Mapping[str, Sha256]                   # min_length=1
    required_capabilities: tuple[SafeIdentifier, ...]
    min_engine_contract_version: Literal[2]
    max_supported_rollout_mode: Literal["off", "shadow_minimal", "shadow_full"]   # rev.2
```

`max_supported_rollout_mode` is **declared capability vs delivered capability**
made explicit: the highest mode this pack version genuinely wires end to end.
A caller may request a rollout mode only up to this value. Naming a mode is not
delivering it — `shadow_full` requires trusted-check integration to *exist*.

### 2.2 `TargetInstallReceiptV2`

Written to `.aiops/install-receipt.v2.json` in the target after every successful
`install`/`upgrade`. Deliberately **not** `RunIdentityV2`: it identifies an
*installation*, never a *review run*. It carries no review or readiness
authority.

```python
class TargetInstallReceiptV2(ContractV2Model):
    schema_id: Literal["agent-review.target-install-receipt.v2"]
    schema_version: Literal[2]
    pack_version: SafeText
    toolrepo_sha: GitSha
    target_repo: SafeText
    target_profile_hash: Sha256
    target_policy_hash: Sha256 | None = None      # rev.2: absence is None, never a fabricated digest
    review_pack_hashes: Mapping[SafeIdentifier, Sha256]
    generated_file_hashes: Mapping[SafeText, Sha256]   # UPSTREAM_GENERATED only
    target_owned_paths: tuple[SafeText, ...]           # declared ownership, not "written this run"
    required_capabilities: tuple[SafeIdentifier, ...]
    expected_runner_labels: tuple[SafeIdentifier, ...]
    required_secret_names: tuple[SafeIdentifier, ...]  # NAMES ONLY, validated
    rollout_mode: Literal["off", "shadow_minimal", "shadow_full"]
    compatibility: Literal["compatible", "major_incompatible"]
    previous_install_identity: ReceiptIdentityRefV2 | None
    generated_at: Rfc3339Timestamp | None = None
    receipt_hash: Sha256
```

**`target_policy_hash: Sha256 | None`** *(rev.2)*. rev.1 typed this as a plain
`Sha256`, which forced a writer with no policy artifact to invent one. A
syntactically valid all-zero digest is indistinguishable from a real policy hash
to any schema consumer — the same fabricated-identity class already eliminated
for `toolrepo_sha` and `target_profile_hash`. Absence is now represented as
absence.

**Ownership-derived fields** *(rev.2)*. `generated_file_hashes` and
`target_owned_paths` are derived from the manifest's ownership classification,
never from "which files happened to be written this invocation". A fresh `init`
must not record a `TARGET_OWNED` path as generated content, and an idempotent
re-`init` must not drop a target-owned path merely because nothing was written.

`generated_at` is excluded from `receipt_hash`'s preimage, so two installs of a
byte-identical pack+profile+policy produce byte-identical receipts.
`required_secret_names` is validated to reject value-shaped strings — defence in
depth alongside the rule that the pack never reads an environment variable's
VALUE anywhere.

---

## 3. Pack material identity  *(rev.2, new)*

```text
toolrepo_sha
  ↔ immutable Git tree of that SHA
  ↔ schemas ↔ templates ↔ seed bytes
  ↔ manifest digests
  ↔ receipt
```

`toolrepo_sha` MUST identify the exact bytes the pack consumed. Reading pack
material from the **working tree** breaks this in two ways:

- a dirty checkout yields a receipt asserting a clean HEAD while installing
  different bytes;
- a glob over the schema directory admits **untracked** files into
  `schema_digests`, silently changing the manifest digest.

**Rule.** Pack material is derived from the Git tree at `toolrepo_sha`:
`git ls-tree` to enumerate, `git show <sha>:<path>` to read. This makes coverage
structural rather than a maintained list that can drift.

A fail-closed dirty-checkout guard may exist as **defence in depth**, but is not
the definition of identity: if the bytes can be derived from the Git object,
they must be. A guard alone is acceptable only if it provably covers every
material input, including untracked additions.

**Invariant.** A relevant dirty toolrepo MUST NOT produce a successful receipt
claiming a clean HEAD.

**Consequence, documented plainly.** `init` from a dirty checkout installs
*committed* bytes, and the receipt truthfully says so.

---

## 4. CLI surface

`scripts/agent-review-target-pack-v2.py`, argparse subcommands, matching the
existing `-v2` convention. Each subcommand is a thin wrapper: it parses args,
calls exactly one library function in `app/agent_review/target_pack_*`, and
prints/writes the result. **The CLI never re-implements a decision** — the same
discipline `#201-C`'s `produce_review_readiness_v2` follows.

```text
init        --target-root PATH --toolrepo-root PATH --target-repo OWNER/NAME
            --pack-version X.Y.Z [--rollout off|shadow_minimal|shadow_full]
doctor      --target-root PATH --toolrepo-root PATH --pack-version X.Y.Z   # READ-ONLY
validate    --target-root PATH
conformance --target-root PATH --matrix PATH
install-workflows --target-root PATH [--dry-run]
upgrade     --target-root PATH [--dry-run] [--yes]
rollback    --target-root PATH [--dry-run] [--yes]
```

`init` and `doctor` are implemented (slice 1). The rest are specified here and
deferred (§12).

`doctor` is **READ-ONLY by construction**: it accepts no mutating parameter and
calls no write/mkdir/rename/remove primitive anywhere in its call graph, proven
mechanically by AST/call-graph inspection — the same mechanical-proof discipline
`#201-C` established, applied to a new invariant.

### 4.1 `doctor` diagnoses identity, not just structure  *(rev.2)*

A receipt that *parses* is not a receipt that describes *this* install. `doctor`
cross-checks, in deterministic order, first mismatch winning:

```text
receipt.pack_version        vs manifest.pack_version
receipt.toolrepo_sha        vs manifest.toolrepo_sha
receipt.target_profile_hash vs the profile actually on disk   (only when it loaded)
receipt.rollout_mode        vs manifest.max_supported_rollout_mode
```

Structural validity alone must never yield `healthy=true`.

---

## 5. Installation contract

### 5.1 Preflight before mutation  *(rev.2)*

```text
resolve immutable toolrepo
  → build/validate manifest
  → validate rollout against pack capability
  → compute plan
  → only then create or write any target path
```

**No directory creation, no file write, before every applicable preflight gate
has passed.** A previously nonexistent `target_root` MUST remain nonexistent
after any refusal — unresolvable toolrepo SHA, rollout above the pack ceiling,
invalid manifest, or plan failure.

### 5.2 Determinism / idempotence

`compute_install_plan_v2` derives a plan of `(path, action, ownership)` from
`(manifest, current target state, previous receipt)` as a **pure** function that
never touches the filesystem beyond reading. Applying the identical plan twice
produces byte-identical output and an empty second-run diff — proven by property
test, not convention.

### 5.3 Drift detection

For every `UPSTREAM_GENERATED` entry, compare the on-disk sha256 against the
value in the previous receipt. The table is total and disjoint:

| previous receipt | on-disk | action |
|---|---|---|
| absent | absent | `WRITE_NEW` |
| absent/any | equals seed content | `NOOP_UNCHANGED` |
| present | equals recorded hash | `OVERWRITE_SAFE` |
| present/absent | diverges | `REFUSE_DRIFT` |

Drift refuses **outright — nothing is written, for any path** — unless the exact
drifted path is named explicitly. Never a blanket force flag.

`TARGET_OWNED` paths are written once at `init` and never read, hashed, diffed or
written again. `MERGED_DECLARATIVE` files have only their fenced region
(`# --- agent-review-v2:begin ---` / `:end`) replaced; content outside the
markers is preserved byte-for-byte.

### 5.4 Mutation boundary and root identity  *(rev.2)*

```text
InstallPlanV2.target_root_real  →  all file writes  →  receipt write
```

The resolved root captured **at plan time** must survive through the receipt
commit. Re-resolving the root independently for the receipt reintroduces the
TOCTOU that the apply-time check closes, and can land files and receipt under
**different roots**. Receipt persistence is therefore bound to the plan's root
identity, or moved inside the same mutation boundary.

Every write to a target repository — receipt included — goes through the single
writer, which re-verifies containment immediately before writing.

### 5.5 Recovery semantics  *(rev.2)*

Per-file atomicity (temp file in the same directory, then `os.replace`) stands:
a process killed mid-write leaves either the old or the fully-written new file,
never a partial one.

For multi-file operations:

```text
interruption at N of M
  → no valid final receipt
  → rerun detects the intermediate state
  → deterministic convergence to the same final state
```

**A valid receipt represents a complete installation commit. An intermediate
state must never be able to look like a completed installation.** The receipt is
the commit marker: its absence or invalidity means "not completed", never
"completed but unrecorded". This becomes materially important once
`upgrade`/`install-workflows` write more than one file.

### 5.6 Rollback

`rollback` restores the set recorded in `previous_install_identity`, refusing if
the target's current state does not match the receipt it is rolling back *from*
— the same "verify before trusting" discipline as `#201-C0`'s re-derivation,
applied to install state.

---

## 6. Workflow templates

`evidence.yml`, `analysis.yml`, `publish.yml` (deferred, §12):

```text
evidence.yml:  pull_request trigger, secretless, checkout + acquire diff/manifest
               only, uploads an artifact.
analysis.yml:  workflow_run trigger (never pull_request_target on subject code),
               base-owned checkout, Router access only if rollout allows, DLP
               before any transport, trusted-check execution only via the
               isolated broker (#201-B2/B3), never inline shell.
publish.yml:   minimum GITHUB_TOKEN scope (pull-requests: write, contents: read).
```

### 6.1 Publisher preconditions  *(rev.2)*

Consuming a sanitized artifact is **necessary but not sufficient**. Before any
GitHub write:

```text
PR == OPEN
live_head == analyzed_head
artifact identity valid          (run/identity/digests coherent)
producer authorized
sanitized artifact valid
```

This mirrors the merge-gate discipline used for PRs in this repository: a HEAD
that moved between analysis and publication invalidates the evidence, and the
publisher must revalidate rather than assume.

None of the three templates ever gives PR-controlled code the harness,
serializer, authority decision, publisher, policy or trusted inventory.

---

## 7. Trusted-check ownership

The pack owns the **mechanism**; the target owns the **inventory**.

- `TrustedCheckInventoryV2` (additive schema) defines the shape of a target's
  `.aiops/trusted-checks.v2.yaml`: `check_name`, allowlisted `command` (argv
  list, never a shell string), `working_directory`, `timeout_seconds`,
  `resource_limits`. The pack ships it as a seed template the target edits.
- `validate`/`doctor` load it with strict parsing and cross-check against
  `TargetProfileV2.policies.required_checks` using the same bidirectional
  equality `validate_policy_against_profile_v2` already enforces — reused, not
  reimplemented.
- The pack NEVER invents a target-specific command (no hardcoded `pytest`, no
  hardcoded `mypy`) anywhere in `app/agent_review/*` or `templates/*`, enforced
  by an architecture test.
- Allowlisting a command is **containment**. It says nothing about whether that
  command's result reaches `authority=TRUSTED` in a given run.
  `containment != evidence != provenance != authority` is a hard vocabulary
  boundary.
- Per §1.1, the inventory consumed by privileged analysis comes from the trusted
  base checkout, never the PR branch.

---

## 8. Safe file ownership

```text
UPSTREAM_GENERATED  -- pack writes/overwrites, subject to drift check (§5.3)
TARGET_OWNED        -- pack writes ONCE at init, never touches again
MERGED_DECLARATIVE  -- pack writes/updates ONLY its own fenced block
```

Classification lives solely in `TargetPackManifestV2.generated_files[].ownership`
— one source of truth, never path-pattern matching scattered across commands,
and never inferred from what a given invocation happened to write.

---

## 9. Rollout modes

```text
OFF             -- pack installed, nothing runs.
SHADOW_MINIMAL  -- semantic review + informational publication only. No
                   trusted-check integration; a required-check claim under this
                   mode is refused.
SHADOW_FULL     -- semantic review + trusted-check integration wired through the
                   existing #201-C0/#201-C chain; ReviewReadinessV2 is real and
                   calculable. Still never becomes a required/default/primary
                   branch-protection check — that is a human action outside any
                   pack authority, always.
```

Two independent ceilings, both enforced:

- **requested vs delivered** *(rev.2)* — a request above
  `manifest.max_supported_rollout_mode` is refused before any mutation. A pack
  that ships no trusted-check integration cannot accept `shadow_full`.
- **requested vs resolved** — no install/upgrade writes a mode higher than the
  one explicitly requested. No silent ceiling promotion.

`doctor` additionally refuses a receipt whose recorded `rollout_mode` exceeds
the current manifest's capability (§4.1).

---

## 10. `#203` / `#204` / `#205` boundaries  *(rev.2)*

rev.1 left target-adoption criteria inside `#203` while `#204` existed precisely
to prove them, which would have made `#203` impossible to close without
duplicating its child. Reconciled:

```text
#203 — complete distributable target pack
       complete CLIs, templates
       synthetic/offline conformance
       no target-name branching
       reproducible install/upgrade/rollback behaviour

#204 — AgentEscala real migration
       InterLeitos real installation
       Class C execution
       CT104 canaries
       target-specific operational DLP/PHI proof

#205 — pinnable distribution/release
```

Target adoption is **not** a `#203` completion criterion.

---

## 11. Test strategy

Class A = real CLI subprocess E2E against a synthetic target root; Class B =
pure composition, no filesystem; Class C = **deferred to `#204`** (§10).

| Scenario | Class | Note |
|---|---|---|
| init in empty synthetic repo | A | |
| reinstall identical pack = idempotent, empty second diff | A/B | |
| generated-file drift refused | A | nothing written, for any path |
| target-owned modification preserved | A | |
| compatible / incompatible upgrade | A | deferred with `upgrade` |
| rollback restores previous set; refuses tampered/cross-target receipt | A | deferred with `rollback` |
| missing / invalid profile diagnosed without mutation | A | |
| missing secret NAMES diagnosed read-only | A | never reads a VALUE |
| Unicode / quoted / backslash paths | B | reuses `#202` fixtures, no new heuristic |
| two synthetic targets diverge correctly from one pack | A | |
| v1 coexistence untouched | A | |
| rollout: requested-above-capability refused | A | rev.2 |
| rollout: no silent ceiling promotion | A/B | |
| receipt determinism, timestamp excluded from hash | B | |
| **dirty toolrepo does not yield a clean-HEAD receipt** | A | rev.2, §3 |
| **untracked schema never enters `schema_digests`** | B | rev.2, §3 |
| **nonexistent target stays nonexistent on every refusal** | A | rev.2, §5.1 |
| **root swap between apply and receipt write refused** | A | rev.2, §5.4 |
| **doctor refuses receipt/manifest identity mismatch** | A/B | rev.2, §4.1 |
| multi-file interruption converges deterministically | A | rev.2, §5.5; owed by the multi-file slice |
| `doctor` call graph never writes | static | AST proof |
| no target-specific literal in generic pack code | static | AST/grep |
| no branch-protection-shaped call exists at all | static | design absence, not a guard |

---

## 12. Threat model

| ID | Threat | Control |
|---|---|---|
| P-T1 | ownership escape, target file silently overwritten | §5.3 drift detection; `TARGET_OWNED` never diffed |
| P-T2 | path traversal in target root or generated path | `contracts_v2.RelativePath` inherited, never re-implemented |
| P-T3 | symlink swap between plan and apply | resolve + containment re-check immediately before every write |
| P-T3b | **target root itself swapped after planning** | plan-time `target_root_real` cross-checked at apply (rev.2) |
| P-T3c | **write path bypassing the single writer** | every write, receipt included, routed through it (rev.2) |
| P-T4 | stale receipt reuse across rollback | §5.6 |
| P-T5 | forged manifest | manifest bound to the toolrepo release that built it |
| P-T6/7 | cross-target / cross-repo replay | receipt binds `target_repo`; symmetric to `#201-C0` run-identity binding |
| P-T8 | toolrepo pin mismatch | `required_capabilities`/`min_engine_contract_version` at doctor/install |
| P-T9 | upgrade/downgrade attack | `compatibility` + semver comparison |
| P-T10 | interrupted install / partial write | per-file atomic write; §5.5 convergence; receipt = commit marker |
| P-T11 | workflow generation nondeterminism | golden-file byte identity |
| P-T12 | YAML/script injection from target input | `yaml.safe_load` only; no interpolation of target data into shell |
| P-T13/14 | shell quoting / command injection | argv lists, `shell=False`, schema allowlist |
| P-T15 | secret VALUE persisted | name-shape validation + never reading an env VALUE |
| P-T16 | rollout ceiling bypass | §9's two independent ceilings |
| **P-T17** | **fabricated provenance: SHA not bound to installed bytes** | §3 Git-tree derivation (rev.2) |
| **P-T18** | **mutation before preflight completes** | §5.1 (rev.2) |
| **P-T19** | **receipt committed under a different root than the files** | §5.4 (rev.2) |
| **P-T20** | **PR-branch config reaching privileged analysis** | §1.1 (rev.2) |
| **P-T21** | **publish against a moved HEAD / closed PR** | §6.1 (rev.2) |

---

## 13. Definition of done for `#203`

```text
all seven CLI subcommands implemented and tested
pack material bound to an immutable Git tree              (§3)
no mutation before preflight completes                    (§5.1)
single mutation boundary, receipt inside it               (§5.4)
deterministic multi-file recovery                         (§5.5)
synthetic/offline conformance passing for >= 2 targets    (§10, §11)
no target-name branching anywhere in generic code
doctor mechanically read-only
rollout ceilings enforced in both directions              (§9)
frozen contracts and CAEM pin untouched
```

Explicitly **not** required for `#203` to close: real AgentEscala/InterLeitos
adoption, Class C execution, CT104 canaries, release/pinning — `#204`/`#205`.

---

## 14. Deferred (explicitly, not silently)

- `install-workflows` / `upgrade` / `rollback` and the real workflow templates —
  later `#203` slices. Slice 1 (S1) shipped `init` + `doctor`; slice 2 (S2)
  shipped `validate` + synthetic/offline `conformance`.
- `TrustedCheckInventoryV2` and the `#201-C0` trusted-check wiring, and with it
  any genuine `SHADOW_FULL` capability.

### 14.1 Which slice makes `§7`'s validation applicable  *(S2 clarification)*

`§7` describes the FINAL architecture, in which `validate`/`doctor` also
cross-check a target's trusted-check inventory against
`TargetProfileV2.policies.required_checks`. Read alone it can look like a
requirement already owed by `validate`. It is not, and this section says when
it becomes one — the end-state requirement in `§7` is unchanged, only its
applicability is made explicit:

```text
S2  (this slice)
    validate/doctor verify ONLY currently delivered capabilities:
      profile + manifest + receipt + identity + ownership + drift
      + rollout ceiling
    The trusted-check dimension is reported `unavailable` with a
    deferral reason code -- never `pass`, never omitted.

S3  introduces TrustedCheckInventoryV2 + its seed template +
    install-workflows/wiring. From that slice on, `§7`'s
    inventory <-> policies.required_checks cross-check becomes
    applicable to validate/doctor, and only then may the rollout
    ceiling rise above `off`.
```

The reason is structural, not scheduling convenience: the pack currently
generates exactly one file (`.aiops/target-profile.v2.yaml`), no inventory
contract exists in `app/` or `schemas/`, and the seed profile ships
`required_checks: [REPLACE_ME_WITH_A_REAL_REQUIRED_CHECK_NAME]`. A `validate`
that claimed to have checked an inventory under those conditions would be
asserting a capability with no producer behind it.
- Multi-file crash-convergence metamorphic test (§5.5) — owed by the first slice
  that writes more than one file.
- Class C / real dual-target adoption — `#204` (§10).
- Publication and pinning — `#205`.
- `#222` (quality-gate v2 CLI `--output` `OSError`) — a different CLI, not this
  pack's IO layer.
- Real GitHub Actions runner execution proof — CT104-scoped.
