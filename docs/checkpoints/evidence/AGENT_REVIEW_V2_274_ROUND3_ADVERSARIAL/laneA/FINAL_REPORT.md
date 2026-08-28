codex
# Lane A verdict

**Block.** `-c core.hooksPath=/dev/null` is not sufficient. I reproduced four code-execution paths, including two target-controlled acquisition paths that finish successfully after executing attacker-selected code.

Severity summary: **P0: none; P1: 5; P2: 5; P3: 1.**

Reproduction harnesses, all scratch-only:

```bash
# R1 — execution surface
env TMPDIR=/tmp/ar274-review-r3/laneA/q1-agent \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/opt/agent-tools/ar-200d-successor \
  /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
  /tmp/ar274-review-r3/laneA/q1-agent/probe_q1.py

# R2 — argv positioning and overrides
env TMPDIR=/tmp/ar274-review-r3/laneA/q2-agent \
  PYTHONDONTWRITEBYTECODE=1 \
  /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
  /tmp/ar274-review-r3/laneA/q2-agent/repro_q2.py

# R3 — worktree isolation and operational behavior
env TMPDIR=/tmp/ar274-review-r3/laneA/q3q9-agent \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/opt/agent-tools/ar-200d-successor \
  /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
  /tmp/ar274-review-r3/laneA/q3q9-agent/probe_q3q9.py
```

The harnesses are [probe_q1.py](/tmp/ar274-review-r3/laneA/q1-agent/probe_q1.py), [Q1 JSON output](/tmp/ar274-review-r3/laneA/q1-agent/probe_q1_output.json), [repro_q2.py](/tmp/ar274-review-r3/laneA/q2-agent/repro_q2.py), and [probe_q3q9.py](/tmp/ar274-review-r3/laneA/q3q9-agent/probe_q3q9.py).

## CONFIRMED

### 1. P1 — `includeIf.gitdir` bypasses the filter detector and executes during acquisition

File: [_sealed_git_execution_v2.py:271](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:271)–276; [diff_acquisition_v2.py:938](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:938)–958.

Reproduction: R1 configured:

```text
includeIf.gitdir:<repo>/.git/worktrees/**.path=<included-config>
```

The included config defined `filter.evil.smudge=<marker-and-cat script>`, and committed `.gitattributes` selected that filter.

Output:

```text
detector_before_worktree: false
acquisition_outcome: success
marker_exists: true
marker_content: executed
```

The detector evaluates includes from the primary checkout. After `git worktree add`, the Git directory matches `.git/worktrees/**`, so Git activates the previously invisible filter and executes it during checkout.

Why it matters: a target-controlled repository executes arbitrary code, and acquisition then returns success. This is a fail-open bypass of the correction’s central execution boundary.

### 2. P1 — Partial-clone hydration executes target-controlled transport commands

File: [diff_acquisition_v2.py:952](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:952)–958; [_sealed_git_execution_v2.py:226](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:226)–232.

Reproduction: R1 created a local `blob:none` partial clone with two missing blobs, then set:

```text
remote.origin.url=ext::<helper>
protocol.ext.allow=always
```

The helper wrote a marker and delegated only to a local `git upload-pack`; no network was touched.

Output:

```text
missing_object_count_before: 2
acquisition_outcome: success
marker_exists: true
marker_content: executed
```

Separate missing-object fixtures reproduced the named sinks:

```text
core.sshCommand: marker=true, outcome=DiffAcquisitionError:diff_unreadable
core.gitProxy: marker=true, outcome=DiffAcquisitionError:diff_unreadable
remote.origin.uploadpack: marker=true, outcome=success
```

The fake SSH and proxy helpers exited before opening a socket.

Why it matters: `git worktree add` hydrates missing objects. Repository-local remote/transport configuration therefore reaches arbitrary executables despite the hook/filter/fsmonitor clamps. In two variants acquisition still succeeded.

### 3. P1 — Toolrepo identity executes clean/process filters and can falsely certify dirty source

File: [toolrepo_identity_v2.py:267](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:267)–281.

Reproduction: R1 committed `app/victim.py` with benign bytes, changed the worktree to:

```text
VALUE = 'MALICIOUS_DIRTY_BYTES'
```

A local `filter.evil.clean` wrote a marker and emitted the committed bytes. The public identity function was then called.

Output:

```text
public_identity_outcome: returned
marker_exists: true
actual_worktree_bytes: "VALUE = 'MALICIOUS_DIRTY_BYTES'\n"
returned_sha == declared_head: true
```

A separate `filter.evil.process` probe also executed its marker before Git returned 128.

Why it matters: identity establishment itself executes arbitrary code, and a clean filter can make materially dirty executable source appear identical to `HEAD`.

### 4. P1 — `GIT_CONFIG_PARAMETERS` survives the sealed environment

File: [_sealed_git_execution_v2.py:129](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:129)–168; sink at [toolrepo_identity_v2.py:274](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:274)–277.

Reproduction: R1 injected `filter.evil.clean` through `GIT_CONFIG_PARAMETERS`.

Output:

```text
sealed_env_preserved_GIT_CONFIG_PARAMETERS: true
identity_clean_check_outcome: returned
marker_exists: true
actual_worktree_bytes: "VALUE = 'dirty'\n"
```

