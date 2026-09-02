# AgentReview v2 -- #200-G4B Checkpoint: Centralized External-Path Ingress Authority

**Scope:** issue [#289](https://github.com/mglpsw/aiops-orchestrator/issues/289)
(`#200-G4B`, child of `#200`), narrow successor to `#282`/PR #283
(`STOP_G4_ARCHITECTURE_NOT_CONVERGING`). Issue #291 was reconciled as a
duplicate lineage into #289 (unique scope/provenance detail ported there,
#291 closed with no independent implementation authority). **PR:**
[#294](https://github.com/mglpsw/aiops-orchestrator/pull/294), Draft only.

**Branch:** `feat/200-g4b-external-path-ingress`.

**Load-bearing rule:** `authority != collection of try/except`.

## 1. State at the start of this slice

The branch already carried 6 commits (primitive + RED corpus + one
consumer): `external_path_ingress_v2.py` (three typed capabilities --
`ExternalInputFileV2`, `ExternalInputDirectoryV2`, `ExternalOutputPathV2`),
its adversarial RED corpus (`test_external_path_ingress_v2.py`), and
`profile_loader_v2.py`/`payload_references_v2.py` already migrated onto it.
The PR body named consumer migration and provenance enforcement as the next,
still-pending commits in the same Draft PR.

A prior, unpushed local pass (found in a sibling worktree on this host, not
on any remote) had already produced four further commits addressing most of
the remaining migration and the documented `resolve_within_target_root_v2`
`OSError` hole. That work was reviewed line-by-line, found sound, and
cherry-picked onto this branch rather than re-derived from scratch:

- `2b17f2c` -- routed `authoritative_check_policy_v2.load_authoritative_check_policy_v2`
  and `diff_acquisition_v2._run_git_v2` through the authority.
- `4ef64be` -- closed the `resolve_within_target_root_v2` `OSError` hole
  `target_pack_validate_v2._resolve_contained_path_v2`'s own docstring named
  as deferred, and its ripple into `target_pack_doctor_v2.run_doctor_v2`,
  `target_pack_plan_v2.compute_install_plan_v2`/`_read_on_disk_sha256_v2`,
  and `target_pack_operation_v2._read_target_owned_bytes_v2`.
- `59f25ae` -- RED/GREEN witnesses for the above.

This checkpoint's own new work is `ab7a473` (see §3).

## 2. Migration inventory (per issue #289/#291's own enumerated scope)

| Consumer | Status before this slice | Status after this slice |
|---|---|---|
| `--profile` (`profile_loader_v2`) | migrated (prior 6 commits) | unchanged |
| payload artifact/contract references (`payload_references_v2`) | migrated | unchanged |
| authoritative-check policy (`authoritative_check_policy_v2:344`) | unmigrated | migrated (cherry-picked) |
| diff/review input checkout root (`diff_acquisition_v2:814`, `_run_git_v2`) | unmigrated | migrated (cherry-picked) |
| `resolve_within_target_root_v2` `OSError` hole (`target_pack_plan_v2.py:150-154`) | open, documented | closed (cherry-picked) |
| `target_pack_doctor_v2.run_doctor_v2`'s `target_root` | unguarded for `RuntimeError`/`OSError` | migrated (cherry-picked) |
| `target_pack_plan_v2.compute_install_plan_v2`'s `target_root` | unguarded | local guard, both exceptions typed (documented carve-out; existence may legitimately be absent -- neither capability models that) |
| `target_pack_operation_v2._read_target_owned_bytes_v2` / `target_pack_plan_v2._read_on_disk_sha256_v2` | unguarded `OSError` on an already-contained path | guarded locally (out of authority's scope by design -- already-contained, see `external_path_ingress_v2.py`'s own docstring) |
| `agent-review-target-pack-v2.py` `init`'s `receipt_path` | raw `is_file()` outside the read's try/except | migrated onto the authority |
| `aiops-review-quality-gate-v2.py` -- every `--decision`/`--identity`/`--evaluated-identity`/`--findings`/`--checks`/`--checks-provenance`/`--checks-snapshot`/`--run-origin`/`--payload`/`--response` | raw `Path.read_text()`/`read_bytes()` | migrated onto the authority (`_read_external_text_v2`) |
| `aiops-review-quality-gate-v2.py` `_check_no_output_input_collision` | unguarded `.resolve()` | local guard, `OSError`/`RuntimeError`/`ValueError` typed (documented carve-out -- collision detection, not content interpretation; `--output`'s parent may not exist yet) |
| `aiops-review-quality-gate-v2.py` final `--output` write | **entirely unguarded**, outside `main()`'s try/except | guarded (new gap found and closed this slice, not named in the issue text) |
| `aiops-review-build-payload-set-v2.py` `--manifest` | raw `Path.read_text()` | migrated onto the authority |
| `aiops-review-build-payload-set-v2.py` `--payloads-dir` ("responses directory") + each discovered file | raw `.glob()` + `Path.read_text()` | migrated onto `ExternalInputDirectoryV2.iter_input_files()` |
| `aiops-review-build-payload-set-v2.py` final `--output` write | unguarded | guarded (new gap, closed this slice) |
| `aiops-acquire-authoritative-checks-v2.py` `--observations` | raw `Path.read_bytes()` | migrated onto the authority |
| `aiops-acquire-authoritative-checks-v2.py` `_check_no_output_input_collision`/`_resolve_git_metadata_dir` | missing `RuntimeError` in the outer catch | broadened (local carve-out, same rationale as quality-gate's collision check) |
| `aiops-acquire-authoritative-checks-v2.py` final `--output` write | unguarded | guarded (new gap, closed this slice) |
| `aiops-acquire-authoritative-checks-v2.py` `--git-dir` (`_observe_parents`) | delegated to `git -C <dir>` subprocess, not raw pathlib | **not migrated** -- git's own subprocess error handling already fails closed via the existing `(OSError, subprocess.SubprocessError)` catch; no raw pathlib call on this path in our own code, so there is nothing for the authority to intercept |

**Explicitly out of scope, confirmed unchanged:** engine-derived/internal
paths; AgentReview temp dirs; `ControlledTargetSubject`/G1-derived subject
paths; all of v1; benchmark/tooling scripts
(`run-agent-review-v2-evals.py`, `generate-benchmark-*.py`,
`materialize-benchmark-case.py`, `export-agent-review-v2-schemas.py`);
`target_pack_build_v2.py`'s toolrepo-SHA resolution (git-subprocess-based,
no raw pathlib interpretation); cosmetic `Path(...)` cleanup.

## 3. This slice's own work (commit `ab7a473`)

1. **Migrated the four in-scope v2 CLI scripts** onto the authority (table
   above), matching the pattern already established by the cherry-picked
   commits.
2. **Closed a real gap the issue text did not name**: every one of the four
   CLI scripts' final `--output` write (`mkdir(parents=True)` +
   `write_text`) sat entirely outside `main()`'s try/except. A
   permission-denied parent, a full disk, or an overlong `--output` path
   crashed raw, *after* every other caller-controlled path failure in the
   same CLI had already been converted to a typed refusal. Guarded
   uniformly across all four scripts.
3. **Regression found and fixed during this slice**:
   `test_init_refuses_when_the_receipt_write_would_escape_target_root_via_a_symlink`
   (an existing Round-5 adversarial test) failed after the first version of
   the `init` receipt-path migration, because routing the pre-check through
   `validate_external_input_file_v2` made *containment* (checked before
   existence) fail before *existence*, surfacing a generic
   `target_pack_cli_previous_receipt_invalid` instead of letting
   `compute_target_pack_operation_plan_v2` -- the real, single authority for
   target-owned/containment decisions -- produce its specific
   `path_escapes_target_root`. Fixed by folding
   `EXTERNAL_PATH_ESCAPES_ROOT_REASON_V2` into the same "no previous
   receipt, proceed" bucket as missing/wrong-type, restoring the original
   division of authority (this pre-check answers "is there a *contained*
   receipt to load", not "is `.aiops` escaping `target_root`" -- that
   question belongs downstream, exactly where the test's own docstring
   already says the escape is caught).
4. **Documented, not silently chosen, local carve-outs** where a capability
   genuinely does not fit the shape of the caller's need (`ExternalOutputPathV2`
   models exclusive-create against a pre-existing parent; several CLI
   `--output` flags legitimately `mkdir -p` their own parent and overwrite
   on rerun; `compute_install_plan_v2`'s `target_root` may legitimately not
   exist yet, unlike either input capability). Each carve-out converts every
   failure shape the authority itself would have converted (`OSError`,
   `RuntimeError`, and where relevant `ValueError`) to a typed, path-free
   refusal at that same local site, rather than leaving any of them raw.
5. **Provenance enforcement, decided explicitly**: a full cross-module
   AST/callgraph proof that "no raw caller path reaches a filesystem call
   outside the authority" was not attempted, per the issue's own explicit
   instruction not to ship an undiscriminating proof. The existing
   precedent in this repository (`test_target_pack_arch_v2.py`'s
   single-adapter check for `target_pack_validate_v2.py`) is already scoped
   to one file; this slice makes the same choice for the G4B migration
   surface specifically, in a new file,
   `tests/agent_review/test_g4b_external_path_provenance_v2.py`:
   - mechanically asserts `is_file`/`is_dir`/`iterdir`/`glob` do not appear
     anywhere in the eight migrated files (every legitimate use is now a
     capability method, never a raw `Path` probe);
   - mechanically asserts every `read_text`/`read_bytes` call's receiver is
     not itself a fresh `Path(...)` constructor call (the literal shape of
     every pre-migration defect).

   This is the capability-typed-parameter fallback made mechanical, not a
   claim that no other indirection could smuggle a raw path to a read. The
   fallback decision is documented in the module docstring of that test
   file itself, not just here.
6. **RED/GREEN witnesses added** for every newly migrated consumer
   (symlink loop, wrong-type/missing, and the output-write gap), including
   for the two consumers the prior unpushed pass migrated but never covered
   with tests (`authoritative_check_policy_v2`, `diff_acquisition_v2`).
7. **`external_path_ingress_v2` added to `test_authority_error_surfaces_v2.py`'s
   `_CLOSED_AUTHORITY_MODULES`**, per the issue's explicit mandate.

## 4. Mutation testing (performed against `ab7a473`, tree restored clean after each)

| Mutation | Expected RED | Result |
|---|---|---|
| `_resolve_v2` (`external_path_ingress_v2.py`): drop `RuntimeError` from its `except` tuple | `test_symlink_loop_is_resolution_refusal_not_traceback` fails, raw `RuntimeError` escapes | confirmed -- reproduced the exact #283-class recurrence |
| `authoritative_check_policy_v2.load_authoritative_check_policy_v2`: revert to the pre-migration raw `is_file()`/`read_bytes()` | `test_policy_path_symlink_loop_is_typed_not_raw` fails; `test_g4b_external_path_provenance_v2.py`'s `is_file`/`is_dir` absence check fails on this file | confirmed on both -- the mechanical provenance check independently caught the same regression the targeted RED test caught, at the file-scan level |
| Injected `assert False` at the top of `_stat_v2` (`external_path_ingress_v2.py`), simulating a genuine post-seal programmer defect | `AssertionError` propagates raw through every layer (capability -> CLI helper -> `main()`), never caught or relabelled as a typed refusal | confirmed -- reproduced via the quality-gate CLI test harness; the exception surfaced unmodified at the first frame inside `_stat_v2` |

All three mutations were reverted with `git checkout -- <file>` and the
affected suites re-run GREEN before proceeding.

## 5. Test results at `ab7a473`

`tests/agent_review/` in full: 2672 passed, 12 skipped, 2 failed --
`test_isolated_executor_v2.py::test_execute_denies_sudo_inside_the_isolated_check`
and `::test_sudo_path_resolves_to_an_absolute_path_via_a_fixed_search_list`,
both failing on `SUDO_PATH_V2 is None` -- an environment-class failure
(no `sudo` binary resolvable in this sandbox), reproduced identically at
the pre-existing base (`69c752f`) before any change in this slice, per this
repository's own `Sudo tests fail locally, pass in CI` precedent. Not a
regression; not reclassified as green.

`test_target_pack_arch_v2.py` and `test_authority_error_surfaces_v2.py`:
pass at `ab7a473`, including the newly added `external_path_ingress_v2`
entry in the latter's closed-authority conformance matrix.

## 6. Not done in this slice / explicitly deferred

- `--git-dir` in `aiops-acquire-authoritative-checks-v2.py` (see table, §2)
  was evaluated and found not to need migration: it never reaches a raw
  pathlib filesystem call in this repository's own code, only a `git -C`
  subprocess invocation whose own failure modes are already typed by the
  existing `(OSError, subprocess.SubprocessError)` catch.
- `target_pack_build_v2.py`'s `_resolve_toolrepo_sha` (`--toolrepo-root`)
  is git-subprocess-based, matching the same shape; not touched, and not
  named in the issue's own enumerated scope.

## 7. Review round 1 (against `c1abc24`)

Two independent adversarial review lanes dispatched via the `Agent` tool
against `c1abc24c89e3f789ed6ef3677d1f9fdb0641c225`. Both found real, distinct
P0s in the `doctor` subcommand -- the same subcommand, different layers of
the same call chain, both independently reproduced by the author before
fixing:

- **Lane B P0**: `target_pack_doctor_v2._check_profile_v2`'s `resolved.
  is_file()` and `_check_receipt_v2`'s `receipt_path.is_file()` were raw,
  unguarded filesystem operations on already-contained paths -- every OTHER
  probe in the same module already wrapped this pair; these two were
  missed. Not caught by `test_g4b_external_path_provenance_v2.py` because
  `target_pack_doctor_v2.py` was correctly out of that check's scope (it
  operates on already-contained, not raw caller, paths -- the gap is a
  plain missing-`try/except` defect, not a provenance violation).
  Reproduced via targeted `Path.is_file` monkeypatch at the exact resolved
  path; fixed by wrapping both in `try/except OSError`, folding into the
  same reason codes each function's own existing downstream read failure
  already used.
- **Lane A P0**: `run_doctor_v2` correctly converts a bad `target_root`
  into a typed `NotADirectoryError` (an earlier, correct commit in this
  slice) -- but `_cmd_doctor` and `main()`'s dispatcher in
  `scripts/agent-review-target-pack-v2.py` never caught that type, so it
  propagated as a raw traceback through the real CLI subprocess. The
  existing RED/GREEN test for this exact shape
  (`test_doctor_refuses_a_target_root_symlink_loop_instead_of_crashing`)
  only ever called `run_doctor_v2` in-process, so it proved the library fix
  without ever exercising the CLI dispatch boundary built on top of it --
  reproduced with a plain nonexistent `--target-root` (no symlink
  required) via the real CLI subprocess; fixed by wrapping `_cmd_doctor`'s
  call to `run_doctor_v2` locally and catching `NotADirectoryError`,
  matching `_cmd_init`'s established local-catch pattern (`validate` needs
  no equivalent catch -- `run_validate_v2` is documented as total over its
  own failure domain).

Both findings independently reproduced by the author (monkeypatch for
Lane B; direct CLI subprocess invocation for Lane A) before any fix was
written. Fixed in commit `50dce03`, with regression coverage added at BOTH
layers: unit-level monkeypatch tests in `test_target_pack_doctor_v2.py`
(matching that file's own established pattern) for the `is_file()` gap, and
NEW subprocess-level tests in `test_agent_review_target_pack_v2_cli.py` for
the dispatch-layer gap -- the exact blind spot that let the second P0
through undetected the first time. All three fixes mutation-tested
(mutate -> confirm RED -> `git checkout --` restore -> confirm GREEN)
before commit.

Two P2s also reported (Lane A), deliberately deferred rather than fixed in
this bounded correction round:

- `target_pack_apply_v2._load_previous_receipt_under_epoch_v2` (the real
  `--apply` receipt read) still uses raw pathlib via the older
  `resolve_within_target_root_v2` rather than the new `external_path_
  ingress_v2` authority. Confirmed functionally safe today -- its own local
  `except (OSError, RuntimeError, ValueError, ValidationError, PlanError)`
  is already complete -- but it is a second, undeclared "collection of
  try/except" on a real write-time path, with a reason code
  (`TARGET_PACK_PREVIOUS_RECEIPT_INVALID_REASON_V2`) that does not match
  the preview path's for the identical escape case. Not touched this round:
  it sits on the `--apply` epoch-locked mutation path, where the risk of an
  unforced architectural-consistency change outweighs the benefit inside a
  bounded correction round whose job is closing the two reported P0s.
  Left as an explicit follow-up, not silently dropped.
- The provenance AST guard (`test_g4b_external_path_provenance_v2.py`) is
  defeated by a trivial `p = Path(raw); p.read_text()` assign-then-read
  split (the receiver becomes an `ast.Name`, not an `ast.Call`). Confirmed
  not currently exploited anywhere in the eight migrated files. The test
  file's own module docstring already discloses this as the accepted
  narrowness of the decidable-approximation choice; no action taken, per
  Lane A's own assessment that none is required.

## 8. Review round 2 (against `6144ba4`)

Two FRESH independent adversarial review lanes dispatched against
`6144ba4cdd3793a8d82ec873060c5ae24217706b`, specifically targeting whether
round 1's class of defect (a fix verified at one layer but not the
adjacent one) recurs.

- **Lane A**: both round-1 P0s confirmed genuinely fixed -- reproduced
  broken against the pre-correction tree, confirmed fixed against
  `6144ba4`, both via unit-level and real CLI subprocess reproduction.
  Exhaustively traced every `raise` reachable from `run_doctor_v2`/
  `_cmd_doctor`/`_cmd_init`/`_cmd_validate` and their transitive callees:
  `run_doctor_v2` now has exactly one remaining raise (`NotADirectoryError`),
  and it is caught; no sibling gap found in `init`/`validate`. One new
  finding, P1: `resolve_within_target_root_v2` and two sites in `target_
  pack_epoch_v2.py` were missing `ValueError` in their `.resolve()` guard
  (see below).
- **Lane B**: fully clean. Independently defeated both new regression tests
  (revert each fix -> confirmed RED; restore -> confirmed GREEN) to verify
  they are structurally complete, not coincidentally passing. Two P2s,
  both documentation-only (a stale test-name citation in a docstring, and a
  self-reported-count discrepancy in an earlier, unrelated commit message
  that does not affect any claim made at this frozen head).

No P0s from either lane in round 2.

### The one P1: `resolve_within_target_root_v2`'s missing `ValueError` guard

Independently reproduced by the author (not just accepted from the review
report): `resolve_within_target_root_v2(root, root / "ab\x00cd")` raised a
raw `ValueError` ("embedded null byte") before this fix. Independently
verified the claimed non-exploitability too, not just the defect: every
relative-path caller across `target_pack_install_v2.py`, `target_pack_
apply_v2.py`, `target_pack_operation_v2.py`, `target_pack_plan_v2.py`,
`target_pack_doctor_v2.py`, and `target_pack_validate_v2.py` is either a
fixed module constant (`RECEIPT_RELATIVE_PATH_V2`, `DEFAULT_TARGET_PROFILE_
RELATIVE_PATH`) or a value that passed `contracts_v2.RelativePath`
validation first (`_validate_normalized_relative_posix` rejects any
`ord(character) < 32`, including NUL, at the pydantic layer, before the
string ever reaches this function) -- confirmed by reading that validator
directly, not by taking the claim on faith. `target_pack_install_v2.py`'s
own call site (`_canonical_relative_write_path_v2`) additionally already
wraps its own call in a local `except (OSError, RuntimeError, ValueError,
PlanError)`, independent of this authority's own completeness. Root-level
callers (`target_root` itself) are `sys.argv`-sourced, which cannot carry
an embedded NUL at all.

Fixed anyway (commit `8acb477`) rather than left as a documented follow-up:
the change is small, mirrors an already-established pattern
(`external_path_ingress_v2._resolve_v2`'s `(OSError, RuntimeError,
ValueError)`) exactly, and three drifted `except` clauses across two
modules is itself a minor instance of the property this whole PR exists to
close -- a shared containment primitive whose guard is not (yet) uniform
with the newer authority it predates. Mutation-tested the same way as
every other fix in this slice (revert -> confirm RED -> restore -> confirm
GREEN).

## 9. Terminal verdict

**`PRIMITIVE_NON_REFUTED`.**

Two full review rounds (four independent lanes total) against two
successive frozen heads found: round 1 -- two P0s, both real, both
independently reproduced and fixed, both closed with regression coverage
added at the specific layer each one lived at; round 2 -- zero P0s, one
P1 (independently reproduced, confirmed non-exploitable by two independent
methods, fixed anyway), two documentation-only P2s. No recurrence of the
same defect CLASS after correction -- the bar this slice's own terminal-
state rule sets for declaring `STOP_G4B_ARCHITECTURE_NOT_CONVERGING`
instead. The centralized external-path ingress authority, its consumer
migration across the full in-scope surface (four v2 CLI scripts, four v2
library loaders, and the target-pack module family), and its provenance
enforcement (the decidable, file-scoped AST approximation, per the issue's
own explicit instruction not to ship an undiscriminating cross-module
proof) stand as implemented at the final head below.

Final head: `8acb477` (branch `feat/200-g4b-external-path-ingress`, PR
#294, still Draft). CI green. Full suite: 2706 passed, 12 skipped, 2
pre-existing environment-class failures (no `sudo` resolvable in this
sandbox), unchanged across every commit in this slice.

Not authorized and not attempted under this slice's grant: marking Ready,
merging, tagging/releasing, deploying, modifying CI workflow files, calling
a live Router or real LLM provider, mutating AgentEscala/InterLeitos/CAEM
repos, closing #200, modifying #273, building G5/any operational composer.
