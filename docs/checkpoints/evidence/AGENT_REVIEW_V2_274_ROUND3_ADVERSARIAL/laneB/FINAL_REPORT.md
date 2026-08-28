codex
Verdict: **BLOCK**. I reproduced five P1 findings, three P2 findings, and two P3 findings across questions 4, 5, and 6. No P0 finding. Question 11 showed byte-for-byte equivalence for tested ordinary repositories.

The subject remained unchanged at `c68a8b9a6b4d57383918f7fc1fa6a85536e331c6`; final `git status --porcelain=v1` was empty.

## CONFIRMED

### P1 — Unicode whitespace bypasses `info/attributes` refusal (Q4)

File: [_sealed_git_execution_v2.py:323](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:323), lines 323–325.

Reproduction:

```bash
TMPDIR=/tmp/ar274-review-r3/laneB/q4 \
PYTHONDONTWRITEBYTECODE=1 \
timeout 30s \
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
/tmp/ar274-review-r3/laneB/q4/probe_q4.py
```

An attributes line beginning with U+00A0 had bytes:

```text
c2a0237370656369616c202d646966660a
```

Output:

```text
python_stripped="#special -diff"
helper_active=false
check_attr_stdout="\"\302\240#special\": diff: unset"
plain_git_diff_binary=true
acquire.kind="diff"
acquire.binary=true
```

Python’s `strip()` removes U+00A0 and misclassifies the rule as a comment. Git treats U+00A0 as part of the filename pattern. Public acquisition therefore returned a corrupted binary patch instead of refusing.

### P1 — FIFO `info/attributes` is skipped although Git reads it (Q4)

File: [_sealed_git_execution_v2.py:314](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:314), lines 314–315.

Same reproduction command produced:

```text
path_is_file=false
path_is_fifo=true
helper_active=false
feed_writes=2
feed_errors=[]
acquire.kind="diff"
acquire.binary=true
acquire.contains_world=false
```

A feeder wrote `reviewed.txt -diff\n` into the FIFO. `Path.is_file()` rejected the FIFO as absent, but Git opened it and applied the rule. This is another canonical-diff integrity bypass; without a feeder it can instead hang acquisition.

### P1 — Actual Python import roots are outside the identity bound (Q6)

Files:

- [toolrepo_identity_v2.py:153](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:153), lines 153–156
- [aiops-review-run-v2.py:61](/opt/agent-tools/ar-200d-successor/scripts/aiops-review-run-v2.py:61)
- [aiops-review-run-v2.py:67](/opt/agent-tools/ar-200d-successor/scripts/aiops-review-run-v2.py:67), lines 67–73

The bound contains only `app/` and the exact CLI file. Python also imports from sibling files in `scripts/`, then from repository-root modules after `REPO_ROOT` is inserted into `sys.path`.

Reproduction:

```bash
TMPDIR=/tmp/ar274-review-r3/laneB/q6 \
PYTHONDONTWRITEBYTECODE=1 \
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
/tmp/ar274-review-r3/laneB/q6/repro_q6.py
```

Against a disposable clone of exact HEAD, an untracked `scripts/argparse.py` proxy produced:

```text
fixture_head c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
identity PASS sha=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
ls-files []
cli_rc 0
cli_first_lines UNTRACKED_ARGPARSE_PROXY_EXECUTED | usage: aiops-review-run-v2.py ...
```

An untracked repository-root `pydantic.py` likewise executed. A writer can therefore execute unbound Python before identity while the process still reports the declared clean SHA.

### P1 — Index flags hide modified and deleted bounded source (Q6)

File: [toolrepo_identity_v2.py:267](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:267), especially lines 274–281.

The code treats an empty `git diff --name-only HEAD` as proof of cleanliness. Both `assume-unchanged` and `skip-worktree` suppress this check.

Exact-HEAD result:

```text
CASE full subject assume-unchanged modified CLI
fixture_head c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
identity PASS sha=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
git_diff ''
ls_files_v h scripts/aiops-review-run-v2.py
cli_rc 0
cli_first_lines FULL_SUBJECT_ASSUME_UNCHANGED_CODE_EXECUTED | usage: ...
```

Other reproduced variants:

```text
CASE index skip-worktree modify
identity PASS
git_diff ''
ls_files_v S app/agent_review/mod.py
import_output INDEX_FLAG_MODIFIED_SOURCE_EXECUTED

CASE index skip-worktree delete
identity PASS
git_diff ''

CASE assume-unchanged exact CLI deletion
identity PASS
git_diff ''
```

This reopens both modified-source execution and the exact bounded-deletion gap the correction claims to close.

### P1 — `.py` filtering admits executable bytecode, symlink packages, and nested repositories (Q6)

File: [toolrepo_identity_v2.py:246](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:246), especially lines 258–264; explicit cache exclusion is at lines 218–226.

An unchecked-hash cache replaced the checker’s own tracked module in an exact-HEAD clone:

```text
fixture_head c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
identity PASS sha=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
ls-files ['app/agent_review/__pycache__/toolrepo_identity_v2.cpython-311.pyc']
cli_rc 0
cli_first_lines FULL_SUBJECT_UNCHECKED_PYC_EXECUTED | usage: ...
```