Why it matters: the correction still permits ambient ad-hoc Git configuration to introduce executable filters and defeat source identity. This is ambient rather than target-local, but directly contradicts the sealed-environment claim.

### 5. P1 — The `safe.directory` repair fails for relative and symlink repo roots

File: [_sealed_git_execution_v2.py:231](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:231); [operational_run_v2.py:274](/opt/agent-tools/ar-200d-successor/app/agent_review/operational_run_v2.py:274); CLI input at [aiops-review-run-v2.py:115](/opt/agent-tools/ar-200d-successor/scripts/aiops-review-run-v2.py:115).

`Path(repo_root)` is serialized without canonicalization.

Reproduction: R3 exercised Git’s different-owner path using `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`:

```text
safe.relative_path_rc: 128
safe.relative_path_dubious: true
safe.absolute_path_rc: 0
safe.symlink_path_rc: 128
safe.symlink_path_dubious: true
```

For the relative case, the generated option was literally `safe.directory=.`.

Why it matters: ordinary invocations such as `--repo-root .` or an absolute symlink still fail with “dubious ownership.” The claimed container/CI repair only works when the caller supplies an already-canonical absolute path.

### 6. P2 — Mutable local diff configuration changes authoritative output for identical SHAs

File: [_sealed_git_execution_v2.py:226](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:226)–232; [diff_acquisition_v2.py:1022](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1022)–1041 and [diff_acquisition_v2.py:1391](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1391)–1399.

Reproduction: R3 ran the same base/head pair before and after local-config mutations:

```text
diff.context=0:
  equal: false
  baseline hunk: @@ -1,7 +1,7 @@
  configured hunk: @@ -4 +4 @@ three

diff.evil.binary=true with committed "diff=evil":
  text_before: true
  binary_after: true
  equal: false
```

The second acquisition returned a `GIT binary patch`.

Why it matters: the disposable worktree shares `.git/config` with the target. The same declared commits do not define stable review bytes; local state can alter fragment boundaries or force text into binary handling. Raw correlation did not reject it.

### 7. P2 — Acquisition no longer works against read-only Git metadata

File: [diff_acquisition_v2.py:948](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:948)–980.

Reproduction: R3 made the fixture Git metadata read-only and dropped root’s DAC override/read-search capabilities.

Output:

```text
process.euid=0
git_dir.writable=False
direct.rc=0
direct.has_head=True
direct.stderr=''
acquire.error=diff_unreadable
.git/worktrees exists=False
```

Why it matters: a direct fixed `git diff` can review a read-only checkout, but the new implementation must register a linked worktree in the target’s common Git directory. Read-only bind mounts and similarly hardened CI checkouts now fail.

### 8. P2 — Caller-supplied Git-global options override the sealed policy

File: [_sealed_git_execution_v2.py:226](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:226)–232.

The policy is prepended, then unvalidated `argv[1:]` follows it.

Reproduction: R2:

```text
later-c-hooksPath: rc=0 marker=True
later-config-env-hooksPath: rc=0 marker=True
later-c-fsmonitor: rc=0 marker=True
later-c-attributesFile:
  normal_binary_patch=False
  overridden_binary_patch=True
trusted-repo-but-caller-C-other:
  rc=0
  out=<different repository>
```

A later `-c safe.directory=*` was retained as a second additive value.

Why it matters: `sealed_git_argv_v2` is not itself a sealed boundary. Any present or future caller that passes Git-global `-c`, `--config-env`, or `-C` can reinstate execution sinks or redirect the command.

I verified this is currently latent: none of the 12 production call shapes supplies those global options.

### 9. P2 — Stripping ambient alternate object directories breaks legitimate repositories

File: [_sealed_git_execution_v2.py:135](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:135).

Reproduction: R3 made the requested objects available only through `GIT_ALTERNATE_OBJECT_DIRECTORIES`.

Output:

```text
direct git diff: rc=0, has_head=True
unsealed worktree add: rc=0
sealed acquisition: diff_unreadable
```

Writing the same path into `.git/objects/info/alternates` restored acquisition.

Why it matters: this may be an intentional security tradeoff, but it breaks real CI/object-cache deployments relying on an ambient alternate database.

### 10. P2 — Blanket filter refusal rejects ordinary local Git LFS configuration

File: [_sealed_git_execution_v2.py:245](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:245)–285; refusal at [diff_acquisition_v2.py:941](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:941)–946.

Reproduction: R3 first acquired an ordinary repository successfully, then added standard local `filter.lfs.clean`, `smudge`, `process`, and `required` settings. No reviewed path used LFS attributes.

Output:

```text
lfs.baseline: true
lfs.local_config_error: diff_local_filter_config_active
```

Why it matters: repositories that ran `git lfs install --local` are rejected even when the requested commits and paths do not use the driver.

### 11. P3 — The helper rejects a pinned Git executable but still trusts ambient `PATH`

File: [_sealed_git_execution_v2.py:151](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:151)–168 and [line 224](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:224)–232.

Reproduction: R2:

```text
which-git='/usr/bin/git'
token='/usr/bin/git': ValueError: expects an argv beginning with 'git'
token='git': rc=0
literal-git-via-PATH: rc=0 out='fake git' marker=True
```

