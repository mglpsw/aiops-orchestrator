from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SUBJECT = Path("/opt/agent-tools/ar-200d-successor")
sys.path.insert(0, str(SUBJECT))

from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError, acquire_diff_v2  # noqa: E402


repo = Path(sys.argv[1])
base_sha = sys.argv[2]
head_sha = sys.argv[3]

print(f"process.euid={os.geteuid()}")
print(f"git_dir.writable={os.access(repo / '.git', os.W_OK)}")

direct = subprocess.run(
    [
        "git", "diff", "--no-ext-diff", "--no-textconv", "--binary",
        f"{base_sha}...{head_sha}",
    ],
    cwd=repo,
    capture_output=True,
    text=True,
    check=False,
)
print(f"direct.rc={direct.returncode}")
print(f"direct.has_head={'+head' in direct.stdout}")
print(f"direct.stderr={direct.stderr.strip()!r}")

try:
    acquired = acquire_diff_v2(repo, base_sha=base_sha, head_sha=head_sha)
except DiffAcquisitionError as exc:
    print(f"acquire.error={exc.reason_code}")
else:
    print("acquire.error=None")
    print(f"acquire.has_head={'+head' in acquired}")
