from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


REPO = Path("/tmp/ar274-review-r3/laneC/q10/config-semantics").resolve()


def child_main() -> None:
    from app.agent_review.diff_acquisition_v2 import _attribute_bound_diff_worktree_v2

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()
    with _attribute_bound_diff_worktree_v2(REPO, head) as worktree:
        print(worktree.resolve(), flush=True)
        time.sleep(300)


if sys.argv[1:] == ["--child"]:
    child_main()
    raise SystemExit

env = dict(os.environ)
env["TMPDIR"] = str(REPO / ".review-tmp")
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PYTHONPATH"] = "/opt/agent-tools/ar-200d-successor"
child = subprocess.Popen(
    [sys.executable, "-B", __file__, "--child"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env=env,
)
assert child.stdout is not None
line = child.stdout.readline().strip()
worktree = Path(line)
print(f"CHILD_WORKTREE={worktree}")
print(f"INSIDE_TARGET={worktree.is_relative_to(REPO)}")
print(f"EXISTS_BEFORE_KILL={worktree.exists()}")
child.send_signal(signal.SIGKILL)
returncode = child.wait(timeout=10)
print(f"CHILD_RETURN={returncode}")
print(f"EXISTS_AFTER_KILL={worktree.exists()}")
status = subprocess.run(
    ["git", "status", "--porcelain=v1", "-uall", "--ignored=matching"],
    cwd=REPO,
    check=True,
    capture_output=True,
    text=True,
)
print("STATUS_AFTER_KILL_BEGIN")
print(status.stdout.rstrip())
print("STATUS_AFTER_KILL_END")
listed = subprocess.run(
    ["git", "worktree", "list", "--porcelain"],
    cwd=REPO,
    check=True,
    capture_output=True,
    text=True,
)
print("WORKTREES_AFTER_KILL_BEGIN")
print(listed.stdout.rstrip())
print("WORKTREES_AFTER_KILL_END")