Why it matters: a deployment cannot pin `/usr/bin/git`, while literal `git` remains resolved through inherited `PATH`. No current production caller uses an absolute executable, so this is an operational/API defect rather than a presently target-controlled path.

## Required-question closure

1. **Is hooksPath sufficient?** No. Conditional filters, partial-clone transport commands, toolrepo clean/process filters, and ambient `GIT_CONFIG_PARAMETERS` all executed.

   In fully materialized ordinary repositories, my probes did not execute `core.pager`, `core.editor`, `credential.helper`, `diff.external`, diff command/textconv, merge drivers, `uploadpack.packObjectsHook`, `core.alternateRefsCommand`, fsmonitor, or hooks. A custom submodule update command also stayed unexecuted. These negatives apply only to the commands and repository states tested; partial-clone state made SSH/proxy/uploadpack commands reachable.

2. **Does argv insertion break existing command positions?** No positional failure was reproduced on Git 2.39.5. All 12 current production shapes returned successfully: config listing, `rev-parse --git-path`, unified/raw diff, `ls-tree`, `cat-file`, toolrepo `rev-parse`, recursive `ls-tree`, untracked enumeration, bounded diff, and worktree add/remove.

   Existing caller-global `-C`/`-c` orders also parsed, but they override or redirect the seal as reported in finding 8. A fix must distinguish Git-global options from valid subcommand-local options such as `git grep -c` and `git grep -C`.

3. **Can the disposable worktree fail open or leak state?** Yes through shared/re-evaluated configuration: conditional `includeIf.gitdir` executes only after the linked worktree exists, and ordinary local diff configuration changes authoritative output. I did **not** reproduce fallback to the target working tree itself.

   A target checkout polluted with working-tree `.gitattributes` produced byte-identical canonical output; `core.worktree` redirection did not bypass isolation; an invalid full SHA failed closed; linked-worktree input succeeded and ignored its untracked attribute change. Successful ordinary runs restored the original worktree-registration count.

9. **Does sealing break realistic deployments?** Yes: relative/symlink foreign-owner paths, read-only Git metadata, ambient alternate object databases, and local LFS configuration all reproduced failures. A normal linked-worktree checkout succeeded. A benign local `core.hooksPath` was suppressed without blocking acquisition, and its hook marker remained absent.

## SUSPECTED / could not test

No unconfirmed finding is reported.

Two requested cases could not be tested end-to-end:

- An actual HTTP `credential.helper` trigger would require network access, which the review explicitly prohibited.
- A truly foreign-UID full acquisition could not be created because this filesystem returned `EINVAL` for ownership/UID changes; the repository’s own foreign-owner tests skipped for the same reason. I reproduced the relevant Git ownership path with `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`, including canonical, relative, and symlink comparisons.

Final integrity check:

```text
HEAD: c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
git status --porcelain: <empty>
diff stat: 15 files changed, 5555 insertions(+), 30 deletions(-)
```

