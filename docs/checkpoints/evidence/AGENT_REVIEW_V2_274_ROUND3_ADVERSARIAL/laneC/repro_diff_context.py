from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from app.agent_review.diff_acquisition_v2 import (
    _RENAME_COPY_DETECTION_ARGS_V2,
    _attribute_bound_diff_worktree_v2,
    _run_git_v2,
    acquire_authoritative_diff_v2,
    acquire_diff_v2,
)


repo = Path("/tmp/ar274-review-r3/laneC/q10/config-semantics")
base = subprocess.run(
    ["git", "rev-parse", "HEAD^"], cwd=repo, check=True, capture_output=True, text=True
).stdout.strip()
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
).stdout.strip()

subprocess.run(["git", "config", "--unset-all", "diff.context"], cwd=repo, check=False)
default = acquire_diff_v2(repo, base_sha=base, head_sha=head)
default_authoritative = acquire_authoritative_diff_v2(repo, base_sha=base, head_sha=head)
subprocess.run(["git", "config", "diff.context", "0"], cwd=repo, check=True)
zero = acquire_diff_v2(repo, base_sha=base, head_sha=head)
zero_authoritative = acquire_authoritative_diff_v2(repo, base_sha=base, head_sha=head)
with _attribute_bound_diff_worktree_v2(repo, head) as worktree:
    forced_result = _run_git_v2(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "--unified=3",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            *_RENAME_COPY_DETECTION_ARGS_V2,
            f"{base}...{head}",
        ],
        repo_root=repo,
        cwd=worktree,
    )
forced = forced_result.stdout.decode("utf-8")

print(f"SAME_SHA_RANGE={base}...{head}")
print(f"DEFAULT_SHA256={hashlib.sha256(default.encode()).hexdigest()}")
print(f"CONTEXT0_SHA256={hashlib.sha256(zero.encode()).hexdigest()}")
print(f"OUTPUTS_EQUAL={default == zero}")
print("DEFAULT_HUNK=" + next(line for line in default.splitlines() if line.startswith("@@")))
print("CONTEXT0_HUNK=" + next(line for line in zero.splitlines() if line.startswith("@@")))
print(f"AUTHORITATIVE_EQUAL={default_authoritative == zero_authoritative}")
print(f"DEFAULT_PARSED_HUNK={default_authoritative[0].hunks[0]}")
print(f"CONTEXT0_PARSED_HUNK={zero_authoritative[0].hunks[0]}")
print(f"FORCED_U3_RC={forced_result.returncode}")
print(f"FORCED_U3_EQUALS_DEFAULT={forced == default}")
print(f"FORCED_U3_SHA256={hashlib.sha256(forced.encode()).hexdigest()}")
