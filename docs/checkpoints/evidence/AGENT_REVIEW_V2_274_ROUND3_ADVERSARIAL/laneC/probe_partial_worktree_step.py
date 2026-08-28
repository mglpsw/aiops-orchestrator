from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.agent_review.diff_acquisition_v2 import _attribute_bound_diff_worktree_v2


repo = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ar274-review-r3/laneC/q7/partial2")
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
).stdout.strip()


def missing() -> list[str]:
    result = subprocess.run(
        ["git", "rev-list", "--objects", "--missing=print", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line for line in result.stdout.splitlines() if line.startswith("?"))


def pack_names() -> list[str]:
    return sorted(path.name for path in (repo / ".git" / "objects" / "pack").glob("*"))


print(f"MISSING_BEFORE={missing()!r}")
print(f"PACKS_BEFORE={pack_names()!r}")
with _attribute_bound_diff_worktree_v2(repo, head) as disposable:
    print(f"DISPOSABLE_FILE={(disposable / 'sample.txt').read_text()!r}")
    print(f"MISSING_INSIDE={missing()!r}")
    print(f"PACKS_INSIDE={pack_names()!r}")
print(f"MISSING_AFTER={missing()!r}")
print(f"PACKS_AFTER={pack_names()!r}")