The subject worktree was not modified.
diff --git a/main-fixtures/conditional-filter-smudge.sh b/main-fixtures/conditional-filter-smudge.sh
new file mode 100644
index 0000000000000000000000000000000000000000..6949738fa7adc9200979bad4a60b33a7352aaa23
--- /dev/null
+++ b/main-fixtures/conditional-filter-smudge.sh
@@ -0,0 +1,3 @@
+#!/bin/sh
+touch /tmp/ar274-review-r3/laneA/main-fixtures/conditional-filter-ran
+cat
diff --git a/main-fixtures/conditional-filter.cfg b/main-fixtures/conditional-filter.cfg
new file mode 100644
index 0000000000000000000000000000000000000000..0fe0bc9b3dd23f45e4ed64f98463367c8db53786
--- /dev/null
+++ b/main-fixtures/conditional-filter.cfg
@@ -0,0 +1,2 @@
+[core]
+    worktree = /tmp/ar274-review-r3/laneA/main-fixtures/evil-worktree
diff --git a/main-fixtures/conditional-filter/.gitattributes b/main-fixtures/conditional-filter/.gitattributes
new file mode 100644
index 0000000000000000000000000000000000000000..17cd3a9b5baaa44a2df5ac9137b15c79238d7ca5
--- /dev/null
+++ b/main-fixtures/conditional-filter/.gitattributes
@@ -0,0 +1 @@
+f.txt filter=evil
diff --git a/main-fixtures/conditional-filter/f.txt b/main-fixtures/conditional-filter/f.txt
new file mode 100644
index 0000000000000000000000000000000000000000..564b12f45becba5fb2f70e270af067c1f13b3aab
--- /dev/null
+++ b/main-fixtures/conditional-filter/f.txt
@@ -0,0 +1 @@
+head
diff --git a/main-fixtures/config-leak/f.txt b/main-fixtures/config-leak/f.txt
new file mode 100644
index 0000000000000000000000000000000000000000..c5d94d6d5c18fa95be64df0b6371633913c54338
--- /dev/null
+++ b/main-fixtures/config-leak/f.txt
@@ -0,0 +1,9 @@
+line1
+line2
+line3
+line4
+LINE5-CHANGED
+line6
+line7
+line8
+line9
diff --git a/main-fixtures/evil-worktree/.gitattributes b/main-fixtures/evil-worktree/.gitattributes
new file mode 100644
index 0000000000000000000000000000000000000000..8f675a44f9404cd521c130f2c4f631ae4ec8ccb2
--- /dev/null
+++ b/main-fixtures/evil-worktree/.gitattributes
@@ -0,0 +1 @@
+f.txt -diff
diff --git a/main-fixtures/execution-matrix/.git/objects/info/alternates b/main-fixtures/execution-matrix/.git/objects/info/alternates
new file mode 100644
index 0000000000000000000000000000000000000000..79aec8bea48ba406e12adfcc04f003d7465f47df
--- /dev/null
+++ b/main-fixtures/execution-matrix/.git/objects/info/alternates
@@ -0,0 +1 @@
+/tmp/ar274-review-r3/laneA/main-fixtures/alternate-ref-source/.git/objects
diff --git a/main-fixtures/execution-matrix/.gitattributes b/main-fixtures/execution-matrix/.gitattributes
new file mode 100644
index 0000000000000000000000000000000000000000..a7fd8451b1a061e0bffe23fe47706e1d945c0265
--- /dev/null
+++ b/main-fixtures/execution-matrix/.gitattributes
@@ -0,0 +1 @@
+f.txt diff=evil merge=evil
diff --git a/main-fixtures/execution-matrix/f.txt b/main-fixtures/execution-matrix/f.txt
new file mode 100644
index 0000000000000000000000000000000000000000..564b12f45becba5fb2f70e270af067c1f13b3aab
--- /dev/null
+++ b/main-fixtures/execution-matrix/f.txt
@@ -0,0 +1 @@
+head
diff --git a/main-fixtures/execution-probe.sh b/main-fixtures/execution-probe.sh
new file mode 100644
index 0000000000000000000000000000000000000000..fef91f690d59d03619b2f3534f37d26b10043b6a
--- /dev/null
+++ b/main-fixtures/execution-probe.sh
@@ -0,0 +1,7 @@
+#!/bin/sh
+touch /tmp/ar274-review-r3/laneA/main-fixtures/execution-probe-ran
+if test "$#" -ge 1 && test -f "$1"; then
+    cat "$1"
+else
+    cat
+fi
diff --git a/main-fixtures/fake_uid.c b/main-fixtures/fake_uid.c
new file mode 100644
index 0000000000000000000000000000000000000000..e276e5698c67526abe286096bf480a0cc2e001e8
--- /dev/null
+++ b/main-fixtures/fake_uid.c
@@ -0,0 +1,10 @@
+#include <sys/types.h>
+#include <unistd.h>
+
+uid_t getuid(void) {
+    return (uid_t)12345;
+}
+
+uid_t geteuid(void) {
+    return (uid_t)12345;
+}
diff --git a/main-fixtures/fakeuid_acquire_probe.py b/main-fixtures/fakeuid_acquire_probe.py
new file mode 100644
index 0000000000000000000000000000000000000000..4bd68b76a69838ddf1eea2726d5469f0f4e43150
--- /dev/null
+++ b/main-fixtures/fakeuid_acquire_probe.py
@@ -0,0 +1,15 @@
+from pathlib import Path
+
+from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError, acquire_diff_v2
+
+repo = Path("/tmp/ar274-review-r3/laneA/main-fixtures/config-leak")
+try:
+    result = acquire_diff_v2(
+        repo,
+        base_sha="9dc2ae6dd2360ebaeec2859f47d61cdcab16ced5",
+        head_sha="fa8f5945ae34f0111c3876e3f0cf0e3c0a14e092",
+    )
+except DiffAcquisitionError as exc:
+    print("actual_acquire=REFUSED", exc.reason_code)
+else:
+    print("actual_acquire=SUCCESS", len(result))
diff --git a/main-fixtures/foreign_owner_probe.sh b/main-fixtures/foreign_owner_probe.sh
new file mode 100644
index 0000000000000000000000000000000000000000..9ae5cfef461626a1abbed53e573cc3b5eb70be87
--- /dev/null
+++ b/main-fixtures/foreign_owner_probe.sh
@@ -0,0 +1,30 @@
+#!/bin/sh
+set -eu
+
+repo=/tmp/ar274-review-r3/laneA/main-fixtures/config-leak
+chown -R 65534:65534 "$repo"
+stat -c 'simulated_repo_owner=%u:%g' "$repo"
+
+set +e
+plain_output=$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
+    git --no-optional-locks -C "$repo" -c core.fsmonitor=false status --short \
+    2>&1)
+plain_rc=$?
+set -e
+printf 'unsealed_status_rc=%s\n' "$plain_rc"
+printf '%s\n' "$plain_output" | sed -n '1p'
+
+GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
+    git --no-optional-locks -C "$repo" \
+    -c core.hooksPath=/dev/null \
+    -c core.fsmonitor=false \
+    -c core.attributesFile=/dev/null \
+    -c safe.directory="$repo" \
+    status --short
+printf 'sealed_main_status_rc=%s\n' "$?"
+
+TMPDIR=/tmp/ar274-review-r3/laneA \
+PYTHONDONTWRITEBYTECODE=1 \
+PYTHONPATH=/opt/agent-tools/ar-200d-successor \
+    /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
+    /tmp/ar274-review-r3/laneA/main-fixtures/fakeuid_acquire_probe.py
diff --git a/main-fixtures/override-hooks/post-checkout b/main-fixtures/override-hooks/post-checkout
new file mode 100644
index 0000000000000000000000000000000000000000..98f4cb8b0bd1d4f15e595fef95960e83d5d539c3
--- /dev/null
+++ b/main-fixtures/override-hooks/post-checkout
@@ -0,0 +1,2 @@
+#!/bin/sh
+touch /tmp/ar274-review-r3/laneA/main-fixtures/caller-override-hook-ran
diff --git a/main-fixtures/probe_execution_matrix.py b/main-fixtures/probe_execution_matrix.py
new file mode 100644
index 0000000000000000000000000000000000000000..529868b608e961385155ed1d8bd4c8f45625cd62
--- /dev/null
+++ b/main-fixtures/probe_execution_matrix.py
@@ -0,0 +1,47 @@
+from pathlib import Path
+import subprocess
+
+from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError, acquire_diff_v2
+
+repo = Path("/tmp/ar274-review-r3/laneA/main-fixtures/execution-matrix")
+marker = Path("/tmp/ar274-review-r3/laneA/main-fixtures/execution-probe-ran")
+probe = "/tmp/ar274-review-r3/laneA/main-fixtures/execution-probe.sh"
+base = "443b8bbb35ea7c3d94f000a2675387d8020d828c"
+head = "caddc3daf50261a0fd0864db3cad969d64118966"
+
+cases = {
+    "core.pager": [("core.pager", probe), ("pager.diff", "true")],
+    "core.editor": [("core.editor", probe)],
+    "sequence.editor": [("sequence.editor", probe)],
+    "core.sshCommand": [("core.sshCommand", probe)],
+    "credential.helper": [("credential.helper", f"!{probe}")],
+    "diff.external": [("diff.external", probe)],
+    "diff.evil.textconv": [("diff.evil.textconv", probe)],
+    "merge.evil.driver": [("merge.evil.driver", probe)],
+    "uploadpack.packObjectsHook": [("uploadpack.packObjectsHook", probe)],
+    "core.gitProxy": [("core.gitProxy", probe)],
+    "core.alternateRefsCommand": [("core.alternateRefsCommand", probe)],
+    "core.fsmonitor": [("core.fsmonitor", probe)],
+    "filter.evil.smudge": [("filter.evil.smudge", probe)],
+}
+
+all_keys = sorted({key for settings in cases.values() for key, _ in settings})
+for case_name, settings in cases.items():
+    for key in all_keys:
+        subprocess.run(
+            ["git", "config", "--local", "--unset-all", key],
+            cwd=repo,
+            stdout=subprocess.DEVNULL,
+            stderr=subprocess.DEVNULL,
+            check=False,
+        )
+    for key, value in settings:
+        subprocess.run(["git", "config", "--local", key, value], cwd=repo, check=True)
+    marker.unlink(missing_ok=True)
+    try:
+        acquire_diff_v2(repo, base_sha=base, head_sha=head)
+    except DiffAcquisitionError as exc:
+        outcome = f"REFUSED:{exc.reason_code}"
+    else:
+        outcome = "SUCCESS"
+    print(f"{case_name}: {outcome}; marker={'CREATED' if marker.exists() else 'ABSENT'}")
diff --git a/main-fixtures/run_conditional_probe.py b/main-fixtures/run_conditional_probe.py
new file mode 100644
index 0000000000000000000000000000000000000000..de9a447050241dce6808699bd3689b6ef33f5f97
--- /dev/null
+++ b/main-fixtures/run_conditional_probe.py
@@ -0,0 +1,23 @@
+from pathlib import Path
+
+from app.agent_review._sealed_git_execution_v2 import (
+    has_executable_local_filter_config_v2,
+    sealed_git_child_env_v2,
+)
+from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError, acquire_diff_v2
+
+repo = Path("/tmp/ar274-review-r3/laneA/main-fixtures/conditional-filter")
+print(
+    "pre_detector=",
+    has_executable_local_filter_config_v2(repo, env=sealed_git_child_env_v2()),
+)
+try:
+    diff = acquire_diff_v2(
+        repo,
+        base_sha="c4b9b9ebd52bea9418f7061245bdc4d9e2d58bc5",
+        head_sha="99e3c0a19b232ee058edfb95fd7d269cbc3ac66d",
+    )
+except DiffAcquisitionError as exc:
+    print("acquire=REFUSED", exc.reason_code)
+else:
+    print("acquire=SUCCESS", len(diff))
diff --git a/main-fixtures/toolrepo-filter-clean.sh b/main-fixtures/toolrepo-filter-clean.sh
new file mode 100644
index 0000000000000000000000000000000000000000..3a356354f068c24c0d0e329b00642fc47aeffe65
--- /dev/null
+++ b/main-fixtures/toolrepo-filter-clean.sh
@@ -0,0 +1,3 @@
+#!/bin/sh
+touch /tmp/ar274-review-r3/laneA/main-fixtures/toolrepo-clean-filter-ran
+printf 'VALUE = "committed"\n'
diff --git a/main-fixtures/toolrepo-filter/.gitattributes b/main-fixtures/toolrepo-filter/.gitattributes
new file mode 100644
index 0000000000000000000000000000000000000000..b7f3febdd4497e07fc1c984d82aa8b231040d89a
--- /dev/null
+++ b/main-fixtures/toolrepo-filter/.gitattributes
@@ -0,0 +1 @@
+app/engine.py filter=evil
diff --git a/main-fixtures/toolrepo-filter/app/__init__.py b/main-fixtures/toolrepo-filter/app/__init__.py
new file mode 100644
index 0000000000000000000000000000000000000000..30d4d9902c98bdb47456730d89949d880551d0a8
--- /dev/null
+++ b/main-fixtures/toolrepo-filter/app/__init__.py
@@ -0,0 +1 @@
+"""Fixture package."""
diff --git a/main-fixtures/toolrepo-filter/app/engine.py b/main-fixtures/toolrepo-filter/app/engine.py
new file mode 100644
index 0000000000000000000000000000000000000000..024b53b6e8bfb6d12965da99205a499cbd889f11
--- /dev/null
+++ b/main-fixtures/toolrepo-filter/app/engine.py
@@ -0,0 +1 @@
+VALUE = "tampered and importable"
diff --git a/main-fixtures/toolrepo-filter/scripts/aiops-review-run-v2.py b/main-fixtures/toolrepo-filter/scripts/aiops-review-run-v2.py
new file mode 100644
index 0000000000000000000000000000000000000000..601e890da05be6b1897df2bf412413e8bcc341af
--- /dev/null
+++ b/main-fixtures/toolrepo-filter/scripts/aiops-review-run-v2.py
@@ -0,0 +1,2 @@
+#!/usr/bin/env python3
+print("fixture")

