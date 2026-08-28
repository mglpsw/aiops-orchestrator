codex
# Lane C verdict: BLOCK

I reproduced findings at P0, P1, P2, and P3. No suspected findings.

Initial diff: `15 files changed, 5555 insertions(+), 30 deletions(-)`. The six changed test files currently report:

```text
168 passed, 2 skipped in 7.99s
```

## CONFIRMED

### P0 — Lazy object fetching restores target-controlled execution and persistently mutates the target

Questions: 7, 10, 12.

Files:

- [_sealed_git_execution_v2.py:159](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:159) constructs the sealed environment but does not set `GIT_NO_LAZY_FETCH=1`.
- [diff_acquisition_v2.py:952](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:952) runs `git worktree add`.
- [diff_acquisition_v2.py:1022](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1022) runs the authoritative diff.

I made a local partial clone whose repository-local config contained:

```text
remote.origin.promisor=true
remote.origin.partialclonefilter=blob:none
remote.origin.url=ext::/tmp/ar274-review-r3/laneC/q7/lazy_remote_helper_full.sh
protocol.ext.allow=always
```

The helper only wrote a marker and delegated to a local `git-upload-pack`; no network was used.

Reproduction:

```bash
export TMPDIR=/tmp/ar274-review-r3/laneC/q7
export GIT_OPTIONAL_LOCKS=0
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/opt/agent-tools/ar-200d-successor

/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
  /tmp/ar274-review-r3/laneC/q7/make_full_cli_partial_fixture.py

git clone --quiet --filter=blob:none \
  file:///tmp/ar274-review-r3/laneC/q7/full-cli/blackbox-target-repo \
  /tmp/ar274-review-r3/laneC/q7/full-cli/partial

git -C /tmp/ar274-review-r3/laneC/q7/full-cli/partial \
  config protocol.ext.allow always
git -C /tmp/ar274-review-r3/laneC/q7/full-cli/partial \
  config remote.origin.url \
  ext::/tmp/ar274-review-r3/laneC/q7/lazy_remote_helper_full.sh
```

I then ran the real `scripts/aiops-review-run-v2.py` with offline responses. It completed successfully:

```text
EXIT=0
STDERR_BYTES=0
READINESS_STATE=manual_required
REASON_CODES=['policy_failure']
HELPER_EXECUTIONS=1
MARKER=2026-08-28T05:22:00Z
STATUS_EMPTY=true
MISSING_COUNT=0
```

A new promisor pack remained in the reviewed repository’s `.git/objects/pack`, while `git status` remained empty. Complete evidence is in [full_cli_success_summary.log](/tmp/ar274-review-r3/laneC/q7/full_cli_success_summary.log) and [full_cli_stdout.json](/tmp/ar274-review-r3/laneC/q7/full_cli_stdout.json).

Causal control with `GIT_NO_LAZY_FETCH=1`:

```text
error: diff_unreadable
MARKER_ABSENT
missing blob remains
no new pack
```

Why it matters: a successful operational run can execute an attacker-selected command and persistently alter the reviewed repository. Removing `--output` does not establish target non-mutation.

This directly contradicts:

- [checkpoint:535](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:535), especially its exhaustive execution-surface claim.
- [checkpoint:655](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:655), `git_semantic_execution_closure: true`.
- [CHANGELOG.md:17](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:17), “no target mutation”.
- [CHANGELOG.md:149](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:149), the claimed remaining config-driven execution surface.

### P1 — Local Git configuration can silently remove an entire submodule update

Questions: 10, 12.

Files:

- [diff_acquisition_v2.py:1022](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1022) does not pin `--ignore-submodules=none` or context size.
- [diff_acquisition_v2.py:1391](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1391) similarly leaves raw-diff submodule semantics configurable.
- [_sealed_git_execution_v2.py:226](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:226) pins only four config values.

Reproduction:

```bash
env TMPDIR=/tmp/ar274-review-r3/laneC \
  GIT_OPTIONAL_LOCKS=0 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/opt/agent-tools/ar-200d-successor \
  /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python -B \
  /tmp/ar274-review-r3/laneC/q10/repro_ignore_submodules.py
```

Output for a real gitlink update:

```text
DEFAULT_AUTHORITATIVE=(ParsedFileDiffV2(...is_submodule=True...),)
IGNORED_DIFF=''
IGNORED_RAW=''
IGNORED_AUTHORITATIVE=()
FORCED_NONE_DIFF_EQUALS_DEFAULT=True
FORCED_NONE_RAW_EQUALS_DEFAULT=True
```

Setting only repository-local `diff.ignoreSubmodules=all` made both streams empty, so correlation accepted the omission. Adding `--ignore-submodules=none` to both commands restored the default output byte-for-byte.

I also reproduced identity instability from `diff.context=0`:

```text
OUTPUTS_EQUAL=False
DEFAULT_HUNK=@@ -1,7 +1,7 @@
CONTEXT0_HUNK=@@ -4 +4 @@ line 3
AUTHORITATIVE_EQUAL=False
DEFAULT_PARSED_HUNK=...diff_sha256='fc340a72...'
CONTEXT0_PARSED_HUNK=...diff_sha256='c93d30ce...'
FORCED_U3_EQUALS_DEFAULT=True
```

Reproducer: [repro_diff_context.py](/tmp/ar274-review-r3/laneC/q10/repro_diff_context.py).

Why it matters: attacker-controlled local config can hide a material change entirely or alter review identity for the same base/head pair. This falsifies [checkpoint:622](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:622) and [CHANGELOG.md:94](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:94).

### P1 — The target-mutation tests miss actual target mutations

Questions: 7, 8, 10, 12.

Files:

- [test_operational_run_blackbox_e2e_v2.py:227](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_blackbox_e2e_v2.py:227)
- [test_operational_run_v2.py:336](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:336)

Both oracles ultimately rely on committed-tree identity plus `git status`. Neither observes `.git` contents.

I inserted a mutant that writes `.git/agent-review-mutant-marker` from `prepare_operational_review_v2`. These tests still passed:

```text
test_target_checkout_is_never_mutated
test_cli_has_no_filesystem_output_authority
2 passed in 2.43s

test_cli_process_reaches_honest_readiness_from_a_separate_target_repo
1 passed in 1.32s
```

Mutant: [target-git-write](/tmp/ar274-review-r3/laneC/main/mutants/target-git-write).

A second mutant wrote `.git/info/exclude` and an excluded working-tree file:

```text
test_target_checkout_is_never_mutated
1 passed in 1.00s
```

The real lazy-fetch exploit above likewise persisted a pack while the oracle remained empty.

Why it matters: the test advertised as proving “never mutate” passes the exact production defect it should catch. Consequently [checkpoint:626](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:626) and [CHANGELOG.md:78](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:78) are false.

### P2 — Removing `--output` does not give the process “no filesystem-output authority”

Questions: 7, 8, 10.

Files:

- [aiops-review-run-v2.py:24](/opt/agent-tools/ar-200d-successor/scripts/aiops-review-run-v2.py:24)
- [diff_acquisition_v2.py:948](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:948)
- [reference_source_v2.py:203](/opt/agent-tools/ar-200d-successor/app/agent_review/reference_source_v2.py:203)

Both temporary roots use ambient `tempfile` placement. When `TMPDIR` points inside the target, acquisition creates the disposable worktree there:

```text
BEFORE=''
DISPOSABLE=.../agent-review-diff-attr-source-v2-.../wt
INSIDE='?? agent-review-diff-attr-source-v2-.../wt/\n'
AFTER=''
```

Reproducer: [probe_temp_root_inside_target.py](/tmp/ar274-review-r3/laneC/q7/probe_temp_root_inside_target.py).

Killing the acquiring process made that mutation persistent:

```bash
export TMPDIR=/tmp/ar274-review-r3/laneC
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python -B \
  /tmp/ar274-review-r3/laneC/q10/repro_sigkill_tmpdir.py
```

Output:

```text
INSIDE_TARGET=True
EXISTS_BEFORE_KILL=True
CHILD_RETURN=-9
EXISTS_AFTER_KILL=True
?? .review-tmp/agent-review-diff-attr-source-v2-dxn9tg_n/wt/
```

The linked worktree also remained registered.

The strongest true version of the Q7 claim is:

> The CLI accepts no `--output` destination for its final readiness artifact and writes that artifact to its inherited stdout. With a trusted temporary root outside the subject, a complete non-promisor object database, normal cleanup, and no caller-directed stdout redirection into the subject, the tested ordinary fixtures showed no persistent working-tree content change visible to `git status`.

It cannot truthfully claim “no filesystem-output authority”, “no target mutation”, or byte-identical target state.

### P2 — Full-disk allocation escapes the documented typed error surface

Questions: 10, 12.

Files:

- [diff_acquisition_v2.py:948](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:948)
- [reference_source_v2.py:203](/opt/agent-tools/ar-200d-successor/app/agent_review/reference_source_v2.py:203)
- [operational_run_v2.py:288](/opt/agent-tools/ar-200d-successor/app/agent_review/operational_run_v2.py:288)

Reproduction, injecting exact `ENOSPC` at the two `tempfile.mkdtemp` calls:

```bash
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python -B \
  /tmp/ar274-review-r3/laneC/q10/repro_full_disk_surface.py
```

Output:

```text
DIFF_EXCEPTION=OSError:[Errno 28] No space left on device
REFERENCE_EXCEPTION=OSError:[Errno 28] No space left on device
```

I did not fill a real filesystem; the syscall result was fault-injected at the exact allocation sites.

Why it matters: raw `OSError` escapes instead of the `DiffAcquisitionError` and `ReferenceSourceError` promised by [checkpoint:234](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:234).

## Question 8 — New-test mutant audit

I identified 80 newly added test functions. Every runnable function received a claim-specific mutant; the following tests passed mutants that removed the property they claim to establish. Mutants were applied only in scratch repository copies.

| Severity | Test | Mutant and reproduced result |
|---|---|---|
| P2 | [blackbox:245](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_blackbox_e2e_v2.py:245) | Removed `--ignored=matching`; `1 passed in 1.09s`. The new untracked `.gitignore` itself changes status, so the ignored leak need never be observed. |
| P2 | [blackbox:279](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_blackbox_e2e_v2.py:279), [operational:299](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:299), [operational:379](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:379) | Replaced all bound results with `bound_results = ()`; `3 passed in 2.16s`. `manual_required/policy_failure` does not prove responses were consumed. |
| P2 | [operational:701](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:701) | Bypassed receipt verification and directly minted `_VerifiedRouterResultV2`; `1 passed in 0.96s`. |
| P2 | [reference:179](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_reference_source_v2.py:179) | Moved materialization before `try/finally`; `1 passed in 0.42s`, with an actual leaked `agent-review-reference-source-v2-*` directory. |
| P2 | [diff:1851](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_diff_acquisition_v2.py:1851), [diff:1870](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_diff_acquisition_v2.py:1870) | Forced Git cwd back to the target instead of the attribute worktree. These two passed; discriminating attribute tests failed. Overall output: `2 failed, 3 passed in 1.52s`. |
| P3 | [blackbox:354](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_blackbox_e2e_v2.py:354) | Added `--result-path` and wrote there while still rejecting `--output`; `1 passed in 2.19s`. The test proves only the spelling `--output` is absent. |
| P3 | [operational:671](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:671) | Caught `ValidationError` through an alias; structural selector passed, `1 passed in 0.51s`. The separate behavioral post-seal test did kill this mutant. |
| P3 | [operational:692](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:692) | Renamed the exception variable and used `getattr(error, "reason_code")`; `1 passed in 0.56s`. |
| P3 | [reference:341](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_reference_source_v2.py:341) | Imported `subprocess.run` under an alias and ran `git rev-parse HEAD`; `1 passed in 0.42s`. |
| P3 | [toolrepo:264](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_toolrepo_identity_v2.py:264) | Reintroduced the old `.exists()` prefilter; selector passed. Its fixture is a modified tracked file, not an index deletion followed by an untracked same-spelling replacement. Overall related output: `1 failed, 2 passed in 0.39s`. |