`PYTHONDONTWRITEBYTECODE=1` does not prevent reading bytecode.

Additional reproduced forms:

```text
CASE untracked symlink directory
identity PASS
ls-files ['app/agent_review/linked_pkg']
import_output SYMLINK_PACKAGE_EXECUTED

CASE untracked nested Git repository
identity PASS
ls-files ['app/agent_review/vendor_pkg/']
import_output NESTED_GIT_PAYLOAD_EXECUTED
```

Git collapses these to suffixless directory entries, so neither passes the `endswith(".py")` filter.

### P2 — Failed cleanup silently leaves `.git/worktrees` registrations (Q5)

File: [diff_acquisition_v2.py:961](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:961), lines 961 and 972–980.

Killed removal child:

```bash
TMPDIR=/tmp/ar274-review-r3/laneB/q5 \
PATH=/tmp/ar274-review-r3/laneB/q5/git-shim:/usr/bin:/bin \
Q5_GIT_MODE=kill_remove_before_execution \
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
/tmp/ar274-review-r3/laneB/q5/lifecycle_probe.py \
remove_child_sigkill /tmp/ar274-review-r3/laneB/q5/repo \
e3121762493b6540df94556078431a1b37eacce8 \
f29f138d5913164c4a57eff50d130778fe376edb \
/tmp/ar274-review-r3/laneB/q5
```

Output:

```text
"acquire_result": "success"
"diff_has_added_line": true
"holder_paths": []
"registration_names": ["wt"]
prunable gitdir file points to non-existent location
```

When the add child was killed after real Git had successfully registered the worktree:

```text
"acquire_result": "raised"
"reason_code": "diff_unreadable"
"holder_paths": []
"registration_names": ["wt"]
```

The add-error path never invokes removal, while the remove path ignores its return code. `rmtree` then removes the filesystem checkout and leaves administrative residue. Three repeated failures accumulated `wt`, `wt1`, and `wt2`.

This was not irreparable repository corruption: `git status` and `git fsck` remained successful, and `git worktree prune` recovered the state. It is nevertheless persistent target mutation and unbounded stale-state accumulation.

### P2 — A tracked symlink does not bind executed bytes (Q6)

File: [toolrepo_identity_v2.py:267](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:267), lines 267–283.

Reproduction:

```text
identity_before_external_change PASS sha=f99b2a12...
identity_after_external_change PASS sha=f99b2a12...
git_status <clean>
import_output TRACKED_SYMLINK_PAYLOAD_V2_UNBOUND
```

Changing the external referent changed executed Python without changing HEAD, the symlink blob, or status. Current HEAD has no bounded tracked symlink, hence P2 rather than P1.

### P2 — A symlink loop escapes as raw `RuntimeError` (Q4)

Files:

- [_sealed_git_execution_v2.py:313](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:313)
- [diff_acquisition_v2.py:931](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:931), lines 931–934

A self-referential `info/attributes -> attributes` produced:

```text
helper.exception="RuntimeError"
helper.detail="Symlink loop from '.../.git/info/attributes'"
acquire.exception="RuntimeError"
```

`Path.resolve()` raises `RuntimeError`, but the caller only converts `OSError`. A hostile repository can therefore crash the operational path outside its typed refusal contract.

### P3 — Syntactically inert attributes content causes false refusal (Q4)

File: [_sealed_git_execution_v2.py:322](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:322), lines 322–325.

With `info/attributes` containing only `reviewed.txt`:

```text
check_attr_stdout=""
plain_git_diff_binary=false
plain_git_diff_contains_world=true
helper_active=true
acquire.reason="diff_info_attributes_active"
```

Git applies no attribute, but the helper calls every non-comment line active. This is an availability regression.

### P3 — Owner-process SIGKILL has no recovery path (Q5)

File: [diff_acquisition_v2.py:914](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:914), with cleanup only at lines 971–980.

After waiting until the context yielded and killing the owning Python process:

```text
wait_rc=137
holders_after_parent_kill:
.../agent-review-diff-attr-source-v2-oaqys3i7
```

Git still listed the detached worktree. This is initially a valid abandoned worktree, not corruption; if temporary-directory cleanup later removes it, it becomes prunable stale state. OOM kills and hard CI cancellation require external startup scavenging or a narrower lifecycle guarantee.

## Correctly reproduced paths

Question 4:

```text
normal active file       -> diff_info_attributes_active
linked worktree .git FILE -> shared main .git/info/attributes; refused
separate --git-dir .git FILE -> separate-git-dir/info/attributes; refused
/proc/kcore unreadable regular file -> PermissionError; helper=true; refused
```

A chmod-`000` file could not be made unreadable because only uid 0 is mapped in this namespace. `/proc/kcore` exercised the real read-error branch without mocking.

Question 5:

- Normal exit: cleaned.
- Deliberate exception inside `with`: cleaned and preserved the original exception.
- Killed body/diff child: acquisition refused and cleaned.
- Injected `ENOSPC` during checkout-file creation: Git rolled back registration and the holder was removed.
- Fault-injected `ENOSPC` during administrative deletion reproduced the same stale-registration leak. I did not physically exhaust the shared filesystem because that would endanger other lanes.