tokens used
348,574
# Lane A verdict

**Block.** `-c core.hooksPath=/dev/null` is not sufficient. I reproduced four code-execution paths, including two target-controlled acquisition paths that finish successfully after executing attacker-selected code.

Severity summary: **P0: none; P1: 5; P2: 5; P3: 1.**

Reproduction harnesses, all scratch-only:

```bash
# R1 — execution surface
env TMPDIR=/tmp/ar274-review-r3/laneA/q1-agent \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/opt/agent-tools/ar-200d-successor \
  /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
  /tmp/ar274-review-r3/laneA/q1-agent/probe_q1.py

# R2 — argv positioning and overrides
env TMPDIR=/tmp/ar274-review-r3/laneA/q2-agent \
  PYTHONDONTWRITEBYTECODE=1 \
  /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
  /tmp/ar274-review-r3/laneA/q2-agent/repro_q2.py

# R3 — worktree isolation and operational behavior
env TMPDIR=/tmp/ar274-review-r3/laneA/q3q9-agent \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/opt/agent-tools/ar-200d-successor \
  /opt/agent-tools/aiops-orchestrator-toolrepo/.venv/bin/python \
  /tmp/ar274-review-r3/laneA/q3q9-agent/probe_q3q9.py
```

The harnesses are [probe_q1.py](/tmp/ar274-review-r3/laneA/q1-agent/probe_q1.py), [Q1 JSON output](/tmp/ar274-review-r3/laneA/q1-agent/probe_q1_output.json), [repro_q2.py](/tmp/ar274-review-r3/laneA/q2-agent/repro_q2.py), and [probe_q3q9.py](/tmp/ar274-review-r3/laneA/q3q9-agent/probe_q3q9.py).