The six tests left to the final mutation pass produced three additional survivors above (`--result-path`, aliased catch, renamed exception variable) and three valid kills:

- [operational:623](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:623) rejected a post-seal `ValidationError` catch.
- [toolrepo:316](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_toolrepo_identity_v2.py:316) rejected a gitless fallback.
- [toolrepo:327](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_toolrepo_identity_v2.py:327) rejected removal of the required second-order-honesty text.

This directly falsifies the “all executed and killed” statement at [checkpoint:586](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:586), including M10.

Could not test:

- `test_sealed_argv_admits_a_foreign_owned_checkout`
- `test_sealed_argv_safe_directory_does_not_admit_other_repositories`

Both skipped because this sandbox cannot change ownership:

```text
cannot change ownership in this environment
2 skipped
```

## Question 10 — Documentation overclaims

Confirmed overclaims:

- [CHANGELOG.md:17](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:17): “no target mutation” — contradicted by lazy-fetch pack writes and worktree administration.
- [CHANGELOG.md:78](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:78): “target tree byte-identical” — the oracle does not compare bytes and cannot see `.git` or modification of existing ignored files.
- [CHANGELOG.md:94](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:94): Git semantics closed for every subprocess — contradicted by `diff.ignoreSubmodules`, `diff.context`, and lazy fetch.
- [CHANGELOG.md:149](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:149): remaining execution surface enumerated — omits promisor lazy-fetch transports.
- [CHANGELOG.md:161](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:161): stdout-only plus corrected oracle establishes target safety — contradicted above.
- [checkpoint:586](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:586): all mutants killed — directly false.
- [checkpoint:622](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:622): `git_interpretation_environment_bound: true` — false.
- [checkpoint:626](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:626): `target_mutation_oracle_complete: true` — false.
- [checkpoint:655](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:655): `git_semantic_execution_closure: true` — false.
- [checkpoint:662](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:662): `cli_no_filesystem_output_authority: true` — false.

## SUSPECTED

None.

## Question 12 — Blocking assessment

I would block this PR on:

1. The P0 target-controlled `ext::` execution and persistent object-store mutation.
2. The P1 `diff.ignoreSubmodules=all` fail-open omission.
3. The mutation oracle’s inability to detect the P0 defect it claims to exclude.
4. The qualification/checkpoint claims remaining asserted despite reproduced counterexamples.

Final subject integrity:

```text
HEAD=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
git status --porcelain=v1 --untracked-files=all: empty
```

The subject worktree was not modified.
diff --git a/main/acquire_existing_repo.py b/main/acquire_existing_repo.py
new file mode 100644
index 0000000000000000000000000000000000000000..ed8939cd349b3d112eb03ff0bd2b9fe6bf8df37d
--- /dev/null
+++ b/main/acquire_existing_repo.py
@@ -0,0 +1,26 @@
+from __future__ import annotations
+
+import os
+import subprocess
+from pathlib import Path
+
+from app.agent_review.diff_acquisition_v2 import acquire_diff_v2
+
+
+repo = Path(os.environ["TRACE_TARGET_ROOT"])
+
+
+def git(*args: str) -> str:
+    return subprocess.run(
+        ["/usr/bin/git", *args], cwd=repo, check=True, capture_output=True, text=True
+    ).stdout.strip()
+
+
+commits = git("rev-list", "--reverse", "HEAD").splitlines()
+before = git("status", "--porcelain=v1", "--untracked-files=all")
+result = acquire_diff_v2(repo, base_sha=commits[-2], head_sha=commits[-1])
+after = git("status", "--porcelain=v1", "--untracked-files=all")
+print(f"TMPDIR={os.environ.get('TMPDIR')}")
+print(f"diff_has_hunk={'@@' in result}")
+print(f"status_before={before!r}")
+print(f"status_after={after!r}")
diff --git a/main/bin/git b/main/bin/git
new file mode 100644
index 0000000000000000000000000000000000000000..eb79f2f201f9ccd6a7b1dc6493baeeffced16e27
--- /dev/null
+++ b/main/bin/git
@@ -0,0 +1,29 @@
+#!/bin/sh
+set -eu
+
+is_worktree_add=0
+previous=
+for argument in "$@"; do
+    if [ "$previous" = "worktree" ] && [ "$argument" = "add" ]; then
+        is_worktree_add=1
+        break
+    fi
+    previous=$argument
+done
+
+/usr/bin/git "$@"
+status=$?
+
+if [ "$is_worktree_add" -eq 1 ] && [ "$status" -eq 0 ]; then
+    {
+        echo "WORKTREE_ADD_RETURNED"
+        echo "cwd=$PWD"
+        printf 'argv='
+        printf '%s ' "$@"
+        printf '\n'
+        find "${TRACE_TARGET_GITDIR}/worktrees" -maxdepth 3 -printf '%y %P\n' 2>&1 || true
+        find "${TRACE_TARGET_ROOT}" -maxdepth 2 -name 'agent-review-*' -printf 'target_path=%y %p\n' 2>&1 || true
+    } >> "${TRACE_LOG}"
+fi
+
+exit "$status"
diff --git a/main/mutants/cli-alt-output/scripts/aiops-review-run-v2.py b/main/mutants/cli-alt-output/scripts/aiops-review-run-v2.py
index e4fa9df50a14c29627cdcb3c06967f14a053c36c..513676eae71b67c95a6fee03fccc4bb45e697533
--- a/main/mutants/cli-alt-output/scripts/aiops-review-run-v2.py
+++ b/main/mutants/cli-alt-output/scripts/aiops-review-run-v2.py
@@ -133,6 +133,7 @@
     parser.add_argument("--offline-responses-dir", help="offline mode: transport envelope directory")
     parser.add_argument("--router-base-url", help="router mode: Agent Router base URL")
     parser.add_argument("--router-model", help="router mode: logical review preset")
+    parser.add_argument("--result-path", help="optional filesystem destination for the readiness JSON")
     return parser.parse_args(argv)
 
 