Question 6:

- Ordinary namespace-package `.py`: correctly refused.
- Local `.pth`: ignored by identity but not processed by actual CLI startup; no execution finding.
- Uppercase `.PY`: evaded filtering but was not importable on this Linux host. A case-insensitive filesystem was unavailable.

## Question 11 — No reproduced output regression for ordinary repositories

I compared current HEAD against tree `2fdedbf909785beab4f7659395b88d6f2bbdbb4f`, immediately before the sealed/worktree implementation, using [compare_revision.py](/tmp/ar274-review-r3/laneB/q11/compare_revision.py).

The fixture covered text, committed binary attributes, copy, rename, Unicode paths, parsed authoritative identity, and toolrepo identity.

```text
785d7d4a18f0d2598aee34fbd7e22b12054c441fb3439bd4fdd2b0cdc5fb08fb  pre.json
785d7d4a18f0d2598aee34fbd7e22b12054c441fb3439bd4fdd2b0cdc5fb08fb  head.json
5158 pre.json
5158 head.json
cmp: no output
```

A linked-worktree run was also identical:

```text
fdefebf5c5626a83f21f7ece61595996e0600512c427694823699d8a8cb275b3  pre-linked.json
fdefebf5c5626a83f21f7ece61595996e0600512c427694823699d8a8cb275b3  head-linked.json
cmp: no output
```

Unified and raw hashes were respectively:

```text
ae01038f3c82f404f0df5cd2a8f476ef6f435b38faa0cb7ce23419c0c61f4775
9c88a870a54f27021da8eea2bdfc12a313eea44bda73a48fa91332441a326f99
```

No disposable-worktree residue remained in these successful runs.

## SUSPECTED

No additional suspected findings reported. Case-insensitive filesystem behavior and literal physical-disk exhaustion were not available for safe testing, as noted above.
diff --git a/q11/compare_revision.py b/q11/compare_revision.py
new file mode 100644
index 0000000000000000000000000000000000000000..0342a47b587c70a0a598da155fe5436ecc1d3430
--- /dev/null
+++ b/q11/compare_revision.py
@@ -0,0 +1,69 @@
+from __future__ import annotations
+
+import argparse
+import hashlib
+import json
+from pathlib import Path
+
+from app.agent_review import toolrepo_identity_v2 as identity
+from app.agent_review.diff_acquisition_v2 import (
+    acquire_authoritative_diff_v2,
+    acquire_diff_v2,
+    acquire_raw_diff_v2,
+)
+
+
+def main() -> None:
+    parser = argparse.ArgumentParser()
+    parser.add_argument("repo", type=Path)
+    parser.add_argument("base")
+    parser.add_argument("head")
+    parser.add_argument("output", type=Path)
+    args = parser.parse_args()
+
+    unified = acquire_diff_v2(args.repo, base_sha=args.base, head_sha=args.head)
+    raw = acquire_raw_diff_v2(args.repo, base_sha=args.base, head_sha=args.head)
+    authoritative = acquire_authoritative_diff_v2(
+        args.repo, base_sha=args.base, head_sha=args.head
+    )
+
+    # Exercise the exact same clean fixture and declared SHA under both
+    # implementations. The package location is the authority's only root
+    # discovery input, so bind it to the fixture instead of the archived
+    # implementation checkout.
+    identity._agent_review_package_v2.__file__ = str(
+        args.repo / "app" / "agent_review" / "__init__.py"
+    )
+    observed = identity.establish_toolrepo_source_identity_v2(
+        declared_toolrepo_sha=args.head,
+        executing_script=args.repo / "scripts" / "aiops-review-run-v2.py",
+    )
+
+    payload = {
+        "unified": unified,
+        "unified_sha256": hashlib.sha256(unified.encode()).hexdigest(),
+        "raw_hex": raw.encode().hex(),
+        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
+        "authoritative": [
+            {
+                "old_path": item.old_path,
+                "new_path": item.new_path,
+                "change_type": item.change_type,
+                "is_binary": item.is_binary,
+                "is_submodule": item.is_submodule,
+                "similarity_index": item.similarity_index,
+                "old_no_newline_at_eof": item.old_no_newline_at_eof,
+                "new_no_newline_at_eof": item.new_no_newline_at_eof,
+                "truncated": item.truncated,
+                "hunks": [hunk.__dict__ for hunk in item.hunks],
+            }
+            for item in authoritative
+        ],
+        "identity_sha": observed.toolrepo_sha,
+        "identity_root": str(observed.toolrepo_root),
+    }
+    args.output.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False), encoding="utf-8")
+
+
+if __name__ == "__main__":
+    main()
diff --git a/q11/fixture/.gitattributes b/q11/fixture/.gitattributes
new file mode 100644
index 0000000000000000000000000000000000000000..348df56654ee541e5da023b5bac0ff2446d75227
--- /dev/null
+++ b/q11/fixture/.gitattributes
@@ -0,0 +1 @@
+binary.dat -diff
diff --git a/q11/fixture/app/agent_review/__init__.py b/q11/fixture/app/agent_review/__init__.py
new file mode 100644
index 0000000000000000000000000000000000000000..36df71023e1a5e2db54ed97f32bdc15e1668d6d4
--- /dev/null
+++ b/q11/fixture/app/agent_review/__init__.py
@@ -0,0 +1 @@
+"""Fixture package for identity-equivalence review."""
diff --git a/q11/fixture/app/common.py b/q11/fixture/app/common.py
new file mode 100644
index 0000000000000000000000000000000000000000..17f74052c128c293445e6dcb79d126dffcdb662d
--- /dev/null
+++ b/q11/fixture/app/common.py
@@ -0,0 +1 @@
+VALUE = "head"
diff --git a/q11/fixture/binary.dat b/q11/fixture/binary.dat
new file mode 100644
index 0000000000000000000000000000000000000000..137a995c7de704122e84677183e9520b1eead498
--- /dev/null
+++ b/q11/fixture/binary.dat
@@ -0,0 +1 @@
+head binary-shaped content
diff --git a/q11/fixture/copied-target.txt b/q11/fixture/copied-target.txt
new file mode 100644
index 0000000000000000000000000000000000000000..eb0c95281675d8bc9b62914ae464038ca48a651c
--- /dev/null
+++ b/q11/fixture/copied-target.txt
@@ -0,0 +1,10 @@
+copy line 01
+copy line 02
+copy line 03
+copy line 04
+copy line 05
+copy line 06
+copy line 07
+copy line 08
+copy line 09
+copy line 10
diff --git a/q11/fixture/copy-source.txt b/q11/fixture/copy-source.txt
new file mode 100644
index 0000000000000000000000000000000000000000..103b161695b327d856dbbf5ba6900dcad48f79a2
--- /dev/null
+++ b/q11/fixture/copy-source.txt
@@ -0,0 +1,10 @@
+copy line 01
+copy line 02
+copy line 03
+copy line 04
+copy line 05
+copy line 06
+copy line 07
+copy line 08
+copy line 09
+copy line 10 changed
diff --git a/q11/fixture/rename-new.txt b/q11/fixture/rename-new.txt
index 7125a1b46a77bb47981136161ca24f99d10ff84a..79274081bf718c86c8c50c1b8ab8c21fc71fcb52
--- a/q11/fixture/rename-new.txt
+++ b/q11/fixture/rename-new.txt
@@ -7,4 +7,4 @@
 rename line 07
 rename line 08
 rename line 09
