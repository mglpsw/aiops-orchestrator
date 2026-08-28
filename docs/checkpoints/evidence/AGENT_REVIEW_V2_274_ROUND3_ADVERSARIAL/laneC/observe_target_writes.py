from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.agent_review.diff_acquisition_v2 import acquire_diff_v2


scratch = Path("/tmp/ar274-review-r3/laneC/main/fixtures/q7-target")
scratch.mkdir(parents=True, exist_ok=True)
repo = scratch / "repo"
repo.mkdir()


def git(*args: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


git("init", "--quiet", "-b", "main", ".")
git("config", "user.email", "review@example.invalid")
git("config", "user.name", "review")
(repo / "ordinary.txt").write_text("base\n", encoding="utf-8")
git("add", "ordinary.txt")
git("commit", "--quiet", "-m", "base")
base = git("rev-parse", "HEAD")
(repo / "ordinary.txt").write_text("head\n", encoding="utf-8")
git("add", "ordinary.txt")
git("commit", "--quiet", "-m", "head")
head = git("rev-parse", "HEAD")

status_before = git("status", "--porcelain=v1", "--untracked-files=all")
worktrees_before = sorted((repo / ".git" / "worktrees").rglob("*")) if (repo / ".git" / "worktrees").exists() else []

os.environ["TRACE_TARGET_GITDIR"] = str(repo / ".git")
os.environ["TRACE_LOG"] = "/tmp/ar274-review-r3/laneC/main/logs/worktree-write.log"
os.environ["PATH"] = "/tmp/ar274-review-r3/laneC/main/bin:" + os.environ["PATH"]

diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)

status_after = git("status", "--porcelain=v1", "--untracked-files=all")
worktrees_after = sorted((repo / ".git" / "worktrees").rglob("*")) if (repo / ".git" / "worktrees").exists() else []

print(f"base={base}")
print(f"head={head}")
print(f"diff_has_hunk={'@@' in diff}")
print(f"status_before={status_before!r}")
print(f"status_after={status_after!r}")
print(f"worktree_admin_paths_before={[str(path.relative_to(repo)) for path in worktrees_before]}")
print(f"worktree_admin_paths_after={[str(path.relative_to(repo)) for path in worktrees_after]}")
print(Path(os.environ["TRACE_LOG"]).read_text(encoding="utf-8"))
