# AgentReview v2 — `agentreview-v2-target-pack` (`#203`)

Refs `#203`, child of distribution epic `#199`. Depends on the core
reconciliation of `#200`/`#201`/`#202` (all `CORE_COMPLETE`, see the
reconciliation comments on those issues and on `#199`, posted after PR #220
merged).

## What this is

An installer for the AgentReview v2 engine into a **target** repository,
without forking the engine. `aiops-orchestrator` owns the engine, the
generic templates, and the pack compiler/installer/CLI. The target owns its
own profile, domain contracts, DLP extensions, and trusted-check inventory.

```text
#203 MAY INSTALL/CONFIGURE INTEGRATION.
#203 MUST NEVER CREATE AUTHORITY, FORK THE ENGINE, OR SILENTLY PROMOTE ROLLOUT.
```

Full design: `/root/.claude/plans/203-agentreview-v2-target-pack.md`
(Execution-Ready Engineering Specification rev.1) — ownership boundary,
contract shapes, CLI surface, install/drift/rollback semantics, rollout
modes, trusted-check ownership, and the full threat model.

## What is delivered in this first commit

Two of the specification's seven CLI subcommands, as a coherent, tested
slice — `validate`/`conformance`/`install-workflows`/`upgrade`/`rollback`
are deferred to a follow-up commit on the same branch/PR (spec `§12`):

```text
agent-review-target-pack-v2.py init    --target-root PATH --toolrepo-root PATH --target-repo OWNER/NAME --pack-version X.Y.Z [--rollout off|shadow_minimal|shadow_full]
agent-review-target-pack-v2.py doctor  --target-root PATH --toolrepo-root PATH --pack-version X.Y.Z
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

## Tests

62 new tests across 8 files: contract validation (manifest/receipt,
including path-traversal rejection reused from `contracts_v2.RelativePath`
and secret-name-shape rejection with two independent layers), pure plan
computation (all five `PlannedActionV2` cases, rollout-ceiling refusal),
install/apply (atomic-write-under-simulated-crash, drift refusal writes
NOTHING, target-owned files never touched, fenced-block merge preserves
surrounding content), doctor (missing/invalid/healthy, secret NAME-only
checking, read-only-under-CLI-subprocess), manifest building (from the
real toolrepo template tree, catching a real template-source-vs-target-path
bug during implementation), and CLI E2E subprocess tests (`init` idempotence
never overwriting a target-customized profile, `doctor` unhealthy-before/
healthy-after `init`, no traceback on bad input).

Combined suite: 1812 passed (1750 baseline + 62 new), 16 skipped, 2 failed
(pre-existing environment class, `sudo` absent in sandbox,
`test_isolated_executor_v2.py`, unrelated to `#203`). Schema export
byte-identical; CAEM F0 pin unchanged.

## Deferred (spec `§12`, not silently dropped)

- `validate`/`conformance`/`install-workflows`/`upgrade`/`rollback`
  subcommands.
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