@@ -267,12 +268,13 @@
     # A caller redirects stdout itself; this process never receives or
     # interprets a destination path, so it structurally cannot write into
     # the target checkout it just reviewed.
-    print(
-        json.dumps(
-            outcome.review.readiness.model_dump(mode="json"),
-            ensure_ascii=False, indent=2, sort_keys=True,
-        )
+    rendered = json.dumps(
+        outcome.review.readiness.model_dump(mode="json"),
+        ensure_ascii=False, indent=2, sort_keys=True,
     )
+    if args.result_path:
+        Path(args.result_path).write_text(rendered, encoding="utf-8")
+    print(rendered)
     return 0
 
 
diff --git a/main/mutants/op-ast-alias/app/agent_review/operational_run_v2.py b/main/mutants/op-ast-alias/app/agent_review/operational_run_v2.py
index bfaecd0265ce40238dba11b6851ed8b171a9dab7..8eceb0772434b6fa1966c666e50f2f81a19ab627
--- a/main/mutants/op-ast-alias/app/agent_review/operational_run_v2.py
+++ b/main/mutants/op-ast-alias/app/agent_review/operational_run_v2.py
@@ -117,6 +117,8 @@
 from pathlib import Path
 from typing import Mapping, Sequence
 
+from pydantic import ValidationError as _PostSealValidationError
+
 from app.agent_review.authoritative_check_policy_v2 import AuthoritativeCheckPolicyErrorV2
 from app.agent_review.contracts_v2 import (
     ChunkPayloadV2,
@@ -276,8 +278,8 @@
 
     try:
         profile = load_target_profile_v2(target_profile_root)
-    except TargetProfileLoadErrorV2 as exc:
-        raise OperationalRunError(exc.reason_code) from exc
+    except TargetProfileLoadErrorV2 as error:
+        raise OperationalRunError(getattr(error, "reason_code")) from error
 
     # The policy must be usable against THIS profile, not merely well-formed.
     try:
@@ -315,6 +317,8 @@
         )
     except RunAssemblyError as exc:
         raise OperationalRunError(exc.reason_code) from exc
+    except _PostSealValidationError as error:
+        raise OperationalRunError("run_assembly_identity_invalid") from error
 
     if outcome.state != "assembled" or outcome.manifest is None:
         # `blocked_reason` is an `AssemblyBlockedReasonV2` DATACLASS carrying
diff --git a/main/mutants/oracle/tests/agent_review/test_operational_run_blackbox_e2e_v2.py b/main/mutants/oracle/tests/agent_review/test_operational_run_blackbox_e2e_v2.py
index 70235a97bfaa26e6bc001316491dbd499cc21399..b7e4925d3df44b6c0bde7ee896900810b7714a96
--- a/main/mutants/oracle/tests/agent_review/test_operational_run_blackbox_e2e_v2.py
+++ b/main/mutants/oracle/tests/agent_review/test_operational_run_blackbox_e2e_v2.py
@@ -236,7 +236,7 @@
     though it is fully present on disk."""
 
     result = subprocess.run(
-        ["git", "status", "--porcelain=v1", "-z", "-uall", "--ignored=matching"],
+        ["git", "status", "--porcelain=v1", "-z", "-uall"],
         cwd=repo, capture_output=True, text=True, check=True,
     )
     return result.stdout
diff --git a/main/mutants/q8-last/app/agent_review/toolrepo_identity_v2.py b/main/mutants/q8-last/app/agent_review/toolrepo_identity_v2.py
index 92c0cc335a61900edd45d4dddb1057b219bcecaf..2da5cf2baa1c42de4e7eec9bea3e32a87ca66c19
--- a/main/mutants/q8-last/app/agent_review/toolrepo_identity_v2.py
+++ b/main/mutants/q8-last/app/agent_review/toolrepo_identity_v2.py
@@ -106,9 +106,8 @@
 ## Second-order honesty (recorded, not hidden)
 
 This module is itself code that was imported and is running before it has
-verified anything. The claim it supports is *"review execution was blocked
-before semantic review/transport"* -- never *"zero unverified code
-execution"*. Some code necessarily ran to perform the proof; the invariant
+verified anything. The claim it supports is only that the review stopped.
+Some code necessarily ran to perform the proof; the invariant
 above bounds what runs AFTER that point, not before.
 
 ## Gitless toolrepo distribution -- out of scope for this slice
@@ -296,6 +295,10 @@
         raise ToolrepoIdentityError(TOOLREPO_IDENTITY_MISMATCH_REASON_V2)
 
     toolrepo_root = resolve_toolrepo_root_v2(executing_script=executing_script)
+    if not (toolrepo_root / ".git").exists():
+        return ToolrepoSourceIdentityV2(
+            toolrepo_root=toolrepo_root, toolrepo_sha=declared_toolrepo_sha
+        )
     observed_head = _resolve_toolrepo_head_v2(toolrepo_root)
     if observed_head != declared_toolrepo_sha:
         raise ToolrepoIdentityError(TOOLREPO_IDENTITY_MISMATCH_REASON_V2)
diff --git a/main/mutants/raw-no-binding/app/agent_review/diff_acquisition_v2.py b/main/mutants/raw-no-binding/app/agent_review/diff_acquisition_v2.py
index 0597ea6f65d5e31fb9256380fc0dd4df556f54f2..794027a8c904eacd8d11ee16c216c756e2f84792
--- a/main/mutants/raw-no-binding/app/agent_review/diff_acquisition_v2.py
+++ b/main/mutants/raw-no-binding/app/agent_review/diff_acquisition_v2.py
@@ -1037,7 +1037,7 @@
                 *_RENAME_COPY_DETECTION_ARGS_V2, f"{base_sha}...{head_sha}",
             ],
             repo_root=repo_root,
-            cwd=attr_source_worktree,
+            cwd=repo_root,
         )
     if result.returncode != 0:
         raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
@@ -1395,7 +1395,7 @@
                 *_RENAME_COPY_DETECTION_ARGS_V2, f"{base_sha}...{head_sha}",
             ],
             repo_root=repo_root,
-            cwd=attr_source_worktree,
+            cwd=repo_root,
         )
     if result.returncode != 0:
         raise DiffAcquisitionError(DIFF_UNREADABLE_REASON_V2)
diff --git a/main/mutants/ref-checkout-head/app/agent_review/reference_source_v2.py b/main/mutants/ref-checkout-head/app/agent_review/reference_source_v2.py
index bc0fc87ef0e789276f01e84b536bbc4438f081f1..30c0cc6bca73dbec6a948792203aa08955b51797
--- a/main/mutants/ref-checkout-head/app/agent_review/reference_source_v2.py
+++ b/main/mutants/ref-checkout-head/app/agent_review/reference_source_v2.py
@@ -101,6 +101,7 @@
 from contextlib import contextmanager
 from dataclasses import dataclass
 from pathlib import Path
+from subprocess import run as _resolve_current_checkout_head
 from typing import Iterator
 
 from app.agent_review.contracts_v2 import TargetProfileV2
@@ -143,6 +144,13 @@
 
 
 def _materialize_v2(*, repo_root: Path, head_sha: str, profile: TargetProfileV2, root: Path) -> None:
+    head_sha = _resolve_current_checkout_head(
+        ["git", "rev-parse", "HEAD"],
+        cwd=repo_root,
+        capture_output=True,
+        text=True,
+        check=False,
+    ).stdout.strip()
     for relative_path in _declared_reference_paths_v2(profile):
         try:
             entry = read_head_tree_entry_v2(repo_root, head_sha=head_sha, relative_path=relative_path)
diff --git a/main/mutants/ref-typed-leak/app/agent_review/reference_source_v2.py b/main/mutants/ref-typed-leak/app/agent_review/reference_source_v2.py
index bc0fc87ef0e789276f01e84b536bbc4438f081f1..f39bc33d8b5a1a5aafc8d2b65b51bbe60d3e553c
--- a/main/mutants/ref-typed-leak/app/agent_review/reference_source_v2.py
+++ b/main/mutants/ref-typed-leak/app/agent_review/reference_source_v2.py
@@ -201,8 +201,8 @@
     """
 
     tmp_dir = Path(tempfile.mkdtemp(prefix=_PRIVATE_ROOT_PREFIX_V2))
+    _materialize_v2(repo_root=repo_root, head_sha=head_sha, profile=profile, root=tmp_dir)
     try:
-        _materialize_v2(repo_root=repo_root, head_sha=head_sha, profile=profile, root=tmp_dir)
         yield ReferenceSourceV2(root=tmp_dir)
     finally:
         shutil.rmtree(tmp_dir, ignore_errors=True)
diff --git a/main/mutants/target-git-write/app/agent_review/operational_run_v2.py b/main/mutants/target-git-write/app/agent_review/operational_run_v2.py
index bfaecd0265ce40238dba11b6851ed8b171a9dab7..c3fbcadfb1273c040769b9c032e4bbc44a329cf0
--- a/main/mutants/target-git-write/app/agent_review/operational_run_v2.py
+++ b/main/mutants/target-git-write/app/agent_review/operational_run_v2.py
@@ -273,6 +273,9 @@
 
     repo_root = Path(repo_root)
     target_profile_root = Path(target_profile_root)
+    (repo_root / ".git" / "agent-review-mutant-marker").write_text(
+        "persistent target-repository mutation\n", encoding="utf-8"
+    )
 
     try:
         profile = load_target_profile_v2(target_profile_root)
diff --git a/main/observe_target_writes.py b/main/observe_target_writes.py
new file mode 100644
index 0000000000000000000000000000000000000000..20d1f12e38c23958023bb7ec5aafa0335793b94c
--- /dev/null
+++ b/main/observe_target_writes.py
@@ -0,0 +1,57 @@
+from __future__ import annotations
+
+import os
+import subprocess
+from pathlib import Path
+
+from app.agent_review.diff_acquisition_v2 import acquire_diff_v2
+
+
+scratch = Path("/tmp/ar274-review-r3/laneC/main/fixtures/q7-target")
+scratch.mkdir(parents=True, exist_ok=True)
+repo = scratch / "repo"
+repo.mkdir()
+
+
+def git(*args: str) -> str:
+    return subprocess.run(
+        ["/usr/bin/git", *args],
+        cwd=repo,
+        check=True,
+        capture_output=True,
+        text=True,
+    ).stdout.strip()
+
+
+git("init", "--quiet", "-b", "main", ".")
+git("config", "user.email", "review@example.invalid")
+git("config", "user.name", "review")
+(repo / "ordinary.txt").write_text("base\n", encoding="utf-8")
+git("add", "ordinary.txt")
+git("commit", "--quiet", "-m", "base")
+base = git("rev-parse", "HEAD")
+(repo / "ordinary.txt").write_text("head\n", encoding="utf-8")
+git("add", "ordinary.txt")
+git("commit", "--quiet", "-m", "head")
+head = git("rev-parse", "HEAD")
+
+status_before = git("status", "--porcelain=v1", "--untracked-files=all")
+worktrees_before = sorted((repo / ".git" / "worktrees").rglob("*")) if (repo / ".git" / "worktrees").exists() else []
+
+os.environ["TRACE_TARGET_GITDIR"] = str(repo / ".git")
+os.environ["TRACE_LOG"] = "/tmp/ar274-review-r3/laneC/main/logs/worktree-write.log"
+os.environ["PATH"] = "/tmp/ar274-review-r3/laneC/main/bin:" + os.environ["PATH"]
+
+diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)
+
+status_after = git("status", "--porcelain=v1", "--untracked-files=all")
+worktrees_after = sorted((repo / ".git" / "worktrees").rglob("*")) if (repo / ".git" / "worktrees").exists() else []
+
+print(f"base={base}")
+print(f"head={head}")
+print(f"diff_has_hunk={'@@' in diff}")
+print(f"status_before={status_before!r}")
+print(f"status_after={status_after!r}")
+print(f"worktree_admin_paths_before={[str(path.relative_to(repo)) for path in worktrees_before]}")
+print(f"worktree_admin_paths_after={[str(path.relative_to(repo)) for path in worktrees_after]}")
+print(Path(os.environ["TRACE_LOG"]).read_text(encoding="utf-8"))
diff --git a/main/repro_ignore_submodules.py b/main/repro_ignore_submodules.py
new file mode 100644
index 0000000000000000000000000000000000000000..1cbcdba94f5d4097591ec1606dc71b07e60ab1e7
--- /dev/null
+++ b/main/repro_ignore_submodules.py
@@ -0,0 +1,53 @@
+from __future__ import annotations
+
+import subprocess
+from pathlib import Path
+
+from app.agent_review.diff_acquisition_v2 import acquire_authoritative_diff_v2, acquire_diff_v2, acquire_raw_diff_v2
+
+
+repo = Path("/tmp/ar274-review-r3/laneC/main/fixtures/ignore-submodules-2")
+repo.mkdir(parents=True, exist_ok=True)
+
+
+def git(*args: str) -> str:
+    return subprocess.run(
+        ["/usr/bin/git", *args], cwd=repo, check=True, capture_output=True, text=True
+    ).stdout.strip()
+
+
+git("init", "--quiet", "-b", "main", ".")
+git("config", "user.email", "review@example.invalid")
+git("config", "user.name", "review")
+(repo / "seed.txt").write_text("one\n", encoding="utf-8")
+git("add", "seed.txt")
+git("commit", "--quiet", "-m", "seed one")
+sub_old = git("rev-parse", "HEAD")
+(repo / "seed.txt").write_text("two\n", encoding="utf-8")
+git("add", "seed.txt")
+git("commit", "--quiet", "-m", "seed two")
+sub_new = git("rev-parse", "HEAD")
+git("update-index", "--add", "--cacheinfo", f"160000,{sub_old},submodule")
+git("commit", "--quiet", "-m", "base gitlink")
+base = git("rev-parse", "HEAD")
+git("update-index", "--cacheinfo", f"160000,{sub_new},submodule")
+git("commit", "--quiet", "-m", "update gitlink")
+head = git("rev-parse", "HEAD")
+
+subprocess.run(["/usr/bin/git", "config", "--unset-all", "diff.ignoreSubmodules"], cwd=repo, check=False)
+default_text = acquire_diff_v2(repo, base_sha=base, head_sha=head)
+default_raw = acquire_raw_diff_v2(repo, base_sha=base, head_sha=head)
+default_authoritative = acquire_authoritative_diff_v2(repo, base_sha=base, head_sha=head)
+git("config", "diff.ignoreSubmodules", "all")
+hidden_text = acquire_diff_v2(repo, base_sha=base, head_sha=head)
+hidden_raw = acquire_raw_diff_v2(repo, base_sha=base, head_sha=head)
+hidden_authoritative = acquire_authoritative_diff_v2(repo, base_sha=base, head_sha=head)
+
+print(f"BASE={base}")
+print(f"HEAD={head}")
+print(f"DEFAULT_DIFF={default_text!r}")
+print(f"DEFAULT_RAW={default_raw!r}")
+print(f"DEFAULT_AUTHORITATIVE_COUNT={len(default_authoritative)}")
+print(f"HIDDEN_DIFF={hidden_text!r}")
+print(f"HIDDEN_RAW={hidden_raw!r}")
+print(f"HIDDEN_AUTHORITATIVE_COUNT={len(hidden_authoritative)}")