## CONFIRMED

### 1. P1 — `includeIf.gitdir` bypasses the filter detector and executes during acquisition

File: [_sealed_git_execution_v2.py:271](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:271)–276; [diff_acquisition_v2.py:938](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:938)–958.

Reproduction: R1 configured:

```text
includeIf.gitdir:<repo>/.git/worktrees/**.path=<included-config>
```

The included config defined `filter.evil.smudge=<marker-and-cat script>`, and committed `.gitattributes` selected that filter.

Output:

```text
detector_before_worktree: false
acquisition_outcome: success
marker_exists: true
marker_content: executed
```

The detector evaluates includes from the primary checkout. After `git worktree add`, the Git directory matches `.git/worktrees/**`, so Git activates the previously invisible filter and executes it during checkout.

Why it matters: a target-controlled repository executes arbitrary code, and acquisition then returns success. This is a fail-open bypass of the correction’s central execution boundary.

### 2. P1 — Partial-clone hydration executes target-controlled transport commands

File: [diff_acquisition_v2.py:952](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:952)–958; [_sealed_git_execution_v2.py:226](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:226)–232.

Reproduction: R1 created a local `blob:none` partial clone with two missing blobs, then set:

```text
remote.origin.url=ext::<helper>
protocol.ext.allow=always
```

The helper wrote a marker and delegated only to a local `git upload-pack`; no network was touched.

Output:

```text
missing_object_count_before: 2
acquisition_outcome: success
marker_exists: true
marker_content: executed
```

Separate missing-object fixtures reproduced the named sinks:

