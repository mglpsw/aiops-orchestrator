# AgentReview v2 — `agentreview-v2-target-pack` (`#203`)

**Status:** `CURRENT | V2 DEVELOPMENT` — not GA, not default, not a required check.

Refs `#203`, child of distribution epic `#199`. Depends on the core
reconciliation of `#200`/`#201`/`#202` (all `CORE_COMPLETE`, see the
reconciliation comments on those issues and on `#199`, posted after PR #220
merged).

## Subcommand status

Merged to `master` across PR #223 (first slice), PR #228 (operation-plan
binding), PR #243 (`#203-C1`, installed-pack identity contracts) and PR #244
(`#203-C2`, bounded offline `validate`). Not yet in any published release.

**Compiled CURRENT state** (`app/agent_review/target_pack_current_state_v1.py`,
anchored at the SHA named in `docs/generated/target-pack-current-state.json`):

<!-- BEGIN GENERATED: target-pack-current.target-pack-doc.status -->Canonical on `master`: `doctor`, `init`, `validate`. Deferred: `conformance`, `install-workflows`, `rollback`, `upgrade`.<!-- END GENERATED: target-pack-current.target-pack-doc.status -->

Two capabilities are implemented but are not CLI subcommands, so they fall
outside the canonical/deferred split above and are not derivable from the
anchor's `argparse`: operation-plan binding (`target_pack_operation_v2.py` +
its own schema, PR #228) and the installed-pack identity contracts
(`Repository`/`RelativePath`, `#203-C1`, PR #243). Trusted-check inventory
integration into the `#201-C0` provenance chain is `PLANNED`, not yet
implemented.

Deferred means specified and intentionally not shipped — never silently dropped,
and never described as available.

## What this is

An installer for the AgentReview v2 engine into a **target** repository,
without forking the engine. `aiops-orchestrator` owns the engine, the
generic templates, and the pack compiler/installer/CLI. The target owns its
own profile, domain contracts, DLP extensions, and trusted-check inventory.

```text
#203 MAY INSTALL/CONFIGURE INTEGRATION.
#203 MUST NEVER CREATE AUTHORITY, FORK THE ENGINE, OR SILENTLY PROMOTE ROLLOUT.
```

Full design: `docs/checkpoints/AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md`
(Execution-Ready Engineering Specification **rev.2**, current — supersedes
rev.1 in full) — ownership boundary, contract shapes, CLI surface,
install/drift/rollback semantics, rollout modes, trusted-check ownership,
and the full threat model.

## What was delivered in the first slice (PR #223)

Two of the specification's seven CLI subcommands, as a coherent, tested
slice — `validate`/`conformance`/`install-workflows`/`upgrade`/`rollback`
were not part of this slice (see the compiled status above for their
current, now-later state). PR #228
subsequently bound `init` to an explicit, schema-backed operation plan. Without
`--apply`, `init` is write-zero: it prints the operation plan and exits.
Writing the profile/receipt requires a second, explicit invocation with
`--apply` and the previewed plan's hash. `--rollout` accepts `off`,
`shadow_minimal` and `shadow_full` at the CLI/argparse level, but **this pack
version's `max_supported_rollout_mode` is `off`** (`target_pack_build_v2.py`)
— any request for `shadow_minimal`/`shadow_full` is refused before preview or
apply; they exist as interface/future options only, not usable capability
today:

```text
agent-review-target-pack-v2.py init    --target-root PATH --toolrepo-root PATH --target-repo OWNER/NAME --pack-version X.Y.Z --rollout off   # only currently-supported value
agent-review-target-pack-v2.py init    --target-root PATH --toolrepo-root PATH --target-repo OWNER/NAME --pack-version X.Y.Z --apply --expected-plan-sha256 <hash previewed above>
agent-review-target-pack-v2.py doctor  --target-root PATH --toolrepo-root PATH --target-repo OWNER/NAME --pack-version X.Y.Z
```

### New contracts (additive, non-wire; zero existing schema touched)

- `app/agent_review/target_pack_manifest_v2.py` — `TargetPackManifestV2`:
  what one pack version can install, with every generated file classified
  by ownership (`UPSTREAM_GENERATED` / `TARGET_OWNED` /
  `MERGED_DECLARATIVE`).
- `app/agent_review/target_pack_receipt_v2.py` — `TargetInstallReceiptV2`:
  written to `.aiops/install-receipt.v2.json` in the target after a
  successful install. Self-validating hash (same pattern as
  `ChunkPayloadV2.payload_sha256`); `required_secret_names` carries NAMES
  ONLY, validated to reject value-shaped strings; `generated_at` is
  deliberately excluded from the canonical hash preimage.

### Pure logic

- `target_pack_plan_v2.py::compute_install_plan_v2` — the pure,
  three-case drift table (`WRITE_NEW`/`OVERWRITE_SAFE`/`REFUSE_DRIFT` for
  `UPSTREAM_GENERATED`; `WRITE_NEW`/`SKIP_TARGET_OWNED` for `TARGET_OWNED`;
  `MERGE_FENCED_BLOCK` always for `MERGED_DECLARATIVE`). Never touches the
  filesystem beyond reading current content to hash it.
- `target_pack_build_v2.py::build_target_pack_manifest_v2` — builds a
  manifest from this toolrepo's own `templates/agentreview-v2-target-pack/`
  tree. The single place template-source-path -> target-install-path
  mapping and ownership classification are assigned.

### The only writer

- `target_pack_install_v2.py::apply_install_plan_v2` — atomic
  (temp-file-then-`os.replace`) writes; refuses **everything**, writing
  nothing at all, if any drifted path isn't explicitly named in
  `force_overwrite_paths`.

### Read-only diagnostics

- `target_pack_doctor_v2.py::run_doctor_v2` — proven read-only by AST/
  call-graph inspection (`tests/agent_review/test_target_pack_arch_v2.py`),
  the same mechanical-proof discipline `#201-C` established for its own
  choke-point invariants. Checks secret NAME presence
  (`name in os.environ`) — never reads or reports a VALUE.

## What C2 added (`#203-C1`/PR #243 + `#203-C2`/PR #244)

`agent-review target validate` — a new CLI capability, structural successor
to a forensic prior attempt at this command (PR #242, closed via Structural
Change Preflight STOP/REDESIGN after three review rounds converged on the
same ad-hoc `tuple[str, X | None]` projection boundary). This is a rewrite
from canonical `master`, never a port of #242's production code.

**Contract surface, stated per PR rather than collapsed into one claim:**

- **PR #243 (C1)** changed `TargetInstallReceiptV2`'s field types and its
  validator, and regenerated the affected receipt and operation-plan
  schemas. It did **not** redefine `contracts_v2.Repository` and did
  **not** redefine `contracts_v2.RelativePath` — it bound the receipt's
  fields to those already-existing definitions.
- **PR #244 (C2)** changed no shared or public contract.

`#203-C2` delivers `validate` itself: target-only, offline, read-only. It
answers a narrower question than `doctor`: *of the relations for which this
command holds local, independent evidence, is any violated?* It does
**not** independently establish that an installed target corresponds to its
claimed upstream pack — `pack_version`/`toolrepo_sha`/`manifest_digest`
remain `unavailable` (disclosed, never fabricated as pass or fail) without
an upstream manifest/toolrepo.

<!-- BEGIN GENERATED: target-pack-current.target-pack-doc.inventory -->
**Check inventory (derived from the anchor, `d454e8f2d272b9edb011513b4a8f5d4e89ece4c2`):** 17 total dimensions, 11 locally evaluable when applicable, 6 permanently disclosed `unavailable`: `generated_file_set`, `previous_install_lineage`, `rollout_capability`, `target_owned_set`, `trusted_check_inventory`, `upstream_pack_identity`.
<!-- END GENERATED: target-pack-current.target-pack-doc.inventory -->

Qualified across three adversarial Codex review rounds on the branch, each
closed with `STOP_REDESIGN: false`.

<!-- BEGIN GENERATED: target-pack-current.target-pack-doc.evidence -->
**PR #244 qualification** — tested at `a792b23c3ed18eb4e87cd7adf0930b6c60214ae2`, canonicalized as `d454e8f2d272b9edb011513b4a8f5d4e89ece4c2` (tree_identical). Full suite: 2801 passed, 4 skipped (recorded_qualification; evidence: git_commit_message@`a792b23c3ed18eb4e87cd7adf0930b6c60214ae2`).
<!-- END GENERATED: target-pack-current.target-pack-doc.evidence -->

## Templates shipped

`templates/agentreview-v2-target-pack/target-profile.v2.yaml` — a
structurally valid `TargetProfileV2` seed (validated by its own test,
`test_target_pack_build_v2.py::test_the_shipped_profile_template_actually_
validates_as_a_target_profile`), installed to `.aiops/target-profile.v2.yaml`
in the target, `TARGET_OWNED` (written once at `init`, never touched again).

## Architecture proofs (mechanical, not docstring-only)

`tests/agent_review/test_target_pack_arch_v2.py`:

1. `run_doctor_v2`'s call graph contains no filesystem-mutating primitive
   (mutation-verified: reintroducing a stray `.mkdir()` call is caught).
2. `run_doctor_v2` accepts no write-shaped parameter.
3. No target-specific literal (`AgentEscala`, `InterLeitos`, `pytest`,
   `mypy`, ...) appears anywhere in the generic pack engine or shipped
   templates.
4. No branch-protection/required-check-promotion-shaped identifier is
   called anywhere in the pack engine — the capability is structurally
   absent, not merely unused.

## Test evidence

Every figure below names the PR whose qualification recorded it. None is
re-derived at read time, and none describes the current tree as a whole —
they are dated evidence bound to a subject, not a running total.

### First slice — PR #223 (historical)

Coverage areas, as historical narrative without a suite total pinned here
(a prior revision of this document stated `2497 passed`; the live PR #223
record itself carries a different final figure, so no count is restated as
if immutably bound to this document): contract validation (manifest/receipt,
including path-traversal rejection reused from `contracts_v2.RelativePath`
and secret-name-shape rejection with two independent layers), pure plan
computation (all five `PlannedActionV2` cases, rollout-ceiling refusal),
install/apply (atomic-write-under-simulated-crash, drift refusal writes
NOTHING, target-owned files never touched, fenced-block merge preserves
surrounding content, symlink/root-identity containment), doctor
(missing/invalid/healthy, receipt-identity cross-checked against the
manifest and loaded profile, secret NAME-only checking,
read-only-under-CLI-subprocess), manifest building (from the real toolrepo
template tree, catching a real template-source-vs-target-path bug during
implementation), and CLI E2E subprocess tests (`init` idempotence never
overwriting a target-customized profile, ownership-derived receipt fields
stable across reinstalls, rollout-capability refusal, `doctor`
unhealthy-before/healthy-after `init`, no traceback on bad input). Grown
across two adversarial-review passes (pre-PR self-review: symlink escape,
fabricated `toolrepo_sha`/`target_profile_hash`; post-PR external
exact-HEAD review: doctor identity binding, fabricated
`target_policy_hash`, ownership-derivation bug, missing
rollout-capability enforcement). For the exact suite figure PR #223 itself
recorded, see that PR's own body on the forge — not restated here.

### C2 qualification — PR #244 / compiled evidence

See the generated evidence block above (`## What C2 added`), which states
the figures `#244`'s own qualification recorded, bound to its exact tested
and canonical SHAs.

## Deferred (spec `§14`, not silently dropped)

- Subcommand lifecycle (which are canonical, which remain deferred) is
  normative in spec §4's compiled state, not re-enumerated here — see the
  compiled status above.
- `conformance`/`install-workflows`/`upgrade`/`rollback`.
- Workflow templates (`evidence.yml`/`analysis.yml`/`publish.yml`).
- Trusted-check inventory schema + integration into the `#201-C0`
  provenance chain.
- Rollout-mode enforcement end-to-end (the pure `validate_rollout_ceiling_
  v2` primitive exists and is tested; no CLI command calls it yet since
  `upgrade` — the only command that changes rollout mode — is deferred).
- Class C tests (real AgentEscala/InterLeitos live install) — target
  adoption, `#204`'s own charter.
- The round-11 `#201-C` adjacent CLI defect (`#222`) — confirmed not
  owned by this new IO layer (different CLI script entirely).