tokens used
401,660
# Lane C verdict: BLOCK

I reproduced findings at P0, P1, P2, and P3. No suspected findings.

Initial diff: `15 files changed, 5555 insertions(+), 30 deletions(-)`. The six changed test files currently report:

```text
168 passed, 2 skipped in 7.99s
```

## CONFIRMED

### P0 — Lazy object fetching restores target-controlled execution and persistently mutates the target

Questions: 7, 10, 12.

Files:

- [_sealed_git_execution_v2.py:159](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:159) constructs the sealed environment but does not set `GIT_NO_LAZY_FETCH=1`.
- [diff_acquisition_v2.py:952](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:952) runs `git worktree add`.
- [diff_acquisition_v2.py:1022](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1022) runs the authoritative diff.

I made a local partial clone whose repository-local config contained:

```text
remote.origin.promisor=true
remote.origin.partialclonefilter=blob:none
remote.origin.url=ext::/tmp/ar274-review-r3/laneC/q7/lazy_remote_helper_full.sh
protocol.ext.allow=always
```

The helper only wrote a marker and delegated to a local `git-upload-pack`; no network was used.

Reproduction:

```bash
export TMPDIR=/tmp/ar274-review-r3/laneC/q7
export GIT_OPTIONAL_LOCKS=0
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/opt/agent-tools/ar-200d-successor

/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
  /tmp/ar274-review-r3/laneC/q7/make_full_cli_partial_fixture.py

git clone --quiet --filter=blob:none \
  file:///tmp/ar274-review-r3/laneC/q7/full-cli/blackbox-target-repo \
  /tmp/ar274-review-r3/laneC/q7/full-cli/partial

git -C /tmp/ar274-review-r3/laneC/q7/full-cli/partial \
  config protocol.ext.allow always
git -C /tmp/ar274-review-r3/laneC/q7/full-cli/partial \
  config remote.origin.url \
  ext::/tmp/ar274-review-r3/laneC/q7/lazy_remote_helper_full.sh
```