-rename line 10
+rename line 10 changed
diff --git a/q11/fixture/rename-old.txt b/q11/fixture/rename-old.txt
new file mode 100644
index 0000000000000000000000000000000000000000..7125a1b46a77bb47981136161ca24f99d10ff84a
--- /dev/null
+++ b/q11/fixture/rename-old.txt
@@ -0,0 +1,10 @@
+rename line 01
+rename line 02
+rename line 03
+rename line 04
+rename line 05
+rename line 06
+rename line 07
+rename line 08
+rename line 09
+rename line 10
diff --git a/q11/fixture/scripts/aiops-review-run-v2.py b/q11/fixture/scripts/aiops-review-run-v2.py
new file mode 100644
index 0000000000000000000000000000000000000000..601e890da05be6b1897df2bf412413e8bcc341af
--- /dev/null
+++ b/q11/fixture/scripts/aiops-review-run-v2.py
@@ -0,0 +1,2 @@
+#!/usr/bin/env python3
+print("fixture")
diff --git a/q11/fixture/text.txt b/q11/fixture/text.txt
new file mode 100644
index 0000000000000000000000000000000000000000..3021541b87d014d651cc122f4a1901315b6404d3
--- /dev/null
+++ b/q11/fixture/text.txt
@@ -0,0 +1,4 @@
+alpha
+beta changed
+gamma
+delta
diff --git a/q11/fixture/unicode-é.txt b/q11/fixture/unicode-é.txt
new file mode 100644
index 0000000000000000000000000000000000000000..341988a28d0799fdab7483e80334d3a3ec8d8b55
--- /dev/null
+++ b/q11/fixture/unicode-é.txt
@@ -0,0 +1 @@
+bonjour modifié
diff --git a/root-q4/fixture/.git/info/attributes b/root-q4/fixture/.git/info/attributes
new file mode 100644
index 0000000000000000000000000000000000000000..0a9ebe92a356bc550a33690fdfd8a38bfdd467b1
--- /dev/null
+++ b/root-q4/fixture/.git/info/attributes
@@ -0,0 +1 @@
+*.txt -diff
diff --git a/root-q4/fixture/f.txt b/root-q4/fixture/f.txt
new file mode 100644
index 0000000000000000000000000000000000000000..564b12f45becba5fb2f70e270af067c1f13b3aab
--- /dev/null
+++ b/root-q4/fixture/f.txt
@@ -0,0 +1 @@
+head
diff --git a/root-q4/probe.py b/root-q4/probe.py
new file mode 100644
index 0000000000000000000000000000000000000000..c89a086a0671a84616ca499501dbc61c0783c9c1
--- /dev/null
+++ b/root-q4/probe.py
@@ -0,0 +1,18 @@
+from pathlib import Path
+import sys
+
+from app.agent_review._sealed_git_execution_v2 import (
+    has_semantically_active_info_attributes_v2,
+    sealed_git_child_env_v2,
+)
+from app.agent_review.diff_acquisition_v2 import acquire_diff_v2
+
+repo = Path(sys.argv[1])
+base, head = sys.argv[2:4]
+print("active=", has_semantically_active_info_attributes_v2(repo, env=sealed_git_child_env_v2()))
+try:
+    result = acquire_diff_v2(repo, base_sha=base, head_sha=head)
+except Exception as exc:
+    print("acquire_exc=", type(exc).__name__, getattr(exc, "reason_code", None), repr(str(exc)))
+else:
+    print("acquire_ok=", len(result), result.splitlines()[-1])
diff --git a/root-q5/bin/git b/root-q5/bin/git
new file mode 100644
index 0000000000000000000000000000000000000000..3cde68a1a9660320efcad752bc090568e17e0e42
--- /dev/null
+++ b/root-q5/bin/git
@@ -0,0 +1,12 @@
+#!/bin/sh
+previous=""
+for argument in "$@"; do
+    if [ "${KILL_GIT_PHASE:-}" = "remove" ] && [ "$previous" = "worktree" ] && [ "$argument" = "remove" ]; then
+        kill -KILL "$$"
+    fi
+    if [ "${KILL_GIT_PHASE:-}" = "diff" ] && [ "$argument" = "diff" ]; then
+        kill -KILL "$$"
+    fi
+    previous="$argument"
+done
+exec /usr/bin/git "$@"
diff --git a/root-q5/make_long_commit.py b/root-q5/make_long_commit.py
new file mode 100644
index 0000000000000000000000000000000000000000..bb6b3b31412f6a13c19b11e1f62b1dcf38702e93
--- /dev/null
+++ b/root-q5/make_long_commit.py
@@ -0,0 +1,27 @@
+from __future__ import annotations
+
+import subprocess
+import sys
+from pathlib import Path
+
+repo = Path(sys.argv[1])
+base = sys.argv[2]
+blob = subprocess.run(
+    ["git", "hash-object", "-w", "--stdin"],
+    cwd=repo,
+    input=b"payload\n",
+    capture_output=True,
+    check=True,
+).stdout.strip().decode()
+name = "x" * 300
+record = f"100644 blob {blob}\t{name}\0".encode()
+tree = subprocess.run(
+    ["git", "mktree", "-z"], cwd=repo, input=record, capture_output=True, check=True
+).stdout.strip().decode()
+commit = subprocess.run(
+    ["git", "commit-tree", tree, "-p", base, "-m", "long path"],
+    cwd=repo,
+    capture_output=True,
+    check=True,
+).stdout.strip().decode()
+print(commit)
diff --git a/root-q5/probe_cleanup.py b/root-q5/probe_cleanup.py
new file mode 100644
index 0000000000000000000000000000000000000000..281b3d5bc752c43a03dacd3f14d93d8fba5e94a2
--- /dev/null
+++ b/root-q5/probe_cleanup.py
@@ -0,0 +1,12 @@
+from pathlib import Path
+import sys
+
+from app.agent_review.diff_acquisition_v2 import acquire_diff_v2
+
+repo = Path(sys.argv[1]).resolve()
+try:
+    result = acquire_diff_v2(repo, base_sha=sys.argv[2], head_sha=sys.argv[3])
+except BaseException as exc:
+    print("result=exception", type(exc).__name__, getattr(exc, "reason_code", None), repr(str(exc)))
+else:
+    print("result=success", len(result))
diff --git a/root-q5/repo/base.txt b/root-q5/repo/base.txt
new file mode 100644
index 0000000000000000000000000000000000000000..df967b96a579e45a18b8251732d16804b2e56a55
--- /dev/null
+++ b/root-q5/repo/base.txt
@@ -0,0 +1 @@
+base
diff --git a/root-q5/subrepo/f.txt b/root-q5/subrepo/f.txt
new file mode 100644
index 0000000000000000000000000000000000000000..df967b96a579e45a18b8251732d16804b2e56a55
--- /dev/null
+++ b/root-q5/subrepo/f.txt
@@ -0,0 +1 @@
+base
diff --git a/root-q6/check_identity.py b/root-q6/check_identity.py
new file mode 100644
index 0000000000000000000000000000000000000000..ba6ea32f28bfd99f570af154bb77d9b52c6bd0aa
--- /dev/null
+++ b/root-q6/check_identity.py
@@ -0,0 +1,13 @@
+from pathlib import Path
+import sys
+
+import app.agent_review as package
+from app.agent_review.toolrepo_identity_v2 import establish_toolrepo_source_identity_v2
+
+repo = Path(sys.argv[1]).resolve()
+package.__file__ = str(repo / "app" / "agent_review" / "__init__.py")
+identity = establish_toolrepo_source_identity_v2(
+    declared_toolrepo_sha=sys.argv[2],
+    executing_script=repo / "scripts" / "aiops-review-run-v2.py",
+)
+print("identity=accepted", identity.toolrepo_sha)
diff --git a/root-q6/external/victim/__init__.py b/root-q6/external/victim/__init__.py
new file mode 100644
index 0000000000000000000000000000000000000000..bd4ad1236c4cbf0c2b114e976455458c0c006958
--- /dev/null
+++ b/root-q6/external/victim/__init__.py
@@ -0,0 +1 @@
+ORIGIN = "external-symlink-package-overrode-tracked-source"
diff --git a/root-q6/import_victim.py b/root-q6/import_victim.py
new file mode 100644
index 0000000000000000000000000000000000000000..84a0934734393e80eca26626507b7cf99c09a383
--- /dev/null
+++ b/root-q6/import_victim.py
@@ -0,0 +1,4 @@
+import app.agent_review.victim as victim
+
+print("origin=", victim.ORIGIN)
+print("loaded_from=", victim.__file__)
diff --git a/root-q6/pyc_override_payload.py b/root-q6/pyc_override_payload.py
new file mode 100644
index 0000000000000000000000000000000000000000..79c691c5c4827ea2c5fdf6a711af591593b07ac5
--- /dev/null
+++ b/root-q6/pyc_override_payload.py
@@ -0,0 +1 @@
+ORIGIN = "malicious-unchecked-cache-overrode-tracked-source"
diff --git a/root-q6/pyc_payload.py b/root-q6/pyc_payload.py
new file mode 100644
index 0000000000000000000000000000000000000000..ba1ae69ba801de85452cafd8be55b123e58a943b
--- /dev/null
+++ b/root-q6/pyc_payload.py
@@ -0,0 +1 @@
+ORIGIN = "untracked-legacy-bytecode"
diff --git a/root-q6/repo/app/__init__.py b/root-q6/repo/app/__init__.py
new file mode 100644
index 0000000000000000000000000000000000000000..242f1b1b5bf6bc5c598a62d59dfbcc7da0c52cf2
--- /dev/null
+++ b/root-q6/repo/app/__init__.py
@@ -0,0 +1 @@
+"""Fixture app."""
diff --git a/root-q6/repo/app/agent_review/__init__.py b/root-q6/repo/app/agent_review/__init__.py
new file mode 100644
index 0000000000000000000000000000000000000000..fbfc7402c75ebd4126e41464511100675309fe5e
--- /dev/null
+++ b/root-q6/repo/app/agent_review/__init__.py
@@ -0,0 +1 @@
+"""Fixture agent_review."""
diff --git a/root-q6/repo/app/agent_review/victim.py b/root-q6/repo/app/agent_review/victim.py
new file mode 100644
index 0000000000000000000000000000000000000000..b818f4a9fb4c384f9d585e9b293d80804a448dc8
--- /dev/null
+++ b/root-q6/repo/app/agent_review/victim.py
@@ -0,0 +1 @@
+ORIGIN = "tracked-module"
diff --git a/root-q6/repo/app/agent_review/victim/__init__.py b/root-q6/repo/app/agent_review/victim/__init__.py
new file mode 100644
index 0000000000000000000000000000000000000000..20d45438e5f10230cf3de700740a04bc3e8367af
--- /dev/null
+++ b/root-q6/repo/app/agent_review/victim/__init__.py
@@ -0,0 +1 @@
+ORIGIN = "untracked-nested-repository-package"
diff --git a/root-q6/repo/scripts/aiops-review-run-v2.py b/root-q6/repo/scripts/aiops-review-run-v2.py
new file mode 100644
index 0000000000000000000000000000000000000000..601e890da05be6b1897df2bf412413e8bcc341af
--- /dev/null
+++ b/root-q6/repo/scripts/aiops-review-run-v2.py
@@ -0,0 +1,2 @@
+#!/usr/bin/env python3
+print("fixture")