```text
core.sshCommand: marker=true, outcome=DiffAcquisitionError:diff_unreadable
core.gitProxy: marker=true, outcome=DiffAcquisitionError:diff_unreadable
remote.origin.uploadpack: marker=true, outcome=success
```

The fake SSH and proxy helpers exited before opening a socket.

Why it matters: `git worktree add` hydrates missing objects. Repository-local remote/transport configuration therefore reaches arbitrary executables despite the hook/filter/fsmonitor clamps. In two variants acquisition still succeeded.

### 3. P1 — Toolrepo identity executes clean/process filters and can falsely certify dirty source

File: [toolrepo_identity_v2.py:267](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:267)–281.

Reproduction: R1 committed `app/victim.py` with benign bytes, changed the worktree to:

```text
VALUE = 'MALICIOUS_DIRTY_BYTES'
```

A local `filter.evil.clean` wrote a marker and emitted the committed bytes. The public identity function was then called.

Output:

```text
public_identity_outcome: returned
marker_exists: true
actual_worktree_bytes: "VALUE = 'MALICIOUS_DIRTY_BYTES'\n"
returned_sha == declared_head: true
```

A separate `filter.evil.process` probe also executed its marker before Git returned 128.

Why it matters: identity establishment itself executes arbitrary code, and a clean filter can make materially dirty executable source appear identical to `HEAD`.

### 4. P1 — `GIT_CONFIG_PARAMETERS` survives the sealed environment

File: [_sealed_git_execution_v2.py:129](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:129)–168; sink at [toolrepo_identity_v2.py:274](/opt/agent-tools/ar-200d-successor/app/agent_review/toolrepo_identity_v2.py:274)–277.

Reproduction: R1 injected `filter.evil.clean` through `GIT_CONFIG_PARAMETERS`.

Output:

```text
sealed_env_preserved_GIT_CONFIG_PARAMETERS: true
identity_clean_check_outcome: returned
marker_exists: true
actual_worktree_bytes: "VALUE = 'dirty'\n"
```

Why it matters: the correction still permits ambient ad-hoc Git configuration to introduce executable filters and defeat source identity. This is ambient rather than target-local, but directly contradicts the sealed-environment claim.

### 5. P1 — The `safe.directory` repair fails for relative and symlink repo roots

File: [_sealed_git_execution_v2.py:231](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:231); [operational_run_v2.py:274](/opt/agent-tools/ar-200d-successor/app/agent_review/operational_run_v2.py:274); CLI input at [aiops-review-run-v2.py:115](/opt/agent-tools/ar-200d-successor/scripts/aiops-review-run-v2.py:115).

`Path(repo_root)` is serialized without canonicalization.

Reproduction: R3 exercised Git’s different-owner path using `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`:

```text
safe.relative_path_rc: 128
safe.relative_path_dubious: true
safe.absolute_path_rc: 0
safe.symlink_path_rc: 128
safe.symlink_path_dubious: true
```

For the relative case, the generated option was literally `safe.directory=.`.

Why it matters: ordinary invocations such as `--repo-root .` or an absolute symlink still fail with “dubious ownership.” The claimed container/CI repair only works when the caller supplies an already-canonical absolute path.

### 6. P2 — Mutable local diff configuration changes authoritative output for identical SHAs

File: [_sealed_git_execution_v2.py:226](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:226)–232; [diff_acquisition_v2.py:1022](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1022)–1041 and [diff_acquisition_v2.py:1391](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:1391)–1399.

Reproduction: R3 ran the same base/head pair before and after local-config mutations:

```text
diff.context=0:
  equal: false
  baseline hunk: @@ -1,7 +1,7 @@
  configured hunk: @@ -4 +4 @@ three

diff.evil.binary=true with committed "diff=evil":
  text_before: true
  binary_after: true
  equal: false
```

The second acquisition returned a `GIT binary patch`.

Why it matters: the disposable worktree shares `.git/config` with the target. The same declared commits do not define stable review bytes; local state can alter fragment boundaries or force text into binary handling. Raw correlation did not reject it.

### 7. P2 — Acquisition no longer works against read-only Git metadata

File: [diff_acquisition_v2.py:948](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:948)–980.

Reproduction: R3 made the fixture Git metadata read-only and dropped root’s DAC override/read-search capabilities.

Output:

```text
process.euid=0
git_dir.writable=False
direct.rc=0
direct.has_head=True
direct.stderr=''
acquire.error=diff_unreadable
.git/worktrees exists=False
```

Why it matters: a direct fixed `git diff` can review a read-only checkout, but the new implementation must register a linked worktree in the target’s common Git directory. Read-only bind mounts and similarly hardened CI checkouts now fail.

### 8. P2 — Caller-supplied Git-global options override the sealed policy

File: [_sealed_git_execution_v2.py:226](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:226)–232.

The policy is prepended, then unvalidated `argv[1:]` follows it.

Reproduction: R2:

```text
later-c-hooksPath: rc=0 marker=True
later-config-env-hooksPath: rc=0 marker=True
later-c-fsmonitor: rc=0 marker=True
later-c-attributesFile:
  normal_binary_patch=False
  overridden_binary_patch=True
trusted-repo-but-caller-C-other:
  rc=0
  out=<different repository>
```

