# AgentReview v2 — `agentreview-v2-target-pack` (`#203`)

**Status:** `CURRENT | V2 DEVELOPMENT` — not GA, not default, not a required check.

Refs `#203`, child of distribution epic `#199`. Depends on the core
reconciliation of `#200`/`#201`/`#202` (all `CORE_COMPLETE`, see the
reconciliation comments on those issues and on `#199`, posted after PR #220
merged).

## Subcommand status

Merged to `master` across PR #223 (first slice) and PR #228 (operation-plan
binding). Not yet in any published release.

| Subcommand | Status | Notes |
|---|---|---|
| `init` | `IMPLEMENTED` | idempotent; never overwrites a target-customized profile — repeating `init` after a target edits `.aiops/target-profile.v2.yaml` requires naming the path via `--accept-target-owned` on both the preview and the `--apply` call, or `apply` fails with `target_owned_identity_acceptance_required`; the profile bytes themselves are left untouched. **This pack version's `max_supported_rollout_mode` is `off`** — `--rollout off` is the only value this slice accepts; `shadow_minimal`/`shadow_full` are interface/future options and are refused before preview or apply |
| `doctor` | `IMPLEMENTED` | target-read-only; one coherent K-SH observation epoch; typed completed/unknown runtime outcome |
| operation-plan binding | `IMPLEMENTED` | `target_pack_operation_v2.py` + its own schema (PR #228) |
| `validate` | `DEFERRED` | spec `§12` |
| `conformance` | `DEFERRED` | spec `§12`; target adoption is `#204`'s charter |
| `install-workflows` | `DEFERRED` | workflow templates not shipped |
| `upgrade` | `DEFERRED` | only command that changes rollout mode |
| `rollback` | `DEFERRED` | spec `§12` |
| trusted-check inventory integration | `PLANNED` | into the `#201-C0` provenance chain |

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
remain deferred (spec `§12`; see the status table above). PR #228
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

- `target_pack_apply_v2.py::apply_authorized_target_pack_init_v2` is the
  only orchestration entry point for `init --apply`.  It acquires the
  private K exclusive lease, rereads current target state, computes one
  locked operation plan and requires its existing `operation_plan_hash` to
  equal `--expected-plan-sha256` before it creates a target directory,
  writes a pack file, or writes a receipt.  Equality applies that same
  locked representation; mismatch is `target_pack_plan_stale` and is
  write-zero.
- `target_pack_epoch_v2.py` implements K as a private runtime carrier at
  `/tmp/agentreview-target-locks-v1-<euid>/`.  It is a length-framed SHA-256
  address over protocol version, effective UID, mount-namespace device/inode
  and the canonical target locator.  The protocol directory is held SH for
  namespace lifecycle integrity and `<K>.lock` is held SH/EX for the full
  reader/writer epoch.  Carriers are regular, empty, reusable files; neither
  a carrier nor the directory is automatically deleted.
- `target_pack_install_v2.py::{apply_install_plan_v2,write_receipt_v2}`
  consume the already-live exclusive capability and an `O_PATH` bound target
  directory.  They use FD-relative target operations while retaining
  `resolve_within_target_root_v2` as the sole semantic containment authority.
  This preserves legal mode-0300 targets and missing ancestor materialization.

The concrete claim is deliberately narrow: cooperative exclusion among
same-host, same-effective-UID, same-mount-namespace AgentReview participants
using this exact K protocol and a supported local Linux runtime filesystem.
It is not single-writer proof, a filesystem snapshot, cross-host/distributed
locking, crash recovery, a durable install generation, or protection from
uncooperative/external writers.  K is neither receipt/plan identity nor
authorization; `receipt_hash` remains a declaration identity only.  Journal,
recovery, application-record, and mutation-strength vocabulary remain outside
this implementation.

### Read-only diagnostics

- `target_pack_doctor_v2.py::run_doctor_v2` is **target-read-only** and
  consumes exactly one shared K epoch. K may create/reuse only its inert,
  external runtime carrier under `/tmp`; the doctor never creates, modifies,
  or removes the target root or a target artifact. This intentionally replaces
  #258 R39's predecessor assertion that neither doctor nor validate consumes K:
  doctor now consumes one K-SH epoch, while validate still consumes none.
- After K acquisition the doctor binds the target root with the additive,
  observation-only `O_PATH` API, retains `.aiops` as a role/object identity,
  and performs every material traversal root-FD-relative after the existing
  `resolve_within_target_root_v2` authority sanctions the logical path. The
  final lookup uses that same containment authority; failure to repeat it can
  only make the observation stale/unknown, never retroactively turn it into a
  completed-negative containment finding.
- One private registry keeps logical relation, sanctioned resolved path, and
  retained physical object distinct. A physical regular object is read once;
  profile bytes feed parsing, semantic hashing, and ledger hashing, while
  hardlink aliases retain independent path-specific conformance relations.
  Missing, non-regular, and unreadable observations are cached too. Every FD
  remains non-inheritable and retained through final revalidation. Initial,
  content, and final-relookup FDs enter the same raw-fork tracker before any
  fallible configuration; local release removes them from that tracker.
- Cleanup is total across the session's owned observations: failure closing
  one FD does not skip later release attempts or registry clearing. An
  operational cleanup failure yields report-zero `unknown`; a programmer
  errno is re-raised only after every release attempt. The lease remains the
  exception-safety backstop until K is released.
- The single provisional content open uses `O_NONBLOCK` and compares the
  returned descriptor's file type and identity with the prior `O_PATH`
  observation before reading. `O_NONBLOCK` is behavior-neutral for regular
  files; a raced FIFO, device, directory, symlink, or different regular inode
  is classified stale/unknown without a blocking read.
- A sanctioned resolved object may be the target root itself. Directory roles
  reuse the already-held root object; regular-file roles observe that the root
  is non-regular and preserve the relation's existing completed-negative
  reason. Final relookup supports the same case without weakening containment.
  Profile status is projected from exact named reasons, never from reason-code
  prefixes, and the retained object identity owns file-type stability.
- `DoctorReportV2` still represents only a completed diagnosis. The internal
  `DoctorRunOutcomeV2` is either a `DoctorDecisionV2` (`healthy`/`unhealthy`)
  carrying that unchanged report or a report-zero `DoctorUnknownV2` with
  reason/stage/relation. Stable invalid invocation subjects remain outside the
  union as `DoctorInputErrorV2`.
- CLI outcomes are: completed healthy = existing JSON/exit 0; completed
  unhealthy = existing JSON/exit 1; unknown = empty stdout, stable error on
  stderr, exit 3; absent/non-directory target = empty stdout, stable input
  error, exit 2. There is no `healthy: null` or synthetic failed report.
- The environment key set is captured once and secret checks use membership in
  that snapshot. No environment value is read or reported.

The completed-observation claim is cooperative only: same host, effective UID,
mount namespace, K object, and participating AgentReview readers/writers.
External writers and undetectable ABA are not excluded. Equal bytes do not
prove provenance or an install generation. The doctor evaluates one validated
receipt declaration identity against observed target state; the receipt is not
authority over that state. Manifest `TARGET_OWNED` entries define the read
domain, and `generated_file_hashes` are deliberately not a conformance relation
in this slice. Journal/recovery/application-record and mutation-strength
vocabulary remain owned by #253; validate's coherent-read successor is deferred.

## Templates shipped

`templates/agentreview-v2-target-pack/target-profile.v2.yaml` — a
structurally valid `TargetProfileV2` seed (validated by its own test,
`test_target_pack_build_v2.py::test_the_shipped_profile_template_actually_
validates_as_a_target_profile`), installed to `.aiops/target-profile.v2.yaml`
in the target, `TARGET_OWNED` (written once at `init`, never touched again).

## Architecture proofs (mechanical, not docstring-only)

`tests/agent_review/test_target_pack_arch_v2.py`:

1. `run_doctor_v2`'s registered transitive call graph contains no target
   filesystem-mutating primitive; the epoch module's external carrier writes
   are separately allowlisted by exact function/callsite (mutation-verified).
2. `run_doctor_v2` accepts no write-shaped parameter.
3. Doctor acquires exactly one literal shared epoch, never materializes a
   target, and validate still imports no epoch primitive.
4. Initial and final semantic lookup both call the single existing containment
   authority; content reads and environment capture each have one choke point.
5. Every doctor/epoch FD is explicitly non-inheritable, and the reachable
   helper registry fails closed when the observation graph changes.
6. No target-specific literal (`AgentEscala`, `InterLeitos`, `pytest`,
   `mypy`, ...) appears anywhere in the generic pack engine or shipped
   templates.
7. No branch-protection/required-check-promotion-shaped identifier is
   called anywhere in the pack engine — the capability is structurally
   absent, not merely unused.

## Tests

83 tests across 8 files: contract validation (manifest/receipt,
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
from an initial 62 to 83 across two adversarial-review passes (pre-PR
self-review: symlink escape, fabricated `toolrepo_sha`/
`target_profile_hash`; post-PR external exact-HEAD review: doctor identity
binding, fabricated `target_policy_hash`, ownership-derivation bug,
missing rollout-capability enforcement).

Combined suite (full `tests/`): 2497 passed, 16 skipped, 2 failed
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