tokens used
233,973
Verdict: **BLOCK**. I reproduced five P1 findings, three P2 findings, and two P3 findings across questions 4, 5, and 6. No P0 finding. Question 11 showed byte-for-byte equivalence for tested ordinary repositories.

The subject remained unchanged at `c68a8b9a6b4d57383918f7fc1fa6a85536e331c6`; final `git status --porcelain=v1` was empty.

## CONFIRMED

### P1 — Unicode whitespace bypasses `info/attributes` refusal (Q4)

File: [_sealed_git_execution_v2.py:323](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:323), lines 323–325.

Reproduction:

```bash
TMPDIR=/tmp/ar274-review-r3/laneB/q4 \
PYTHONDONTWRITEBYTECODE=1 \
timeout 30s \
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
/tmp/ar274-review-r3/laneB/q4/probe_q4.py
```

An attributes line beginning with U+00A0 had bytes:

```text
c2a0237370656369616c202d646966660a
```

Output:

```text
python_stripped="#special -diff"
helper_active=false
check_attr_stdout="\"\302\240#special\": diff: unset"
plain_git_diff_binary=true
acquire.kind="diff"
acquire.binary=true
```

Python’s `strip()` removes U+00A0 and misclassifies the rule as a comment. Git treats U+00A0 as part of the filename pattern. Public acquisition therefore returned a corrupted binary patch instead of refusing.