A later `-c safe.directory=*` was retained as a second additive value.

Why it matters: `sealed_git_argv_v2` is not itself a sealed boundary. Any present or future caller that passes Git-global `-c`, `--config-env`, or `-C` can reinstate execution sinks or redirect the command.

I verified this is currently latent: none of the 12 production call shapes supplies those global options.

### 9. P2 — Stripping ambient alternate object directories breaks legitimate repositories

File: [_sealed_git_execution_v2.py:135](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:135).

Reproduction: R3 made the requested objects available only through `GIT_ALTERNATE_OBJECT_DIRECTORIES`.

Output:

```text
direct git diff: rc=0, has_head=True
unsealed worktree add: rc=0
sealed acquisition: diff_unreadable
```

Writing the same path into `.git/objects/info/alternates` restored acquisition.

Why it matters: this may be an intentional security tradeoff, but it breaks real CI/object-cache deployments relying on an ambient alternate database.

### 10. P2 — Blanket filter refusal rejects ordinary local Git LFS configuration

File: [_sealed_git_execution_v2.py:245](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:245)–285; refusal at [diff_acquisition_v2.py:941](/opt/agent-tools/ar-200d-successor/app/agent_review/diff_acquisition_v2.py:941)–946.

Reproduction: R3 first acquired an ordinary repository successfully, then added standard local `filter.lfs.clean`, `smudge`, `process`, and `required` settings. No reviewed path used LFS attributes.

Output:

```text
lfs.baseline: true
lfs.local_config_error: diff_local_filter_config_active
```

Why it matters: repositories that ran `git lfs install --local` are rejected even when the requested commits and paths do not use the driver.

### 11. P3 — The helper rejects a pinned Git executable but still trusts ambient `PATH`

File: [_sealed_git_execution_v2.py:151](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:151)–168 and [line 224](/opt/agent-tools/ar-200d-successor/app/agent_review/_sealed_git_execution_v2.py:224)–232.

Reproduction: R2:

```text
which-git='/usr/bin/git'
token='/usr/bin/git': ValueError: expects an argv beginning with 'git'
token='git': rc=0
literal-git-via-PATH: rc=0 out='fake git' marker=True
```

Why it matters: a deployment cannot pin `/usr/bin/git`, while literal `git` remains resolved through inherited `PATH`. No current production caller uses an absolute executable, so this is an operational/API defect rather than a presently target-controlled path.

## Required-question closure

1. **Is hooksPath sufficient?** No. Conditional filters, partial-clone transport commands, toolrepo clean/process filters, and ambient `GIT_CONFIG_PARAMETERS` all executed.

   In fully materialized ordinary repositories, my probes did not execute `core.pager`, `core.editor`, `credential.helper`, `diff.external`, diff command/textconv, merge drivers, `uploadpack.packObjectsHook`, `core.alternateRefsCommand`, fsmonitor, or hooks. A custom submodule update command also stayed unexecuted. These negatives apply only to the commands and repository states tested; partial-clone state made SSH/proxy/uploadpack commands reachable.

2. **Does argv insertion break existing command positions?** No positional failure was reproduced on Git 2.39.5. All 12 current production shapes returned successfully: config listing, `rev-parse --git-path`, unified/raw diff, `ls-tree`, `cat-file`, toolrepo `rev-parse`, recursive `ls-tree`, untracked enumeration, bounded diff, and worktree add/remove.

   Existing caller-global `-C`/`-c` orders also parsed, but they override or redirect the seal as reported in finding 8. A fix must distinguish Git-global options from valid subcommand-local options such as `git grep -c` and `git grep -C`.

3. **Can the disposable worktree fail open or leak state?** Yes through shared/re-evaluated configuration: conditional `includeIf.gitdir` executes only after the linked worktree exists, and ordinary local diff configuration changes authoritative output. I did **not** reproduce fallback to the target working tree itself.

   A target checkout polluted with working-tree `.gitattributes` produced byte-identical canonical output; `core.worktree` redirection did not bypass isolation; an invalid full SHA failed closed; linked-worktree input succeeded and ignored its untracked attribute change. Successful ordinary runs restored the original worktree-registration count.

9. **Does sealing break realistic deployments?** Yes: relative/symlink foreign-owner paths, read-only Git metadata, ambient alternate object databases, and local LFS configuration all reproduced failures. A normal linked-worktree checkout succeeded. A benign local `core.hooksPath` was suppressed without blocking acquisition, and its hook marker remained absent.

## SUSPECTED / could not test

No unconfirmed finding is reported.

Two requested cases could not be tested end-to-end:

- An actual HTTP `credential.helper` trigger would require network access, which the review explicitly prohibited.
- A truly foreign-UID full acquisition could not be created because this filesystem returned `EINVAL` for ownership/UID changes; the repository’s own foreign-owner tests skipped for the same reason. I reproduced the relevant Git ownership path with `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`, including canonical, relative, and symlink comparisons.

Final integrity check:

```text
HEAD: c68a8b9a6b4d57383918f7fc1fa6a85536e331c6
git status --porcelain: <empty>
diff stat: 15 files changed, 5555 insertions(+), 30 deletions(-)
```

The subject worktree was not modified.
EXIT=0
