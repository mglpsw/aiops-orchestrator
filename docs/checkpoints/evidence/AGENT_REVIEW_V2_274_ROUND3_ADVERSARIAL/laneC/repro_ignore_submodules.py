from __future__ import annotations

import subprocess
from pathlib import Path

from app.agent_review.diff_acquisition_v2 import (
    _RENAME_COPY_DETECTION_ARGS_V2,
    _attribute_bound_diff_worktree_v2,
    _run_git_v2,
    acquire_authoritative_diff_v2,
    acquire_diff_v2,
    acquire_raw_diff_v2,
    correlate_raw_and_unified_v2,
    parse_raw_diff_z,
    parse_unified_diff,
)


repo = Path("/tmp/ar274-review-r3/laneC/q10/ignore-submodules/super")
base = subprocess.run(
    ["git", "rev-parse", "HEAD^"], cwd=repo, check=True, capture_output=True, text=True
).stdout.strip()
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
).stdout.strip()

subprocess.run(["git", "config", "--unset-all", "diff.ignoreSubmodules"], cwd=repo, check=False)
default_diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)
default_raw = acquire_raw_diff_v2(repo, base_sha=base, head_sha=head)
default_authoritative = acquire_authoritative_diff_v2(repo, base_sha=base, head_sha=head)

subprocess.run(["git", "config", "diff.ignoreSubmodules", "all"], cwd=repo, check=True)
ignored_diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)
ignored_raw = acquire_raw_diff_v2(repo, base_sha=base, head_sha=head)
ignored_authoritative = acquire_authoritative_diff_v2(repo, base_sha=base, head_sha=head)
with _attribute_bound_diff_worktree_v2(repo, head) as worktree:
    forced_diff = _run_git_v2(
        [
            "git", "diff", "--no-ext-diff", "--no-textconv", "--binary",
            "--ignore-submodules=none", "--src-prefix=a/", "--dst-prefix=b/",
            *_RENAME_COPY_DETECTION_ARGS_V2, f"{base}...{head}",
        ],
        repo_root=repo,
        cwd=worktree,
    ).stdout.decode("utf-8")
with _attribute_bound_diff_worktree_v2(repo, head) as worktree:
    forced_raw = _run_git_v2(
        [
            "git", "diff", "--no-ext-diff", "--raw", "-z",
            "--ignore-submodules=none", *_RENAME_COPY_DETECTION_ARGS_V2,
            f"{base}...{head}",
        ],
        repo_root=repo,
        cwd=worktree,
    ).stdout.decode("utf-8")
forced_parsed = parse_unified_diff(forced_diff)
correlate_raw_and_unified_v2(parse_raw_diff_z(forced_raw), forced_parsed)

print(f"RANGE={base}...{head}")
print(f"DEFAULT_DIFF={default_diff!r}")
print(f"DEFAULT_RAW={default_raw!r}")
print(f"DEFAULT_AUTHORITATIVE={default_authoritative!r}")
print(f"IGNORED_DIFF={ignored_diff!r}")
print(f"IGNORED_RAW={ignored_raw!r}")
print(f"IGNORED_AUTHORITATIVE={ignored_authoritative!r}")
print(f"FORCED_NONE_DIFF_EQUALS_DEFAULT={forced_diff == default_diff}")
print(f"FORCED_NONE_RAW_EQUALS_DEFAULT={forced_raw == default_raw}")
print(f"FORCED_NONE_PARSED={forced_parsed!r}")