### P1 — FIFO `info/attributes` is skipped although Git reads it (Q4)

File: [_sealed_git_execution_v2.py:314](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:314), lines 314–315.

Same reproduction command produced:

```text
path_is_file=false
path_is_fifo=true
helper_active=false
feed_writes=2
feed_errors=[]
acquire.kind="diff"
acquire.binary=true
acquire.contains_world=false
```

A feeder wrote `reviewed.txt -diff\n` into the FIFO. `Path.is_file()` rejected the FIFO as absent, but Git opened it and applied the rule. This is another canonical-diff integrity bypass; without a feeder it can instead hang acquisition.

### P1 — Actual Python import roots are outside the identity bound (Q6)

Files:

- [toolrepo_identity_v2.py:153](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:153), lines 153–156
- [aiops-review-run-v2.py:61](/opt/agent-tools/ar-200d-successor/scripts/aiops-review-run-v2.py:61)
- [aiops-review-run-v2.py:67](/opt/agent-tools/ar-200d-successor/scripts/aiops-review-run-v2.py:67), lines 67–73

The bound contains only `app/` and the exact CLI file. Python also imports from sibling files in `scripts/`, then from repository-root modules after `REPO_ROOT` is inserted into `sys.path`.

Reproduction:

```bash
TMPDIR=/tmp/ar274-review-r3/laneB/q6 \
PYTHONDONTWRITEBYTECODE=1 \
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
/tmp/ar274-review-r3/laneB/q6/repro_q6.py
```

