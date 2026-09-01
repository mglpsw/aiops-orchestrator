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

## 7. Review status

Two independent adversarial review lanes dispatched via the `Agent` tool
against the frozen head this checkpoint describes (see PR #294 / commit
history for the exact SHA and CI status at dispatch time). Findings and
disposition to be appended here once both lanes report.