I then ran the real `scripts/aiops-review-run-v2.py` with offline responses. It completed successfully:

```text
EXIT=0
STDERR_BYTES=0
READINESS_STATE=manual_required
REASON_CODES=['policy_failure']
HELPER_EXECUTIONS=1
MARKER=2026-08-28T05:22:00Z
STATUS_EMPTY=true
MISSING_COUNT=0
```

A new promisor pack remained in the reviewed repository’s `.git/objects/pack`, while `git status` remained empty. Complete evidence is in [full_cli_success_summary.log](/tmp/ar274-review-r3/laneC/q7/full_cli_success_summary.log) and [full_cli_stdout.json](/tmp/ar274-review-r3/laneC/q7/full_cli_stdout.json).

Causal control with `GIT_NO_LAZY_FETCH=1`:

```text
error: diff_unreadable
MARKER_ABSENT
missing blob remains
no new pack
```

Why it matters: a successful operational run can execute an attacker-selected command and persistently alter the reviewed repository. Removing `--output` does not establish target non-mutation.

This directly contradicts:

- [checkpoint:535](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:535), especially its exhaustive execution-surface claim.
- [checkpoint:655](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:655), `git_semantic_execution_closure: true`.
- [CHANGELOG.md:17](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:17), “no target mutation”.
- [CHANGELOG.md:149](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:149), the claimed remaining config-driven execution surface.