Against a disposable clone of exact HEAD, an untracked `scripts/argparse.py` proxy produced:

```text
fixture_head c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
identity PASS sha=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
ls-files []
cli_rc 0
cli_first_lines UNTRACKED_ARGPARSE_PROXY_EXECUTED | usage: aiops-review-run-v2.py ...
```

An untracked repository-root `pydantic.py` likewise executed. A writer can therefore execute unbound Python before identity while the process still reports the declared clean SHA.

### P1 — Index flags hide modified and deleted bounded source (Q6)

File: [toolrepo_identity_v2.py:267](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:267), especially lines 274–281.

The code treats an empty `git diff --name-only HEAD` as proof of cleanliness. Both `assume-unchanged` and `skip-worktree` suppress this check.

Exact-HEAD result:

```text
CASE full subject assume-unchanged modified CLI
fixture_head c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
identity PASS sha=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
git_diff ''
ls_files_v h scripts/aiops-review-run-v2.py
cli_rc 0
cli_first_lines FULL_SUBJECT_ASSUME_UNCHANGED_CODE_EXECUTED | usage: ...
```

Other reproduced variants:

```text
CASE index skip-worktree modify
identity PASS
git_diff ''
ls_files_v S app/agent_review/mod.py
import_output INDEX_FLAG_MODIFIED_SOURCE_EXECUTED

CASE index skip-worktree delete
identity PASS
git_diff ''

CASE assume-unchanged exact CLI deletion
identity PASS
git_diff ''
```

This reopens both modified-source execution and the exact bounded-deletion gap the correction claims to close.

### P1 — `.py` filtering admits executable bytecode, symlink packages, and nested repositories (Q6)

File: [toolrepo_identity_v2.py:246](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:246), especially lines 258–264; explicit cache exclusion is at lines 218–226.

An unchecked-hash cache replaced the checker’s own tracked module in an exact-HEAD clone:

```text
fixture_head c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
identity PASS sha=c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
ls-files ['app/agent_review/__pycache__/toolrepo_identity_v2.cpython-311.pyc']
cli_rc 0
cli_first_lines FULL_SUBJECT_UNCHECKED_PYC_EXECUTED | usage: ...
```

`PYTHONDONTWRITEBYTECODE=1` does not prevent reading bytecode.

Additional reproduced forms:

```text
CASE untracked symlink directory
identity PASS
ls-files ['app/agent_review/linked_pkg']
import_output SYMLINK_PACKAGE_EXECUTED

CASE untracked nested Git repository
identity PASS
ls-files ['app/agent_review/vendor_pkg/']
import_output NESTED_GIT_PAYLOAD_EXECUTED
```

Git collapses these to suffixless directory entries, so neither passes the `endswith(".py")` filter.

### P2 — Failed cleanup silently leaves `.git/worktrees` registrations (Q5)

File: [diff_acquisition_v2.py:961](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:961), lines 961 and 972–980.

Killed removal child:

```bash
TMPDIR=/tmp/ar274-review-r3/laneB/q5 \
PATH=/tmp/ar274-review-r3/laneB/q5/git-shim:/usr/bin:/bin \
Q5_GIT_MODE=kill_remove_before_execution \
/opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
/tmp/ar274-review-r3/laneB/q5/lifecycle_probe.py \
remove_child_sigkill /tmp/ar274-review-r3/laneB/q5/repo \
e3121762493b6540df94556078431a1b37eacce8 \
f29f138d5913164c4a57eff50d130778fe376edb \
/tmp/ar274-review-r3/laneB/q5
```

Output:

```text
"acquire_result": "success"
"diff_has_added_line": true
"holder_paths": []
"registration_names": ["wt"]
prunable gitdir file points to non-existent location
```

When the add child was killed after real Git had successfully registered the worktree:

```text
"acquire_result": "raised"
"reason_code": "diff_unreadable"
"holder_paths": []
"registration_names": ["wt"]
```

The add-error path never invokes removal, while the remove path ignores its return code. `rmtree` then removes the filesystem checkout and leaves administrative residue. Three repeated failures accumulated `wt`, `wt1`, and `wt2`.

