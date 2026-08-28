from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from app.agent_review.diff_acquisition_v2 import acquire_diff_v2


repo = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ar274-review-r3/laneC/q7/partial")
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
).stdout.strip()
base = subprocess.run(
    ["git", "rev-parse", "HEAD^"], cwd=repo, capture_output=True, text=True, check=True
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


def packs() -> list[tuple[str, int, str]]:
    answer = []
    for path in sorted((repo / ".git" / "objects" / "pack").glob("*")):
        if path.is_file():
            answer.append((path.name, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()))
    return answer


def status_oracle() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "-uall", "--ignored=matching"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


print(f"MISSING_BEFORE={missing()!r}")
print(f"PACKS_BEFORE={packs()!r}")
print(f"STATUS_BEFORE={status_oracle()!r}")
diff = acquire_diff_v2(repo, base_sha=base, head_sha=head)
print(f"DIFF_PREFIX={diff.splitlines()[:4]!r}")
print(f"MISSING_AFTER={missing()!r}")
print(f"PACKS_AFTER={packs()!r}")
print(f"STATUS_AFTER={status_oracle()!r}")
