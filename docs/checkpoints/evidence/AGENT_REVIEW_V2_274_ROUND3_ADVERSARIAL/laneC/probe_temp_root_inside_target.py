from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.agent_review.diff_acquisition_v2 import _attribute_bound_diff_worktree_v2


repo = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ar274-review-r3/laneC/q7/target")
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
).stdout.strip()


def oracle() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain=v1", "-uall", "--ignored=matching"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


print(f"PROCESS_TMPDIR={os.environ.get('TMPDIR')}")
# Equivalent to Python having selected repo as its ambient tempfile root,
# while retaining the review harness's mandated process TMPDIR.
tempfile.tempdir = str(repo)
print(f"PYTHON_TEMP_ROOT={tempfile.gettempdir()}")
print(f"BEFORE={oracle()!r}")
with _attribute_bound_diff_worktree_v2(repo, head) as disposable:
    print(f"DISPOSABLE={disposable}")
    print(f"INSIDE={oracle()!r}")
print(f"AFTER={oracle()!r}")