This was not irreparable repository corruption: `git status` and `git fsck` remained successful, and `git worktree prune` recovered the state. It is nevertheless persistent target mutation and unbounded stale-state accumulation.

### P2 — A tracked symlink does not bind executed bytes (Q6)

File: [toolrepo_identity_v2.py:267](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:267), lines 267–283.

Reproduction:

```text
identity_before_external_change PASS sha=f99b2a12...
identity_after_external_change PASS sha=f99b2a12...
git_status <clean>
import_output TRACKED_SYMLINK_PAYLOAD_V2_UNBOUND
```

Changing the external referent changed executed Python without changing HEAD, the symlink blob, or status. Current HEAD has no bounded tracked symlink, hence P2 rather than P1.

### P2 — A symlink loop escapes as raw `RuntimeError` (Q4)

Files:

- [_sealed_git_execution_v2.py:313](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:313)
- [diff_acquisition_v2.py:931](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:931), lines 931–934

A self-referential `info/attributes -> attributes` produced:

```text
helper.exception="RuntimeError"
helper.detail="Symlink loop from '.../.git/info/attributes'"
acquire.exception="RuntimeError"
```

`Path.resolve()` raises `RuntimeError`, but the caller only converts `OSError`. A hostile repository can therefore crash the operational path outside its typed refusal contract.

### P3 — Syntactically inert attributes content causes false refusal (Q4)

File: [_sealed_git_execution_v2.py:322](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:322), lines 322–325.

With `info/attributes` containing only `reviewed.txt`:

```text
check_attr_stdout=""
plain_git_diff_binary=false
plain_git_diff_contains_world=true
helper_active=true
acquire.reason="diff_info_attributes_active"
```

Git applies no attribute, but the helper calls every non-comment line active. This is an availability regression.

### P3 — Owner-process SIGKILL has no recovery path (Q5)

File: [diff_acquisition_v2.py:914](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:914), with cleanup only at lines 971–980.

After waiting until the context yielded and killing the owning Python process:

```text
wait_rc=137
holders_after_parent_kill:
.../agent-review-diff-attr-source-v2-oaqys3i7
```

Git still listed the detached worktree. This is initially a valid abandoned worktree, not corruption; if temporary-directory cleanup later removes it, it becomes prunable stale state. OOM kills and hard CI cancellation require external startup scavenging or a narrower lifecycle guarantee.

## Correctly reproduced paths

Question 4:

```text
normal active file       -> diff_info_attributes_active
linked worktree .git FILE -> shared main .git/info/attributes; refused
separate --git-dir .git FILE -> separate-git-dir/info/attributes; refused
/proc/kcore unreadable regular file -> PermissionError; helper=true; refused
```

A chmod-`000` file could not be made unreadable because only uid 0 is mapped in this namespace. `/proc/kcore` exercised the real read-error branch without mocking.

Question 5:

- Normal exit: cleaned.
- Deliberate exception inside `with`: cleaned and preserved the original exception.
- Killed body/diff child: acquisition refused and cleaned.
- Injected `ENOSPC` during checkout-file creation: Git rolled back registration and the holder was removed.
- Fault-injected `ENOSPC` during administrative deletion reproduced the same stale-registration leak. I did not physically exhaust the shared filesystem because that would endanger other lanes.

Question 6:

- Ordinary namespace-package `.py`: correctly refused.
- Local `.pth`: ignored by identity but not processed by actual CLI startup; no execution finding.
- Uppercase `.PY`: evaded filtering but was not importable on this Linux host. A case-insensitive filesystem was unavailable.

## Question 11 — No reproduced output regression for ordinary repositories

I compared current HEAD against tree `2fdedbf909785beab4f7659395b88d6f2bbdbb4f`, immediately before the sealed/worktree implementation, using [compare_revision.py](/tmp/ar274-review-r3/laneB/q11/compare_revision.py).

The fixture covered text, committed binary attributes, copy, rename, Unicode paths, parsed authoritative identity, and toolrepo identity.

```text
785d7d4a18f0d2598aee34fbd7e22b12054c441fb3439bd4fdd2b0cdc5fb08fb  pre.json
785d7d4a18f0d2598aee34fbd7e22b12054c441fb3439bd4fdd2b0cdc5fb08fb  head.json
5158 pre.json
5158 head.json
cmp: no output
```

A linked-worktree run was also identical:

```text
fdefebf5c5626a83f21f7ece61595996e0600512c427694823699d8a8cb275b3  pre-linked.json
fdefebf5c5626a83f21f7ece61595996e0600512c427694823699d8a8cb275b3  head-linked.json
cmp: no output
```

Unified and raw hashes were respectively:

```text
ae01038f3c82f404f0df5cd2a8f476ef6f435b38faa0cb7ce23419c0c61f4775
9c88a870a54f27021da8eea2bdfc12a313eea44bda73a48fa91332441a326f99
```

No disposable-worktree residue remained in these successful runs.

## SUSPECTED

No additional suspected findings reported. Case-insensitive filesystem behavior and literal physical-disk exhaustion were not available for safe testing, as noted above.
EXIT=0