### P1 — Local Git configuration can silently remove an entire submodule update

Questions: 10, 12.

Files:

- [diff_acquisition_v2.py:1022](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1022) does not pin `--ignore-submodules=none` or context size.
- [diff_acquisition_v2.py:1391](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1391) similarly leaves raw-diff submodule semantics configurable.
- [_sealed_git_execution_v2.py:226](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:226) pins only four config values.

Reproduction:

```bash
env TMPDIR=/tmp/ar274-review-r3/laneC \
  GIT_OPTIONAL_LOCKS=0 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/opt/agent-tools/ar-200d-successor \
  /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python -B \
  /tmp/ar274-review-r3/laneC/q10/repro_ignore_submodules.py
```

Output for a real gitlink update:

```text
DEFAULT_AUTHORITATIVE=(ParsedFileDiffV2(...is_submodule=True...),)
IGNORED_DIFF=''
IGNORED_RAW=''
IGNORED_AUTHORITATIVE=()
FORCED_NONE_DIFF_EQUALS_DEFAULT=True
FORCED_NONE_RAW_EQUALS_DEFAULT=True
```

Setting only repository-local `diff.ignoreSubmodules=all` made both streams empty, so correlation accepted the omission. Adding `--ignore-submodules=none` to both commands restored the default output byte-for-byte.

I also reproduced identity instability from `diff.context=0`:

```text
OUTPUTS_EQUAL=False
DEFAULT_HUNK=@@ -1,7 +1,7 @@
CONTEXT0_HUNK=@@ -4 +4 @@ line 3
AUTHORITATIVE_EQUAL=False
DEFAULT_PARSED_HUNK=...diff_sha256='fc340a72...'
CONTEXT0_PARSED_HUNK=...diff_sha256='c93d30ce...'
FORCED_U3_EQUALS_DEFAULT=True
```

Reproducer: [repro_diff_context.py](/tmp/ar274-review-r3/laneC/q10/repro_diff_context.py).

Why it matters: attacker-controlled local config can hide a material change entirely or alter review identity for the same base/head pair. This falsifies [checkpoint:622](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:622) and [CHANGELOG.md:94](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:94).

### P1 — The target-mutation tests miss actual target mutations

Questions: 7, 8, 10, 12.

Files:

- [test_operational_run_blackbox_e2e_v2.py:227](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_blackbox_e2e_v2.py:227)
- [test_operational_run_v2.py:336](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:336)

Both oracles ultimately rely on committed-tree identity plus `git status`. Neither observes `.git` contents.

I inserted a mutant that writes `.git/agent-review-mutant-marker` from `prepare_operational_review_v2`. These tests still passed:

```text
test_target_checkout_is_never_mutated
test_cli_has_no_filesystem_output_authority
2 passed in 2.43s

test_cli_process_reaches_honest_readiness_from_a_separate_target_repo
1 passed in 1.32s
```

Mutant: [target-git-write](/tmp/ar274-review-r3/laneC/main/mutants/target-git-write).

A second mutant wrote `.git/info/exclude` and an excluded working-tree file:

```text
test_target_checkout_is_never_mutated
1 passed in 1.00s
```

The real lazy-fetch exploit above likewise persisted a pack while the oracle remained empty.

Why it matters: the test advertised as proving “never mutate” passes the exact production defect it should catch. Consequently [checkpoint:626](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:626) and [CHANGELOG.md:78](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:78) are false.

### P2 — Removing `--output` does not give the process “no filesystem-output authority”

Questions: 7, 8, 10.

Files:

- [aiops-review-run-v2.py:24](/opt/agent-tools/ar-200d-successor/scripts/aiops-review-run-v2.py:24)
- [diff_acquisition_v2.py:948](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:948)
- [reference_source_v2.py:203](/opt/agent-tools/ar-200d-successor/app/agent_review/reference_source_v2.py:203)

Both temporary roots use ambient `tempfile` placement. When `TMPDIR` points inside the target, acquisition creates the disposable worktree there:

```text
BEFORE=''
DISPOSABLE=.../agent-review-diff-attr-source-v2-.../wt
INSIDE='?? agent-review-diff-attr-source-v2-.../wt/\n'
AFTER=''
```

Reproducer: [probe_temp_root_inside_target.py](/tmp/ar274-review-r3/laneC/q7/probe_temp_root_inside_target.py).

Killing the acquiring process made that mutation persistent:

```bash
export TMPDIR=/tmp/ar274-review-r3/laneC
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python -B \
  /tmp/ar274-review-r3/laneC/q10/repro_sigkill_tmpdir.py
```

Output:

```text
INSIDE_TARGET=True
EXISTS_BEFORE_KILL=True
CHILD_RETURN=-9
EXISTS_AFTER_KILL=True
?? .review-tmp/agent-review-diff-attr-source-v2-dxn9tg_n/wt/
```

The linked worktree also remained registered.

The strongest true version of the Q7 claim is:

> The CLI accepts no `--output` destination for its final readiness artifact and writes that artifact to its inherited stdout. With a trusted temporary root outside the subject, a complete non-promisor object database, normal cleanup, and no caller-directed stdout redirection into the subject, the tested ordinary fixtures showed no persistent working-tree content change visible to `git status`.

It cannot truthfully claim “no filesystem-output authority”, “no target mutation”, or byte-identical target state.

### P2 — Full-disk allocation escapes the documented typed error surface

Questions: 10, 12.

Files:

- [diff_acquisition_v2.py:948](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:948)
- [reference_source_v2.py:203](/opt/agent-tools/ar-200d-successor/app/agent_review/reference_source_v2.py:203)
- [operational_run_v2.py:288](/opt/agent-tools/ar-200d-successor/app/agent_review/operational_run_v2.py:288)

Reproduction, injecting exact `ENOSPC` at the two `tempfile.mkdtemp` calls:

```bash
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python -B \
  /tmp/ar274-review-r3/laneC/q10/repro_full_disk_surface.py
```

Output:

```text
DIFF_EXCEPTION=OSError:[Errno 28] No space left on device
REFERENCE_EXCEPTION=OSError:[Errno 28] No space left on device
```

I did not fill a real filesystem; the syscall result was fault-injected at the exact allocation sites.

Why it matters: raw `OSError` escapes instead of the `DiffAcquisitionError` and `ReferenceSourceError` promised by [checkpoint:234](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:234).

## Question 8 — New-test mutant audit

I identified 80 newly added test functions. Every runnable function received a claim-specific mutant; the following tests passed mutants that removed the property they claim to establish. Mutants were applied only in scratch repository copies.

| Severity | Test | Mutant and reproduced result |
|---|---|---|
| P2 | [blackbox:245](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_blackbox_e2e_v2.py:245) | Removed `--ignored=matching`; `1 passed in 1.09s`. The new untracked `.gitignore` itself changes status, so the ignored leak need never be observed. |
| P2 | [blackbox:279](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_blackbox_e2e_v2.py:279), [operational:299](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:299), [operational:379](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:379) | Replaced all bound results with `bound_results = ()`; `3 passed in 2.16s`. `manual_required/policy_failure` does not prove responses were consumed. |
| P2 | [operational:701](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:701) | Bypassed receipt verification and directly minted `_VerifiedRouterResultV2`; `1 passed in 0.96s`. |
| P2 | [reference:179](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_reference_source_v2.py:179) | Moved materialization before `try/finally`; `1 passed in 0.42s`, with an actual leaked `agent-review-reference-source-v2-*` directory. |
| P2 | [diff:1851](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_diff_acquisition_v2.py:1851), [diff:1870](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_diff_acquisition_v2.py:1870) | Forced Git cwd back to the target instead of the attribute worktree. These two passed; discriminating attribute tests failed. Overall output: `2 failed, 3 passed in 1.52s`. |
| P3 | [blackbox:354](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_blackbox_e2e_v2.py:354) | Added `--result-path` and wrote there while still rejecting `--output`; `1 passed in 2.19s`. The test proves only the spelling `--output` is absent. |
| P3 | [operational:671](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:671) | Caught `ValidationError` through an alias; structural selector passed, `1 passed in 0.51s`. The separate behavioral post-seal test did kill this mutant. |
| P3 | [operational:692](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:692) | Renamed the exception variable and used `getattr(error, "reason_code")`; `1 passed in 0.56s`. |
| P3 | [reference:341](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_reference_source_v2.py:341) | Imported `subprocess.run` under an alias and ran `git rev-parse HEAD`; `1 passed in 0.42s`. |
| P3 | [toolrepo:264](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_toolrepo_identity_v2.py:264) | Reintroduced the old `.exists()` prefilter; selector passed. Its fixture is a modified tracked file, not an index deletion followed by an untracked same-spelling replacement. Overall related output: `1 failed, 2 passed in 0.39s`. |

The six tests left to the final mutation pass produced three additional survivors above (`--result-path`, aliased catch, renamed exception variable) and three valid kills:

- [operational:623](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_operational_run_v2.py:623) rejected a post-seal `ValidationError` catch.
- [toolrepo:316](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_toolrepo_identity_v2.py:316) rejected a gitless fallback.
- [toolrepo:327](/opt/agent-tools/ar-200d-successor/tests/agent_review/test_toolrepo_identity_v2.py:327) rejected removal of the required second-order-honesty text.

This directly falsifies the “all executed and killed” statement at [checkpoint:586](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:586), including M10.

Could not test:

- `test_sealed_argv_admits_a_foreign_owned_checkout`
- `test_sealed_argv_safe_directory_does_not_admit_other_repositories`

Both skipped because this sandbox cannot change ownership:

```text
cannot change ownership in this environment
2 skipped
```

## Question 10 — Documentation overclaims

Confirmed overclaims:

- [CHANGELOG.md:17](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:17): “no target mutation” — contradicted by lazy-fetch pack writes and worktree administration.
- [CHANGELOG.md:78](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:78): “target tree byte-identical” — the oracle does not compare bytes and cannot see `.git` or modification of existing ignored files.
- [CHANGELOG.md:94](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:94): Git semantics closed for every subprocess — contradicted by `diff.ignoreSubmodules`, `diff.context`, and lazy fetch.
- [CHANGELOG.md:149](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:149): remaining execution surface enumerated — omits promisor lazy-fetch transports.
- [CHANGELOG.md:161](/opt/agent-tools/ar-200d-successor/CHANGELOG.md:161): stdout-only plus corrected oracle establishes target safety — contradicted above.
- [checkpoint:586](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:586): all mutants killed — directly false.
- [checkpoint:622](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:622): `git_interpretation_environment_bound: true` — false.
- [checkpoint:626](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:626): `target_mutation_oracle_complete: true` — false.
- [checkpoint:655](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:655): `git_semantic_execution_closure: true` — false.
- [checkpoint:662](/opt/agent-tools/ar-200d-successor/docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md:662): `cli_no_filesystem_output_authority: true` — false.

## SUSPECTED

None.

## Question 12 — Blocking assessment

I would block this PR on:

1. The P0 target-controlled `ext::` execution and persistent object-store mutation.
2. The P1 `diff.ignoreSubmodules=all` fail-open omission.
3. The mutation oracle’s inability to detect the P0 defect it claims to exclude.
4. The qualification/checkpoint claims remaining asserted despite reproduced counterexamples.

Final subject integrity:

```text
HEAD=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
git status --porcelain=v1 --untracked-files=all: empty
```

The subject worktree was not modified.
EXIT=0
